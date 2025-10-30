import os

from financepype.operators.blockchains.models import BlockchainConfiguration
from financepype.platforms.blockchain import BlockchainPlatform, BlockchainType
from pydantic import SecretStr
from solana.rpc.async_api import AsyncClient
from web3 import AsyncHTTPProvider

from blockchainpype.evm.blockchain.blockchain import (
    EthereumBlockchain,
    EthereumBlockchainType,
)
from blockchainpype.evm.blockchain.configuration import (
    EthereumBlockchainConfiguration,
    EthereumConnectivityConfiguration,
    EthereumNativeAssetConfiguration,
)
from blockchainpype.evm.explorer.etherscan import EtherscanConfiguration
from blockchainpype.factory import BlockchainFactory
from blockchainpype.solana.blockchain.blockchain import (
    SolanaBlockchain,
    SolanaBlockchainType,
)
from blockchainpype.solana.blockchain.configuration import (
    SolanaBlockchainConfiguration,
    SolanaConnectivityConfiguration,
)
from blockchainpype.solana.explorer.solscan import SolscanConfiguration


class SupportedBlockchainType(BlockchainType):
    EVM = EthereumBlockchainType
    SOLANA = SolanaBlockchainType


class BlockchainConfigurations:
    @classmethod
    def ethereum_configuration(cls) -> EthereumBlockchainConfiguration | None:
        api_key = os.getenv("ETHERSCAN_API_KEY")

        return EthereumBlockchainConfiguration(
            platform=BlockchainPlatform(
                identifier="ethereum",
                type=SupportedBlockchainType.EVM.value,
                chain_id=1,
            ),
            native_asset=EthereumNativeAssetConfiguration(),
            connectivity=EthereumConnectivityConfiguration(
                rpc_provider=AsyncHTTPProvider("https://eth.llamarpc.com")
            ),
            explorer=EtherscanConfiguration(
                base_url="https://etherscan.io",
                api_url="https://api.etherscan.io/v2/api",
                chain_id=1,
                api_key=SecretStr(api_key) if api_key else None,
            ),
        )

    @classmethod
    def hardhat_configuration(cls) -> EthereumBlockchainConfiguration | None:
        return EthereumBlockchainConfiguration(
            platform=BlockchainPlatform(
                identifier="hardhat",
                type=SupportedBlockchainType.EVM.value,
                local=True,
                testnet=True,
                chain_id=None,
            ),
            native_asset=EthereumNativeAssetConfiguration(),
            connectivity=EthereumConnectivityConfiguration(
                rpc_provider=AsyncHTTPProvider("http://127.0.0.1:8545/"),
                # ws_provider=WebSocketProvider("ws://127.0.0.1:8546"),
            ),
            explorer=None,
        )

    @classmethod
    def solana_configuration(cls) -> SolanaBlockchainConfiguration | None:
        return SolanaBlockchainConfiguration(
            platform=BlockchainPlatform(
                identifier="solana",
                type=SupportedBlockchainType.SOLANA.value,
                chain_id=None,
            ),
            connectivity=SolanaConnectivityConfiguration(
                rpc_provider=AsyncClient("https://api.mainnet-beta.solana.com"),
            ),
            explorer=SolscanConfiguration(),
        )

    @classmethod
    def configurations_methods(cls) -> list[str]:
        return [
            method
            for method in dir(cls)
            if callable(getattr(cls, method)) and method.endswith("_configuration")
        ]

    @classmethod
    def get_configurations(cls) -> dict[str, BlockchainConfiguration | None]:
        return {
            method.replace("_configuration", ""): getattr(cls, method)()
            for method in cls.configurations_methods()
        }


class BlockchainsInitializer:
    @classmethod
    def register_blockchain_classes(cls) -> None:
        """Register blockchain classes for different blockchain types."""
        BlockchainFactory.register_blockchain_class_for_type(
            EthereumBlockchain, SupportedBlockchainType.EVM.value
        )
        BlockchainFactory.register_blockchain_class_for_type(
            SolanaBlockchain, SupportedBlockchainType.SOLANA.value
        )

    @classmethod
    def register_blockchain_configurations(
        cls,
        blockchain_types: list[BlockchainType] | None = None,
        configurations: type[BlockchainConfigurations] = BlockchainConfigurations,
    ) -> None:
        """Register blockchain configurations for different blockchain types."""
        if blockchain_types is None:
            blockchain_types = BlockchainFactory.get_blockchain_types()

        for _, config in configurations.get_configurations().items():
            if config is not None and config.platform.type in blockchain_types:
                BlockchainFactory.register_configuration(config)

    @classmethod
    def configure(
        cls,
        blockchain_types: list[BlockchainType] | None = None,
        configurations: type[BlockchainConfigurations] = BlockchainConfigurations,
    ) -> None:
        cls.register_blockchain_classes()
        cls.register_blockchain_configurations(blockchain_types, configurations)
