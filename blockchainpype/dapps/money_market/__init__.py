"""
This package provides interfaces for interacting with money market protocols like Aave.
It includes base classes for lending, borrowing, and market data management.
"""

from .models import (
    BlockchainAsset,
    BorrowingPosition,
    CollateralMode,
    InterestRateMode,
    LendingPosition,
    MarketData,
    MoneyMarketAction,
    MoneyMarketConfiguration,
    ProtocolConfiguration,
    UserAccountData,
)
from .money_market import MoneyMarket, ProtocolImplementation

__all__ = [
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
    "BlockchainAsset",
]
