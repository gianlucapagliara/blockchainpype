"""
This module provides classes for handling Solana-based assets, including both native SOL
and SPL tokens. It defines the data structures and relationships between different
types of Solana assets.
"""

from decimal import Decimal

from financepype.assets.blockchain import BlockchainAsset, BlockchainAssetData
from pydantic import Field

from blockchainpype.solana.blockchain.identifier import (
    SolanaAddress,
    SolanaNullAddress,
)


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

    This class represents any asset on the Solana blockchain, serving as a base for
    both native SOL and SPL tokens. It provides the foundation for asset management
    and interaction within the Solana ecosystem.

    Unlike the base BlockchainAsset, the data field is optional so asset metadata
    (name, symbol, decimals) can be provided lazily via initialize_data().
    Subclasses that can fetch metadata from the chain (e.g. SPL tokens reading
    the mint account or Metaplex metadata) should override initialize_data.

    Attributes:
        identifier (SolanaAddress): The asset's on-chain address (mint address
            for SPL tokens, the native-SOL sentinel for SOL)
        data (SolanaAssetData | None): Asset-specific data including name,
            symbol, and decimals; None until provided or initialized
    """

    identifier: SolanaAddress
    data: SolanaAssetData | None = None

    async def initialize_data(self) -> None:
        """
        Initialize the asset data.

        The base implementation only accepts assets whose data was supplied at
        construction time. Subclasses supporting on-chain metadata discovery
        must override this method.

        Raises:
            NotImplementedError: If data is missing and this asset type does
                not implement on-chain metadata fetching
        """
        if self.data is not None:
            return
        raise NotImplementedError(
            f"{type(self).__name__} does not support on-chain metadata fetching; "
            "provide the asset data at construction time"
        )

    @property
    def address(self) -> SolanaAddress:
        return self.identifier

    def convert_to_decimals(self, raw_amount: int) -> Decimal:
        """
        Convert raw token units (lamports for SOL) to decimal representation.

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
        Convert decimal amount to raw token units (lamports for SOL).

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


class SolanaNativeAsset(SolanaAsset):
    """
    Represents the native Solana asset (SOL).

    This class specifically handles the native SOL token, with predefined properties
    such as the null-address sentinel identifier (since native SOL has no mint
    account; see SolanaNullAddress for the distinction from the wrapped-SOL mint)
    and standard SOL token data.

    Attributes:
        identifier (SolanaNullAddress): The native-SOL sentinel address
        data (SolanaAssetData): Predefined SOL token data with name, symbol, and decimals
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

    async def initialize_data(self) -> None:
        return
