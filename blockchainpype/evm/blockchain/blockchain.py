"""
This module provides the core functionality for interacting with EVM-compatible blockchains.
It implements blockchain data retrieval, transaction management, and native asset operations
through Web3.py integration and optional block explorer support.
"""

from decimal import Decimal
from typing import cast

from eth_account.datastructures import SignedTransaction
from eth_typing import BlockIdentifier
from financepype.operations.transactions.models import (
    BlockchainTransactionState,
    BlockchainTransactionUpdate,
)
from financepype.operators.blockchains.blockchain import Blockchain
from financepype.operators.blockchains.identifier import BlockchainIdentifier
from web3 import AsyncWeb3
from web3.types import BlockData

from blockchainpype.evm.asset import EthereumAssetData, EthereumNativeAsset
from blockchainpype.evm.blockchain.configuration import EthereumBlockchainConfiguration
from blockchainpype.evm.blockchain.identifier import (
    EthereumAddress,
    EthereumTransactionHash,
)
from blockchainpype.evm.explorer.etherscan import EtherscanExplorer
from blockchainpype.evm.transaction import (
    EthereumRawTransaction,
    EthereumTransaction,
    EthereumTransactionReceipt,
)
from blockchainpype.evm.wallet.identifier import EthereumWalletIdentifier


