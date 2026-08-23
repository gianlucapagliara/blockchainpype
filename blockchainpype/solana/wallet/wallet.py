"""
This module provides comprehensive Solana wallet functionality, including transaction
management, balance tracking, and interaction with the Solana blockchain. It implements
wallet configuration, transaction signing, and various transaction operations.
"""

import asyncio
import time
from collections.abc import Coroutine, Iterable
from datetime import timedelta
from decimal import Decimal
from typing import Any, cast

from financepype.assets.blockchain import BlockchainAsset
from financepype.operations.transactions.models import (
    BlockchainTransactionState,
    BlockchainTransactionUpdate,
)
from financepype.owners.wallet import BlockchainWallet, BlockchainWalletConfiguration
from financepype.simulations.balances.tracking.tracker import BalanceType
from solders.hash import Hash
from solders.message import Message, MessageV0
from solders.transaction import Transaction, VersionedTransaction
from solders.transaction_status import TransactionConfirmationStatus

from blockchainpype.factory import BlockchainFactory
from blockchainpype.solana.asset import SolanaAsset
from blockchainpype.solana.blockchain.blockchain import SolanaBlockchain
from blockchainpype.solana.blockchain.identifier import SolanaAddress
from blockchainpype.solana.dapp.token import SPLToken
from blockchainpype.solana.transaction import SolanaTransaction
from blockchainpype.solana.wallet.identifier import SolanaWalletIdentifier
from blockchainpype.solana.wallet.signer import SolanaSigner, SolanaSignerConfiguration


class SolanaWalletConfiguration(BlockchainWalletConfiguration):
    """
    Configuration class for Solana wallets.

    This class defines the configuration parameters needed for a Solana wallet,
    including wallet identification and signing capabilities.

    Attributes:
        identifier (SolanaWalletIdentifier): The wallet's unique identifier
        signer (SolanaSignerConfiguration | None): Optional signer configuration for transaction signing
    """

    identifier: SolanaWalletIdentifier
    signer: SolanaSignerConfiguration | None = None


