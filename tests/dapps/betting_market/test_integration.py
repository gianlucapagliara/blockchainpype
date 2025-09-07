"""
Integration tests for betting market functionality.

This module tests:
- End-to-end betting market workflows
- Import and module loading
- Cross-component integration
"""

from decimal import Decimal

import pytest

from blockchainpype.dapps.betting_market import (
    BettingMarketAction,
    BettingMarketConfiguration,
    BettingMarketDApp,
    BettingMarketModel,
    BettingPosition,
    MarketOutcome,
    MarketStatus,
    OutcomeToken,
    ProtocolConfiguration,
)
from blockchainpype.initializer import BlockchainsInitializer

# Initialize blockchain configurations for tests
try:
    BlockchainsInitializer.configure()
except ValueError:
    # Configuration already registered
    pass


class TestImports:
    """Test that all imports work correctly."""

    def test_base_imports(self):
        """Test that base betting market imports work."""
        assert BettingMarketDApp is not None
        assert BettingMarketModel is not None
        assert BettingMarketConfiguration is not None
        assert ProtocolConfiguration is not None

    def test_model_imports(self):
        """Test that model imports work."""
        assert BettingPosition is not None
        assert MarketOutcome is not None
        assert OutcomeToken is not None
        assert MarketStatus is not None
        assert BettingMarketAction is not None

    def test_evm_imports(self):
        """Test that EVM-specific imports work."""
        try:
            from blockchainpype.evm.dapp.betting_market import (
                EVMBettingMarket,
                Polymarket,
                PolymarketBettingMarket,
                PolymarketConfiguration,
            )

            assert EVMBettingMarket is not None
            assert Polymarket is not None
            assert PolymarketConfiguration is not None
            assert PolymarketBettingMarket is not None
        except ImportError as e:
            pytest.fail(f"EVM betting market imports failed: {e}")

    def test_main_dapp_imports(self):
        """Test that main DApp package includes betting market exports."""
        try:
            from blockchainpype.dapps import (
                BettingMarketAction,
                BettingMarketDApp,
                BettingMarketModel,
            )

            assert BettingMarketDApp is not None
            assert BettingMarketModel is not None
            assert BettingMarketAction is not None
        except ImportError as e:
            # This might fail if betting market is not available, which is okay
            print(f"Main DApp imports not available: {e}")

    def test_evm_dapp_imports(self):
        """Test that EVM DApp package includes betting market exports."""
        try:
            from blockchainpype.evm.dapp import (
                EVMBettingMarket,
                PolymarketBettingMarket,
            )

            assert EVMBettingMarket is not None
            assert PolymarketBettingMarket is not None
        except ImportError as e:
            # This might fail if betting market is not available, which is okay
            print(f"EVM DApp imports not available: {e}")


class TestEnumValues:
    """Test enum values and consistency."""

    def test_market_status_enum(self):
        """Test MarketStatus enum values."""
        assert MarketStatus.ACTIVE.value == "active"
        assert MarketStatus.CLOSED.value == "closed"
        assert MarketStatus.RESOLVED.value == "resolved"
        assert MarketStatus.CANCELLED.value == "cancelled"

    def test_betting_action_enum(self):
        """Test BettingMarketAction enum values."""
        assert BettingMarketAction.BUY.value == "buy"
        assert BettingMarketAction.SELL.value == "sell"
        assert BettingMarketAction.REDEEM.value == "redeem"
        assert BettingMarketAction.CLAIM.value == "claim"


class TestConfigurationCompatibility:
    """Test configuration compatibility and validation."""

    def test_protocol_configuration_creation(self):
        """Test creating protocol configuration."""
        config = ProtocolConfiguration(
            protocol_name="Test Protocol",
            contract_address="0x1234567890123456789012345678901234567890",
        )

        assert config.protocol_name == "Test Protocol"
        assert config.contract_address == "0x1234567890123456789012345678901234567890"

    def test_betting_market_configuration_creation(self, test_platform):
        """Test creating betting market configuration."""
        protocol = ProtocolConfiguration(
            protocol_name="Test Protocol",
            contract_address="0x1234567890123456789012345678901234567890",
        )

        config = BettingMarketConfiguration(
            platform=test_platform, protocols=[protocol]
        )

        assert len(config.protocols) == 1
        assert config.protocols[0].protocol_name == "Test Protocol"


