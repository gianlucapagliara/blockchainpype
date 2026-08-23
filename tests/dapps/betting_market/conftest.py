"""
Pytest configuration for betting market tests.
"""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from blockchainpype.dapps.betting_market import (
    BettingMarketModel,
    MarketOutcome,
    MarketStatus,
    OutcomeToken,
    ProtocolConfiguration,
)


@pytest.fixture
def generic_betting_protocol():
    """Generic betting market protocol configuration."""
    return ProtocolConfiguration(
        protocol_name="Generic Betting Market",
        contract_address="0x1234567890123456789012345678901234567890",
        conditional_tokens_address="0xabcdefabcdefabcdefabcdefabcdefabcdefabcd",
        collateral_token_address="0xfedcbafedcbafedcbafedcbafedcbafedcbafedc",
        fee_rate=Decimal("0.025"),
    )


@pytest.fixture
def yes_token():
    """Sample YES outcome token."""
    return OutcomeToken(
        token_id="yes_token_1",
        outcome_name="Yes",
        current_price=Decimal("0.65"),
        total_supply=Decimal("10000"),
        probability=Decimal("0.65"),
    )


@pytest.fixture
def no_token():
    """Sample NO outcome token."""
    return OutcomeToken(
        token_id="no_token_1",
        outcome_name="No",
        current_price=Decimal("0.35"),
        total_supply=Decimal("5000"),
        probability=Decimal("0.35"),
    )


@pytest.fixture
def yes_outcome(yes_token):
    """Sample YES market outcome."""
    return MarketOutcome(
        outcome_id="outcome_yes",
        outcome_text="Yes",
        outcome_tokens=[yes_token],
    )


@pytest.fixture
def no_outcome(no_token):
    """Sample NO market outcome."""
    return MarketOutcome(
        outcome_id="outcome_no",
        outcome_text="No",
        outcome_tokens=[no_token],
    )


@pytest.fixture
def sample_market(usdc_asset, yes_outcome, no_outcome):
    """Sample betting market backed by a real financepype asset."""
    return BettingMarketModel(
        market_id="test_market_1",
        title="Test Market",
        description="A test betting market",
        category="test",
        status=MarketStatus.ACTIVE,
        collateral_asset=usdc_asset,
        outcomes=[yes_outcome, no_outcome],
        total_volume=Decimal("50000"),
        total_liquidity=Decimal("25000"),
        creation_date=datetime(2024, 1, 1, 12, 0, 0),
        end_date=datetime(2024, 1, 1, 12, 0, 0) + timedelta(days=30),
        protocol="Test Protocol",
    )
