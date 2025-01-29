"""
This module provides the core functionality for interacting with the Solana blockchain.
It implements blockchain data retrieval, transaction management, and native asset operations
through Solders integration.
"""

from decimal import Decimal
from typing import cast

from financepype.operations.transactions.models import (
    BlockchainTransactionState,
    BlockchainTransactionUpdate,
)
from financepype.operators.blockchains.blockchain import Blockchain, BlockchainProcessor
from solana.rpc.types import TxOpts
from solders.hash import Hash
from solders.transaction import Transaction, VersionedTransaction
from solders.transaction_status import TransactionStatus, UiConfirmedBlock

from blockchainpype.solana.asset import SolanaAssetData, SolanaNativeAsset
from blockchainpype.solana.blockchain.configuration import SolanaBlockchainConfiguration
from blockchainpype.solana.blockchain.identifier import (
    SolanaAddress,
    SolanaTransactionSignature,
)
from blockchainpype.solana.explorer.solscan import SolscanExplorer
from blockchainpype.solana.transaction import SolanaTransaction


class SolanaBlockchain(Blockchain):
    """
    Implementation of the Solana blockchain interface.

    This class provides comprehensive functionality for interacting with the Solana
    blockchain, including:
    - RPC client management
    - Block and transaction data retrieval
    - Native asset (SOL) operations
    - Transaction sending and tracking

    Attributes:
        rpc_client: Solana RPC client instance for blockchain interaction
        native_asset (SolanaNativeAsset): Native blockchain asset (SOL)
    """

    def __init__(self, configuration: SolanaBlockchainConfiguration):
        """
        Initialize the blockchain interface with the provided configuration.

        Args:
            configuration (SolanaBlockchainConfiguration): Configuration including
                connectivity settings and native asset properties
        """
        super().__init__(configuration)

        self.rpc_client = configuration.connectivity.rpc
        self.commitment = configuration.connectivity.rpc.commitment

        self.native_asset = SolanaNativeAsset(
            platform=self.platform,
            data=SolanaAssetData(
                name=self.configuration.native_asset.name,
                symbol=self.configuration.native_asset.symbol,
                decimals=self.configuration.native_asset.decimals,
            ),
        )

        self.explorer = None
        if configuration.explorer is not None:
            self.explorer = SolscanExplorer(configuration=configuration.explorer)

    @property
    def configuration(self) -> SolanaBlockchainConfiguration:
        """
        Get the blockchain configuration.

        Returns:
            SolanaBlockchainConfiguration: The current blockchain configuration
        """
        return cast(SolanaBlockchainConfiguration, super().configuration)

    # === Conversions ===

    def to_lamports(self, amount: Decimal) -> int:
        return int(amount * 10**9)

    def from_lamports(self, amount: int) -> Decimal:
        return Decimal(amount) / Decimal(f"{10**9}")

    # === Blockchain ===

    async def fetch_block_data(self, block_number: int) -> UiConfirmedBlock:
        """
        Fetch detailed data for a specific block.

        Args:
            block_number (int): Block number (slot)

        Returns:
            UiConfirmedBlock: Detailed block information
        """
        return (await self.rpc_client.get_block(block_number)).value

    async def fetch_block_number(self) -> int:
        """
        Fetch the current block number (slot).

        Returns:
            int: The latest block number (slot)
        """
        return (await self.rpc_client.get_slot()).value

    async def fetch_block_timestamp(self, block_number: int) -> int:
        """
        Fetch the timestamp of a specific block.

        Args:
            block_number (int): Block number (slot)

        Returns:
            int: Block timestamp in Unix format

        Raises:
            ValueError: If the block has no timestamp
        """
        block_time = (await self.rpc_client.get_block_time(block_number)).value
        if block_time is None:
            raise ValueError(f"Block {block_number} does not have a timestamp")
        return block_time

    async def fetch_recent_blockhash(self) -> Hash:
        """
        Fetch the recent blockhash.

        Returns:
            Hash: The recent blockhash
        """
        return (await self.rpc_client.get_latest_blockhash()).value.blockhash

    # === Native Asset ===

    async def fetch_native_asset_balance(self, address: SolanaAddress) -> Decimal:
        """
        Fetch the native asset (SOL) balance for an address.

        Args:
            address (SolanaAddress): The address to check

        Returns:
            Decimal: The balance in SOL (not lamports)
        """
        balance = (
            await self.rpc_client.get_balance(
                address.raw,
                commitment=self.commitment,
            )
        ).value
        return self.from_lamports(balance)

    # === Transactions ===

    async def send_signed_transaction(
        self, signed_tx: Transaction | VersionedTransaction
    ) -> SolanaTransactionSignature:
        """
        Send a signed transaction to the network.

        Args:
            signed_tx (SoldersTransaction): The signed transaction to send

        Returns:
            SolanaTransactionSignature: The transaction signature
        """
        raw_tx_sig = await self.rpc_client.send_transaction(signed_tx, opts=TxOpts())
        tx_sig = SolanaTransactionSignature.from_raw(raw_tx_sig)
        return tx_sig

    async def send_transaction(
        self, transaction: SolanaTransaction
    ) -> BlockchainTransactionUpdate:
        """
        Send a transaction and create an update record.

        This method broadcasts the transaction to the network and creates a
        transaction update record with the result status.

        Args:
            transaction (Any): The transaction to send

        Returns:
            BlockchainTransactionUpdate: Transaction update record

        Raises:
            ValueError: If the transaction is not signed
        """
        if transaction.signed_transaction is None:
            raise ValueError("Transaction is not signed")

        try:
            transaction_sig = await self.send_signed_transaction(
                transaction.signed_transaction
            )
            transaction_update = BlockchainTransactionUpdate(
                update_timestamp=self.current_timestamp,
                client_transaction_id=transaction.client_operation_id,
                transaction_id=transaction_sig,
                new_state=BlockchainTransactionState.BROADCASTED,
                receipt=None,
                explorer_link=self.explorer.get_transaction_link(transaction_sig)
                if self.explorer is not None
                else None,
            )
        except Exception as e:
            transaction_update = BlockchainTransactionUpdate(
                update_timestamp=self.current_timestamp,
                client_transaction_id=transaction.client_operation_id,
                transaction_id=None,
                new_state=BlockchainTransactionState.REJECTED,
                receipt=None,
                explorer_link=None,
                other_data={"exception": e},
            )
        return transaction_update

    async def fetch_transaction_status(
        self, transaction_id: SolanaTransactionSignature
    ) -> TransactionStatus | None:
        """
        Fetch the status for a transaction.

        Args:
            transaction_id (SolanaTransactionSignature): Transaction signature

        Returns:
            GetSignatureStatusesResponse: Transaction status information
        """
        return (
            await self.rpc_client.get_signature_statuses([transaction_id.raw])
        ).value[0]


class SolanaBlockchainProcessor(BlockchainProcessor):
    """
    Processor for handling Solana blockchain operations.

    This class extends the base BlockchainProcessor to provide Solana-specific
    functionality for transaction processing and monitoring.
    """

    pass
