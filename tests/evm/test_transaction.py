import time
from decimal import Decimal
from typing import Any, cast

import pytest
from eth_typing import BlockNumber, HexStr
from financepype.platforms.blockchain import BlockchainPlatform
from hexbytes import HexBytes
from web3.types import Nonce, TxData, TxReceipt, Wei

from blockchainpype.evm.blockchain.blockchain import EthereumBlockchainType
from blockchainpype.evm.blockchain.identifier import (
    EthereumAddress,
    EthereumBlockHash,
    EthereumTransactionHash,
)
from blockchainpype.evm.transaction import (
    EthereumRawTransaction,
    EthereumTransaction,
    EthereumTransactionReceipt,
)
from blockchainpype.evm.wallet.identifier import EthereumWalletIdentifier


@pytest.fixture
def sample_tx_receipt_data() -> dict:
    """Fixture providing sample transaction receipt data."""
    tx_hash = EthereumTransactionHash(
        raw=HexBytes("0x" + "1" * 64),
        string="0x" + "1" * 64,
    )
    block_hash = EthereumBlockHash(
        raw=HexBytes("0x" + "2" * 64),
        string="0x" + "2" * 64,
    )

    return {
        "transaction_id": tx_hash,
        "block_hash": block_hash,
        "block_number": BlockNumber(123),
        "contract_address": None,
        "cumulative_gas_used": 100000,
        "effective_gas_price": Wei(20000000000),
        "gas_used": 50000,
        "sender": EthereumAddress.from_string("0x" + "3" * 40),
        "logs": [],
        "logs_bloom": HexBytes("0x" + "0" * 512),
        "root": HexStr("0x" + "4" * 64),
        "status": 1,
        "to": EthereumAddress.from_string("0x" + "5" * 40),
        "transaction_index": 0,
        "type": 2,
    }


def test_transaction_receipt_creation(sample_tx_receipt_data: dict) -> None:
    """Test creation and properties of EthereumTransactionReceipt."""
    receipt = EthereumTransactionReceipt(**sample_tx_receipt_data)

    assert receipt.transaction_id == sample_tx_receipt_data["transaction_id"]
    assert receipt.block_hash == sample_tx_receipt_data["block_hash"]
    assert receipt.block_number == sample_tx_receipt_data["block_number"]
    assert receipt.status == 1
    assert receipt.gas_used == 50000

    # Test fee calculation
    expected_fee = Decimal(50000 * 20000000000)
    assert receipt.fee_amount == expected_fee


def test_raw_transaction_creation() -> None:
    """Test creation and properties of EthereumRawTransaction."""
    sender = EthereumAddress.from_string("0x" + "1" * 40)
    to = EthereumAddress.from_string("0x" + "2" * 40)

    raw_tx = EthereumRawTransaction(
        block_number=BlockNumber(123),
        sender=sender,
        to=to,
        nonce=Nonce(123),  # Using Nonce type
        value=Wei(1000000000000000000),  # 1 ETH
        gas=21000,
        gas_price=Wei(20000000000),
    )

    assert raw_tx.sender == sender
    assert raw_tx.to == to
    assert raw_tx.value == Wei(1000000000000000000)
    assert raw_tx.gas == 21000


def test_ethereum_transaction(sample_tx_receipt_data: dict) -> None:
    """Test EthereumTransaction functionality."""
    platform = BlockchainPlatform(
        identifier="ethereum",
        type=EthereumBlockchainType,
        chain_id=1,
    )
    owner = EthereumWalletIdentifier(
        platform=platform,
        name="test_wallet",
        address=EthereumAddress.from_string("0x" + "1" * 40),
    )

    tx = EthereumTransaction(
        client_operation_id="test_op",
        owner_identifier=owner,
        creation_timestamp=time.time(),
    )

    # Test initial state
    assert tx.operator_operation_id is None
    assert tx.signed_transaction is None
    assert tx.raw_transaction is None
    assert tx.receipt is None

    # Test modification flags
    assert tx.can_be_modified is True  # No receipt yet
    assert tx.can_be_cancelled is True  # No receipt yet
    assert tx.can_be_speeded_up is True  # No receipt yet

    # Test receipt processing
    receipt = EthereumTransactionReceipt(**sample_tx_receipt_data)

    success = tx.process_receipt(receipt)
    assert success is True
    assert tx.receipt == receipt

    # Test flags after receipt
    assert tx.can_be_modified is False
    assert tx.can_be_cancelled is False
    assert tx.can_be_speeded_up is False


# === from_raw parsing over realistic chain payloads ===

SENDER_ADDRESS = "0xA1E4380A3B1f749673E270229993eE55F35663b4"
RECIPIENT_ADDRESS = "0x5DF9B87991262F6BA471F09758CDE1c0FC1De734"
CONTRACT_ADDRESS = "0xdAC17F958D2ee523a2206206994597C13D831ec7"

