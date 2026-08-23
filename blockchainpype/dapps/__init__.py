"""
This package provides interfaces for interacting with decentralized applications (DApps).
It includes router abstractions for DEXes, money market and betting market protocols.
"""

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
from .router import (
    DecentralizedExchange,
    DexConfiguration,
    SlippageMode,
    SwapHop,
    SwapMode,
    SwapRoute,
)
from .router import ProtocolConfiguration as DexProtocolConfiguration
from .router import ProtocolImplementation as DexProtocolImplementation

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
    # Router / DEX
    "DecentralizedExchange",
    "DexConfiguration",
    "DexProtocolConfiguration",
    "DexProtocolImplementation",
    "SlippageMode",
    "SwapHop",
    "SwapMode",
    "SwapRoute",
    # Betting Market
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
