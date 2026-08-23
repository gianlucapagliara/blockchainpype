"""
Solana-specific implementation of money market protocols.
Provides base classes for implementing money market protocols on Solana.
"""

from decimal import Decimal
from typing import cast

from financepype.owners.wallet import BlockchainWallet

from blockchainpype.dapps.money_market import MoneyMarket, MoneyMarketConfiguration
from blockchainpype.solana.blockchain.blockchain import SolanaBlockchain


class SolanaMoneyMarketConfiguration(MoneyMarketConfiguration):
    """Configuration for Solana money market protocols."""

    pass


class SolanaMoneyMarket(MoneyMarket):
    """Solana-specific money market implementation.

    This facade narrows the generic :class:`MoneyMarket` base to the Solana
    platform (typed configuration and blockchain accessors) and adds the
    wallet-binding plumbing shared by Solana protocol strategies: strategies
    build unsigned :class:`~blockchainpype.solana.transaction.SolanaTransaction`
    objects, and a bound wallet is only needed to sign and broadcast them.
    """

    def __init__(self, configuration: SolanaMoneyMarketConfiguration):
        super().__init__(configuration)

    @property
    def configuration(self) -> SolanaMoneyMarketConfiguration:
        return cast(SolanaMoneyMarketConfiguration, super().configuration)

    @property
    def blockchain(self) -> SolanaBlockchain:
        """Get the Solana blockchain instance."""
        return cast(SolanaBlockchain, super().blockchain)

    def set_wallet(self, wallet: BlockchainWallet | None) -> None:
        """Bind (or unbind, with ``None``) a wallet on every protocol strategy.

        The money-market protocol contract is build-only: strategies never need
        a wallet to build or read. The bound wallet is used by strategy-level
        execution helpers (e.g. ``Solend.place_transaction``) to sign and
        broadcast previously built transactions.

        Args:
            wallet: The wallet to bind, or None to unbind
        """
        for strategy in self._protocol_strategies.values():
            set_wallet = getattr(strategy, "set_wallet", None)
            if callable(set_wallet):
                set_wallet(wallet)

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
