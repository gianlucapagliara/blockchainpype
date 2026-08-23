"""
Unit tests for betting market models.

This module tests:
- Model validation and constraints with REAL financepype assets
- Data consistency checks (resolution, dates, outcome references)
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
    BettingPosition,
    MarketOutcome,
    MarketStatus,
    OutcomeToken,
    ProtocolConfiguration,
)
from blockchainpype.dapps.betting_market import BettingMarketModel as BettingMarket

CREATION_DATE = datetime(2024, 1, 1, 12, 0, 0)


def make_market(usdc_asset, outcomes, **overrides) -> BettingMarket:
    """Build a valid BettingMarket, overriding selected fields."""
    values = {
        "market_id": "market_123",
        "title": "Will Bitcoin reach $100k by end of 2024?",
        "description": "Resolves to Yes if BTC reaches $100,000 by Dec 31, 2024",
        "category": "cryptocurrency",
        "status": MarketStatus.ACTIVE,
        "collateral_asset": usdc_asset,
        "outcomes": outcomes,
        "total_volume": Decimal("50000"),
        "total_liquidity": Decimal("25000"),
        "creation_date": CREATION_DATE,
        "end_date": CREATION_DATE + timedelta(days=30),
        "protocol": "Polymarket",
    }
    values.update(overrides)
    return BettingMarket(**values)


class TestOutcomeToken:
    """Test OutcomeToken model."""

    def test_valid_outcome_token(self, yes_token):
        """Test creating a valid outcome token."""
        assert yes_token.token_id == "yes_token_1"
        assert yes_token.outcome_name == "Yes"
        assert yes_token.current_price == Decimal("0.65")
        assert yes_token.probability == Decimal("0.65")

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
        token_zero = OutcomeToken(
            token_id="token_0",
            outcome_name="Zero",
            current_price=Decimal("0.01"),
            total_supply=Decimal("1000"),
            probability=Decimal("0"),
        )
        assert token_zero.probability == Decimal("0")

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

    def test_valid_market_outcome(self, yes_outcome):
        """Test creating a valid market outcome."""
        assert yes_outcome.outcome_id == "outcome_yes"
        assert yes_outcome.outcome_text == "Yes"
        assert len(yes_outcome.outcome_tokens) == 1
        assert not yes_outcome.is_winning_outcome

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

    def test_valid_betting_market(self, usdc_asset, yes_outcome, no_outcome):
        """Test creating a valid betting market with a real asset."""
        market = make_market(usdc_asset, [yes_outcome, no_outcome])

        assert market.market_id == "market_123"
        assert market.status == MarketStatus.ACTIVE
        assert market.is_active
        assert not market.is_resolved
        assert market.collateral_asset == usdc_asset
        assert market.collateral_asset.data.symbol == "USDC"
        assert len(market.outcomes) == 2

    def test_non_asset_collateral_rejected(self, yes_outcome):
        """Objects that are not financepype assets must be rejected."""

        class NotAnAsset:
            symbol = "USDC"
            decimals = 6

        with pytest.raises(ValidationError):
            make_market(NotAnAsset(), [yes_outcome])

    def test_resolved_market(self, usdc_asset, yes_outcome, no_outcome):
        """Test a validly resolved market and the winning_outcome property."""
        market = make_market(
            usdc_asset,
            [yes_outcome, no_outcome],
            status=MarketStatus.RESOLVED,
            resolved_outcome_id="outcome_yes",
            resolution_date=CREATION_DATE + timedelta(days=31),
        )

        assert market.is_resolved
        assert not market.is_active
        winning_outcome = market.winning_outcome
        assert winning_outcome is not None
        assert winning_outcome.outcome_id == "outcome_yes"

    def test_winning_outcome_none_when_unresolved(
        self, usdc_asset, yes_outcome, no_outcome
    ):
        """Unresolved markets have no winning outcome."""
        market = make_market(usdc_asset, [yes_outcome, no_outcome])
        assert market.winning_outcome is None

    def test_resolved_market_without_outcome_id_rejected(
        self, usdc_asset, yes_outcome, no_outcome
    ):
        """Resolved markets must reference their resolved outcome."""
        with pytest.raises(
            ValidationError, match="Resolved markets must have a resolved outcome ID"
        ):
            make_market(
                usdc_asset,
                [yes_outcome, no_outcome],
                status=MarketStatus.RESOLVED,
                resolved_outcome_id=None,
            )

    def test_resolved_outcome_id_must_reference_existing_outcome(
        self, usdc_asset, yes_outcome, no_outcome
    ):
        """A resolved outcome ID not present among outcomes must be rejected."""
        with pytest.raises(
            ValidationError,
            match="Resolved outcome ID must reference an existing outcome",
        ):
            make_market(
                usdc_asset,
                [yes_outcome, no_outcome],
                status=MarketStatus.RESOLVED,
                resolved_outcome_id="outcome_nonexistent",
            )

    def test_invalid_end_date_before_creation(self, usdc_asset, yes_outcome):
        """Test validation error when end date is before creation date."""
        with pytest.raises(
            ValidationError, match="End date cannot be before creation date"
        ):
            make_market(
                usdc_asset,
                [yes_outcome],
                end_date=CREATION_DATE - timedelta(days=1),
            )

    def test_invalid_resolution_date_before_creation(
        self, usdc_asset, yes_outcome, no_outcome
    ):
        """Test validation error when resolution date is before creation date."""
        with pytest.raises(
            ValidationError, match="Resolution date cannot be before creation date"
        ):
            make_market(
                usdc_asset,
                [yes_outcome, no_outcome],
                status=MarketStatus.RESOLVED,
                resolved_outcome_id="outcome_yes",
                resolution_date=CREATION_DATE - timedelta(days=1),
            )

    def test_resolution_date_equal_to_creation_accepted(
        self, usdc_asset, yes_outcome, no_outcome
    ):
        """A resolution date equal to the creation date is valid."""
        market = make_market(
            usdc_asset,
            [yes_outcome, no_outcome],
            status=MarketStatus.RESOLVED,
            resolved_outcome_id="outcome_yes",
            resolution_date=CREATION_DATE,
        )
        assert market.resolution_date == CREATION_DATE


class TestBettingPosition:
    """Test BettingPosition model."""

    def test_valid_betting_position(self, yes_token):
        """Test creating a valid betting position."""
        position = BettingPosition(
            market_id="market_123",
            outcome_token=yes_token,
            shares_owned=Decimal("100"),
            average_price=Decimal("0.55"),
            total_invested=Decimal("55"),
            current_value=Decimal("65"),
            unrealized_pnl=Decimal("10"),
            protocol="Polymarket",
        )

        assert position.market_id == "market_123"
        assert position.shares_owned == Decimal("100")
        assert abs(position.roi_percentage - Decimal("18.18")) < Decimal("0.01")
        assert position.is_profitable

    def test_roi_calculation(self, yes_token):
        """Test ROI percentage calculation."""
        profitable_position = BettingPosition(
            market_id="market_123",
            outcome_token=yes_token,
            shares_owned=Decimal("100"),
            average_price=Decimal("0.50"),
            total_invested=Decimal("50"),
            current_value=Decimal("75"),
            unrealized_pnl=Decimal("25"),
            protocol="Test",
        )
        assert profitable_position.roi_percentage == Decimal("50")
        assert profitable_position.is_profitable

        loss_position = BettingPosition(
            market_id="market_123",
            outcome_token=yes_token,
            shares_owned=Decimal("100"),
            average_price=Decimal("0.70"),
            total_invested=Decimal("70"),
            current_value=Decimal("50"),
            unrealized_pnl=Decimal("-20"),
            protocol="Test",
        )
        assert abs(loss_position.roi_percentage - Decimal("-28.57")) < Decimal("0.01")
        assert not loss_position.is_profitable

    def test_zero_investment_roi(self, yes_token):
        """Test ROI calculation with zero investment."""
        position = BettingPosition(
            market_id="market_123",
            outcome_token=yes_token,
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

    def test_valid_protocol_configuration(self, generic_betting_protocol):
        """Test creating a valid protocol configuration."""
        assert generic_betting_protocol.protocol_name == "Generic Betting Market"
        assert generic_betting_protocol.fee_rate == Decimal("0.025")

    def test_default_fee_rate(self):
        """Test default fee rate."""
        config = ProtocolConfiguration(
            protocol_name="Test Protocol",
            contract_address="0x1234567890123456789012345678901234567890",
        )
        assert config.fee_rate == Decimal("0.02")  # 2% default


class TestBettingMarketConfiguration:
    """Test BettingMarketConfiguration model."""

    def test_valid_configuration(self, dapp_platform):
        """Test creating a valid betting market configuration."""
        protocol = ProtocolConfiguration(
            protocol_name="Test Protocol",
            contract_address="0x1234567890123456789012345678901234567890",
        )

        config = BettingMarketConfiguration(
            platform=dapp_platform,
            protocols=[protocol],
            default_slippage_tolerance=Decimal("0.015"),
            max_gas_price_gwei=75,
        )

        assert len(config.protocols) == 1
        assert config.default_slippage_tolerance == Decimal("0.015")
        assert config.max_gas_price_gwei == 75

    def test_default_values(self, dapp_platform):
        """Test default configuration values."""
        protocol = ProtocolConfiguration(
            protocol_name="Test Protocol",
            contract_address="0x1234567890123456789012345678901234567890",
        )

        config = BettingMarketConfiguration(
            platform=dapp_platform, protocols=[protocol]
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
