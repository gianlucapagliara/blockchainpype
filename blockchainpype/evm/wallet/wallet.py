"""
This module provides comprehensive Ethereum wallet functionality, including transaction
management, balance tracking, and interaction with the Ethereum blockchain. It implements
wallet configuration, transaction signing, nonce management, and various transaction
operations like speedup and cancellation.
"""

import asyncio
from datetime import timedelta
from decimal import Decimal
from typing import Any, cast

from eth_account.datastructures import SignedTransaction
from financepype.operations.transactions.models import (
    BlockchainTransactionState,
    BlockchainTransactionUpdate,
)
from financepype.owners.wallet import BlockchainWallet, BlockchainWalletConfiguration
from financepype.simulations.balances.tracking.tracker import BalanceType
from pydantic import Field
from web3.contract.async_contract import AsyncContractFunction
from web3.types import TxParams

from blockchainpype.evm.asset import EthereumAsset
from blockchainpype.evm.blockchain.blockchain import EthereumBlockchain
from blockchainpype.evm.blockchain.gas import GasConfiguration
from blockchainpype.evm.blockchain.identifier import (
    EthereumAddress,
    EthereumTransactionHash,
)
from blockchainpype.evm.dapp.erc20 import ERC20Token
from blockchainpype.evm.transaction import EthereumTransaction
from blockchainpype.evm.wallet.identifier import EthereumWalletIdentifier
from blockchainpype.evm.wallet.signer import EthereumSigner, EthereumSignerConfiguration
from blockchainpype.factory import BlockchainFactory


class EthereumWalletConfiguration(BlockchainWalletConfiguration):
    """
    Configuration class for Ethereum wallets.

    This class defines the configuration parameters needed for an Ethereum wallet,
    including wallet identification, signing capabilities, and gas settings.

    Attributes:
        identifier (EthereumWalletIdentifier): The wallet's unique identifier
        signer (EthereumSignerConfiguration | None): Optional signer configuration for transaction signing
        gas_configuration (GasConfiguration): Gas settings for transactions
    """

    identifier: EthereumWalletIdentifier
    signer: EthereumSignerConfiguration | None = None
    gas_configuration: GasConfiguration = Field(
        default_factory=lambda: GasConfiguration()
    )


