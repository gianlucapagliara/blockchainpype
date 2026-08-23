"""
Uniswap V3 protocol strategy.

This module implements the :class:`ProtocolImplementation` contract for
Uniswap V3 concentrated-liquidity pools: quoter-driven quoting across
configurable fee tiers, single-hop ``exactInputSingle``/``exactOutputSingle``
and multi-hop packed-path ``exactInput``/``exactOutput`` transaction building
through a bound :class:`EthereumWallet`.

Fee tier handling: the shared :class:`SwapRoute` model has no protocol-specific
fields, so the selected pool fee travels in ``SwapRoute.taxes`` as a fraction
(``fee_tier / 1_000_000``, e.g. ``0.003`` for the 3000 tier). Single-hop
builds recover the tier as ``taxes * 1e6``. Multi-hop routes need one tier per
hop, which cannot be reconstructed from the single ``taxes`` sum, so
:meth:`UniswapV3.compose_multi_hop_route` records the per-hop tiers in an
internal route-keyed map consulted at build time.
"""

import uuid
from collections import OrderedDict
from collections.abc import Sequence
from decimal import Decimal
from typing import Any, cast

from financepype.assets.blockchain import BlockchainAsset
from financepype.owners.wallet import BlockchainWallet
from web3.types import TxParams

from blockchainpype.dapps.router.dex import ProtocolImplementation
from blockchainpype.dapps.router.models import SwapHop, SwapMode, SwapRoute
from blockchainpype.evm.asset import EthereumAsset
from blockchainpype.evm.blockchain.blockchain import EthereumBlockchain
from blockchainpype.evm.blockchain.identifier import EthereumAddress
from blockchainpype.evm.dapp.abi import EthereumLocalFileABI
from blockchainpype.evm.dapp.contract import EthereumContractConfiguration
from blockchainpype.evm.dapp.uniswap.v2 import (
    ZERO_ADDRESS,
    BlockchainBoundContract,
    asset_decimals,
    asset_symbol,
    ensure_asset_data,
)
from blockchainpype.evm.dapp.unsigned import build_unsigned_transaction
from blockchainpype.evm.transaction import EthereumTransaction
from blockchainpype.evm.wallet.wallet import EthereumWallet

FEE_TIER_DENOMINATOR = 1_000_000
Q96 = 2**96