class SolanaWallet(BlockchainWallet):
    """
    Implementation of a Solana wallet with full transaction and balance management capabilities.

    This class provides comprehensive functionality for interacting with the Solana blockchain,
    including transaction signing (legacy and versioned) and balance tracking for
    native SOL and SPL tokens.

    Attributes:
        signer (SolanaSigner | None): The signer instance for transaction signing
    """

    DEFAULT_TRANSACTION_CLASS = SolanaTransaction

    _tracked_assets: set[BlockchainAsset]

    def __init__(
        self,
        configuration: SolanaWalletConfiguration,
        blockchain: SolanaBlockchain | None = None,
    ):
        """
        Initialize the Solana wallet with the provided configuration.

        Args:
            configuration (SolanaWalletConfiguration): The wallet configuration
            blockchain (SolanaBlockchain | None): Optional blockchain instance;
                resolved from the BlockchainFactory when not provided
        """
        super().__init__(configuration)

        if blockchain is None:
            blockchain = cast(
                SolanaBlockchain,
                BlockchainFactory.get(configuration.identifier.platform),
            )
        self._blockchain = blockchain

        self.signer: SolanaSigner | None = (
            SolanaSigner(configuration.signer) if configuration.signer else None
        )

        self._background_tasks: set[asyncio.Task[None]] = set()

        self.add_tracked_assets([self.blockchain.native_asset])

    @property
    def configuration(self) -> SolanaWalletConfiguration:
        """
        Get the wallet's configuration.

        Returns:
            SolanaWalletConfiguration: The wallet configuration
        """
        return cast(SolanaWalletConfiguration, super().configuration)

    @property
    def address(self) -> SolanaAddress:
        """
        Get the wallet's Solana address.

        Returns:
            SolanaAddress: The wallet's address
        """
        return self.configuration.identifier.address

    @property
    def blockchain(self) -> SolanaBlockchain:
        """
        Get the Solana blockchain instance.

        Returns:
            SolanaBlockchain: The blockchain instance
        """
        return self._blockchain

    @property
    def current_timestamp(self) -> float:
        """
        Get the current timestamp used for transaction tracking.

        Returns:
            float: The current wall-clock time in seconds since the epoch
        """
        return time.time()

    # === Background Tasks ===

    def _create_background_task(
        self, coroutine: Coroutine[Any, Any, None]
    ) -> asyncio.Task[None]:
        """
        Schedule a fire-and-forget coroutine on the running event loop.

        A strong reference to the task is kept until completion so it cannot be
        garbage collected mid-flight, and any exception is logged instead of
        being silently dropped as an unobserved task exception.

        Args:
            coroutine: The coroutine to schedule

        Returns:
            asyncio.Task[None]: The scheduled task
        """
        task = asyncio.get_running_loop().create_task(coroutine)
        self._background_tasks.add(task)
        task.add_done_callback(self._on_background_task_done)
        return task

    def _on_background_task_done(self, task: asyncio.Task[None]) -> None:
        self._background_tasks.discard(task)
        if task.cancelled():
            return
        exception = task.exception()
        if exception is not None:
            self.logger().error(f"Background wallet task failed: {exception!r}")

    # === Tracked Assets ===

    def add_tracked_assets(self, assets: Iterable[BlockchainAsset]) -> None:
        """
        Add assets to track, scheduling a balance update for each new asset.

        Unlike the base implementation, balance updates are only scheduled when
        an event loop is running (their exceptions are logged, and the tasks are
        strongly referenced until completion). When called from synchronous code
        the assets are still registered, and their balances are populated by the
        next update_all_balances() call.

        Args:
            assets (Iterable[BlockchainAsset]): Assets to track
        """
        old_assets = self._tracked_assets.copy()
        self._tracked_assets = self._tracked_assets.union(assets)
        new_assets = self._tracked_assets - old_assets
        if not new_assets:
            return

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return

        for asset in new_assets:
            self._create_background_task(self.update_balance(cast(SolanaAsset, asset)))

    # === Signing ===

    def sign_transaction(
        self,
        transaction: Transaction | VersionedTransaction,
        recent_blockhash: Hash,
        additional_signers: list[SolanaSigner] | None = None,
    ) -> Transaction | VersionedTransaction:
        """
        Sign a transaction with the wallet's private key.

        Legacy transactions are partially signed in place with the provided
        blockhash. Versioned transactions are rebuilt with the provided
        blockhash (preserving the compiled instructions, account keys, and any
        address table lookups) and fully re-signed by the wallet and any
        additional signers.

        Args:
            transaction (Transaction | VersionedTransaction): The transaction to sign
            recent_blockhash (Hash): The blockhash to sign the transaction with
            additional_signers (list[SolanaSigner] | None): Extra required signers

        Returns:
            Transaction | VersionedTransaction: The signed transaction

        Raises:
            ValueError: If signer is not initialized
        """
        if self.signer is None:
            raise ValueError("Signer is not initialized")

        additional_signers = additional_signers or []
        keypairs = [
            self.signer.keypair,
            *[additional_signer.keypair for additional_signer in additional_signers],
        ]

        self.logger().debug(f"Signing transaction: {transaction}")
        if isinstance(transaction, Transaction):
            # Legacy transactions support partial signing in place
            transaction.partial_sign(keypairs, recent_blockhash)
        else:
            message = transaction.message
            if isinstance(message, MessageV0):
                new_message: Message | MessageV0 = MessageV0(
                    header=message.header,
                    account_keys=message.account_keys,
                    recent_blockhash=recent_blockhash,
                    instructions=message.instructions,
                    address_table_lookups=message.address_table_lookups,
                )
            else:
                new_message = Message.new_with_compiled_instructions(
                    num_required_signatures=message.header.num_required_signatures,
                    num_readonly_signed_accounts=message.header.num_readonly_signed_accounts,
                    num_readonly_unsigned_accounts=message.header.num_readonly_unsigned_accounts,
                    account_keys=message.account_keys,
                    recent_blockhash=recent_blockhash,
                    instructions=message.instructions,
                )
            # Constructing a VersionedTransaction signs the message with every
            # provided keypair, matching them to the required signers
            transaction = VersionedTransaction(new_message, keypairs)
        self.logger().debug(f"Signed. Signature: {transaction.signatures[0]}")

        return transaction

    def sign_and_send_transaction(
        self,
        client_operation_id: str,
        transaction: Transaction | VersionedTransaction,
        recent_blockhash: Hash,
        transaction_class: Any | None = None,
        additional_kwargs: dict[str, Any] | None = None,
    ) -> SolanaTransaction:
        """
        Sign a transaction and broadcast it to the network.

        Calling this method again with the client_operation_id of an already
        signed transaction is an idempotent retry: the tracked transaction is
        returned unchanged without re-signing or re-broadcasting.

        Args:
            client_operation_id (str): Unique identifier for the transaction
            transaction (Transaction | VersionedTransaction): The transaction to sign and send
            recent_blockhash (Hash): The blockhash to sign the transaction with
            transaction_class (Any | None): Optional transaction class to use
            additional_kwargs (dict[str, Any] | None): Additional arguments for transaction creation

        Returns:
            SolanaTransaction: The created and sent transaction
        """
        solana_transaction = cast(
            SolanaTransaction,
            self.prepare_tracking_transaction(
                client_operation_id=client_operation_id,
                transaction_class=transaction_class,
                additional_kwargs=additional_kwargs,
            ),
        )

        if solana_transaction.signed_transaction is not None:
            return solana_transaction

        signed_tx = self.sign_transaction(transaction, recent_blockhash)
        solana_transaction.update_signed_transaction(signed_tx)

        self._create_background_task(self.broadcast_transaction(solana_transaction))

        return solana_transaction

    async def broadcast_transaction(self, transaction: SolanaTransaction) -> None:
        """
        Broadcast a signed transaction to the network.

        Args:
            transaction (SolanaTransaction): The signed transaction to broadcast
        """
        transaction_update = await self.blockchain.send_transaction(transaction)
        self.transaction_tracker.process_transaction_update(
            transaction_update, lambda: self.current_timestamp
        )

    # === Balances ===

    async def update_balance(self, asset: SolanaAsset) -> None:
        """
        Update the balance for a specific asset.

        Args:
            asset (SolanaAsset): The asset to update the balance for
        """
        balance = await self.fetch_balance(asset)
        self.balance_tracker.set_balance(
            asset, balance, reason="update_balance", balance_type=BalanceType.TOTAL
        )
        self.balance_tracker.set_balance(
            asset, balance, reason="update_balance", balance_type=BalanceType.AVAILABLE
        )

    async def fetch_balance(self, asset: SolanaAsset) -> Decimal:
        """
        Fetch the on-chain balance of an asset in decimal-adjusted units.

        The native asset is reported in SOL (not lamports). SPL token balances
        are read from the wallet's associated token account, derived from
        (wallet address, mint), and scaled by the mint's decimals; a missing
        associated token account is reported as a zero balance.

        Args:
            asset (SolanaAsset): The asset to fetch the balance for

        Returns:
            Decimal: The decimal-adjusted balance

        Raises:
            ValueError: If the asset belongs to another platform or is unsupported
        """
        if asset.platform != self.platform:
            raise ValueError("Asset platform does not match wallet platform")

        if asset == self.blockchain.native_asset:
            return await self.blockchain.fetch_native_asset_balance(self.address)
        elif isinstance(asset, SPLToken):
            return await self.blockchain.fetch_spl_token_balance(
                self.address, asset.mint
            )
        else:
            raise ValueError(f"Unsupported asset type: {type(asset)}")

    # === Transactions ===

    async def _fetch_status_update(
        self, transaction: SolanaTransaction
    ) -> BlockchainTransactionUpdate | None:
        """
        Try to build a transaction update from the on-chain signature status.

        A transaction is only reported once it reaches at least the confirmed
        commitment (or fails); a merely processed transaction keeps polling.

        Args:
            transaction (SolanaTransaction): The transaction to check

        Returns:
            BlockchainTransactionUpdate | None: The final update when the
                transaction is confirmed, finalized, or failed; None while it
                is still unknown or only processed
        """
        tx_sig = transaction.operator_operation_id
        if tx_sig is None:
            return None

        status = await self.blockchain.fetch_transaction_status(tx_sig)
        if status is None:
            return None

        explorer_link = (
            self.blockchain.explorer.get_transaction_link(tx_sig)
            if self.blockchain.explorer is not None
            else None
        )

        if status.err is not None:
            receipt = await self.blockchain.fetch_transaction_receipt(tx_sig)
            return BlockchainTransactionUpdate(
                update_timestamp=self.current_timestamp,
                client_transaction_id=transaction.client_operation_id,
                transaction_id=tx_sig,
                new_state=BlockchainTransactionState.FAILED,
                receipt=receipt,
                explorer_link=explorer_link,
                other_data={"error": status.err},
            )

        if status.confirmation_status not in (
            TransactionConfirmationStatus.Confirmed,
            TransactionConfirmationStatus.Finalized,
        ):
            return None

        new_state = (
            BlockchainTransactionState.FINALIZED
            if status.confirmation_status == TransactionConfirmationStatus.Finalized
            else BlockchainTransactionState.CONFIRMED
        )
        receipt = await self.blockchain.fetch_transaction_receipt(tx_sig)
        return BlockchainTransactionUpdate(
            update_timestamp=self.current_timestamp,
            client_transaction_id=transaction.client_operation_id,
            transaction_id=tx_sig,
            new_state=new_state,
            receipt=receipt,
            explorer_link=explorer_link,
        )

    async def get_transaction_update(
        self,
        transaction: SolanaTransaction,
        timeout: timedelta,
        raise_timeout: bool,
        **kwargs: Any,
    ) -> BlockchainTransactionUpdate:
        """
        Poll the blockchain for a transaction's confirmation status.

        The signature status is polled until the transaction reaches at least
        the confirmed commitment (CONFIRMED/FINALIZED), fails (FAILED), or the
        timeout elapses. Confirmed and failed updates carry the transaction
        receipt fetched via getTransaction. When the timeout elapses and
        raise_timeout is False, an update carrying the current (unchanged)
        state is returned.

        Args:
            transaction (SolanaTransaction): The transaction to get an update for
            timeout (timedelta): Maximum time to wait for confirmation
            raise_timeout (bool): Whether to raise TimeoutError on timeout
            **kwargs: poll_interval (float) overrides the 2s polling interval

        Returns:
            BlockchainTransactionUpdate: The transaction update

        Raises:
            TimeoutError: If the transaction is not confirmed within the
                timeout and raise_timeout is True
        """
        poll_interval = float(kwargs.get("poll_interval", 2.0))
        deadline = time.monotonic() + timeout.total_seconds()

        while True:
            update = await self._fetch_status_update(transaction)
            if update is not None:
                return update

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if raise_timeout:
                    raise TimeoutError(
                        f"Timed out waiting for transaction "
                        f"{transaction.client_operation_id} "
                        f"(signature={transaction.operator_operation_id})"
                    )
                return BlockchainTransactionUpdate(
                    update_timestamp=self.current_timestamp,
                    client_transaction_id=transaction.client_operation_id,
                    transaction_id=transaction.operator_operation_id,
                    new_state=transaction.current_state,
                    receipt=None,
                    explorer_link=None,
                )

            await asyncio.sleep(min(poll_interval, remaining))
