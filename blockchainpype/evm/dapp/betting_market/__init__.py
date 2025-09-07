"""
This package provides EVM-specific implementations for betting market protocols.
It includes Polymarket integration and other EVM-based prediction markets.
"""

from .polymarket import (
    EVMBettingMarket,
    EVMBettingMarketConfiguration,
    Polymarket,
    PolymarketBettingMarket,
    PolymarketConfiguration,
)

__all__ = [
    "EVMBettingMarket",
    "EVMBettingMarketConfiguration",
    "Polymarket",
    "PolymarketConfiguration",
    "PolymarketBettingMarket",
]