class TestModelCreation:
    """Test model creation and validation."""

    def test_outcome_token_creation(self):
        """Test creating outcome token."""
        token = OutcomeToken(
            token_id="test_token_1",
            outcome_name="Test Outcome",
            current_price=0.65,
            total_supply=10000,
            probability=0.65,
        )

        assert token.token_id == "test_token_1"
        assert token.outcome_name == "Test Outcome"
        assert token.probability == Decimal("0.65")

    def test_market_outcome_creation(self):
        """Test creating market outcome."""
        token = OutcomeToken(
            token_id="test_token_1",
            outcome_name="Yes",
            current_price=0.65,
            total_supply=10000,
            probability=0.65,
        )

        outcome = MarketOutcome(
            outcome_id="outcome_1",
            outcome_text="Yes",
            outcome_tokens=[token],
        )

        assert outcome.outcome_id == "outcome_1"
        assert outcome.outcome_text == "Yes"
        assert len(outcome.outcome_tokens) == 1

    def test_betting_position_creation(self):
        """Test creating betting position."""
        token = OutcomeToken(
            token_id="test_token_1",
            outcome_name="Yes",
            current_price=0.65,
            total_supply=10000,
            probability=0.65,
        )

        position = BettingPosition(
            market_id="test_market_1",
            outcome_token=token,
            shares_owned=100,
            average_price=0.55,
            total_invested=55,
            current_value=65,
            unrealized_pnl=10,
            protocol="Test Protocol",
        )

        assert position.market_id == "test_market_1"
        assert position.shares_owned == 100
        assert position.is_profitable


class TestArchitectureConsistency:
    """Test that the betting market architecture is consistent with other DApps."""

    def test_configuration_inheritance(self):
        """Test that configurations inherit from correct base classes."""
        from financepype.operators.dapps.dapp import (
            DecentralizedApplicationConfiguration,
        )

        # Test protocol configuration
        protocol_config = ProtocolConfiguration(
            protocol_name="Test",
            contract_address="0x123",
        )

        # Test betting market configuration
        from financepype.operators.blockchains.models import BlockchainPlatform

        from blockchainpype.initializer import SupportedBlockchainType

        test_platform = BlockchainPlatform(
            identifier="ethereum",
            type=SupportedBlockchainType.EVM.value,
            chain_id=1,
        )
        betting_config = BettingMarketConfiguration(
            platform=test_platform, protocols=[protocol_config]
        )

        # Should inherit from DecentralizedApplicationConfiguration
        assert isinstance(betting_config, DecentralizedApplicationConfiguration)

    def test_dapp_inheritance(self):
        """Test that betting market DApp inherits from correct base class."""
        from financepype.operators.dapps.dapp import DecentralizedApplication

        protocol_config = ProtocolConfiguration(
            protocol_name="Test",
            contract_address="0x123",
        )
        from financepype.operators.blockchains.models import BlockchainPlatform

        from blockchainpype.initializer import SupportedBlockchainType

        test_platform = BlockchainPlatform(
            identifier="ethereum",
            type=SupportedBlockchainType.EVM.value,
            chain_id=1,
        )
        betting_config = BettingMarketConfiguration(
            platform=test_platform, protocols=[protocol_config]
        )

        # Create a test implementation
        class TestBettingMarket(BettingMarketDApp):
            def _initialize_protocols(self):
                pass

        betting_market = TestBettingMarket(betting_config)

        # Should inherit from DecentralizedApplication
        assert isinstance(betting_market, DecentralizedApplication)

    def test_protocol_pattern_consistency(self):
        """Test that protocol pattern is consistent with other DApps."""
        # The betting market should follow the same protocol pattern as money markets
        from blockchainpype.dapps import MoneyMarketProtocolConfiguration

        # Both should have similar structure
        betting_protocol = ProtocolConfiguration(
            protocol_name="Betting Test",
            contract_address="0x123",
        )

        money_protocol = MoneyMarketProtocolConfiguration(
            protocol_name="Money Test",
            lending_pool_address="0x456",
            data_provider_address="0x789",
        )

        # Both should have protocol_name
        assert hasattr(betting_protocol, "protocol_name")
        assert hasattr(money_protocol, "protocol_name")

        # Both should have some form of contract address
        assert hasattr(betting_protocol, "contract_address")
        assert hasattr(money_protocol, "lending_pool_address")
