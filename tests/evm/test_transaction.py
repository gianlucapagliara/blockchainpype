import time
from decimal import Decimal

import pytest
from eth_typing import BlockNumber, HexStr
from financepype.platforms.blockchain import BlockchainPlatform, BlockchainType
from hexbytes import HexBytes
from web3.types import Nonce, Wei

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
        type=BlockchainType.EVM,
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
