"""
Unit tests for blockchainpype.factory.

This module tests:
- BlockchainFactory class/configuration registration and typed getters
- BlockchainFactory reset behavior (including per-type class registrations)
- WalletRegistry register/unregister/get/list/reset
- WalletFactory creation paths, singleton caching, config-mismatch detection,
  and reset
"""

from datetime import timedelta

import pytest
from financepype.owners.wallet import (
    BlockchainWallet,
    BlockchainWalletConfiguration,
    BlockchainWalletIdentifier,
)
from financepype.platforms.blockchain import BlockchainPlatform

from blockchainpype.evm.blockchain.blockchain import (
    EthereumBlockchain,
    EthereumBlockchainType,
)
from blockchainpype.evm.blockchain.identifier import EthereumAddress
from blockchainpype.factory import BlockchainFactory, WalletFactory, WalletRegistry
from blockchainpype.initializer import (
    BlockchainsInitializer,
    SupportedBlockchainType,
)
from blockchainpype.solana.blockchain.blockchain import (
    SolanaBlockchain,
    SolanaBlockchainType,
)

TEST_ADDRESS = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"


@pytest.fixture(autouse=True)
def reset_factories():
    """Isolate every test from the class-level singleton state."""
    BlockchainFactory.reset()
    WalletRegistry.reset()
    WalletFactory.reset()
    yield
    BlockchainFactory.reset()
    WalletRegistry.reset()
    WalletFactory.reset()


class DummyWallet(BlockchainWallet):
    """Concrete wallet used to exercise the factory without RPC dependencies."""


def make_wallet_config(
    name: str = "main", tx_wait_minutes: int = 2
) -> BlockchainWalletConfiguration:
    platform = BlockchainPlatform(
        identifier="dummychain",
        type=EthereumBlockchainType,
        chain_id=1,
    )
    identifier = BlockchainWalletIdentifier(
        name=name,
        platform=platform,
        address=EthereumAddress.from_string(TEST_ADDRESS),
    )
    return BlockchainWalletConfiguration(
        identifier=identifier,
        tracked_assets=set(),
        default_tx_wait=timedelta(minutes=tx_wait_minutes),
    )


class TestBlockchainFactory:
    def test_register_blockchain_class_for_type(self):
        BlockchainFactory.register_blockchain_class_for_type(
            EthereumBlockchain, EthereumBlockchainType
        )

        assert BlockchainFactory.get_blockchain_types() == [EthereumBlockchainType]

    def test_reset_clears_blockchain_classes(self):
        BlockchainFactory.register_blockchain_class_for_type(
            EthereumBlockchain, EthereumBlockchainType
        )

        BlockchainFactory.reset()

        assert BlockchainFactory.get_blockchain_types() == []
        assert BlockchainFactory.list_configurations() == {}

    def test_register_configuration_auto_registers_operator_class(self):
        BlockchainsInitializer.configure(blockchain_types=[SupportedBlockchainType.EVM])

        blockchain = BlockchainFactory.get_by_identifier("ethereum")
        assert isinstance(blockchain, EthereumBlockchain)

    def test_singleton_instances(self):
        BlockchainsInitializer.configure(blockchain_types=[SupportedBlockchainType.EVM])

        first = BlockchainFactory.get_by_identifier("ethereum")
        second = BlockchainFactory.get_by_identifier("ethereum")
        assert first is second

    def test_get_by_identifier_unknown_raises(self):
        with pytest.raises(ValueError, match="No operator class found"):
            BlockchainFactory.get_by_identifier("unknown-chain")

    def test_get_evm_blockchain_by_identifier(self):
        BlockchainsInitializer.configure()

        blockchain = BlockchainFactory.get_evm_blockchain_by_identifier("ethereum")
        assert isinstance(blockchain, EthereumBlockchain)
        assert blockchain.platform.identifier == "ethereum"

    def test_get_solana_blockchain_by_identifier(self):
        BlockchainsInitializer.configure()

        blockchain = BlockchainFactory.get_solana_blockchain_by_identifier("solana")
        assert isinstance(blockchain, SolanaBlockchain)
        assert blockchain.platform.identifier == "solana"

    def test_get_evm_blockchain_with_solana_identifier_raises_type_error(self):
        BlockchainsInitializer.configure()

        with pytest.raises(
            TypeError,
            match="'solana' is not an EVM blockchain \\(got SolanaBlockchain\\)",
        ):
            BlockchainFactory.get_evm_blockchain_by_identifier("solana")

    def test_get_solana_blockchain_with_evm_identifier_raises_type_error(self):
        BlockchainsInitializer.configure()

        with pytest.raises(
            TypeError,
            match="'ethereum' is not a Solana blockchain \\(got EthereumBlockchain\\)",
        ):
            BlockchainFactory.get_solana_blockchain_by_identifier("ethereum")


