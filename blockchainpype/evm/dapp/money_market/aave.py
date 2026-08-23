"""
Aave V3 protocol implementation for money market operations.

Provides integration with Aave's Pool and PoolDataProvider contracts using the
official vendored ABIs (``common/abi/aave_v3_pool.json`` and
``common/abi/aave_v3_data_provider.json``).

Unit conventions used by Aave V3 (and converted here):

* Interest rates (``liquidityRate``, ``variableBorrowRate``,
  ``stableBorrowRate``) are annualized per-second-compounding rates (APRs)
  expressed in ray (1e27). :class:`MarketData` reports ``liquidity_rate`` as
  the raw APR fraction, while the ``*_apy`` fields report the per-second
  compounded APY: ``(1 + apr / 31_536_000) ** 31_536_000 - 1``.
* LTV, liquidation threshold and reserve factor are expressed in basis
  points (1e4) and reported as decimal fractions.
* ``getUserAccountData`` values are in the market's base currency (USD with
  8 decimals on Ethereum mainnet) and the health factor is wad (1e18).
"""

import asyncio
import uuid
from collections.abc import Sequence
from decimal import Decimal
from typing import Self, cast

from financepype.assets.blockchain import BlockchainAsset
from financepype.owners.wallet import BlockchainWallet
from financepype.platforms.blockchain import BlockchainPlatform
from pydantic import Field, model_validator
from web3.contract.async_contract import AsyncContractFunction

from blockchainpype.dapps.money_market import (
    BorrowingPosition,
    CollateralMode,
    InterestRateMode,
    LendingPosition,
    MarketData,
    ProtocolConfiguration,
    UserAccountData,
)
from blockchainpype.evm.asset import EthereumAsset, EthereumAssetData
from blockchainpype.evm.blockchain.identifier import EthereumAddress
from blockchainpype.evm.dapp.abi import EthereumLocalFileABI
from blockchainpype.evm.dapp.contract import (
    EthereumContractConfiguration,
    EthereumSmartContract,
)
from blockchainpype.evm.dapp.erc20 import (
    ERC20Contract,
    ERC20ContractConfiguration,
    ERC20Token,
)
from blockchainpype.evm.dapp.unsigned import build_unsigned_transaction
from blockchainpype.evm.transaction import EthereumTransaction
from blockchainpype.evm.wallet.wallet import EthereumWallet

from .money_market import EVMMoneyMarket, EVMMoneyMarketConfiguration

# Aave V3 Ethereum mainnet deployment
AAVE_V3_ETHEREUM_POOL_ADDRESS = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
AAVE_V3_ETHEREUM_DATA_PROVIDER_ADDRESS = "0x7B4EB56E7CD4b454BA8ff71E4518426369a138a3"

# Protocol unit constants
RAY = Decimal(10) ** 27
BPS = Decimal(10) ** 4
WAD = Decimal(10) ** 18
BASE_CURRENCY_UNIT = Decimal(10) ** 8
SECONDS_PER_YEAR = 31_536_000
UINT256_MAX = 2**256 - 1
REFERRAL_CODE_NONE = 0

# Aave DataTypes.InterestRateMode on-chain encoding
AAVE_STABLE_RATE_MODE = 1
AAVE_VARIABLE_RATE_MODE = 2

# --- Tuple index maps, ground truth: common/abi/aave_v3_data_provider.json ---

# IPoolDataProvider.getReserveData(asset) output ordering:
# (unbacked, accruedToTreasuryScaled, totalAToken, totalStableDebt,
#  totalVariableDebt, liquidityRate, variableBorrowRate, stableBorrowRate,
#  averageStableBorrowRate, liquidityIndex, variableBorrowIndex,
#  lastUpdateTimestamp)
RESERVE_DATA_TOTAL_ATOKEN = 2
RESERVE_DATA_TOTAL_STABLE_DEBT = 3
RESERVE_DATA_TOTAL_VARIABLE_DEBT = 4
RESERVE_DATA_LIQUIDITY_RATE = 5
RESERVE_DATA_VARIABLE_BORROW_RATE = 6
RESERVE_DATA_STABLE_BORROW_RATE = 7

