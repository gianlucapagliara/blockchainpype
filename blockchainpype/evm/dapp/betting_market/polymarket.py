"""
Polymarket betting market implementation (Polygon).

Polymarket's architecture is hybrid and this strategy mirrors it:

* **Off-chain trading**: buy/sell orders are EIP-712-signed and posted to the
  CLOB REST API (:mod:`blockchainpype.evm.dapp.betting_market.clob`); they are
  NOT on-chain transactions. :meth:`Polymarket.place_buy` /
  :meth:`Polymarket.place_sell` post orders directly, while the
  facade-facing ``build_buy_transaction`` / ``build_sell_transaction`` sign the
  CLOB order and wrap it in an (unsigned, never-broadcastable-on-chain)
  :class:`EthereumTransaction` tracking object carrying the signed order in
  ``other_data["clob_order"]`` — see their docstrings.
* **On-chain settlement**: redeeming winnings goes through
  ``ConditionalTokens.redeemPositions`` and allowances through USDC
  ``approve`` / ConditionalTokens ``setApprovalForAll``, built through the
  bound :class:`EthereumWallet`.

Read paths are wallet-less and use the real public APIs:
CLOB (``clob.polymarket.com``) for markets/books/prices, Gamma
(``gamma-api.polymarket.com``) for market discovery/metadata and the Data API
(``data-api.polymarket.com``) for user positions.
"""

import json
import uuid
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from types import TracebackType
from typing import Any, Final, Self, cast

import aiohttp
from financepype.operations.transactions.models import BlockchainTransactionState
from financepype.owners.wallet import BlockchainWallet
from pydantic import model_validator
from web3.types import TxParams

from blockchainpype.dapps.betting_market import (
    BettingMarketConfiguration,
    BettingMarketModel,
    BettingPosition,
    MarketOutcome,
    MarketStatus,
    OutcomeToken,
    ProtocolConfiguration,
    ProtocolImplementation,
)
from blockchainpype.dapps.betting_market.betting_market import BettingMarket
from blockchainpype.evm.asset import EthereumAssetData
from blockchainpype.evm.blockchain.blockchain import EthereumBlockchain
from blockchainpype.evm.blockchain.identifier import EthereumAddress
from blockchainpype.evm.dapp.abi import EthereumLocalFileABI
from blockchainpype.evm.dapp.betting_market.clob import (
    ClobClient,
    ClobCredentials,
    ClobOrder,
    OrderPostResponse,
    OrderSide,
    SignatureType,
    SignedClobOrder,
    compute_order_amounts,
    generate_salt,
    serialize_query_params,
    sign_clob_order,
)
from blockchainpype.evm.dapp.contract import (
    EthereumContractConfiguration,
    EthereumSmartContract,
)
from blockchainpype.evm.dapp.erc20 import (
    ERC20Contract,
    ERC20ContractConfiguration,
    ERC20Token,
)
from blockchainpype.evm.transaction import EthereumTransaction
from blockchainpype.evm.wallet.wallet import EthereumWallet

# === Polygon mainnet deployment addresses ===

POLYGON_CHAIN_ID: Final[int] = 137
CONDITIONAL_TOKENS_ADDRESS: Final[str] = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
USDC_ADDRESS: Final[str] = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CTF_EXCHANGE_ADDRESS: Final[str] = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
USDC_DECIMALS: Final[int] = 6

#: Index sets redeemed for a binary market: 0b01 (first slot) and 0b10
#: (second slot). ``redeemPositions`` pays out only for held balances, so
#: redeeming both slots is always safe.
BINARY_INDEX_SETS: Final[tuple[int, int]] = (1, 2)

#: Version tag of the market/position payload parsers below. Bump when the
#: parsed Gamma/CLOB/Data-API schemas change; it is embedded in every parsed
#: model's ``metadata["parser_version"]``.
MARKET_PARSER_VERSION: Final[str] = "2025-08"

#: Epoch fallback for payloads that carry no creation date (the CLOB market
#: payload does not include one).
_EPOCH: Final[datetime] = datetime.fromtimestamp(0, tz=UTC)


class PolymarketConfiguration(ProtocolConfiguration):
    """Configuration specific to the Polymarket protocol.

    Attributes:
        api_base_url: CLOB REST API base (order books, prices, orders)
        gamma_api_url: Gamma API base (market discovery/metadata)
        data_api_url: Data API base (user positions)
        conditional_tokens_address: Gnosis ConditionalTokens (ERC-1155)
        collateral_token_address: USDC on Polygon
        ctf_exchange_address: CTF Exchange (EIP-712 verifying contract)
        neg_risk_ctf_exchange_address: Neg-risk exchange (reserved, unused)
        neg_risk_adapter_address: Neg-risk adapter (reserved, unused)
        credentials: Optional L2 API credentials; without them all read
            endpoints still work but orders cannot be posted
        signature_type: How order signatures are verified (EOA by default)
        default_tick_size: Price tick used when building orders
        default_order_type: CLOB order type used by ``place_buy``/``place_sell``
    """

    api_base_url: str = "https://clob.polymarket.com"
    gamma_api_url: str = "https://gamma-api.polymarket.com"
    data_api_url: str = "https://data-api.polymarket.com"
    conditional_tokens_address: str = CONDITIONAL_TOKENS_ADDRESS
    collateral_token_address: str = USDC_ADDRESS
    ctf_exchange_address: str = CTF_EXCHANGE_ADDRESS
    neg_risk_ctf_exchange_address: str = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
    neg_risk_adapter_address: str = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
    credentials: ClobCredentials | None = None
    signature_type: SignatureType = SignatureType.EOA
    default_tick_size: Decimal = Decimal("0.001")
    default_order_type: str = "GTC"

    @model_validator(mode="before")
    @classmethod
    def _apply_polymarket_defaults(cls, data: Any) -> Any:
        """Default ``protocol_name`` and ``contract_address`` for Polymarket."""
        if isinstance(data, dict):
            data = dict(data)
            data.setdefault("protocol_name", "Polymarket")
            if "contract_address" not in data:
                data["contract_address"] = data.get(
                    "ctf_exchange_address", CTF_EXCHANGE_ADDRESS
                )
        return data


