from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Self

from financepype.operators.dapps.dapp import DecentralizedApplicationConfiguration
from pydantic import BaseModel, ConfigDict, model_validator


# Mock BlockchainAsset for testing purposes
class BlockchainAsset:
    """Mock blockchain asset class."""

    pass


class MarketStatus(str, Enum):
    """Status of a betting market."""

    ACTIVE = "active"
    CLOSED = "closed"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"


class BettingMarketAction(str, Enum):
    """Available actions in betting market protocols."""

    BUY = "buy"
    SELL = "sell"
    REDEEM = "redeem"
    CLAIM = "claim"


class ProtocolConfiguration(BaseModel):
    """Configuration for a specific betting market protocol."""

    protocol_name: str
    contract_address: str
    conditional_tokens_address: str | None = None
    collateral_token_address: str | None = None
    oracle_address: str | None = None
    fee_rate: Decimal = Decimal("0.02")  # 2% default fee


class BettingMarketConfiguration(DecentralizedApplicationConfiguration):
    """Configuration for betting market DApp."""

    protocols: list[ProtocolConfiguration]
    default_slippage_tolerance: Decimal = Decimal("0.01")  # 1% default slippage
    max_gas_price_gwei: int = 50


class OutcomeToken(BaseModel):
    """Represents an outcome token in a prediction market."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    token_id: str
    outcome_name: str
    token_address: str | None = None
    current_price: Decimal
    total_supply: Decimal
    volume_24h: Decimal = Decimal("0")
    probability: Decimal  # Implied probability from price

    @model_validator(mode="after")
    def validate_probability(self) -> Self:
        """Validate probability is between 0 and 1."""
        if not (0 <= self.probability <= 1):
            raise ValueError("Probability must be between 0 and 1")
        return self


class MarketOutcome(BaseModel):
    """Represents a possible outcome in a betting market."""

    outcome_id: str
    outcome_text: str
    outcome_tokens: list[OutcomeToken]
    is_winning_outcome: bool = False

    @property
    def total_probability(self) -> Decimal:
        """Calculate total probability across all outcome tokens."""
        return sum(token.probability for token in self.outcome_tokens)


class BettingMarket(BaseModel):
    """Represents a betting/prediction market."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    market_id: str
    title: str
    description: str
    category: str
    status: MarketStatus
    collateral_asset: BlockchainAsset
    outcomes: list[MarketOutcome]
    total_volume: Decimal
    total_liquidity: Decimal
    creation_date: datetime
    end_date: datetime | None = None
    resolution_date: datetime | None = None
    resolved_outcome_id: str | None = None
    protocol: str
    metadata: dict[str, Any] = {}

    @property
    def is_active(self) -> bool:
        """Check if market is currently active for trading."""
        return self.status == MarketStatus.ACTIVE

    @property
    def is_resolved(self) -> bool:
        """Check if market has been resolved."""
        return self.status == MarketStatus.RESOLVED

    @property
    def winning_outcome(self) -> MarketOutcome | None:
        """Get the winning outcome if market is resolved."""
        if not self.resolved_outcome_id:
            return None
        return next(
            (
                outcome
                for outcome in self.outcomes
                if outcome.outcome_id == self.resolved_outcome_id
            ),
            None,
        )

    @model_validator(mode="after")
    def validate_market_consistency(self) -> Self:
        """Validate market data consistency."""
        if self.status == MarketStatus.RESOLVED and not self.resolved_outcome_id:
            raise ValueError("Resolved markets must have a resolved outcome ID")

        if self.end_date and self.end_date < self.creation_date:
            raise ValueError("End date cannot be before creation date")

        return self


class BettingPosition(BaseModel):
    """Represents a user's position in a betting market."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    market_id: str
    outcome_token: OutcomeToken
    shares_owned: Decimal
    average_price: Decimal
    total_invested: Decimal
    current_value: Decimal
    unrealized_pnl: Decimal
    protocol: str

    @property
    def roi_percentage(self) -> Decimal:
        """Calculate return on investment percentage."""
        if self.total_invested == 0:
            return Decimal("0")
        return (self.unrealized_pnl / self.total_invested) * 100

    @property
    def is_profitable(self) -> bool:
        """Check if position is currently profitable."""
        return self.unrealized_pnl > 0
