"""
This module provides classes for handling Ethereum-based assets, including both native ETH
and ERC-20 tokens. It defines the data structures and relationships between different
types of Ethereum assets.
"""

from abc import abstractmethod
from decimal import Decimal

from financepype.assets.blockchain import BlockchainAsset, BlockchainAssetData
from pydantic import Field

from blockchainpype.evm.blockchain.identifier import (
    EthereumAddress,
    EthereumNullAddress,
)


class EthereumAssetData(BlockchainAssetData):
    """
    Data container for Ethereum asset information.

    This class extends BlockchainAssetData to provide Ethereum-specific asset data handling.
    It maintains compatibility with the general blockchain asset data structure while
    allowing for future Ethereum-specific extensions.
    """

    pass


class EthereumAsset(BlockchainAsset):
    """
    Base class for all Ethereum-based assets.

    This class represents any asset on the Ethereum blockchain, serving as a base for
    both native ETH and ERC-20 tokens. It provides the foundation for asset management
    and interaction within the Ethereum ecosystem.

    Unlike the base BlockchainAsset, the data field is optional so asset metadata
    (name, symbol, decimals) can be fetched lazily from the chain via
    initialize_data(). This is an abstract class: concrete subclasses (pydantic
    enforces this at instantiation) must implement initialize_data.

    Attributes:
        data (EthereumAssetData | None): Asset-specific data including name,
            symbol, and decimals; None until initialize_data() has run
    """

    identifier: EthereumAddress
    data: EthereumAssetData | None = None

    @abstractmethod
    async def initialize_data(self) -> None:
        """
        Initialize the asset data, typically by fetching it from the chain.
        """
        raise NotImplementedError

    @property
    def address(self) -> EthereumAddress:
        return self.identifier

    def convert_to_decimals(self, raw_amount: int) -> Decimal:
        """
        Convert raw token units to decimal representation.

        Args:
            raw_amount (int): The raw amount in smallest token units

        Returns:
            Decimal: The amount converted to decimal representation

        Raises:
            ValueError: If the asset data has not been initialized yet
        """
        if self.data is None:
            raise ValueError(
                "Asset data is not initialized; call initialize_data() first"
            )
        return Decimal(raw_amount) / Decimal(10**self.data.decimals)

    def convert_to_raw(self, decimal_amount: Decimal) -> int:
        """
        Convert decimal amount to raw token units.

        Args:
            decimal_amount (Decimal): The amount in decimal representation

        Returns:
            int: The amount converted to raw token units

        Raises:
            ValueError: If the asset data has not been initialized yet
        """
        if self.data is None:
            raise ValueError(
                "Asset data is not initialized; call initialize_data() first"
            )
        return int(decimal_amount * 10**self.data.decimals)


class EthereumNativeAsset(EthereumAsset):
    """
    Represents the native Ethereum asset (ETH).

    This class specifically handles the native ETH token, with predefined properties
    such as the null address identifier (since ETH doesn't have a contract address)
    and standard ETH token data.

    Attributes:
        identifier (EthereumAddress): The null address, as ETH has no contract address
        data (EthereumAssetData): Predefined ETH token data with name, symbol, and decimals
    """

    identifier: EthereumAddress = Field(
        default_factory=lambda: EthereumNullAddress(),
        init=False,
    )
    data: EthereumAssetData = Field(
        default_factory=lambda: EthereumAssetData(
            name="Ethereum",
            symbol="ETH",
            decimals=18,
        ),
    )

    async def initialize_data(self) -> None:
        return
