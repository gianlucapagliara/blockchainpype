"""
This package provides interfaces for interacting with betting market protocols.
It includes abstractions for prediction markets like Polymarket.
"""

from .betting_market import (
    MAX_DERIVED_OUTCOME_PRICE,
    MIN_DERIVED_OUTCOME_PRICE,
    ProtocolImplementation,
)
from .betting_market import BettingMarket as BettingMarketDApp
from .models import BettingMarket as BettingMarketModel
from .models import (
    BettingMarketAction,
    BettingMarketConfiguration,
    BettingPosition,
    MarketOutcome,
    MarketStatus,
    OutcomeToken,
    ProtocolConfiguration,
)

__all__ = [
    "MAX_DERIVED_OUTCOME_PRICE",
    "MIN_DERIVED_OUTCOME_PRICE",
    "BettingMarketDApp",
    "BettingMarketModel",
    "ProtocolImplementation",
    "BettingMarketConfiguration",
    "ProtocolConfiguration",
    "BettingMarketAction",
    "BettingPosition",
    "MarketOutcome",
    "MarketStatus",
    "OutcomeToken",
]
