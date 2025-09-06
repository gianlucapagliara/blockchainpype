from decimal import Decimal
from typing import Protocol

from financepype.assets.blockchain import BlockchainAsset
from financepype.operations.transactions.transaction import BlockchainTransaction
from financepype.operators.dapps.dapp import DecentralizedApplication

from .models import (
    BorrowingPosition,
    CollateralMode,
    InterestRateMode,
    LendingPosition,
    MarketData,
    MoneyMarketConfiguration,
    UserAccountData,
)


class ProtocolImplementation(Protocol):
    """Protocol-specific implementation of money market operations."""

    async def get_market_data(
        self,
        asset: BlockchainAsset,
    ) -> MarketData:
        """Get market data for a specific asset."""
        ...

    async def get_user_account_data(
        self,
        user_address: str,
    ) -> UserAccountData:
        """Get user's account data across all positions."""
        ...

    async def get_lending_positions(
        self,
        user_address: str,
    ) -> list[LendingPosition]:
        """Get user's lending positions."""
        ...

    async def get_borrowing_positions(
        self,
        user_address: str,
    ) -> list[BorrowingPosition]:
        """Get user's borrowing positions."""
        ...

    async def build_supply_transaction(
        self,
        asset: BlockchainAsset,
        amount: Decimal,
        user_address: str,
        enable_as_collateral: bool = True,
    ) -> BlockchainTransaction:
        """Build transaction to supply assets to the protocol."""
        ...

    async def build_withdraw_transaction(
        self,
        asset: BlockchainAsset,
        amount: Decimal,
        user_address: str,
    ) -> BlockchainTransaction:
        """Build transaction to withdraw assets from the protocol."""
        ...

    async def build_borrow_transaction(
        self,
        asset: BlockchainAsset,
        amount: Decimal,
        interest_rate_mode: InterestRateMode,
        user_address: str,
    ) -> BlockchainTransaction:
        """Build transaction to borrow assets from the protocol."""
        ...

    async def build_repay_transaction(
        self,
        asset: BlockchainAsset,
        amount: Decimal,
        interest_rate_mode: InterestRateMode,
        user_address: str,
        repay_all: bool = False,
    ) -> BlockchainTransaction:
        """Build transaction to repay borrowed assets."""
        ...

    async def build_collateral_transaction(
        self,
        asset: BlockchainAsset,
        mode: CollateralMode,
        user_address: str,
    ) -> BlockchainTransaction:
        """Build transaction to enable/disable asset as collateral."""
        ...

    async def build_liquidation_transaction(
        self,
        collateral_asset: BlockchainAsset,
        debt_asset: BlockchainAsset,
        user_to_liquidate: str,
        debt_to_cover: Decimal,
        receive_collateral: bool = True,
    ) -> BlockchainTransaction:
        """Build transaction to liquidate an undercollateralized position."""
        ...


