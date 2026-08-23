"""
Unit tests for the Aave V3 money market integration.

This module exercises the REAL AaveV3 strategy and AaveV3MoneyMarket facade
against a fake JSON-RPC node: the only mocked boundary is the RPC transport,
which answers ``eth_call`` with ABI-encoded payloads built in the exact output
ordering of the vendored official ABIs (``common/abi/aave_v3_pool.json`` and
``common/abi/aave_v3_data_provider.json``).

Covered:
- Tuple index maps for getReserveData / getReserveConfigurationData /
  getUserAccountData / getUserReserveData (exact Decimal conversions)
- Ray (1e27) APR -> APY compounding, bps (1e4) and wad (1e18) conversions
- Position enumeration over multiple reserves with real financepype assets,
  including how many data-provider calls it costs and that the independent
  per-reserve calls are issued concurrently rather than one after another
- Exact calldata (selector + arguments) for every build method, including the
  receiveAToken inversion regression and uint256-max full repay/withdraw
- Wallet binding: unbound ValueError, rebinding, unsigned build-only output
- Facade dispatch through the MoneyMarket base with the strategy registered
"""

import asyncio
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from decimal import Decimal
from typing import Any, cast
from unittest.mock import patch

import pytest
from eth_abi import decode, encode
from eth_account import Account
from eth_account.signers.local import LocalAccount
from financepype.operations.transactions.models import BlockchainTransactionState
from financepype.platforms.blockchain import BlockchainPlatform
from pydantic import ValidationError

from blockchainpype.dapps.money_market import (
    CollateralMode,
    InterestRateMode,
    ProtocolConfiguration,
)
from blockchainpype.dapps.money_market import (
    ProtocolImplementation as MoneyMarketProtocolImplementation,
)
from blockchainpype.evm.asset import EthereumAssetData
from blockchainpype.evm.blockchain.blockchain import (
    EthereumBlockchain,
    EthereumBlockchainType,
)
from blockchainpype.evm.blockchain.configuration import (
    EthereumBlockchainConfiguration,
    EthereumConnectivityConfiguration,
    EthereumNativeAssetConfiguration,
)
from blockchainpype.evm.blockchain.identifier import EthereumAddress
from blockchainpype.evm.dapp.erc20 import (
    ERC20Contract,
    ERC20ContractConfiguration,
    ERC20Token,
)
from blockchainpype.evm.dapp.money_market.aave import (
    AAVE_V3_ETHEREUM_DATA_PROVIDER_ADDRESS,
    AAVE_V3_ETHEREUM_POOL_ADDRESS,
    AaveV3,
    AaveV3Configuration,
    AaveV3MoneyMarket,
    apr_to_apy,
    bps_to_decimal,
    ray_to_decimal,
    wad_to_decimal,
)
from blockchainpype.evm.dapp.unsigned import (
    UNSIGNED_TX_DATA_KEY,
    build_unsigned_transaction,
    unsigned_tx_params,
)
from blockchainpype.evm.transaction import EthereumTransaction
from blockchainpype.evm.wallet.wallet import EthereumWallet
from tests.evm.test_wallet import TX_HASH_HEX, FakeRPCProvider, build_wallet

POOL_ADDRESS = AAVE_V3_ETHEREUM_POOL_ADDRESS
DATA_PROVIDER_ADDRESS = AAVE_V3_ETHEREUM_DATA_PROVIDER_ADDRESS
USDC_ADDRESS = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
WETH_ADDRESS = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
USER_ADDRESS = "0x5B38Da6a701c568545dCfcB03FcB875f56beddC4"
LIQUIDATED_USER = "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984"

# Canonical Aave V3 function selectors (keccak-256 of the signature, 4 bytes)
SEL_SUPPLY = "0x617ba037"  # supply(address,uint256,address,uint16)
SEL_WITHDRAW = "0x69328dec"  # withdraw(address,uint256,address)
SEL_BORROW = "0xa415bcad"  # borrow(address,uint256,uint256,uint16,address)
SEL_REPAY = "0x573ade81"  # repay(address,uint256,uint256,address)
SEL_SET_COLLATERAL = "0x5a3b74b9"  # setUserUseReserveAsCollateral(address,bool)
SEL_LIQUIDATION_CALL = "0x00a718a9"  # liquidationCall(addr,addr,addr,uint256,bool)
SEL_GET_USER_ACCOUNT_DATA = "0xbf92857c"  # getUserAccountData(address)
SEL_GET_RESERVE_DATA = "0x35ea6a75"  # getReserveData(address)
SEL_GET_RESERVE_CONFIGURATION_DATA = "0x3e150141"
SEL_GET_USER_RESERVE_DATA = "0x28dd2d01"  # getUserReserveData(address,address)
SEL_GET_ALL_RESERVES_TOKENS = "0xb316ff89"  # getAllReservesTokens()

RAY = 10**27
WAD = 10**18
SECONDS_PER_YEAR = 31_536_000
UINT256_MAX = 2**256 - 1


def expected_apy(apr: str) -> Decimal:
    """Per-second compounded APY for an APR fraction, computed independently."""
    return (1 + Decimal(apr) / SECONDS_PER_YEAR) ** SECONDS_PER_YEAR - 1


APY_1 = expected_apy("0.01")
APY_3 = expected_apy("0.03")
APY_4 = expected_apy("0.04")
APY_5 = expected_apy("0.05")
APY_6 = expected_apy("0.06")
APY_7 = expected_apy("0.07")


