"""
EVM-specific implementation of money market protocols.
Provides base classes for implementing money market protocols on EVM-compatible chains.
"""

from typing import cast

from blockchainpype.dapps.money_market import MoneyMarket, MoneyMarketConfiguration
from blockchainpype.evm.blockchain.blockchain import EthereumBlockchain


class EVMMoneyMarketConfiguration(MoneyMarketConfiguration):
    """Configuration for EVM money market protocols."""

    pass


class EVMMoneyMarket(MoneyMarket):
    """EVM-specific money market implementation."""

    def __init__(self, configuration: EVMMoneyMarketConfiguration):
        super().__init__(configuration)

    @property
    def configuration(self) -> EVMMoneyMarketConfiguration:
        return cast(EVMMoneyMarketConfiguration, super().configuration)

    @property
    def blockchain(self) -> EthereumBlockchain:
        """Get the EVM blockchain instance."""
        return cast(EthereumBlockchain, super().blockchain)
