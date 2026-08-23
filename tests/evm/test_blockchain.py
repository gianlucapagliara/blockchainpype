import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, cast

import pytest
from ens import AsyncENS
from eth_account import Account
from financepype.operations.transactions.models import BlockchainTransactionState
from financepype.platforms.blockchain import BlockchainPlatform
from web3 import AsyncWeb3
from web3.exceptions import Web3RPCError
from web3.providers.async_base import AsyncJSONBaseProvider
from web3.types import RPCEndpoint, RPCResponse

from blockchainpype.evm.asset import EthereumNativeAsset
from blockchainpype.evm.blockchain.blockchain import (
    EthereumBlockchain,
    EthereumBlockchainType,
)
from blockchainpype.evm.blockchain.configuration import (
    EthereumBlockchainConfiguration,
    EthereumConnectivityConfiguration,
    EthereumNativeAssetConfiguration,
)
from blockchainpype.evm.blockchain.identifier import (
    EthereumAddress,
    EthereumTransactionHash,
)
from blockchainpype.evm.explorer.etherscan import EtherscanConfiguration
from blockchainpype.evm.transaction import EthereumTransaction
from blockchainpype.evm.wallet.identifier import EthereumWalletIdentifier


@pytest.fixture
def ethereum_config() -> EthereumBlockchainConfiguration:
    """Fixture providing a test Ethereum blockchain configuration."""
    return EthereumBlockchainConfiguration(
        platform=BlockchainPlatform(
            identifier="ethereum",
            type=EthereumBlockchainType,
            chain_id=1,
        ),
        native_asset=EthereumNativeAssetConfiguration(),
        connectivity=EthereumConnectivityConfiguration(
            rpc_provider=AsyncWeb3.AsyncHTTPProvider("https://eth.llamarpc.com"),
        ),
        explorer=None,
    )


@pytest.fixture
def ethereum_blockchain(
    ethereum_config: EthereumBlockchainConfiguration,
) -> EthereumBlockchain:
    """Fixture providing a test Ethereum blockchain instance."""
    return EthereumBlockchain(configuration=ethereum_config)


@pytest.mark.asyncio
async def test_blockchain_initialization(
    ethereum_blockchain: EthereumBlockchain,
) -> None:
    """Test blockchain instance initialization and basic properties."""
    assert ethereum_blockchain.platform.identifier == "ethereum"
    assert ethereum_blockchain.platform.type == EthereumBlockchainType
    assert ethereum_blockchain.platform.chain_id == 1

    assert isinstance(ethereum_blockchain.native_asset, EthereumNativeAsset)
    assert ethereum_blockchain.native_asset.data.symbol == "ETH"
    assert ethereum_blockchain.native_asset.data.decimals == 18

    assert ethereum_blockchain.explorer is None


@pytest.mark.network
@pytest.mark.asyncio
async def test_fetch_block_number(ethereum_blockchain: EthereumBlockchain) -> None:
    """Test fetching the current block number."""
    block_number = await ethereum_blockchain.fetch_block_number()
    assert isinstance(block_number, int)
    assert block_number > 0


@pytest.mark.network
@pytest.mark.asyncio
async def test_fetch_native_asset_balance(
    ethereum_blockchain: EthereumBlockchain,
) -> None:
    """Test fetching native asset (ETH) balance."""
    # Using Ethereum Foundation's address as an example
    address = EthereumAddress.from_string("0xde0B295669a9FD93d5F28D9Ec85E40f4cb697BAe")
    balance = await ethereum_blockchain.fetch_native_asset_balance(address)

    assert balance is not None
    assert balance >= 0


@pytest.mark.network
@pytest.mark.asyncio
async def test_fetch_block_timestamp(ethereum_blockchain: EthereumBlockchain) -> None:
    """Test fetching block timestamp."""
    # Fetch current block number first
    block_number = await ethereum_blockchain.fetch_block_number()

    # Fetch timestamp for that block
    timestamp = await ethereum_blockchain.fetch_block_timestamp(block_number)

    assert timestamp is not None
    assert timestamp > 0  # Ethereum timestamps are Unix timestamps


# === Mocked-RPC test harness ===

TX_HASH = "0x" + "ab" * 32
BLOCK_HASH = "0x" + "cd" * 32
SENDER = "0xde0B295669a9FD93d5F28D9Ec85E40f4cb697BAe"
RECIPIENT = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"
BLOCK_NUMBER_HEX = "0x10d4f"
BLOCK_TIMESTAMP = 1700000000  # 0x6553f100