class FakeAaveNode:
    """Answers Aave eth_calls with payloads in the vendored ABI output order."""

    def __init__(self) -> None:
        self.health_factor_wad = 2_340_000_000_000_000_000  # 2.34

        # IPoolDataProvider.getReserveData(asset):
        # (unbacked, accruedToTreasuryScaled, totalAToken, totalStableDebt,
        #  totalVariableDebt, liquidityRate, variableBorrowRate,
        #  stableBorrowRate, averageStableBorrowRate, liquidityIndex,
        #  variableBorrowIndex, lastUpdateTimestamp)
        self.reserve_data: dict[str, tuple[int, ...]] = {
            USDC_ADDRESS.lower(): (
                0,
                5_000_000,
                1_000_000 * 10**6,  # totalAToken: 1,000,000 USDC
                100_000 * 10**6,  # totalStableDebt: 100,000 USDC
                400_000 * 10**6,  # totalVariableDebt: 400,000 USDC
                30 * 10**24,  # liquidityRate: 3% APR in ray
                50 * 10**24,  # variableBorrowRate: 5% APR in ray
                70 * 10**24,  # stableBorrowRate: 7% APR in ray
                65 * 10**24,
                1_010 * 10**24,  # liquidityIndex: 1.01 ray
                1_020 * 10**24,  # variableBorrowIndex: 1.02 ray
                1_700_000_000,
            ),
            WETH_ADDRESS.lower(): (
                0,
                0,
                500 * WAD,
                10 * WAD,
                100 * WAD,
                10 * 10**24,  # liquidityRate: 1%
                40 * 10**24,  # variableBorrowRate: 4%
                60 * 10**24,  # stableBorrowRate: 6%
                55 * 10**24,
                RAY,
                RAY,
                1_700_000_000,
            ),
        }

        # IPoolDataProvider.getReserveConfigurationData(asset):
        # (decimals, ltv, liquidationThreshold, liquidationBonus,
        #  reserveFactor, usageAsCollateralEnabled, borrowingEnabled,
        #  stableBorrowRateEnabled, isActive, isFrozen)
        self.configuration_data: dict[str, tuple[int | bool, ...]] = {
            USDC_ADDRESS.lower(): (
                6,
                7500,
                7800,
                10500,
                1000,
                True,
                True,
                False,
                True,
                False,
            ),
            WETH_ADDRESS.lower(): (
                18,
                8000,
                8250,
                10500,
                1500,
                True,
                True,
                True,
                True,
                False,
            ),
        }

        # IPoolDataProvider.getUserReserveData(asset, user):
        # (currentATokenBalance, currentStableDebt, currentVariableDebt,
        #  principalStableDebt, scaledVariableDebt, stableBorrowRate,
        #  liquidityRate, stableRateLastUpdated, usageAsCollateralEnabled)
        self.user_reserve_data: dict[str, tuple[int | bool, ...]] = {
            USDC_ADDRESS.lower(): (
                2_500 * 10**6,  # currentATokenBalance: 2,500 USDC
                0,
                0,
                0,
                0,
                0,
                30 * 10**24,  # liquidityRate: 3%
                0,
                True,
            ),
            WETH_ADDRESS.lower(): (
                0,
                500_000_000_000_000_000,  # currentStableDebt: 0.5 WETH
                1_500_000_000_000_000_000,  # currentVariableDebt: 1.5 WETH
                450_000_000_000_000_000,  # principalStableDebt: 0.45 WETH
                1_400_000_000_000_000_000,
                60 * 10**24,  # user's stableBorrowRate: 6%
                10 * 10**24,  # liquidityRate: 1%
                1_699_000_000,
                False,
            ),
        }

    def eth_call(self, params: Any) -> str:
        call = params[0]
        to = str(call["to"]).lower()
        data = str(call.get("data") or call.get("input"))
        selector = data[:10].lower()
        args = bytes.fromhex(data[10:])

        if to == POOL_ADDRESS.lower():
            if selector == SEL_GET_USER_ACCOUNT_DATA:
                # (totalCollateralBase, totalDebtBase, availableBorrowsBase,
                #  currentLiquidationThreshold, ltv, healthFactor)
                payload = encode(
                    ["uint256"] * 6,
                    [
                        15_000 * 10**8,
                        5_000 * 10**8,
                        6_250 * 10**8,
                        7800,
                        7500,
                        self.health_factor_wad,
                    ],
                )
                return "0x" + payload.hex()

        if to == DATA_PROVIDER_ADDRESS.lower():
            if selector == SEL_GET_ALL_RESERVES_TOKENS:
                payload = encode(
                    ["(string,address)[]"],
                    [[("USDC", USDC_ADDRESS), ("WETH", WETH_ADDRESS)]],
                )
                return "0x" + payload.hex()
            if selector == SEL_GET_RESERVE_DATA:
                (asset,) = decode(["address"], args)
                payload = encode(
                    ["uint256"] * 11 + ["uint40"],
                    list(self.reserve_data[str(asset).lower()]),
                )
                return "0x" + payload.hex()
            if selector == SEL_GET_RESERVE_CONFIGURATION_DATA:
                (asset,) = decode(["address"], args)
                payload = encode(
                    ["uint256"] * 5 + ["bool"] * 5,
                    list(self.configuration_data[str(asset).lower()]),
                )
                return "0x" + payload.hex()
            if selector == SEL_GET_USER_RESERVE_DATA:
                asset, user = decode(["address", "address"], args)
                assert str(user).lower() == USER_ADDRESS.lower()
                payload = encode(
                    ["uint256"] * 7 + ["uint40", "bool"],
                    list(self.user_reserve_data[str(asset).lower()]),
                )
                return "0x" + payload.hex()

        raise AssertionError(f"Unexpected eth_call: to={to} selector={selector}")


def make_block_payload() -> dict[str, Any]:
    """Realistic post-London block payload with a 10 gwei base fee."""
    return {
        "number": "0x10",
        "hash": "0x" + "33" * 32,
        "parentHash": "0x" + "44" * 32,
        "sha3Uncles": "0x" + "55" * 32,
        "miner": "0x0000000000000000000000000000000000000000",
        "stateRoot": "0x" + "66" * 32,
        "transactionsRoot": "0x" + "77" * 32,
        "receiptsRoot": "0x" + "88" * 32,
        "logsBloom": "0x" + "00" * 256,
        "difficulty": "0x0",
        "totalDifficulty": "0x0",
        "extraData": "0x",
        "size": "0x220",
        "gasLimit": "0x1c9c380",
        "gasUsed": "0x5208",
        "timestamp": "0x60000000",
        "baseFeePerGas": "0x2540be400",  # 10 gwei
        "transactions": [],
        "uncles": [],
        "nonce": "0x0000000000000000",
        "mixHash": "0x" + "99" * 32,
    }


@pytest.fixture
def aave_node() -> FakeAaveNode:
    return FakeAaveNode()


@pytest.fixture
def rpc_provider(aave_node: FakeAaveNode) -> FakeRPCProvider:
    return FakeRPCProvider(
        {
            "eth_call": aave_node.eth_call,
            "eth_chainId": "0x1",
            "eth_getBalance": "0xde0b6b3a7640000",
            "eth_getTransactionCount": "0x2",
            "eth_sendRawTransaction": TX_HASH_HEX,
            "eth_estimateGas": "0x30d40",  # 200,000
            "eth_getBlockByNumber": make_block_payload(),
            "eth_maxPriorityFeePerGas": "0x3b9aca00",  # 1 gwei
            "eth_feeHistory": {
                "oldestBlock": "0x1",
                "baseFeePerGas": ["0x2540be400", "0x2540be400"],
                "gasUsedRatio": [0.5],
                "reward": [["0x3b9aca00", "0x3b9aca00", "0x3b9aca00", "0x3b9aca00"]],
            },
        }
    )


@pytest.fixture
def ethereum_config(rpc_provider: FakeRPCProvider) -> EthereumBlockchainConfiguration:
    return EthereumBlockchainConfiguration(
        platform=BlockchainPlatform(
            identifier="ethereum",
            type=EthereumBlockchainType,
            chain_id=1,
        ),
        native_asset=EthereumNativeAssetConfiguration(),
        connectivity=EthereumConnectivityConfiguration(rpc_provider=rpc_provider),
        explorer=None,
    )