class TestWalletRegistry:
    def test_register_and_get(self):
        config = make_wallet_config()

        WalletRegistry.register(config)

        assert WalletRegistry.get("dummychain:main") is config

    def test_get_missing_returns_none(self):
        assert WalletRegistry.get("dummychain:missing") is None

    def test_register_duplicate_raises(self):
        config = make_wallet_config()
        WalletRegistry.register(config)

        with pytest.raises(
            ValueError, match="Wallet configuration for dummychain:main already exists"
        ):
            WalletRegistry.register(make_wallet_config())

    def test_list_returns_copy(self):
        config = make_wallet_config()
        WalletRegistry.register(config)

        listed = WalletRegistry.list()
        assert listed == {"dummychain:main": config}

        listed.clear()
        assert WalletRegistry.get("dummychain:main") is config

    def test_unregister(self):
        WalletRegistry.register(make_wallet_config())

        WalletRegistry.unregister("dummychain:main")

        assert WalletRegistry.get("dummychain:main") is None

    def test_unregister_missing_raises(self):
        with pytest.raises(
            ValueError, match="Wallet configuration not found for dummychain:main"
        ):
            WalletRegistry.unregister("dummychain:main")

    def test_reset(self):
        WalletRegistry.register(make_wallet_config())

        WalletRegistry.reset()

        assert WalletRegistry.list() == {}


class TestWalletFactory:
    def test_create_without_configuration_raises(self):
        with pytest.raises(
            ValueError, match="Wallet configuration not found for dummychain:main"
        ):
            WalletFactory.create("dummychain:main")

    def test_create_without_wallet_class_raises(self):
        WalletRegistry.register(make_wallet_config())

        with pytest.raises(ValueError, match="Wallet class not found"):
            WalletFactory.create("dummychain:main")

    def test_create_from_registered_configuration(self):
        config = make_wallet_config()
        WalletRegistry.register(config)
        WalletFactory.register_wallet_class(EthereumBlockchainType, DummyWallet)

        wallet = WalletFactory.create("dummychain:main")

        assert isinstance(wallet, DummyWallet)
        assert wallet.configuration is config

    def test_create_is_singleton(self):
        config = make_wallet_config()
        WalletRegistry.register(config)
        WalletFactory.register_wallet_class(EthereumBlockchainType, DummyWallet)

        first = WalletFactory.create("dummychain:main")
        second = WalletFactory.create("dummychain:main")
        assert first is second

    def test_create_accepts_identifier_object(self):
        config = make_wallet_config()
        WalletRegistry.register(config)
        WalletFactory.register_wallet_class(EthereumBlockchainType, DummyWallet)

        by_string = WalletFactory.create("dummychain:main")
        by_object = WalletFactory.create(config.identifier)
        assert by_string is by_object

    def test_create_from_config(self):
        WalletFactory.register_wallet_class(EthereumBlockchainType, DummyWallet)
        config = make_wallet_config()

        wallet = WalletFactory.create_from_config(config)

        assert isinstance(wallet, DummyWallet)
        assert WalletFactory.get_instance("dummychain:main") is wallet

    def test_create_from_config_returns_cached_for_equal_config(self):
        WalletFactory.register_wallet_class(EthereumBlockchainType, DummyWallet)

        first = WalletFactory.create_from_config(make_wallet_config())
        second = WalletFactory.create_from_config(make_wallet_config())
        assert first is second

    def test_create_from_config_with_different_config_raises(self):
        WalletFactory.register_wallet_class(EthereumBlockchainType, DummyWallet)
        WalletFactory.create_from_config(make_wallet_config(tx_wait_minutes=2))

        with pytest.raises(
            ValueError, match="already exists with a different configuration"
        ):
            WalletFactory.create_from_config(make_wallet_config(tx_wait_minutes=5))

    def test_create_from_config_without_wallet_class_raises(self):
        with pytest.raises(ValueError, match="Wallet class not found"):
            WalletFactory.create_from_config(make_wallet_config())

    def test_create_and_create_from_config_share_cache(self):
        config = make_wallet_config()
        WalletRegistry.register(config)
        WalletFactory.register_wallet_class(EthereumBlockchainType, DummyWallet)

        created = WalletFactory.create("dummychain:main")
        from_config = WalletFactory.create_from_config(config)
        assert created is from_config

    def test_get_instance_missing_returns_none(self):
        assert WalletFactory.get_instance("dummychain:missing") is None

    def test_get_wallet_class(self):
        assert WalletFactory.get_wallet_class(EthereumBlockchainType) is None

        WalletFactory.register_wallet_class(EthereumBlockchainType, DummyWallet)
        assert WalletFactory.get_wallet_class(EthereumBlockchainType) is DummyWallet
        assert WalletFactory.get_wallet_class(SolanaBlockchainType) is None

    def test_reset_clears_classes_and_instances(self):
        config = make_wallet_config()
        WalletRegistry.register(config)
        WalletFactory.register_wallet_class(EthereumBlockchainType, DummyWallet)
        WalletFactory.create("dummychain:main")

        WalletFactory.reset()

        assert WalletFactory.get_wallet_class(EthereumBlockchainType) is None
        assert WalletFactory.get_instance("dummychain:main") is None
