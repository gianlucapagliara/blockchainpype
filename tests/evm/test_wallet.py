"""
Unit tests for the Ethereum wallet: nonce management, transaction signing,
sign-and-send tracking, broadcast rejection handling, receipt polling and
replacement (speedup/cancel) transactions.

All tests are network-free: the JSON-RPC boundary is replaced by a fake
provider returning realistic payloads.
"""

import asyncio
from datetime import timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest
from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_account.typed_transactions import TypedTransaction
from financepype.operations.transactions.models import (
    BlockchainTransactionState,
)
from financepype.platforms.blockchain import BlockchainPlatform
from financepype.simulations.balances.tracking.tracker import BalanceType
from hexbytes import HexBytes
from pydantic import SecretStr
from web3.exceptions import TransactionNotFound
from web3.providers.async_base import AsyncJSONBaseProvider
from web3.types import RPCEndpoint, RPCResponse, Wei

from blockchainpype.evm.blockchain.blockchain import (
    EthereumBlockchain,
    EthereumBlockchainType,
)
from blockchainpype.evm.blockchain.configuration import (
    EthereumBlockchainConfiguration,
    EthereumConnectivityConfiguration,
    EthereumNativeAssetConfiguration,
)
from blockchainpype.evm.blockchain.gas import GasConfiguration
from blockchainpype.evm.blockchain.identifier import (
    EthereumAddress,
    EthereumTransactionHash,
)
from blockchainpype.evm.transaction import EthereumTransaction
from blockchainpype.evm.wallet.identifier import EthereumWalletIdentifier
from blockchainpype.evm.wallet.signer import (
    EthereumSigner,
    EthereumSignerConfiguration,
)
from blockchainpype.evm.wallet.wallet import EthereumWallet, EthereumWalletConfiguration

TX_HASH_HEX = "0x" + "ab" * 32
RECIPIENT = "0x5B38Da6a701c568545dCfcB03FcB875f56beddC4"


class FakeRPCProvider(AsyncJSONBaseProvider):
    """In-memory JSON-RPC provider answering from a canned response table.

    Values in ``responses`` may be a plain result, a callable receiving the
    request params and returning a result, an Exception instance (raised), or
    a dict containing an ``error`` key (returned as a JSON-RPC error response).
    """

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        super().__init__()
        self.responses: dict[str, Any] = responses or {}
        self.calls: list[tuple[str, Any]] = []

    def calls_for(self, method: str) -> list[Any]:
        return [params for m, params in self.calls if m == method]

    async def make_request(self, method: RPCEndpoint, params: Any) -> RPCResponse:
        self.calls.append((str(method), params))
        if str(method) not in self.responses:
            raise AssertionError(f"Unexpected RPC call: {method} {params}")
        value = self.responses[str(method)]
        if callable(value):
            value = value(params)
        if isinstance(value, Exception):
            raise value
        if isinstance(value, dict) and "error" in value:
            return {"jsonrpc": "2.0", "id": 1, **value}  # type: ignore[typeddict-item]
        return {"jsonrpc": "2.0", "id": 1, "result": value}


def make_receipt_payload(
    tx_hash: str,
    sender: str,
    status: str = "0x1",
    gas_used: str = "0x5208",
    effective_gas_price: str = "0x4a817c800",
) -> dict[str, Any]:
    """Build a realistic post-Byzantium eth_getTransactionReceipt payload."""
    return {
        "blockHash": "0x" + "11" * 32,
        "blockNumber": "0x10d4f",
        "contractAddress": None,
        "cumulativeGasUsed": gas_used,
        "effectiveGasPrice": effective_gas_price,
        "from": sender,
        "gasUsed": gas_used,
        "logs": [],
        "logsBloom": "0x" + "00" * 256,
        "status": status,
        "to": RECIPIENT,
        "transactionHash": tx_hash,
        "transactionIndex": "0x0",
        "type": "0x2",
    }


