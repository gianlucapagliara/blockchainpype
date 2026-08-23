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
from blockchainpype.factory import BlockchainFactory, WalletFactory
from blockchainpype.solana.blockchain.blockchain import (
    SolanaBlockchain,
    SolanaBlockchainType,
)
from blockchainpype.solana.blockchain.configuration import (
    SolanaBlockchainConfiguration,
    SolanaConnectivityConfiguration,
)
from blockchainpype.solana.explorer.solscan import SolscanConfiguration

HARDHAT_CHAIN_ID = 31337


class SupportedBlockchainType(BlockchainType):
    EVM = EthereumBlockchainType
    SOLANA = SolanaBlockchainType


def normalize_blockchain_type(blockchain_type: BlockchainType) -> BlockchainType:
    """Normalize a blockchain type to the underlying chain-specific member.

    SupportedBlockchainType members wrap the chain-specific enum members
    (e.g. ``SupportedBlockchainType.EVM.value is EthereumBlockchainType``), so
    comparing them directly against ``platform.type`` fails. This helper
    unwraps such aliases so both spellings can be used interchangeably.

    Args:
        blockchain_type: Either a chain-specific member (e.g.
            ``EthereumBlockchainType``) or an aliasing member whose value is a
            chain-specific member (e.g. ``SupportedBlockchainType.EVM``)

    Returns:
        BlockchainType: The chain-specific blockchain type member
    """
    value = blockchain_type.value
    if isinstance(value, BlockchainType):
        return value
    return blockchain_type


class BlockchainConfigurations:
    @classmethod
    def ethereum_configuration(cls) -> EthereumBlockchainConfiguration | None:
        api_key = os.getenv("ETHERSCAN_API_KEY")

        return EthereumBlockchainConfiguration(
            platform=BlockchainPlatform(
                identifier="ethereum",
                type=EthereumBlockchainType,
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
                type=EthereumBlockchainType,
                local=True,
                testnet=True,
                chain_id=HARDHAT_CHAIN_ID,
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
                type=SolanaBlockchainType,
                chain_id=None,
            ),
            connectivity=SolanaConnectivityConfiguration(
                rpc_provider=AsyncClient("https://api.mainnet-beta.solana.com"),
            ),
            explorer=SolscanConfiguration(),
        )

    @classmethod
    def configuration_blockchain_types(cls) -> dict[str, BlockchainType]:
        """Declare the blockchain type of each named configuration.

        This mapping allows filtering configurations by blockchain type
        BEFORE building them, avoiding the construction (and associated
        resource allocation, e.g. RPC clients) of filtered-out configurations.
        Configurations missing from this mapping are built and filtered by
        their platform type afterwards.

        Returns:
            dict[str, BlockchainType]: Configuration name -> blockchain type
        """
        return {
            "ethereum": EthereumBlockchainType,
            "hardhat": EthereumBlockchainType,
            "solana": SolanaBlockchainType,
        }

    @classmethod
    def configurations_methods(cls) -> list[str]:
        return [
            method
            for method in dir(cls)
            if callable(getattr(cls, method)) and method.endswith("_configuration")
        ]

    @classmethod
    def get_configurations(
        cls, blockchain_types: list[BlockchainType] | None = None
    ) -> dict[str, BlockchainConfiguration | None]:
        """Build the declared configurations, optionally filtered by type.

        Args:
            blockchain_types: When provided, only configurations of these
                (already normalized) blockchain types are built and returned

        Returns:
            dict[str, BlockchainConfiguration | None]: Configuration name ->
                built configuration
        """
        declared_types = cls.configuration_blockchain_types()
        configurations: dict[str, BlockchainConfiguration | None] = {}
        for method in cls.configurations_methods():
            name = method.removesuffix("_configuration")

            declared = declared_types.get(name)
            if (
                blockchain_types is not None
                and declared is not None
                and declared not in blockchain_types
            ):
                continue

            config = getattr(cls, method)()
            if (
                blockchain_types is not None
                and config is not None
                and config.platform.type not in blockchain_types
            ):
                continue

            configurations[name] = config
        return configurations


class BlockchainsInitializer:
    @classmethod
    def register_blockchain_classes(cls) -> None:
        """Register blockchain classes for different blockchain types."""
        BlockchainFactory.register_blockchain_class_for_type(
            EthereumBlockchain, EthereumBlockchainType
        )
        BlockchainFactory.register_blockchain_class_for_type(
            SolanaBlockchain, SolanaBlockchainType
        )

    @classmethod
    def register_blockchain_configurations(
        cls,
        blockchain_types: list[BlockchainType] | None = None,
        configurations: type[BlockchainConfigurations] = BlockchainConfigurations,
    ) -> None:
        """Register blockchain configurations for different blockchain types.

        Blockchain types are normalized first, so both the chain-specific
        members (e.g. ``EthereumBlockchainType``) and their
        ``SupportedBlockchainType`` aliases are accepted. Configurations whose
        platform is already registered are skipped, making this idempotent.
        """
        if blockchain_types is None:
            blockchain_types = BlockchainFactory.get_blockchain_types()

        normalized_types = [normalize_blockchain_type(t) for t in blockchain_types]

        for config in configurations.get_configurations(normalized_types).values():
            if config is None or config.platform.type not in normalized_types:
                continue
            if BlockchainFactory.get_configuration(config.platform) is not None:
                continue
            BlockchainFactory.register_configuration(config)

    @classmethod
    def configure(
        cls,
        blockchain_types: list[BlockchainType] | None = None,
        configurations: type[BlockchainConfigurations] = BlockchainConfigurations,
    ) -> None:
        cls.register_blockchain_classes()
        cls.register_blockchain_configurations(blockchain_types, configurations)


class WalletsInitializer:
    """Registers the library's wallet classes into the WalletFactory.

    Mirror of BlockchainsInitializer for the wallet subsystem: after calling
    ``WalletsInitializer.configure()``, EthereumWallet and SolanaWallet can be
    created through WalletFactory for their respective platform types.
    """

    @classmethod
    def register_wallet_classes(cls) -> None:
        """Register wallet classes for different blockchain types."""
        # Imported lazily: the wallet modules import blockchainpype.factory,
        # so importing them at module level would risk an import cycle.
        from blockchainpype.evm.wallet.wallet import EthereumWallet
        from blockchainpype.solana.wallet.wallet import SolanaWallet

        WalletFactory.register_wallet_class(EthereumBlockchainType, EthereumWallet)
        WalletFactory.register_wallet_class(SolanaBlockchainType, SolanaWallet)

    @classmethod
    def configure(cls) -> None:
        cls.register_wallet_classes()