# IPoolDataProvider.getReserveConfigurationData(asset) output ordering:
# (decimals, ltv, liquidationThreshold, liquidationBonus, reserveFactor,
#  usageAsCollateralEnabled, borrowingEnabled, stableBorrowRateEnabled,
#  isActive, isFrozen)
CONFIGURATION_DATA_DECIMALS = 0
CONFIGURATION_DATA_LTV = 1
CONFIGURATION_DATA_LIQUIDATION_THRESHOLD = 2
CONFIGURATION_DATA_LIQUIDATION_BONUS = 3
CONFIGURATION_DATA_RESERVE_FACTOR = 4
CONFIGURATION_DATA_USAGE_AS_COLLATERAL = 5
CONFIGURATION_DATA_BORROWING_ENABLED = 6
CONFIGURATION_DATA_STABLE_RATE_ENABLED = 7
CONFIGURATION_DATA_IS_ACTIVE = 8
CONFIGURATION_DATA_IS_FROZEN = 9

# IPoolDataProvider.getUserReserveData(asset, user) output ordering:
# (currentATokenBalance, currentStableDebt, currentVariableDebt,
#  principalStableDebt, scaledVariableDebt, stableBorrowRate, liquidityRate,
#  stableRateLastUpdated, usageAsCollateralEnabled)
USER_RESERVE_ATOKEN_BALANCE = 0
USER_RESERVE_STABLE_DEBT = 1
USER_RESERVE_VARIABLE_DEBT = 2
USER_RESERVE_PRINCIPAL_STABLE_DEBT = 3
USER_RESERVE_STABLE_BORROW_RATE = 5
USER_RESERVE_LIQUIDITY_RATE = 6
USER_RESERVE_USAGE_AS_COLLATERAL = 8

# IPool.getUserAccountData(user) output ordering (see aave_v3_pool.json):
# (totalCollateralBase, totalDebtBase, availableBorrowsBase,
#  currentLiquidationThreshold, ltv, healthFactor)
ACCOUNT_DATA_TOTAL_COLLATERAL_BASE = 0
ACCOUNT_DATA_TOTAL_DEBT_BASE = 1
ACCOUNT_DATA_AVAILABLE_BORROWS_BASE = 2
ACCOUNT_DATA_LIQUIDATION_THRESHOLD = 3
ACCOUNT_DATA_LTV = 4
ACCOUNT_DATA_HEALTH_FACTOR = 5

#: One decoded ``getUserReserveData`` tuple, indexed by the USER_RESERVE_*
#: constants above.
type ReserveTuple = tuple[int | bool, ...]
#: One enumerated reserve: ``(symbol, token_address, user_reserve_data)``.
type UserReserveEntry = tuple[str, str, ReserveTuple]


def ray_to_decimal(value: int) -> Decimal:
    """Convert a ray-scaled (1e27) protocol value to a decimal fraction."""
    return Decimal(value) / RAY


def bps_to_decimal(value: int) -> Decimal:
    """Convert a basis-points (1e4) protocol value to a decimal fraction."""
    return Decimal(value) / BPS


def wad_to_decimal(value: int) -> Decimal:
    """Convert a wad-scaled (1e18) protocol value to a Decimal."""
    return Decimal(value) / WAD


def base_currency_to_decimal(value: int) -> Decimal:
    """Convert a base-currency (8 decimals) amount to a Decimal."""
    return Decimal(value) / BASE_CURRENCY_UNIT


def apr_to_apy(apr: Decimal) -> Decimal:
    """Convert an Aave APR into the per-second compounded APY.

    Aave accrues interest every second, so the effective annual yield is
    ``(1 + apr / seconds_per_year) ** seconds_per_year - 1``.
    """
    return (Decimal(1) + apr / SECONDS_PER_YEAR) ** SECONDS_PER_YEAR - Decimal(1)


class AaveV3Configuration(EVMMoneyMarketConfiguration):
    """Configuration for the Aave V3 money market.

    Defaults to the Ethereum mainnet deployment (Pool and
    AaveProtocolDataProvider); pass explicit ``protocols`` entries to target
    other networks. At least one protocol whose name contains ``aave`` is
    required.
    """

    protocols: list[ProtocolConfiguration] = Field(
        default_factory=lambda: [
            ProtocolConfiguration(
                protocol_name="aave_v3",
                lending_pool_address=AAVE_V3_ETHEREUM_POOL_ADDRESS,
                data_provider_address=AAVE_V3_ETHEREUM_DATA_PROVIDER_ADDRESS,
            )
        ]
    )

    @model_validator(mode="after")
    def validate_aave_protocol_present(self) -> Self:
        """Require at least one Aave protocol configuration."""
        if not any("aave" in p.protocol_name.lower() for p in self.protocols):
            raise ValueError(
                "AaveV3Configuration requires at least one Aave protocol configuration"
            )
        return self