@pytest.fixture
def ethereum_blockchain(
    ethereum_config: EthereumBlockchainConfiguration,
) -> Iterator[EthereumBlockchain]:
    """Fake-RPC blockchain, resolved by every dapp/contract in these tests.

    The OperatorFactory is patched for the whole test so contracts created at
    any point (including inside position enumeration) bind to the fake node.
    """
    blockchain = EthereumBlockchain(configuration=ethereum_config)
    with patch(
        "financepype.operators.dapps.dapp.OperatorFactory.get",
        return_value=blockchain,
    ):
        yield blockchain


@pytest.fixture
def platform(ethereum_blockchain: EthereumBlockchain) -> BlockchainPlatform:
    return ethereum_blockchain.platform


@pytest.fixture
def protocol_config() -> ProtocolConfiguration:
    return ProtocolConfiguration(
        protocol_name="aave_v3",
        lending_pool_address=POOL_ADDRESS,
        data_provider_address=DATA_PROVIDER_ADDRESS,
    )


@pytest.fixture
def aave_strategy(
    platform: BlockchainPlatform, protocol_config: ProtocolConfiguration
) -> AaveV3:
    """Wallet-less strategy: read paths must work exactly like this."""
    return AaveV3(protocol_config, platform=platform)


def make_token(
    platform: BlockchainPlatform, address: str, symbol: str, decimals: int
) -> ERC20Token:
    """Build a real ERC20Token with pre-populated metadata."""
    ethereum_address = EthereumAddress.from_string(address)
    return ERC20Token(
        platform=platform,
        identifier=ethereum_address,
        contract=ERC20Contract(
            ERC20ContractConfiguration(platform=platform, address=ethereum_address)
        ),
        data=EthereumAssetData(name=symbol, symbol=symbol, decimals=decimals),
    )


@pytest.fixture
def usdc(platform: BlockchainPlatform) -> ERC20Token:
    return make_token(platform, USDC_ADDRESS, "USDC", 6)


@pytest.fixture
def weth(platform: BlockchainPlatform) -> ERC20Token:
    return make_token(platform, WETH_ADDRESS, "WETH", 18)


@pytest.fixture
def test_account() -> LocalAccount:
    return Account.create()


@pytest.fixture
async def ethereum_wallet(
    ethereum_config: EthereumBlockchainConfiguration,
    test_account: LocalAccount,
    ethereum_blockchain: EthereumBlockchain,
) -> AsyncIterator[EthereumWallet]:
    wallet = build_wallet(ethereum_config, test_account, ethereum_blockchain)
    if wallet._background_tasks:
        await asyncio.gather(*wallet._background_tasks, return_exceptions=True)
    yield wallet


@pytest.fixture
def bound_strategy(aave_strategy: AaveV3, ethereum_wallet: EthereumWallet) -> AaveV3:
    aave_strategy.set_wallet(ethereum_wallet)
    return aave_strategy


@pytest.fixture
def money_market(platform: BlockchainPlatform) -> AaveV3MoneyMarket:
    return AaveV3MoneyMarket(AaveV3Configuration(platform=platform))


@pytest.fixture
def bound_money_market(
    platform: BlockchainPlatform, ethereum_wallet: EthereumWallet
) -> AaveV3MoneyMarket:
    return AaveV3MoneyMarket(
        AaveV3Configuration(platform=platform), wallet=ethereum_wallet
    )


def tx_params_of(transaction: EthereumTransaction) -> dict[str, Any]:
    """Read the built parameters through the shared unsigned-tx convention."""
    return dict(unsigned_tx_params(transaction))


def calldata_of(transaction: EthereumTransaction) -> str:
    return str(tx_params_of(transaction)["data"]).lower()


def encode_call(selector: str, types: list[str], args: list[Any]) -> str:
    return selector + encode(types, args).hex()


def assert_unsigned_pool_transaction(
    transaction: EthereumTransaction, wallet: EthereumWallet
) -> None:
    """The build contract: unsigned, pending-broadcast, targeted at the Pool."""
    assert isinstance(transaction, EthereumTransaction)
    assert transaction.current_state == BlockchainTransactionState.PENDING_BROADCAST
    assert transaction.signed_transaction is None
    assert transaction.operator_operation_id is None
    params = tx_params_of(transaction)
    assert str(params["to"]).lower() == POOL_ADDRESS.lower()
    assert params["from"] == wallet.address.raw
    assert params["chainId"] == 1


# === Unit conversion helpers ===


class TestUnitConversions:
    def test_ray_to_decimal(self) -> None:
        assert ray_to_decimal(30 * 10**24) == Decimal("0.03")
        assert ray_to_decimal(RAY) == Decimal(1)
        assert ray_to_decimal(0) == Decimal(0)

    def test_bps_to_decimal(self) -> None:
        assert bps_to_decimal(7500) == Decimal("0.75")
        assert bps_to_decimal(10000) == Decimal(1)

    def test_wad_to_decimal(self) -> None:
        assert wad_to_decimal(2_340_000_000_000_000_000) == Decimal("2.34")

    def test_apr_to_apy_compounds_per_second(self) -> None:
        assert apr_to_apy(Decimal(0)) == Decimal(0)
        assert apr_to_apy(Decimal("0.05")) == APY_5
        # Compounding strictly increases the effective rate
        assert apr_to_apy(Decimal("0.05")) > Decimal("0.05")


# === Configuration ===


class TestConfiguration:
    def test_default_configuration_targets_ethereum_mainnet(
        self, platform: BlockchainPlatform
    ) -> None:
        configuration = AaveV3Configuration(platform=platform)
        assert len(configuration.protocols) == 1
        protocol = configuration.protocols[0]
        assert protocol.protocol_name == "aave_v3"
        assert (
            protocol.lending_pool_address
            == "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
        )
        assert (
            protocol.data_provider_address
            == "0x7B4EB56E7CD4b454BA8ff71E4518426369a138a3"
        )

    def test_configuration_rejects_missing_aave_protocol(
        self, platform: BlockchainPlatform
    ) -> None:
        with pytest.raises(ValidationError, match="Aave protocol"):
            AaveV3Configuration(
                platform=platform,
                protocols=[
                    ProtocolConfiguration(
                        protocol_name="compound_v3",
                        lending_pool_address=POOL_ADDRESS,
                        data_provider_address=DATA_PROVIDER_ADDRESS,
                    )
                ],
            )


# === Market data (tuple index maps) ===


