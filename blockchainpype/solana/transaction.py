"""
This module provides classes for handling Solana transactions, including transaction receipts,
raw transactions, and transaction management.
"""

from decimal import Decimal
from typing import Any, Self

from financepype.operations.transactions.models import BlockchainTransactionReceipt
from financepype.operations.transactions.transaction import BlockchainTransaction
from pydantic import BaseModel, ConfigDict
from solders.hash import Hash
from solders.message import Message, MessageV0
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import Transaction, VersionedTransaction
from solders.transaction_status import EncodedConfirmedTransactionWithStatusMeta

from blockchainpype.solana.blockchain.identifier import SolanaTransactionSignature


class SolanaTransactionReceipt(BlockchainTransactionReceipt):
    """
    Represents a Solana transaction receipt containing detailed information about a
    confirmed transaction.

    This class extends BlockchainTransactionReceipt to provide Solana-specific
    transaction receipt handling. It is built from the solders
    EncodedConfirmedTransactionWithStatusMeta returned by the getTransaction RPC.

    Attributes:
        transaction_id (SolanaTransactionSignature): The unique signature of the transaction
        slot (int): The slot number where this transaction was processed
        block_time (int | None): The block timestamp
        err (Any | None): Error information if the transaction failed
        fee (int): The fee paid for this transaction in lamports
        pre_balances (list[int]): Account balances before the transaction
        post_balances (list[int]): Account balances after the transaction
        pre_token_balances (list[Any] | None): Token balances before the transaction
        post_token_balances (list[Any] | None): Token balances after the transaction
        logs (list[str] | None): Program log messages
        rewards (list[Any] | None): Rewards issued by this transaction
        compute_units_consumed (int | None): Compute units consumed by the transaction
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    transaction_id: SolanaTransactionSignature
    slot: int
    block_time: int | None = None
    err: Any | None = None
    fee: int = 0
    pre_balances: list[int] = []
    post_balances: list[int] = []
    pre_token_balances: list[Any] | None = None
    post_token_balances: list[Any] | None = None
    logs: list[str] | None = None
    rewards: list[Any] | None = None
    compute_units_consumed: int | None = None

    @classmethod
    def from_raw(
        cls,
        raw_transaction: EncodedConfirmedTransactionWithStatusMeta,
        transaction_id: SolanaTransactionSignature | None = None,
    ) -> Self:
        """
        Creates a SolanaTransactionReceipt from a getTransaction RPC result.

        Args:
            raw_transaction (EncodedConfirmedTransactionWithStatusMeta): The
                confirmed transaction (with status meta) returned by the RPC
            transaction_id (SolanaTransactionSignature | None): The transaction
                signature; extracted from the encoded transaction when omitted

        Returns:
            Self: A new instance of SolanaTransactionReceipt

        Raises:
            ValueError: If no transaction_id is given and the encoded
                transaction carries no signatures
        """
        encoded = raw_transaction.transaction

        if transaction_id is None:
            signatures: list[Signature] = list(encoded.transaction.signatures)
            if not signatures:
                raise ValueError("Encoded transaction carries no signatures")
            transaction_id = SolanaTransactionSignature.from_raw(signatures[0])

        meta = encoded.meta
        if meta is None:
            # Very old ledger entries may miss status metadata entirely
            return cls(
                transaction_id=transaction_id,
                slot=raw_transaction.slot,
                block_time=raw_transaction.block_time,
            )

        return cls(
            transaction_id=transaction_id,
            slot=raw_transaction.slot,
            block_time=raw_transaction.block_time,
            err=meta.err,
            fee=meta.fee,
            pre_balances=list(meta.pre_balances),
            post_balances=list(meta.post_balances),
            pre_token_balances=list(meta.pre_token_balances)
            if meta.pre_token_balances is not None
            else None,
            post_token_balances=list(meta.post_token_balances)
            if meta.post_token_balances is not None
            else None,
            logs=list(meta.log_messages) if meta.log_messages is not None else None,
            rewards=list(meta.rewards) if meta.rewards is not None else None,
            compute_units_consumed=meta.compute_units_consumed,
        )

    @property
    def fee_amount(self) -> Decimal:
        """
        Gets the transaction fee in lamports.

        Returns:
            Decimal: The transaction fee in lamports
        """
        return Decimal(self.fee)


class SolanaRawTransaction(BaseModel):
    """
    Represents a raw Solana transaction before it's signed and submitted to the network.

    This class contains all the necessary fields for a Solana transaction, supporting
    both legacy and versioned transactions.

    Attributes:
        message (Message | MessageV0): The transaction message containing instructions
        signatures (list[Signature]): List of signatures for the transaction
        recent_blockhash (Hash): Recent blockhash for transaction timing
        fee_payer (Pubkey): The account that will pay transaction fees
        is_versioned (bool): Whether this is a versioned transaction
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    message: Message | MessageV0
    signatures: list[Signature]
    recent_blockhash: Hash
    fee_payer: Pubkey
    is_versioned: bool = False

    @classmethod
    def from_raw(cls, raw_transaction: Transaction | VersionedTransaction) -> Self:
        """
        Creates a SolanaRawTransaction instance from a raw transaction.

        Args:
            raw_transaction (Transaction | VersionedTransaction): Raw transaction from the blockchain

        Returns:
            Self: A new instance of SolanaRawTransaction
        """
        message = raw_transaction.message

        # In Solana, the first account in the account keys is always the fee payer
        fee_payer = message.account_keys[0]

        return cls(
            message=message,
            signatures=list(raw_transaction.signatures),
            recent_blockhash=message.recent_blockhash,
            fee_payer=fee_payer,
            is_versioned=isinstance(raw_transaction, VersionedTransaction),
        )


