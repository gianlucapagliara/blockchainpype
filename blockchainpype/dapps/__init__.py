"""
This package provides interfaces for interacting with decentralized applications (DApps).
It includes router abstractions for DEXes and money market protocols.
"""

from .money_market import (
    BorrowingPosition,
    CollateralMode,
    InterestRateMode,
    LendingPosition,
    MarketData,
    MoneyMarket,
    MoneyMarketAction,
    MoneyMarketConfiguration,
)
from .money_market import ProtocolConfiguration as MoneyMarketProtocolConfiguration
from .money_market import ProtocolImplementation as MoneyMarketProtocolImplementation
from .money_market import UserAccountData

# Betting Market imports
try:
    from .betting_market import (
        BettingMarketAction,
        BettingMarketConfiguration,
        BettingMarketDApp,
        BettingMarketModel,
        BettingPosition,
        MarketOutcome,
        MarketStatus,
        OutcomeToken,
    )
    from .betting_market import (
        ProtocolConfiguration as BettingMarketProtocolConfiguration,
    )
    from .betting_market import (
        ProtocolImplementation as BettingMarketProtocolImplementation,
    )

    _betting_market_available = True
except ImportError:
    BettingMarketDApp = None
    BettingMarketModel = None
    BettingMarketAction = None
    BettingMarketConfiguration = None
    BettingPosition = None
    MarketOutcome = None
    MarketStatus = None
    OutcomeToken = None
    BettingMarketProtocolConfiguration = None
    BettingMarketProtocolImplementation = None
    _betting_market_available = False

# Router imports (optional, may not be available in all setups)
try:
    from .router import SwapMode, SwapRoute

    _router_available = True
except ImportError:
    SwapMode = None
    SwapRoute = None
    _router_available = False

# Base exports
__all__ = [
    # Money Market
    "MoneyMarket",
    "MoneyMarketProtocolImplementation",
    "MoneyMarketConfiguration",
    "MoneyMarketProtocolConfiguration",
    "MoneyMarketAction",
    "LendingPosition",
    "BorrowingPosition",
    "MarketData",
    "UserAccountData",
    "InterestRateMode",
    "CollateralMode",
]

# Add router exports if available
if _router_available:
    __all__.extend(["SwapMode", "SwapRoute"])

# Add betting market exports if available
if _betting_market_available:
    __all__.extend(
        [
            "BettingMarketDApp",
            "BettingMarketModel",
            "BettingMarketProtocolImplementation",
            "BettingMarketConfiguration",
            "BettingMarketProtocolConfiguration",
            "BettingMarketAction",
            "BettingPosition",
            "MarketOutcome",
            "MarketStatus",
            "OutcomeToken",
        ]
    )