class EVMBettingMarketConfiguration(BettingMarketConfiguration):
    """EVM-specific betting market configuration."""

    pass


class BlockchainBoundContract(EthereumSmartContract):
    """An :class:`EthereumSmartContract` bound to an explicit blockchain.

    The stock smart contract resolves its blockchain operator from the global
    ``OperatorFactory`` using ``configuration.platform``. The Polymarket
    strategy instead binds every contract (ConditionalTokens, CTF Exchange,
    USDC) directly to the blockchain instance it was constructed with, so any
    :class:`EthereumBlockchain` — Polygon mainnet or a local fork — works
    without requiring a global factory registration, while the configuration
    still carries the real ``blockchain.platform``.
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


class BlockchainBoundERC20Contract(ERC20Contract):
    """An :class:`ERC20Contract` bound to an explicit blockchain.

    Same rationale as :class:`BlockchainBoundContract`, for the USDC
    collateral token contract (which needs the ERC-20 helpers such as
    ``place_approve``).
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


class Polymarket(ProtocolImplementation):
    """Polymarket strategy fulfilling the betting-market protocol contract.

    Wallet binding follows the shared strategy contract: write paths require
    a bound :class:`EthereumWallet` (bind at construction or via
    :meth:`set_wallet`) and raise ``ValueError`` when none is bound; all read
    paths work wallet-less.

    Because CLOB orders are off-chain, the buy/sell "transactions" returned
    by ``build_buy_transaction`` / ``build_sell_transaction`` are tracking
    objects wrapping a signed CLOB order (see their docstrings), while
    ``build_redeem_transaction`` returns a genuine unsigned on-chain
    transaction. Configuration knobs wired here:

    * ``ProtocolConfiguration.fee_rate`` -> ``feeRateBps`` in every order
      (the maximum fee rate the maker accepts).
    * ``BettingMarketConfiguration.max_gas_price_gwei`` (forwarded by the
      facade as ``max_gas_price_gwei``) caps the gas-fee fields of every
      transaction this strategy builds.
    """

    def __init__(
        self,
        configuration: PolymarketConfiguration,
        blockchain: EthereumBlockchain,
        wallet: EthereumWallet | None = None,
        max_gas_price_gwei: int | None = None,
        clob_client: ClobClient | None = None,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        """Initialize the strategy against a specific blockchain.

        Args:
            configuration: Polymarket protocol configuration
            blockchain: The blockchain used for all on-chain interaction;
                its platform is threaded into every contract configuration
            wallet: Optional wallet for signing orders and transactions;
                can also be bound later via :meth:`set_wallet`
            max_gas_price_gwei: Optional cap applied to the gas-fee fields of
                built transactions (facades forward their configured value)
            clob_client: Optional pre-built CLOB client (dependency injection
                for tests); created from the configuration when omitted
            session: Optional externally managed aiohttp session for the
                Gamma/Data API requests
        """
        self.configuration = configuration
        self.blockchain = blockchain
        self._wallet = wallet
        self._max_gas_price_gwei = max_gas_price_gwei
        self._session = session
        self._owns_clob_client = clob_client is None
        self._order_chain_id = (
            blockchain.platform.chain_id
            if blockchain.platform.chain_id is not None
            else POLYGON_CHAIN_ID
        )
        self._clob = clob_client or ClobClient(
            base_url=configuration.api_base_url,
            chain_id=self._order_chain_id,
            credentials=configuration.credentials,
        )
        self._fee_rate_bps = int(
            (configuration.fee_rate * Decimal(10_000)).to_integral_value(
                rounding=ROUND_HALF_UP
            )
        )
        self._collateral_token: ERC20Token | None = None

        self.conditional_tokens_contract = BlockchainBoundContract(
            EthereumContractConfiguration(
                address=EthereumAddress.from_string(
                    configuration.conditional_tokens_address
                ),
                abi_configuration=EthereumLocalFileABI(
                    file_name="polymarket_conditional_tokens.json"
                ),
                platform=blockchain.platform,
            ),
            blockchain=blockchain,
        )
        self.ctf_exchange_contract = BlockchainBoundContract(
            EthereumContractConfiguration(
                address=EthereumAddress.from_string(configuration.ctf_exchange_address),
                abi_configuration=EthereumLocalFileABI(
                    file_name="polymarket_ctf_exchange.json"
                ),
                platform=blockchain.platform,
            ),
            blockchain=blockchain,
        )

    # === Wallet binding ===

    @property
    def wallet(self) -> EthereumWallet | None:
        """The wallet currently bound for order/transaction building, if any."""
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
                f"Polymarket requires an EthereumWallet, got {type(wallet).__name__}"
            )
        self._wallet = wallet

    def _require_wallet(self) -> EthereumWallet:
        """Return the bound wallet or raise per the protocol contract."""
        if self._wallet is None:
            raise ValueError(
                "No wallet is bound to this Polymarket strategy; "
                "call set_wallet() before building orders or transactions"
            )
        return self._wallet

    @staticmethod
    def _require_wallet_address_match(
        user_address: str, wallet: EthereumWallet
    ) -> None:
        """Reject build requests for an address other than the bound wallet's."""
        if user_address and user_address.lower() != wallet.address.raw.lower():
            raise ValueError(
                f"user_address {user_address} does not match the bound wallet "
                f"address {wallet.address.raw}"
            )

    # === Collateral asset ===

    @property
    def collateral_token(self) -> ERC20Token:
        """The USDC collateral token as a real :class:`ERC20Token` asset.

        USDC metadata (6 decimals) is a protocol constant on Polygon, so the
        asset data is populated statically instead of being fetched on-chain.
        """
        if self._collateral_token is None:
            address = EthereumAddress.from_string(
                self.configuration.collateral_token_address
            )
            contract = BlockchainBoundERC20Contract(
                ERC20ContractConfiguration(
                    address=address,
                    platform=self.blockchain.platform,
                ),
                blockchain=self.blockchain,
            )
            self._collateral_token = ERC20Token(
                platform=self.blockchain.platform,
                identifier=address,
                data=EthereumAssetData(
                    name="USD Coin", symbol="USDC", decimals=USDC_DECIMALS
                ),
                contract=contract,
            )
        return self._collateral_token

    # === HTTP plumbing (Gamma / Data API) ===

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or lazily create the HTTP session for Gamma/Data API calls."""
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _get_json(
        self, base_url: str, path: str, params: dict[str, Any] | None = None
    ) -> Any:
        """GET a JSON payload from a Polymarket API host.

        Query parameters are serialized through
        :func:`serialize_query_params` (booleans become ``"true"``/``"false"``
        — aiohttp/yarl reject Python bools).

        Raises:
            ValueError: On transport failures or non-2xx responses
        """
        session = await self._get_session()
        url = f"{base_url}{path}"
        query = serialize_query_params(params) if params else None
        try:
            async with session.get(url, params=query) as response:
                text = await response.text()
                if response.status >= 400:
                    raise ValueError(
                        f"API request GET {url} failed with status "
                        f"{response.status}: {text[:500]}"
                    )
                return json.loads(text) if text else None
        except aiohttp.ClientError as error:
            raise ValueError(f"API request GET {url} failed: {error}") from error

    async def close(self) -> None:
        """Close the HTTP session and the owned CLOB client."""
        if self._session is not None:
            await self._session.close()
            self._session = None
        if self._owns_clob_client:
            await self._clob.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()

    # === Read path ===

    async def get_market(self, market_id: str) -> BettingMarketModel:
        """Get detailed information about a specific market.

        Args:
            market_id: The market's condition id (0x-prefixed 32-byte hex),
                resolved through the CLOB ``GET /markets/{condition_id}``
        """
        market_data = await self._clob.get_market(market_id)
        return self._parse_clob_market(market_data)

    async def get_markets(
        self,
        category: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[BettingMarketModel]:
        """Get available markets from the Gamma API with optional filtering.

        Args:
            category: Optional category filter (forwarded to Gamma)
            status: ``"active"`` maps to ``active=true&closed=false``;
                ``"closed"``/``"resolved"`` map to ``closed=true``
            limit: Maximum number of markets to return
            offset: Number of markets to skip
        """
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if category:
            params["category"] = category
        if status == "active":
            params["active"] = True
            params["closed"] = False
        elif status in ("closed", "resolved"):
            params["closed"] = True

        payload = await self._get_json(
            self.configuration.gamma_api_url, "/markets", params
        )
        # Gamma returns a bare JSON array; tolerate {"data": [...]} wrappers.
        markets_data: list[dict[str, Any]]
        if isinstance(payload, dict):
            markets_data = list(payload.get("data", []))
        else:
            markets_data = list(payload or [])
        return [self._parse_gamma_market(market) for market in markets_data]

    async def get_user_positions(
        self,
        user_address: str,
        market_id: str | None = None,
    ) -> list[BettingPosition]:
        """Get a user's positions from the Data API (``/positions``).

        Args:
            user_address: The user's (proxy) wallet address
            market_id: Optional condition id to filter by
        """
        params: dict[str, Any] = {"user": user_address}
        if market_id:
            params["market"] = market_id

        payload = await self._get_json(
            self.configuration.data_api_url, "/positions", params
        )
        positions_data: list[dict[str, Any]]
        if isinstance(payload, dict):
            positions_data = list(payload.get("data", []))
        else:
            positions_data = list(payload or [])
        return [self._parse_position_data(position) for position in positions_data]

    async def get_outcome_token_price(
        self,
        market_id: str,
        outcome_token_id: str,
    ) -> Decimal:
        """Get the CLOB midpoint price of an outcome token.

        Args:
            market_id: Unused (CLOB token ids are globally unique); kept for
                the protocol contract
            outcome_token_id: The CLOB/ERC-1155 outcome token id
        """
        return await self._clob.get_midpoint(outcome_token_id)

    async def calculate_buy_quote(
        self,
        market_id: str,
        outcome_token_id: str,
        amount: Decimal,
    ) -> tuple[Decimal, Decimal]:
        """Estimate shares received and total cost for a buy.

        Uses the CLOB midpoint as the reference price and the configured
        ``fee_rate`` as the fee estimate.

        Returns:
            Tuple of (expected_shares, total_cost_including_fees)
        """
        price = await self.get_outcome_token_price(market_id, outcome_token_id)
        if price <= 0:
            raise ValueError(
                f"Cannot quote buy for token {outcome_token_id}: price is {price}"
            )
        expected_shares = amount / price
        fee = amount * self.configuration.fee_rate
        return expected_shares, amount + fee

    async def calculate_sell_quote(
        self,
        market_id: str,
        outcome_token_id: str,
        shares: Decimal,
    ) -> tuple[Decimal, Decimal]:
        """Estimate net payout and fees for a sell at the CLOB midpoint.

        Returns:
            Tuple of (net_payout, total_fees)
        """
        price = await self.get_outcome_token_price(market_id, outcome_token_id)
        gross_payout = shares * price
        fee = gross_payout * self.configuration.fee_rate
        return gross_payout - fee, fee

    # === Order building / posting (off-chain write path) ===

    def build_order(
        self,
        outcome_token_id: str,
        side: OrderSide,
        price: Decimal,
        size: Decimal,
        expiration: int = 0,
        nonce: int = 0,
        salt: int | None = None,
        tick_size: Decimal | None = None,
    ) -> SignedClobOrder:
        """Build and EIP-712-sign a CLOB order with the bound wallet.

        Args:
            outcome_token_id: The outcome token id (decimal string)
            side: BUY or SELL
            price: Limit price per share in USDC (strictly between 0 and 1)
            size: Number of outcome shares
            expiration: Unix expiration timestamp; 0 means no expiration
            nonce: Maker's exchange nonce
            salt: Explicit salt (random when omitted)
            tick_size: Market price tick; configuration default when omitted

        Returns:
            The signed order, ready to be posted to the CLOB

        Raises:
            ValueError: If no wallet is bound, the wallet has no signer, or
                price/size are out of range
        """
        wallet = self._require_wallet()
        signer = wallet.signer
        if signer is None:
            raise ValueError(
                "The bound wallet has no signer; a private key is required "
                "to sign CLOB orders"
            )

        maker_amount, taker_amount = compute_order_amounts(
            side,
            price,
            size,
            tick_size
            if tick_size is not None
            else self.configuration.default_tick_size,
        )
        order = ClobOrder(
            salt=salt if salt is not None else generate_salt(),
            maker=wallet.address.raw,
            signer=wallet.address.raw,
            token_id=int(outcome_token_id),
            maker_amount=maker_amount,
            taker_amount=taker_amount,
            expiration=expiration,
            nonce=nonce,
            fee_rate_bps=self._fee_rate_bps,
            side=side,
            signature_type=self.configuration.signature_type,
        )
        return sign_clob_order(
            order,
            chain_id=self._order_chain_id,
            private_key=bytes(signer.key),
            verifying_contract=self.configuration.ctf_exchange_address,
        )

    async def place_buy(
        self,
        outcome_token_id: str,
        price: Decimal,
        size: Decimal,
        order_type: str | None = None,
    ) -> OrderPostResponse:
        """Sign a BUY order and post it to the CLOB.

        Requires a bound wallet (for signing) and configured
        :class:`ClobCredentials` (for the authenticated ``POST /order``).
        """
        signed_order = self.build_order(outcome_token_id, OrderSide.BUY, price, size)
        return await self._clob.post_order(
            signed_order,
            order_type=order_type or self.configuration.default_order_type,
        )

    async def place_sell(
        self,
        outcome_token_id: str,
        price: Decimal,
        size: Decimal,
        order_type: str | None = None,
    ) -> OrderPostResponse:
        """Sign a SELL order and post it to the CLOB."""
        signed_order = self.build_order(outcome_token_id, OrderSide.SELL, price, size)
        return await self._clob.post_order(
            signed_order,
            order_type=order_type or self.configuration.default_order_type,
        )

    async def post_order(
        self, signed_order: SignedClobOrder, order_type: str | None = None
    ) -> OrderPostResponse:
        """Post an already-signed order (e.g. from a built buy/sell) to the CLOB."""
        return await self._clob.post_order(
            signed_order,
            order_type=order_type or self.configuration.default_order_type,
        )

    def _wrap_order_in_transaction(
        self,
        signed_order: SignedClobOrder,
        market_id: str,
        client_operation_id: str,
    ) -> EthereumTransaction:
        """Wrap a signed CLOB order into a tracking :class:`EthereumTransaction`.

        The result is NOT an on-chain transaction: it stays unsigned (as a
        chain transaction) in ``PENDING_BROADCAST`` state and carries the
        signed order payload in ``other_data["clob_order"]``. "Broadcasting"
        it means posting the order via :meth:`post_order` /
        :meth:`ClobClient.post_order`.
        """
        wallet = self._require_wallet()
        return EthereumTransaction(
            client_operation_id=client_operation_id,
            owner_identifier=wallet.identifier,
            creation_timestamp=wallet.current_timestamp,
            current_state=BlockchainTransactionState.PENDING_BROADCAST,
            signed_transaction=None,
            other_data={
                "clob_order": signed_order.to_api_payload(),
                "market_id": market_id,
            },
        )

    async def build_buy_transaction(
        self,
        market_id: str,
        outcome_token_id: str,
        amount: Decimal,
        max_price: Decimal,
        user_address: str,
        client_operation_id: str | None = None,
    ) -> EthereumTransaction:
        """Build (without posting) a signed CLOB BUY order for the facade.

        Polymarket buys are off-chain CLOB orders, so the returned
        :class:`EthereumTransaction` is a tracking wrapper around the signed
        order (``other_data["clob_order"]``), not an on-chain transaction —
        post it with :meth:`post_order`. The order buys
        ``amount / max_price`` shares (rounded down to the CLOB size step) at
        limit price ``max_price``, spending at most ``amount`` USDC.

        Args:
            market_id: The market's condition id (carried in ``other_data``)
            outcome_token_id: The outcome token to buy
            amount: Collateral (USDC) to invest
            max_price: Maximum acceptable price per share
            user_address: Must match the bound wallet's address
            client_operation_id: Optional tracking id, generated when omitted

        Raises:
            ValueError: If no wallet is bound or ``user_address`` mismatches
        """
        wallet = self._require_wallet()
        self._require_wallet_address_match(user_address, wallet)
        if max_price <= 0:
            raise ValueError(f"max_price must be positive, got {max_price}")

        signed_order = self.build_order(
            outcome_token_id, OrderSide.BUY, price=max_price, size=amount / max_price
        )
        if client_operation_id is None:
            client_operation_id = f"polymarket_buy_{uuid.uuid4().hex[:12]}"
        return self._wrap_order_in_transaction(
            signed_order, market_id, client_operation_id
        )

    async def build_sell_transaction(
        self,
        market_id: str,
        outcome_token_id: str,
        shares: Decimal,
        min_price: Decimal,
        user_address: str,
        client_operation_id: str | None = None,
    ) -> EthereumTransaction:
        """Build (without posting) a signed CLOB SELL order for the facade.

        As with buys, the returned object wraps an off-chain CLOB order (see
        :meth:`build_buy_transaction`); it sells ``shares`` outcome tokens at
        limit price ``min_price``.

        Raises:
            ValueError: If no wallet is bound or ``user_address`` mismatches
        """
        wallet = self._require_wallet()
        self._require_wallet_address_match(user_address, wallet)

        signed_order = self.build_order(
            outcome_token_id, OrderSide.SELL, price=min_price, size=shares
        )
        if client_operation_id is None:
            client_operation_id = f"polymarket_sell_{uuid.uuid4().hex[:12]}"
        return self._wrap_order_in_transaction(
            signed_order, market_id, client_operation_id
        )

    # === On-chain write path ===

    def _apply_gas_cap(self, tx_data: TxParams) -> TxParams:
        """Cap the gas-fee fields at the configured ``max_gas_price_gwei``."""
        if self._max_gas_price_gwei is None:
            return tx_data
        cap = self._max_gas_price_gwei * 10**9
        capped = dict(tx_data)
        for key in ("gasPrice", "maxFeePerGas", "maxPriorityFeePerGas"):
            value = capped.get(key)
            if isinstance(value, int) and value > cap:
                capped[key] = cap
        return cast(TxParams, capped)

    @staticmethod
    def _parse_condition_id(market_id: str) -> bytes:
        """Parse a market id into its 32-byte condition id.

        Raises:
            ValueError: If the id is not 0x-prefixed 32-byte hex
        """
        value = market_id[2:] if market_id.lower().startswith("0x") else market_id
        try:
            condition_id = bytes.fromhex(value)
        except ValueError as error:
            raise ValueError(
                f"market_id must be the 0x-prefixed 32-byte condition id, "
                f"got {market_id!r}"
            ) from error
        if len(condition_id) != 32:
            raise ValueError(
                f"market_id must be the 0x-prefixed 32-byte condition id, "
                f"got {market_id!r}"
            )
        return condition_id

    async def build_redeem_transaction(
        self,
        market_id: str,
        user_address: str,
        client_operation_id: str | None = None,
    ) -> EthereumTransaction:
        """Build the unsigned ``ConditionalTokens.redeemPositions`` transaction.

        Redeems both binary outcome slots (index sets ``[1, 2]``) of the
        market's condition against the USDC collateral, with the null parent
        collection. The transaction parameters are built through the bound
        wallet (sender, chain id, calldata, gas fees — capped at the
        configured ``max_gas_price_gwei``) and returned unsigned in
        ``PENDING_BROADCAST`` state with the parameters in
        ``other_data["tx_data"]``.

        Args:
            market_id: The market's condition id (0x-prefixed 32-byte hex)
            user_address: Must match the bound wallet's address
            client_operation_id: Optional tracking id, generated when omitted

        Raises:
            ValueError: If no wallet is bound, ``user_address`` mismatches or
                ``market_id`` is not a condition id
        """
        wallet = self._require_wallet()
        self._require_wallet_address_match(user_address, wallet)
        condition_id = self._parse_condition_id(market_id)

        if not self.conditional_tokens_contract.is_initialized:
            await self.conditional_tokens_contract.initialize()

        function = self.conditional_tokens_contract.functions.redeemPositions(
            EthereumAddress.from_string(
                self.configuration.collateral_token_address
            ).raw,
            b"\x00" * 32,
            condition_id,
            list(BINARY_INDEX_SETS),
        )
        tx_params = self._apply_gas_cap(
            await wallet.build_transaction(function=function)
        )

        if client_operation_id is None:
            client_operation_id = f"polymarket_redeem_{uuid.uuid4().hex[:12]}"
        return EthereumTransaction(
            client_operation_id=client_operation_id,
            owner_identifier=wallet.identifier,
            creation_timestamp=wallet.current_timestamp,
            current_state=BlockchainTransactionState.PENDING_BROADCAST,
            signed_transaction=None,
            other_data={"tx_data": dict(tx_params)},
        )

    async def approve_collateral(
        self,
        amount: Decimal,
        wallet: EthereumWallet | None = None,
        spender: str | None = None,
        client_operation_id: str | None = None,
    ) -> EthereumTransaction:
        """Approve the CTF Exchange to spend USDC (signs and broadcasts).

        Delegates to :meth:`ERC20Contract.place_approve` on the collateral
        token with an explicit wallet, per the ERC-20 helper pattern.

        Args:
            amount: Decimal-adjusted USDC amount to approve
            wallet: Wallet granting the approval; the bound wallet when omitted
            spender: Spender address; the CTF Exchange when omitted
            client_operation_id: Optional operation id for tracking
        """
        ethereum_wallet = wallet if wallet is not None else self._require_wallet()
        spender_address = EthereumAddress.from_string(
            spender if spender is not None else self.configuration.ctf_exchange_address
        )
        return await self.collateral_token.contract.place_approve(
            ethereum_wallet,
            spender_address,
            amount,
            client_operation_id=client_operation_id,
        )

    async def place_conditional_tokens_approval(
        self,
        approved: bool = True,
        operator: str | None = None,
        wallet: EthereumWallet | None = None,
        client_operation_id: str | None = None,
    ) -> EthereumTransaction:
        """Set ERC-1155 operator approval on ConditionalTokens (signs+broadcasts).

        Grants (or revokes) the CTF Exchange the right to move outcome tokens,
        which is required before SELL orders can settle.

        Args:
            approved: Grant when True, revoke when False
            operator: Operator address; the CTF Exchange when omitted
            wallet: Wallet granting the approval; the bound wallet when omitted
            client_operation_id: Optional operation id for tracking
        """
        ethereum_wallet = wallet if wallet is not None else self._require_wallet()
        operator_address = EthereumAddress.from_string(
            operator
            if operator is not None
            else self.configuration.ctf_exchange_address
        )

        if not self.conditional_tokens_contract.is_initialized:
            await self.conditional_tokens_contract.initialize()

        function = self.conditional_tokens_contract.functions.setApprovalForAll(
            operator_address.raw, approved
        )
        tx_data = self._apply_gas_cap(
            await ethereum_wallet.build_transaction(function=function)
        )

        if ethereum_wallet.last_nonce is None:
            await ethereum_wallet.sync_nonce()

        if client_operation_id is None:
            client_operation_id = f"polymarket_ctf_approval_{uuid.uuid4().hex[:12]}"
        return ethereum_wallet.sign_and_send_transaction(
            client_operation_id=client_operation_id,
            tx_data=cast(dict[str, Any], dict(tx_data)),
        )

    # === Payload parsers (version: MARKET_PARSER_VERSION) ===

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        """Parse an ISO-8601 (optionally ``Z``-suffixed) or epoch timestamp."""
        if value in (None, ""):
            return None
        if isinstance(value, int | float):
            return datetime.fromtimestamp(float(value), tz=UTC)
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None

    @staticmethod
    def _clamp_probability(price: Decimal) -> Decimal:
        """Clamp a price into the [0, 1] probability range."""
        return min(max(price, Decimal(0)), Decimal(1))

    @staticmethod
    def _decode_json_list(value: Any) -> list[Any]:
        """Decode Gamma's JSON-string-encoded lists (or pass lists through)."""
        if value is None:
            return []
        if isinstance(value, list):
            return value
        try:
            decoded = json.loads(str(value))
        except json.JSONDecodeError:
            return []
        return decoded if isinstance(decoded, list) else []

    def _build_market_model(
        self,
        *,
        market_id: str,
        title: str,
        description: str,
        category: str,
        status: MarketStatus,
        outcomes: list[MarketOutcome],
        total_volume: Decimal,
        total_liquidity: Decimal,
        creation_date: datetime,
        end_date: datetime | None,
        resolved_outcome_id: str | None,
        raw: dict[str, Any],
    ) -> BettingMarketModel:
        """Assemble a :class:`BettingMarketModel` with shared invariants."""
        return BettingMarketModel(
            market_id=market_id,
            title=title,
            description=description,
            category=category,
            status=status,
            collateral_asset=self.collateral_token,
            outcomes=outcomes,
            total_volume=total_volume,
            total_liquidity=total_liquidity,
            creation_date=creation_date,
            end_date=end_date,
            resolved_outcome_id=resolved_outcome_id,
            protocol=self.configuration.protocol_name,
            metadata={"parser_version": MARKET_PARSER_VERSION, "raw": raw},
        )

    def _parse_clob_market(self, market_data: dict[str, Any]) -> BettingMarketModel:
        """Parse a CLOB ``GET /markets/{condition_id}`` payload.

        Expected shape (parser version :data:`MARKET_PARSER_VERSION`)::

            {
              "condition_id": "0x...", "question": "...", "description": "...",
              "active": true, "closed": false, "archived": false,
              "end_date_iso": "2025-12-31T00:00:00Z",
              "accepting_order_timestamp": "2025-01-01T00:00:00Z" | null,
              "tags": ["Crypto", ...],
              "tokens": [
                {"token_id": "713...", "outcome": "Yes",
                 "price": 0.65, "winner": false},
                ...
              ]
            }

        Status precedence: a market with a winning token is RESOLVED even
        when also flagged closed; otherwise closed/inactive markets are
        CLOSED. The payload carries no volume/liquidity or creation date
        (zeros / epoch are used).
        """
        outcomes: list[MarketOutcome] = []
        winner_outcome_id: str | None = None
        for index, token in enumerate(market_data.get("tokens", [])):
            price = Decimal(str(token.get("price") or "0"))
            is_winner = bool(token.get("winner", False))
            outcome_id = str(index)
            if is_winner:
                winner_outcome_id = outcome_id
            outcome_name = str(token.get("outcome", f"Outcome {index}"))
            outcomes.append(
                MarketOutcome(
                    outcome_id=outcome_id,
                    outcome_text=outcome_name,
                    is_winning_outcome=is_winner,
                    outcome_tokens=[
                        OutcomeToken(
                            token_id=str(token.get("token_id", "")),
                            outcome_name=outcome_name,
                            current_price=price,
                            total_supply=Decimal(0),
                            probability=self._clamp_probability(price),
                        )
                    ],
                )
            )

        if winner_outcome_id is not None:
            status = MarketStatus.RESOLVED
        elif bool(market_data.get("closed", False)) or bool(
            market_data.get("archived", False)
        ):
            status = MarketStatus.CLOSED
        elif bool(market_data.get("active", True)):
            status = MarketStatus.ACTIVE
        else:
            status = MarketStatus.CLOSED

        tags = market_data.get("tags") or []
        category = str(tags[0]) if tags else "Other"

        return self._build_market_model(
            market_id=str(market_data.get("condition_id", "")),
            title=str(market_data.get("question", "Unknown Market")),
            description=str(market_data.get("description", "")),
            category=category,
            status=status,
            outcomes=outcomes,
            total_volume=Decimal(0),
            total_liquidity=Decimal(0),
            creation_date=self._parse_datetime(
                market_data.get("accepting_order_timestamp")
            )
            or _EPOCH,
            end_date=self._parse_datetime(market_data.get("end_date_iso")),
            resolved_outcome_id=winner_outcome_id,
            raw=market_data,
        )

    def _parse_gamma_market(self, market_data: dict[str, Any]) -> BettingMarketModel:
        """Parse a Gamma ``GET /markets`` item.

        Expected shape (parser version :data:`MARKET_PARSER_VERSION`)::

            {
              "id": "253591", "conditionId": "0x...", "question": "...",
              "description": "...", "category": "Crypto",
              "outcomes": "[\\"Yes\\", \\"No\\"]",
              "outcomePrices": "[\\"0.65\\", \\"0.35\\"]",
              "clobTokenIds": "[\\"713...\\", \\"123...\\"]",
              "volumeNum": 1091701.53, "liquidityNum": 302845.92,
              "createdAt": "2025-01-04T22:58:00.169Z",
              "endDate": "2025-12-31T12:00:00Z",
              "active": true, "closed": false,
              "umaResolutionStatus": "resolved" | ...
            }

        ``outcomes``/``outcomePrices``/``clobTokenIds`` arrive JSON-encoded
        as strings (lists are tolerated). Status precedence: a market that is
        UMA-resolved AND has an identifiable winner (an outcome priced at 1)
        is RESOLVED; a resolved market without an identifiable winner is
        conservatively reported CLOSED (the model requires a winner for
        RESOLVED); otherwise closed/archived/inactive map to CLOSED.
        """
        outcome_names = [
            str(name) for name in self._decode_json_list(market_data.get("outcomes"))
        ] or ["Yes", "No"]
        raw_prices = self._decode_json_list(market_data.get("outcomePrices"))
        token_ids = self._decode_json_list(market_data.get("clobTokenIds"))
        market_id = str(market_data.get("conditionId") or market_data.get("id") or "")

        outcomes: list[MarketOutcome] = []
        winner_outcome_id: str | None = None
        for index, outcome_name in enumerate(outcome_names):
            price = (
                Decimal(str(raw_prices[index]))
                if index < len(raw_prices)
                else Decimal("0.5")
            )
            token_id = (
                str(token_ids[index])
                if index < len(token_ids)
                else f"{market_id}_{index}"
            )
            is_winner = price == Decimal(1)
            outcome_id = str(index)
            if is_winner and winner_outcome_id is None:
                winner_outcome_id = outcome_id
            outcomes.append(
                MarketOutcome(
                    outcome_id=outcome_id,
                    outcome_text=outcome_name,
                    is_winning_outcome=is_winner,
                    outcome_tokens=[
                        OutcomeToken(
                            token_id=token_id,
                            outcome_name=outcome_name,
                            current_price=price,
                            total_supply=Decimal(0),
                            probability=self._clamp_probability(price),
                        )
                    ],
                )
            )

        uma_status = str(market_data.get("umaResolutionStatus", "")).lower()
        is_resolved = uma_status == "resolved"
        is_closed = bool(market_data.get("closed", False)) or bool(
            market_data.get("archived", False)
        )
        if is_resolved and winner_outcome_id is not None:
            status = MarketStatus.RESOLVED
        elif is_resolved or is_closed:
            # Resolved-without-identifiable-winner is downgraded to CLOSED:
            # the model requires a resolved outcome id for RESOLVED markets.
            status = MarketStatus.CLOSED
            winner_outcome_id = None
        elif bool(market_data.get("active", True)):
            status = MarketStatus.ACTIVE
        else:
            status = MarketStatus.CLOSED

        volume = market_data.get("volumeNum", market_data.get("volume", 0))
        liquidity = market_data.get("liquidityNum", market_data.get("liquidity", 0))

        return self._build_market_model(
            market_id=market_id,
            title=str(market_data.get("question", "Unknown Market")),
            description=str(market_data.get("description", "")),
            category=str(market_data.get("category", "Other")),
            status=status,
            outcomes=outcomes,
            total_volume=Decimal(str(volume or 0)),
            total_liquidity=Decimal(str(liquidity or 0)),
            creation_date=self._parse_datetime(market_data.get("createdAt"))
            or self._parse_datetime(market_data.get("startDate"))
            or _EPOCH,
            end_date=self._parse_datetime(market_data.get("endDate")),
            resolved_outcome_id=winner_outcome_id,
            raw=market_data,
        )

    def _parse_position_data(self, position_data: dict[str, Any]) -> BettingPosition:
        """Parse a Data API ``GET /positions`` item.

        Expected shape (parser version :data:`MARKET_PARSER_VERSION`)::

            {
              "proxyWallet": "0x...", "asset": "713...",
              "conditionId": "0x...", "size": 100.5, "avgPrice": 0.55,
              "curPrice": 0.65, "outcome": "Yes", "outcomeIndex": 0,
              "title": "...", "redeemable": false, ...
            }
        """
        shares_owned = Decimal(str(position_data.get("size", 0)))
        average_price = Decimal(str(position_data.get("avgPrice", 0)))
        current_price = Decimal(str(position_data.get("curPrice", 0)))

        outcome_token = OutcomeToken(
            token_id=str(position_data.get("asset", "")),
            outcome_name=str(position_data.get("outcome", "Unknown")),
            current_price=current_price,
            total_supply=Decimal(0),
            probability=self._clamp_probability(current_price),
        )

        total_invested = shares_owned * average_price
        current_value = shares_owned * current_price
        return BettingPosition(
            market_id=str(position_data.get("conditionId", "")),
            outcome_token=outcome_token,
            shares_owned=shares_owned,
            average_price=average_price,
            total_invested=total_invested,
            current_value=current_value,
            unrealized_pnl=current_value - total_invested,
            protocol=self.configuration.protocol_name,
        )


class EVMBettingMarket(BettingMarket):
    """EVM betting market facade wiring Polymarket protocol strategies."""

    def __init__(self, configuration: EVMBettingMarketConfiguration):
        super().__init__(configuration)

    def _initialize_protocols(self) -> None:
        """Initialize EVM-compatible betting market protocol strategies."""
        blockchain = cast(EthereumBlockchain, self.blockchain)

        for protocol_config in self.configuration.protocols:
            if isinstance(protocol_config, PolymarketConfiguration):
                self._protocol_strategies[protocol_config.protocol_name] = Polymarket(
                    protocol_config,
                    blockchain,
                    max_gas_price_gwei=self.configuration.max_gas_price_gwei,
                )

    def set_wallet(self, wallet: BlockchainWallet | None) -> None:
        """Bind (or unbind) the wallet on every protocol strategy."""
        for strategy in self._protocol_strategies.values():
            cast(Polymarket, strategy).set_wallet(wallet)

    async def close(self) -> None:
        """Close the HTTP resources of every protocol strategy."""
        for strategy in self._protocol_strategies.values():
            await cast(Polymarket, strategy).close()


class PolymarketBettingMarket(EVMBettingMarket):
    """Polymarket-specific betting market facade."""

    pass
