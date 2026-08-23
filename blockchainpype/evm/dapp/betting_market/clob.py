"""
Polymarket CLOB (Central Limit Order Book) client and order signing.

Polymarket trading is hybrid: orders are signed off-chain with EIP-712 and
posted to the CLOB REST API (https://clob.polymarket.com), while settlement,
redemption and allowances happen on-chain (CTF Exchange / ConditionalTokens).
This module implements the off-chain half:

* EIP-712 ``Order`` struct building and signing, using the authoritative
  domain/struct layout from ``@polymarket/order-utils``
  (name='Polymarket CTF Exchange', version='1').
* Price/size to makerAmount/takerAmount conversion with the CLOB rounding
  conventions (USDC and outcome tokens both use 6 decimals).
* L2 HMAC authentication headers (``POLY_*``) for private endpoints.
* An aiohttp-based :class:`ClobClient` for the public read endpoints
  (markets, books, prices, midpoints) and the authenticated order endpoint.
"""

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Mapping
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from enum import IntEnum
from types import TracebackType
from typing import Any, Final, Self, cast

import aiohttp
from eth_account import Account
from pydantic import BaseModel, ConfigDict, Field, SecretStr

# === Protocol constants (Polygon mainnet, from @polymarket/order-utils) ===

POLYGON_CHAIN_ID: Final[int] = 137
CLOB_API_BASE_URL: Final[str] = "https://clob.polymarket.com"
CLOB_DOMAIN_NAME: Final[str] = "Polymarket CTF Exchange"
CLOB_DOMAIN_VERSION: Final[str] = "1"
CTF_EXCHANGE_ADDRESS: Final[str] = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
ZERO_ADDRESS: Final[str] = "0x0000000000000000000000000000000000000000"

#: USDC (collateral) and CTF outcome tokens both use 6 decimals on Polygon.
USDC_DECIMALS: Final[int] = 6
OUTCOME_TOKEN_DECIMALS: Final[int] = 6
AMOUNT_SCALE: Final[int] = 10**6

#: CLOB share-size granularity: sizes are quoted in hundredths of a share.
SIZE_STEP: Final[Decimal] = Decimal("0.01")
#: Default price tick; markets use 0.1 / 0.01 / 0.001 / 0.0001 ticks.
DEFAULT_TICK_SIZE: Final[Decimal] = Decimal("0.001")
VALID_TICK_SIZES: Final[tuple[Decimal, ...]] = (
    Decimal("0.1"),
    Decimal("0.01"),
    Decimal("0.001"),
    Decimal("0.0001"),
)

#: EIP-712 ``Order`` struct fields, in the exact order of the official struct
#: definition (any reordering changes the type hash and breaks verification).
ORDER_STRUCT_FIELDS: Final[tuple[tuple[str, str], ...]] = (
    ("salt", "uint256"),
    ("maker", "address"),
    ("signer", "address"),
    ("taker", "address"),
    ("tokenId", "uint256"),
    ("makerAmount", "uint256"),
    ("takerAmount", "uint256"),
    ("expiration", "uint256"),
    ("nonce", "uint256"),
    ("feeRateBps", "uint256"),
    ("side", "uint8"),
    ("signatureType", "uint8"),
)


class OrderSide(IntEnum):
    """CLOB order side, encoded as ``uint8`` in the EIP-712 struct."""

    BUY = 0
    SELL = 1


class SignatureType(IntEnum):
    """How the CLOB order signature must be verified on-chain."""

    EOA = 0
    POLY_PROXY = 1
    POLY_GNOSIS_SAFE = 2


class ClobCredentials(BaseModel):
    """L2 API credentials for the authenticated CLOB endpoints.

    These are the credentials returned by Polymarket's API-key derivation
    (``POST /auth/api-key``). The secret is a base64url-encoded HMAC key and
    is stored as a :class:`SecretStr` so it never leaks into logs.

    Attributes:
        api_key: The API key UUID (sent as ``POLY_API_KEY`` and order owner)
        secret: base64url-encoded HMAC-SHA256 secret
        passphrase: The API passphrase (sent as ``POLY_PASSPHRASE``)
    """

    api_key: str
    secret: SecretStr
    passphrase: str