class TestMarketData:
    async def test_reserve_tuple_maps_and_conversions(
        self, aave_strategy: AaveV3, usdc: ERC20Token
    ) -> None:
        assert aave_strategy._wallet is None  # read path is wallet-less

        market_data = await aave_strategy.get_market_data(usdc)

        assert market_data.asset is usdc
        assert market_data.supply_apy == APY_3
        assert market_data.variable_borrow_apy == APY_5
        assert market_data.stable_borrow_apy == APY_7
        assert market_data.total_supply == Decimal("1000000")
        assert market_data.total_borrows == Decimal("500000")
        assert market_data.utilization_rate == Decimal("0.5")
        assert market_data.liquidity_rate == Decimal("0.03")
        assert market_data.liquidation_threshold == Decimal("0.78")
        assert market_data.loan_to_value == Decimal("0.75")
        assert market_data.reserve_factor == Decimal("0.1")
        assert market_data.is_borrowing_enabled is True
        assert market_data.is_stable_rate_enabled is False
        assert market_data.is_frozen is False
        assert market_data.protocol == "aave_v3"

    async def test_reads_hit_data_provider_with_exact_calldata(
        self, aave_strategy: AaveV3, usdc: ERC20Token, rpc_provider: FakeRPCProvider
    ) -> None:
        await aave_strategy.get_market_data(usdc)

        calls = rpc_provider.calls_for("eth_call")
        assert len(calls) == 2
        for params in calls:
            assert str(params[0]["to"]).lower() == DATA_PROVIDER_ADDRESS.lower()
        assert str(calls[0][0]["data"]).lower() == encode_call(
            SEL_GET_RESERVE_DATA, ["address"], [USDC_ADDRESS]
        )
        assert str(calls[1][0]["data"]).lower() == encode_call(
            SEL_GET_RESERVE_CONFIGURATION_DATA, ["address"], [USDC_ADDRESS]
        )


# === User account data ===


class TestUserAccountData:
    async def test_account_tuple_map_and_conversions(
        self, aave_strategy: AaveV3, rpc_provider: FakeRPCProvider
    ) -> None:
        account_data = await aave_strategy.get_user_account_data(USER_ADDRESS)

        assert account_data.total_collateral_value == Decimal("15000")
        assert account_data.total_debt_value == Decimal("5000")
        assert account_data.available_borrow_value == Decimal("6250")
        assert account_data.current_liquidation_threshold == Decimal("0.78")
        assert account_data.loan_to_value == Decimal("0.75")
        assert account_data.health_factor == Decimal("2.34")
        assert account_data.is_healthy is True
        assert account_data.protocol == "aave_v3"

        (call,) = rpc_provider.calls_for("eth_call")
        assert str(call[0]["to"]).lower() == POOL_ADDRESS.lower()
        assert str(call[0]["data"]).lower() == encode_call(
            SEL_GET_USER_ACCOUNT_DATA, ["address"], [USER_ADDRESS]
        )


# === Position enumeration ===


class TestPositions:
    async def test_lending_positions_enumerate_reserves(
        self, aave_strategy: AaveV3, rpc_provider: FakeRPCProvider
    ) -> None:
        positions = await aave_strategy.get_lending_positions(USER_ADDRESS)

        # WETH has no aToken balance, so only the USDC position exists
        assert len(positions) == 1
        position = positions[0]

        assert isinstance(position.asset, ERC20Token)
        assert position.asset.address.string == USDC_ADDRESS
        assert position.asset.data is not None
        assert position.asset.data.symbol == "USDC"
        assert position.asset.data.decimals == 6

        assert position.supplied_amount == Decimal("2500")
        assert position.accrued_interest == Decimal(0)
        assert position.total_balance == Decimal("2500")
        assert position.apy == APY_3
        assert position.is_collateral is True
        assert position.protocol == "aave_v3"

        # Per-reserve user data was requested with exact calldata
        user_reserve_calls = [
            str(params[0]["data"]).lower()
            for params in rpc_provider.calls_for("eth_call")
            if str(params[0]["data"]).lower().startswith(SEL_GET_USER_RESERVE_DATA)
        ]
        assert user_reserve_calls == [
            encode_call(
                SEL_GET_USER_RESERVE_DATA,
                ["address", "address"],
                [USDC_ADDRESS, USER_ADDRESS],
            ),
            encode_call(
                SEL_GET_USER_RESERVE_DATA,
                ["address", "address"],
                [WETH_ADDRESS, USER_ADDRESS],
            ),
        ]

    async def test_borrowing_positions_split_by_rate_mode(
        self, aave_strategy: AaveV3
    ) -> None:
        positions = await aave_strategy.get_borrowing_positions(USER_ADDRESS)

        # USDC has no debt; WETH carries both variable and stable debt
        assert len(positions) == 2
        variable, stable = positions

        assert isinstance(variable.asset, ERC20Token)
        assert variable.asset.address.string == WETH_ADDRESS
        assert variable.asset.data is not None
        assert variable.asset.data.symbol == "WETH"
        assert variable.asset.data.decimals == 18
        assert variable.interest_rate_mode == InterestRateMode.VARIABLE
        assert variable.borrowed_amount == Decimal("1.5")
        assert variable.accrued_interest == Decimal(0)
        assert variable.total_debt == Decimal("1.5")
        # Variable borrow rate comes from the reserve data (4% APR)
        assert variable.current_rate == APY_4

        assert stable.interest_rate_mode == InterestRateMode.STABLE
        assert stable.borrowed_amount == Decimal("0.45")
        assert stable.accrued_interest == Decimal("0.05")
        assert stable.total_debt == Decimal("0.5")
        # Stable rate comes from the user's reserve data (6% APR)
        assert stable.current_rate == APY_6
        assert stable.protocol == "aave_v3"


# === Position fetching cost (call counts + concurrency) ===

#: A ``getUserReserveData`` tuple for a reserve the user never touched.
IDLE_USER_RESERVE: tuple[int | bool, ...] = (0, 0, 0, 0, 0, 0, 0, 0, False)


class InstrumentedCall:
    """One pending data-provider call that records when it is in flight."""

    def __init__(
        self, provider: "InstrumentedDataProvider", name: str, result: Any
    ) -> None:
        self._provider = provider
        self._name = name
        self._result = result

    async def call(self) -> Any:
        provider = self._provider
        provider.call_counts[self._name] += 1
        provider.in_flight[self._name] += 1
        provider.max_in_flight[self._name] = max(
            provider.max_in_flight[self._name], provider.in_flight[self._name]
        )
        try:
            # Yield twice, so that sibling calls dispatched in the same batch
            # get to start before this one resolves: with asyncio.gather they
            # overlap, with a sequential await loop they never can.
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            return self._result
        finally:
            provider.in_flight[self._name] -= 1