@pytest.fixture
def rpc_provider() -> FakeRPCProvider:
    """Fake JSON-RPC provider with defaults for balance/nonce/broadcast."""
    return FakeRPCProvider(
        {
            "eth_getBalance": "0xde0b6b3a7640000",  # 1 ETH
            "eth_getTransactionCount": "0x5",
            "eth_sendRawTransaction": TX_HASH_HEX,
        }
    )


@pytest.fixture
def ethereum_config(
    rpc_provider: FakeRPCProvider,
) -> EthereumBlockchainConfiguration:
    """Fixture providing a test Ethereum blockchain configuration."""
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
    """Fixture providing a test Ethereum blockchain instance."""
    return EthereumBlockchain(configuration=ethereum_config)


@pytest.fixture
def test_account() -> LocalAccount:
    """Fixture providing a test Ethereum account."""
    return Account.create()


def build_wallet(
    ethereum_config: EthereumBlockchainConfiguration,
    test_account: LocalAccount,
    ethereum_blockchain: EthereumBlockchain,
) -> EthereumWallet:
    identifier = EthereumWalletIdentifier(
        platform=ethereum_config.platform,
        name="test_wallet",
        address=EthereumAddress.from_string(test_account.address),
    )
    signer_config = EthereumSignerConfiguration(
        private_key=SecretStr(test_account.key.hex()),
    )
    wallet_config = EthereumWalletConfiguration(
        identifier=identifier,
        signer=signer_config,
        gas_configuration=GasConfiguration(),
    )
    return EthereumWallet(configuration=wallet_config, blockchain=ethereum_blockchain)


@pytest.fixture
async def ethereum_wallet(
    ethereum_config: EthereumBlockchainConfiguration,
    test_account: LocalAccount,
    ethereum_blockchain: EthereumBlockchain,
) -> EthereumWallet:
    """Fixture providing a test Ethereum wallet."""
    wallet = build_wallet(ethereum_config, test_account, ethereum_blockchain)
    if wallet._background_tasks:
        await asyncio.gather(*wallet._background_tasks, return_exceptions=True)
    return wallet


async def drain_background_tasks(wallet: EthereumWallet) -> None:
    while wallet._background_tasks:
        await asyncio.gather(*wallet._background_tasks, return_exceptions=True)


LEGACY_TX_DATA: dict[str, Any] = {
    "to": RECIPIENT,
    "value": Wei(10**18),
    "gas": 21000,
    "gasPrice": Wei(20_000_000_000),
    "chainId": 1,
}


# === Initialization / identifier / signer ===


async def test_wallet_initialization(
    ethereum_wallet: EthereumWallet, test_account: LocalAccount
) -> None:
    """Test wallet initialization and basic properties."""
    assert ethereum_wallet.identifier.name == "test_wallet"
    assert (
        ethereum_wallet.identifier.address.string.lower()
        == test_account.address.lower()
    )
    assert ethereum_wallet.signer is not None
    assert ethereum_wallet.current_timestamp > 0


def test_default_transaction_class_is_set() -> None:
    """Regression: financepype only declares the annotation, never assigns it."""
    assert EthereumWallet.DEFAULT_TRANSACTION_CLASS is EthereumTransaction


def test_wallet_identifier_name_is_optional() -> None:
    """Regression: OwnerIdentifier.name has no default in financepype."""
    platform = BlockchainPlatform(
        identifier="ethereum", type=EthereumBlockchainType, chain_id=1
    )
    identifier = EthereumWalletIdentifier(
        platform=platform,
        address=EthereumAddress.from_string(RECIPIENT),
    )
    assert identifier.name is None
    assert (
        identifier.identifier
        == f"ethereum:{EthereumAddress.from_string(RECIPIENT).string}"
    )


def test_signer_uses_public_account_api(test_account: LocalAccount) -> None:
    """The signer address and signatures must match the source private key."""
    signer = EthereumSigner(
        EthereumSignerConfiguration(private_key=SecretStr(test_account.key.hex()))
    )
    assert signer.address == test_account.address

    signed = signer.sign_transaction(dict(LEGACY_TX_DATA, nonce=0))
    expected = test_account.sign_transaction(dict(LEGACY_TX_DATA, nonce=0))
    assert signed.raw_transaction == expected.raw_transaction


