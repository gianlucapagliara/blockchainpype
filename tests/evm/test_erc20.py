"""
Unit tests for the ERC-20 contract interface: read methods (unit policy: human
decimal-adjusted amounts with raw accessors), calldata building for
transfer/transferFrom/approve, and the ERC20Token asset wrapper.

All tests are network-free: the JSON-RPC boundary is replaced by a fake
provider returning realistic ABI-encoded payloads.
"""

import asyncio
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest
from eth_abi import encode
from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_account.typed_transactions import TypedTransaction
from financepype.operations.transactions.models import BlockchainTransactionState
from financepype.platforms.blockchain import BlockchainPlatform
from hexbytes import HexBytes

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
from blockchainpype.evm.dapp.abi import EthereumLocalFileABI
from blockchainpype.evm.dapp.erc20 import (
    ERC20Contract,
    ERC20ContractConfiguration,
    ERC20Token,
)
from blockchainpype.evm.transaction import EthereumTransaction
from blockchainpype.evm.wallet.wallet import EthereumWallet
from tests.evm.test_wallet import (
    TX_HASH_HEX,
    FakeRPCProvider,
    build_wallet,
    drain_background_tasks,
)

TOKEN_ADDRESS = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
RECIPIENT = "0x5B38Da6a701c568545dCfcB03FcB875f56beddC4"
SPENDER = "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984"

# Canonical ERC-20 function selectors (keccak256 of the signature, first 4 bytes)
SEL_NAME = "0x06fdde03"
SEL_SYMBOL = "0x95d89b41"
SEL_DECIMALS = "0x313ce567"
SEL_TOTAL_SUPPLY = "0x18160ddd"
SEL_BALANCE_OF = "0x70a08231"
SEL_ALLOWANCE = "0xdd62ed3e"
SEL_TRANSFER = "0xa9059cbb"
SEL_APPROVE = "0x095ea7b3"
SEL_TRANSFER_FROM = "0x23b872dd"

TOKEN_DECIMALS = 6
RAW_BALANCE = 1_500_000  # 1.5 tokens
RAW_TOTAL_SUPPLY = 1_000_000_000_000  # 1,000,000 tokens
RAW_ALLOWANCE = 250_000  # 0.25 tokens


def encode_uint(value: int) -> str:
    return "0x" + encode(["uint256"], [value]).hex()


