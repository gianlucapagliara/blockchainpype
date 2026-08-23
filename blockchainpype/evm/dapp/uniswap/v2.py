"""
Uniswap V2 protocol strategy.

This module implements the :class:`ProtocolImplementation` contract for
Uniswap V2 style constant-product pools: on-chain pair discovery, quoting with
the exact Uniswap V2 integer formulas over raw reserves, and building (or
signing and broadcasting) router swap transactions through a bound
:class:`EthereumWallet`.
"""

import uuid
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
from blockchainpype.evm.dapp.contract import (
    EthereumContractConfiguration,
    EthereumSmartContract,
)
from blockchainpype.evm.dapp.unsigned import build_unsigned_transaction
from blockchainpype.evm.transaction import EthereumTransaction
from blockchainpype.evm.wallet.wallet import EthereumWallet

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


async def ensure_asset_data(asset: BlockchainAsset) -> EthereumAsset:
    """Cast ``asset`` to :class:`EthereumAsset` and lazily initialize its data.

    Args:
        asset: The DEX-agnostic asset handed in through the protocol contract

    Returns:
        EthereumAsset: The same asset with ``data`` guaranteed to be populated

    Raises:
        ValueError: If the asset data cannot be initialized
    """
    ethereum_asset = cast(EthereumAsset, asset)
    if ethereum_asset.data is None:
        await ethereum_asset.initialize_data()
    if ethereum_asset.data is None:
        raise ValueError(
            f"Could not initialize data for asset {ethereum_asset.address.string}"
        )
    return ethereum_asset


def asset_decimals(asset: EthereumAsset) -> int:
    """Return the asset's decimals, requiring initialized asset data."""
    if asset.data is None:
        raise ValueError(
            f"Asset data is not initialized for {asset.address.string}; "
            "call initialize_data() first"
        )
    return int(asset.data.decimals)


def asset_symbol(asset: EthereumAsset) -> str:
    """Return the asset's symbol, falling back to its address."""
    if asset.data is not None:
        return str(asset.data.symbol)
    return asset.address.string


class BlockchainBoundContract(EthereumSmartContract):
    """An :class:`EthereumSmartContract` bound to an explicit blockchain.

    The stock smart contract resolves its blockchain operator from the global
    ``OperatorFactory`` using ``configuration.platform``. The Uniswap
    strategies instead bind every contract (factory, router, quoter, pair,
    pool) directly to the blockchain instance they were constructed with, so
    any :class:`EthereumBlockchain` — mainnet, Polygon, or a hardhat/local
    node — works without requiring a global factory registration, while the
    configuration still carries the real ``blockchain.platform``.
    """

    def __init__(
        self,
        configuration: EthereumContractConfiguration,
        blockchain: EthereumBlockchain,
    ) -> None:
        self._bound_blockchain = blockchain
        super().__init__(configuration)

    def initialize_blockchain(self) -> EthereumBlockchain:
        """Return the explicitly bound blockchain instead of a factory lookup."""
        return self._bound_blockchain


