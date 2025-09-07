"""
This package provides interfaces for interacting with betting market protocols.
It includes abstractions for prediction markets like Polymarket.
"""

from .betting_market import BettingMarket as BettingMarketDApp
from .betting_market import ProtocolImplementation
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