def test_signer_rejects_malformed_private_key() -> None:
    with pytest.raises(ValueError):
        EthereumSigner(EthereumSignerConfiguration(private_key=SecretStr("0x1234")))


def test_signer_configuration_masks_private_key(test_account: LocalAccount) -> None:
    config = EthereumSignerConfiguration(private_key=SecretStr(test_account.key.hex()))
    assert test_account.key.hex() not in repr(config)
    assert test_account.key.hex() not in str(config)


def test_wallet_construction_without_running_loop(
    ethereum_config: EthereumBlockchainConfiguration,
    test_account: LocalAccount,
    ethereum_blockchain: EthereumBlockchain,
) -> None:
    """Constructing a wallet outside an event loop must not crash or leak tasks."""
    wallet = build_wallet(ethereum_config, test_account, ethereum_blockchain)
    assert ethereum_blockchain.native_asset in wallet._tracked_assets
    assert wallet._background_tasks == set()


async def test_add_tracked_assets_schedules_balance_update(
    ethereum_config: EthereumBlockchainConfiguration,
    test_account: LocalAccount,
    ethereum_blockchain: EthereumBlockchain,
    rpc_provider: FakeRPCProvider,
) -> None:
    """Inside a loop, new tracked assets get their balance fetched in background."""
    wallet = build_wallet(ethereum_config, test_account, ethereum_blockchain)
    assert len(wallet._background_tasks) == 1

    await drain_background_tasks(wallet)
    assert rpc_provider.calls_for("eth_getBalance")
    balance = wallet.balance_tracker.get_balance(
        ethereum_blockchain.native_asset, BalanceType.TOTAL
    )
    assert balance == Decimal(1)


# === Nonce management ===


async def test_wallet_nonce_management(
    ethereum_wallet: EthereumWallet, rpc_provider: FakeRPCProvider
) -> None:
    """Test wallet nonce management against a mocked RPC."""
    assert ethereum_wallet.last_nonce is None
    assert ethereum_wallet.allocate_nonce() is None

    await ethereum_wallet.sync_nonce()
    assert ethereum_wallet.last_nonce == 5
    assert rpc_provider.calls_for("eth_getTransactionCount")

    allocated_nonce = ethereum_wallet.allocate_nonce()
    assert allocated_nonce == 5
    assert ethereum_wallet.last_nonce == 6


async def test_concurrent_nonce_allocation_is_unique(
    ethereum_wallet: EthereumWallet, rpc_provider: FakeRPCProvider
) -> None:
    """Concurrent allocations must produce distinct, sequential nonces."""
    rpc_provider.responses["eth_getTransactionCount"] = "0x0"
    await ethereum_wallet.sync_nonce()

    async def allocate(i: int) -> int | None:
        await asyncio.sleep(0.001 * (i % 5))
        return ethereum_wallet.allocate_nonce()

    results = await asyncio.gather(*(allocate(i) for i in range(25)))
    assert sorted(results) == list(range(25))
    assert ethereum_wallet.last_nonce == 25


async def test_release_nonce_only_rolls_back_latest(
    ethereum_wallet: EthereumWallet,
) -> None:
    ethereum_wallet.last_nonce = 10
    await ethereum_wallet._release_nonce(9)
    assert ethereum_wallet.last_nonce == 9

    # 5 is not the most recent allocation anymore: no rollback
    await ethereum_wallet._release_nonce(5)
    assert ethereum_wallet.last_nonce == 9

    await ethereum_wallet._release_nonce(None)
    assert ethereum_wallet.last_nonce == 9


# === Signing ===