# First transaction ever mined on Ethereum mainnet (block 46147).
LEGACY_TX_HASH = "0x5c504ed432cb51138bcf09aa5e8a410dd4a1e204ef84bfed1be16dfba1b22060"
BLOCK_HASH = HexBytes(
    "0x4e3a3754410177e6937ef1f84bba68ea139e8d1a2258c5f85db9f1cd715a1bdd"
)

ERC20_TRANSFER_TOPIC = HexBytes(
    "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
)


def legacy_tx_data() -> TxData:
    """Realistic ``eth_getTransactionByHash`` payload for a legacy transfer."""
    return cast(
        TxData,
        {
            "blockHash": BLOCK_HASH,
            "blockNumber": BlockNumber(46_147),
            "from": SENDER_ADDRESS,
            "gas": 21_000,
            "gasPrice": Wei(50_000_000_000),
            "hash": HexBytes(LEGACY_TX_HASH),
            "input": HexBytes("0x"),
            "nonce": Nonce(0),
            "to": RECIPIENT_ADDRESS,
            "transactionIndex": 0,
            "value": Wei(31_337),
            "type": 0,
            "v": 28,
            "r": HexBytes(
                "0x88ff6cf0fefd94db46111149ae4bfc179e9b94721fffd821d38d16464b3f71d0"
            ),
            "s": HexBytes(
                "0x45e0aff800961cfce805daef7016b9b675c137a6a41a548f7b60a3484c06a33a"
            ),
        },
    )


def eip1559_tx_data() -> TxData:
    """Realistic payload for a confirmed EIP-1559 (type 2) transfer."""
    return cast(
        TxData,
        {
            "accessList": [],
            "blockHash": BLOCK_HASH,
            "blockNumber": BlockNumber(19_000_000),
            "chainId": 1,
            "from": SENDER_ADDRESS,
            "gas": 21_000,
            "gasPrice": Wei(30_000_000_000),  # effective price reported by nodes
            "maxFeePerGas": Wei(40_000_000_000),
            "maxPriorityFeePerGas": Wei(1_000_000_000),
            "hash": HexBytes("0x" + "aa" * 32),
            "input": HexBytes("0x"),
            "nonce": Nonce(5),
            "to": RECIPIENT_ADDRESS,
            "transactionIndex": 42,
            "value": Wei(10**18),
            "type": 2,
            "v": 0,
            "r": HexBytes("0x" + "01" * 32),
            "s": HexBytes("0x" + "02" * 32),
            "yParity": 0,
        },
    )


def successful_receipt_data() -> TxReceipt:
    """Realistic post-Byzantium ``eth_getTransactionReceipt`` payload."""
    log_entry: dict[str, Any] = {
        "address": CONTRACT_ADDRESS,
        "blockHash": BLOCK_HASH,
        "blockNumber": BlockNumber(19_000_000),
        "data": HexBytes("0x" + "00" * 31 + "64"),
        "logIndex": 7,
        "removed": False,
        "topics": [
            ERC20_TRANSFER_TOPIC,
            HexBytes("0x" + "00" * 12 + SENDER_ADDRESS[2:].lower()),
            HexBytes("0x" + "00" * 12 + RECIPIENT_ADDRESS[2:].lower()),
        ],
        "transactionHash": HexBytes("0x" + "aa" * 32),
        "transactionIndex": 42,
    }
    return cast(
        TxReceipt,
        {
            "blockHash": BLOCK_HASH,
            "blockNumber": BlockNumber(19_000_000),
            "contractAddress": None,
            "cumulativeGasUsed": 3_251_077,
            "effectiveGasPrice": Wei(30_000_000_000),
            "from": SENDER_ADDRESS,
            "gasUsed": 51_234,
            "logs": [log_entry],
            "logsBloom": HexBytes("0x" + "00" * 256),
            "status": 1,
            "to": RECIPIENT_ADDRESS,
            "transactionHash": HexBytes("0x" + "aa" * 32),
            "transactionIndex": 42,
            "type": 2,
        },
    )


