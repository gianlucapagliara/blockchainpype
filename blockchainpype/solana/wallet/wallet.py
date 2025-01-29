"""
This module provides comprehensive Solana wallet functionality, including transaction
management, balance tracking, and interaction with the Solana blockchain. It implements
wallet configuration, transaction signing, and various transaction operations.
"""

import asyncio
from datetime import timedelta
from decimal import Decimal
from typing import Any, cast

from financepype.operations.transactions.models import (
    BlockchainTransactionState,
    BlockchainTransactionUpdate,
)
from financepype.owners.wallet import BlockchainWallet, BlockchainWalletConfiguration
from financepype.simulations.balances.tracking.tracker import BalanceType
from solders.hash import Hash
from solders.message import Message, to_bytes_versioned
from solders.transaction import Transaction, VersionedTransaction

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
    including transaction signing and balance tracking.

    Attributes:
        signer (SolanaSigner | None): The signer instance for transaction signing
    """

    def __init__(self, configuration: SolanaWalletConfiguration):
        """
        Initialize the Solana wallet with the provided configuration.

        Args:
            configuration (SolanaWalletConfiguration): The wallet configuration
        """
        super().__init__(configuration)

        self._blockchain: SolanaBlockchain = cast(
            SolanaBlockchain,
            BlockchainFactory.create(configuration.identifier.platform.identifier),
        )
        self.signer: SolanaSigner | None = (
            SolanaSigner(configuration.signer) if configuration.signer else None
        )

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

    def sign_transaction(
        self,
        transaction: Transaction | VersionedTransaction,
        recent_blockhash: Hash,
        additional_signers: list[SolanaSigner] | None = None,
    ) -> Transaction | VersionedTransaction:
        """
        Sign a transaction with the wallet's private key.

        Args:
            transaction (Transaction | VersionedTransaction): The transaction to sign

        Returns:
            Transaction | VersionedTransaction: The signed transaction

        Raises:
            ValueError: If signer is not initialized
        """
        if self.signer is None:
            raise ValueError("Signer is not initialized")

        additional_signers = additional_signers or []

        self.logger().debug(f"Signing transaction: {transaction}")
        # For legacy transactions
        if isinstance(transaction, Transaction):
            transaction.partial_sign(
                [
                    self.signer.keypair,
                    *[
                        additional_signer.keypair
                        for additional_signer in additional_signers
                    ],
                ],
                recent_blockhash,
            )
        # For versioned transactions
        else:
            # Change recent_blockhash
            new_message = Message.new_with_blockhash(
                transaction.message.instructions,
                transaction.message.account_keys[0],
                recent_blockhash,
            )
            message_to_sign = to_bytes_versioned(new_message)
            signatures = [self.signer.keypair.sign_message(message_to_sign)]
            for additional_signer in additional_signers:
                signatures.append(
                    additional_signer.keypair.sign_message(message_to_sign)
                )
            transaction = transaction.populate(transaction.message, signatures)
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

        Args:
            client_operation_id (str): Unique identifier for the transaction
            transaction (Transaction | VersionedTransaction): The transaction to sign and send
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

        signed_tx = self.sign_transaction(transaction, recent_blockhash)
        solana_transaction.signed_transaction = signed_tx

        asyncio.ensure_future(self.broadcast_transaction(solana_transaction))

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
        Fetch the current balance for a specific asset.

        Args:
            asset (SolanaAsset): The asset to fetch the balance for

        Returns:
            Decimal: The current balance
        """
        if asset.platform != self.platform:
            raise ValueError("Asset platform does not match wallet platform")

        if asset == self.blockchain.native_asset:
            return await self.blockchain.fetch_native_asset_balance(self.address)
        elif isinstance(asset, SPLToken):
            if asset.program is None:
                raise ValueError("Token program is not initialized")
            return await asset.program.get_token_account_balance(self.address)
        else:
            raise ValueError(f"Unsupported asset type: {type(asset)}")

    # === Transactions ===

    async def get_transaction_update(
        self,
        transaction: SolanaTransaction,
        timeout: timedelta,
        raise_timeout: bool,
        **kwargs: Any,
    ) -> BlockchainTransactionUpdate:
        """
        Get an update for a specific transaction.

        Args:
            transaction (SolanaTransaction): The transaction to get an update for
            timeout (timedelta): Maximum time to wait for the update
            raise_timeout (bool): Whether to raise an exception on timeout
            **kwargs: Additional arguments for the update process

        Returns:
            BlockchainTransactionUpdate: The transaction update

        Raises:
            TimeoutError: If raise_timeout is True and the timeout is reached
        """
        if transaction.operator_operation_id is None:
            raise ValueError("Transaction has no operator operation ID")

        status = await self.blockchain.fetch_transaction_status(
            transaction.operator_operation_id
        )

        if status is None:
            return BlockchainTransactionUpdate(
                update_timestamp=self.current_timestamp,
                client_transaction_id=transaction.client_operation_id,
                transaction_id=transaction.operator_operation_id,
                new_state=BlockchainTransactionState.BROADCASTED,
                receipt=None,
                explorer_link=self.blockchain.explorer.get_transaction_link(
                    transaction.operator_operation_id
                )
                if self.blockchain.explorer is not None
                else None,
            )

        if status.err is not None:
            return BlockchainTransactionUpdate(
                update_timestamp=self.current_timestamp,
                client_transaction_id=transaction.client_operation_id,
                transaction_id=transaction.operator_operation_id,
                new_state=BlockchainTransactionState.FAILED,
                receipt=None,
                explorer_link=self.blockchain.explorer.get_transaction_link(
                    transaction.operator_operation_id
                )
                if self.blockchain.explorer is not None
                else None,
                other_data={"error": status.err},
            )

        return BlockchainTransactionUpdate(
            update_timestamp=self.current_timestamp,
            client_transaction_id=transaction.client_operation_id,
            transaction_id=transaction.operator_operation_id,
            new_state=BlockchainTransactionState.CONFIRMED,
            receipt=None,  # TODO: Implement receipt fetching
            explorer_link=self.blockchain.explorer.get_transaction_link(
                transaction.operator_operation_id
            )
            if self.blockchain.explorer is not None
            else None,
        )