class InstrumentedDataProvider:
    """Counting stand-in for the Aave data provider contract.

    Answers from the same canned tuples as :class:`FakeAaveNode` while
    recording, per ABI function, how many calls were made and how many were
    ever in flight simultaneously — the observable difference between an
    ``asyncio.gather`` fan-out and one sequential round trip per reserve.
    """

    def __init__(self, node: FakeAaveNode, idle_reserves: int = 0) -> None:
        self.node = node
        self.reserves: list[tuple[str, str]] = [
            ("USDC", USDC_ADDRESS),
            ("WETH", WETH_ADDRESS),
        ]
        # Extra listed reserves the user has no position in: they still cost
        # one getUserReserveData each, which is the N+1 storm being fixed.
        for index in range(idle_reserves):
            self.reserves.append((f"IDLE{index}", "0x" + f"{index + 1:040x}"))
        self.call_counts: Counter[str] = Counter()
        self.in_flight: Counter[str] = Counter()
        self.max_in_flight: Counter[str] = Counter()
        self.is_initialized = True

    async def initialize(self) -> None:
        return None

    @property
    def functions(self) -> "InstrumentedDataProvider":
        return self

    def getAllReservesTokens(self) -> InstrumentedCall:
        return InstrumentedCall(self, "getAllReservesTokens", list(self.reserves))

    def getUserReserveData(self, asset: str, user: str) -> InstrumentedCall:
        assert user.lower() == USER_ADDRESS.lower()
        return InstrumentedCall(
            self,
            "getUserReserveData",
            self.node.user_reserve_data.get(asset.lower(), IDLE_USER_RESERVE),
        )

    def getReserveConfigurationData(self, asset: str) -> InstrumentedCall:
        return InstrumentedCall(
            self,
            "getReserveConfigurationData",
            self.node.configuration_data[asset.lower()],
        )

    def getReserveData(self, asset: str) -> InstrumentedCall:
        return InstrumentedCall(
            self, "getReserveData", self.node.reserve_data[asset.lower()]
        )


def instrument(
    strategy: AaveV3, node: FakeAaveNode, idle_reserves: int = 0
) -> InstrumentedDataProvider:
    """Swap the strategy's data provider for the counting stand-in."""
    provider = InstrumentedDataProvider(node, idle_reserves=idle_reserves)
    strategy.data_provider_contract = cast(Any, provider)
    return provider


def lending_summary(positions: list[Any]) -> list[tuple[Any, ...]]:
    return [
        (p.asset.address.string, p.supplied_amount, p.apy, p.is_collateral)
        for p in positions
    ]


def borrowing_summary(positions: list[Any]) -> list[tuple[Any, ...]]:
    return [
        (
            p.asset.address.string,
            p.borrowed_amount,
            p.accrued_interest,
            p.interest_rate_mode,
            p.current_rate,
        )
        for p in positions
    ]


class TestPositionFetchingCost:
    """Regression: positions used to cost one sequential RPC per reserve.

    Each getter enumerated every reserve on its own and awaited
    ``getUserReserveData`` one reserve at a time, so reading both sides on
    mainnet was ~75 sequential round trips.
    """

    async def test_combined_accessor_enumerates_once(
        self, aave_strategy: AaveV3, aave_node: FakeAaveNode
    ) -> None:
        provider = instrument(aave_strategy, aave_node)

        lending, borrowing = await aave_strategy.get_positions(USER_ADDRESS)

        assert provider.call_counts["getAllReservesTokens"] == 1
        assert provider.call_counts["getUserReserveData"] == 2  # one per reserve
        # Decimals for the one supplied and the one indebted reserve
        assert provider.call_counts["getReserveConfigurationData"] == 2
        # The variable borrow rate of the single variable-debt reserve
        assert provider.call_counts["getReserveData"] == 1

        assert [p.supplied_amount for p in lending] == [Decimal("2500")]
        assert [p.interest_rate_mode for p in borrowing] == [
            InterestRateMode.VARIABLE,
            InterestRateMode.STABLE,
        ]

    async def test_separate_getters_each_enumerate_once(
        self, aave_strategy: AaveV3, aave_node: FakeAaveNode
    ) -> None:
        provider = instrument(aave_strategy, aave_node)

        await aave_strategy.get_lending_positions(USER_ADDRESS)
        assert provider.call_counts["getAllReservesTokens"] == 1
        assert provider.call_counts["getUserReserveData"] == 2

        await aave_strategy.get_borrowing_positions(USER_ADDRESS)
        # Unchanged API: a second getter still costs a second enumeration...
        assert provider.call_counts["getAllReservesTokens"] == 2
        assert provider.call_counts["getUserReserveData"] == 4

        # ...which is exactly what the combined accessor avoids
        provider.call_counts.clear()
        await aave_strategy.get_positions(USER_ADDRESS)
        assert provider.call_counts["getAllReservesTokens"] == 1
        assert provider.call_counts["getUserReserveData"] == 2

    async def test_per_reserve_calls_are_issued_concurrently(
        self, aave_strategy: AaveV3, aave_node: FakeAaveNode
    ) -> None:
        """All per-reserve lookups overlap; a sequential loop never exceeds 1."""
        provider = instrument(aave_strategy, aave_node, idle_reserves=10)

        lending, borrowing = await aave_strategy.get_positions(USER_ADDRESS)

        assert provider.call_counts["getUserReserveData"] == 12
        assert provider.max_in_flight["getUserReserveData"] == 12
        # Idle reserves hold nothing, so the position lists are unaffected
        assert len(lending) == 1
        assert len(borrowing) == 2

    async def test_lending_getter_fans_out(
        self, aave_strategy: AaveV3, aave_node: FakeAaveNode
    ) -> None:
        provider = instrument(aave_strategy, aave_node, idle_reserves=5)

        await aave_strategy.get_lending_positions(USER_ADDRESS)

        assert provider.max_in_flight["getUserReserveData"] == 7

    async def test_borrowing_getter_fans_out(
        self, aave_strategy: AaveV3, aave_node: FakeAaveNode
    ) -> None:
        provider = instrument(aave_strategy, aave_node, idle_reserves=5)

        await aave_strategy.get_borrowing_positions(USER_ADDRESS)

        assert provider.max_in_flight["getUserReserveData"] == 7

    async def test_combined_accessor_matches_the_single_sided_getters(
        self, aave_strategy: AaveV3, aave_node: FakeAaveNode
    ) -> None:
        """Behaviour preserved: same positions, in the same order."""
        instrument(aave_strategy, aave_node, idle_reserves=3)

        lending, borrowing = await aave_strategy.get_positions(USER_ADDRESS)
        expected_lending = await aave_strategy.get_lending_positions(USER_ADDRESS)
        expected_borrowing = await aave_strategy.get_borrowing_positions(USER_ADDRESS)

        assert lending_summary(lending) == lending_summary(expected_lending)
        assert borrowing_summary(borrowing) == borrowing_summary(expected_borrowing)

    async def test_enumeration_preserves_reserve_order(
        self, aave_strategy: AaveV3, aave_node: FakeAaveNode
    ) -> None:
        """Concurrency must not reshuffle results onto the wrong reserves."""
        provider = instrument(aave_strategy, aave_node, idle_reserves=6)

        entries = await aave_strategy._iter_user_reserves(USER_ADDRESS)

        assert [
            (symbol, address) for symbol, address, _ in entries
        ] == provider.reserves
        assert entries[0][2] == tuple(aave_node.user_reserve_data[USDC_ADDRESS.lower()])
        assert entries[1][2] == tuple(aave_node.user_reserve_data[WETH_ADDRESS.lower()])
        assert all(entry[2] == IDLE_USER_RESERVE for entry in entries[2:])