class UniswapV2(ProtocolImplementation):
    """Uniswap V2 protocol strategy fulfilling :class:`ProtocolImplementation`.

    Quotes are computed with the exact Uniswap V2 integer formulas over raw
    (smallest-unit) reserves; the public :meth:`get_reserves` returns
    decimal-adjusted amounts per the protocol contract. Transactions are built
    through the bound wallet and returned unsigned; the convenience methods
    :meth:`create_swap_transaction` / :meth:`execute_swap` sign and broadcast.
    """

    PROTOCOL_NAME = "uniswap_v2"
    FEE_FRACTION = Decimal("0.003")  # Uniswap V2 protocol-constant 0.3% fee
    DEFAULT_MAX_SLIPPAGE = Decimal("0.005")
    DEFAULT_DEADLINE_MINUTES = 20

    def __init__(
        self,
        blockchain: EthereumBlockchain,
        factory_address: str,
        router_address: str,
        wallet: EthereumWallet | None = None,
    ) -> None:
        """Initialize the strategy against a specific blockchain.

        Args:
            blockchain: The blockchain every contract call is routed through;
                its platform is threaded into all contract configurations
            factory_address: The Uniswap V2 factory contract address
            router_address: The Uniswap V2 router (Router02) contract address
            wallet: Optional wallet used to build/sign transactions; it can
                also be bound later via :meth:`set_wallet`
        """
        self.blockchain = blockchain
        self.factory_address = factory_address
        self.router_address = router_address
        self._wallet = wallet
        self._pair_contracts: dict[str, BlockchainBoundContract] = {}

        self.factory_contract = BlockchainBoundContract(
            EthereumContractConfiguration(
                address=EthereumAddress.from_string(factory_address),
                abi_configuration=EthereumLocalFileABI(
                    file_name="UniswapV2Factory.json"
                ),
                platform=blockchain.platform,
            ),
            blockchain=blockchain,
        )
        self.router_contract = BlockchainBoundContract(
            EthereumContractConfiguration(
                address=EthereumAddress.from_string(router_address),
                abi_configuration=EthereumLocalFileABI(
                    file_name="UniswapV2Router02.json"
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
                f"UniswapV2 requires an EthereumWallet, got {type(wallet).__name__}"
            )
        self._wallet = wallet

    def _require_wallet(self) -> EthereumWallet:
        """Return the bound wallet or raise per the protocol contract."""
        if self._wallet is None:
            raise ValueError(
                "No wallet is bound to this Uniswap V2 strategy; "
                "call set_wallet() before building transactions"
            )
        return self._wallet

    # === Contract plumbing ===

    async def _ensure_contracts_initialized(self) -> None:
        """Ensure factory and router contracts are initialized."""
        if not self.factory_contract.is_initialized:
            await self.factory_contract.initialize()
        if not self.router_contract.is_initialized:
            await self.router_contract.initialize()

    async def _get_pair(self, asset_a: EthereumAsset, asset_b: EthereumAsset) -> str:
        """Get the pair address for two assets (zero address when absent)."""
        await self._ensure_contracts_initialized()
        result: str = await self.factory_contract.functions.getPair(
            asset_a.address.raw, asset_b.address.raw
        ).call()
        return result

    async def _get_pair_contract(self, pair_address: str) -> BlockchainBoundContract:
        """Get (and cache) an initialized pair contract for ``pair_address``."""
        contract = self._pair_contracts.get(pair_address)
        if contract is None:
            contract = BlockchainBoundContract(
                EthereumContractConfiguration(
                    address=EthereumAddress.from_string(pair_address),
                    abi_configuration=EthereumLocalFileABI(
                        file_name="UniswapV2Pair.json"
                    ),
                    platform=self.blockchain.platform,
                ),
                blockchain=self.blockchain,
            )
            self._pair_contracts[pair_address] = contract
        if not contract.is_initialized:
            await contract.initialize()
        return contract

    async def pool_exists(
        self, asset_a: BlockchainAsset, asset_b: BlockchainAsset
    ) -> bool:
        """Check whether a Uniswap V2 pair exists for the two assets."""
        pair_address = await self._get_pair(
            cast(EthereumAsset, asset_a), cast(EthereumAsset, asset_b)
        )
        return bool(pair_address) and pair_address != ZERO_ADDRESS

    # === Constant-product math (exact Uniswap V2 integer formulas) ===

    @staticmethod
    def get_amount_out(amount_in: int, reserve_in: int, reserve_out: int) -> int:
        """Compute the output amount for an exact input (all raw integers).

        Implements the canonical UniswapV2Library.getAmountOut formula:
        ``out = in*997*reserveOut // (reserveIn*1000 + in*997)``.

        Raises:
            ValueError: If the amount is not positive or reserves are empty
        """
        if amount_in <= 0:
            raise ValueError("Input amount must be positive")
        if reserve_in <= 0 or reserve_out <= 0:
            raise ValueError("Insufficient liquidity: pool reserves must be positive")
        amount_in_with_fee = amount_in * 997
        numerator = amount_in_with_fee * reserve_out
        denominator = reserve_in * 1000 + amount_in_with_fee
        return numerator // denominator

    @staticmethod
    def get_amount_in(amount_out: int, reserve_in: int, reserve_out: int) -> int:
        """Compute the input required for an exact output (all raw integers).

        Implements the canonical UniswapV2Library.getAmountIn formula:
        ``in = reserveIn*out*1000 // ((reserveOut - out)*997) + 1``.

        Raises:
            ValueError: If the amount is not positive, reserves are empty, or
                the requested output is not available in the pool
        """
        if amount_out <= 0:
            raise ValueError("Output amount must be positive")
        if reserve_in <= 0 or reserve_out <= 0:
            raise ValueError("Insufficient liquidity: pool reserves must be positive")
        if amount_out >= reserve_out:
            raise ValueError(
                "Insufficient liquidity: requested output exceeds pool reserves"
            )
        numerator = reserve_in * amount_out * 1000
        denominator = (reserve_out - amount_out) * 997
        return numerator // denominator + 1

    # === Reserves ===

    async def get_raw_reserves(
        self,
        asset_a: BlockchainAsset,
        asset_b: BlockchainAsset,
    ) -> tuple[int, int]:
        """Get the pair reserves in raw (smallest) units, oriented (a, b).

        Raises:
            ValueError: If no pair exists for the two assets
        """
        ethereum_a = cast(EthereumAsset, asset_a)
        ethereum_b = cast(EthereumAsset, asset_b)

        pair_address = await self._get_pair(ethereum_a, ethereum_b)
        if not pair_address or pair_address == ZERO_ADDRESS:
            raise ValueError(
                f"No Uniswap V2 pair found for "
                f"{asset_symbol(ethereum_a)}/{asset_symbol(ethereum_b)}"
            )

        pair_contract = await self._get_pair_contract(pair_address)
        reserve0, reserve1, _ = await pair_contract.functions.getReserves().call()
        token0: str = await pair_contract.functions.token0().call()

        if ethereum_a.address.raw.lower() == token0.lower():
            return int(reserve0), int(reserve1)
        return int(reserve1), int(reserve0)

    async def get_reserves(
        self,
        asset_a: BlockchainAsset,
        asset_b: BlockchainAsset,
    ) -> tuple[Decimal, Decimal]:
        """Get the current pair reserves in decimal (non-raw) units."""
        ethereum_a = await ensure_asset_data(asset_a)
        ethereum_b = await ensure_asset_data(asset_b)
        raw_a, raw_b = await self.get_raw_reserves(ethereum_a, ethereum_b)
        return (
            ethereum_a.convert_to_decimals(raw_a),
            ethereum_b.convert_to_decimals(raw_b),
        )

    # === Quoting ===

    async def quote_swap(
        self,
        input_asset: BlockchainAsset,
        output_asset: BlockchainAsset,
        amount: Decimal,
        mode: SwapMode = SwapMode.EXACT_INPUT,
        max_slippage: Decimal | None = None,
    ) -> SwapRoute:
        """Quote a single-hop swap using the exact V2 constant-product math.

        The math runs entirely on raw integer amounts and raw reserves; the
        returned route carries decimal-adjusted amounts. ``max_slippage`` is
        embedded in the route (defaulting to ``DEFAULT_MAX_SLIPPAGE`` when the
        caller — normally the DEX facade — does not forward one).

        Raises:
            ValueError: If the amount is not positive, no pair exists, or
                liquidity is insufficient
        """
        if amount <= 0:
            raise ValueError("Swap amount must be positive")
        slippage = (
            max_slippage if max_slippage is not None else self.DEFAULT_MAX_SLIPPAGE
        )

        ethereum_input = await ensure_asset_data(input_asset)
        ethereum_output = await ensure_asset_data(output_asset)

        reserve_in, reserve_out = await self.get_raw_reserves(
            ethereum_input, ethereum_output
        )

        if mode == SwapMode.EXACT_INPUT:
            input_amount = amount
            input_amount_raw = ethereum_input.convert_to_raw(amount)
            output_amount_raw = self.get_amount_out(
                input_amount_raw, reserve_in, reserve_out
            )
            if output_amount_raw <= 0:
                raise ValueError("Swap amount too small: computed output is zero")
            output_amount = ethereum_output.convert_to_decimals(output_amount_raw)
        elif mode == SwapMode.EXACT_OUTPUT:
            output_amount = amount
            output_amount_raw = ethereum_output.convert_to_raw(amount)
            input_amount_raw = self.get_amount_in(
                output_amount_raw, reserve_in, reserve_out
            )
            input_amount = ethereum_input.convert_to_decimals(input_amount_raw)
        else:
            raise ValueError(f"Unsupported swap mode: {mode}")

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
            taxes=self.FEE_FRACTION,
            protocol=self.PROTOCOL_NAME,
        )

    def compose_multi_hop_route(
        self,
        hop_routes: Sequence[SwapRoute],
        mode: SwapMode,
        max_slippage: Decimal,
    ) -> SwapRoute:
        """Compose per-hop quotes into a single multi-hop V2 route.

        The hops must already chain (each hop's output asset/amount feeding the
        next hop's input); :meth:`UniswapDEX.find_best_route` produces them by
        quoting hop by hop.

        Raises:
            ValueError: If no hops are given or a hop belongs to another
                protocol
        """
        if not hop_routes:
            raise ValueError("At least one hop route is required")
        for hop_route in hop_routes:
            if hop_route.protocol != self.PROTOCOL_NAME:
                raise ValueError(
                    f"Cannot compose a {self.PROTOCOL_NAME} route from a "
                    f"'{hop_route.protocol}' hop"
                )

        sequence = [hop for hop_route in hop_routes for hop in hop_route.sequence]
        taxes = sum((hop_route.taxes for hop_route in hop_routes), Decimal(0))
        return SwapRoute(
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

        # Multi-hop path: first hop input plus every hop output
        path: list[str] = [
            cast(EthereumAsset, route.sequence[0].input_asset).address.raw
        ]
        for hop in route.sequence:
            path.append(cast(EthereumAsset, hop.output_asset).address.raw)

        if deadline_minutes is None:
            deadline_minutes = self.DEFAULT_DEADLINE_MINUTES
        deadline = int(self.blockchain.current_timestamp) + deadline_minutes * 60

        if recipient is None:
            recipient = wallet.address.raw

        await self._ensure_contracts_initialized()

        one = Decimal(1)
        if route.mode == SwapMode.EXACT_INPUT:
            amount_in = ethereum_input.convert_to_raw(route.input_amount)
            min_amount_out = ethereum_output.convert_to_raw(
                route.output_amount * (one - route.max_slippage)
            )
            function = self.router_contract.functions.swapExactTokensForTokens(
                amount_in, min_amount_out, path, recipient, deadline
            )
        elif route.mode == SwapMode.EXACT_OUTPUT:
            amount_out = ethereum_output.convert_to_raw(route.output_amount)
            max_amount_in = ethereum_input.convert_to_raw(
                route.input_amount * (one + route.max_slippage)
            )
            function = self.router_contract.functions.swapTokensForExactTokens(
                amount_out, max_amount_in, path, recipient, deadline
            )
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
