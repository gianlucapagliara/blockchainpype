from collections.abc import Sequence
from decimal import Decimal
from typing import Protocol, cast

from financepype.assets.blockchain import BlockchainAsset
from financepype.operations.transactions.transaction import BlockchainTransaction
from financepype.operators.dapps.dapp import (
    DecentralizedApplication,
    DecentralizedApplicationConfiguration,
)
from pydantic import BaseModel

from .models import SwapMode, SwapRoute


class ProtocolConfiguration(BaseModel):
    protocol_name: str
    factory_address: str
    router_address: str
    fee_tiers: list[Decimal]


class DexConfiguration(DecentralizedApplicationConfiguration):
    protocols: list[ProtocolConfiguration]
    default_slippage: Decimal = Decimal("0.005")  # 0.5%
    default_deadline_minutes: int = 20
    minimum_received_multiplier: Decimal = Decimal("0.95")  # 5% slippage protection


class ProtocolStrategy(Protocol):
    """Protocol-specific implementation of routing logic."""

    async def quote_swap(
        self,
        input_asset: BlockchainAsset,
        output_asset: BlockchainAsset,
        amount: Decimal,
        mode: SwapMode = SwapMode.EXACT_INPUT,
    ) -> SwapRoute: ...

    async def get_reserves(
        self,
        asset_a: BlockchainAsset,
        asset_b: BlockchainAsset,
    ) -> tuple[Decimal, Decimal]: ...

    async def build_swap_transaction(
        self,
        route: SwapRoute,
        recipient: str | None = None,
    ) -> BlockchainTransaction: ...


class DecentralizedExchange(DecentralizedApplication):
    def __init__(self, configuration: DexConfiguration):
        super().__init__(configuration)
        self._configuration = configuration
        self._protocol_strategies: dict[str, ProtocolStrategy] = {}
        self._initialize_protocols()

    def _initialize_protocols(self) -> None:
        """Initialize protocol-specific strategies."""
        raise NotImplementedError

    @property
    def configuration(self) -> DexConfiguration:
        return self._configuration

    @property
    def supported_protocols(self) -> list[str]:
        """Get list of supported DEX protocols."""
        return list(self._protocol_strategies.keys())

    async def quote_swap(
        self,
        input_asset: BlockchainAsset,
        output_asset: BlockchainAsset,
        amount: Decimal,
        mode: SwapMode = SwapMode.EXACT_INPUT,
        protocol: str | None = None,
    ) -> SwapRoute:
        """Get a quote for swapping between two assets.

        Args:
            input_asset: The asset to swap from
            output_asset: The asset to swap to
            amount: The amount to swap
            mode: Whether the amount is input or output
            protocol: Specific protocol to use, if None will find best across all
        """
        if protocol:
            if protocol not in self._protocol_strategies:
                raise ValueError(f"Unsupported protocol: {protocol}")
            return await self._protocol_strategies[protocol].quote_swap(
                input_asset, output_asset, amount, mode
            )

        # Find best quote across all protocols
        best_quote: SwapRoute | None = None
        for strategy in self._protocol_strategies.values():
            quote = await strategy.quote_swap(input_asset, output_asset, amount, mode)
            if best_quote is None or quote.output_amount > best_quote.output_amount:
                best_quote = quote

        if best_quote is None:
            raise ValueError("No valid route found")
        return best_quote

    async def update_quote(
        self,
        quote: SwapRoute,
    ) -> SwapRoute:
        """Update a quote with the latest information."""
        return await self.quote_swap(
            quote.input_asset,
            quote.output_asset,
            quote.input_amount
            if quote.mode == SwapMode.EXACT_INPUT
            else quote.output_amount,
            quote.mode,
        )

    async def find_best_route(
        self,
        input_asset: BlockchainAsset,
        output_asset: BlockchainAsset,
        amount: Decimal,
        mode: SwapMode = SwapMode.EXACT_INPUT,
        max_hops: int = 3,
        protocol: str | None = None,
    ) -> SwapRoute:
        """Find the optimal trading route between two assets."""
        raise NotImplementedError

    async def execute_swap(
        self,
        route: SwapRoute,
        recipient: str | None = None,
    ) -> BlockchainTransaction:
        """Execute a swap following the specified route."""
        # The protocol should be encoded in the route
        protocol = route.protocol
        if protocol not in self._protocol_strategies:
            raise ValueError(f"Unsupported protocol: {protocol}")

        return await self._protocol_strategies[protocol].build_swap_transaction(
            route, recipient
        )

    async def get_reserves(
        self,
        asset_a: BlockchainAsset,
        asset_b: BlockchainAsset,
        protocol: str | None = None,
    ) -> tuple[Decimal, Decimal]:
        """Get the current reserves for a pair of assets."""
        if protocol:
            if protocol not in self._protocol_strategies:
                raise ValueError(f"Unsupported protocol: {protocol}")
            return await self._protocol_strategies[protocol].get_reserves(
                asset_a, asset_b
            )

        # Return first available reserves
        for strategy in self._protocol_strategies.values():
            try:
                return await strategy.get_reserves(asset_a, asset_b)
            except Exception:
                continue
        raise ValueError("No reserves found for pair")

    async def get_supported_pools(
        self,
        protocol: str | None = None,
    ) -> Sequence[tuple[BlockchainAsset, BlockchainAsset]]:
        """Get a list of supported liquidity pools."""
        raise NotImplementedError

    @property
    def current_timestamp(self) -> float:
        return cast(float, self.blockchain.current_timestamp)
