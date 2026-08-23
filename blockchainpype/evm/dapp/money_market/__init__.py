"""
This package provides EVM-specific implementations for money market protocols.
It includes contract interfaces and implementations for protocols like Aave.
"""

from .aave import (
    AAVE_V3_ETHEREUM_DATA_PROVIDER_ADDRESS,
    AAVE_V3_ETHEREUM_POOL_ADDRESS,
    AaveV3,
    AaveV3Configuration,
    AaveV3DataProviderContract,
    AaveV3MoneyMarket,
    AaveV3PoolContract,
)
from .money_market import EVMMoneyMarket, EVMMoneyMarketConfiguration

__all__ = [
    "AAVE_V3_ETHEREUM_DATA_PROVIDER_ADDRESS",
    "AAVE_V3_ETHEREUM_POOL_ADDRESS",
    "EVMMoneyMarket",
    "EVMMoneyMarketConfiguration",
    "AaveV3",
    "AaveV3Configuration",
    "AaveV3DataProviderContract",
    "AaveV3MoneyMarket",
    "AaveV3PoolContract",
]