async def test_transaction_signing(ethereum_wallet: EthereumWallet) -> None:
    """Test transaction signing."""
    tx_data = dict(LEGACY_TX_DATA, nonce=0)

    signed_tx = ethereum_wallet.sign_transaction(tx_data, auto_assign_nonce=False)
    assert signed_tx is not None
    assert signed_tx.hash is not None


async def test_sign_transaction_requires_synced_nonce(
    ethereum_wallet: EthereumWallet,
) -> None:
    with pytest.raises(ValueError, match="allocate nonce"):
        ethereum_wallet.sign_transaction(dict(LEGACY_TX_DATA))


# === Balances ===


async def test_wallet_balance(
    ethereum_wallet: EthereumWallet, rpc_provider: FakeRPCProvider
) -> None:
    """Native balance is returned decimal-adjusted (ETH, not wei)."""
    rpc_provider.responses["eth_getBalance"] = "0x1bc16d674ec80000"  # 2 ETH
    balance = await ethereum_wallet.fetch_balance(
        ethereum_wallet.blockchain.native_asset
    )
    assert balance == Decimal(2)


# === sign_and_send / broadcast ===


async def test_sign_and_send_returns_tracked_transaction(
    ethereum_wallet: EthereumWallet, rpc_provider: FakeRPCProvider
) -> None:
    """Regression: DEFAULT_TRANSACTION_CLASS missing made this raise AttributeError."""
    await ethereum_wallet.sync_nonce()

    transaction = ethereum_wallet.sign_and_send_transaction(
        client_operation_id="op-1", tx_data=dict(LEGACY_TX_DATA)
    )

    assert isinstance(transaction, EthereumTransaction)
    assert (
        ethereum_wallet.transaction_tracker.fetch_tracked_operation("op-1")
        is transaction
    )
    assert transaction.signed_transaction is not None
    assert ethereum_wallet.last_nonce == 6

    await drain_background_tasks(ethereum_wallet)
    assert transaction.current_state == BlockchainTransactionState.BROADCASTED
    assert transaction.operator_operation_id is not None
    assert transaction.operator_operation_id.string == TX_HASH_HEX
    assert len(rpc_provider.calls_for("eth_sendRawTransaction")) == 1


async def test_sign_and_send_retry_is_idempotent(
    ethereum_wallet: EthereumWallet, rpc_provider: FakeRPCProvider
) -> None:
    """Re-sending the same client_operation_id must not raise or re-broadcast."""
    await ethereum_wallet.sync_nonce()

    transaction = ethereum_wallet.sign_and_send_transaction(
        client_operation_id="op-1", tx_data=dict(LEGACY_TX_DATA)
    )
    await drain_background_tasks(ethereum_wallet)

    retried = ethereum_wallet.sign_and_send_transaction(
        client_operation_id="op-1", tx_data=dict(LEGACY_TX_DATA)
    )
    await drain_background_tasks(ethereum_wallet)

    assert retried is transaction
    assert len(rpc_provider.calls_for("eth_sendRawTransaction")) == 1
    assert ethereum_wallet.last_nonce == 6  # no second nonce allocated


async def test_broadcast_rejection_nonce_error_resyncs(
    ethereum_wallet: EthereumWallet, rpc_provider: FakeRPCProvider
) -> None:
    """A nonce-related rejection re-syncs the nonce from the chain."""
    counts = iter(["0x5", "0x9"])
    rpc_provider.responses["eth_getTransactionCount"] = lambda params: next(counts)
    rpc_provider.responses["eth_sendRawTransaction"] = {
        "error": {"code": -32000, "message": "nonce too low"}
    }

    await ethereum_wallet.sync_nonce()
    transaction = ethereum_wallet.sign_and_send_transaction(
        client_operation_id="op-rejected", tx_data=dict(LEGACY_TX_DATA)
    )
    await drain_background_tasks(ethereum_wallet)

    assert transaction.current_state == BlockchainTransactionState.REJECTED
    assert ethereum_wallet.last_nonce == 9
    assert len(rpc_provider.calls_for("eth_getTransactionCount")) == 2