# === Transaction building ===


class TestBuildTransactions:
    async def test_supply_builds_exact_calldata(
        self,
        bound_strategy: AaveV3,
        usdc: ERC20Token,
        ethereum_wallet: EthereumWallet,
        rpc_provider: FakeRPCProvider,
    ) -> None:
        transaction = await bound_strategy.build_supply_transaction(
            usdc, Decimal("100"), USER_ADDRESS
        )

        assert_unsigned_pool_transaction(transaction, ethereum_wallet)
        assert transaction.client_operation_id.startswith("aave-v3-supply-")
        expected = encode_call(
            SEL_SUPPLY,
            ["address", "uint256", "address", "uint16"],
            [USDC_ADDRESS, 100 * 10**6, USER_ADDRESS, 0],
        )
        assert calldata_of(transaction) == expected

        # Gas estimation ran against the real calldata...
        estimate_calls = rpc_provider.calls_for("eth_estimateGas")
        assert estimate_calls
        assert str(estimate_calls[0][0]["data"]).lower() == expected
        # ...but nothing was signed or broadcast (build-only contract)
        assert rpc_provider.calls_for("eth_sendRawTransaction") == []

    async def test_withdraw_builds_exact_calldata(
        self,
        bound_strategy: AaveV3,
        usdc: ERC20Token,
        ethereum_wallet: EthereumWallet,
    ) -> None:
        transaction = await bound_strategy.build_withdraw_transaction(
            usdc, Decimal("50"), USER_ADDRESS
        )

        assert_unsigned_pool_transaction(transaction, ethereum_wallet)
        assert calldata_of(transaction) == encode_call(
            SEL_WITHDRAW,
            ["address", "uint256", "address"],
            [USDC_ADDRESS, 50 * 10**6, USER_ADDRESS],
        )

    async def test_withdraw_all_uses_uint256_max(
        self, bound_strategy: AaveV3, usdc: ERC20Token
    ) -> None:
        transaction = await bound_strategy.build_withdraw_transaction(
            usdc, Decimal(0), USER_ADDRESS, withdraw_all=True
        )

        assert calldata_of(transaction) == encode_call(
            SEL_WITHDRAW,
            ["address", "uint256", "address"],
            [USDC_ADDRESS, UINT256_MAX, USER_ADDRESS],
        )

    async def test_borrow_variable_maps_to_rate_mode_2(
        self,
        bound_strategy: AaveV3,
        weth: ERC20Token,
        ethereum_wallet: EthereumWallet,
    ) -> None:
        transaction = await bound_strategy.build_borrow_transaction(
            weth, Decimal("0.75"), InterestRateMode.VARIABLE, USER_ADDRESS
        )

        assert_unsigned_pool_transaction(transaction, ethereum_wallet)
        assert calldata_of(transaction) == encode_call(
            SEL_BORROW,
            ["address", "uint256", "uint256", "uint16", "address"],
            [WETH_ADDRESS, 750_000_000_000_000_000, 2, 0, USER_ADDRESS],
        )

    async def test_borrow_stable_maps_to_rate_mode_1(
        self, bound_strategy: AaveV3, usdc: ERC20Token
    ) -> None:
        transaction = await bound_strategy.build_borrow_transaction(
            usdc, Decimal("50"), InterestRateMode.STABLE, USER_ADDRESS
        )

        assert calldata_of(transaction) == encode_call(
            SEL_BORROW,
            ["address", "uint256", "uint256", "uint16", "address"],
            [USDC_ADDRESS, 50 * 10**6, 1, 0, USER_ADDRESS],
        )

    async def test_repay_builds_exact_calldata(
        self, bound_strategy: AaveV3, usdc: ERC20Token
    ) -> None:
        transaction = await bound_strategy.build_repay_transaction(
            usdc, Decimal("25.5"), InterestRateMode.VARIABLE, USER_ADDRESS
        )

        assert calldata_of(transaction) == encode_call(
            SEL_REPAY,
            ["address", "uint256", "uint256", "address"],
            [USDC_ADDRESS, 25_500_000, 2, USER_ADDRESS],
        )

    async def test_repay_all_uses_uint256_max(
        self, bound_strategy: AaveV3, usdc: ERC20Token
    ) -> None:
        transaction = await bound_strategy.build_repay_transaction(
            usdc,
            Decimal(0),
            InterestRateMode.VARIABLE,
            USER_ADDRESS,
            repay_all=True,
        )

        assert calldata_of(transaction) == encode_call(
            SEL_REPAY,
            ["address", "uint256", "uint256", "address"],
            [USDC_ADDRESS, UINT256_MAX, 2, USER_ADDRESS],
        )

    async def test_collateral_enable_and_disable(
        self, bound_strategy: AaveV3, usdc: ERC20Token
    ) -> None:
        enabled = await bound_strategy.build_collateral_transaction(
            usdc, CollateralMode.ENABLED, USER_ADDRESS
        )
        disabled = await bound_strategy.build_collateral_transaction(
            usdc, CollateralMode.DISABLED, USER_ADDRESS
        )

        assert calldata_of(enabled) == encode_call(
            SEL_SET_COLLATERAL, ["address", "bool"], [USDC_ADDRESS, True]
        )
        assert calldata_of(disabled) == encode_call(
            SEL_SET_COLLATERAL, ["address", "bool"], [USDC_ADDRESS, False]
        )

    async def test_liquidation_receive_collateral_inverts_receive_atoken(
        self,
        bound_strategy: AaveV3,
        weth: ERC20Token,
        usdc: ERC20Token,
        ethereum_wallet: EthereumWallet,
    ) -> None:
        """Regression: receive_collateral=True means receiveAToken=False."""
        transaction = await bound_strategy.build_liquidation_transaction(
            weth, usdc, LIQUIDATED_USER, Decimal("1000"), receive_collateral=True
        )

        assert_unsigned_pool_transaction(transaction, ethereum_wallet)
        # debt_to_cover is converted with the DEBT asset's decimals (USDC: 6)
        assert calldata_of(transaction) == encode_call(
            SEL_LIQUIDATION_CALL,
            ["address", "address", "address", "uint256", "bool"],
            [WETH_ADDRESS, USDC_ADDRESS, LIQUIDATED_USER, 1000 * 10**6, False],
        )

    async def test_liquidation_receive_atokens(
        self, bound_strategy: AaveV3, weth: ERC20Token, usdc: ERC20Token
    ) -> None:
        transaction = await bound_strategy.build_liquidation_transaction(
            weth, usdc, LIQUIDATED_USER, Decimal("1000"), receive_collateral=False
        )

        assert calldata_of(transaction) == encode_call(
            SEL_LIQUIDATION_CALL,
            ["address", "address", "address", "uint256", "bool"],
            [WETH_ADDRESS, USDC_ADDRESS, LIQUIDATED_USER, 1000 * 10**6, True],
        )


