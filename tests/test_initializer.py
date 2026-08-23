"""
Unit tests for blockchainpype.initializer.

This module tests:
- Blockchain type normalization (SupportedBlockchainType aliases vs the
  underlying chain-specific enum members)
- BlockchainConfigurations discovery and contents (including the hardhat
  chain_id and ETHERSCAN_API_KEY handling)
- BlockchainsInitializer.configure() idempotency and type filtering (with
  pre-build filtering of unwanted configurations)
- WalletsInitializer wallet class registration
"""

import pytest

from blockchainpype.evm.blockchain.blockchain import (
    EthereumBlockchain,
    EthereumBlockchainType,
)
from blockchainpype.evm.blockchain.configuration import (
    EthereumBlockchainConfiguration,
)
from blockchainpype.factory import BlockchainFactory, WalletFactory
from blockchainpype.initializer import (
    HARDHAT_CHAIN_ID,
    BlockchainConfigurations,
    BlockchainsInitializer,
    SupportedBlockchainType,
    WalletsInitializer,
    normalize_blockchain_type,
)
from blockchainpype.solana.blockchain.blockchain import (
    SolanaBlockchain,
    SolanaBlockchainType,
)


@pytest.fixture(autouse=True)
def reset_factories():
    """Isolate every test from the class-level singleton state."""
    BlockchainFactory.reset()
    WalletFactory.reset()
    yield
    BlockchainFactory.reset()
    WalletFactory.reset()


class RecordingConfigurations(BlockchainConfigurations):
    """Counts configuration builds to verify pre-build filtering."""

    ethereum_calls = 0
    solana_calls = 0

    @classmethod
    def ethereum_configuration(cls) -> EthereumBlockchainConfiguration | None:
        cls.ethereum_calls += 1
        return super().ethereum_configuration()

    @classmethod
    def solana_configuration(cls):
        cls.solana_calls += 1
        return super().solana_configuration()


@pytest.fixture
def recording_configurations():
    RecordingConfigurations.ethereum_calls = 0
    RecordingConfigurations.solana_calls = 0
    return RecordingConfigurations


class TestNormalizeBlockchainType:
    def test_supported_alias_evm(self):
        assert (
            normalize_blockchain_type(SupportedBlockchainType.EVM)
            is EthereumBlockchainType
        )

    def test_supported_alias_solana(self):
        assert (
            normalize_blockchain_type(SupportedBlockchainType.SOLANA)
            is SolanaBlockchainType
        )

    def test_underlying_member_passthrough(self):
        assert (
            normalize_blockchain_type(EthereumBlockchainType) is EthereumBlockchainType
        )
        assert normalize_blockchain_type(SolanaBlockchainType) is SolanaBlockchainType


