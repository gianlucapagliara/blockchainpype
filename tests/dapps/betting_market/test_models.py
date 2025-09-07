"""
Unit tests for betting market models.

This module tests:
- Model validation and constraints
- Data consistency checks
- Property calculations
- Edge cases and error conditions
"""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from blockchainpype.dapps.betting_market import (
    BettingMarketAction,
    BettingMarketConfiguration,
)
from blockchainpype.dapps.betting_market import BettingMarketModel as BettingMarket
from blockchainpype.dapps.betting_market import (
    BettingPosition,
    MarketOutcome,
    MarketStatus,
    OutcomeToken,
    ProtocolConfiguration,
)

# Import the BlockchainAsset from betting market models
from blockchainpype.dapps.betting_market.models import BlockchainAsset


class MockBlockchainAsset(BlockchainAsset):
    """Mock blockchain asset for testing."""

    def __init__(self, symbol: str, decimals: int):
        super().__init__()
        self.symbol = symbol
        self.decimals = decimals


@pytest.fixture
def usdc_asset():
    """Create a mock USDC asset."""
    return MockBlockchainAsset("USDC", 6)


@pytest.fixture
def sample_outcome_token():
    """Create a sample outcome token."""
    return OutcomeToken(
        token_id="token_yes_1",
        outcome_name="Yes",
        current_price=Decimal("0.65"),
        total_supply=Decimal("10000"),
        probability=Decimal("0.65"),
    )


@pytest.fixture
def sample_market_outcome(sample_outcome_token):
    """Create a sample market outcome."""
    return MarketOutcome(
        outcome_id="outcome_1",
        outcome_text="Yes",
        outcome_tokens=[sample_outcome_token],
    )


@pytest.fixture
def sample_betting_market(usdc_asset, sample_market_outcome):
    """Create a sample betting market."""
    no_token = OutcomeToken(
        token_id="token_no_1",
        outcome_name="No",
        current_price=Decimal("0.35"),
        total_supply=Decimal("5000"),
        probability=Decimal("0.35"),
    )

    no_outcome = MarketOutcome(
        outcome_id="outcome_2",
        outcome_text="No",
        outcome_tokens=[no_token],
    )

    return BettingMarket(
        market_id="market_123",
        title="Will Bitcoin reach $100k by end of 2024?",
        description="Market resolves to Yes if Bitcoin reaches $100,000 USD by Dec 31, 2024",
        category="cryptocurrency",
        status=MarketStatus.ACTIVE,
        collateral_asset=usdc_asset,
        outcomes=[sample_market_outcome, no_outcome],
        total_volume=Decimal("50000"),
        total_liquidity=Decimal("25000"),
        creation_date=datetime.now(),
        end_date=datetime.now() + timedelta(days=30),
        protocol="Polymarket",
    )


class TestOutcomeToken:
    """Test OutcomeToken model."""

    def test_valid_outcome_token(self, sample_outcome_token):
        """Test creating a valid outcome token."""
        assert sample_outcome_token.token_id == "token_yes_1"
        assert sample_outcome_token.outcome_name == "Yes"
        assert sample_outcome_token.current_price == Decimal("0.65")
        assert sample_outcome_token.probability == Decimal("0.65")

    def test_invalid_probability_too_high(self):
        """Test that probability > 1 raises validation error."""
        with pytest.raises(
            ValidationError, match="Probability must be between 0 and 1"
        ):
            OutcomeToken(
                token_id="token_1",
                outcome_name="Yes",
                current_price=Decimal("1.5"),
                total_supply=Decimal("1000"),
                probability=Decimal("1.5"),
            )

    def test_invalid_probability_negative(self):
        """Test that negative probability raises validation error."""
        with pytest.raises(
            ValidationError, match="Probability must be between 0 and 1"
        ):
            OutcomeToken(
                token_id="token_1",
                outcome_name="Yes",
                current_price=Decimal("0.5"),
                total_supply=Decimal("1000"),
                probability=Decimal("-0.1"),
            )

    def test_boundary_probabilities(self):
        """Test boundary probability values (0 and 1)."""
        # Test probability = 0
        token_zero = OutcomeToken(
            token_id="token_0",
            outcome_name="Zero",
            current_price=Decimal("0.01"),
            total_supply=Decimal("1000"),
            probability=Decimal("0"),
        )
        assert token_zero.probability == Decimal("0")

        # Test probability = 1
        token_one = OutcomeToken(
            token_id="token_1",
            outcome_name="One",
            current_price=Decimal("0.99"),
            total_supply=Decimal("1000"),
            probability=Decimal("1"),
        )
        assert token_one.probability == Decimal("1")