class ClobOrder(BaseModel):
    """A Polymarket CTF Exchange order (the EIP-712 ``Order`` struct).

    Attributes:
        salt: Random salt making the order hash unique
        maker: Address funding the order (source of funds / assets)
        signer: Address whose signature authorizes the order
        taker: Counterparty address; the zero address for public orders
        token_id: CTF outcome-token (ERC-1155) id being bought or sold
        maker_amount: Maximum amount the maker spends, in 6-decimal base units
        taker_amount: Minimum amount the maker receives, in 6-decimal base units
        expiration: Unix expiration timestamp; 0 means no expiration
        nonce: Maker's exchange nonce for on-chain cancellations
        fee_rate_bps: Maximum fee rate the maker accepts, in basis points
        side: :class:`OrderSide` (BUY buys outcome tokens with USDC)
        signature_type: :class:`SignatureType` verification scheme
    """

    salt: int
    maker: str
    signer: str
    taker: str = ZERO_ADDRESS
    token_id: int
    maker_amount: int
    taker_amount: int
    expiration: int = 0
    nonce: int = 0
    fee_rate_bps: int = 0
    side: OrderSide
    signature_type: SignatureType = SignatureType.EOA

    def to_eip712_message(self) -> dict[str, Any]:
        """Return the struct values keyed by their EIP-712 field names."""
        return {
            "salt": self.salt,
            "maker": self.maker,
            "signer": self.signer,
            "taker": self.taker,
            "tokenId": self.token_id,
            "makerAmount": self.maker_amount,
            "takerAmount": self.taker_amount,
            "expiration": self.expiration,
            "nonce": self.nonce,
            "feeRateBps": self.fee_rate_bps,
            "side": int(self.side),
            "signatureType": int(self.signature_type),
        }


class SignedClobOrder(BaseModel):
    """A :class:`ClobOrder` together with its EIP-712 signature."""

    order: ClobOrder
    signature: str

    def to_api_payload(self) -> dict[str, Any]:
        """Serialize into the JSON order object the CLOB ``POST /order`` expects.

        Amount/id fields are stringified and the side is symbolic
        (``"BUY"``/``"SELL"``), matching the official client wire format.
        """
        order = self.order
        return {
            "salt": order.salt,
            "maker": order.maker,
            "signer": order.signer,
            "taker": order.taker,
            "tokenId": str(order.token_id),
            "makerAmount": str(order.maker_amount),
            "takerAmount": str(order.taker_amount),
            "expiration": str(order.expiration),
            "nonce": str(order.nonce),
            "feeRateBps": str(order.fee_rate_bps),
            "side": "BUY" if order.side == OrderSide.BUY else "SELL",
            "signatureType": int(order.signature_type),
            "signature": self.signature,
        }


