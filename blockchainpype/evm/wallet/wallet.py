"""
This module provides comprehensive Ethereum wallet functionality, including transaction
management, balance tracking, and interaction with the Ethereum blockchain. It implements
wallet configuration, transaction signing, nonce management, and various transaction
operations like speedup and cancellation.
"""

import asyncio
import math
import time
import uuid
from collections.abc import Coroutine, Iterable
from datetime import timedelta
from decimal import Decimal
from typing import Any, cast

from eth_account.datastructures import SignedTransaction
from financepype.assets.blockchain import BlockchainAsset
from financepype.operations.transactions.models import (
    BlockchainTransactionFee,
    BlockchainTransactionState,
    BlockchainTransactionUpdate,
)
from financepype.owners.wallet import BlockchainWallet, BlockchainWalletConfiguration
from financepype.simulations.balances.tracking.tracker import BalanceType
from pydantic import Field
from web3.contract.async_contract import AsyncContractFunction
from web3.exceptions import TransactionNotFound
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

    DEFAULT_TRANSACTION_CLASS = EthereumTransaction

    _tracked_assets: set[BlockchainAsset]

    def __init__(
        self,
        configuration: EthereumWalletConfiguration,
        blockchain: EthereumBlockchain | None = None,
    ):
        """
        Initialize the Ethereum wallet with the provided configuration.

        Args:
            configuration (EthereumWalletConfiguration): The wallet configuration
            blockchain (EthereumBlockchain | None): Optional blockchain instance;
                resolved from the BlockchainFactory when not provided
        """
        super().__init__(configuration)

        if blockchain is None:
            blockchain = cast(
                EthereumBlockchain,
                BlockchainFactory.get(configuration.identifier.platform),
            )
        self._blockchain = blockchain

        self.last_nonce: int | None = None
        self.signer: EthereumSigner | None = (
            EthereumSigner(configuration.signer) if configuration.signer else None
        )

        # Serializes nonce re-synchronization and rollback against each other.
        # Plain allocation (allocate_nonce) is synchronous and therefore atomic
        # within the event loop, so it does not need to take the lock.
        self._nonce_lock = asyncio.Lock()
        self._background_tasks: set[asyncio.Task[None]] = set()

        self.add_tracked_assets([self.blockchain.native_asset])

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
            self._create_background_task(
                self.update_balance(cast(EthereumAsset, asset))
            )

    # === Nonce Management ===

    async def sync_nonce(self) -> None:
        """
        Synchronize the wallet's nonce with the blockchain.

        This method fetches the current transaction count for the wallet's address
        and updates the last_nonce accordingly.
        """
        async with self._nonce_lock:
            self.logger().info("Syncing nonce...")
            self.last_nonce = await self.blockchain.fetch_transaction_count(
                self.address
            )

    def allocate_nonce(self) -> int | None:
        """
        Allocate the next available nonce for a transaction.

        This method is synchronous (no awaits), so allocations are atomic within
        the event loop and concurrent callers always receive distinct nonces.

        Returns:
            int | None: The next available nonce, or None if nonce hasn't been synced
        """
        if self.last_nonce is None:
            return None

        nonce = self.last_nonce
        self.last_nonce += 1

        return nonce

    async def _release_nonce(self, nonce: int | None) -> None:
        """
        Return an allocated nonce after a rejected broadcast.

        The nonce is only rolled back when it is still the most recently
        allocated one; otherwise later in-flight transactions already consumed
        subsequent nonces and rolling back would hand out a duplicate.

        Args:
            nonce (int | None): The nonce used by the rejected transaction
        """
        if nonce is None:
            return

        async with self._nonce_lock:
            if self.last_nonce == nonce + 1:
                self.last_nonce = nonce
            else:
                self.logger().warning(
                    f"Not rolling back nonce {nonce}: later nonces were already allocated"
                )

    # === Transactions ===

    def sign_transaction(
        self, tx_data: dict[str, Any], auto_assign_nonce: bool = True
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
        tx_data: dict[str, Any],
        auto_assign_nonce: bool = True,
        transaction_class: Any | None = None,
        additional_kwargs: dict[str, Any] | None = None,
    ) -> EthereumTransaction:
        """
        Sign a transaction and broadcast it to the network.

        Calling this method again with the client_operation_id of an already
        signed transaction is an idempotent retry: the tracked transaction is
        returned unchanged without re-signing or re-broadcasting.

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

        if transaction.signed_transaction is not None:
            return transaction

        signed_tx = self.sign_transaction(tx_data, auto_assign_nonce=auto_assign_nonce)
        transaction.update_signed_transaction(signed_tx)

        nonce = cast(int | None, tx_data.get("nonce"))
        self._create_background_task(
            self.broadcast_transaction(transaction, nonce=nonce)
        )

        return transaction

    async def broadcast_transaction(
        self, transaction: EthereumTransaction, nonce: int | None = None
    ) -> None:
        """
        Broadcast a signed transaction to the network.

        On a rejected broadcast caused by a nonce mismatch the local nonce is
        re-synchronized from the chain; on any other rejection the allocated
        nonce is released so it can be reused.

        Args:
            transaction (EthereumTransaction): The signed transaction to broadcast
            nonce (int | None): The nonce used by the transaction, if known
        """
        transaction_update = await self.blockchain.send_transaction(transaction)
        if transaction_update.new_state == BlockchainTransactionState.REJECTED:
            exception = transaction_update.other_data.get("exception")
            if "nonce" in str(exception).lower():
                await self.sync_nonce()
            else:
                await self._release_nonce(nonce)

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

        if gas_configuration is None:
            gas_configuration = self.gas_configuration
        fees = await gas_configuration.get_gas(
            self.blockchain.web3,
            transaction_params=tx_data,
            gas_strategy=self.blockchain.configuration.gas_strategy,
        )
        tx_data = cast(TxParams, {**tx_data, **fees})

        self.logger().debug(f"Built transaction: {tx_data}")

        return tx_data

    @staticmethod
    def _increase_gas_value(value: int, increase_percentage: float) -> int:
        """
        Increase a gas fee value by a percentage, rounding up.

        The computation goes through Decimal so common percentages (e.g. 0.12)
        do not suffer from binary floating point drift.

        Args:
            value (int): The original fee value in wei
            increase_percentage (float): The relative increase (0.12 = +12%)

        Returns:
            int: The increased fee value in wei
        """
        return math.ceil(
            Decimal(value) * (Decimal(1) + Decimal(str(increase_percentage)))
        )

    async def modify_transaction(
        self,
        tx_hash: EthereumTransactionHash,
        tx_data_to_modify: dict[str, Any] | None = None,
        gas_increase_percentage: float = 0.12,
        transaction_class: Any | None = None,
        client_operation_id: str | None = None,
    ) -> EthereumTransaction:
        """
        Modify an existing transaction by creating a replacement with updated parameters.

        The replacement reuses the original transaction's nonce, bumps the gas
        fees by gas_increase_percentage over the original fees (both legacy
        gasPrice and EIP-1559 maxFeePerGas/maxPriorityFeePerGas are supported),
        applies tx_data_to_modify overrides on top and broadcasts the result.

        Args:
            tx_hash (EthereumTransactionHash): Hash of the transaction to modify
            tx_data_to_modify (dict | None): New transaction parameters
            gas_increase_percentage (float): Percentage to increase gas price
            transaction_class (Any | None): Optional transaction class to use
            client_operation_id (str | None): Optional operation ID for the new transaction

        Returns:
            EthereumTransaction: The tracked replacement transaction

        Raises:
            ValueError: If the original transaction cannot be found or carries
                no gas fee information
        """
        raw_transaction = await self.blockchain.fetch_raw_transaction(tx_hash)
        if raw_transaction is None:
            raise ValueError(f"Transaction {tx_hash.string} not found")

        tx_data: dict[str, Any] = {
            "nonce": int(raw_transaction.nonce),
            "value": int(raw_transaction.value)
            if raw_transaction.value is not None
            else 0,
        }
        if raw_transaction.to is not None:
            tx_data["to"] = raw_transaction.to.raw

        chain_id = (
            raw_transaction.chain_id
            if raw_transaction.chain_id is not None
            else self.blockchain.platform.chain_id
        )
        if chain_id is not None:
            tx_data["chainId"] = chain_id

        data = (
            raw_transaction.input
            if raw_transaction.input is not None
            else raw_transaction.data
        )
        if data is not None:
            tx_data["data"] = data

        if raw_transaction.gas is not None:
            tx_data["gas"] = int(raw_transaction.gas)

        # EIP-1559 transactions fetched from a node carry both maxFeePerGas and
        # an effective gasPrice, so the dynamic-fee fields take precedence.
        if raw_transaction.max_fee_per_gas is not None:
            tx_data["maxFeePerGas"] = self._increase_gas_value(
                int(raw_transaction.max_fee_per_gas), gas_increase_percentage
            )
            tx_data["maxPriorityFeePerGas"] = self._increase_gas_value(
                int(raw_transaction.max_priority_fee_per_gas or 0),
                gas_increase_percentage,
            )
        elif raw_transaction.gas_price is not None:
            tx_data["gasPrice"] = self._increase_gas_value(
                int(raw_transaction.gas_price), gas_increase_percentage
            )
        else:
            raise ValueError(
                f"Transaction {tx_hash.string} has no gas fee information to bump"
            )

        if tx_data_to_modify:
            tx_data.update(tx_data_to_modify)

        if client_operation_id is None:
            client_operation_id = f"modify-{tx_hash.string}-{uuid.uuid4().hex[:8]}"

        return self.sign_and_send_transaction(
            client_operation_id=client_operation_id,
            tx_data=tx_data,
            auto_assign_nonce=False,
            transaction_class=transaction_class,
        )

    async def speedup_transaction(
        self, tx_hash: EthereumTransactionHash, gas_increase_percentage: float = 0.13
    ) -> EthereumTransaction:
        """
        Re-broadcast a pending transaction with higher gas fees.

        Args:
            tx_hash (EthereumTransactionHash): Hash of the transaction to speed up
            gas_increase_percentage (float): Percentage to increase gas fees

        Returns:
            EthereumTransaction: The tracked replacement transaction
        """
        return await self.modify_transaction(
            tx_hash, gas_increase_percentage=gas_increase_percentage
        )

    async def cancel_transaction(
        self, tx_hash: EthereumTransactionHash, gas_increase_percentage: float = 0.13
    ) -> EthereumTransaction:
        """
        Cancel a pending transaction by replacing it with a zero-value self-transfer.

        Args:
            tx_hash (EthereumTransactionHash): Hash of the transaction to cancel
            gas_increase_percentage (float): Percentage to increase gas fees

        Returns:
            EthereumTransaction: The tracked cancellation transaction
        """
        return await self.modify_transaction(
            tx_hash,
            tx_data_to_modify={
                "to": self.address.raw,
                "value": 0,
                "data": b"",
                "gas": self.gas_configuration.default_cancel_gas,
            },
            gas_increase_percentage=gas_increase_percentage,
        )

    async def _fetch_receipt_update(
        self, transaction: EthereumTransaction
    ) -> BlockchainTransactionUpdate | None:
        """
        Try to build a transaction update from the on-chain receipt.

        Args:
            transaction (EthereumTransaction): The transaction to check

        Returns:
            BlockchainTransactionUpdate | None: The final update when the
                receipt is available, None while the transaction is unmined
        """
        tx_hash = transaction.operator_operation_id
        if tx_hash is None:
            return None

        try:
            receipt = await self.blockchain.fetch_transaction_receipt(tx_hash)
        except TransactionNotFound:
            receipt = None
        if receipt is None:
            return None

        # Post-Byzantium receipts carry status (0 = reverted); pre-Byzantium
        # receipts carry a state root instead and are treated as confirmed.
        new_state = (
            BlockchainTransactionState.FAILED
            if receipt.status == 0
            else BlockchainTransactionState.CONFIRMED
        )
        fee = BlockchainTransactionFee(
            amount=self.blockchain.native_asset.convert_to_decimals(
                int(receipt.fee_amount)
            ),
            asset=self.blockchain.native_asset,
        )
        explorer_link = (
            self.blockchain.explorer.get_transaction_link(tx_hash)
            if self.blockchain.explorer is not None
            else None
        )

        return BlockchainTransactionUpdate(
            update_timestamp=self.current_timestamp,
            client_transaction_id=transaction.client_operation_id,
            transaction_id=tx_hash,
            new_state=new_state,
            receipt=receipt,
            explorer_link=explorer_link,
            other_data={"fee": fee},
        )

    async def get_transaction_update(
        self,
        transaction: EthereumTransaction,
        timeout: timedelta,
        raise_timeout: bool,
        **kwargs: Any,
    ) -> BlockchainTransactionUpdate:
        """
        Poll the blockchain for a transaction's receipt.

        The receipt is polled until it is found or the timeout elapses. The
        receipt status is mapped to CONFIRMED (status 1) or FAILED (status 0)
        and the paid fee is reported in other_data["fee"]. When the timeout
        elapses and raise_timeout is False, an update carrying the current
        (unchanged) state is returned.

        Args:
            transaction (EthereumTransaction): The transaction to get an update for
            timeout (timedelta): Maximum time to wait for the receipt
            raise_timeout (bool): Whether to raise TimeoutError on timeout
            **kwargs: poll_interval (float) overrides the 2s polling interval

        Returns:
            BlockchainTransactionUpdate: The transaction update

        Raises:
            TimeoutError: If the receipt is not available within the timeout
                and raise_timeout is True
        """
        poll_interval = float(kwargs.get("poll_interval", 2.0))
        deadline = time.monotonic() + timeout.total_seconds()

        while True:
            update = await self._fetch_receipt_update(transaction)
            if update is not None:
                return update

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if raise_timeout:
                    raise TimeoutError(
                        f"Timed out waiting for transaction "
                        f"{transaction.client_operation_id} "
                        f"(hash={transaction.operator_operation_id})"
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
        """
        Fetch the on-chain balance of an asset in decimal-adjusted units.

        Both the native asset and ERC-20 tokens are reported in human-readable
        decimal units (e.g. ETH, not wei).

        Args:
            asset (EthereumAsset): The asset to fetch the balance for

        Returns:
            Decimal: The decimal-adjusted balance

        Raises:
            ValueError: If the asset belongs to another platform or is unsupported
        """
        if asset.platform != self.platform:
            raise ValueError("Asset platform does not match wallet platform")

        if asset == self.blockchain.native_asset:
            return await self.blockchain.fetch_native_asset_balance(self.address)
        elif isinstance(asset, ERC20Token):
            if not asset.contract.is_initialized:
                await asset.contract.initialize()
            return await asset.contract.get_balance_of(self.address)
        else:
            raise ValueError(f"Unsupported asset type: {type(asset)}")