class TestMarketOutcome:
    """Test MarketOutcome model."""

    def test_valid_market_outcome(self, sample_market_outcome):
        """Test creating a valid market outcome."""
        assert sample_market_outcome.outcome_id == "outcome_1"
        assert sample_market_outcome.outcome_text == "Yes"
        assert len(sample_market_outcome.outcome_tokens) == 1
        assert not sample_market_outcome.is_winning_outcome

    def test_total_probability_calculation(self):
        """Test total probability calculation across outcome tokens."""
        token1 = OutcomeToken(
            token_id="token_1",
            outcome_name="Token 1",
            current_price=Decimal("0.3"),
            total_supply=Decimal("1000"),
            probability=Decimal("0.3"),
        )
        token2 = OutcomeToken(
            token_id="token_2",
            outcome_name="Token 2",
            current_price=Decimal("0.4"),
            total_supply=Decimal("1000"),
            probability=Decimal("0.4"),
        )

        outcome = MarketOutcome(
            outcome_id="outcome_multi",
            outcome_text="Multiple tokens",
            outcome_tokens=[token1, token2],
        )

        assert outcome.total_probability == Decimal("0.7")


class TestBettingMarket:
    """Test BettingMarket model."""

    def test_valid_betting_market(self, sample_betting_market):
        """Test creating a valid betting market."""
        assert sample_betting_market.market_id == "market_123"
        assert sample_betting_market.status == MarketStatus.ACTIVE
        assert sample_betting_market.is_active
        assert not sample_betting_market.is_resolved
        assert len(sample_betting_market.outcomes) == 2

    def test_market_status_properties(self, sample_betting_market):
        """Test market status properties."""
        # Test active market
        assert sample_betting_market.is_active
        assert not sample_betting_market.is_resolved

        # Test resolved market
        sample_betting_market.status = MarketStatus.RESOLVED
        sample_betting_market.resolved_outcome_id = "outcome_1"

        assert not sample_betting_market.is_active
        assert sample_betting_market.is_resolved

    def test_winning_outcome_property(self, sample_betting_market):
        """Test winning outcome property."""
        # No winning outcome initially
        assert sample_betting_market.winning_outcome is None

        # Set resolved outcome
        sample_betting_market.status = MarketStatus.RESOLVED
        sample_betting_market.resolved_outcome_id = "outcome_1"

        winning_outcome = sample_betting_market.winning_outcome
        assert winning_outcome is not None
        assert winning_outcome.outcome_id == "outcome_1"

    def test_invalid_end_date_before_creation(self, usdc_asset, sample_market_outcome):
        """Test validation error when end date is before creation date."""
        creation_date = datetime.now()
        end_date = creation_date - timedelta(days=1)

        with pytest.raises(
            ValidationError, match="End date cannot be before creation date"
        ):
            BettingMarket(
                market_id="invalid_market",
                title="Invalid Market",
                description="Test market with invalid dates",
                category="test",
                status=MarketStatus.ACTIVE,
                collateral_asset=usdc_asset,
                outcomes=[sample_market_outcome],
                total_volume=Decimal("1000"),
                total_liquidity=Decimal("500"),
                creation_date=creation_date,
                end_date=end_date,
                protocol="Test",
            )

    def test_resolved_market_without_outcome_id(self, sample_betting_market):
        """Test validation error for resolved market without outcome ID."""
        with pytest.raises(
            ValidationError, match="Resolved markets must have a resolved outcome ID"
        ):
            sample_betting_market.status = MarketStatus.RESOLVED
            sample_betting_market.resolved_outcome_id = None
            # Trigger validation by creating a new instance
            BettingMarket.model_validate(sample_betting_market.model_dump())


