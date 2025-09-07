"""
This package provides interfaces for interacting with Ethereum smart contracts and dApps.
It includes implementations for standard contracts like ERC-20 tokens and provides
base classes for building custom contract interfaces.
"""

from .money_market import (
    AaveV3,
    AaveV3Configuration,
    AaveV3DataProviderContract,
    AaveV3MoneyMarket,
    AaveV3PoolContract,
    EVMMoneyMarket,
    EVMMoneyMarketConfiguration,
)

# Betting Market imports
try:
    from .betting_market import (
        EVMBettingMarket,
        EVMBettingMarketConfiguration,
        Polymarket,
        PolymarketBettingMarket,
        PolymarketConfiguration,
    )

    _betting_market_available = True
except ImportError:
    EVMBettingMarket = None
    EVMBettingMarketConfiguration = None
    Polymarket = None
    PolymarketConfiguration = None
    PolymarketBettingMarket = None
    _betting_market_available = False

__all__ = [
    "EVMMoneyMarket",
    "EVMMoneyMarketConfiguration",
    "AaveV3",
    "AaveV3Configuration",
    "AaveV3DataProviderContract",
    "AaveV3MoneyMarket",
    "AaveV3PoolContract",
]

# Add betting market exports if available
if _betting_market_available:
    __all__.extend(
        [
            "EVMBettingMarket",
            "EVMBettingMarketConfiguration",
            "Polymarket",
            "PolymarketConfiguration",
            "PolymarketBettingMarket",
        ]
    )