# === Unsigned-transaction carrier convention ===


class TestUnsignedCarrier:
    """Built parameters travel under the shared ``tx_data`` key."""

    async def test_carrier_key_is_the_shared_constant(
        self, bound_strategy: AaveV3, usdc: ERC20Token
    ) -> None:
        transaction = await bound_strategy.build_supply_transaction(
            usdc, Decimal("1"), USER_ADDRESS
        )

        assert UNSIGNED_TX_DATA_KEY == "tx_data"
        assert set(transaction.other_data) == {UNSIGNED_TX_DATA_KEY}
        # The old Aave-only "tx_params" key is gone
        assert "tx_params" not in transaction.other_data

        params = unsigned_tx_params(transaction)
        assert params["from"] == bound_strategy._require_wallet().address.raw
        assert str(params["data"]).lower().startswith(SEL_SUPPLY)

    async def test_reading_a_foreign_transaction_raises(
        self, ethereum_wallet: EthereumWallet
    ) -> None:
        transaction = EthereumTransaction(
            client_operation_id="not-a-dapp-build",
            owner_identifier=ethereum_wallet.identifier,
            creation_timestamp=ethereum_wallet.current_timestamp,
        )
        with pytest.raises(ValueError, match="carries no 'tx_data' entry"):
            unsigned_tx_params(transaction)

    def test_extra_data_travels_alongside_the_parameters(
        self, ethereum_wallet: EthereumWallet
    ) -> None:
        params: Any = {"to": POOL_ADDRESS, "chainId": 1}
        transaction = build_unsigned_transaction(
            "op-1", ethereum_wallet, params, extra_data={"market_id": "m-1"}
        )

        assert transaction.other_data["market_id"] == "m-1"
        assert unsigned_tx_params(transaction) == params
        assert transaction.current_state == BlockchainTransactionState.PENDING_BROADCAST
        # The stored copy is detached from the caller's mapping
        params["to"] = DATA_PROVIDER_ADDRESS
        assert unsigned_tx_params(transaction)["to"] == POOL_ADDRESS

    def test_extra_data_cannot_shadow_the_carrier_key(
        self, ethereum_wallet: EthereumWallet
    ) -> None:
        with pytest.raises(ValueError, match="must not override the reserved"):
            build_unsigned_transaction(
                "op-2",
                ethereum_wallet,
                cast(Any, {}),
                extra_data={UNSIGNED_TX_DATA_KEY: {"to": POOL_ADDRESS}},
            )


# === Protocol contract conformance ===


class TestProtocolConformance:
    """AaveV3 fulfils the runtime-checkable money-market protocol."""

    def test_strategy_conforms(self, aave_strategy: AaveV3) -> None:
        assert isinstance(aave_strategy, MoneyMarketProtocolImplementation)

    def test_registered_strategies_conform(
        self, money_market: AaveV3MoneyMarket
    ) -> None:
        strategies = list(money_market._protocol_strategies.values())
        assert strategies
        for strategy in strategies:
            assert isinstance(strategy, MoneyMarketProtocolImplementation)


# === Wallet binding ===


def build_calls(
    strategy: AaveV3, usdc: ERC20Token, weth: ERC20Token
) -> dict[str, Callable[[], Awaitable[EthereumTransaction]]]:
    return {
        "supply": lambda: strategy.build_supply_transaction(
            usdc, Decimal(1), USER_ADDRESS
        ),
        "withdraw": lambda: strategy.build_withdraw_transaction(
            usdc, Decimal(1), USER_ADDRESS
        ),
        "borrow": lambda: strategy.build_borrow_transaction(
            usdc, Decimal(1), InterestRateMode.VARIABLE, USER_ADDRESS
        ),
        "repay": lambda: strategy.build_repay_transaction(
            usdc, Decimal(1), InterestRateMode.VARIABLE, USER_ADDRESS
        ),
        "collateral": lambda: strategy.build_collateral_transaction(
            usdc, CollateralMode.ENABLED, USER_ADDRESS
        ),
        "liquidation": lambda: strategy.build_liquidation_transaction(
            weth, usdc, LIQUIDATED_USER, Decimal(1)
        ),
    }


class TestWalletBinding:
    @pytest.mark.parametrize(
        "operation",
        ["supply", "withdraw", "borrow", "repay", "collateral", "liquidation"],
    )
    async def test_build_without_wallet_raises(
        self,
        aave_strategy: AaveV3,
        usdc: ERC20Token,
        weth: ERC20Token,
        operation: str,
    ) -> None:
        with pytest.raises(ValueError, match="No wallet bound"):
            await build_calls(aave_strategy, usdc, weth)[operation]()

    async def test_set_wallet_rebinding(
        self,
        aave_strategy: AaveV3,
        usdc: ERC20Token,
        ethereum_wallet: EthereumWallet,
    ) -> None:
        aave_strategy.set_wallet(ethereum_wallet)
        transaction = await aave_strategy.build_supply_transaction(
            usdc, Decimal(1), USER_ADDRESS
        )
        assert isinstance(transaction, EthereumTransaction)

        aave_strategy.set_wallet(None)
        with pytest.raises(ValueError, match="No wallet bound"):
            await aave_strategy.build_supply_transaction(usdc, Decimal(1), USER_ADDRESS)

        aave_strategy.set_wallet(ethereum_wallet)
        transaction = await aave_strategy.build_supply_transaction(
            usdc, Decimal(1), USER_ADDRESS
        )
        assert isinstance(transaction, EthereumTransaction)

    def test_set_wallet_rejects_non_ethereum_wallet(
        self, aave_strategy: AaveV3
    ) -> None:
        with pytest.raises(TypeError, match="EthereumWallet"):
            aave_strategy.set_wallet(object())  # type: ignore[arg-type]


# === Facade dispatch through the MoneyMarket base ===