class TestBettingPosition:
    """Test BettingPosition model."""

    def test_valid_betting_position(self, sample_outcome_token):
        """Test creating a valid betting position."""
        position = BettingPosition(
            market_id="market_123",
            outcome_token=sample_outcome_token,
            shares_owned=Decimal("100"),
            average_price=Decimal("0.55"),
            total_invested=Decimal("55"),
            current_value=Decimal("65"),
            unrealized_pnl=Decimal("10"),
            protocol="Polymarket",
        )

        assert position.market_id == "market_123"
        assert position.shares_owned == Decimal("100")
        assert abs(position.roi_percentage - Decimal("18.18")) < Decimal(
            "0.01"
        )  # 10/55 * 100 ≈ 18.18%
        assert position.is_profitable

    def test_roi_calculation(self, sample_outcome_token):
        """Test ROI percentage calculation."""
        # Profitable position
        profitable_position = BettingPosition(
            market_id="market_123",
            outcome_token=sample_outcome_token,
            shares_owned=Decimal("100"),
            average_price=Decimal("0.50"),
            total_invested=Decimal("50"),
            current_value=Decimal("75"),
            unrealized_pnl=Decimal("25"),
            protocol="Test",
        )
        assert profitable_position.roi_percentage == Decimal("50")  # 25/50 * 100
        assert profitable_position.is_profitable

        # Loss position
        loss_position = BettingPosition(
            market_id="market_123",
            outcome_token=sample_outcome_token,
            shares_owned=Decimal("100"),
            average_price=Decimal("0.70"),
            total_invested=Decimal("70"),
            current_value=Decimal("50"),
            unrealized_pnl=Decimal("-20"),
            protocol="Test",
        )
        assert abs(loss_position.roi_percentage - Decimal("-28.57")) < Decimal(
            "0.01"
        )  # -20/70 * 100 ≈ -28.57%
        assert not loss_position.is_profitable

    def test_zero_investment_roi(self, sample_outcome_token):
        """Test ROI calculation with zero investment."""
        position = BettingPosition(
            market_id="market_123",
            outcome_token=sample_outcome_token,
            shares_owned=Decimal("0"),
            average_price=Decimal("0"),
            total_invested=Decimal("0"),
            current_value=Decimal("0"),
            unrealized_pnl=Decimal("0"),
            protocol="Test",
        )
        assert position.roi_percentage == Decimal("0")
        assert not position.is_profitable


class TestProtocolConfiguration:
    """Test ProtocolConfiguration model."""

    def test_valid_protocol_configuration(self):
        """Test creating a valid protocol configuration."""
        config = ProtocolConfiguration(
            protocol_name="Test Protocol",
            contract_address="0x1234567890123456789012345678901234567890",
            conditional_tokens_address="0xabcdefabcdefabcdefabcdefabcdefabcdefabcd",
            collateral_token_address="0xfedcbafedcbafedcbafedcbafedcbafedcbafedcba",
            fee_rate=Decimal("0.025"),
        )

        assert config.protocol_name == "Test Protocol"
        assert config.fee_rate == Decimal("0.025")

    def test_default_fee_rate(self):
        """Test default fee rate."""
        config = ProtocolConfiguration(
            protocol_name="Test Protocol",
            contract_address="0x1234567890123456789012345678901234567890",
        )
        assert config.fee_rate == Decimal("0.02")  # 2% default


class TestBettingMarketConfiguration:
    """Test BettingMarketConfiguration model."""

    def test_valid_configuration(self, test_platform):
        """Test creating a valid betting market configuration."""
        protocol = ProtocolConfiguration(
            protocol_name="Test Protocol",
            contract_address="0x1234567890123456789012345678901234567890",
        )

        config = BettingMarketConfiguration(
            platform=test_platform,
            protocols=[protocol],
            default_slippage_tolerance=Decimal("0.015"),
            max_gas_price_gwei=75,
        )

        assert len(config.protocols) == 1
        assert config.default_slippage_tolerance == Decimal("0.015")
        assert config.max_gas_price_gwei == 75

    def test_default_values(self, test_platform):
        """Test default configuration values."""
        protocol = ProtocolConfiguration(
            protocol_name="Test Protocol",
            contract_address="0x1234567890123456789012345678901234567890",
        )

        config = BettingMarketConfiguration(
            platform=test_platform, protocols=[protocol]
        )

        assert config.default_slippage_tolerance == Decimal("0.01")  # 1% default
        assert config.max_gas_price_gwei == 50  # 50 gwei default


class TestEnums:
    """Test enum values and behavior."""

    def test_market_status_values(self):
        """Test MarketStatus enum values."""
        assert MarketStatus.ACTIVE == "active"
        assert MarketStatus.CLOSED == "closed"
        assert MarketStatus.RESOLVED == "resolved"
        assert MarketStatus.CANCELLED == "cancelled"

    def test_betting_market_action_values(self):
        """Test BettingMarketAction enum values."""
        assert BettingMarketAction.BUY == "buy"
        assert BettingMarketAction.SELL == "sell"
        assert BettingMarketAction.REDEEM == "redeem"
        assert BettingMarketAction.CLAIM == "claim"
