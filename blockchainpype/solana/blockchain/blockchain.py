"""
This module provides the core functionality for interacting with the Solana blockchain.
It implements blockchain data retrieval, transaction management, and native asset operations
through solana-py/solders integration.
"""

from decimal import Decimal
from typing import cast

from financepype.operations.transactions.models import (
    BlockchainTransactionFee,
    BlockchainTransactionState,
    BlockchainTransactionUpdate,
)
from financepype.operators.blockchains.blockchain import BlockchainProcessor
from financepype.operators.blockchains.identifier import BlockchainIdentifier
from financepype.platforms.blockchain import BlockchainType
from solana.rpc.core import RPCException
from solana.rpc.types import TxOpts
from solders.hash import Hash
from solders.pubkey import Pubkey
from solders.transaction import Transaction, VersionedTransaction
from solders.transaction_status import (
    EncodedConfirmedTransactionWithStatusMeta,
    TransactionStatus,
    UiConfirmedBlock,
)
from spl.token.instructions import get_associated_token_address

from blockchainpype.blockchain import Blockchain
from blockchainpype.solana.asset import SolanaAssetData, SolanaNativeAsset
from blockchainpype.solana.blockchain.configuration import SolanaBlockchainConfiguration
from blockchainpype.solana.blockchain.identifier import (
    SolanaAddress,
    SolanaTransactionSignature,
)
from blockchainpype.solana.explorer.solscan import SolscanExplorer
from blockchainpype.solana.transaction import (
    SolanaTransaction,
    SolanaTransactionReceipt,
)
from blockchainpype.solana.wallet.identifier import SolanaWalletIdentifier


class _SolanaBlockchainType(BlockchainType):
    """
    Type of Solana blockchain.
    """

    SOLANA = "SOLANA"


SolanaBlockchainType = _SolanaBlockchainType.SOLANA