# 21000 gas at an effective price of 2 gwei -> 42_000_000_000_000 wei fee
GAS_USED_HEX = "0x5208"
EFFECTIVE_GAS_PRICE_HEX = "0x77359400"
EXPECTED_FEE_ETH = Decimal("0.000042")

TX_PAYLOAD: dict[str, Any] = {
    "accessList": [],
    "blockHash": BLOCK_HASH,
    "blockNumber": BLOCK_NUMBER_HEX,
    "chainId": "0x1",
    "from": SENDER,
    "gas": GAS_USED_HEX,
    "gasPrice": EFFECTIVE_GAS_PRICE_HEX,
    "hash": TX_HASH,
    "input": "0x",
    "maxFeePerGas": EFFECTIVE_GAS_PRICE_HEX,
    "maxPriorityFeePerGas": "0x3b9aca00",
    "nonce": "0x1",
    "r": "0x" + "01" * 32,
    "s": "0x" + "02" * 32,
    "to": RECIPIENT,
    "transactionIndex": "0x0",
    "type": "0x2",
    "v": "0x0",
    "value": "0xde0b6b3a7640000",
    "yParity": "0x0",
}

BLOCK_PAYLOAD: dict[str, Any] = {
    "baseFeePerGas": "0x3b9aca00",
    "difficulty": "0x0",
    "extraData": "0x",
    "gasLimit": "0x1c9c380",
    "gasUsed": GAS_USED_HEX,
    "hash": BLOCK_HASH,
    "logsBloom": "0x" + "00" * 256,
    "miner": "0x4675C7e5BaAFBFFbca748158bEcBA61ef3b0a263",
    "mixHash": "0x" + "00" * 32,
    "nonce": "0x0000000000000000",
    "number": BLOCK_NUMBER_HEX,
    "parentHash": "0x" + "11" * 32,
    "receiptsRoot": "0x" + "22" * 32,
    "sha3Uncles": "0x" + "33" * 32,
    "size": "0x220",
    "stateRoot": "0x" + "44" * 32,
    "timestamp": "0x6553f100",
    "totalDifficulty": "0x0",
    "transactions": [],
    "transactionsRoot": "0x" + "55" * 32,
    "uncles": [],
}

RECEIPT_PAYLOAD: dict[str, Any] = {
    "blockHash": BLOCK_HASH,
    "blockNumber": BLOCK_NUMBER_HEX,
    "contractAddress": None,
    "cumulativeGasUsed": GAS_USED_HEX,
    "effectiveGasPrice": EFFECTIVE_GAS_PRICE_HEX,
    "from": SENDER,
    "gasUsed": GAS_USED_HEX,
    "logs": [],
    "logsBloom": "0x" + "00" * 256,
    "status": "0x0",
    "to": RECIPIENT,
    "transactionHash": TX_HASH,
    "transactionIndex": "0x0",
    "type": "0x2",
}


@dataclass
class RPCError:
    """Marks a canned response as a JSON-RPC error object."""

    code: int
    message: str


class MockRPCProvider(AsyncJSONBaseProvider):
    """
    In-memory JSON-RPC provider returning canned results per method.

    Values in ``results`` are returned as the JSON-RPC ``result``; RPCError
    values are returned as JSON-RPC error responses; exception instances are
    raised. Unexpected methods fail the test.
    """

    def __init__(self, results: dict[str, Any]) -> None:
        super().__init__()
        self.results = dict(results)
        self.calls: list[tuple[str, Any]] = []

    async def make_request(self, method: RPCEndpoint, params: Any) -> RPCResponse:
        self.calls.append((str(method), params))
        if method not in self.results:
            raise AssertionError(f"Unexpected RPC method: {method}")

        value = self.results[method]
        if isinstance(value, BaseException):
            raise value

        request_id = next(self.request_counter)
        if isinstance(value, RPCError):
            return cast(
                RPCResponse,
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": value.code, "message": value.message},
                },
            )
        return cast(
            RPCResponse,
            {"jsonrpc": "2.0", "id": request_id, "result": value},
        )


def build_blockchain(
    results: dict[str, Any],
    explorer: EtherscanConfiguration | None = None,
) -> EthereumBlockchain:
    """Build an EthereumBlockchain wired to a MockRPCProvider."""
    configuration = EthereumBlockchainConfiguration(
        platform=BlockchainPlatform(
            identifier="ethereum",
            type=EthereumBlockchainType,
            chain_id=1,
        ),
        native_asset=EthereumNativeAssetConfiguration(),
        connectivity=EthereumConnectivityConfiguration(
            rpc_provider=MockRPCProvider(results),
        ),
        explorer=explorer,
    )
    return EthereumBlockchain(configuration=configuration)


