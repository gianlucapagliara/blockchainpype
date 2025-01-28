"""
This module provides configuration classes for Ethereum blockchain connectivity,
native asset properties, and blockchain-wide settings. It defines the structure
for configuring Web3 providers, ENS integration, middleware, and various
blockchain-specific parameters.
"""

from collections.abc import Sequence
from typing import Any

from ens import AsyncENS
from financepype.operators.blockchains.models import BlockchainConfiguration
from pydantic import BaseModel, ConfigDict, Field
from web3 import WebSocketProvider
from web3.module import Module
from web3.providers.async_base import AsyncJSONBaseProvider

from blockchainpype.evm.blockchain.gas import GasStrategy
from blockchainpype.evm.blockchain.identifier import (
    EthereumAddress,
    EthereumNullAddress,
)
from blockchainpype.evm.explorer.etherscan import EtherscanConfiguration


class EthereumConnectivityConfiguration(BaseModel):
    """
    Configuration for Ethereum network connectivity.

    This class defines the connection settings for interacting with Ethereum networks,
    including RPC and WebSocket providers, ENS support, and middleware configuration.

    Attributes:
        rpc_provider (AsyncJSONBaseProvider): Async JSON-RPC provider for blockchain interaction
        ws_provider (WebSocketProvider | None): Optional WebSocket provider for real-time events
        ens (AsyncENS | None): Optional Ethereum Name Service integration
        middleware (list[Any]): List of Web3.py middleware to apply
        modules (dict[str, type[Module] | Sequence[Any]] | None): Optional Web3 modules to load
        external_modules (dict[str, type[Module] | Sequence[Any]] | None): Optional external modules
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    rpc_provider: AsyncJSONBaseProvider
    ws_provider: WebSocketProvider | None = None
    ens: AsyncENS | None = None
    middleware: list[Any] = Field(default_factory=lambda: [])
    modules: dict[str, type[Module] | Sequence[Any]] | None = None
    external_modules: dict[str, type[Module] | Sequence[Any]] | None = None


class EthereumNativeAssetConfiguration(BaseModel):
    """
    Configuration for the Ethereum native asset (ETH).

    This class defines the properties of the native Ethereum token, including
    its address (null address for ETH), name, symbol, and decimal places.

    Attributes:
        address (EthereumAddress): The null address for ETH
        name (str): Token name, defaults to "Ethereum"
        symbol (str): Token symbol, defaults to "ETH"
        decimals (int): Number of decimal places, defaults to 18
    """

    address: EthereumAddress = Field(
        default_factory=lambda: EthereumNullAddress(),
    )
    name: str = "Ethereum"
    symbol: str = "ETH"
    decimals: int = 18


class EthereumBlockchainConfiguration(BlockchainConfiguration):
    """
    Main configuration class for Ethereum blockchain interaction.

    This class combines all necessary configurations for interacting with an
    Ethereum network, including connectivity settings, block explorer integration,
    native asset properties, and gas fee strategy.

    Attributes:
        connectivity (EthereumConnectivityConfiguration): Network connection settings
        explorer (EtherscanConfiguration | None): Optional Etherscan integration settings
        native_asset (EthereumNativeAssetConfiguration): Native ETH token configuration
        gas_strategy (GasStrategy): Gas fee calculation strategy, defaults to EIP1559
    """

    connectivity: EthereumConnectivityConfiguration
    explorer: EtherscanConfiguration | None = None
    native_asset: EthereumNativeAssetConfiguration
    gas_strategy: GasStrategy = Field(default=GasStrategy.EIP1559)
