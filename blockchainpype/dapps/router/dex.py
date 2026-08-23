from abc import ABC, abstractmethod
from collections.abc import Sequence
from decimal import Decimal
from typing import Protocol, cast, runtime_checkable

from financepype.assets.blockchain import BlockchainAsset
from financepype.operations.transactions.transaction import BlockchainTransaction
from financepype.operators.dapps.dapp import (
    DecentralizedApplication,
    DecentralizedApplicationConfiguration,
)
from financepype.owners.wallet import BlockchainWallet
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


@runtime_checkable
class ProtocolImplementation(Protocol):
    """Structural contract for protocol-specific swap strategies.

    Implementations translate DEX-agnostic requests into protocol-specific
    on-chain interactions. The :class:`DecentralizedExchange` facade selects a
    strategy per call and delegates to these methods.

    Contract:

    * ``quote_swap`` receives ``max_slippage`` from the facade (either the
      caller's explicit value or ``DexConfiguration.default_slippage``).
      Implementations MUST embed it in the returned ``SwapRoute.max_slippage``
      and use it when computing protection amounts (e.g. minimum received).
    * ``build_swap_transaction`` receives ``deadline_minutes`` from the facade
      (the caller's explicit value or
      ``DexConfiguration.default_deadline_minutes``) and MUST only build and
      return an unsigned :class:`BlockchainTransaction` — never sign or
      broadcast it. ``recipient`` defaults to the bound wallet's address when
      ``None``.
    * Wallet binding: strategies that need a wallet to build transactions
      (e.g. EVM implementations that need a sender address/nonce) SHOULD
      accept an optional ``wallet`` keyword argument at construction and MUST
      implement ``set_wallet`` so the owning facade (or application code) can
      bind or replace the wallet after construction. ``build_swap_transaction``
      MUST raise ``ValueError`` when a wallet is required but none is bound.
      Read-only methods (``quote_swap``/``get_reserves``) MUST work without a
      wallet. Strategies that never need a wallet implement ``set_wallet`` as
      a no-op.

    The protocol is runtime-checkable and intentionally contains only method
    signatures (no behavior).
    """

    def set_wallet(self, wallet: BlockchainWallet | None) -> None:
        """Bind (or unbind, with ``None``) the wallet used to build transactions."""
        ...

    async def quote_swap(
        self,
        input_asset: BlockchainAsset,
        output_asset: BlockchainAsset,
        amount: Decimal,
        mode: SwapMode = SwapMode.EXACT_INPUT,
        max_slippage: Decimal | None = None,
    ) -> SwapRoute:
        """Quote a swap, embedding ``max_slippage`` in the returned route."""
        ...

    async def get_reserves(
        self,
        asset_a: BlockchainAsset,
        asset_b: BlockchainAsset,
    ) -> tuple[Decimal, Decimal]:
        """Get the current pair reserves in decimal (non-raw) units."""
        ...

    async def build_swap_transaction(
        self,
        route: SwapRoute,
        recipient: str | None = None,
        deadline_minutes: int | None = None,
    ) -> BlockchainTransaction:
        """Build (without signing or broadcasting) the swap transaction."""
        ...


class DecentralizedExchange(DecentralizedApplication, ABC):
    def __init__(self, configuration: DexConfiguration):
        super().__init__(configuration)
        self._configuration = configuration
        self._protocol_strategies: dict[str, ProtocolImplementation] = {}
        self._initialize_protocols()

    @abstractmethod
    def _initialize_protocols(self) -> None:
        """Initialize protocol-specific strategies.

        Subclasses must populate ``self._protocol_strategies`` with
        :class:`ProtocolImplementation` instances keyed by protocol name.
        """

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
        max_slippage: Decimal | None = None,
    ) -> SwapRoute:
        """Get a quote for swapping between two assets.

        Args:
            input_asset: The asset to swap from
            output_asset: The asset to swap to
            amount: The amount to swap
            mode: Whether the amount is input or output
            protocol: Specific protocol to use, if None will find best across all
            max_slippage: Maximum acceptable slippage, if None uses the
                configuration default
        """
        if max_slippage is None:
            max_slippage = self.configuration.default_slippage

        if protocol:
            if protocol not in self._protocol_strategies:
                raise ValueError(f"Unsupported protocol: {protocol}")
            return await self._protocol_strategies[protocol].quote_swap(
                input_asset, output_asset, amount, mode, max_slippage=max_slippage
            )

        # Find best quote across all protocols. A single failing protocol must
        # not abort the aggregation, but if every protocol fails the first
        # error is re-raised so callers see the real cause.
        best_quote: SwapRoute | None = None
        first_error: Exception | None = None
        for name, strategy in self._protocol_strategies.items():
            try:
                quote = await strategy.quote_swap(
                    input_asset, output_asset, amount, mode, max_slippage=max_slippage
                )
            except Exception as error:
                self.logger().warning(
                    "Failed to quote swap on protocol '%s': %s", name, error
                )
                if first_error is None:
                    first_error = error
                continue
            if best_quote is None or self._is_better_quote(quote, best_quote, mode):
                best_quote = quote

        if best_quote is None:
            if first_error is not None:
                raise first_error
            raise ValueError("No valid route found")
        return best_quote

    @staticmethod
    def _is_better_quote(candidate: SwapRoute, best: SwapRoute, mode: SwapMode) -> bool:
        """Compare quotes: EXACT_OUTPUT minimizes input, otherwise maximize output."""
        if mode == SwapMode.EXACT_OUTPUT:
            return candidate.input_amount < best.input_amount
        return candidate.output_amount > best.output_amount

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
            max_slippage=quote.max_slippage,
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
        """Find the optimal trading route between two assets.

        Optional extension point: subclasses with multi-hop routing support
        should override this; the base class does not implement it.
        """
        raise NotImplementedError

    async def execute_swap(
        self,
        route: SwapRoute,
        recipient: str | None = None,
        deadline_minutes: int | None = None,
    ) -> BlockchainTransaction:
        """Build the swap transaction following the specified route.

        Args:
            route: The route to execute (its protocol selects the strategy)
            recipient: Optional recipient address, defaults to the strategy wallet
            deadline_minutes: Transaction deadline, if None uses the
                configuration default
        """
        # The protocol should be encoded in the route
        protocol = route.protocol
        if protocol not in self._protocol_strategies:
            raise ValueError(f"Unsupported protocol: {protocol}")

        if deadline_minutes is None:
            deadline_minutes = self.configuration.default_deadline_minutes

        return await self._protocol_strategies[protocol].build_swap_transaction(
            route, recipient, deadline_minutes=deadline_minutes
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

        # Return first available reserves, logging per-protocol failures
        for name, strategy in self._protocol_strategies.items():
            try:
                return await strategy.get_reserves(asset_a, asset_b)
            except Exception as error:
                self.logger().warning(
                    "Failed to fetch reserves on protocol '%s': %s", name, error
                )
                continue
        raise ValueError("No reserves found for pair")

    async def get_supported_pools(
        self,
        protocol: str | None = None,
    ) -> Sequence[tuple[BlockchainAsset, BlockchainAsset]]:
        """Get a list of supported liquidity pools.

        Optional extension point: subclasses with pool discovery support
        should override this; the base class does not implement it.
        """
        raise NotImplementedError

    @property
    def current_timestamp(self) -> float:
        return cast(float, self.blockchain.current_timestamp)