class TestMiddlewareConfiguration:
    def test_middleware_defaults_to_none(self) -> None:
        connectivity = EthereumConnectivityConfiguration(
            rpc_provider=MockRPCProvider({}),
        )
        assert connectivity.middleware is None

    def test_default_configuration_preserves_web3_default_middleware(self) -> None:
        blockchain = build_blockchain({})
        default_w3: AsyncWeb3 = AsyncWeb3(
            AsyncWeb3.AsyncHTTPProvider("http://localhost:1")
        )

        default_middleware = default_w3.middleware_onion.as_tuple_of_middleware()
        blockchain_middleware = (
            blockchain.web3.middleware_onion.as_tuple_of_middleware()
        )

        assert len(default_middleware) > 0
        assert len(blockchain_middleware) == len(default_middleware)

    def test_explicit_empty_middleware_disables_defaults(self) -> None:
        configuration = EthereumBlockchainConfiguration(
            platform=BlockchainPlatform(
                identifier="ethereum",
                type=EthereumBlockchainType,
                chain_id=1,
            ),
            native_asset=EthereumNativeAssetConfiguration(),
            connectivity=EthereumConnectivityConfiguration(
                rpc_provider=MockRPCProvider({}),
                middleware=[],
            ),
            explorer=None,
        )
        blockchain = EthereumBlockchain(configuration=configuration)

        assert len(blockchain.web3.middleware_onion.as_tuple_of_middleware()) == 0

    def test_configured_ens_is_bound_to_web3(self) -> None:
        ens = AsyncENS(AsyncWeb3.AsyncHTTPProvider("http://localhost:1"), middleware=[])
        configuration = EthereumBlockchainConfiguration(
            platform=BlockchainPlatform(
                identifier="ethereum",
                type=EthereumBlockchainType,
                chain_id=1,
            ),
            native_asset=EthereumNativeAssetConfiguration(),
            connectivity=EthereumConnectivityConfiguration(
                rpc_provider=MockRPCProvider({}),
                ens=ens,
            ),
            explorer=None,
        )
        blockchain = EthereumBlockchain(configuration=configuration)

        assert blockchain.web3.ens is ens
        assert ens.w3 is blockchain.web3


class TestFetchTransaction:
    async def test_fetch_transaction_returns_none_when_not_found(self) -> None:
        blockchain = build_blockchain({"eth_getTransactionByHash": None})
        tx_hash = EthereumTransactionHash.from_string(TX_HASH)

        transaction = await blockchain.fetch_transaction(tx_hash)

        assert transaction is None

    async def test_fetch_raw_transaction_returns_none_when_not_found(self) -> None:
        blockchain = build_blockchain({"eth_getTransactionByHash": None})
        tx_hash = EthereumTransactionHash.from_string(TX_HASH)

        assert await blockchain.fetch_raw_transaction(tx_hash) is None

    async def test_fetch_transaction_receipt_returns_none_when_not_found(self) -> None:
        blockchain = build_blockchain({"eth_getTransactionReceipt": None})
        tx_hash = EthereumTransactionHash.from_string(TX_HASH)

        assert await blockchain.fetch_transaction_receipt(tx_hash) is None

    async def test_fetch_transaction_invalid_id_raises(self) -> None:
        blockchain = build_blockchain({})
        address = EthereumAddress.from_string(SENDER)

        with pytest.raises(ValueError, match="Invalid transaction id"):
            await blockchain.fetch_transaction(address)

    async def test_reverted_transaction_reported_failed_with_fee(self) -> None:
        blockchain = build_blockchain(
            {
                "eth_getTransactionByHash": TX_PAYLOAD,
                "eth_getBlockByNumber": BLOCK_PAYLOAD,
                "eth_getTransactionReceipt": RECEIPT_PAYLOAD,
            }
        )
        tx_hash = EthereumTransactionHash.from_string(TX_HASH)

        transaction = await blockchain.fetch_transaction(tx_hash)

        assert transaction is not None
        assert transaction.current_state == BlockchainTransactionState.FAILED
        assert transaction.receipt is not None
        assert transaction.receipt.status == 0

        assert transaction.fee is not None
        assert transaction.fee.amount == EXPECTED_FEE_ETH
        assert transaction.fee.asset is blockchain.native_asset

        assert transaction.creation_timestamp == BLOCK_TIMESTAMP
        assert transaction.operator_operation_id == tx_hash
        assert isinstance(transaction.owner_identifier, EthereumWalletIdentifier)
        assert transaction.owner_identifier.address.string == SENDER
        assert transaction.explorer_link is None

    async def test_confirmed_transaction_reported_confirmed_with_fee(self) -> None:
        blockchain = build_blockchain(
            {
                "eth_getTransactionByHash": TX_PAYLOAD,
                "eth_getBlockByNumber": BLOCK_PAYLOAD,
                "eth_getTransactionReceipt": {**RECEIPT_PAYLOAD, "status": "0x1"},
            }
        )
        tx_hash = EthereumTransactionHash.from_string(TX_HASH)

        transaction = await blockchain.fetch_transaction(tx_hash)

        assert transaction is not None
        assert transaction.current_state == BlockchainTransactionState.CONFIRMED
        assert transaction.fee is not None
        assert transaction.fee.amount == EXPECTED_FEE_ETH

    async def test_pending_transaction_reported_broadcasted_without_fee(self) -> None:
        pending_payload = {
            **TX_PAYLOAD,
            "blockHash": None,
            "blockNumber": None,
            "transactionIndex": None,
        }
        blockchain = build_blockchain(
            {
                "eth_getTransactionByHash": pending_payload,
                "eth_getTransactionReceipt": None,
            }
        )
        tx_hash = EthereumTransactionHash.from_string(TX_HASH)

        before = time.time()
        transaction = await blockchain.fetch_transaction(tx_hash)
        after = time.time()

        assert transaction is not None
        assert transaction.current_state == BlockchainTransactionState.BROADCASTED
        assert transaction.receipt is None
        assert transaction.fee is None
        assert before <= transaction.creation_timestamp <= after

    async def test_explorer_link_generated_when_explorer_configured(self) -> None:
        blockchain = build_blockchain(
            {
                "eth_getTransactionByHash": TX_PAYLOAD,
                "eth_getBlockByNumber": BLOCK_PAYLOAD,
                "eth_getTransactionReceipt": {**RECEIPT_PAYLOAD, "status": "0x1"},
            },
            explorer=EtherscanConfiguration(),
        )
        tx_hash = EthereumTransactionHash.from_string(TX_HASH)

        transaction = await blockchain.fetch_transaction(tx_hash)

        assert transaction is not None
        assert transaction.explorer_link == f"https://etherscan.io/tx/{TX_HASH}"