async def test_broadcast_rejection_rolls_back_nonce(
    ethereum_wallet: EthereumWallet, rpc_provider: FakeRPCProvider
) -> None:
    """A non-nonce rejection releases the allocated nonce for reuse."""
    rpc_provider.responses["eth_sendRawTransaction"] = {
        "error": {
            "code": -32000,
            "message": "insufficient funds for gas * price + value",
        }
    }

    await ethereum_wallet.sync_nonce()
    transaction = ethereum_wallet.sign_and_send_transaction(
        client_operation_id="op-rejected", tx_data=dict(LEGACY_TX_DATA)
    )
    assert ethereum_wallet.last_nonce == 6
    await drain_background_tasks(ethereum_wallet)

    assert transaction.current_state == BlockchainTransactionState.REJECTED
    assert ethereum_wallet.last_nonce == 5  # rolled back


async def test_broadcast_rejection_no_rollback_after_later_allocation(
    ethereum_wallet: EthereumWallet, rpc_provider: FakeRPCProvider
) -> None:
    """The nonce is not rolled back when later nonces were already handed out."""
    rpc_provider.responses["eth_sendRawTransaction"] = {
        "error": {"code": -32000, "message": "insufficient funds"}
    }

    await ethereum_wallet.sync_nonce()
    ethereum_wallet.sign_and_send_transaction(
        client_operation_id="op-rejected", tx_data=dict(LEGACY_TX_DATA)
    )
    # A concurrent transaction grabs the next nonce before the rejection lands
    assert ethereum_wallet.allocate_nonce() == 6
    await drain_background_tasks(ethereum_wallet)

    assert ethereum_wallet.last_nonce == 7


# === get_transaction_update ===


async def broadcast_test_transaction(
    wallet: EthereumWallet, client_operation_id: str = "op-1"
) -> EthereumTransaction:
    await wallet.sync_nonce()
    transaction = wallet.sign_and_send_transaction(
        client_operation_id=client_operation_id, tx_data=dict(LEGACY_TX_DATA)
    )
    await drain_background_tasks(wallet)
    assert transaction.current_state == BlockchainTransactionState.BROADCASTED
    return transaction


async def test_get_transaction_update_confirmed(
    ethereum_wallet: EthereumWallet,
    rpc_provider: FakeRPCProvider,
    test_account: LocalAccount,
) -> None:
    transaction = await broadcast_test_transaction(ethereum_wallet)
    rpc_provider.responses["eth_getTransactionReceipt"] = make_receipt_payload(
        TX_HASH_HEX, test_account.address, status="0x1"
    )

    update = await ethereum_wallet.get_transaction_update(
        transaction, timeout=timedelta(seconds=5), raise_timeout=True
    )

    assert update.new_state == BlockchainTransactionState.CONFIRMED
    assert update.transaction_id == transaction.operator_operation_id
    assert update.receipt is not None
    assert update.receipt.status == 1
    assert update.receipt.gas_used == 21000
    # fee = 21000 gas * 20 gwei = 0.00042 ETH
    fee = update.other_data["fee"]
    assert fee.amount == Decimal("0.00042")
    assert fee.asset == ethereum_wallet.blockchain.native_asset
    assert update.explorer_link is None  # no explorer configured


async def test_get_transaction_update_failed(
    ethereum_wallet: EthereumWallet,
    rpc_provider: FakeRPCProvider,
    test_account: LocalAccount,
) -> None:
    """A receipt with status 0 maps to the FAILED state."""
    transaction = await broadcast_test_transaction(ethereum_wallet)
    rpc_provider.responses["eth_getTransactionReceipt"] = make_receipt_payload(
        TX_HASH_HEX, test_account.address, status="0x0"
    )

    update = await ethereum_wallet.get_transaction_update(
        transaction, timeout=timedelta(seconds=5), raise_timeout=True
    )

    assert update.new_state == BlockchainTransactionState.FAILED
    assert update.receipt is not None
    assert update.receipt.status == 0