class AaveV3PoolContract(EthereumSmartContract):
    """Aave V3 Pool contract interface (vendored official IPool ABI)."""

    def __init__(self, address: EthereumAddress, platform: BlockchainPlatform) -> None:
        """Bind the Pool contract to the given deployment and platform.

        Args:
            address: The deployed Pool contract address
            platform: The blockchain platform the contract lives on
        """
        config = EthereumContractConfiguration(
            platform=platform,
            address=address,
            abi_configuration=EthereumLocalFileABI(file_name="aave_v3_pool.json"),
        )
        super().__init__(config)


class AaveV3DataProviderContract(EthereumSmartContract):
    """Aave V3 PoolDataProvider interface (vendored official ABI)."""

    def __init__(self, address: EthereumAddress, platform: BlockchainPlatform) -> None:
        """Bind the data provider contract to the given deployment and platform.

        Args:
            address: The deployed AaveProtocolDataProvider contract address
            platform: The blockchain platform the contract lives on
        """
        config = EthereumContractConfiguration(
            platform=platform,
            address=address,
            abi_configuration=EthereumLocalFileABI(
                file_name="aave_v3_data_provider.json"
            ),
        )
        super().__init__(config)


class AaveV3:
    """Aave V3 strategy fulfilling the money-market ProtocolImplementation contract.

    Read paths (market data, account data, positions) work without a wallet.
    ``build_*`` methods require a bound wallet (constructor ``wallet`` kwarg or
    :meth:`set_wallet`) and return an **unsigned** :class:`EthereumTransaction`
    in ``PENDING_BROADCAST`` state; the built web3 transaction parameters are
    exposed in ``other_data[UNSIGNED_TX_DATA_KEY]`` (see
    :mod:`blockchainpype.evm.dapp.unsigned`) so callers can sign and broadcast
    via ``wallet.sign_and_send_transaction``.
    """

    def __init__(
        self,
        protocol_config: ProtocolConfiguration,
        platform: BlockchainPlatform,
        wallet: EthereumWallet | None = None,
    ) -> None:
        """Initialize the strategy against a specific deployment.

        Args:
            protocol_config: Protocol name and contract addresses
            platform: Blockchain platform the contracts are deployed on
                (threaded through to every contract this strategy creates)
            wallet: Optional wallet used to build transactions; can also be
                bound later via :meth:`set_wallet`
        """
        self.protocol_config = protocol_config
        self._platform = platform
        self._wallet: EthereumWallet | None = wallet

        self.pool_contract = AaveV3PoolContract(
            EthereumAddress.from_string(protocol_config.lending_pool_address),
            platform,
        )
        self.data_provider_contract = AaveV3DataProviderContract(
            EthereumAddress.from_string(protocol_config.data_provider_address),
            platform,
        )

    # === Wallet binding ===

    def set_wallet(self, wallet: BlockchainWallet | None) -> None:
        """Bind (or unbind, with ``None``) the wallet used to build transactions.

        Args:
            wallet: The wallet to bind; must be an :class:`EthereumWallet`

        Raises:
            TypeError: If the wallet is not an EthereumWallet
        """
        if wallet is not None and not isinstance(wallet, EthereumWallet):
            raise TypeError(
                f"AaveV3 requires an EthereumWallet, got {type(wallet).__name__}"
            )
        self._wallet = wallet

    def _require_wallet(self) -> EthereumWallet:
        """Return the bound wallet or raise when transactions cannot be built."""
        if self._wallet is None:
            raise ValueError(
                "No wallet bound: bind one with set_wallet() before building "
                "Aave V3 transactions"
            )
        return self._wallet

    # === Helpers ===

    async def _ensure_contracts_initialized(self) -> None:
        """Lazily initialize the Pool and data provider contracts."""
        if not self.pool_contract.is_initialized:
            await self.pool_contract.initialize()
        if not self.data_provider_contract.is_initialized:
            await self.data_provider_contract.initialize()

    async def _resolve_asset(self, asset: BlockchainAsset) -> EthereumAsset:
        """Cast to an EthereumAsset and make sure its metadata is available."""
        ethereum_asset = cast(EthereumAsset, asset)
        if ethereum_asset.data is None:
            await ethereum_asset.initialize_data()
        if ethereum_asset.data is None:
            raise ValueError(
                f"Asset data could not be initialized for {ethereum_asset.address.string}"
            )
        return ethereum_asset

    def _build_reserve_token(
        self, symbol: str, token_address: str, decimals: int
    ) -> ERC20Token:
        """Build a real ERC20Token asset for a reserve underlying token."""
        address = EthereumAddress.from_string(token_address)
        contract = ERC20Contract(
            ERC20ContractConfiguration(platform=self._platform, address=address)
        )
        return ERC20Token(
            platform=self._platform,
            identifier=address,
            contract=contract,
            data=EthereumAssetData(name=symbol, symbol=symbol, decimals=decimals),
        )

    async def _build_pool_transaction(
        self, function: AsyncContractFunction, operation: str
    ) -> EthereumTransaction:
        """Build an unsigned, trackable transaction for a Pool function call.

        The bound wallet builds the web3 transaction parameters (calldata, gas
        and fees) which are carried in ``other_data[UNSIGNED_TX_DATA_KEY]``;
        the returned transaction is neither signed nor broadcast.
        """
        wallet = self._require_wallet()
        tx_params = await wallet.build_transaction(function=function)
        return build_unsigned_transaction(
            f"aave-v3-{operation}-{uuid.uuid4().hex}", wallet, tx_params
        )

    # === Read paths (wallet-less) ===

    async def get_market_data(self, asset: BlockchainAsset) -> MarketData:
        """Get market data for a specific asset from the data provider."""
        ethereum_asset = await self._resolve_asset(asset)
        await self._ensure_contracts_initialized()

        asset_address = ethereum_asset.address.raw
        reserve_data = await self.data_provider_contract.functions.getReserveData(
            asset_address
        ).call()
        configuration_data = (
            await self.data_provider_contract.functions.getReserveConfigurationData(
                asset_address
            ).call()
        )

        total_supply = ethereum_asset.convert_to_decimals(
            int(reserve_data[RESERVE_DATA_TOTAL_ATOKEN])
        )
        total_borrows = ethereum_asset.convert_to_decimals(
            int(reserve_data[RESERVE_DATA_TOTAL_STABLE_DEBT])
            + int(reserve_data[RESERVE_DATA_TOTAL_VARIABLE_DEBT])
        )
        utilization_rate = (
            total_borrows / total_supply if total_supply > 0 else Decimal(0)
        )
        liquidity_rate = ray_to_decimal(int(reserve_data[RESERVE_DATA_LIQUIDITY_RATE]))

        return MarketData(
            asset=ethereum_asset,
            supply_apy=apr_to_apy(liquidity_rate),
            variable_borrow_apy=apr_to_apy(
                ray_to_decimal(int(reserve_data[RESERVE_DATA_VARIABLE_BORROW_RATE]))
            ),
            stable_borrow_apy=apr_to_apy(
                ray_to_decimal(int(reserve_data[RESERVE_DATA_STABLE_BORROW_RATE]))
            ),
            total_supply=total_supply,
            total_borrows=total_borrows,
            utilization_rate=utilization_rate,
            liquidity_rate=liquidity_rate,
            liquidation_threshold=bps_to_decimal(
                int(configuration_data[CONFIGURATION_DATA_LIQUIDATION_THRESHOLD])
            ),
            loan_to_value=bps_to_decimal(
                int(configuration_data[CONFIGURATION_DATA_LTV])
            ),
            reserve_factor=bps_to_decimal(
                int(configuration_data[CONFIGURATION_DATA_RESERVE_FACTOR])
            ),
            is_borrowing_enabled=bool(
                configuration_data[CONFIGURATION_DATA_BORROWING_ENABLED]
            ),
            is_stable_rate_enabled=bool(
                configuration_data[CONFIGURATION_DATA_STABLE_RATE_ENABLED]
            ),
            is_frozen=bool(configuration_data[CONFIGURATION_DATA_IS_FROZEN]),
            protocol=self.protocol_config.protocol_name,
        )

    async def get_user_account_data(self, user_address: str) -> UserAccountData:
        """Get the user's aggregated account data from the Pool contract."""
        await self._ensure_contracts_initialized()

        account_data = await self.pool_contract.functions.getUserAccountData(
            EthereumAddress.from_string(user_address).raw
        ).call()

        return UserAccountData(
            total_collateral_value=base_currency_to_decimal(
                int(account_data[ACCOUNT_DATA_TOTAL_COLLATERAL_BASE])
            ),
            total_debt_value=base_currency_to_decimal(
                int(account_data[ACCOUNT_DATA_TOTAL_DEBT_BASE])
            ),
            available_borrow_value=base_currency_to_decimal(
                int(account_data[ACCOUNT_DATA_AVAILABLE_BORROWS_BASE])
            ),
            current_liquidation_threshold=bps_to_decimal(
                int(account_data[ACCOUNT_DATA_LIQUIDATION_THRESHOLD])
            ),
            loan_to_value=bps_to_decimal(int(account_data[ACCOUNT_DATA_LTV])),
            health_factor=wad_to_decimal(int(account_data[ACCOUNT_DATA_HEALTH_FACTOR])),
            protocol=self.protocol_config.protocol_name,
        )

    async def _iter_user_reserves(self, user_address: str) -> list[UserReserveEntry]:
        """Enumerate all reserves with the user's per-reserve data.

        The reserve list costs one call; the per-reserve ``getUserReserveData``
        lookups that follow are independent read calls, so they are issued
        concurrently with :func:`asyncio.gather` instead of one round trip
        after another (Ethereum mainnet lists ~35 reserves, which is ~35
        sequential round trips when awaited in a loop).

        Returns:
            A list of ``(symbol, token_address, user_reserve_data)`` tuples in
            ``getAllReservesTokens`` order, one per reserve.
        """
        await self._ensure_contracts_initialized()
        user = EthereumAddress.from_string(user_address).raw

        reserves = (
            await self.data_provider_contract.functions.getAllReservesTokens().call()
        )
        listed = [
            (str(symbol), str(token_address)) for symbol, token_address in reserves
        ]
        user_data = await asyncio.gather(
            *(
                self.data_provider_contract.functions.getUserReserveData(
                    token_address, user
                ).call()
                for _, token_address in listed
            )
        )
        return [
            (symbol, token_address, tuple(data))
            for (symbol, token_address), data in zip(listed, user_data, strict=True)
        ]

    async def _get_reserve_decimals(self, token_address: str) -> int:
        """Fetch a reserve's decimals from its on-chain configuration data."""
        configuration_data = (
            await self.data_provider_contract.functions.getReserveConfigurationData(
                token_address
            ).call()
        )
        return int(configuration_data[CONFIGURATION_DATA_DECIMALS])

    async def _get_variable_borrow_apy(self, token_address: str) -> Decimal:
        """Fetch a reserve's current variable borrow rate as an APY."""
        reserve_data = await self.data_provider_contract.functions.getReserveData(
            token_address
        ).call()
        return apr_to_apy(
            ray_to_decimal(int(reserve_data[RESERVE_DATA_VARIABLE_BORROW_RATE]))
        )

    def _build_lending_position(
        self, symbol: str, token_address: str, user_data: ReserveTuple, decimals: int
    ) -> LendingPosition:
        """Assemble one lending position from already-fetched reserve data."""
        token = self._build_reserve_token(symbol, token_address, decimals)
        return LendingPosition(
            asset=token,
            supplied_amount=token.convert_to_decimals(
                int(user_data[USER_RESERVE_ATOKEN_BALANCE])
            ),
            accrued_interest=Decimal(0),
            apy=apr_to_apy(ray_to_decimal(int(user_data[USER_RESERVE_LIQUIDITY_RATE]))),
            is_collateral=bool(user_data[USER_RESERVE_USAGE_AS_COLLATERAL]),
            protocol=self.protocol_config.protocol_name,
        )

    async def _lending_positions_from(
        self, reserves: Sequence[UserReserveEntry]
    ) -> list[LendingPosition]:
        """Build the lending positions of an already-enumerated reserve list.

        Only reserves with a non-zero aToken balance need their decimals, and
        those lookups are independent, so they are fetched concurrently.
        """
        held = [
            entry
            for entry in reserves
            if int(entry[2][USER_RESERVE_ATOKEN_BALANCE]) != 0
        ]
        decimals = await asyncio.gather(
            *(self._get_reserve_decimals(token_address) for _, token_address, _ in held)
        )
        return [
            self._build_lending_position(symbol, token_address, user_data, reserve_dp)
            for (symbol, token_address, user_data), reserve_dp in zip(
                held, decimals, strict=True
            )
        ]

    async def _borrowing_positions_from(
        self, reserves: Sequence[UserReserveEntry]
    ) -> list[BorrowingPosition]:
        """Build the borrowing positions of an already-enumerated reserve list.

        Reserve decimals (one per indebted reserve) and variable borrow rates
        (one per reserve carrying variable debt) are independent read calls,
        so each set is fetched concurrently rather than inside the loop.
        """
        indebted = [
            entry
            for entry in reserves
            if int(entry[2][USER_RESERVE_STABLE_DEBT]) != 0
            or int(entry[2][USER_RESERVE_VARIABLE_DEBT]) != 0
        ]
        decimals = await asyncio.gather(
            *(
                self._get_reserve_decimals(token_address)
                for _, token_address, _ in indebted
            )
        )
        variable_addresses = [
            token_address
            for _, token_address, user_data in indebted
            if int(user_data[USER_RESERVE_VARIABLE_DEBT]) > 0
        ]
        variable_apys = dict(
            zip(
                variable_addresses,
                await asyncio.gather(
                    *(
                        self._get_variable_borrow_apy(token_address)
                        for token_address in variable_addresses
                    )
                ),
                strict=True,
            )
        )

        positions: list[BorrowingPosition] = []
        for (symbol, token_address, user_data), reserve_dp in zip(
            indebted, decimals, strict=True
        ):
            stable_debt = int(user_data[USER_RESERVE_STABLE_DEBT])
            variable_debt = int(user_data[USER_RESERVE_VARIABLE_DEBT])
            token = self._build_reserve_token(symbol, token_address, reserve_dp)

            if variable_debt > 0:
                positions.append(
                    BorrowingPosition(
                        asset=token,
                        borrowed_amount=token.convert_to_decimals(variable_debt),
                        accrued_interest=Decimal(0),
                        interest_rate_mode=InterestRateMode.VARIABLE,
                        current_rate=variable_apys[token_address],
                        protocol=self.protocol_config.protocol_name,
                    )
                )

            if stable_debt > 0:
                principal = min(
                    int(user_data[USER_RESERVE_PRINCIPAL_STABLE_DEBT]), stable_debt
                )
                positions.append(
                    BorrowingPosition(
                        asset=token,
                        borrowed_amount=token.convert_to_decimals(principal),
                        accrued_interest=token.convert_to_decimals(
                            stable_debt - principal
                        ),
                        interest_rate_mode=InterestRateMode.STABLE,
                        current_rate=apr_to_apy(
                            ray_to_decimal(
                                int(user_data[USER_RESERVE_STABLE_BORROW_RATE])
                            )
                        ),
                        protocol=self.protocol_config.protocol_name,
                    )
                )
        return positions

    async def get_positions(
        self, user_address: str
    ) -> tuple[list[LendingPosition], list[BorrowingPosition]]:
        """Get the user's lending and borrowing positions in one pass.

        Both sides are derived from the same reserve enumeration, so this
        costs one ``getAllReservesTokens`` plus one concurrent round of
        ``getUserReserveData`` — half the calls of invoking
        :meth:`get_lending_positions` and :meth:`get_borrowing_positions`
        separately, which enumerate once each. Prefer it whenever both sides
        are needed; the two single-sided getters are unchanged for callers
        that only want one.

        Returns:
            A ``(lending_positions, borrowing_positions)`` tuple
        """
        reserves = await self._iter_user_reserves(user_address)
        lending, borrowing = await asyncio.gather(
            self._lending_positions_from(reserves),
            self._borrowing_positions_from(reserves),
        )
        return lending, borrowing

    async def get_lending_positions(self, user_address: str) -> list[LendingPosition]:
        """Get the user's lending positions across all Aave V3 reserves.

        aToken balances rebase, so ``supplied_amount`` is the current balance
        (interest included) and ``accrued_interest`` is reported as zero: the
        protocol does not expose the originally supplied principal on-chain.

        Use :meth:`get_positions` when the borrowing side is needed too: it
        shares this method's single reserve enumeration.
        """
        return await self._lending_positions_from(
            await self._iter_user_reserves(user_address)
        )

    async def get_borrowing_positions(
        self, user_address: str
    ) -> list[BorrowingPosition]:
        """Get the user's borrowing positions across all Aave V3 reserves.

        A reserve can hold both a variable-rate and a stable-rate debt; each
        yields its own position. For stable debt the accrued interest is the
        difference between the current and the principal stable debt; variable
        debt has no on-chain principal, so the current debt is reported as
        ``borrowed_amount`` with zero ``accrued_interest``.

        Use :meth:`get_positions` when the lending side is needed too: it
        shares this method's single reserve enumeration.
        """
        return await self._borrowing_positions_from(
            await self._iter_user_reserves(user_address)
        )

    # === Transaction building (wallet required) ===

    async def build_supply_transaction(
        self,
        asset: BlockchainAsset,
        amount: Decimal,
        user_address: str,
        enable_as_collateral: bool = True,
    ) -> EthereumTransaction:
        """Build an unsigned ``Pool.supply`` transaction.

        Aave V3 automatically enables a first-time supply as collateral, so
        ``enable_as_collateral=True`` needs no extra action. To opt out,
        follow up with
        :meth:`build_collateral_transaction`(:attr:`CollateralMode.DISABLED`).
        """
        self._require_wallet()
        ethereum_asset = await self._resolve_asset(asset)
        await self._ensure_contracts_initialized()

        function = self.pool_contract.functions.supply(
            ethereum_asset.address.raw,
            ethereum_asset.convert_to_raw(amount),
            EthereumAddress.from_string(user_address).raw,
            REFERRAL_CODE_NONE,
        )
        return await self._build_pool_transaction(function, "supply")

    async def build_withdraw_transaction(
        self,
        asset: BlockchainAsset,
        amount: Decimal,
        user_address: str,
        withdraw_all: bool = False,
    ) -> EthereumTransaction:
        """Build an unsigned ``Pool.withdraw`` transaction.

        With ``withdraw_all`` the on-chain amount is ``type(uint256).max``,
        which Aave interprets as the full aToken balance.
        """
        self._require_wallet()
        ethereum_asset = await self._resolve_asset(asset)
        await self._ensure_contracts_initialized()

        raw_amount = (
            UINT256_MAX if withdraw_all else ethereum_asset.convert_to_raw(amount)
        )
        function = self.pool_contract.functions.withdraw(
            ethereum_asset.address.raw,
            raw_amount,
            EthereumAddress.from_string(user_address).raw,
        )
        return await self._build_pool_transaction(function, "withdraw")

    async def build_borrow_transaction(
        self,
        asset: BlockchainAsset,
        amount: Decimal,
        interest_rate_mode: InterestRateMode,
        user_address: str,
    ) -> EthereumTransaction:
        """Build an unsigned ``Pool.borrow`` transaction."""
        self._require_wallet()
        ethereum_asset = await self._resolve_asset(asset)
        await self._ensure_contracts_initialized()

        function = self.pool_contract.functions.borrow(
            ethereum_asset.address.raw,
            ethereum_asset.convert_to_raw(amount),
            self._to_aave_rate_mode(interest_rate_mode),
            REFERRAL_CODE_NONE,
            EthereumAddress.from_string(user_address).raw,
        )
        return await self._build_pool_transaction(function, "borrow")

    async def build_repay_transaction(
        self,
        asset: BlockchainAsset,
        amount: Decimal,
        interest_rate_mode: InterestRateMode,
        user_address: str,
        repay_all: bool = False,
    ) -> EthereumTransaction:
        """Build an unsigned ``Pool.repay`` transaction.

        With ``repay_all`` the on-chain amount is ``type(uint256).max``, which
        Aave interprets as the entire outstanding debt for the rate mode.
        """
        self._require_wallet()
        ethereum_asset = await self._resolve_asset(asset)
        await self._ensure_contracts_initialized()

        raw_amount = UINT256_MAX if repay_all else ethereum_asset.convert_to_raw(amount)
        function = self.pool_contract.functions.repay(
            ethereum_asset.address.raw,
            raw_amount,
            self._to_aave_rate_mode(interest_rate_mode),
            EthereumAddress.from_string(user_address).raw,
        )
        return await self._build_pool_transaction(function, "repay")

    async def build_collateral_transaction(
        self,
        asset: BlockchainAsset,
        mode: CollateralMode,
        user_address: str,
    ) -> EthereumTransaction:
        """Build an unsigned ``Pool.setUserUseReserveAsCollateral`` transaction.

        The Pool applies the change to ``msg.sender``; ``user_address`` is part
        of the abstract contract but has no on-chain argument here.
        """
        self._require_wallet()
        ethereum_asset = await self._resolve_asset(asset)
        await self._ensure_contracts_initialized()

        function = self.pool_contract.functions.setUserUseReserveAsCollateral(
            ethereum_asset.address.raw,
            mode == CollateralMode.ENABLED,
        )
        return await self._build_pool_transaction(function, "set-collateral")

    async def build_liquidation_transaction(
        self,
        collateral_asset: BlockchainAsset,
        debt_asset: BlockchainAsset,
        user_to_liquidate: str,
        debt_to_cover: Decimal,
        receive_collateral: bool = True,
    ) -> EthereumTransaction:
        """Build an unsigned ``Pool.liquidationCall`` transaction.

        ``receive_collateral=True`` means the liquidator wants the underlying
        collateral, so the on-chain ``receiveAToken`` flag is its inverse
        (``receiveAToken=True`` would keep the position as aTokens).
        """
        self._require_wallet()
        collateral_ethereum_asset = await self._resolve_asset(collateral_asset)
        debt_ethereum_asset = await self._resolve_asset(debt_asset)
        await self._ensure_contracts_initialized()

        function = self.pool_contract.functions.liquidationCall(
            collateral_ethereum_asset.address.raw,
            debt_ethereum_asset.address.raw,
            EthereumAddress.from_string(user_to_liquidate).raw,
            debt_ethereum_asset.convert_to_raw(debt_to_cover),
            not receive_collateral,
        )
        return await self._build_pool_transaction(function, "liquidation")

    @staticmethod
    def _to_aave_rate_mode(interest_rate_mode: InterestRateMode) -> int:
        """Map the abstract interest rate mode to Aave's on-chain encoding."""
        if interest_rate_mode == InterestRateMode.STABLE:
            return AAVE_STABLE_RATE_MODE
        return AAVE_VARIABLE_RATE_MODE


