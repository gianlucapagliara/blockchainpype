from financepype.operators.blockchains.blockchain import Blockchain
from financepype.operators.blockchains.models import BlockchainConfiguration
from financepype.operators.factory import OperatorFactory
from financepype.owners.wallet import (
    BlockchainWallet,
    BlockchainWalletConfiguration,
    BlockchainWalletIdentifier,
)
from financepype.platforms.blockchain import BlockchainType


class BlockchainFactory(OperatorFactory):
    """Factory for creating blockchain instances.

    This factory handles the creation of appropriate blockchain instances based on
    the configuration and blockchain type. It maintains a cache of blockchain instances
    to ensure singleton behavior - only one instance per chain is created and reused.

    The factory works in conjunction with BlockchainRegistry to manage blockchain
    configurations and their corresponding instances.
    """

    _blockchain_classes: dict[BlockchainType, type[Blockchain]] = {}

    @classmethod
    def register_blockchain_class_for_type(
        cls,
        blockchain_class: type[Blockchain],
        blockchain_type: BlockchainType,
    ) -> None:
        """Register a new blockchain class for a specific blockchain type.
        This method is useful to not have to register the blockchain class for each
        blockchain platform.
        """
        cls._blockchain_classes[blockchain_type] = blockchain_class

    @classmethod
    def get_blockchain_types(cls) -> list[BlockchainType]:
        """Get all registered blockchain types."""
        return list(cls._blockchain_classes.keys())

    @classmethod
    def register_configuration(cls, configuration: BlockchainConfiguration) -> None:
        """Register a new blockchain configuration."""
        if configuration.platform.type in cls._blockchain_classes and (
            configuration.platform not in cls._operator_classes
        ):
            cls.register_operator_class(
                configuration.platform,
                cls._blockchain_classes[configuration.platform.type],
            )

        super().register_configuration(configuration)


class WalletRegistry:
    """A registry for wallet configurations.

    This class provides a flexible way to register and retrieve wallet configurations.
    It supports pre-configuring wallets at startup and retrieving their configurations
    later when needed.

    The registry allows for centralized wallet configuration management, making it
    easier to maintain consistent wallet settings across the application.
    """

    _configurations: dict[str, BlockchainWalletConfiguration] = {}

    @classmethod
    def register(cls, config: BlockchainWalletConfiguration) -> None:
        """Register a new wallet configuration.

        Args:
            config: The wallet configuration to register

        Raises:
            ValueError: If a configuration with the same identifier already exists
        """
        identifier = config.identifier.identifier
        if identifier in cls._configurations:
            raise ValueError(f"Wallet configuration for {identifier} already exists")
        cls._configurations[identifier] = config

    @classmethod
    def get(cls, identifier: str) -> BlockchainWalletConfiguration | None:
        """Get a wallet configuration by its identifier."""
        return cls._configurations.get(identifier)

    @classmethod
    def list(cls) -> dict[str, BlockchainWalletConfiguration]:
        """List all registered wallet configurations."""
        return cls._configurations.copy()


class WalletFactory:
    """Factory for creating blockchain wallet instances.

    This factory handles the creation of appropriate wallet instances based on
    the configuration and blockchain type. It maintains a cache of wallet instances
    to ensure singleton behavior - only one instance per wallet identifier is created and reused.

    The factory works in conjunction with WalletRegistry to manage wallet
    configurations and their corresponding instances.
    """

    _wallet_classes: dict[BlockchainType, type[BlockchainWallet]] = {}
    _wallet_instances: dict[str, BlockchainWallet] = {}

    @classmethod
    def register_wallet_class(
        cls, blockchain_type: BlockchainType, wallet_class: type[BlockchainWallet]
    ) -> None:
        """Register a new wallet class for a specific blockchain type."""
        cls._wallet_classes[blockchain_type] = wallet_class

    @classmethod
    def create(cls, identifier: BlockchainWalletIdentifier | str) -> BlockchainWallet:
        """Create or retrieve a wallet instance from a registered configuration.

        This method implements the singleton pattern - if an instance for the given
        wallet identifier already exists, it will be returned instead of creating a new one.

        Args:
            identifier: The identifier of the wallet configuration to use

        Returns:
            A wallet instance for the given configuration

        Raises:
            ValueError: If configuration or wallet class is not found
        """
        identifier = (
            identifier.identifier
            if isinstance(identifier, BlockchainWalletIdentifier)
            else identifier
        )

        if identifier in cls._wallet_instances:
            return cls._wallet_instances[identifier]

        config = WalletRegistry.get(identifier)
        if not config:
            raise ValueError(f"Wallet configuration not found for {identifier}")

        wallet_class = cls._wallet_classes.get(config.identifier.platform.type)
        if not wallet_class:
            raise ValueError(
                f"Wallet class not found for blockchain type {config.identifier.platform.type}"
            )

        instance = wallet_class(configuration=config)
        cls._wallet_instances[identifier] = instance
        return instance

    @classmethod
    def create_from_config(
        cls, config: BlockchainWalletConfiguration
    ) -> BlockchainWallet:
        """Create or retrieve a wallet instance directly from a configuration object.

        This method also implements the singleton pattern using the wallet's identifier
        as the cache key.

        Args:
            config: The wallet configuration to use

        Returns:
            The wallet instance

        Raises:
            ValueError: If no wallet class is found for the blockchain type
        """
        identifier = config.identifier.identifier

        if identifier in cls._wallet_instances:
            return cls._wallet_instances[identifier]

        wallet_class = cls._wallet_classes.get(config.identifier.platform.type)
        if not wallet_class:
            raise ValueError(
                f"Wallet class not found for blockchain type {config.identifier.platform.type}"
            )

        instance = wallet_class(configuration=config)
        cls._wallet_instances[identifier] = instance
        return instance

    @classmethod
    def get_instance(cls, identifier: str) -> BlockchainWallet | None:
        """Get an existing wallet instance from the cache."""
        return cls._wallet_instances.get(identifier)
