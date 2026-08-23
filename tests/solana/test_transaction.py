"""
Unit tests for the Solana transaction models: SolanaTransactionReceipt
(parsed from real solders getTransaction responses), SolanaRawTransaction,
and the SolanaTransaction state machine.
"""

import json
import time
from decimal import Decimal
from typing import Any

import pytest
from financepype.operations.transactions.models import BlockchainTransactionState
from financepype.platforms.blockchain import BlockchainPlatform
from solders.hash import Hash
from solders.keypair import Keypair
from solders.message import Message, MessageV0
from solders.rpc.responses import GetTransactionResp
from solders.signature import Signature
from solders.system_program import TransferParams, transfer
from solders.transaction import Transaction, VersionedTransaction
from solders.transaction_status import EncodedConfirmedTransactionWithStatusMeta

from blockchainpype.solana.blockchain.blockchain import SolanaBlockchainType
from blockchainpype.solana.blockchain.identifier import (
    SolanaAddress,
    SolanaTransactionSignature,
)
from blockchainpype.solana.transaction import (
    SolanaRawTransaction,
    SolanaTransaction,
    SolanaTransactionReceipt,
)
from blockchainpype.solana.wallet.identifier import SolanaWalletIdentifier
from tests.solana.test_blockchain import (
    BLOCK_TIME,
    BLOCKHASH,
    FEE_LAMPORTS,
    SLOT,
    transaction_payload,
)


def parse_encoded_transaction(
    payload: dict[str, Any],
) -> EncodedConfirmedTransactionWithStatusMeta:
    """Parse a getTransaction result through the real solders parser."""
    response = GetTransactionResp.from_json(
        json.dumps({"jsonrpc": "2.0", "id": 1, "result": payload})
    )
    value = response.value
    assert value is not None
    return value


def make_transaction(
    signed_transaction: Transaction | VersionedTransaction | None = None,
    operator_operation_id: SolanaTransactionSignature | None = None,
) -> SolanaTransaction:
    platform = BlockchainPlatform(
        identifier="solana", type=SolanaBlockchainType, chain_id=None
    )
    return SolanaTransaction(
        client_operation_id="op-model-1",
        owner_identifier=SolanaWalletIdentifier(
            platform=platform,
            name=None,
            address=SolanaAddress.from_raw(Keypair().pubkey()),
        ),
        creation_timestamp=time.time(),
        operator_operation_id=operator_operation_id,
        signed_transaction=signed_transaction,
    )


class TestSolanaTransactionReceipt:
    def test_from_raw_maps_successful_transaction(self) -> None:
        keypair = Keypair()
        signature = Signature.new_unique()
        raw = parse_encoded_transaction(
            transaction_payload(keypair.pubkey(), Keypair().pubkey(), signature)
        )

        receipt = SolanaTransactionReceipt.from_raw(raw)

        assert receipt.transaction_id.raw == signature
        assert receipt.slot == SLOT
        assert receipt.block_time == BLOCK_TIME
        assert receipt.err is None
        assert receipt.fee == FEE_LAMPORTS
        assert receipt.fee_amount == Decimal(FEE_LAMPORTS)
        assert receipt.pre_balances == [1000000000, 0, 1]
        assert receipt.post_balances == [999994000, 1000, 1]
        assert receipt.pre_token_balances == []
        assert receipt.post_token_balances == []
        assert receipt.logs is not None
        assert len(receipt.logs) == 2
        assert receipt.rewards == []
        assert receipt.compute_units_consumed == 150

    def test_from_raw_with_explicit_transaction_id(self) -> None:
        keypair = Keypair()
        signature = Signature.new_unique()
        raw = parse_encoded_transaction(
            transaction_payload(keypair.pubkey(), Keypair().pubkey(), signature)
        )
        tx_sig = SolanaTransactionSignature.from_raw(signature)

        receipt = SolanaTransactionReceipt.from_raw(raw, transaction_id=tx_sig)

        assert receipt.transaction_id == tx_sig

    def test_from_raw_maps_failed_transaction(self) -> None:
        keypair = Keypair()
        signature = Signature.new_unique()
        raw = parse_encoded_transaction(
            transaction_payload(
                keypair.pubkey(),
                Keypair().pubkey(),
                signature,
                err={"InstructionError": [0, {"Custom": 1}]},
            )
        )

        receipt = SolanaTransactionReceipt.from_raw(raw)

        assert receipt.err is not None

    def test_from_raw_without_meta_defaults(self) -> None:
        keypair = Keypair()
        signature = Signature.new_unique()
        payload = transaction_payload(keypair.pubkey(), Keypair().pubkey(), signature)
        payload["meta"] = None
        raw = parse_encoded_transaction(payload)

        receipt = SolanaTransactionReceipt.from_raw(raw)

        assert receipt.transaction_id.raw == signature
        assert receipt.slot == SLOT
        assert receipt.err is None
        assert receipt.fee == 0
        assert receipt.pre_balances == []
        assert receipt.logs is None

    def test_receipt_is_frozen(self) -> None:
        keypair = Keypair()
        raw = parse_encoded_transaction(
            transaction_payload(
                keypair.pubkey(), Keypair().pubkey(), Signature.new_unique()
            )
        )
        receipt = SolanaTransactionReceipt.from_raw(raw)

        with pytest.raises(Exception, match="frozen"):
            receipt.fee = 1  # type: ignore[misc]