def eth_call_handler(params: Any) -> str:
    """Answer ERC-20 view calls with realistic ABI-encoded payloads."""
    call = params[0]
    data = str(call.get("data") or call.get("input"))
    selector = data[:10].lower()
    if selector == SEL_DECIMALS:
        return "0x" + encode(["uint8"], [TOKEN_DECIMALS]).hex()
    if selector == SEL_NAME:
        return "0x" + encode(["string"], ["Test USD"]).hex()
    if selector == SEL_SYMBOL:
        return "0x" + encode(["string"], ["TUSD"]).hex()
    if selector == SEL_TOTAL_SUPPLY:
        return encode_uint(RAW_TOTAL_SUPPLY)
    if selector == SEL_BALANCE_OF:
        return encode_uint(RAW_BALANCE)
    if selector == SEL_ALLOWANCE:
        return encode_uint(RAW_ALLOWANCE)
    raise AssertionError(f"Unexpected eth_call selector: {selector}")


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
def rpc_provider() -> FakeRPCProvider:
    return FakeRPCProvider(
        {
            "eth_call": eth_call_handler,
            "eth_chainId": "0x1",
            "eth_getBalance": "0xde0b6b3a7640000",
            "eth_getTransactionCount": "0x2",
            "eth_sendRawTransaction": TX_HASH_HEX,
            "eth_estimateGas": "0xc350",  # 50,000
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
def ethereum_config(
    rpc_provider: FakeRPCProvider,
) -> EthereumBlockchainConfiguration:
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
) -> EthereumBlockchain:
    return EthereumBlockchain(configuration=ethereum_config)


@pytest.fixture
def erc20_contract(ethereum_blockchain: EthereumBlockchain) -> ERC20Contract:
    """ERC-20 contract bound to the fake-RPC blockchain."""
    with patch(
        "financepype.operators.dapps.dapp.OperatorFactory.get",
        return_value=ethereum_blockchain,
    ):
        contract = ERC20Contract(
            ERC20ContractConfiguration(
                platform=ethereum_blockchain.platform,
                address=EthereumAddress.from_string(TOKEN_ADDRESS),
            )
        )
    return contract


@pytest.fixture
def test_account() -> LocalAccount:
    return Account.create()


@pytest.fixture
async def ethereum_wallet(
    ethereum_config: EthereumBlockchainConfiguration,
    test_account: LocalAccount,
    ethereum_blockchain: EthereumBlockchain,
) -> EthereumWallet:
    wallet = build_wallet(ethereum_config, test_account, ethereum_blockchain)
    if wallet._background_tasks:
        await asyncio.gather(*wallet._background_tasks, return_exceptions=True)
    return wallet


# === Configuration / ABI ===


def test_default_abi_is_canonical_erc20() -> None:
    config = ERC20ContractConfiguration(
        platform=BlockchainPlatform(
            identifier="ethereum", type=EthereumBlockchainType, chain_id=1
        ),
        address=EthereumAddress.from_string(TOKEN_ADDRESS),
    )
    abi_configuration = config.abi_configuration
    assert isinstance(abi_configuration, EthereumLocalFileABI)
    assert abi_configuration.file_name == "ERC20.json"


async def test_canonical_erc20_abi_contains_standard_interface() -> None:
    abi = await EthereumLocalFileABI(file_name="ERC20.json").get_abi()
    assert isinstance(abi, list)
    function_names = {entry["name"] for entry in abi if entry.get("type") == "function"}
    assert function_names == {
        "name",
        "symbol",
        "decimals",
        "totalSupply",
        "balanceOf",
        "allowance",
        "transfer",
        "transferFrom",
        "approve",
    }
    event_names = {entry["name"] for entry in abi if entry.get("type") == "event"}
    assert event_names == {"Transfer", "Approval"}


# === Read methods / unit policy ===


async def test_read_methods_return_decimal_adjusted_amounts(
    erc20_contract: ERC20Contract,
) -> None:
    await erc20_contract.initialize()

    assert await erc20_contract.get_name() == "Test USD"
    assert await erc20_contract.get_symbol() == "TUSD"
    assert await erc20_contract.get_decimals() == TOKEN_DECIMALS

    holder = EthereumAddress.from_string(RECIPIENT)
    spender = EthereumAddress.from_string(SPENDER)

    assert await erc20_contract.get_balance_of(holder) == Decimal("1.5")
    assert await erc20_contract.get_total_supply() == Decimal("1000000")
    assert await erc20_contract.get_allowance(holder, spender) == Decimal("0.25")


async def test_raw_accessors_return_smallest_units(
    erc20_contract: ERC20Contract,
) -> None:
    await erc20_contract.initialize()
    holder = EthereumAddress.from_string(RECIPIENT)
    spender = EthereumAddress.from_string(SPENDER)

    assert await erc20_contract.get_raw_balance_of(holder) == RAW_BALANCE
    assert await erc20_contract.get_raw_total_supply() == RAW_TOTAL_SUPPLY
    assert await erc20_contract.get_raw_allowance(holder, spender) == RAW_ALLOWANCE


async def test_decimals_are_cached(
    erc20_contract: ERC20Contract, rpc_provider: FakeRPCProvider
) -> None:
    await erc20_contract.initialize()
    holder = EthereumAddress.from_string(RECIPIENT)

    await erc20_contract.get_decimals()
    await erc20_contract.get_decimals()
    await erc20_contract.get_balance_of(holder)
    await erc20_contract.get_total_supply()

    decimals_calls = [
        params
        for params in rpc_provider.calls_for("eth_call")
        if str(params[0].get("data", "")).lower().startswith(SEL_DECIMALS)
    ]
    assert len(decimals_calls) == 1


# === Write methods: calldata + broadcast ===


def pad_address(address: str) -> str:
    return address[2:].lower().rjust(64, "0")


def pad_uint(value: int) -> str:
    return f"{value:064x}"


async def test_place_transfer_builds_exact_calldata(
    erc20_contract: ERC20Contract,
    ethereum_wallet: EthereumWallet,
    rpc_provider: FakeRPCProvider,
) -> None:
    recipient = EthereumAddress.from_string(RECIPIENT)

    transaction = await erc20_contract.place_transfer(
        ethereum_wallet, recipient, Decimal("1.5"), client_operation_id="transfer-1"
    )
    await drain_background_tasks(ethereum_wallet)

    assert isinstance(transaction, EthereumTransaction)
    assert transaction.current_state == BlockchainTransactionState.BROADCASTED
    assert (
        ethereum_wallet.transaction_tracker.fetch_tracked_operation("transfer-1")
        is transaction
    )

    # transfer(address,uint256) with 1.5 tokens at 6 decimals = 1,500,000 raw
    expected_data = SEL_TRANSFER + pad_address(RECIPIENT) + pad_uint(1_500_000)

    estimate_calls = rpc_provider.calls_for("eth_estimateGas")
    assert estimate_calls, "gas estimation must run against the built calldata"
    assert str(estimate_calls[0][0]["data"]).lower() == expected_data

    # The signed, broadcast transaction carries the same calldata and the
    # wallet-configured EIP-1559 fees
    raw_tx_hex = rpc_provider.calls_for("eth_sendRawTransaction")[0][0]
    decoded = TypedTransaction.from_bytes(HexBytes(raw_tx_hex)).as_dict()
    assert HexBytes(decoded["data"]).to_0x_hex() == expected_data
    assert HexBytes(decoded["to"]).to_0x_hex().lower() == TOKEN_ADDRESS.lower()
    assert decoded["nonce"] == 2  # synced from eth_getTransactionCount
    assert decoded["gas"] == 65000  # ceil(50,000 estimate * 1.3)
    assert decoded["maxPriorityFeePerGas"] == 1_000_000_000  # fee history avg
    assert decoded["maxFeePerGas"] == 21_000_000_000  # 2 * 10 gwei base + tip


async def test_place_approve_builds_exact_calldata_and_allowance_reads_back(
    erc20_contract: ERC20Contract,
    ethereum_wallet: EthereumWallet,
    rpc_provider: FakeRPCProvider,
) -> None:
    spender = EthereumAddress.from_string(SPENDER)

    transaction = await erc20_contract.place_approve(
        ethereum_wallet, spender, Decimal("0.25"), client_operation_id="approve-1"
    )
    await drain_background_tasks(ethereum_wallet)

    assert transaction.current_state == BlockchainTransactionState.BROADCASTED

    expected_data = SEL_APPROVE + pad_address(SPENDER) + pad_uint(250_000)
    raw_tx_hex = rpc_provider.calls_for("eth_sendRawTransaction")[0][0]
    decoded = TypedTransaction.from_bytes(HexBytes(raw_tx_hex)).as_dict()
    assert HexBytes(decoded["data"]).to_0x_hex() == expected_data

    # The fake node reports the (canned) allowance in raw units; the contract
    # exposes it decimal-adjusted
    owner = EthereumAddress.from_string(ethereum_wallet.address.string)
    assert await erc20_contract.get_allowance(owner, spender) == Decimal("0.25")


async def test_place_transfer_from_builds_exact_calldata(
    erc20_contract: ERC20Contract,
    ethereum_wallet: EthereumWallet,
    rpc_provider: FakeRPCProvider,
) -> None:
    sender = EthereumAddress.from_string(RECIPIENT)
    recipient = EthereumAddress.from_string(SPENDER)

    transaction = await erc20_contract.place_transfer_from(
        ethereum_wallet,
        sender,
        recipient,
        Decimal("100"),
        client_operation_id="transfer-from-1",
    )
    await drain_background_tasks(ethereum_wallet)

    assert transaction.current_state == BlockchainTransactionState.BROADCASTED

    expected_data = (
        SEL_TRANSFER_FROM
        + pad_address(RECIPIENT)
        + pad_address(SPENDER)
        + pad_uint(100_000_000)
    )
    raw_tx_hex = rpc_provider.calls_for("eth_sendRawTransaction")[0][0]
    decoded = TypedTransaction.from_bytes(HexBytes(raw_tx_hex)).as_dict()
    assert HexBytes(decoded["data"]).to_0x_hex() == expected_data


async def test_place_transfer_generates_operation_id(
    erc20_contract: ERC20Contract,
    ethereum_wallet: EthereumWallet,
) -> None:
    recipient = EthereumAddress.from_string(RECIPIENT)

    transaction = await erc20_contract.place_transfer(
        ethereum_wallet, recipient, Decimal("1")
    )
    await drain_background_tasks(ethereum_wallet)

    assert transaction.client_operation_id.startswith(
        f"erc20-transfer-{erc20_contract.address.string}-"
    )


# === ERC20Token asset ===


async def test_erc20_token_initialize_data(
    erc20_contract: ERC20Contract, ethereum_blockchain: EthereumBlockchain
) -> None:
    token = ERC20Token(
        platform=ethereum_blockchain.platform,
        identifier=erc20_contract.address,
        contract=erc20_contract,
    )
    assert token.data is None

    await token.initialize_data()
    assert token.data is not None
    assert token.data.name == "Test USD"
    assert token.data.symbol == "TUSD"
    assert token.data.decimals == TOKEN_DECIMALS

    assert token.convert_to_raw(Decimal("1.5")) == 1_500_000
    assert token.convert_to_decimals(1_500_000) == Decimal("1.5")


async def test_uninitialized_token_conversions_raise(
    erc20_contract: ERC20Contract, ethereum_blockchain: EthereumBlockchain
) -> None:
    token = ERC20Token(
        platform=ethereum_blockchain.platform,
        identifier=erc20_contract.address,
        contract=erc20_contract,
    )
    with pytest.raises(ValueError, match="not initialized"):
        token.convert_to_raw(Decimal("1"))
    with pytest.raises(ValueError, match="not initialized"):
        token.convert_to_decimals(1)


async def test_wallet_fetch_balance_erc20_is_decimal_adjusted(
    erc20_contract: ERC20Contract,
    ethereum_wallet: EthereumWallet,
    ethereum_blockchain: EthereumBlockchain,
) -> None:
    """Unit policy: ERC-20 balances match the native asset's decimal units."""
    token = ERC20Token(
        platform=ethereum_blockchain.platform,
        identifier=erc20_contract.address,
        contract=erc20_contract,
    )

    balance = await ethereum_wallet.fetch_balance(token)
    assert balance == Decimal("1.5")
    assert erc20_contract.is_initialized  # lazily initialized by fetch_balance