class TestBlockchainConfigurations:
    def test_discovery(self):
        methods = BlockchainConfigurations.configurations_methods()
        assert set(methods) == {
            "ethereum_configuration",
            "hardhat_configuration",
            "solana_configuration",
        }

    def test_get_configurations_names(self):
        configurations = BlockchainConfigurations.get_configurations()
        assert set(configurations.keys()) == {"ethereum", "hardhat", "solana"}
        assert all(config is not None for config in configurations.values())

    def test_hardhat_chain_id_is_31337(self):
        config = BlockchainConfigurations.hardhat_configuration()
        assert config is not None
        assert HARDHAT_CHAIN_ID == 31337
        assert config.platform.chain_id == 31337
        assert config.platform.local is True
        assert config.platform.testnet is True

    def test_ethereum_configuration_contents(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("ETHERSCAN_API_KEY", raising=False)
        config = BlockchainConfigurations.ethereum_configuration()
        assert config is not None
        assert config.platform.identifier == "ethereum"
        assert config.platform.type is EthereumBlockchainType
        assert config.platform.chain_id == 1
        assert config.explorer is not None
        assert config.explorer.api_key is None

    def test_ethereum_configuration_reads_etherscan_api_key(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("ETHERSCAN_API_KEY", "super-secret")
        config = BlockchainConfigurations.ethereum_configuration()
        assert config is not None
        assert config.explorer is not None
        assert config.explorer.api_key is not None
        assert config.explorer.api_key.get_secret_value() == "super-secret"

    def test_solana_configuration_contents(self):
        config = BlockchainConfigurations.solana_configuration()
        assert config is not None
        assert config.platform.identifier == "solana"
        assert config.platform.type is SolanaBlockchainType

    def test_get_configurations_filtered_by_type(self):
        configurations = BlockchainConfigurations.get_configurations(
            blockchain_types=[EthereumBlockchainType]
        )
        assert set(configurations.keys()) == {"ethereum", "hardhat"}


class TestBlockchainsInitializer:
    def test_configure_registers_all_blockchains(self):
        BlockchainsInitializer.configure()

        ethereum = BlockchainFactory.get_by_identifier("ethereum")
        hardhat = BlockchainFactory.get_by_identifier("hardhat")
        solana = BlockchainFactory.get_by_identifier("solana")

        assert isinstance(ethereum, EthereumBlockchain)
        assert isinstance(hardhat, EthereumBlockchain)
        assert isinstance(solana, SolanaBlockchain)

    def test_configure_is_idempotent(self):
        BlockchainsInitializer.configure()
        first = BlockchainFactory.get_by_identifier("ethereum")

        # A second call must not raise and must keep the registered state.
        BlockchainsInitializer.configure()

        assert BlockchainFactory.get_by_identifier("ethereum") is first
        assert len(BlockchainFactory.list_configurations()) == 3

    def test_configure_filters_with_supported_alias(self):
        BlockchainsInitializer.configure(blockchain_types=[SupportedBlockchainType.EVM])

        assert isinstance(
            BlockchainFactory.get_by_identifier("ethereum"), EthereumBlockchain
        )
        assert isinstance(
            BlockchainFactory.get_by_identifier("hardhat"), EthereumBlockchain
        )
        with pytest.raises(ValueError, match="No operator class found"):
            BlockchainFactory.get_by_identifier("solana")

    def test_configure_filters_with_underlying_member(self):
        BlockchainsInitializer.configure(blockchain_types=[EthereumBlockchainType])

        assert isinstance(
            BlockchainFactory.get_by_identifier("ethereum"), EthereumBlockchain
        )
        with pytest.raises(ValueError, match="No operator class found"):
            BlockchainFactory.get_by_identifier("solana")

    def test_both_spellings_register_the_same_platforms(self):
        BlockchainsInitializer.configure(blockchain_types=[SupportedBlockchainType.EVM])
        alias_platforms = set(BlockchainFactory.list_configurations().keys())

        BlockchainFactory.reset()
        BlockchainsInitializer.configure(blockchain_types=[EthereumBlockchainType])
        member_platforms = set(BlockchainFactory.list_configurations().keys())

        assert alias_platforms == member_platforms
        assert {platform.identifier for platform in alias_platforms} == {
            "ethereum",
            "hardhat",
        }

    def test_configure_filters_solana_with_alias(self):
        BlockchainsInitializer.configure(
            blockchain_types=[SupportedBlockchainType.SOLANA]
        )

        assert isinstance(
            BlockchainFactory.get_by_identifier("solana"), SolanaBlockchain
        )
        with pytest.raises(ValueError, match="No operator class found"):
            BlockchainFactory.get_by_identifier("ethereum")

    def test_filtered_configurations_are_not_built(self, recording_configurations):
        BlockchainsInitializer.configure(
            blockchain_types=[SupportedBlockchainType.EVM],
            configurations=recording_configurations,
        )

        assert recording_configurations.ethereum_calls == 1
        assert recording_configurations.solana_calls == 0

    def test_unfiltered_configure_builds_all(self, recording_configurations):
        BlockchainsInitializer.configure(configurations=recording_configurations)

        assert recording_configurations.ethereum_calls == 1
        assert recording_configurations.solana_calls == 1


class TestWalletsInitializer:
    def test_configure_registers_wallet_classes(self):
        from blockchainpype.evm.wallet.wallet import EthereumWallet
        from blockchainpype.solana.wallet.wallet import SolanaWallet

        WalletsInitializer.configure()

        assert WalletFactory.get_wallet_class(EthereumBlockchainType) is EthereumWallet
        assert WalletFactory.get_wallet_class(SolanaBlockchainType) is SolanaWallet

    def test_configure_is_idempotent(self):
        from blockchainpype.evm.wallet.wallet import EthereumWallet

        WalletsInitializer.configure()
        WalletsInitializer.configure()

        assert WalletFactory.get_wallet_class(EthereumBlockchainType) is EthereumWallet
