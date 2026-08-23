"""
Uniswap DEX facade.

This module provides :class:`UniswapDEX`, a :class:`DecentralizedExchange`
implementation wiring the Uniswap V2 and V3 protocol strategies against a
concrete :class:`EthereumBlockchain`, plus :class:`UniswapConfiguration` with
presets for common networks and a builder for local (e.g. hardhat) deployments.
"""

from collections.abc import Sequence
from decimal import Decimal
from itertools import pairwise, permutations
from typing import cast

from financepype.assets.blockchain import BlockchainAsset
from financepype.owners.wallet import BlockchainWallet
from financepype.platforms.blockchain import BlockchainPlatform
from pydantic import Field

from blockchainpype.dapps.router.dex import (
    DecentralizedExchange,
    DexConfiguration,
    ProtocolConfiguration,
    ProtocolImplementation,
)
from blockchainpype.dapps.router.models import SwapMode, SwapRoute
from blockchainpype.evm.blockchain.blockchain import (
    EthereumBlockchain,
    EthereumBlockchainType,
)
from blockchainpype.evm.dapp.uniswap.v2 import UniswapV2
from blockchainpype.evm.dapp.uniswap.v3 import UniswapV3
from blockchainpype.evm.wallet.wallet import EthereumWallet

# The canonical IQuoter deployment shared by Uniswap V3 on most networks
DEFAULT_V3_QUOTER_ADDRESS = "0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6"

V3_DEFAULT_FEE_FRACTIONS = [
    Decimal("0.0001"),
    Decimal("0.0005"),
    Decimal("0.003"),
    Decimal("0.01"),
]


class UniswapConfiguration(DexConfiguration):
    """Configuration for the Uniswap DEX facade.

    Attributes:
        intermediate_assets: Assets considered as multi-hop intermediates by
            :meth:`UniswapDEX.find_best_route` (e.g. WETH) and as the default
            candidate set for :meth:`UniswapDEX.get_supported_pools`
        v3_quoter_address: Override for the V3 quoter contract address; when
            None the canonical deployment address is used
    """

    intermediate_assets: list[BlockchainAsset] = Field(default_factory=list)
    v3_quoter_address: str | None = None

    @classmethod
    def ethereum_mainnet(
        cls,
        intermediate_assets: list[BlockchainAsset] | None = None,
    ) -> "UniswapConfiguration":
        """Create configuration for Ethereum mainnet (V2 + V3)."""
        return cls(
            protocols=[
                ProtocolConfiguration(
                    protocol_name="uniswap_v2",
                    factory_address="0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f",
                    router_address="0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D",
                    fee_tiers=[Decimal("0.003")],  # 0.3% fixed fee
                ),
                ProtocolConfiguration(
                    protocol_name="uniswap_v3",
                    factory_address="0x1F98431c8aD98523631AE4a59f267346ea31F984",
                    router_address="0xE592427A0AEce92De3Edee1F18E0157C05861564",
                    fee_tiers=list(V3_DEFAULT_FEE_FRACTIONS),
                ),
            ],
            default_slippage=Decimal("0.005"),  # 0.5%
            default_deadline_minutes=20,
            platform=BlockchainPlatform(
                identifier="ethereum",
                type=EthereumBlockchainType,
                chain_id=1,
            ),
            intermediate_assets=intermediate_assets or [],
        )

    @classmethod
    def polygon_mainnet(
        cls,
        intermediate_assets: list[BlockchainAsset] | None = None,
    ) -> "UniswapConfiguration":
        """Create configuration for Polygon mainnet (V3 only)."""
        return cls(
            protocols=[
                ProtocolConfiguration(
                    protocol_name="uniswap_v3",
                    factory_address="0x1F98431c8aD98523631AE4a59f267346ea31F984",
                    router_address="0xE592427A0AEce92De3Edee1F18E0157C05861564",
                    fee_tiers=list(V3_DEFAULT_FEE_FRACTIONS),
                )
            ],
            default_slippage=Decimal("0.005"),
            default_deadline_minutes=20,
            platform=BlockchainPlatform(
                identifier="polygon",
                type=EthereumBlockchainType,
                chain_id=137,
            ),
            intermediate_assets=intermediate_assets or [],
        )

    @classmethod
    def local_network(
        cls,
        platform: BlockchainPlatform,
        *,
        v2_factory_address: str | None = None,
        v2_router_address: str | None = None,
        v3_factory_address: str | None = None,
        v3_router_address: str | None = None,
        v3_quoter_address: str | None = None,
        v3_fee_tiers: list[Decimal] | None = None,
        default_slippage: Decimal = Decimal("0.005"),
        default_deadline_minutes: int = 20,
        intermediate_assets: list[BlockchainAsset] | None = None,
    ) -> "UniswapConfiguration":
        """Create configuration for a local/custom network (e.g. hardhat).

        Provide the deployed factory+router address pair for each protocol
        version to enable; at least one version is required.

        Raises:
            ValueError: If no complete factory+router address pair is provided
        """
        protocols: list[ProtocolConfiguration] = []
        if v2_factory_address and v2_router_address:
            protocols.append(
                ProtocolConfiguration(
                    protocol_name="uniswap_v2",
                    factory_address=v2_factory_address,
                    router_address=v2_router_address,
                    fee_tiers=[Decimal("0.003")],
                )
            )
        if v3_factory_address and v3_router_address:
            protocols.append(
                ProtocolConfiguration(
                    protocol_name="uniswap_v3",
                    factory_address=v3_factory_address,
                    router_address=v3_router_address,
                    fee_tiers=v3_fee_tiers or list(V3_DEFAULT_FEE_FRACTIONS),
                )
            )
        if not protocols:
            raise ValueError(
                "At least one Uniswap V2/V3 factory+router address pair "
                "must be provided"
            )
        return cls(
            protocols=protocols,
            default_slippage=default_slippage,
            default_deadline_minutes=default_deadline_minutes,
            platform=platform,
            intermediate_assets=intermediate_assets or [],
            v3_quoter_address=v3_quoter_address,
        )


