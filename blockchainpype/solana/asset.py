"""
This module provides classes for handling Ethereum-based assets, including both native ETH
and ERC-20 tokens. It defines the data structures and relationships between different
types of Ethereum assets.
"""

from financepype.assets.blockchain import BlockchainAsset, BlockchainAssetData
from pydantic import Field

from blockchainpype.solana.blockchain.identifier import SolanaNullAddress


class SolanaAssetData(BlockchainAssetData):
    """
    Data container for Solana asset information.

    This class extends BlockchainAssetData to provide Solana-specific asset data handling.
    It maintains compatibility with the general blockchain asset data structure while
    allowing for future Solana-specific extensions.
    """

    pass


class SolanaAsset(BlockchainAsset):
    """
    Base class for all Solana-based assets.

    This class represents any asset on the Ethereum blockchain, serving as a base for
    both native ETH and ERC-20 tokens. It provides the foundation for asset management
    and interaction within the Ethereum ecosystem.

    Attributes:
        data (EthereumAssetData): Asset-specific data including name, symbol, and decimals
    """

    data: SolanaAssetData


class SolanaNativeAsset(SolanaAsset):
    """
    Represents the native Solana asset (SOL).

    This class specifically handles the native Solana token, with predefined properties
    such as the null address identifier (since Solana doesn't have a contract address)
    and standard Solana token data.

    Attributes:
        identifier (SolanaNullAddress): The null address, as Solana has no contract address
        data (SolanaAssetData): Predefined Solana token data with name, symbol, and decimals
    """

    identifier: SolanaNullAddress = Field(
        default_factory=lambda: SolanaNullAddress(),
        init=False,
    )
    data: SolanaAssetData = Field(
        default_factory=lambda: SolanaAssetData(
            name="Solana",
            symbol="SOL",
            decimals=9,
        ),
    )
