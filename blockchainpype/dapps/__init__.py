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
    ProtocolConfiguration,
    ProtocolImplementation,
    UserAccountData,
)

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
    "ProtocolImplementation",
    "MoneyMarketConfiguration",
    "ProtocolConfiguration",
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