class UniswapDEX(DecentralizedExchange):
    """Uniswap decentralized exchange facade supporting V2 and V3.

    All protocol contracts are bound directly to the ``blockchain`` instance
    passed at construction (carrying its real platform), so the facade works
    against any :class:`EthereumBlockchain` — mainnet, Polygon, or a local
    hardhat node — without global operator registration.
    """

    def __init__(
        self,
        blockchain: EthereumBlockchain,
        configuration: UniswapConfiguration | None = None,
        wallet: EthereumWallet | None = None,
    ) -> None:
        """Initialize the facade against a blockchain.

        Args:
            blockchain: The blockchain instance all strategies operate on
            configuration: Explicit configuration; when None it is
                auto-detected from the blockchain's chain id (Ethereum and
                Polygon mainnet only)
            wallet: Optional wallet bound to every strategy for transaction
                building; it can also be bound later via :meth:`set_wallet`

        Raises:
            ValueError: If no configuration is given and the chain id has no
                default, or the configuration platform does not match the
                blockchain platform
        """
        self._ethereum_blockchain = blockchain
        self._initial_wallet = wallet

        if configuration is None:
            if blockchain.platform.chain_id == 1:  # Ethereum mainnet
                configuration = UniswapConfiguration.ethereum_mainnet()
            elif blockchain.platform.chain_id == 137:  # Polygon mainnet
                configuration = UniswapConfiguration.polygon_mainnet()
            else:
                raise ValueError(
                    f"No default Uniswap configuration for chain ID "
                    f"{blockchain.platform.chain_id}; pass an explicit "
                    f"UniswapConfiguration (see UniswapConfiguration.local_network)"
                )
            if configuration.platform != blockchain.platform:
                configuration = configuration.model_copy(
                    update={"platform": blockchain.platform}
                )
        elif configuration.platform != blockchain.platform:
            raise ValueError(
                f"Configuration platform {configuration.platform.identifier!r} "
                f"does not match blockchain platform "
                f"{blockchain.platform.identifier!r}"
            )

        super().__init__(configuration)

    def initialize_blockchain(self) -> EthereumBlockchain:
        """Bind to the explicitly provided blockchain (no factory lookup)."""
        return self._ethereum_blockchain

    @property
    def blockchain(self) -> EthereumBlockchain:
        """The Ethereum blockchain instance this DEX operates on."""
        return self._ethereum_blockchain

    @property
    def configuration(self) -> UniswapConfiguration:
        """The Uniswap configuration."""
        return cast(UniswapConfiguration, super().configuration)

    # === Strategy wiring ===

    def _initialize_protocols(self) -> None:
        """Initialize the configured Uniswap protocol strategies.

        Raises:
            ValueError: If a configured protocol name is not supported
        """
        for protocol_config in self.configuration.protocols:
            strategy: ProtocolImplementation
            if protocol_config.protocol_name == "uniswap_v2":
                strategy = UniswapV2(
                    blockchain=self.blockchain,
                    factory_address=protocol_config.factory_address,
                    router_address=protocol_config.router_address,
                    wallet=self._initial_wallet,
                )
            elif protocol_config.protocol_name == "uniswap_v3":
                strategy = UniswapV3(
                    blockchain=self.blockchain,
                    factory_address=protocol_config.factory_address,
                    router_address=protocol_config.router_address,
                    quoter_address=self._get_quoter_address(),
                    fee_tiers=[
                        UniswapV3.fee_tier_from_fraction(fraction)
                        for fraction in protocol_config.fee_tiers
                    ],
                    wallet=self._initial_wallet,
                )
            else:
                raise ValueError(
                    f"Unsupported Uniswap protocol: {protocol_config.protocol_name}"
                )
            self._protocol_strategies[protocol_config.protocol_name] = strategy

    def _get_quoter_address(self) -> str:
        """Get the V3 quoter address (configuration override or canonical)."""
        if self.configuration.v3_quoter_address is not None:
            return self.configuration.v3_quoter_address
        return DEFAULT_V3_QUOTER_ADDRESS

    def set_wallet(self, wallet: BlockchainWallet | None) -> None:
        """Bind (or unbind, with ``None``) a wallet on every strategy."""
        self._initial_wallet = cast(EthereumWallet | None, wallet)
        for strategy in self._protocol_strategies.values():
            strategy.set_wallet(wallet)

    # === Routing ===

    def _selected_strategies(
        self, protocol: str | None
    ) -> dict[str, ProtocolImplementation]:
        """Return the strategies selected by ``protocol`` (all when None).

        Raises:
            ValueError: If the named protocol is not registered
        """
        if protocol is None:
            return dict(self._protocol_strategies)
        if protocol not in self._protocol_strategies:
            raise ValueError(f"Unsupported protocol: {protocol}")
        return {protocol: self._protocol_strategies[protocol]}

    async def _quote_asset_path(
        self,
        strategy: UniswapV2 | UniswapV3,
        assets: Sequence[BlockchainAsset],
        amount: Decimal,
        mode: SwapMode,
        max_slippage: Decimal,
    ) -> SwapRoute:
        """Quote a fixed asset path hop by hop on a single strategy.

        For EXACT_INPUT the hops are quoted forward (each hop's output feeds
        the next); for EXACT_OUTPUT backward (each hop's required input feeds
        the previous). The per-hop quotes are composed into one route by the
        strategy, which also records any protocol-specific per-hop data
        (e.g. V3 fee tiers).
        """
        hop_routes: list[SwapRoute] = []
        if mode == SwapMode.EXACT_INPUT:
            current_amount = amount
            for hop_input, hop_output in pairwise(assets):
                hop_route = await strategy.quote_swap(
                    hop_input,
                    hop_output,
                    current_amount,
                    SwapMode.EXACT_INPUT,
                    max_slippage=max_slippage,
                )
                hop_routes.append(hop_route)
                current_amount = hop_route.output_amount
        elif mode == SwapMode.EXACT_OUTPUT:
            current_amount = amount
            for hop_input, hop_output in reversed(list(pairwise(assets))):
                hop_route = await strategy.quote_swap(
                    hop_input,
                    hop_output,
                    current_amount,
                    SwapMode.EXACT_OUTPUT,
                    max_slippage=max_slippage,
                )
                hop_routes.insert(0, hop_route)
                current_amount = hop_route.input_amount
        else:
            raise ValueError(f"Unsupported swap mode: {mode}")

        return strategy.compose_multi_hop_route(hop_routes, mode, max_slippage)

    async def _collect_direct_candidates(
        self,
        strategies: dict[str, ProtocolImplementation],
        input_asset: BlockchainAsset,
        output_asset: BlockchainAsset,
        amount: Decimal,
        mode: SwapMode,
        max_slippage: Decimal,
    ) -> tuple[list[SwapRoute], Exception | None]:
        """Quote the direct pair on every strategy, tolerating failures."""
        candidates: list[SwapRoute] = []
        first_error: Exception | None = None
        for name, strategy in strategies.items():
            try:
                candidates.append(
                    await strategy.quote_swap(
                        input_asset,
                        output_asset,
                        amount,
                        mode,
                        max_slippage=max_slippage,
                    )
                )
            except Exception as error:
                self.logger().debug("No direct route on protocol '%s': %s", name, error)
                if first_error is None:
                    first_error = error
        return candidates, first_error

    async def _collect_multi_hop_candidates(
        self,
        strategies: dict[str, ProtocolImplementation],
        input_asset: BlockchainAsset,
        output_asset: BlockchainAsset,
        amount: Decimal,
        mode: SwapMode,
        max_slippage: Decimal,
        max_hops: int,
    ) -> tuple[list[SwapRoute], Exception | None]:
        """Quote routes through the configured intermediates, per strategy."""
        candidates: list[SwapRoute] = []
        first_error: Exception | None = None
        intermediates = [
            asset
            for asset in self.configuration.intermediate_assets
            if asset != input_asset and asset != output_asset
        ]
        for intermediate_count in range(1, max_hops):
            if intermediate_count > len(intermediates):
                break
            for middle_assets in permutations(intermediates, intermediate_count):
                asset_path: list[BlockchainAsset] = [
                    input_asset,
                    *middle_assets,
                    output_asset,
                ]
                for name, strategy in strategies.items():
                    if not isinstance(strategy, UniswapV2 | UniswapV3):
                        continue
                    try:
                        candidates.append(
                            await self._quote_asset_path(
                                strategy, asset_path, amount, mode, max_slippage
                            )
                        )
                    except Exception as error:
                        self.logger().debug(
                            "No route through %s on protocol '%s': %s",
                            [str(asset.identifier) for asset in middle_assets],
                            name,
                            error,
                        )
                        if first_error is None:
                            first_error = error
        return candidates, first_error

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

        Considers the direct pair on every selected protocol plus multi-hop
        routes through ``configuration.intermediate_assets`` (each candidate
        route stays on a single protocol so it is executable as one
        transaction). ``max_hops`` bounds the route length: 1 restricts to
        direct swaps, N allows up to N-1 intermediates.

        Raises:
            ValueError: If ``max_hops < 1``, the named protocol is unknown,
                or no candidate route can be quoted (the first quoting error
                is re-raised when available)
        """
        if max_hops < 1:
            raise ValueError("max_hops must be at least 1")
        strategies = self._selected_strategies(protocol)
        max_slippage = self.configuration.default_slippage

        candidates, first_error = await self._collect_direct_candidates(
            strategies, input_asset, output_asset, amount, mode, max_slippage
        )
        hop_candidates, hop_error = await self._collect_multi_hop_candidates(
            strategies, input_asset, output_asset, amount, mode, max_slippage, max_hops
        )
        candidates.extend(hop_candidates)
        if first_error is None:
            first_error = hop_error

        best_route: SwapRoute | None = None
        for candidate in candidates:
            if best_route is None or self._is_better_quote(candidate, best_route, mode):
                best_route = candidate

        if best_route is None:
            if first_error is not None:
                raise first_error
            raise ValueError("No valid route found")
        return best_route

    # === Pool discovery ===

    async def get_supported_pools(
        self,
        protocol: str | None = None,
        candidate_assets: Sequence[BlockchainAsset] | None = None,
    ) -> Sequence[tuple[BlockchainAsset, BlockchainAsset]]:
        """Enumerate existing pools among a candidate asset set.

        Uniswap factories expose no pair enumeration cheap enough for client
        calls, so discovery is scoped to explicit candidates: every unordered
        pair of ``candidate_assets`` (defaulting to
        ``configuration.intermediate_assets``) is checked against the selected
        protocols — V2 via ``getPair``, V3 via ``getPool`` per configured fee
        tier — and included once if any pool exists.

        Raises:
            ValueError: If fewer than two candidate assets are available or
                the named protocol is unknown
        """
        if candidate_assets is None:
            candidate_assets = self.configuration.intermediate_assets
        if len(candidate_assets) < 2:
            raise ValueError(
                "get_supported_pools requires at least two candidate assets; "
                "pass candidate_assets or configure intermediate_assets"
            )
        strategies = self._selected_strategies(protocol)

        pools: list[tuple[BlockchainAsset, BlockchainAsset]] = []
        for index, asset_a in enumerate(candidate_assets):
            for asset_b in candidate_assets[index + 1 :]:
                if asset_a == asset_b:
                    continue
                for strategy in strategies.values():
                    if not isinstance(strategy, UniswapV2 | UniswapV3):
                        continue
                    if await strategy.pool_exists(asset_a, asset_b):
                        pools.append((asset_a, asset_b))
                        break
        return pools