class SolanaBlockchain(Blockchain):
    """
    Implementation of the Solana blockchain interface.

    This class provides comprehensive functionality for interacting with the Solana
    blockchain, including:
    - RPC client management
    - Block and transaction data retrieval
    - Native asset (SOL) and SPL token balance operations
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

        self.rpc_client = configuration.connectivity.rpc_provider
        self.commitment = configuration.connectivity.rpc_provider.commitment

        self.native_asset = SolanaNativeAsset(
            platform=self.platform,
            data=SolanaAssetData(
                name=self.configuration.native_asset.name,
                symbol=self.configuration.native_asset.symbol,
                decimals=self.configuration.native_asset.decimals,
            ),
        )

        self._explorer: SolscanExplorer | None = None
        if configuration.explorer is not None:
            self._explorer = SolscanExplorer(configuration=configuration.explorer)

    @property
    def configuration(self) -> SolanaBlockchainConfiguration:
        """
        Get the blockchain configuration.

        Returns:
            SolanaBlockchainConfiguration: The current blockchain configuration
        """
        return cast(SolanaBlockchainConfiguration, super().configuration)

    @property
    def explorer(self) -> SolscanExplorer | None:
        return self._explorer

    # === Blockchain ===

    async def fetch_block_data(
        self,
        block_number: int,
        max_supported_transaction_version: int | None = 0,
    ) -> UiConfirmedBlock:
        """
        Fetch detailed data for a specific block.

        Args:
            block_number (int): Block number (slot)
            max_supported_transaction_version (int | None): Highest transaction
                version to return; defaults to 0 so blocks containing versioned
                (v0) transactions do not error

        Returns:
            UiConfirmedBlock: Detailed block information
        """
        return (
            await self.rpc_client.get_block(
                block_number,
                max_supported_transaction_version=max_supported_transaction_version,
            )
        ).value

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

    # === Balances ===

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
        return Decimal(self.native_asset.convert_to_decimals(balance))

    @staticmethod
    def derive_associated_token_account(
        owner: SolanaAddress, mint: SolanaAddress
    ) -> SolanaAddress:
        """
        Derive the associated token account (ATA) for an owner and mint.

        Args:
            owner (SolanaAddress): The token owner's wallet address
            mint (SolanaAddress): The token mint address

        Returns:
            SolanaAddress: The derived associated token account address
        """
        ata: Pubkey = get_associated_token_address(owner.raw, mint.raw)
        return cast(SolanaAddress, SolanaAddress.from_raw(ata))

    async def fetch_spl_token_balance(
        self, owner: SolanaAddress, mint: SolanaAddress
    ) -> Decimal:
        """
        Fetch an owner's SPL token balance via its associated token account.

        The associated token account is derived from (owner, mint) and the raw
        amount is scaled by the mint's decimals as reported by the RPC. An
        owner whose associated token account does not exist has a balance of 0.

        Args:
            owner (SolanaAddress): The token owner's wallet address
            mint (SolanaAddress): The token mint address

        Returns:
            Decimal: The decimal-adjusted token balance
        """
        token_account = self.derive_associated_token_account(owner, mint)
        try:
            balance = (
                await self.rpc_client.get_token_account_balance(
                    token_account.raw,
                    commitment=self.commitment,
                )
            ).value
        except RPCException as e:
            # A missing associated token account means the owner simply never
            # received this token: report a zero balance instead of erroring.
            error = e.args[0] if e.args else None
            message = str(getattr(error, "message", error))
            if "could not find account" in message.lower():
                return Decimal(0)
            raise
        return Decimal(balance.amount) / Decimal(10**balance.decimals)

    # === Transactions ===

    async def send_signed_transaction(
        self, signed_tx: Transaction | VersionedTransaction
    ) -> SolanaTransactionSignature:
        """
        Send a signed transaction to the network.

        Args:
            signed_tx (Transaction | VersionedTransaction): The signed
                transaction to send

        Returns:
            SolanaTransactionSignature: The transaction signature
        """
        resp = await self.rpc_client.send_transaction(signed_tx, opts=TxOpts())
        return cast(
            SolanaTransactionSignature,
            SolanaTransactionSignature.from_raw(resp.value),
        )

    async def send_transaction(
        self, transaction: SolanaTransaction
    ) -> BlockchainTransactionUpdate:
        """
        Send a transaction and create an update record.

        This method broadcasts the transaction to the network and creates a
        transaction update record with the result status.

        Args:
            transaction (SolanaTransaction): The transaction to send

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
        self,
        transaction_id: SolanaTransactionSignature,
        search_transaction_history: bool = True,
    ) -> TransactionStatus | None:
        """
        Fetch the status for a transaction.

        Args:
            transaction_id (SolanaTransactionSignature): Transaction signature
            search_transaction_history (bool): Whether the node should also
                search its ledger cache for signatures outside the recent
                status cache; defaults to True so transactions older than the
                recent-status window still resolve

        Returns:
            TransactionStatus | None: Transaction status information, or None
                when the signature is unknown to the node
        """
        return (
            await self.rpc_client.get_signature_statuses(
                [transaction_id.raw],
                search_transaction_history=search_transaction_history,
            )
        ).value[0]

    async def fetch_transaction_receipt(
        self, transaction_id: SolanaTransactionSignature
    ) -> SolanaTransactionReceipt | None:
        """
        Fetch the receipt for a transaction.

        Args:
            transaction_id (SolanaTransactionSignature): Transaction signature

        Returns:
            SolanaTransactionReceipt | None: Transaction receipt, or None when
                the transaction is unknown to the node or not yet confirmed
        """
        raw_transaction = (
            await self.rpc_client.get_transaction(
                transaction_id.raw,
                max_supported_transaction_version=0,
            )
        ).value
        if raw_transaction is None:
            return None
        return SolanaTransactionReceipt.from_raw(
            raw_transaction, transaction_id=transaction_id
        )

    @staticmethod
    def _extract_fee_payer(
        raw_transaction: EncodedConfirmedTransactionWithStatusMeta,
    ) -> Pubkey:
        """
        Extract the fee payer from an encoded transaction.

        In Solana the fee payer is always the first account key of the message.

        Args:
            raw_transaction: The encoded transaction returned by getTransaction

        Returns:
            Pubkey: The fee payer's public key

        Raises:
            ValueError: If the encoding does not carry account keys
        """
        encoded_tx = raw_transaction.transaction.transaction
        message = getattr(encoded_tx, "message", None)
        account_keys = getattr(message, "account_keys", None)
        if not account_keys:
            raise ValueError("Transaction does not carry account keys")

        first_key = account_keys[0]
        if isinstance(first_key, Pubkey):
            return first_key
        # Parsed encodings wrap keys in ParsedAccount objects
        return cast(Pubkey, first_key.pubkey)

    async def fetch_transaction(
        self, transaction_id: BlockchainIdentifier
    ) -> SolanaTransaction | None:
        """
        Fetch complete transaction information.

        This method retrieves all available information about a confirmed
        transaction, including its receipt and fee, mapped into a
        SolanaTransaction. A transaction returned by the RPC is at least
        confirmed: it is reported as CONFIRMED, or FAILED when its meta
        carries an error.

        Args:
            transaction_id (BlockchainIdentifier): Transaction identifier

        Returns:
            SolanaTransaction | None: Complete transaction information, or
                None when the transaction is unknown to the node

        Raises:
            ValueError: If the transaction ID is invalid
        """
        if not isinstance(transaction_id, SolanaTransactionSignature):
            raise ValueError(f"Invalid transaction id: {transaction_id}")

        raw_transaction = (
            await self.rpc_client.get_transaction(
                transaction_id.raw,
                max_supported_transaction_version=0,
            )
        ).value
        if raw_transaction is None:
            return None

        receipt = SolanaTransactionReceipt.from_raw(
            raw_transaction, transaction_id=transaction_id
        )

        ts: float = (
            float(raw_transaction.block_time)
            if raw_transaction.block_time is not None
            else self.current_timestamp
        )

        fee = BlockchainTransactionFee(
            amount=self.native_asset.convert_to_decimals(receipt.fee),
            asset=self.native_asset,
        )

        current_state = (
            BlockchainTransactionState.FAILED
            if receipt.err is not None
            else BlockchainTransactionState.CONFIRMED
        )

        fee_payer = self._extract_fee_payer(raw_transaction)

        transaction = SolanaTransaction(
            client_operation_id=transaction_id.string,
            operator_operation_id=transaction_id,
            owner_identifier=SolanaWalletIdentifier(
                platform=self.configuration.platform,
                name=None,
                address=SolanaAddress.from_raw(fee_payer),
            ),
            creation_timestamp=ts,
            last_update_timestamp=ts,
            current_state=current_state,
            receipt=receipt,
            fee=fee,
            explorer_link=self.explorer.get_transaction_link(transaction_id)
            if self.explorer is not None
            else None,
        )
        return transaction


class SolanaBlockchainProcessor(BlockchainProcessor):
    """
    Processor for handling Solana blockchain operations.

    This class extends the base BlockchainProcessor to provide Solana-specific
    functionality for transaction processing and monitoring.
    """

    pass