class SolanaTransaction(BlockchainTransaction):
    """
    High-level representation of a Solana transaction with additional functionality.

    This class extends BlockchainTransaction to provide Solana-specific transaction handling.
    Unlike Ethereum, Solana transactions cannot be modified, cancelled, or sped up once submitted.

    Attributes:
        operator_operation_id (SolanaTransactionSignature | None): Optional operator transaction ID
        signed_transaction (Transaction | VersionedTransaction | None): Signed transaction data
        raw_transaction (SolanaRawTransaction | None): Raw transaction data
        receipt (SolanaTransactionReceipt | None): Transaction receipt after processing
    """

    operator_operation_id: SolanaTransactionSignature | None = None
    signed_transaction: Transaction | VersionedTransaction | None = None
    raw_transaction: SolanaRawTransaction | None = None
    receipt: SolanaTransactionReceipt | None = None

    @property
    def can_be_modified(self) -> bool:
        """
        Indicates if the transaction can be modified.

        Returns:
            bool: Always False for Solana transactions
        """
        return False

    @property
    def can_be_cancelled(self) -> bool:
        """
        Indicates if the transaction can be cancelled.

        Returns:
            bool: Always False for Solana transactions
        """
        return False

    @property
    def can_be_speeded_up(self) -> bool:
        """
        Indicates if the transaction can be sped up.

        Returns:
            bool: Always False for Solana transactions
        """
        return False

    @property
    def is_signed(self) -> bool:
        """
        Checks if the transaction is signed.

        Returns:
            bool: True if the transaction carries at least one real
                (non-default) signature, False otherwise
        """
        if self.signed_transaction is None:
            return False
        return any(
            signature != Signature.default()
            for signature in self.signed_transaction.signatures
        )

    def process_receipt(self, receipt: SolanaTransactionReceipt) -> bool:
        """
        Process a transaction receipt and update transaction state.

        Args:
            receipt (SolanaTransactionReceipt): The transaction receipt to process

        Returns:
            bool: True if the transaction was successful, False otherwise
        """
        self.receipt = receipt
        return receipt.err is None