class AaveV3MoneyMarket(EVMMoneyMarket):
    """Aave V3 money market facade.

    Registers one :class:`AaveV3` strategy per configured protocol whose name
    contains ``aave``, threading the configuration platform through to every
    contract. A wallet may be bound at construction or later via
    :meth:`set_wallet`; read paths work without one.
    """

    def __init__(
        self,
        configuration: EVMMoneyMarketConfiguration,
        wallet: EthereumWallet | None = None,
    ) -> None:
        """Initialize the facade and its protocol strategies.

        Args:
            configuration: The money market configuration (typically an
                :class:`AaveV3Configuration`)
            wallet: Optional wallet bound to every strategy for building
                transactions
        """
        self._wallet: EthereumWallet | None = wallet
        self._aave_strategies: list[AaveV3] = []
        super().__init__(configuration)

    def _initialize_protocols(self) -> None:
        """Register an AaveV3 strategy for each Aave protocol configuration."""
        for protocol_config in self.configuration.protocols:
            if "aave" not in protocol_config.protocol_name.lower():
                continue
            strategy = AaveV3(
                protocol_config,
                platform=self.configuration.platform,
                wallet=self._wallet,
            )
            self._aave_strategies.append(strategy)
            self._protocol_strategies[protocol_config.protocol_name] = strategy

    def set_wallet(self, wallet: EthereumWallet | None) -> None:
        """Bind (or unbind, with ``None``) the wallet on every strategy."""
        self._wallet = wallet
        for strategy in self._aave_strategies:
            strategy.set_wallet(wallet)
