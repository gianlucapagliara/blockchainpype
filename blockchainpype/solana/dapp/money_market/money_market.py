"""
Solana-specific implementation of money market protocols.
Provides base classes for implementing money market protocols on Solana.
"""

from typing import cast

from blockchainpype.dapps.money_market import MoneyMarket, MoneyMarketConfiguration
from blockchainpype.solana.blockchain.blockchain import SolanaBlockchain


class SolanaMoneyMarketConfiguration(MoneyMarketConfiguration):
    """Configuration for Solana money market protocols."""

    pass


class SolanaMoneyMarket(MoneyMarket):
    """Solana-specific money market implementation."""

    def __init__(self, configuration: SolanaMoneyMarketConfiguration):
        super().__init__(configuration)

    @property
    def configuration(self) -> SolanaMoneyMarketConfiguration:
        return cast(SolanaMoneyMarketConfiguration, super().configuration)

    @property
    def blockchain(self) -> SolanaBlockchain:
        """Get the Solana blockchain instance."""
        return cast(SolanaBlockchain, super().blockchain)