class EthereumWallet(BlockchainWallet):
    """
    Implementation of an Ethereum wallet with full transaction and balance management capabilities.

    This class provides comprehensive functionality for interacting with the Ethereum blockchain,
    including transaction signing, nonce management, balance tracking, and gas optimization.

    Attributes:
        last_nonce (int | None): The last used nonce for transactions
        signer (EthereumSigner | None): The signer instance for transaction signing
    """

    def __init__(self, configuration: EthereumWalletConfiguration):
        """
        Initialize the Ethereum wallet with the provided configuration.

        Args:
            configuration (EthereumWalletConfiguration): The wallet configuration
        """
        super().__init__(configuration)

        self._blockchain: EthereumBlockchain = cast(
            EthereumBlockchain,
            BlockchainFactory.create(configuration.identifier.platform.identifier),
        )
        self.last_nonce: int | None = None
        self.signer: EthereumSigner | None = (
            EthereumSigner(configuration.signer) if configuration.signer else None
        )

    @property
    def configuration(self) -> EthereumWalletConfiguration:
        """
        Get the wallet's configuration.

        Returns:
            EthereumWalletConfiguration: The wallet configuration
        """
        return cast(EthereumWalletConfiguration, super().configuration)

    @property
    def address(self) -> EthereumAddress:
        """
        Get the wallet's Ethereum address.

        Returns:
            EthereumAddress: The wallet's address
        """
        return self.configuration.identifier.address

    @property
    def blockchain(self) -> EthereumBlockchain:
        """
        Get the Ethereum blockchain instance.

        Returns:
            EthereumBlockchain: The blockchain instance
        """
        return self._blockchain

    @property
    def gas_configuration(self) -> GasConfiguration:
        """
        Get the wallet's gas configuration.

        Returns:
            GasConfiguration: The gas configuration settings
        """
        return self.configuration.gas_configuration

    async def sync_nonce(self) -> None:
        """
        Synchronize the wallet's nonce with the blockchain.

        This method fetches the current transaction count for the wallet's address
        and updates the last_nonce accordingly.
        """
        self.logger().info("Syncing nonce...")
        self.last_nonce = await self.blockchain.fetch_transaction_count(self.address)

    def allocate_nonce(self) -> int | None:
        """
        Allocate the next available nonce for a transaction.

        Returns:
            int | None: The next available nonce, or None if nonce hasn't been synced
        """
        if self.last_nonce is None:
            return None

        nonce = self.last_nonce
        self.last_nonce += 1

        return nonce

    # === Transactions ===

    def sign_transaction(
        self, tx_data: dict, auto_assign_nonce: bool = True
    ) -> SignedTransaction:
        """
        Sign a transaction with the wallet's private key.

        Args:
            tx_data (dict): The transaction data to sign
            auto_assign_nonce (bool): Whether to automatically assign the next nonce

        Returns:
            SignedTransaction: The signed transaction

        Raises:
            ValueError: If signer is not initialized or nonce allocation fails
        """
        if self.signer is None:
            raise ValueError("Signer is not initialized")

        if auto_assign_nonce:
            nonce = self.allocate_nonce()
            if nonce is None:
                raise ValueError("Failed to allocate nonce, not synced yet.")
            tx_data["nonce"] = nonce

        self.logger().debug(f"Signing transaction: {tx_data}")
        signed = self.signer.sign_transaction(tx_data)
        self.logger().debug(f"Signed. Hash: {signed.hash.hex()}")

        return signed

    def sign_and_send_transaction(
        self,
        client_operation_id: str,
        tx_data: dict,
        auto_assign_nonce: bool = True,
        transaction_class: Any | None = None,
        additional_kwargs: dict[str, Any] | None = None,
    ) -> EthereumTransaction:
        """
        Sign a transaction and broadcast it to the network.

        Args:
            client_operation_id (str): Unique identifier for the transaction
            tx_data (dict): The transaction data to sign and send
            auto_assign_nonce (bool): Whether to automatically assign the next nonce
            transaction_class (Any | None): Optional transaction class to use
            additional_kwargs (dict[str, Any] | None): Additional arguments for transaction creation

        Returns:
            EthereumTransaction: The created and sent transaction
        """
        transaction = cast(
            EthereumTransaction,
            self.prepare_tracking_transaction(
                client_operation_id=client_operation_id,
                transaction_class=transaction_class,
                additional_kwargs=additional_kwargs,
            ),
        )

        signed_tx = self.sign_transaction(tx_data, auto_assign_nonce=auto_assign_nonce)
        transaction.update_signed_transaction(signed_tx)

        asyncio.ensure_future(self.broadcast_transaction(transaction))

        return transaction

    async def broadcast_transaction(self, transaction: EthereumTransaction) -> None:
        """
        Broadcast a signed transaction to the network.

        Args:
            transaction (EthereumTransaction): The signed transaction to broadcast
        """
        transaction_update = await self.blockchain.send_transaction(transaction)
        if transaction_update.new_state == BlockchainTransactionState.REJECTED:
            if "nonce" in str(
                transaction_update.other_data.get("exception", "").lower()
            ):
                self._poll_notifier.set()
            elif self.last_nonce is not None:
                self.last_nonce -= 1

        self.transaction_tracker.process_transaction_update(
            transaction_update, lambda: self.current_timestamp
        )

    async def _process_rejection(
        self, client_operation_id: str, exception: Exception | None = None
    ) -> None:
        if "nonce" in str(exception).lower():
            self._poll_notifier.set()

        transaction_update = BlockchainTransactionUpdate(
            update_timestamp=self.current_timestamp,
            client_transaction_id=client_operation_id,
            transaction_id=None,
            new_state=BlockchainTransactionState.REJECTED,
            receipt=None,
            explorer_link=None,
        )

        self.transaction_tracker.process_transaction_update(
            transaction_update, lambda: self.current_timestamp
        )

    async def build_transaction(
        self,
        function: AsyncContractFunction | None = None,
        tx_data: TxParams | None = None,
        gas_configuration: GasConfiguration | None = None,
    ) -> TxParams:
        """
        Build a transaction with appropriate gas settings and chain configuration.

        Args:
            function (AsyncContractFunction | None): Optional contract function to call
            tx_data (TxParams | None): Optional base transaction parameters
            gas_configuration (GasConfiguration | None): Optional custom gas configuration

        Returns:
            TxParams: The complete transaction parameters
        """
        if tx_data is None:
            tx_data = TxParams()

        tx_data = cast(
            TxParams,
            {
                "from": self.address.raw,
                "chainId": self.blockchain.platform.chain_id,
                **tx_data,
            },
        )

        if function is not None:
            tx_data = await function.build_transaction(tx_data)
        fees = await self.gas_configuration.get_gas(
            self.blockchain.web3,
            transaction_params=tx_data,
            gas_strategy=self.blockchain.configuration.gas_strategy,
        )
        tx_data = cast(TxParams, {**tx_data, **fees})

        self.logger().debug(f"Built transaction: {tx_data}")

        return tx_data

    async def modify_transaction(
        self,
        tx_hash: EthereumTransactionHash,
        tx_data_to_modify: dict | None = None,
        gas_increase_percentage: float = 0.12,
        transaction_class: Any | None = None,
        client_operation_id: str | None = None,
    ) -> EthereumTransaction:
        """
        Modify an existing transaction by creating a replacement with updated parameters.

        Args:
            tx_hash (EthereumTransactionHash): Hash of the transaction to modify
            tx_data_to_modify (dict | None): New transaction parameters
            gas_increase_percentage (float): Percentage to increase gas price
            transaction_class (Any | None): Optional transaction class to use
            client_operation_id (str | None): Optional operation ID for the new transaction

        Returns:
            EthereumTransaction: The modified transaction

        Raises:
            NotImplementedError: This method needs to be implemented
        """
        raise NotImplementedError

    async def speedup_transaction(
        self, tx_hash: EthereumTransactionHash, gas_increase_percentage: float = 0.13
    ) -> EthereumTransaction:
        return await self.modify_transaction(
            tx_hash, gas_increase_percentage=gas_increase_percentage
        )

    async def cancel_transaction(
        self, tx_hash: EthereumTransactionHash, gas_increase_percentage: float = 0.13
    ) -> EthereumTransaction:
        return await self.modify_transaction(
            tx_hash,
            tx_data_to_modify={
                "to": self.address.raw,
                "value": 0,
                "gas": self.gas_configuration.default_cancel_gas,
            },
            gas_increase_percentage=gas_increase_percentage,
        )

    async def get_transaction_update(
        self,
        transaction: EthereumTransaction,
        timeout: timedelta,
        raise_timeout: bool,
        **kwargs: Any,
    ) -> BlockchainTransactionUpdate: ...

    # === Balances ===

    async def update_balance(self, asset: EthereumAsset) -> None:
        """
        Update the balance for a specific asset.

        Args:
            asset (EthereumAsset): The asset to update the balance for
        """
        balance = await self.fetch_balance(asset)
        self.balance_tracker.set_balance(
            asset, balance, reason="update_balance", balance_type=BalanceType.TOTAL
        )
        self.balance_tracker.set_balance(
            asset, balance, reason="update_balance", balance_type=BalanceType.AVAILABLE
        )

    async def fetch_balance(self, asset: EthereumAsset) -> Decimal:
        if asset.platform != self.platform:
            raise ValueError("Asset platform does not match wallet platform")

        if asset == self.blockchain.native_asset:
            return await self.blockchain.fetch_native_asset_balance(self.address)
        elif isinstance(asset, ERC20Token):
            if asset.contract is None:
                raise ValueError("Token contract is not initialized")
            return await asset.contract.get_balance_of(self.address)
        else:
            raise ValueError(f"Unsupported asset type: {type(asset)}")