class EthereumBlockchain(Blockchain):
    """
    Implementation of an EVM-compatible blockchain interface.

    This class provides comprehensive functionality for interacting with Ethereum and
    EVM-compatible blockchains, including:
    - Web3 client management for both RPC and WebSocket connections
    - Block and transaction data retrieval
    - Native asset (ETH) operations
    - Transaction sending and tracking
    - Optional block explorer integration

    Attributes:
        web3 (AsyncWeb3): Asynchronous Web3 instance for blockchain interaction
        explorer (EtherscanExplorer | None): Optional block explorer interface
        native_asset (EthereumNativeAsset): Native blockchain asset (e.g., ETH)
    """

    def __init__(self, configuration: EthereumBlockchainConfiguration):
        """
        Initialize the blockchain interface with the provided configuration.

        Args:
            configuration (EthereumBlockchainConfiguration): Configuration including
                connectivity settings, explorer settings, and native asset properties
        """
        super().__init__(configuration)

        self.web3 = AsyncWeb3(
            provider=configuration.connectivity.rpc_provider,
            middleware=configuration.connectivity.middleware,
            modules=configuration.connectivity.modules,
            external_modules=configuration.connectivity.external_modules,
        )

        self.explorer = None
        if configuration.explorer is not None:
            self.explorer = EtherscanExplorer(configuration=configuration.explorer)

        self.native_asset = EthereumNativeAsset(
            platform=self.platform,
            data=EthereumAssetData(
                name=self.configuration.native_asset.name,
                symbol=self.configuration.native_asset.symbol,
                decimals=self.configuration.native_asset.decimals,
            ),
        )

    @property
    def configuration(self) -> EthereumBlockchainConfiguration:
        """
        Get the blockchain configuration.

        Returns:
            EthereumBlockchainConfiguration: The current blockchain configuration
        """
        return cast(EthereumBlockchainConfiguration, super().configuration)

    # === Blockchain ===

    async def fetch_block_data(self, block_number: BlockIdentifier) -> BlockData:
        """
        Fetch detailed data for a specific block.

        Args:
            block_number (BlockIdentifier): Block number or identifier (e.g., 'latest')

        Returns:
            BlockData: Detailed block information
        """
        return await self.web3.eth.get_block(block_number)

    async def fetch_block_number(self) -> int:
        """
        Fetch the current block number.

        Returns:
            int: The latest block number
        """
        return await self.web3.eth.get_block_number()

    async def fetch_block_timestamp(self, block_number: BlockIdentifier) -> int:
        """
        Fetch the timestamp of a specific block.

        Args:
            block_number (BlockIdentifier): Block number or identifier

        Returns:
            int: Block timestamp in Unix format

        Raises:
            ValueError: If the block has no timestamp
        """
        block_data = await self.fetch_block_data(block_number)
        if "timestamp" not in block_data:
            raise ValueError(f"Block {block_number} does not have a timestamp")

        return block_data["timestamp"]

    async def fetch_transaction_count(self, address: EthereumAddress) -> int:
        """
        Fetch the total number of transactions sent by an address (nonce).

        Args:
            address (EthereumAddress): The address to check

        Returns:
            int: The transaction count (nonce)
        """
        return await self.web3.eth.get_transaction_count(address.raw)

    # === Native Asset ===

    async def fetch_native_asset_balance(
        self, address: EthereumAddress, block_number: BlockIdentifier | None = None
    ) -> Decimal:
        """
        Fetch the native asset (ETH) balance for an address.

        Args:
            address (EthereumAddress): The address to check
            block_number (BlockIdentifier | None): Optional block number for historical balance

        Returns:
            Decimal: The balance in ETH (not Wei)
        """
        raw_balance = await self.web3.eth.get_balance(
            address.raw, block_identifier=block_number
        )
        balance = self.web3.from_wei(raw_balance, "ether")
        return Decimal(str(balance))

    # === Transactions ===

    async def send_signed_transaction(
        self, signed_tx: SignedTransaction
    ) -> EthereumTransactionHash:
        """
        Send a signed transaction to the network.

        Args:
            signed_tx (SignedTransaction): The signed transaction to send

        Returns:
            EthereumTransactionHash: The transaction hash
        """
        raw_tx_hash = await self.web3.eth.send_raw_transaction(
            signed_tx.raw_transaction
        )
        tx_hash = EthereumTransactionHash.from_raw(raw_tx_hash)
        return tx_hash

    async def send_transaction(
        self, transaction: EthereumTransaction
    ) -> BlockchainTransactionUpdate:
        """
        Send a transaction and create an update record.

        This method broadcasts the transaction to the network and creates a
        transaction update record with the result status.

        Args:
            transaction (EthereumTransaction): The transaction to send

        Returns:
            BlockchainTransactionUpdate: Transaction update record

        Raises:
            ValueError: If the transaction is not signed
        """
        if transaction.signed_transaction is None:
            raise ValueError("Transaction is not signed")

        try:
            transaction_hash = await self.send_signed_transaction(
                transaction.signed_transaction
            )
            transaction_update = BlockchainTransactionUpdate(
                update_timestamp=self.current_timestamp,
                client_transaction_id=transaction.client_operation_id,
                transaction_id=transaction_hash,
                new_state=BlockchainTransactionState.BROADCASTED,
                receipt=None,
                explorer_link=self.explorer.get_transaction_link(transaction_hash)
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

    async def fetch_transaction_receipt(
        self, transaction_id: EthereumTransactionHash
    ) -> EthereumTransactionReceipt | None:
        """
        Fetch the receipt for a transaction.

        Args:
            transaction_id (EthereumTransactionHash): Transaction hash

        Returns:
            EthereumTransactionReceipt | None: Transaction receipt if available
        """
        raw_receipt = await self.web3.eth.get_transaction_receipt(transaction_id.raw)
        return EthereumTransactionReceipt.from_raw(raw_receipt)

    async def fetch_raw_transaction(
        self, transaction_id: EthereumTransactionHash
    ) -> EthereumRawTransaction | None:
        """
        Fetch raw transaction data.

        Args:
            transaction_id (EthereumTransactionHash): Transaction hash

        Returns:
            EthereumRawTransaction | None: Raw transaction data if available
        """
        raw_transaction = await self.web3.eth.get_transaction(transaction_id.raw)
        return EthereumRawTransaction.from_raw(raw_transaction)

    async def fetch_transaction(
        self, transaction_id: BlockchainIdentifier
    ) -> EthereumTransaction | None:
        """
        Fetch complete transaction information.

        This method retrieves all available information about a transaction,
        including its raw data, receipt, and block information.

        Args:
            transaction_id (BlockchainIdentifier): Transaction identifier

        Returns:
            EthereumTransaction | None: Complete transaction information if available

        Raises:
            ValueError: If the transaction ID is invalid
        """
        if not isinstance(transaction_id, EthereumTransactionHash):
            raise ValueError(f"Invalid transaction id: {transaction_id}")

        raw_transaction = await self.fetch_raw_transaction(transaction_id)
        if raw_transaction is None:
            return None

        block_data = await self.fetch_block_data(raw_transaction.block_number)
        ts = block_data["timestamp"] if "timestamp" in block_data else 0

        transaction_receipt = await self.fetch_transaction_receipt(transaction_id)
        fee = None
        # if transaction_receipt is not None:
        #     fee = BlockchainTransactionFee(
        #         amount=transaction_receipt.fee_amount,
        #         asset=...,
        #     )

        transaction = EthereumTransaction(
            client_operation_id=transaction_id.string,
            operator_operation_id=transaction_id,
            owner_identifier=EthereumWalletIdentifier(
                platform=self.configuration.platform,
                address=raw_transaction.sender,
            ),
            creation_timestamp=ts,
            last_update_timestamp=ts,
            current_state=BlockchainTransactionState.CONFIRMED,
            raw_transaction=raw_transaction,
            receipt=transaction_receipt,
            fee=fee,
            explorer_link=self.explorer.get_transaction_link(transaction_id)
            if self.explorer is not None
            else None,
        )
        return transaction