class OrderPostResponse(BaseModel):
    """Typed response of the CLOB ``POST /order`` endpoint."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    success: bool = False
    error_msg: str | None = Field(default=None, alias="errorMsg")
    order_id: str | None = Field(default=None, alias="orderID")
    status: str | None = None
    transaction_hashes: list[str] | None = Field(
        default=None, alias="transactionsHashes"
    )


class ClobApiError(RuntimeError):
    """A CLOB/HTTP request failed (transport error or non-2xx response)."""

    def __init__(self, status: int, message: str, payload: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.payload = payload


def generate_salt() -> int:
    """Generate a random order salt (fits comfortably in ``uint256``)."""
    return secrets.randbits(128)


def compute_order_amounts(
    side: OrderSide,
    price: Decimal,
    size: Decimal,
    tick_size: Decimal = DEFAULT_TICK_SIZE,
) -> tuple[int, int]:
    """Convert a (price, size) pair into raw maker/taker amounts.

    Rounding conventions (mirroring the official CLOB clients):

    * ``size`` (number of outcome shares) is rounded DOWN to the CLOB's
      0.01-share granularity (:data:`SIZE_STEP`).
    * ``price`` is rounded to the market tick with ROUND_HALF_UP.
    * After that normalization ``price * size`` has at most 6 decimal places
      (tick <= 4 dp, size <= 2 dp), so scaling by 10**6 is exact; the defensive
      final conversion still rounds DOWN so the maker can never be asked to
      spend more than ``price * size``.

    Args:
        side: BUY spends USDC for outcome tokens; SELL is the reverse
        price: Limit price per share in USDC, strictly between 0 and 1
        size: Number of outcome shares
        tick_size: The market's price tick (one of :data:`VALID_TICK_SIZES`)

    Returns:
        ``(maker_amount, taker_amount)`` in 6-decimal base units:
        BUY -> (USDC spent, shares received); SELL -> (shares sold, USDC received)

    Raises:
        ValueError: On out-of-range price/size or an unsupported tick size
    """
    if tick_size not in VALID_TICK_SIZES:
        raise ValueError(
            f"Unsupported tick size {tick_size}; expected one of {VALID_TICK_SIZES}"
        )
    if not Decimal(0) < price < Decimal(1):
        raise ValueError(f"Price must be strictly between 0 and 1, got {price}")

    normalized_price = price.quantize(tick_size, rounding=ROUND_HALF_UP)
    if not Decimal(0) < normalized_price < Decimal(1):
        raise ValueError(
            f"Price {price} rounds to {normalized_price} at tick {tick_size}, "
            "which is outside the (0, 1) range"
        )

    normalized_size = size.quantize(SIZE_STEP, rounding=ROUND_DOWN)
    if normalized_size <= 0:
        raise ValueError(
            f"Size must be at least {SIZE_STEP} shares after rounding, got {size}"
        )

    shares_raw = int(normalized_size.scaleb(OUTCOME_TOKEN_DECIMALS))
    usdc_raw = int(
        (normalized_price * normalized_size)
        .scaleb(USDC_DECIMALS)
        .to_integral_value(rounding=ROUND_DOWN)
    )
    if usdc_raw <= 0:
        raise ValueError(
            f"Order value {normalized_price * normalized_size} USDC is below "
            "the smallest representable amount"
        )

    if side == OrderSide.BUY:
        return usdc_raw, shares_raw
    return shares_raw, usdc_raw


def build_order_typed_data(
    order: ClobOrder,
    chain_id: int,
    verifying_contract: str = CTF_EXCHANGE_ADDRESS,
) -> dict[str, Any]:
    """Build the full EIP-712 typed-data message for a CLOB order.

    The domain and struct layout come from ``@polymarket/order-utils``:
    domain name ``'Polymarket CTF Exchange'``, version ``'1'``, with the
    exchange contract as ``verifyingContract``.

    Args:
        order: The order to encode
        chain_id: EIP-712 domain chain id (137 for Polygon mainnet)
        verifying_contract: The CTF Exchange contract address

    Returns:
        The typed-data dict accepted by ``eth_account`` ``full_message=``
    """
    return {
        "primaryType": "Order",
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "Order": [
                {"name": name, "type": type_} for name, type_ in ORDER_STRUCT_FIELDS
            ],
        },
        "domain": {
            "name": CLOB_DOMAIN_NAME,
            "version": CLOB_DOMAIN_VERSION,
            "chainId": chain_id,
            "verifyingContract": verifying_contract,
        },
        "message": order.to_eip712_message(),
    }


def sign_clob_order(
    order: ClobOrder,
    chain_id: int,
    private_key: bytes | str,
    verifying_contract: str = CTF_EXCHANGE_ADDRESS,
) -> SignedClobOrder:
    """Sign a CLOB order with EIP-712 (``eth_account.Account.sign_typed_data``).

    Args:
        order: The order to sign
        chain_id: EIP-712 domain chain id (137 for Polygon mainnet)
        private_key: The signer's private key (bytes or hex string)
        verifying_contract: The CTF Exchange contract address

    Returns:
        The order together with its 65-byte ``0x``-prefixed signature
    """
    typed_data = build_order_typed_data(order, chain_id, verifying_contract)
    signed_message = Account.sign_typed_data(private_key, full_message=typed_data)
    return SignedClobOrder(order=order, signature=signed_message.signature.to_0x_hex())


def build_hmac_signature(
    secret: str,
    timestamp: str,
    method: str,
    request_path: str,
    body: str | None = None,
) -> str:
    """Build the L2 HMAC signature for the ``POLY_SIGNATURE`` header.

    Per the Polymarket L2 auth spec: the canonical message is
    ``timestamp + method + request_path (+ body)``, MACed with HMAC-SHA256
    using the base64url-decoded secret, and the digest is base64url-encoded.

    Args:
        secret: base64url-encoded HMAC secret from the API credentials
        timestamp: Unix-seconds timestamp string (also sent as ``POLY_TIMESTAMP``)
        method: Upper-case HTTP method (e.g. ``"POST"``)
        request_path: The endpoint path (e.g. ``"/order"``)
        body: The exact JSON request body string, when the request has one

    Returns:
        The base64url-encoded signature string
    """
    message = timestamp + method + request_path
    if body is not None:
        message += body
    digest = hmac.new(
        base64.urlsafe_b64decode(secret), message.encode("utf-8"), hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(digest).decode("utf-8")


def serialize_query_params(params: Mapping[str, Any]) -> dict[str, str]:
    """Serialize query params into the strings aiohttp/yarl accept.

    ``yarl`` raises ``TypeError`` for Python ``bool`` values, so booleans are
    lowered to their JSON forms (``"true"``/``"false"``); ``None`` values are
    dropped and everything else is stringified.
    """
    serialized: dict[str, str] = {}
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, bool):
            serialized[key] = "true" if value else "false"
        else:
            serialized[key] = str(value)
    return serialized


class ClobClient:
    """Async client for the Polymarket CLOB REST API.

    Public read endpoints (markets, books, prices, midpoints) work without
    credentials; posting or cancelling orders requires
    :class:`ClobCredentials` (L2 auth headers are attached automatically).

    The aiohttp session is created lazily on first use and owned by the
    client unless one is injected; :meth:`close` (or use as an async context
    manager) releases it either way.
    """

    def __init__(
        self,
        base_url: str = CLOB_API_BASE_URL,
        chain_id: int = POLYGON_CHAIN_ID,
        credentials: ClobCredentials | None = None,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        """Initialize the client.

        Args:
            base_url: CLOB API base URL
            chain_id: Chain id used for EIP-712 domains (informational here)
            credentials: Optional L2 API credentials for private endpoints
            session: Optional externally managed aiohttp session
        """
        self.base_url = base_url.rstrip("/")
        self.chain_id = chain_id
        self.credentials = credentials
        self._session = session

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or lazily create the HTTP session."""
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        """Close the HTTP session (safe to call multiple times)."""
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()

    def _auth_headers(
        self, method: str, request_path: str, body: str | None, address: str
    ) -> dict[str, str]:
        """Build the ``POLY_*`` L2 auth headers for a private request.

        Args:
            method: Upper-case HTTP method
            request_path: The endpoint path being signed
            body: The exact JSON body string, when present
            address: The maker/funder address (``POLY_ADDRESS``)

        Raises:
            ValueError: If no credentials are configured
        """
        if self.credentials is None:
            raise ValueError(
                "CLOB API credentials are required for authenticated endpoints"
            )
        timestamp = str(int(time.time()))
        signature = build_hmac_signature(
            self.credentials.secret.get_secret_value(),
            timestamp,
            method,
            request_path,
            body,
        )
        return {
            "POLY_ADDRESS": address,
            "POLY_SIGNATURE": signature,
            "POLY_TIMESTAMP": timestamp,
            "POLY_API_KEY": self.credentials.api_key,
            "POLY_PASSPHRASE": self.credentials.passphrase,
        }

    async def _request(
        self,
        method: str,
        path: str,
        params: Mapping[str, Any] | None = None,
        json_payload: Any = None,
        auth_address: str | None = None,
    ) -> Any:
        """Perform an HTTP request and decode the JSON response.

        The JSON body (when present) is serialized once and that exact string
        is both signed (for L2 auth) and sent, so signature and payload can
        never drift apart.

        Raises:
            ClobApiError: On transport failures or non-2xx responses
        """
        session = await self._get_session()
        url = f"{self.base_url}{path}"

        body_text: str | None = None
        data: bytes | None = None
        headers: dict[str, str] = {}
        if json_payload is not None:
            body_text = json.dumps(json_payload)
            data = body_text.encode("utf-8")
            headers["Content-Type"] = "application/json"
        if auth_address is not None:
            headers.update(self._auth_headers(method, path, body_text, auth_address))

        query = serialize_query_params(params) if params else None
        try:
            async with session.request(
                method, url, params=query, data=data, headers=headers
            ) as response:
                text = await response.text()
                payload: Any
                try:
                    payload = json.loads(text) if text else None
                except json.JSONDecodeError:
                    payload = text
                if response.status >= 400:
                    raise ClobApiError(
                        response.status,
                        f"CLOB request {method} {path} failed "
                        f"with status {response.status}: {payload!r}",
                        payload=payload,
                    )
                return payload
        except aiohttp.ClientError as error:
            raise ClobApiError(
                0, f"CLOB request {method} {path} failed: {error}"
            ) from error

    # === Public (unauthenticated) endpoints ===

    async def get_markets(self, next_cursor: str | None = None) -> dict[str, Any]:
        """GET ``/markets`` — paginated CLOB market list.

        Args:
            next_cursor: Opaque pagination cursor from a previous response
        """
        params: dict[str, Any] = {}
        if next_cursor is not None:
            params["next_cursor"] = next_cursor
        result = await self._request("GET", "/markets", params=params or None)
        return cast(dict[str, Any], result)

    async def get_market(self, condition_id: str) -> dict[str, Any]:
        """GET ``/markets/{condition_id}`` — a single CLOB market."""
        result = await self._request("GET", f"/markets/{condition_id}")
        return cast(dict[str, Any], result)

    async def get_book(self, token_id: str) -> dict[str, Any]:
        """GET ``/book`` — the order book for an outcome token."""
        result = await self._request("GET", "/book", params={"token_id": token_id})
        return cast(dict[str, Any], result)

    async def get_price(self, token_id: str, side: str) -> Decimal:
        """GET ``/price`` — best bid/ask price for an outcome token.

        Args:
            token_id: The outcome token id
            side: ``"buy"`` (best ask) or ``"sell"`` (best bid)
        """
        result = await self._request(
            "GET", "/price", params={"token_id": token_id, "side": side}
        )
        return Decimal(str(cast(dict[str, Any], result)["price"]))

    async def get_midpoint(self, token_id: str) -> Decimal:
        """GET ``/midpoint`` — midpoint price for an outcome token."""
        result = await self._request("GET", "/midpoint", params={"token_id": token_id})
        return Decimal(str(cast(dict[str, Any], result)["mid"]))

    # === Authenticated endpoints ===

    async def post_order(
        self, signed_order: SignedClobOrder, order_type: str = "GTC"
    ) -> OrderPostResponse:
        """POST ``/order`` — submit a signed order to the CLOB.

        Args:
            signed_order: The EIP-712-signed order
            order_type: ``"GTC"``, ``"FOK"``, ``"GTD"`` or ``"FAK"``

        Returns:
            The typed CLOB response (order id, status, error message)

        Raises:
            ValueError: If no credentials are configured
            ClobApiError: On transport failures or non-2xx responses
        """
        if self.credentials is None:
            raise ValueError(
                "CLOB API credentials are required to post orders; configure "
                "ClobCredentials on the client"
            )
        payload = {
            "order": signed_order.to_api_payload(),
            "owner": self.credentials.api_key,
            "orderType": order_type,
        }
        result = await self._request(
            "POST",
            "/order",
            json_payload=payload,
            auth_address=signed_order.order.maker,
        )
        return OrderPostResponse.model_validate(result)

    async def cancel_order(self, order_id: str, address: str) -> dict[str, Any]:
        """DELETE ``/order`` — cancel a resting order.

        Args:
            order_id: The CLOB order id to cancel
            address: The maker/funder address for the auth headers
        """
        result = await self._request(
            "DELETE",
            "/order",
            json_payload={"orderID": order_id},
            auth_address=address,
        )
        return cast(dict[str, Any], result)