class TestSolanaRawTransaction:
    def test_from_raw_legacy_transaction(self) -> None:
        keypair = Keypair()
        blockhash = Hash.from_string(BLOCKHASH)
        instruction = transfer(
            TransferParams(
                from_pubkey=keypair.pubkey(),
                to_pubkey=Keypair().pubkey(),
                lamports=1000,
            )
        )
        message = Message.new_with_blockhash([instruction], keypair.pubkey(), blockhash)
        transaction = Transaction.new_unsigned(message)

        raw = SolanaRawTransaction.from_raw(transaction)

        assert raw.is_versioned is False
        assert raw.fee_payer == keypair.pubkey()
        assert raw.recent_blockhash == blockhash
        assert raw.message == message

    def test_from_raw_versioned_transaction(self) -> None:
        keypair = Keypair()
        blockhash = Hash.from_string(BLOCKHASH)
        instruction = transfer(
            TransferParams(
                from_pubkey=keypair.pubkey(),
                to_pubkey=Keypair().pubkey(),
                lamports=1000,
            )
        )
        message = MessageV0.try_compile(keypair.pubkey(), [instruction], [], blockhash)
        transaction = VersionedTransaction(message, [keypair])

        raw = SolanaRawTransaction.from_raw(transaction)

        assert raw.is_versioned is True
        assert raw.fee_payer == keypair.pubkey()
        assert raw.recent_blockhash == blockhash
        assert raw.signatures == list(transaction.signatures)


class TestSolanaTransaction:
    def test_solana_transactions_cannot_be_replaced(self) -> None:
        transaction = make_transaction()

        assert transaction.can_be_modified is False
        assert transaction.can_be_cancelled is False
        assert transaction.can_be_speeded_up is False

    def test_default_state_is_pending_broadcast(self) -> None:
        transaction = make_transaction()

        assert transaction.current_state == BlockchainTransactionState.PENDING_BROADCAST
        assert transaction.is_pending

    def test_is_signed_false_without_signed_transaction(self) -> None:
        transaction = make_transaction()

        assert transaction.is_signed is False

    def test_is_signed_false_with_placeholder_signatures(self) -> None:
        keypair = Keypair()
        blockhash = Hash.from_string(BLOCKHASH)
        instruction = transfer(
            TransferParams(
                from_pubkey=keypair.pubkey(),
                to_pubkey=Keypair().pubkey(),
                lamports=1000,
            )
        )
        message = Message.new_with_blockhash([instruction], keypair.pubkey(), blockhash)
        unsigned = VersionedTransaction.populate(message, [Signature.default()])
        transaction = make_transaction(signed_transaction=unsigned)

        assert transaction.is_signed is False

    def test_is_signed_true_with_real_signature(self) -> None:
        keypair = Keypair()
        blockhash = Hash.from_string(BLOCKHASH)
        instruction = transfer(
            TransferParams(
                from_pubkey=keypair.pubkey(),
                to_pubkey=Keypair().pubkey(),
                lamports=1000,
            )
        )
        message = Message.new_with_blockhash([instruction], keypair.pubkey(), blockhash)
        signed = VersionedTransaction(message, [keypair])
        transaction = make_transaction(signed_transaction=signed)

        assert transaction.is_signed is True

    def test_process_receipt_success(self) -> None:
        keypair = Keypair()
        raw = parse_encoded_transaction(
            transaction_payload(
                keypair.pubkey(), Keypair().pubkey(), Signature.new_unique()
            )
        )
        receipt = SolanaTransactionReceipt.from_raw(raw)
        transaction = make_transaction()

        assert transaction.process_receipt(receipt) is True
        assert transaction.receipt is receipt

    def test_process_receipt_failure(self) -> None:
        keypair = Keypair()
        raw = parse_encoded_transaction(
            transaction_payload(
                keypair.pubkey(),
                Keypair().pubkey(),
                Signature.new_unique(),
                err={"InstructionError": [0, {"Custom": 1}]},
            )
        )
        receipt = SolanaTransactionReceipt.from_raw(raw)
        transaction = make_transaction()

        assert transaction.process_receipt(receipt) is False
        assert transaction.receipt is receipt
