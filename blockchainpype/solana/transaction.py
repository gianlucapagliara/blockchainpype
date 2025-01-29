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
from solders.transaction_status import TransactionStatus

from blockchainpype.solana.blockchain.identifier import SolanaTransactionSignature


class SolanaTransactionReceipt(BlockchainTransactionReceipt):
    """
    Represents a Solana transaction receipt containing detailed information about a completed transaction.

    This class extends BlockchainTransactionReceipt to provide Solana-specific transaction receipt handling.
    It includes information such as transaction status, block details, and fee information.

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
        status (TransactionStatus): The status of the transaction
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    transaction_id: SolanaTransactionSignature
    slot: int
    block_time: int | None
    err: Any | None
    fee: int
    pre_balances: list[int]
    post_balances: list[int]
    pre_token_balances: list[Any] | None
    post_token_balances: list[Any] | None
    logs: list[str] | None
    rewards: list[Any] | None
    status: TransactionStatus

    @classmethod
    def from_raw(cls, raw_transaction_receipt: dict[str, Any]) -> Self:
        """
        Creates a SolanaTransactionReceipt instance from raw transaction receipt data.

        Args:
            raw_transaction_receipt (dict[str, Any]): Raw transaction receipt data from the blockchain

        Returns:
            Self: A new instance of SolanaTransactionReceipt
        """
        return cls(
            transaction_id=SolanaTransactionSignature.from_raw(
                raw_transaction_receipt["signature"]
            ),
            slot=raw_transaction_receipt["slot"],
            block_time=raw_transaction_receipt.get("blockTime"),
            err=raw_transaction_receipt.get("err"),
            fee=raw_transaction_receipt["fee"],
            pre_balances=raw_transaction_receipt["preBalances"],
            post_balances=raw_transaction_receipt["postBalances"],
            pre_token_balances=raw_transaction_receipt.get("preTokenBalances"),
            post_token_balances=raw_transaction_receipt.get("postTokenBalances"),
            logs=raw_transaction_receipt.get("logs"),
            rewards=raw_transaction_receipt.get("rewards"),
            status=raw_transaction_receipt["status"],
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
            signatures=raw_transaction.signatures,
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
            bool: True if the transaction is signed, False otherwise
        """
        return (
            self.signed_transaction is not None
            and len(self.signed_transaction.signatures) > 0
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