class TestMoneyMarketFacade:
    def test_supported_protocols(self, money_market: AaveV3MoneyMarket) -> None:
        assert money_market.supported_protocols == ["aave_v3"]

    def test_non_aave_protocols_are_skipped(self, platform: BlockchainPlatform) -> None:
        configuration = AaveV3Configuration(
            platform=platform,
            protocols=[
                ProtocolConfiguration(
                    protocol_name="aave_v3",
                    lending_pool_address=POOL_ADDRESS,
                    data_provider_address=DATA_PROVIDER_ADDRESS,
                ),
                ProtocolConfiguration(
                    protocol_name="compound_v3",
                    lending_pool_address=POOL_ADDRESS,
                    data_provider_address=DATA_PROVIDER_ADDRESS,
                ),
            ],
        )
        market = AaveV3MoneyMarket(configuration)
        assert market.supported_protocols == ["aave_v3"]

    async def test_read_dispatch_without_wallet(
        self, money_market: AaveV3MoneyMarket, usdc: ERC20Token
    ) -> None:
        market_data = await money_market.get_market_data(usdc)
        assert market_data.total_supply == Decimal("1000000")

        account_data = await money_market.get_user_account_data(USER_ADDRESS)
        assert account_data.health_factor == Decimal("2.34")

        # protocol=None aggregates across all registered strategies
        lending = await money_market.get_lending_positions(USER_ADDRESS)
        assert [p.supplied_amount for p in lending] == [Decimal("2500")]
        borrowing = await money_market.get_borrowing_positions(USER_ADDRESS)
        assert len(borrowing) == 2

    async def test_unknown_protocol_raises(
        self, money_market: AaveV3MoneyMarket, usdc: ERC20Token
    ) -> None:
        with pytest.raises(ValueError, match="Unsupported protocol: unknown"):
            await money_market.get_market_data(usdc, protocol="unknown")

    async def test_supply_dispatch_builds_unsigned_transaction(
        self,
        bound_money_market: AaveV3MoneyMarket,
        usdc: ERC20Token,
        ethereum_wallet: EthereumWallet,
    ) -> None:
        transaction = await bound_money_market.supply(
            usdc, Decimal("100"), USER_ADDRESS
        )

        assert isinstance(transaction, EthereumTransaction)
        assert_unsigned_pool_transaction(transaction, ethereum_wallet)
        assert calldata_of(transaction) == encode_call(
            SEL_SUPPLY,
            ["address", "uint256", "address", "uint16"],
            [USDC_ADDRESS, 100 * 10**6, USER_ADDRESS, 0],
        )

    async def test_borrow_dispatch_uses_configured_default_rate_mode(
        self, bound_money_market: AaveV3MoneyMarket, weth: ERC20Token
    ) -> None:
        assert (
            bound_money_market.configuration.default_interest_rate_mode
            == InterestRateMode.VARIABLE
        )
        transaction = await bound_money_market.borrow(
            weth, Decimal("0.75"), USER_ADDRESS
        )

        assert isinstance(transaction, EthereumTransaction)
        assert calldata_of(transaction) == encode_call(
            SEL_BORROW,
            ["address", "uint256", "uint256", "uint16", "address"],
            [WETH_ADDRESS, 750_000_000_000_000_000, 2, 0, USER_ADDRESS],
        )

    async def test_withdraw_dispatch(
        self, bound_money_market: AaveV3MoneyMarket, usdc: ERC20Token
    ) -> None:
        transaction = await bound_money_market.withdraw(
            usdc, Decimal("50"), USER_ADDRESS
        )

        assert isinstance(transaction, EthereumTransaction)
        assert calldata_of(transaction) == encode_call(
            SEL_WITHDRAW,
            ["address", "uint256", "address"],
            [USDC_ADDRESS, 50 * 10**6, USER_ADDRESS],
        )

    async def test_withdraw_all_dispatch(
        self, bound_money_market: AaveV3MoneyMarket, usdc: ERC20Token
    ) -> None:
        """The facade's withdraw_all must reach the strategy's uint256-max path."""
        transaction = await bound_money_market.withdraw(
            usdc, Decimal(0), USER_ADDRESS, withdraw_all=True
        )

        assert isinstance(transaction, EthereumTransaction)
        assert calldata_of(transaction) == encode_call(
            SEL_WITHDRAW,
            ["address", "uint256", "address"],
            [USDC_ADDRESS, UINT256_MAX, USER_ADDRESS],
        )

    async def test_repay_all_dispatch(
        self, bound_money_market: AaveV3MoneyMarket, usdc: ERC20Token
    ) -> None:
        transaction = await bound_money_market.repay(
            usdc, Decimal(0), USER_ADDRESS, repay_all=True
        )

        assert isinstance(transaction, EthereumTransaction)
        assert calldata_of(transaction) == encode_call(
            SEL_REPAY,
            ["address", "uint256", "uint256", "address"],
            [USDC_ADDRESS, UINT256_MAX, 2, USER_ADDRESS],
        )

    async def test_liquidate_dispatch_receive_collateral_regression(
        self,
        bound_money_market: AaveV3MoneyMarket,
        weth: ERC20Token,
        usdc: ERC20Token,
    ) -> None:
        """Facade default receive_collateral=True must yield receiveAToken=False."""
        transaction = await bound_money_market.liquidate(
            weth, usdc, LIQUIDATED_USER, Decimal("1000")
        )

        assert isinstance(transaction, EthereumTransaction)
        assert calldata_of(transaction) == encode_call(
            SEL_LIQUIDATION_CALL,
            ["address", "address", "address", "uint256", "bool"],
            [WETH_ADDRESS, USDC_ADDRESS, LIQUIDATED_USER, 1000 * 10**6, False],
        )

    async def test_set_collateral_mode_dispatch(
        self, bound_money_market: AaveV3MoneyMarket, usdc: ERC20Token
    ) -> None:
        transaction = await bound_money_market.set_collateral_mode(
            usdc, CollateralMode.DISABLED, USER_ADDRESS
        )

        assert isinstance(transaction, EthereumTransaction)
        assert calldata_of(transaction) == encode_call(
            SEL_SET_COLLATERAL, ["address", "bool"], [USDC_ADDRESS, False]
        )

    async def test_facade_set_wallet_binds_strategies(
        self,
        money_market: AaveV3MoneyMarket,
        usdc: ERC20Token,
        ethereum_wallet: EthereumWallet,
    ) -> None:
        with pytest.raises(ValueError, match="No wallet bound"):
            await money_market.supply(usdc, Decimal(1), USER_ADDRESS)

        money_market.set_wallet(ethereum_wallet)
        transaction = await money_market.supply(usdc, Decimal(1), USER_ADDRESS)
        assert isinstance(transaction, EthereumTransaction)

        money_market.set_wallet(None)
        with pytest.raises(ValueError, match="No wallet bound"):
            await money_market.supply(usdc, Decimal(1), USER_ADDRESS)


# === liquidation_threshold_buffer wiring ===


class TestPositionRiskAssessment:
    async def test_default_buffer_marks_healthy_position_safe(
        self, money_market: AaveV3MoneyMarket
    ) -> None:
        assert money_market.configuration.liquidation_threshold_buffer == Decimal(
            "0.05"
        )
        # Health factor 2.34 clears 1.05 comfortably
        assert await money_market.is_position_safe(USER_ADDRESS) is True

    async def test_position_inside_buffer_is_flagged(
        self, money_market: AaveV3MoneyMarket, aave_node: FakeAaveNode
    ) -> None:
        # Health factor 1.04 is above liquidation (1.0) but inside the 5% buffer
        aave_node.health_factor_wad = 1_040_000_000_000_000_000
        assert await money_market.is_position_safe(USER_ADDRESS) is False

    async def test_position_exactly_at_buffered_threshold_is_safe(
        self, money_market: AaveV3MoneyMarket, aave_node: FakeAaveNode
    ) -> None:
        aave_node.health_factor_wad = 1_050_000_000_000_000_000
        assert await money_market.is_position_safe(USER_ADDRESS) is True
