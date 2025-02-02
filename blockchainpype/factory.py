from financepype.operators.blockchains.blockchain import Blockchain
from financepype.operators.blockchains.models import BlockchainConfiguration
from financepype.owners.wallet import (
    BlockchainWallet,
    BlockchainWalletConfiguration,
    BlockchainWalletIdentifier,
)
from financepype.platforms.blockchain import BlockchainType


class BlockchainRegistry:
    """A registry for blockchain configurations.

    This class provides a flexible way to register and retrieve blockchain configurations.
    It supports dynamic registration of new configurations and lazy loading of configurations.
    The registry is necessary for blockchains because their configurations need to be
    stored and shared across the application.
    """

    _configurations: dict[str, BlockchainConfiguration] = {}

    @classmethod
    def register(cls, config: BlockchainConfiguration) -> None:
        """Register a new blockchain configuration."""
        if config.platform.identifier in cls._configurations:
            raise ValueError(
                f"Blockchain configuration for {config.platform.identifier} already exists"
            )
        cls._configurations[config.platform.identifier] = config

    @classmethod
    def get(cls, identifier: str) -> BlockchainConfiguration | None:
        """Get a blockchain configuration by name."""
        return cls._configurations.get(identifier)

    @classmethod
    def list(cls) -> dict[str, BlockchainConfiguration]:
        """List all registered configurations."""
        return cls._configurations.copy()


class BlockchainFactory:
    """Factory for creating blockchain instances.

    This factory handles the creation of appropriate blockchain instances based on
    the configuration and blockchain type. It maintains a cache of blockchain instances
    to ensure singleton behavior - only one instance per chain is created and reused.

    The factory works in conjunction with BlockchainRegistry to manage blockchain
    configurations and their corresponding instances.
    """

    _blockchain_classes: dict[BlockchainType, type[Blockchain]] = {}
    _blockchain_instances: dict[str, Blockchain] = {}

    @classmethod
    def register_blockchain_class(
        cls, blockchain_type: BlockchainType, blockchain_class: type[Blockchain]
    ) -> None:
        """Register a new blockchain class for a specific blockchain type."""
        cls._blockchain_classes[blockchain_type] = blockchain_class

    @classmethod
    def get_blockchain_types(cls) -> list[BlockchainType]:
        """Get all registered blockchain types."""
        return list(cls._blockchain_classes.keys())

    @classmethod
    def create(cls, chain_name: str) -> Blockchain:
        """Create or retrieve a blockchain instance from a registered configuration.

        This method implements the singleton pattern - if an instance for the given
        chain already exists, it will be returned instead of creating a new one.

        Args:
            chain_name: The name of the chain configuration to use

        Returns:
            A blockchain instance if the configuration exists

        Raises:
            ValueError: If configuration or blockchain class is not found
        """
        if chain_name in cls._blockchain_instances:
            return cls._blockchain_instances[chain_name]

        config = BlockchainRegistry.get(chain_name)
        if not config:
            raise ValueError(f"Blockchain configuration not found for {chain_name}")

        blockchain_class = cls._blockchain_classes.get(config.platform.type)
        if not blockchain_class:
            raise ValueError(f"Blockchain class not found for {chain_name}")

        instance = blockchain_class(configuration=config)
        cls._blockchain_instances[chain_name] = instance
        return instance

    @classmethod
    def create_from_config(cls, config: BlockchainConfiguration) -> Blockchain | None:
        """Create or retrieve a blockchain instance directly from a configuration object.

        This method also implements the singleton pattern using the platform identifier
        as the cache key.

        Args:
            config: The blockchain configuration to use

        Returns:
            The blockchain instance, or None if no blockchain class is found
        """
        identifier = config.platform.identifier

        if identifier in cls._blockchain_instances:
            return cls._blockchain_instances[identifier]

        blockchain_class = cls._blockchain_classes.get(config.platform.type)
        if not blockchain_class:
            return None

        instance = blockchain_class(configuration=config)
        cls._blockchain_instances[identifier] = instance
        return instance

    @classmethod
    def get_instance(cls, chain_name: str) -> Blockchain | None:
        """Get an existing blockchain instance from the cache."""
        return cls._blockchain_instances.get(chain_name)


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