async def test_get_transaction_update_timeout_returns_current_state(
    ethereum_wallet: EthereumWallet, rpc_provider: FakeRPCProvider
) -> None:
    transaction = await broadcast_test_transaction(ethereum_wallet)
    # Unmined transaction: the node answers null
    rpc_provider.responses["eth_getTransactionReceipt"] = None

    update = await ethereum_wallet.get_transaction_update(
        transaction, timeout=timedelta(seconds=0), raise_timeout=False
    )

    assert update.new_state == BlockchainTransactionState.BROADCASTED
    assert update.transaction_id == transaction.operator_operation_id
    assert update.receipt is None


async def test_get_transaction_update_timeout_raises(
    ethereum_wallet: EthereumWallet, rpc_provider: FakeRPCProvider
) -> None:
    transaction = await broadcast_test_transaction(ethereum_wallet)
    rpc_provider.responses["eth_getTransactionReceipt"] = None

    with pytest.raises(TimeoutError):
        await ethereum_wallet.get_transaction_update(
            transaction, timeout=timedelta(seconds=0), raise_timeout=True
        )


# === modify / speedup / cancel ===


def make_raw_transaction_payload(
    tx_hash: str, sender: str, eip1559: bool = False
) -> dict[str, Any]:
    """Build a realistic eth_getTransactionByHash payload."""
    payload: dict[str, Any] = {
        "blockHash": "0x" + "22" * 32,
        "blockNumber": "0x10",
        "chainId": "0x1",
        "from": sender,
        "gas": "0x5208",
        "gasPrice": "0x4a817c800",  # 20 gwei
        "hash": tx_hash,
        "input": "0x",
        "nonce": "0x5",
        "to": RECIPIENT,
        "transactionIndex": "0x0",
        "type": "0x0",
        "value": "0xde0b6b3a7640000",
        "v": "0x25",
        "r": "0x" + "01" * 32,
        "s": "0x" + "02" * 32,
    }
    if eip1559:
        payload.update(
            {
                "type": "0x2",
                "gasPrice": "0x59682f00",  # effective price, 1.5 gwei
                "maxFeePerGas": "0x77359400",  # 2 gwei
                "maxPriorityFeePerGas": "0x3b9aca00",  # 1 gwei
                "accessList": [],
            }
        )
    return payload


def test_increase_gas_value_exact_math() -> None:
    """The bump must be exact for typical percentages (no float drift)."""
    assert EthereumWallet._increase_gas_value(20_000_000_000, 0.12) == 22_400_000_000
    assert EthereumWallet._increase_gas_value(20_000_000_000, 0.13) == 22_600_000_000
    assert EthereumWallet._increase_gas_value(21, 0.1) == 24  # ceil(23.1)
    assert EthereumWallet._increase_gas_value(0, 0.13) == 0


async def test_speedup_transaction_legacy_gas_bump(
    ethereum_wallet: EthereumWallet,
    rpc_provider: FakeRPCProvider,
    test_account: LocalAccount,
) -> None:
    """Speedup reuses the nonce and bumps the legacy gasPrice by the exact %."""
    rpc_provider.responses["eth_getTransactionByHash"] = make_raw_transaction_payload(
        TX_HASH_HEX, test_account.address
    )
    tx_hash = EthereumTransactionHash.from_string(TX_HASH_HEX)

    with patch.object(
        ethereum_wallet,
        "sign_and_send_transaction",
        wraps=ethereum_wallet.sign_and_send_transaction,
    ) as spy:
        replacement = await ethereum_wallet.speedup_transaction(
            tx_hash, gas_increase_percentage=0.13
        )
    await drain_background_tasks(ethereum_wallet)

    tx_data = spy.call_args.kwargs["tx_data"]
    assert spy.call_args.kwargs["auto_assign_nonce"] is False
    assert tx_data["nonce"] == 5
    assert tx_data["to"] == RECIPIENT
    assert tx_data["value"] == 10**18
    assert tx_data["gas"] == 21000
    # ceil(20 gwei * 1.13) = 22.6 gwei, exactly
    assert tx_data["gasPrice"] == 22_600_000_000
    assert "maxFeePerGas" not in tx_data

    assert isinstance(replacement, EthereumTransaction)
    assert replacement.current_state == BlockchainTransactionState.BROADCASTED