class MoneyMarket(DecentralizedApplication):
    """Base class for money market protocols like Aave."""

    def __init__(self, configuration: MoneyMarketConfiguration):
        super().__init__(configuration)
        self._configuration = configuration
        self._protocol_strategies: dict[str, ProtocolImplementation] = {}
        self._initialize_protocols()

    def _initialize_protocols(self) -> None:
        """Initialize protocol-specific strategies."""
        raise NotImplementedError

    @property
    def configuration(self) -> MoneyMarketConfiguration:
        return self._configuration

    @property
    def supported_protocols(self) -> list[str]:
        """Get list of supported money market protocols."""
        return list(self._protocol_strategies.keys())

    async def get_market_data(
        self,
        asset: BlockchainAsset,
        protocol: str | None = None,
    ) -> MarketData:
        """Get market data for a specific asset.

        Args:
            asset: The asset to get market data for
            protocol: Specific protocol to use, if None uses first available
        """
        if protocol:
            if protocol not in self._protocol_strategies:
                raise ValueError(f"Unsupported protocol: {protocol}")
            return await self._protocol_strategies[protocol].get_market_data(asset)

        # Use first available protocol
        if not self._protocol_strategies:
            raise ValueError("No protocols configured")

        first_protocol = next(iter(self._protocol_strategies.values()))
        return await first_protocol.get_market_data(asset)

    async def get_user_account_data(
        self,
        user_address: str,
        protocol: str | None = None,
    ) -> UserAccountData:
        """Get user's account data across all positions.

        Args:
            user_address: The user's wallet address
            protocol: Specific protocol to use, if None uses first available
        """
        if protocol:
            if protocol not in self._protocol_strategies:
                raise ValueError(f"Unsupported protocol: {protocol}")
            return await self._protocol_strategies[protocol].get_user_account_data(
                user_address
            )

        # Use first available protocol
        if not self._protocol_strategies:
            raise ValueError("No protocols configured")

        first_protocol = next(iter(self._protocol_strategies.values()))
        return await first_protocol.get_user_account_data(user_address)

    async def get_lending_positions(
        self,
        user_address: str,
        protocol: str | None = None,
    ) -> list[LendingPosition]:
        """Get user's lending positions.

        Args:
            user_address: The user's wallet address
            protocol: Specific protocol to use, if None aggregates all protocols
        """
        if protocol:
            if protocol not in self._protocol_strategies:
                raise ValueError(f"Unsupported protocol: {protocol}")
            return await self._protocol_strategies[protocol].get_lending_positions(
                user_address
            )

        # Aggregate positions from all protocols
        all_positions: list[LendingPosition] = []
        for strategy in self._protocol_strategies.values():
            positions = await strategy.get_lending_positions(user_address)
            all_positions.extend(positions)

        return all_positions

    async def get_borrowing_positions(
        self,
        user_address: str,
        protocol: str | None = None,
    ) -> list[BorrowingPosition]:
        """Get user's borrowing positions.

        Args:
            user_address: The user's wallet address
            protocol: Specific protocol to use, if None aggregates all protocols
        """
        if protocol:
            if protocol not in self._protocol_strategies:
                raise ValueError(f"Unsupported protocol: {protocol}")
            return await self._protocol_strategies[protocol].get_borrowing_positions(
                user_address
            )

        # Aggregate positions from all protocols
        all_positions: list[BorrowingPosition] = []
        for strategy in self._protocol_strategies.values():
            positions = await strategy.get_borrowing_positions(user_address)
            all_positions.extend(positions)

        return all_positions

    async def supply(
        self,
        asset: BlockchainAsset,
        amount: Decimal,
        user_address: str,
        enable_as_collateral: bool | None = None,
        protocol: str | None = None,
    ) -> BlockchainTransaction:
        """Supply assets to the money market.

        Args:
            asset: The asset to supply
            amount: The amount to supply
            user_address: The user's wallet address
            enable_as_collateral: Whether to enable as collateral (uses default if None)
            protocol: Specific protocol to use, if None uses first available
        """
        if enable_as_collateral is None:
            enable_as_collateral = (
                self.configuration.default_collateral_mode == CollateralMode.ENABLED
            )

        protocol_impl = self._get_protocol_implementation(protocol)
        return await protocol_impl.build_supply_transaction(
            asset, amount, user_address, enable_as_collateral
        )

    async def withdraw(
        self,
        asset: BlockchainAsset,
        amount: Decimal,
        user_address: str,
        protocol: str | None = None,
    ) -> BlockchainTransaction:
        """Withdraw assets from the money market.

        Args:
            asset: The asset to withdraw
            amount: The amount to withdraw
            user_address: The user's wallet address
            protocol: Specific protocol to use, if None uses first available
        """
        protocol_impl = self._get_protocol_implementation(protocol)
        return await protocol_impl.build_withdraw_transaction(
            asset, amount, user_address
        )

    async def borrow(
        self,
        asset: BlockchainAsset,
        amount: Decimal,
        user_address: str,
        interest_rate_mode: InterestRateMode | None = None,
        protocol: str | None = None,
    ) -> BlockchainTransaction:
        """Borrow assets from the money market.

        Args:
            asset: The asset to borrow
            amount: The amount to borrow
            user_address: The user's wallet address
            interest_rate_mode: Interest rate mode (uses default if None)
            protocol: Specific protocol to use, if None uses first available
        """
        if interest_rate_mode is None:
            interest_rate_mode = self.configuration.default_interest_rate_mode

        protocol_impl = self._get_protocol_implementation(protocol)
        return await protocol_impl.build_borrow_transaction(
            asset, amount, interest_rate_mode, user_address
        )

    async def repay(
        self,
        asset: BlockchainAsset,
        amount: Decimal,
        user_address: str,
        interest_rate_mode: InterestRateMode | None = None,
        repay_all: bool = False,
        protocol: str | None = None,
    ) -> BlockchainTransaction:
        """Repay borrowed assets to the money market.

        Args:
            asset: The asset to repay
            amount: The amount to repay
            user_address: The user's wallet address
            interest_rate_mode: Interest rate mode (uses default if None)
            repay_all: Whether to repay the full debt
            protocol: Specific protocol to use, if None uses first available
        """
        if interest_rate_mode is None:
            interest_rate_mode = self.configuration.default_interest_rate_mode

        protocol_impl = self._get_protocol_implementation(protocol)
        return await protocol_impl.build_repay_transaction(
            asset, amount, interest_rate_mode, user_address, repay_all
        )

    async def set_collateral_mode(
        self,
        asset: BlockchainAsset,
        mode: CollateralMode,
        user_address: str,
        protocol: str | None = None,
    ) -> BlockchainTransaction:
        """Enable or disable an asset as collateral.

        Args:
            asset: The asset to modify collateral status for
            mode: Whether to enable or disable as collateral
            user_address: The user's wallet address
            protocol: Specific protocol to use, if None uses first available
        """
        protocol_impl = self._get_protocol_implementation(protocol)
        return await protocol_impl.build_collateral_transaction(
            asset, mode, user_address
        )

    async def liquidate(
        self,
        collateral_asset: BlockchainAsset,
        debt_asset: BlockchainAsset,
        user_to_liquidate: str,
        debt_to_cover: Decimal,
        receive_collateral: bool = True,
        protocol: str | None = None,
    ) -> BlockchainTransaction:
        """Liquidate an undercollateralized position.

        Args:
            collateral_asset: The collateral asset to receive
            debt_asset: The debt asset to repay
            user_to_liquidate: Address of the user to liquidate
            debt_to_cover: Amount of debt to cover
            receive_collateral: Whether to receive collateral (vs aTokens)
            protocol: Specific protocol to use, if None uses first available
        """
        protocol_impl = self._get_protocol_implementation(protocol)
        return await protocol_impl.build_liquidation_transaction(
            collateral_asset,
            debt_asset,
            user_to_liquidate,
            debt_to_cover,
            receive_collateral,
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
