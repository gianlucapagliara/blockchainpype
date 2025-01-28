from financepype.operators.blockchains.blockchain import Blockchain
from financepype.operators.blockchains.models import BlockchainConfiguration
from financepype.platforms.blockchain import BlockchainType


class BlockchainRegistry:
    """A registry for blockchain configurations.

    This class provides a flexible way to register and retrieve blockchain configurations.
    It supports dynamic registration of new configurations and lazy loading of configurations.
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
    """

    _blockchain_classes: dict[BlockchainType, type[Blockchain]] = {}

    # Cache to store blockchain instances
    _blockchain_instances: dict[str, Blockchain] = {}

    @classmethod
    def register_blockchain_class(
        cls, blockchain_type: BlockchainType, blockchain_class: type[Blockchain]
    ) -> None:
        """Register a new blockchain class for a specific blockchain type."""
        cls._blockchain_classes[blockchain_type] = blockchain_class

    @classmethod
    def create(cls, chain_name: str) -> Blockchain:
        """Create or retrieve a blockchain instance from a registered configuration.

        This method implements the singleton pattern - if an instance for the given
        chain already exists, it will be returned instead of creating a new one.

        Args:
            chain_name: The name of the chain configuration to use

        Returns:
            A blockchain instance if the configuration exists
        """
        # Check if we already have an instance for this chain
        if chain_name in cls._blockchain_instances:
            return cls._blockchain_instances[chain_name]

        config = BlockchainRegistry.get(chain_name)
        if not config:
            raise ValueError(f"Blockchain configuration not found for {chain_name}")

        blockchain_class = cls._blockchain_classes.get(config.platform.type)
        if not blockchain_class:
            raise ValueError(f"Blockchain class not found for {chain_name}")

        # Create new instance and cache it
        instance = blockchain_class(configuration=config)
        cls._blockchain_instances[chain_name] = instance
        return instance

    @classmethod
    def create_from_config(cls, config: BlockchainConfiguration) -> Blockchain | None:
        """Create or retrieve a blockchain instance directly from a configuration object.

        This method also implements the singleton pattern using the platform identifier
        as the cache key.
        """
        identifier = config.platform.identifier

        # Check if we already have an instance for this chain
        if identifier in cls._blockchain_instances:
            return cls._blockchain_instances[identifier]

        blockchain_class = cls._blockchain_classes.get(config.platform.type)
        if not blockchain_class:
            return None

        # Create new instance and cache it
        instance = blockchain_class(configuration=config)
        cls._blockchain_instances[identifier] = instance
        return instance

    @classmethod
    def get_instance(cls, chain_name: str) -> Blockchain | None:
        """Get an existing blockchain instance from the cache.

        Args:
            chain_name: The name of the chain

        Returns:
            The cached blockchain instance if it exists, None otherwise
        """
        return cls._blockchain_instances.get(chain_name)
