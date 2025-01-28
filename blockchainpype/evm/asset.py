"""
This module provides classes for handling Ethereum-based assets, including both native ETH
and ERC-20 tokens. It defines the data structures and relationships between different
types of Ethereum assets.
"""

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

    Attributes:
        data (EthereumAssetData): Asset-specific data including name, symbol, and decimals
    """

    data: EthereumAssetData


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