class UniswapV3(ProtocolImplementation):
    """Uniswap V3 protocol strategy fulfilling :class:`ProtocolImplementation`.

    Quotes are fetched on-chain from the IQuoter contract across the
    configured fee tiers, keeping the best tier. Routes carry the registered
    protocol key ``uniswap_v3`` (so the DEX facade can dispatch them) and the
    selected pool fee as ``taxes = fee_tier / 1e6``.
    """

    PROTOCOL_NAME = "uniswap_v3"
    DEFAULT_FEE_TIERS: tuple[int, ...] = (100, 500, 3000, 10000)
    ROUTE_FEE_TIER_CACHE_SIZE = 1024
    DEFAULT_MAX_SLIPPAGE = Decimal("0.005")
    DEFAULT_DEADLINE_MINUTES = 20

    def __init__(
        self,
        blockchain: EthereumBlockchain,
        factory_address: str,
        router_address: str,
        quoter_address: str,
        fee_tiers: Sequence[int] | None = None,
        wallet: EthereumWallet | None = None,
    ) -> None:
        """Initialize the strategy against a specific blockchain.

        Args:
            blockchain: The blockchain every contract call is routed through;
                its platform is threaded into all contract configurations
            factory_address: The Uniswap V3 factory contract address
            router_address: The Uniswap V3 SwapRouter contract address
            quoter_address: The Uniswap V3 IQuoter contract address
            fee_tiers: The uint24 fee tiers to consider when quoting (e.g.
                ``[500, 3000]``); defaults to the four canonical tiers
            wallet: Optional wallet used to build/sign transactions; it can
                also be bound later via :meth:`set_wallet`

        Raises:
            ValueError: If a fee tier is outside the valid uint24 range
        """
        self.blockchain = blockchain
        self.factory_address = factory_address
        self.router_address = router_address
        self.quoter_address = quoter_address
        self._wallet = wallet
        self._pool_contracts: dict[str, BlockchainBoundContract] = {}
        # Bounded LRU: every find_best_route call composes candidate routes
        # with amount-dependent keys, so an unbounded dict would grow for the
        # lifetime of a long-running process.
        self._route_fee_tiers: OrderedDict[str, tuple[int, ...]] = OrderedDict()

        tiers = tuple(fee_tiers) if fee_tiers else self.DEFAULT_FEE_TIERS
        for tier in tiers:
            if tier <= 0 or tier >= 2**24:
                raise ValueError(f"Fee tier {tier} is outside the uint24 range")
        self.fee_tiers: list[int] = list(tiers)

        self.factory_contract = BlockchainBoundContract(
            EthereumContractConfiguration(
                address=EthereumAddress.from_string(factory_address),
                abi_configuration=EthereumLocalFileABI(
                    file_name="uniswap_v3/UniswapV3Factory.json"
                ),
                platform=blockchain.platform,
            ),
            blockchain=blockchain,
        )
        self.router_contract = BlockchainBoundContract(
            EthereumContractConfiguration(
                address=EthereumAddress.from_string(router_address),
                abi_configuration=EthereumLocalFileABI(
                    file_name="uniswap_v3/ISwapRouter.json"
                ),
                platform=blockchain.platform,
            ),
            blockchain=blockchain,
        )
        self.quoter_contract = BlockchainBoundContract(
            EthereumContractConfiguration(
                address=EthereumAddress.from_string(quoter_address),
                abi_configuration=EthereumLocalFileABI(
                    file_name="uniswap_v3/IQuoter.json"
                ),
                platform=blockchain.platform,
            ),
            blockchain=blockchain,
        )

    # === Wallet binding ===

    @property
    def wallet(self) -> EthereumWallet | None:
        """The wallet currently bound for transaction building, if any."""
        return self._wallet

    def set_wallet(self, wallet: BlockchainWallet | None) -> None:
        """Bind (or unbind, with ``None``) the wallet used to build transactions.

        Args:
            wallet: The wallet to bind; must be an :class:`EthereumWallet`

        Raises:
            TypeError: If the wallet is not an EthereumWallet
        """
        if wallet is not None and not isinstance(wallet, EthereumWallet):
            raise TypeError(
                f"UniswapV3 requires an EthereumWallet, got {type(wallet).__name__}"
            )
        self._wallet = wallet

    def _require_wallet(self) -> EthereumWallet:
        """Return the bound wallet or raise per the protocol contract."""
        if self._wallet is None:
            raise ValueError(
                "No wallet is bound to this Uniswap V3 strategy; "
                "call set_wallet() before building transactions"
            )
        return self._wallet

    # === Contract plumbing ===

    async def _ensure_contracts_initialized(self) -> None:
        """Ensure factory, router, and quoter contracts are initialized."""
        if not self.factory_contract.is_initialized:
            await self.factory_contract.initialize()
        if not self.router_contract.is_initialized:
            await self.router_contract.initialize()
        if not self.quoter_contract.is_initialized:
            await self.quoter_contract.initialize()

    async def _get_pool(
        self, asset_a: EthereumAsset, asset_b: EthereumAsset, fee: int
    ) -> str:
        """Get the pool address for two assets and a specific fee tier."""
        await self._ensure_contracts_initialized()
        result: str = await self.factory_contract.functions.getPool(
            asset_a.address.raw, asset_b.address.raw, fee
        ).call()
        return result

    async def _get_pool_contract(self, pool_address: str) -> BlockchainBoundContract:
        """Get (and cache) an initialized pool contract for ``pool_address``."""
        contract = self._pool_contracts.get(pool_address)
        if contract is None:
            contract = BlockchainBoundContract(
                EthereumContractConfiguration(
                    address=EthereumAddress.from_string(pool_address),
                    abi_configuration=EthereumLocalFileABI(
                        file_name="uniswap_v3/UniswapV3Pool.json"
                    ),
                    platform=self.blockchain.platform,
                ),
                blockchain=self.blockchain,
            )
            self._pool_contracts[pool_address] = contract
        if not contract.is_initialized:
            await contract.initialize()
        return contract

    async def pool_exists(
        self, asset_a: BlockchainAsset, asset_b: BlockchainAsset
    ) -> bool:
        """Check whether any configured fee tier has a pool for the assets."""
        ethereum_a = cast(EthereumAsset, asset_a)
        ethereum_b = cast(EthereumAsset, asset_b)
        for fee_tier in self.fee_tiers:
            pool_address = await self._get_pool(ethereum_a, ethereum_b, fee_tier)
            if pool_address and pool_address != ZERO_ADDRESS:
                return True
        return False

    # === Fee tier <-> taxes mapping ===

    @staticmethod
    def fee_tier_from_fraction(fraction: Decimal) -> int:
        """Convert a fee fraction (e.g. ``0.003``) to a uint24 tier (``3000``).

        Raises:
            ValueError: If the fraction does not map to an integral tier in
                the uint24 range
        """
        scaled = fraction * FEE_TIER_DENOMINATOR
        if scaled != scaled.to_integral_value():
            raise ValueError(
                f"Fee fraction {fraction} does not map to an integral "
                f"Uniswap V3 fee tier"
            )
        tier = int(scaled)
        if tier <= 0 or tier >= 2**24:
            raise ValueError(f"Fee tier {tier} is outside the uint24 range")
        return tier

    @staticmethod
    def _route_key(route: SwapRoute) -> str:
        """Deterministic fingerprint of a route used for fee-tier lookup."""
        parts: list[str] = [route.mode.value]
        for hop in route.sequence:
            parts.append(cast(EthereumAsset, hop.input_asset).address.raw.lower())
            parts.append(cast(EthereumAsset, hop.output_asset).address.raw.lower())
            parts.append(str(hop.input_amount))
            parts.append(str(hop.output_amount))
        return "|".join(parts)

    def _fee_tiers_for_route(self, route: SwapRoute) -> tuple[int, ...]:
        """Recover the per-hop fee tiers for ``route``.

        Single-hop routes carry the pool fee in ``taxes`` (``tier / 1e6``),
        so the tier is recovered directly. Multi-hop routes are looked up in
        the map populated by :meth:`compose_multi_hop_route`.

        Raises:
            ValueError: If the tiers cannot be determined
        """
        if len(route.sequence) == 1:
            return (self.fee_tier_from_fraction(route.taxes),)
        tiers = self._route_fee_tiers.get(self._route_key(route))
        if tiers is not None:
            self._route_fee_tiers.move_to_end(self._route_key(route))
        if tiers is None:
            raise ValueError(
                "Cannot determine per-hop fee tiers for this multi-hop route: "
                "multi-hop Uniswap V3 routes must be composed by this strategy "
                "(via compose_multi_hop_route / UniswapDEX.find_best_route)"
            )
        return tiers

    # === Path encoding ===

    @staticmethod
    def encode_path(token_addresses: Sequence[str], fee_tiers: Sequence[int]) -> bytes:
        """Pack a Uniswap V3 swap path: token (20B) | fee (3B) | token | ...

        Args:
            token_addresses: Hop token addresses in path order
            fee_tiers: One uint24 fee tier per hop (``len(tokens) - 1``)

        Returns:
            bytes: The packed path used by ``exactInput``/``exactOutput``

        Raises:
            ValueError: If the lengths are inconsistent or an address or fee
                tier is malformed
        """
        if len(token_addresses) < 2:
            raise ValueError("A path requires at least two token addresses")
        if len(fee_tiers) != len(token_addresses) - 1:
            raise ValueError("A path requires exactly one fee tier per hop")

        encoded = b""
        for index, address in enumerate(token_addresses):
            address_bytes = bytes.fromhex(address.removeprefix("0x"))
            if len(address_bytes) != 20:
                raise ValueError(f"Invalid token address in path: {address}")
            encoded += address_bytes
            if index < len(fee_tiers):
                fee_tier = fee_tiers[index]
                if fee_tier <= 0 or fee_tier >= 2**24:
                    raise ValueError(f"Fee tier {fee_tier} is outside the uint24 range")
                encoded += fee_tier.to_bytes(3, "big")
        return encoded

    # === Quoting ===

    async def quote_swap(
        self,
        input_asset: BlockchainAsset,
        output_asset: BlockchainAsset,
        amount: Decimal,
        mode: SwapMode = SwapMode.EXACT_INPUT,
        max_slippage: Decimal | None = None,
    ) -> SwapRoute:
        """Quote a single-hop swap via the IQuoter across the fee tiers.

        Every configured fee tier with an existing pool is quoted on-chain
        (``quoteExactInputSingle``/``quoteExactOutputSingle``); the best tier
        (highest output for exact input, lowest input for exact output) wins.
        ``max_slippage`` is embedded in the route (defaulting to
        ``DEFAULT_MAX_SLIPPAGE`` when the caller does not forward one).

        Raises:
            ValueError: If the amount is not positive or no tier can quote
        """
        if amount <= 0:
            raise ValueError("Swap amount must be positive")
        slippage = (
            max_slippage if max_slippage is not None else self.DEFAULT_MAX_SLIPPAGE
        )

        ethereum_input = await ensure_asset_data(input_asset)
        ethereum_output = await ensure_asset_data(output_asset)

        await self._ensure_contracts_initialized()

        best_quote: Decimal | None = None
        best_fee_tier: int | None = None

        for fee_tier in self.fee_tiers:
            try:
                pool_address = await self._get_pool(
                    ethereum_input, ethereum_output, fee_tier
                )
                if not pool_address or pool_address == ZERO_ADDRESS:
                    continue

                if mode == SwapMode.EXACT_INPUT:
                    amount_raw = ethereum_input.convert_to_raw(amount)
                    quote_raw = (
                        await self.quoter_contract.functions.quoteExactInputSingle(
                            ethereum_input.address.raw,
                            ethereum_output.address.raw,
                            fee_tier,
                            amount_raw,
                            0,  # sqrtPriceLimitX96 (0 = no limit)
                        ).call()
                    )
                    quote_amount = ethereum_output.convert_to_decimals(int(quote_raw))
                else:
                    amount_raw = ethereum_output.convert_to_raw(amount)
                    quote_raw = (
                        await self.quoter_contract.functions.quoteExactOutputSingle(
                            ethereum_input.address.raw,
                            ethereum_output.address.raw,
                            fee_tier,
                            amount_raw,
                            0,  # sqrtPriceLimitX96 (0 = no limit)
                        ).call()
                    )
                    quote_amount = ethereum_input.convert_to_decimals(int(quote_raw))

                if (
                    best_quote is None
                    or (mode == SwapMode.EXACT_INPUT and quote_amount > best_quote)
                    or (mode == SwapMode.EXACT_OUTPUT and quote_amount < best_quote)
                ):
                    best_quote = quote_amount
                    best_fee_tier = fee_tier

            except Exception:
                # A reverting tier (no liquidity, price limit) is skipped
                continue

        if best_quote is None or best_fee_tier is None:
            raise ValueError(
                f"No valid Uniswap V3 pool found for "
                f"{asset_symbol(ethereum_input)}/{asset_symbol(ethereum_output)}"
            )

        if mode == SwapMode.EXACT_INPUT:
            input_amount = amount
            output_amount = best_quote
        else:
            input_amount = best_quote
            output_amount = amount

        swap_hop = SwapHop(
            input_asset=ethereum_input,
            input_amount=input_amount,
            output_asset=ethereum_output,
            output_amount=output_amount,
            are_amounts_raw=False,
        )
        return SwapRoute(
            input_asset=ethereum_input,
            input_amount=input_amount,
            output_asset=ethereum_output,
            output_amount=output_amount,
            are_amounts_raw=False,
            sequence=[swap_hop],
            mode=mode,
            max_slippage=slippage,
            # The selected pool fee travels here: tier / 1e6 (see module docs)
            taxes=Decimal(best_fee_tier) / FEE_TIER_DENOMINATOR,
            protocol=self.PROTOCOL_NAME,
        )

    def compose_multi_hop_route(
        self,
        hop_routes: Sequence[SwapRoute],
        mode: SwapMode,
        max_slippage: Decimal,
    ) -> SwapRoute:
        """Compose per-hop quotes into a multi-hop V3 route.

        Each hop quote must be a single-hop ``uniswap_v3`` route; its fee
        tier is recovered from ``taxes`` and recorded in the internal
        route-keyed map so :meth:`build_swap_transaction` can encode the
        packed multi-hop path later.

        Raises:
            ValueError: If no hops are given, a hop belongs to another
                protocol, or a hop is itself multi-hop
        """
        if not hop_routes:
            raise ValueError("At least one hop route is required")

        tiers: list[int] = []
        sequence: list[SwapHop] = []
        for hop_route in hop_routes:
            if hop_route.protocol != self.PROTOCOL_NAME:
                raise ValueError(
                    f"Cannot compose a {self.PROTOCOL_NAME} route from a "
                    f"'{hop_route.protocol}' hop"
                )
            if len(hop_route.sequence) != 1:
                raise ValueError("Each hop route must be single-hop")
            tiers.append(self.fee_tier_from_fraction(hop_route.taxes))
            sequence.append(hop_route.sequence[0])

        taxes = sum((hop_route.taxes for hop_route in hop_routes), Decimal(0))
        route = SwapRoute(
            input_asset=sequence[0].input_asset,
            input_amount=sequence[0].input_amount,
            output_asset=sequence[-1].output_asset,
            output_amount=sequence[-1].output_amount,
            are_amounts_raw=False,
            sequence=sequence,
            mode=mode,
            max_slippage=max_slippage,
            taxes=taxes,
            protocol=self.PROTOCOL_NAME,
        )
        self._route_fee_tiers[self._route_key(route)] = tuple(tiers)
        self._route_fee_tiers.move_to_end(self._route_key(route))
        while len(self._route_fee_tiers) > self.ROUTE_FEE_TIER_CACHE_SIZE:
            self._route_fee_tiers.popitem(last=False)
        return route

    # === Reserves ===

    async def get_reserves(
        self,
        asset_a: BlockchainAsset,
        asset_b: BlockchainAsset,
    ) -> tuple[Decimal, Decimal]:
        """Approximate the pair reserves from the first existing pool.

        Uniswap V3 liquidity is concentrated in price ranges, so exact global
        reserves are not observable from ``liquidity()``/``slot0()`` alone.
        This uses the standard approximation for the active range:
        ``amount0 = L / sqrtP`` and ``amount1 = L * sqrtP`` with
        ``sqrtP = sqrtPriceX96 / 2**96``, scaled per token decimals.

        Raises:
            ValueError: If no pool exists or the pool price is not initialized
        """
        ethereum_a = await ensure_asset_data(asset_a)
        ethereum_b = await ensure_asset_data(asset_b)

        pool_address: str | None = None
        for fee_tier in self.fee_tiers:
            candidate = await self._get_pool(ethereum_a, ethereum_b, fee_tier)
            if candidate and candidate != ZERO_ADDRESS:
                pool_address = candidate
                break
        if pool_address is None:
            raise ValueError(
                f"No Uniswap V3 pool found for "
                f"{asset_symbol(ethereum_a)}/{asset_symbol(ethereum_b)}"
            )

        pool_contract = await self._get_pool_contract(pool_address)
        liquidity = int(await pool_contract.functions.liquidity().call())
        slot0 = await pool_contract.functions.slot0().call()
        sqrt_price_x96 = int(slot0[0])
        if sqrt_price_x96 <= 0:
            raise ValueError(
                f"Uniswap V3 pool {pool_address} is not initialized "
                "(sqrtPriceX96 is zero)"
            )

        sqrt_price = Decimal(sqrt_price_x96) / Decimal(Q96)
        amount0_raw = Decimal(liquidity) / sqrt_price
        amount1_raw = Decimal(liquidity) * sqrt_price

        token0: str = await pool_contract.functions.token0().call()
        scale_a = Decimal(10) ** asset_decimals(ethereum_a)
        scale_b = Decimal(10) ** asset_decimals(ethereum_b)

        if ethereum_a.address.raw.lower() == token0.lower():
            return (amount0_raw / scale_a, amount1_raw / scale_b)
        return (amount1_raw / scale_a, amount0_raw / scale_b)

    # === Transaction building ===

    async def _build_swap_tx_params(
        self,
        route: SwapRoute,
        recipient: str | None,
        deadline_minutes: int | None,
    ) -> TxParams:
        """Build the router-call transaction parameters for ``route``."""
        wallet = self._require_wallet()
        if route.protocol != self.PROTOCOL_NAME:
            raise ValueError(
                f"Route protocol '{route.protocol}' is not {self.PROTOCOL_NAME}"
            )

        ethereum_input = await ensure_asset_data(route.input_asset)
        ethereum_output = await ensure_asset_data(route.output_asset)
        fee_tiers = self._fee_tiers_for_route(route)

        if deadline_minutes is None:
            deadline_minutes = self.DEFAULT_DEADLINE_MINUTES
        deadline = int(self.blockchain.current_timestamp) + deadline_minutes * 60

        if recipient is None:
            recipient = wallet.address.raw

        await self._ensure_contracts_initialized()

        one = Decimal(1)
        params: dict[str, Any]
        if len(route.sequence) == 1:
            fee = fee_tiers[0]
            if route.mode == SwapMode.EXACT_INPUT:
                params = {
                    "tokenIn": ethereum_input.address.raw,
                    "tokenOut": ethereum_output.address.raw,
                    "fee": fee,
                    "recipient": recipient,
                    "deadline": deadline,
                    "amountIn": ethereum_input.convert_to_raw(route.input_amount),
                    "amountOutMinimum": ethereum_output.convert_to_raw(
                        route.output_amount * (one - route.max_slippage)
                    ),
                    "sqrtPriceLimitX96": 0,
                }
                function = self.router_contract.functions.exactInputSingle(params)
            elif route.mode == SwapMode.EXACT_OUTPUT:
                params = {
                    "tokenIn": ethereum_input.address.raw,
                    "tokenOut": ethereum_output.address.raw,
                    "fee": fee,
                    "recipient": recipient,
                    "deadline": deadline,
                    "amountOut": ethereum_output.convert_to_raw(route.output_amount),
                    "amountInMaximum": ethereum_input.convert_to_raw(
                        route.input_amount * (one + route.max_slippage)
                    ),
                    "sqrtPriceLimitX96": 0,
                }
                function = self.router_contract.functions.exactOutputSingle(params)
            else:
                raise ValueError(f"Unsupported swap mode: {route.mode}")
        else:
            addresses: list[str] = [
                cast(EthereumAsset, route.sequence[0].input_asset).address.raw
            ]
            for hop in route.sequence:
                addresses.append(cast(EthereumAsset, hop.output_asset).address.raw)

            if route.mode == SwapMode.EXACT_INPUT:
                params = {
                    "path": self.encode_path(addresses, list(fee_tiers)),
                    "recipient": recipient,
                    "deadline": deadline,
                    "amountIn": ethereum_input.convert_to_raw(route.input_amount),
                    "amountOutMinimum": ethereum_output.convert_to_raw(
                        route.output_amount * (one - route.max_slippage)
                    ),
                }
                function = self.router_contract.functions.exactInput(params)
            elif route.mode == SwapMode.EXACT_OUTPUT:
                # exactOutput paths are encoded in reverse (output token first)
                params = {
                    "path": self.encode_path(
                        list(reversed(addresses)), list(reversed(fee_tiers))
                    ),
                    "recipient": recipient,
                    "deadline": deadline,
                    "amountOut": ethereum_output.convert_to_raw(route.output_amount),
                    "amountInMaximum": ethereum_input.convert_to_raw(
                        route.input_amount * (one + route.max_slippage)
                    ),
                }
                function = self.router_contract.functions.exactOutput(params)
            else:
                raise ValueError(f"Unsupported swap mode: {route.mode}")

        return await wallet.build_transaction(function=function)

    def _generate_client_operation_id(self, route: SwapRoute) -> str:
        """Generate a unique tracking id for a swap on this route."""
        input_part = cast(EthereumAsset, route.input_asset).address.raw[:8]
        output_part = cast(EthereumAsset, route.output_asset).address.raw[:8]
        return (
            f"{self.PROTOCOL_NAME}_swap_{input_part}_{output_part}_"
            f"{uuid.uuid4().hex[:8]}"
        )

    async def build_swap_transaction(
        self,
        route: SwapRoute,
        recipient: str | None = None,
        deadline_minutes: int | None = None,
        client_operation_id: str | None = None,
    ) -> EthereumTransaction:
        """Build (without signing or broadcasting) the swap transaction.

        The transaction parameters are built through the bound wallet's
        ``build_transaction`` (sender, chain id, calldata, gas fees) and the
        returned :class:`EthereumTransaction` tracking object stays unsigned
        in ``PENDING_BROADCAST`` state; the built parameters are carried in
        ``other_data[UNSIGNED_TX_DATA_KEY]`` (see
        :mod:`blockchainpype.evm.dapp.unsigned`).

        Args:
            route: The quoted route to execute
            recipient: Output recipient, defaulting to the bound wallet address
            deadline_minutes: Transaction deadline, defaulting to
                ``DEFAULT_DEADLINE_MINUTES`` (the DEX facade forwards its
                configured default)
            client_operation_id: Optional tracking id, generated when omitted

        Raises:
            ValueError: If no wallet is bound
        """
        wallet = self._require_wallet()
        tx_params = await self._build_swap_tx_params(route, recipient, deadline_minutes)
        if client_operation_id is None:
            client_operation_id = self._generate_client_operation_id(route)
        return build_unsigned_transaction(client_operation_id, wallet, tx_params)

    async def create_swap_transaction(
        self,
        route: SwapRoute,
        wallet: EthereumWallet | None = None,
        recipient: str | None = None,
        client_operation_id: str | None = None,
        deadline_minutes: int | None = None,
    ) -> EthereumTransaction:
        """Build, sign and broadcast a swap transaction for the given route.

        Args:
            route: The swap route to execute
            wallet: Optional wallet; when provided it is bound via
                :meth:`set_wallet`, otherwise the already-bound wallet is used
            recipient: Optional recipient address (defaults to wallet address)
            client_operation_id: Optional client operation ID for tracking
            deadline_minutes: Optional transaction deadline in minutes

        Returns:
            EthereumTransaction: The tracked, signed and broadcast transaction

        Raises:
            ValueError: If no wallet is bound
        """
        if wallet is not None:
            self.set_wallet(wallet)
        bound_wallet = self._require_wallet()

        tx_params = await self._build_swap_tx_params(route, recipient, deadline_minutes)
        if bound_wallet.last_nonce is None:
            await bound_wallet.sync_nonce()
        if client_operation_id is None:
            client_operation_id = self._generate_client_operation_id(route)

        return bound_wallet.sign_and_send_transaction(
            client_operation_id=client_operation_id,
            tx_data=cast(dict[str, Any], dict(tx_params)),
            auto_assign_nonce=True,
        )

    async def execute_swap(
        self,
        input_asset: BlockchainAsset,
        output_asset: BlockchainAsset,
        amount: Decimal,
        wallet: EthereumWallet | None = None,
        mode: SwapMode = SwapMode.EXACT_INPUT,
        recipient: str | None = None,
        client_operation_id: str | None = None,
        max_slippage: Decimal | None = None,
        deadline_minutes: int | None = None,
    ) -> EthereumTransaction:
        """Quote and execute a swap between two assets in one call.

        Args:
            input_asset: The asset to swap from
            output_asset: The asset to swap to
            amount: The amount to swap (input or output amount per ``mode``)
            wallet: Optional wallet; when provided it is bound via
                :meth:`set_wallet`, otherwise the already-bound wallet is used
            mode: The swap mode (EXACT_INPUT or EXACT_OUTPUT)
            recipient: Optional recipient address (defaults to wallet address)
            client_operation_id: Optional client operation ID for tracking
            max_slippage: Maximum acceptable slippage embedded into the quote
            deadline_minutes: Optional transaction deadline in minutes

        Returns:
            EthereumTransaction: The tracked, signed and broadcast transaction
        """
        if wallet is not None:
            self.set_wallet(wallet)
        route = await self.quote_swap(
            input_asset, output_asset, amount, mode, max_slippage=max_slippage
        )
        return await self.create_swap_transaction(
            route=route,
            recipient=recipient,
            client_operation_id=client_operation_id,
            deadline_minutes=deadline_minutes,
        )
