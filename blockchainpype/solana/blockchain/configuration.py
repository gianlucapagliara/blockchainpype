"""
This module provides configuration classes for Solana blockchain connectivity,
native asset properties, and blockchain-wide settings. It defines the structure
for configuring RPC providers and various blockchain-specific parameters.
"""

from financepype.operators.blockchains.models import BlockchainConfiguration
from pydantic import BaseModel, ConfigDict
from solana.rpc.async_api import AsyncClient

from blockchainpype.solana.explorer.solscan import SolscanConfiguration


class SolanaConnectivityConfiguration(BaseModel):
    """
    Configuration for Solana network connectivity.

    This class defines the connection settings for interacting with Solana networks,
    including RPC configuration and commitment levels.

    Attributes:
        rpc_config (RpcConfig): Configuration for RPC connection
        commitment: str: The commitment level for transactions and queries
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    rpc: AsyncClient


class SolanaNativeAssetConfiguration(BaseModel):
    """
    Configuration for the Solana native asset (SOL).

    This class defines the properties of the native Solana token,
    including its name, symbol, and decimal places.

    Attributes:
        name (str): Token name, defaults to "Solana"
        symbol (str): Token symbol, defaults to "SOL"
        decimals (int): Number of decimal places, defaults to 9
    """

    name: str = "Solana"
    symbol: str = "SOL"
    decimals: int = 9


class SolanaBlockchainConfiguration(BlockchainConfiguration):
    """
    Main configuration class for Solana blockchain interaction.

    This class combines all necessary configurations for interacting with a
    Solana network, including connectivity settings and native asset properties.

    Attributes:
        connectivity (SolanaConnectivityConfiguration): Network connection settings
        native_asset (SolanaNativeAssetConfiguration): Native SOL token configuration
    """

    connectivity: SolanaConnectivityConfiguration
    explorer: SolscanConfiguration | None = None
    native_asset: SolanaNativeAssetConfiguration = SolanaNativeAssetConfiguration()
