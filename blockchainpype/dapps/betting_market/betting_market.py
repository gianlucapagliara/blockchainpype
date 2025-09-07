from decimal import Decimal
from typing import Protocol

from financepype.operations.transactions.transaction import BlockchainTransaction
from financepype.operators.dapps.dapp import DecentralizedApplication

from .models import BettingMarket, BettingMarketConfiguration, BettingPosition


class ProtocolImplementation(Protocol):
    """Protocol-specific implementation of betting market operations."""

    async def get_market(
        self,
        market_id: str,
    ) -> BettingMarket:
        """Get detailed information about a specific market."""
        ...

    async def get_markets(
        self,
        category: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[BettingMarket]:
        """Get list of available markets with optional filtering."""
        ...

    async def get_user_positions(
        self,
        user_address: str,
        market_id: str | None = None,
    ) -> list[BettingPosition]:
        """Get user's betting positions."""
        ...

    async def get_outcome_token_price(
        self,
        market_id: str,
        outcome_token_id: str,
    ) -> Decimal:
        """Get current price of an outcome token."""
        ...

    async def build_buy_transaction(
        self,
        market_id: str,
        outcome_token_id: str,
        amount: Decimal,
        max_price: Decimal,
        user_address: str,
    ) -> BlockchainTransaction:
        """Build transaction to buy outcome tokens."""
        ...

    async def build_sell_transaction(
        self,
        market_id: str,
        outcome_token_id: str,
        shares: Decimal,
        min_price: Decimal,
        user_address: str,
    ) -> BlockchainTransaction:
        """Build transaction to sell outcome tokens."""
        ...

    async def build_redeem_transaction(
        self,
        market_id: str,
        user_address: str,
    ) -> BlockchainTransaction:
        """Build transaction to redeem winnings from resolved market."""
        ...

    async def calculate_buy_quote(
        self,
        market_id: str,
        outcome_token_id: str,
        amount: Decimal,
    ) -> tuple[Decimal, Decimal]:
        """Calculate expected shares and total cost for buying."""
        ...

    async def calculate_sell_quote(
        self,
        market_id: str,
        outcome_token_id: str,
        shares: Decimal,
    ) -> tuple[Decimal, Decimal]:
        """Calculate expected payout and fees for selling."""
        ...


class BettingMarket(DecentralizedApplication):
    """Base class for betting market protocols like Polymarket."""

    def __init__(self, configuration: BettingMarketConfiguration):
        super().__init__(configuration)
        self._configuration = configuration
        self._protocol_strategies: dict[str, ProtocolImplementation] = {}
        self._initialize_protocols()

    def _initialize_protocols(self) -> None:
        """Initialize protocol-specific strategies."""
        raise NotImplementedError

    @property
    def configuration(self) -> BettingMarketConfiguration:
        return self._configuration

    @property
    def supported_protocols(self) -> list[str]:
        """Get list of supported betting market protocols."""
        return list(self._protocol_strategies.keys())

    async def get_market(
        self,
        market_id: str,
        protocol: str | None = None,
    ) -> BettingMarket:
        """Get detailed information about a specific market.

        Args:
            market_id: The unique identifier of the market
            protocol: Specific protocol to use, if None uses first available
        """
        protocol_impl = self._get_protocol_implementation(protocol)
        return await protocol_impl.get_market(market_id)

    async def get_markets(
        self,
        category: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
        protocol: str | None = None,
    ) -> list[BettingMarket]:
        """Get list of available markets with optional filtering.

        Args:
            category: Filter by market category
            status: Filter by market status
            limit: Maximum number of markets to return
            offset: Number of markets to skip
            protocol: Specific protocol to use, if None aggregates all protocols
        """
        if protocol:
            protocol_impl = self._get_protocol_implementation(protocol)
            return await protocol_impl.get_markets(category, status, limit, offset)

        # Aggregate markets from all protocols
        all_markets: list[BettingMarket] = []
        for strategy in self._protocol_strategies.values():
            markets = await strategy.get_markets(category, status, limit, offset)
            all_markets.extend(markets)

        return all_markets

    async def get_user_positions(
        self,
        user_address: str,
        market_id: str | None = None,
        protocol: str | None = None,
    ) -> list[BettingPosition]:
        """Get user's betting positions.

        Args:
            user_address: The user's wallet address
            market_id: Specific market to get positions for, if None gets all
            protocol: Specific protocol to use, if None aggregates all protocols
        """
        if protocol:
            protocol_impl = self._get_protocol_implementation(protocol)
            return await protocol_impl.get_user_positions(user_address, market_id)

        # Aggregate positions from all protocols
        all_positions: list[BettingPosition] = []
        for strategy in self._protocol_strategies.values():
            positions = await strategy.get_user_positions(user_address, market_id)
            all_positions.extend(positions)

        return all_positions

    async def get_outcome_token_price(
        self,
        market_id: str,
        outcome_token_id: str,
        protocol: str | None = None,
    ) -> Decimal:
        """Get current price of an outcome token.

        Args:
            market_id: The market identifier
            outcome_token_id: The outcome token identifier
            protocol: Specific protocol to use, if None uses first available
        """
        protocol_impl = self._get_protocol_implementation(protocol)
        return await protocol_impl.get_outcome_token_price(market_id, outcome_token_id)

    async def buy_outcome_tokens(
        self,
        market_id: str,
        outcome_token_id: str,
        amount: Decimal,
        user_address: str,
        max_price: Decimal | None = None,
        protocol: str | None = None,
    ) -> BlockchainTransaction:
        """Buy outcome tokens for a specific market outcome.

        Args:
            market_id: The market identifier
            outcome_token_id: The outcome token to buy
            amount: Amount to invest (in collateral token)
            user_address: The user's wallet address
            max_price: Maximum acceptable price per token
            protocol: Specific protocol to use, if None uses first available
        """
        if max_price is None:
            # Get current price and add slippage tolerance
            current_price = await self.get_outcome_token_price(
                market_id, outcome_token_id, protocol
            )
            max_price = current_price * (
                1 + self.configuration.default_slippage_tolerance
            )

        protocol_impl = self._get_protocol_implementation(protocol)
        return await protocol_impl.build_buy_transaction(
            market_id, outcome_token_id, amount, max_price, user_address
        )

    async def sell_outcome_tokens(
        self,
        market_id: str,
        outcome_token_id: str,
        shares: Decimal,
        user_address: str,
        min_price: Decimal | None = None,
        protocol: str | None = None,
    ) -> BlockchainTransaction:
        """Sell outcome tokens for a specific market outcome.

        Args:
            market_id: The market identifier
            outcome_token_id: The outcome token to sell
            shares: Number of shares to sell
            user_address: The user's wallet address
            min_price: Minimum acceptable price per token
            protocol: Specific protocol to use, if None uses first available
        """
        if min_price is None:
            # Get current price and subtract slippage tolerance
            current_price = await self.get_outcome_token_price(
                market_id, outcome_token_id, protocol
            )
            min_price = current_price * (
                1 - self.configuration.default_slippage_tolerance
            )

        protocol_impl = self._get_protocol_implementation(protocol)
        return await protocol_impl.build_sell_transaction(
            market_id, outcome_token_id, shares, min_price, user_address
        )

    async def redeem_winnings(
        self,
        market_id: str,
        user_address: str,
        protocol: str | None = None,
    ) -> BlockchainTransaction:
        """Redeem winnings from a resolved market.

        Args:
            market_id: The resolved market identifier
            user_address: The user's wallet address
            protocol: Specific protocol to use, if None uses first available
        """
        protocol_impl = self._get_protocol_implementation(protocol)
        return await protocol_impl.build_redeem_transaction(market_id, user_address)

    async def get_buy_quote(
        self,
        market_id: str,
        outcome_token_id: str,
        amount: Decimal,
        protocol: str | None = None,
    ) -> tuple[Decimal, Decimal]:
        """Get quote for buying outcome tokens.

        Args:
            market_id: The market identifier
            outcome_token_id: The outcome token to buy
            amount: Amount to invest (in collateral token)
            protocol: Specific protocol to use, if None uses first available

        Returns:
            Tuple of (expected_shares, total_cost_including_fees)
        """
        protocol_impl = self._get_protocol_implementation(protocol)
        return await protocol_impl.calculate_buy_quote(
            market_id, outcome_token_id, amount
        )

    async def get_sell_quote(
        self,
        market_id: str,
        outcome_token_id: str,
        shares: Decimal,
        protocol: str | None = None,
    ) -> tuple[Decimal, Decimal]:
        """Get quote for selling outcome tokens.

        Args:
            market_id: The market identifier
            outcome_token_id: The outcome token to sell
            shares: Number of shares to sell
            protocol: Specific protocol to use, if None uses first available

        Returns:
            Tuple of (expected_payout, total_fees)
        """
        protocol_impl = self._get_protocol_implementation(protocol)
        return await protocol_impl.calculate_sell_quote(
            market_id, outcome_token_id, shares
        )

    def _get_protocol_implementation(
        self, protocol: str | None
    ) -> ProtocolImplementation:
        """Get protocol implementation, using first available if None specified."""
        if protocol:
            if protocol not in self._protocol_strategies:
                raise ValueError(f"Unsupported protocol: {protocol}")
            return self._protocol_strategies[protocol]

        # Use first available protocol
        if not self._protocol_strategies:
            raise ValueError("No protocols configured")

        return next(iter(self._protocol_strategies.values()))