class TestEthereumRawTransactionFromRaw:
    def test_legacy_transaction_keeps_gas_price(self) -> None:
        """Regression: 'gasPrice' must map to gas_price, not be dropped."""
        raw_tx = EthereumRawTransaction.from_raw(legacy_tx_data())

        assert raw_tx.gas_price == Wei(50_000_000_000)
        assert raw_tx.max_fee_per_gas is None
        assert raw_tx.max_priority_fee_per_gas is None
        assert raw_tx.block_number == BlockNumber(46_147)
        assert raw_tx.block_hash == BLOCK_HASH
        assert raw_tx.sender.string == SENDER_ADDRESS
        assert raw_tx.to is not None
        assert raw_tx.to.string == RECIPIENT_ADDRESS
        assert raw_tx.nonce == Nonce(0)
        assert raw_tx.gas == 21_000
        assert raw_tx.value == Wei(31_337)
        assert raw_tx.hash == HexBytes(LEGACY_TX_HASH)
        assert raw_tx.type == 0
        assert raw_tx.v == 28

    def test_eip1559_transaction(self) -> None:
        raw_tx = EthereumRawTransaction.from_raw(eip1559_tx_data())

        assert raw_tx.max_fee_per_gas == Wei(40_000_000_000)
        assert raw_tx.max_priority_fee_per_gas == Wei(1_000_000_000)
        assert raw_tx.gas_price == Wei(30_000_000_000)
        assert raw_tx.access_list == []
        assert raw_tx.chain_id == 1
        assert raw_tx.y_parity == 0
        assert raw_tx.type == 2
        assert raw_tx.transaction_index == 42
        assert raw_tx.value == Wei(10**18)

    def test_pending_transaction_has_no_block_fields(self) -> None:
        """Pending transactions come back with null block fields."""
        raw = dict(eip1559_tx_data())
        raw["blockHash"] = None
        raw["blockNumber"] = None
        raw["transactionIndex"] = None

        raw_tx = EthereumRawTransaction.from_raw(cast(TxData, raw))

        assert raw_tx.block_hash is None
        assert raw_tx.block_number is None
        assert raw_tx.transaction_index is None
        assert raw_tx.sender.string == SENDER_ADDRESS
        assert raw_tx.nonce == Nonce(5)

    def test_contract_creation_transaction_has_no_recipient(self) -> None:
        raw = dict(legacy_tx_data())
        raw["to"] = None
        raw["input"] = HexBytes("0x6080604052")  # start of contract bytecode

        raw_tx = EthereumRawTransaction.from_raw(cast(TxData, raw))

        assert raw_tx.to is None
        assert raw_tx.input == HexBytes("0x6080604052")
        assert raw_tx.sender.string == SENDER_ADDRESS


class TestEthereumTransactionReceiptFromRaw:
    def test_post_byzantium_success_receipt(self) -> None:
        """Modern receipts carry 'status' and no 'root' key."""
        receipt = EthereumTransactionReceipt.from_raw(successful_receipt_data())

        assert receipt.status == 1
        assert receipt.root is None
        assert receipt.contract_address is None
        assert receipt.block_number == BlockNumber(19_000_000)
        assert receipt.block_hash.raw == BLOCK_HASH
        assert receipt.transaction_id.string == "0x" + "aa" * 32
        assert receipt.sender.string == SENDER_ADDRESS
        assert receipt.to is not None
        assert receipt.to.string == RECIPIENT_ADDRESS
        assert receipt.gas_used == 51_234
        assert receipt.cumulative_gas_used == 3_251_077
        assert receipt.effective_gas_price == Wei(30_000_000_000)
        assert receipt.transaction_index == 42
        assert receipt.type == 2
        assert len(receipt.logs) == 1
        assert receipt.logs[0]["topics"][0] == ERC20_TRANSFER_TOPIC
        assert receipt.fee_amount == Decimal(51_234) * Decimal(30_000_000_000)

    def test_failed_receipt_status_zero(self) -> None:
        raw = dict(successful_receipt_data())
        raw["status"] = 0
        raw["logs"] = []

        receipt = EthereumTransactionReceipt.from_raw(cast(TxReceipt, raw))

        assert receipt.status == 0
        assert receipt.logs == []

    def test_contract_creation_receipt(self) -> None:
        raw = dict(successful_receipt_data())
        raw["to"] = None
        raw["contractAddress"] = CONTRACT_ADDRESS
        raw["logs"] = []

        receipt = EthereumTransactionReceipt.from_raw(cast(TxReceipt, raw))

        assert receipt.to is None
        assert receipt.contract_address is not None
        assert receipt.contract_address.string == CONTRACT_ADDRESS

    def test_pre_byzantium_receipt_carries_root(self) -> None:
        raw = dict(successful_receipt_data())
        del raw["status"]
        raw["root"] = HexStr("0x" + "cd" * 32)
        raw["type"] = 0
        raw["logs"] = []

        receipt = EthereumTransactionReceipt.from_raw(cast(TxReceipt, raw))

        assert receipt.status is None
        assert receipt.root == HexStr("0x" + "cd" * 32)
        assert receipt.type == 0

    def test_receipt_without_type_defaults_to_legacy(self) -> None:
        raw = dict(successful_receipt_data())
        del raw["type"]
        raw["logs"] = []

        receipt = EthereumTransactionReceipt.from_raw(cast(TxReceipt, raw))

        assert receipt.type == 0
