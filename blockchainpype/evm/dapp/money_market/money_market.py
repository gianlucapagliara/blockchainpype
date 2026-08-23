"""
EVM-specific implementation of money market protocols.
Provides base classes for implementing money market protocols on EVM-compatible chains.
"""

from decimal import Decimal
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

    async def is_position_safe(
        self,
        user_address: str,
        protocol: str | None = None,
    ) -> bool:
        """Assess whether a position clears the configured liquidation buffer.

        The configuration's ``liquidation_threshold_buffer`` is a safety
        margin on top of the protocol's liquidation point (health factor 1):
        a position is considered safe only when its health factor is at least
        ``1 + liquidation_threshold_buffer``, giving the owner room to react
        before an actual liquidation becomes possible.

        Args:
            user_address: The user's wallet address
            protocol: Specific protocol to use, if None uses first available

        Returns:
            bool: True when the health factor clears the buffered threshold
        """
        account_data = await self.get_user_account_data(user_address, protocol)
        buffered_threshold = (
            Decimal(1) + self.configuration.liquidation_threshold_buffer
        )
        return account_data.health_factor >= buffered_threshold
