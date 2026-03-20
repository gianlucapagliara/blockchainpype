"""
This package provides interfaces for interacting with decentralized applications (DApps).
It includes router abstractions for DEXes and money market protocols.
"""

from typing import Any

from .money_market import (
    BorrowingPosition,
    CollateralMode,
    InterestRateMode,
    LendingPosition,
    MarketData,
    MoneyMarket,
    MoneyMarketAction,
    MoneyMarketConfiguration,
    UserAccountData,
)
from .money_market import ProtocolConfiguration as MoneyMarketProtocolConfiguration
from .money_market import ProtocolImplementation as MoneyMarketProtocolImplementation

# Betting Market imports
_betting_market_available = False
BettingMarketDApp: Any = None
BettingMarketModel: Any = None
BettingMarketAction: Any = None
BettingMarketConfiguration: Any = None
BettingPosition: Any = None
MarketOutcome: Any = None
MarketStatus: Any = None
OutcomeToken: Any = None
BettingMarketProtocolConfiguration: Any = None
BettingMarketProtocolImplementation: Any = None

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
    pass

# Router imports (optional, may not be available in all setups)
_router_available = False
SwapMode: Any = None
SwapRoute: Any = None

try:
    from .router.models import SwapMode, SwapRoute

    _router_available = True
except ImportError:
    pass

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