def build_signed_transaction() -> tuple[Any, Any]:
    """Locally sign an EIP-1559 transfer; returns (account, signed_tx)."""
    account = Account.from_key("0x" + "01" * 32)
    signed = account.sign_transaction(
        {
            "to": RECIPIENT,
            "value": 10**15,
            "gas": 21000,
            "maxFeePerGas": 2_000_000_000,
            "maxPriorityFeePerGas": 1_000_000_000,
            "nonce": 0,
            "chainId": 1,
            "type": 2,
        }
    )
    return account, signed


def build_ethereum_transaction(
    blockchain: EthereumBlockchain, account: Any, signed: Any
) -> EthereumTransaction:
    return EthereumTransaction(
        client_operation_id="op-1",
        owner_identifier=EthereumWalletIdentifier(
            platform=blockchain.configuration.platform,
            name=None,
            address=EthereumAddress.from_string(account.address),
        ),
        creation_timestamp=time.time(),
        signed_transaction=signed,
    )


class TestSendTransaction:
    async def test_send_transaction_broadcasts_and_reports_hash(self) -> None:
        account, signed = build_signed_transaction()
        expected_hash = signed.hash.to_0x_hex()
        blockchain = build_blockchain({"eth_sendRawTransaction": expected_hash})
        transaction = build_ethereum_transaction(blockchain, account, signed)

        update = await blockchain.send_transaction(transaction)

        assert update.new_state == BlockchainTransactionState.BROADCASTED
        assert update.client_transaction_id == "op-1"
        assert update.transaction_id is not None
        assert update.transaction_id.string == expected_hash
        assert update.explorer_link is None
        assert update.update_timestamp > 0

    async def test_send_transaction_rpc_error_reports_rejected(self) -> None:
        account, signed = build_signed_transaction()
        blockchain = build_blockchain(
            {"eth_sendRawTransaction": RPCError(-32000, "nonce too low")}
        )
        transaction = build_ethereum_transaction(blockchain, account, signed)

        update = await blockchain.send_transaction(transaction)

        assert update.new_state == BlockchainTransactionState.REJECTED
        assert update.transaction_id is None
        assert isinstance(update.other_data["exception"], Web3RPCError)

    async def test_send_transaction_unsigned_raises(self) -> None:
        account, signed = build_signed_transaction()
        blockchain = build_blockchain({})
        transaction = build_ethereum_transaction(blockchain, account, signed)
        transaction.signed_transaction = None

        with pytest.raises(ValueError, match="not signed"):
            await blockchain.send_transaction(transaction)