async def test_modify_transaction_eip1559_gas_bump(
    ethereum_wallet: EthereumWallet,
    rpc_provider: FakeRPCProvider,
    test_account: LocalAccount,
) -> None:
    """EIP-1559 replacements bump maxFee/maxPriorityFee over the ORIGINAL fees."""
    rpc_provider.responses["eth_getTransactionByHash"] = make_raw_transaction_payload(
        TX_HASH_HEX, test_account.address, eip1559=True
    )
    tx_hash = EthereumTransactionHash.from_string(TX_HASH_HEX)

    with patch.object(
        ethereum_wallet,
        "sign_and_send_transaction",
        wraps=ethereum_wallet.sign_and_send_transaction,
    ) as spy:
        replacement = await ethereum_wallet.modify_transaction(
            tx_hash, gas_increase_percentage=0.12
        )
    await drain_background_tasks(ethereum_wallet)

    tx_data = spy.call_args.kwargs["tx_data"]
    assert tx_data["nonce"] == 5
    # ceil(2 gwei * 1.12) = 2.24 gwei; ceil(1 gwei * 1.12) = 1.12 gwei
    assert tx_data["maxFeePerGas"] == 2_240_000_000
    assert tx_data["maxPriorityFeePerGas"] == 1_120_000_000
    # the effective gasPrice of the original must NOT leak into the replacement
    assert "gasPrice" not in tx_data
    assert replacement.current_state == BlockchainTransactionState.BROADCASTED

    # The broadcast raw transaction carries the bumped dynamic fees
    raw_tx_hex = rpc_provider.calls_for("eth_sendRawTransaction")[0][0]
    decoded = TypedTransaction.from_bytes(HexBytes(raw_tx_hex)).as_dict()
    assert decoded["nonce"] == 5
    assert decoded["maxFeePerGas"] == 2_240_000_000
    assert decoded["maxPriorityFeePerGas"] == 1_120_000_000


async def test_cancel_transaction_builds_self_transfer(
    ethereum_wallet: EthereumWallet,
    rpc_provider: FakeRPCProvider,
    test_account: LocalAccount,
) -> None:
    """Cancel replaces the tx with a zero-value, empty-data self-transfer."""
    rpc_provider.responses["eth_getTransactionByHash"] = make_raw_transaction_payload(
        TX_HASH_HEX, test_account.address
    )
    tx_hash = EthereumTransactionHash.from_string(TX_HASH_HEX)

    with patch.object(
        ethereum_wallet,
        "sign_and_send_transaction",
        wraps=ethereum_wallet.sign_and_send_transaction,
    ) as spy:
        replacement = await ethereum_wallet.cancel_transaction(
            tx_hash, gas_increase_percentage=0.13
        )
    await drain_background_tasks(ethereum_wallet)

    tx_data = spy.call_args.kwargs["tx_data"]
    assert tx_data["nonce"] == 5
    assert tx_data["to"] == ethereum_wallet.address.raw
    assert tx_data["value"] == 0
    assert tx_data["data"] == b""
    assert tx_data["gas"] == ethereum_wallet.gas_configuration.default_cancel_gas
    assert tx_data["gasPrice"] == 22_600_000_000
    assert replacement.current_state == BlockchainTransactionState.BROADCASTED


async def test_modify_transaction_missing_original_raises(
    ethereum_wallet: EthereumWallet, rpc_provider: FakeRPCProvider
) -> None:
    rpc_provider.responses["eth_getTransactionByHash"] = None
    tx_hash = EthereumTransactionHash.from_string(TX_HASH_HEX)

    with pytest.raises((TransactionNotFound, ValueError)):
        await ethereum_wallet.modify_transaction(tx_hash)
