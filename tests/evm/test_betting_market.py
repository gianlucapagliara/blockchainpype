"""
Unit tests for the Polymarket betting market implementation.

This module tests:
- EIP-712 order signing with pinned regression vectors (typehash, digest,
  signature) cross-checked against a manual keccak/abi.encode computation
- Price/size -> maker/taker amount math with the CLOB rounding conventions
- L2 HMAC auth header construction with pinned vectors
- The CLOB client against realistic mocked HTTP payloads (incl. errors and
  the bool-query-param regression)
- Gamma/CLOB/Data-API payload parsing (status precedence, missing winner,
  real ERC20Token collateral)
- On-chain redeem/approval building with exact calldata via the vendored
  ConditionalTokens ABI, mocked only at the JSON-RPC boundary

No test touches the network: HTTP is faked at the aiohttp session boundary
(with yarl query validation preserved) and JSON-RPC at the provider boundary.
"""

import asyncio
import json
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import aiohttp
import pytest
from eth_abi import encode
from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_account.signers.local import LocalAccount
from eth_account.typed_transactions import TypedTransaction
from eth_utils import keccak
from financepype.assets.blockchain import BlockchainAsset
from financepype.operations.transactions.models import BlockchainTransactionState
from financepype.platforms.blockchain import BlockchainPlatform
from hexbytes import HexBytes
from yarl import URL

from blockchainpype.dapps.betting_market import (
    BettingMarketModel,
    BettingPosition,
    MarketStatus,
)
from blockchainpype.evm.blockchain.blockchain import (
    EthereumBlockchain,
    EthereumBlockchainType,
)
from blockchainpype.evm.blockchain.configuration import (
    EthereumBlockchainConfiguration,
    EthereumConnectivityConfiguration,
    EthereumNativeAssetConfiguration,
)
from blockchainpype.evm.dapp.betting_market import (
    ClobApiError,
    ClobClient,
    ClobCredentials,
    ClobOrder,
    OrderSide,
    Polymarket,
    PolymarketBettingMarket,
    PolymarketConfiguration,
    SignatureType,
    build_hmac_signature,
    build_order_typed_data,
    compute_order_amounts,
    serialize_query_params,
    sign_clob_order,
)
from blockchainpype.evm.dapp.betting_market.polymarket import (
    CONDITIONAL_TOKENS_ADDRESS,
    CTF_EXCHANGE_ADDRESS,
    USDC_ADDRESS,
    EVMBettingMarketConfiguration,
)
from blockchainpype.evm.dapp.erc20 import ERC20Token
from blockchainpype.evm.transaction import EthereumTransaction
from tests.evm.test_erc20 import make_block_payload
from tests.evm.test_wallet import (
    TX_HASH_HEX,
    FakeRPCProvider,
    build_wallet,
    drain_background_tasks,
)

# === Deterministic test identities ===

TEST_PRIVATE_KEY = "0x" + "aa" * 32
TEST_ADDRESS = "0x8fd379246834eac74B8419FfdA202CF8051F7A03"
OTHER_ADDRESS = "0x5B38Da6a701c568545dCfcB03FcB875f56beddC4"

CONDITION_ID = "0x" + "12" * 32
TOKEN_ID_YES = (
    "71321045679252212594626385532706912750332728571942532289631379312455583992563"
)
TOKEN_ID_NO = (
    "52114319501245915516055106046884209969926127482827954674443846427813813222426"
)

# === Pinned EIP-712 regression vectors ===
#
# Computed once with eth_account (and cross-checked below with a manual
# keccak/abi.encode implementation). They lock the exact struct/domain
# encoding: any change to the field order, types, domain constants or
# message building breaks these assertions.
PINNED_SALT = 479249096354
PINNED_MAKER_AMOUNT = 60_000_000  # 60 USDC
PINNED_TAKER_AMOUNT = 100_000_000  # 100 shares
ORDER_TYPEHASH = "0xa852566c4e14d00869b6db0220888a9090a13eccdaea03713ff0a3d27bf9767c"
PINNED_DOMAIN_SEPARATOR = (
    "0x1a573e3617c78403b5b4b892827992f027b03d4eaf570048b8ee8cdd84d151be"
)
PINNED_STRUCT_HASH = (
    "0x27e6528f926bd34b4e5c731fc143f7f3a4c4bab14ffc7e3851cebf9ea28133e6"
)
PINNED_DIGEST = "0xc6f7f69182d882111b6ce452483488787ea631168147800d6479ca9133ee748f"
PINNED_SIGNATURE = (
    "0x7e32d6056d661f060a73d740f88c35b034901ad5c423a1ce7e35a55cab05dbe2"
    "2880e54fecc44ebdd6cad7deb4f263bfeb4fd28e24b19de50d2f798bdc2e017e1c"
)

# === Pinned L2 HMAC auth vectors ===
#
# secret = base64url(b"0123456789abcdef0123456789abcdef"); the signatures were
# computed once with hmac/sha256 + base64url and pin the canonical message
# layout (timestamp + method + path [+ body]).
PINNED_SECRET = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="
PINNED_HMAC_WITH_BODY = "WpJZaY5_wcHS_bmgHkEtjr3KJRrelILvXMn-RsiyEwY="
PINNED_HMAC_NO_BODY = "d_8rYNiskB9DSmPVGBEqPIK9veia9oWolAp_uuLjkXA="

# Canonical function selectors (keccak256 of the signature, first 4 bytes)
SEL_REDEEM_POSITIONS = (
    "0x01b7037c"  # redeemPositions(address,bytes32,bytes32,uint256[])
)
SEL_SET_APPROVAL_FOR_ALL = "0xa22cb465"  # setApprovalForAll(address,bool)
SEL_APPROVE = "0x095ea7b3"  # approve(address,uint256)
SEL_DECIMALS = "0x313ce567"
SEL_NAME = "0x06fdde03"
SEL_SYMBOL = "0x95d89b41"


def pinned_order() -> ClobOrder:
    """The exact order the pinned EIP-712 vectors were computed for."""
    return ClobOrder(
        salt=PINNED_SALT,
        maker=TEST_ADDRESS,
        signer=TEST_ADDRESS,
        token_id=int(TOKEN_ID_YES),
        maker_amount=PINNED_MAKER_AMOUNT,
        taker_amount=PINNED_TAKER_AMOUNT,
        side=OrderSide.BUY,
    )


# === HTTP boundary fakes ===


class FakeHttpResponse:
    """Canned aiohttp-style response usable as an async context manager."""

    def __init__(self, payload: Any, status: int = 200) -> None:
        self._payload = payload
        self.status = status

    async def text(self) -> str:
        return json.dumps(self._payload)

    async def __aenter__(self) -> "FakeHttpResponse":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None


class FakeHttpSession:
    """In-memory aiohttp session answering from a canned response table.

    Query parameters are passed through ``yarl.URL.update_query`` so the fake
    rejects exactly what aiohttp rejects (e.g. Python bools -> TypeError),
    keeping the bool-serialization regression real. Values in ``responses``
    are keyed by ``(METHOD, path)`` and may be a payload, an
    ``(status, payload)`` tuple or an Exception instance (raised).
    """

    def __init__(self, responses: dict[tuple[str, str], Any]) -> None:
        self.responses = responses
        self.requests: list[dict[str, Any]] = []
        self.closed = False

    def request(
        self,
        method: str,
        url: str,
        params: Any = None,
        data: Any = None,
        headers: Any = None,
    ) -> FakeHttpResponse:
        full_url = URL(url)
        if params:
            full_url = full_url.update_query(params)  # bool params raise here
        key = (method.upper(), URL(url).path)
        if key not in self.responses:
            raise AssertionError(f"Unexpected HTTP request: {key}")
        body = data.decode() if isinstance(data, bytes | bytearray) else data
        self.requests.append(
            {
                "method": method.upper(),
                "url": str(full_url),
                "path": URL(url).path,
                "params": dict(params or {}),
                "headers": dict(headers or {}),
                "body": body,
            }
        )
        value = self.responses[key]
        if isinstance(value, Exception):
            raise value
        if isinstance(value, tuple):
            return FakeHttpResponse(value[1], status=value[0])
        return FakeHttpResponse(value)

    def get(
        self, url: str, params: Any = None, headers: Any = None
    ) -> FakeHttpResponse:
        return self.request("GET", url, params=params, headers=headers)

    async def close(self) -> None:
        self.closed = True

    def requests_for(self, path: str) -> list[dict[str, Any]]:
        return [request for request in self.requests if request["path"] == path]


def as_session(fake: FakeHttpSession) -> aiohttp.ClientSession:
    """Type-erase the fake for constructor injection."""
    return fake  # type: ignore[return-value]


# === Realistic API payloads ===


CLOB_MARKET_PAYLOAD: dict[str, Any] = {
    "enable_order_book": True,
    "active": True,
    "closed": False,
    "archived": False,
    "accepting_orders": True,
    "accepting_order_timestamp": "2025-06-01T00:00:00Z",
    "minimum_order_size": 5,
    "minimum_tick_size": 0.01,
    "condition_id": CONDITION_ID,
    "question_id": "0x" + "34" * 32,
    "question": "Will Bitcoin reach $150k by end of 2025?",
    "description": "Resolves Yes if BTC trades at or above $150,000.",
    "market_slug": "will-bitcoin-reach-150k",
    "end_date_iso": "2025-12-31T00:00:00Z",
    "game_start_time": None,
    "seconds_delay": 0,
    "fpmm": "",
    "maker_base_fee": 0,
    "taker_base_fee": 0,
    "neg_risk": False,
    "notifications_enabled": True,
    "icon": "https://polymarket-upload.s3.us-east-2.amazonaws.com/btc.png",
    "image": "https://polymarket-upload.s3.us-east-2.amazonaws.com/btc.png",
    "rewards": {"rates": None, "min_size": 0, "max_spread": 0},
    "is_50_50_outcome": False,
    "tags": ["Crypto", "Bitcoin"],
    "tokens": [
        {"token_id": TOKEN_ID_YES, "outcome": "Yes", "price": 0.65, "winner": False},
        {"token_id": TOKEN_ID_NO, "outcome": "No", "price": 0.35, "winner": False},
    ],
}

GAMMA_MARKET_PAYLOAD: dict[str, Any] = {
    "id": "253591",
    "question": "Will Bitcoin reach $150k by end of 2025?",
    "conditionId": CONDITION_ID,
    "slug": "will-bitcoin-reach-150k",
    "description": "Resolves Yes if BTC trades at or above $150,000.",
    "category": "Crypto",
    "endDate": "2025-12-31T12:00:00Z",
    "startDate": "2025-01-04T22:58:00.169Z",
    "createdAt": "2025-01-04T22:58:00.169Z",
    "outcomes": '["Yes", "No"]',
    "outcomePrices": '["0.65", "0.35"]',
    "clobTokenIds": f'["{TOKEN_ID_YES}", "{TOKEN_ID_NO}"]',
    "liquidity": "302845.92",
    "volume": "1091701.53",
    "volumeNum": 1091701.53,
    "liquidityNum": 302845.92,
    "active": True,
    "closed": False,
    "archived": False,
    "umaResolutionStatus": "",
}

DATA_API_POSITION_PAYLOAD: dict[str, Any] = {
    "proxyWallet": TEST_ADDRESS,
    "asset": TOKEN_ID_YES,
    "conditionId": CONDITION_ID,
    "size": 100,
    "avgPrice": 0.55,
    "initialValue": 55.0,
    "currentValue": 65.0,
    "cashPnl": 10.0,
    "percentPnl": 18.18,
    "totalBought": 100,
    "realizedPnl": 0,
    "curPrice": 0.65,
    "redeemable": False,
    "title": "Will Bitcoin reach $150k by end of 2025?",
    "slug": "will-bitcoin-reach-150k",
    "outcome": "Yes",
    "outcomeIndex": 0,
    "oppositeOutcome": "No",
    "oppositeAsset": TOKEN_ID_NO,
    "endDate": "2025-12-31T12:00:00Z",
    "negativeRisk": False,
}

ORDER_POST_RESPONSE_PAYLOAD: dict[str, Any] = {
    "success": True,
    "errorMsg": "",
    "orderID": "0x" + "77" * 32,
    "transactionsHashes": None,
    "status": "live",
    "takingAmount": "",
    "makingAmount": "",
}


# === JSON-RPC / blockchain fixtures (Polygon chain id 137) ===


def eth_call_handler(params: Any) -> str:
    """Answer USDC view calls with realistic ABI-encoded payloads."""
    call = params[0]
    data = str(call.get("data") or call.get("input"))
    selector = data[:10].lower()
    if selector == SEL_DECIMALS:
        return "0x" + encode(["uint8"], [6]).hex()
    if selector == SEL_NAME:
        return "0x" + encode(["string"], ["USD Coin"]).hex()
    if selector == SEL_SYMBOL:
        return "0x" + encode(["string"], ["USDC"]).hex()
    raise AssertionError(f"Unexpected eth_call selector: {selector}")


@pytest.fixture
def rpc_provider() -> FakeRPCProvider:
    return FakeRPCProvider(
        {
            "eth_call": eth_call_handler,
            "eth_chainId": "0x89",  # 137
            "eth_getBalance": "0xde0b6b3a7640000",
            "eth_getTransactionCount": "0x2",
            "eth_sendRawTransaction": TX_HASH_HEX,
            "eth_estimateGas": "0xc350",  # 50,000
            "eth_getBlockByNumber": make_block_payload(),
            "eth_maxPriorityFeePerGas": "0x3b9aca00",  # 1 gwei
            "eth_feeHistory": {
                "oldestBlock": "0x1",
                "baseFeePerGas": ["0x2540be400", "0x2540be400"],
                "gasUsedRatio": [0.5],
                "reward": [["0x3b9aca00", "0x3b9aca00", "0x3b9aca00", "0x3b9aca00"]],
            },
        }
    )


@pytest.fixture
def polygon_config(rpc_provider: FakeRPCProvider) -> EthereumBlockchainConfiguration:
    return EthereumBlockchainConfiguration(
        platform=BlockchainPlatform(
            identifier="polygon",
            type=EthereumBlockchainType,
            chain_id=137,
        ),
        native_asset=EthereumNativeAssetConfiguration(),
        connectivity=EthereumConnectivityConfiguration(rpc_provider=rpc_provider),
        explorer=None,
    )


@pytest.fixture
def polygon_blockchain(
    polygon_config: EthereumBlockchainConfiguration,
) -> EthereumBlockchain:
    return EthereumBlockchain(configuration=polygon_config)


@pytest.fixture
def test_account() -> LocalAccount:
    """Deterministic account matching the pinned EIP-712 vectors."""
    account = Account.from_key(TEST_PRIVATE_KEY)
    assert account.address == TEST_ADDRESS
    return account


@pytest.fixture
async def ethereum_wallet(
    polygon_config: EthereumBlockchainConfiguration,
    test_account: LocalAccount,
    polygon_blockchain: EthereumBlockchain,
) -> Any:
    wallet = build_wallet(polygon_config, test_account, polygon_blockchain)
    if wallet._background_tasks:
        await asyncio.gather(*wallet._background_tasks, return_exceptions=True)
    return wallet


@pytest.fixture
def polymarket_config() -> PolymarketConfiguration:
    """Zero-fee configuration so orders match the pinned vectors exactly."""
    return PolymarketConfiguration(fee_rate=Decimal("0"))


@pytest.fixture
def polymarket(
    polymarket_config: PolymarketConfiguration,
    polygon_blockchain: EthereumBlockchain,
) -> Polymarket:
    return Polymarket(polymarket_config, polygon_blockchain)


# === Order amount math ===


class TestOrderAmounts:
    def test_buy_price_060_size_100(self) -> None:
        maker, taker = compute_order_amounts(
            OrderSide.BUY, Decimal("0.60"), Decimal("100")
        )
        assert maker == 60_000_000  # 60 USDC in 6-decimal base units
        assert taker == 100_000_000  # 100 shares in 6-decimal base units

    def test_sell_price_060_size_100(self) -> None:
        maker, taker = compute_order_amounts(
            OrderSide.SELL, Decimal("0.60"), Decimal("100")
        )
        assert maker == 100_000_000
        assert taker == 60_000_000

    def test_size_rounds_down_to_hundredths(self) -> None:
        maker, taker = compute_order_amounts(
            OrderSide.BUY, Decimal("0.333"), Decimal("33.335")
        )
        # size floors to 33.33; 0.333 * 33.33 = 11.098890 exactly (5 dp)
        assert taker == 33_330_000
        assert maker == 11_098_890

    def test_price_rounds_half_up_to_tick(self) -> None:
        maker, taker = compute_order_amounts(
            OrderSide.BUY, Decimal("0.6005"), Decimal("10")
        )
        # 0.6005 rounds HALF_UP to 0.601 at the default 0.001 tick
        assert maker == 6_010_000
        assert taker == 10_000_000

    def test_coarser_tick(self) -> None:
        maker, taker = compute_order_amounts(
            OrderSide.SELL, Decimal("0.128"), Decimal("7"), tick_size=Decimal("0.01")
        )
        # 0.128 rounds to 0.13 at a 0.01 tick
        assert maker == 7_000_000
        assert taker == 910_000

    def test_price_out_of_range_raises(self) -> None:
        with pytest.raises(ValueError, match="strictly between 0 and 1"):
            compute_order_amounts(OrderSide.BUY, Decimal("0"), Decimal("10"))
        with pytest.raises(ValueError, match="strictly between 0 and 1"):
            compute_order_amounts(OrderSide.BUY, Decimal("1"), Decimal("10"))

    def test_price_rounding_to_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="rounds to"):
            compute_order_amounts(OrderSide.BUY, Decimal("0.0004"), Decimal("10"))

    def test_size_below_step_raises(self) -> None:
        with pytest.raises(ValueError, match="at least"):
            compute_order_amounts(OrderSide.BUY, Decimal("0.5"), Decimal("0.001"))

    def test_invalid_tick_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported tick size"):
            compute_order_amounts(
                OrderSide.BUY, Decimal("0.5"), Decimal("10"), tick_size=Decimal("0.02")
            )


# === EIP-712 order signing ===


class TestEip712OrderSigning:
    def test_order_typehash_matches_official_struct(self) -> None:
        struct_signature = (
            "Order("
            + ",".join(
                f"{type_} {name}"
                for name, type_ in [
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
                ]
            )
            + ")"
        )
        assert "0x" + keccak(text=struct_signature).hex() == ORDER_TYPEHASH

    def test_typed_data_structure_is_exact(self) -> None:
        typed = build_order_typed_data(pinned_order(), chain_id=137)
        assert typed["primaryType"] == "Order"
        assert typed["domain"] == {
            "name": "Polymarket CTF Exchange",
            "version": "1",
            "chainId": 137,
            "verifyingContract": CTF_EXCHANGE_ADDRESS,
        }
        assert typed["types"]["Order"] == [
            {"name": "salt", "type": "uint256"},
            {"name": "maker", "type": "address"},
            {"name": "signer", "type": "address"},
            {"name": "taker", "type": "address"},
            {"name": "tokenId", "type": "uint256"},
            {"name": "makerAmount", "type": "uint256"},
            {"name": "takerAmount", "type": "uint256"},
            {"name": "expiration", "type": "uint256"},
            {"name": "nonce", "type": "uint256"},
            {"name": "feeRateBps", "type": "uint256"},
            {"name": "side", "type": "uint8"},
            {"name": "signatureType", "type": "uint8"},
        ]
        assert typed["message"] == {
            "salt": PINNED_SALT,
            "maker": TEST_ADDRESS,
            "signer": TEST_ADDRESS,
            "taker": "0x0000000000000000000000000000000000000000",
            "tokenId": int(TOKEN_ID_YES),
            "makerAmount": PINNED_MAKER_AMOUNT,
            "takerAmount": PINNED_TAKER_AMOUNT,
            "expiration": 0,
            "nonce": 0,
            "feeRateBps": 0,
            "side": 0,
            "signatureType": 0,
        }

    def test_signature_matches_pinned_vector(self) -> None:
        signed = sign_clob_order(
            pinned_order(), chain_id=137, private_key=TEST_PRIVATE_KEY
        )
        assert signed.signature == PINNED_SIGNATURE

    def test_digest_matches_pinned_vector(self) -> None:
        typed = build_order_typed_data(pinned_order(), chain_id=137)
        signed_message = Account.sign_typed_data(TEST_PRIVATE_KEY, full_message=typed)
        assert signed_message.message_hash.to_0x_hex() == PINNED_DIGEST
        assert signed_message.signature.to_0x_hex() == PINNED_SIGNATURE

    def test_manual_struct_hash_cross_check(self) -> None:
        """Independently recompute the digest with raw keccak/abi.encode.

        This locks eth_account's typed-data encoding against the EIP-712
        specification: typehash, struct hash, domain separator and the final
        0x1901 digest are all rebuilt by hand and must match the pinned
        values used for signing.
        """
        order = pinned_order()
        struct_hash = keccak(
            encode(
                [
                    "bytes32",
                    "uint256",
                    "address",
                    "address",
                    "address",
                    "uint256",
                    "uint256",
                    "uint256",
                    "uint256",
                    "uint256",
                    "uint256",
                    "uint8",
                    "uint8",
                ],
                [
                    bytes.fromhex(ORDER_TYPEHASH[2:]),
                    order.salt,
                    order.maker,
                    order.signer,
                    order.taker,
                    order.token_id,
                    order.maker_amount,
                    order.taker_amount,
                    order.expiration,
                    order.nonce,
                    order.fee_rate_bps,
                    int(order.side),
                    int(order.signature_type),
                ],
            )
        )
        assert "0x" + struct_hash.hex() == PINNED_STRUCT_HASH

        domain_separator = keccak(
            encode(
                ["bytes32", "bytes32", "bytes32", "uint256", "address"],
                [
                    keccak(
                        text=(
                            "EIP712Domain(string name,string version,"
                            "uint256 chainId,address verifyingContract)"
                        )
                    ),
                    keccak(text="Polymarket CTF Exchange"),
                    keccak(text="1"),
                    137,
                    CTF_EXCHANGE_ADDRESS,
                ],
            )
        )
        assert "0x" + domain_separator.hex() == PINNED_DOMAIN_SEPARATOR

        digest = keccak(b"\x19\x01" + domain_separator + struct_hash)
        assert "0x" + digest.hex() == PINNED_DIGEST

    def test_signature_recovers_signer(self) -> None:
        signed = sign_clob_order(
            pinned_order(), chain_id=137, private_key=TEST_PRIVATE_KEY
        )
        typed = build_order_typed_data(signed.order, chain_id=137)
        recovered = Account.recover_message(
            encode_typed_data(full_message=typed), signature=signed.signature
        )
        assert recovered == TEST_ADDRESS

    def test_api_payload_wire_format(self) -> None:
        signed = sign_clob_order(
            pinned_order(), chain_id=137, private_key=TEST_PRIVATE_KEY
        )
        payload = signed.to_api_payload()
        assert payload == {
            "salt": PINNED_SALT,
            "maker": TEST_ADDRESS,
            "signer": TEST_ADDRESS,
            "taker": "0x0000000000000000000000000000000000000000",
            "tokenId": TOKEN_ID_YES,
            "makerAmount": "60000000",
            "takerAmount": "100000000",
            "expiration": "0",
            "nonce": "0",
            "feeRateBps": "0",
            "side": "BUY",
            "signatureType": 0,
            "signature": PINNED_SIGNATURE,
        }


# === L2 HMAC auth ===


class TestHmacAuth:
    def test_pinned_vector_with_body(self) -> None:
        signature = build_hmac_signature(
            PINNED_SECRET,
            "1700000000",
            "POST",
            "/order",
            '{"order": {"salt": 1}}',
        )
        assert signature == PINNED_HMAC_WITH_BODY

    def test_pinned_vector_without_body(self) -> None:
        signature = build_hmac_signature(PINNED_SECRET, "1700000000", "GET", "/orders")
        assert signature == PINNED_HMAC_NO_BODY

    def test_body_changes_signature(self) -> None:
        signature = build_hmac_signature(
            PINNED_SECRET, "1700000000", "POST", "/order", '{"order": {"salt": 2}}'
        )
        assert signature != PINNED_HMAC_WITH_BODY


# === Query param serialization (bool regression) ===


class TestQueryParamSerialization:
    def test_bools_become_lowercase_strings(self) -> None:
        assert serialize_query_params({"active": True, "closed": False}) == {
            "active": "true",
            "closed": "false",
        }

    def test_none_dropped_and_values_stringified(self) -> None:
        assert serialize_query_params({"limit": 10, "cursor": None, "s": "x"}) == {
            "limit": "10",
            "s": "x",
        }

    def test_fake_session_rejects_raw_bools_like_yarl(self) -> None:
        """Meta-test: the HTTP fake preserves yarl's bool rejection."""
        fake = FakeHttpSession({("GET", "/markets"): []})
        with pytest.raises(TypeError):
            fake.get("https://gamma-api.polymarket.com/markets", {"active": True})


# === CLOB client ===


class TestClobClient:
    def make_client(
        self,
        responses: dict[tuple[str, str], Any],
        credentials: ClobCredentials | None = None,
    ) -> tuple[ClobClient, FakeHttpSession]:
        fake = FakeHttpSession(responses)
        client = ClobClient(credentials=credentials, session=as_session(fake))
        return client, fake

    async def test_get_midpoint(self) -> None:
        client, fake = self.make_client({("GET", "/midpoint"): {"mid": "0.655"}})
        assert await client.get_midpoint(TOKEN_ID_YES) == Decimal("0.655")
        assert fake.requests[0]["params"] == {"token_id": TOKEN_ID_YES}

    async def test_get_price_sends_side(self) -> None:
        client, fake = self.make_client({("GET", "/price"): {"price": "0.66"}})
        assert await client.get_price(TOKEN_ID_YES, "buy") == Decimal("0.66")
        assert fake.requests[0]["params"] == {"token_id": TOKEN_ID_YES, "side": "buy"}

    async def test_get_book(self) -> None:
        book_payload = {
            "market": CONDITION_ID,
            "asset_id": TOKEN_ID_YES,
            "bids": [{"price": "0.64", "size": "120"}],
            "asks": [{"price": "0.66", "size": "80"}],
            "hash": "0x" + "55" * 20,
        }
        client, fake = self.make_client({("GET", "/book"): book_payload})
        book = await client.get_book(TOKEN_ID_YES)
        assert book == book_payload

    async def test_get_market(self) -> None:
        client, _ = self.make_client(
            {("GET", f"/markets/{CONDITION_ID}"): CLOB_MARKET_PAYLOAD}
        )
        market = await client.get_market(CONDITION_ID)
        assert market["condition_id"] == CONDITION_ID

    async def test_post_order_requires_credentials(self) -> None:
        client, _ = self.make_client({("POST", "/order"): ORDER_POST_RESPONSE_PAYLOAD})
        signed = sign_clob_order(
            pinned_order(), chain_id=137, private_key=TEST_PRIVATE_KEY
        )
        with pytest.raises(ValueError, match="credentials are required"):
            await client.post_order(signed)

    async def test_post_order_payload_and_auth_headers(self) -> None:
        credentials = ClobCredentials(
            api_key="key-123", secret=PINNED_SECRET, passphrase="pass-456"
        )
        client, fake = self.make_client(
            {("POST", "/order"): ORDER_POST_RESPONSE_PAYLOAD}, credentials=credentials
        )
        signed = sign_clob_order(
            pinned_order(), chain_id=137, private_key=TEST_PRIVATE_KEY
        )

        response = await client.post_order(signed, order_type="GTC")

        assert response.success is True
        assert response.order_id == "0x" + "77" * 32
        assert response.status == "live"
        assert response.error_msg == ""

        request = fake.requests[0]
        body = json.loads(request["body"])
        assert body == {
            "order": signed.to_api_payload(),
            "owner": "key-123",
            "orderType": "GTC",
        }

        headers = request["headers"]
        assert headers["POLY_ADDRESS"] == TEST_ADDRESS
        assert headers["POLY_API_KEY"] == "key-123"
        assert headers["POLY_PASSPHRASE"] == "pass-456"
        assert headers["Content-Type"] == "application/json"
        # The signature must cover the exact body string that was sent
        expected_signature = build_hmac_signature(
            PINNED_SECRET, headers["POLY_TIMESTAMP"], "POST", "/order", request["body"]
        )
        assert headers["POLY_SIGNATURE"] == expected_signature

    async def test_http_error_raises_clob_api_error(self) -> None:
        client, _ = self.make_client(
            {("GET", "/midpoint"): (400, {"error": "invalid token id"})}
        )
        with pytest.raises(ClobApiError, match="status 400") as excinfo:
            await client.get_midpoint("not-a-token")
        assert excinfo.value.status == 400
        assert excinfo.value.payload == {"error": "invalid token id"}

    async def test_transport_error_raises_clob_api_error(self) -> None:
        client, _ = self.make_client(
            {("GET", "/midpoint"): aiohttp.ClientError("connection reset")}
        )
        with pytest.raises(ClobApiError, match="connection reset") as excinfo:
            await client.get_midpoint(TOKEN_ID_YES)
        assert excinfo.value.status == 0

    async def test_injected_session_is_closed_on_close(self) -> None:
        client, fake = self.make_client({})
        await client.close()
        assert fake.closed is True

    async def test_owned_session_lifecycle(self) -> None:
        async with ClobClient() as client:
            session = await client._get_session()
            assert isinstance(session, aiohttp.ClientSession)
            assert session.closed is False
        assert session.closed is True
        assert client._session is None


# === Market / position parsing ===


class TestMarketParsing:
    def test_clob_market_active(self, polymarket: Polymarket) -> None:
        market = polymarket._parse_clob_market(CLOB_MARKET_PAYLOAD)

        assert isinstance(market, BettingMarketModel)
        assert market.market_id == CONDITION_ID
        assert market.status == MarketStatus.ACTIVE
        assert market.category == "Crypto"
        assert [outcome.outcome_text for outcome in market.outcomes] == ["Yes", "No"]
        yes_token = market.outcomes[0].outcome_tokens[0]
        assert yes_token.token_id == TOKEN_ID_YES
        assert yes_token.current_price == Decimal("0.65")
        assert yes_token.probability == Decimal("0.65")
        assert market.metadata["parser_version"] == "2025-08"
        assert market.end_date is not None and market.end_date.year == 2025

    def test_clob_market_collateral_is_real_erc20_token(
        self, polymarket: Polymarket
    ) -> None:
        """Regression: the old USDCAsset stub crashed with real models."""
        market = polymarket._parse_clob_market(CLOB_MARKET_PAYLOAD)
        collateral = market.collateral_asset
        assert isinstance(collateral, BlockchainAsset)
        assert isinstance(collateral, ERC20Token)
        assert collateral.data is not None
        assert collateral.data.symbol == "USDC"
        assert collateral.data.decimals == 6
        assert collateral.address.raw == USDC_ADDRESS
        assert collateral.convert_to_raw(Decimal("1.5")) == 1_500_000

    def test_clob_market_resolved_wins_over_closed(
        self, polymarket: Polymarket
    ) -> None:
        """Regression: resolved+closed markets were reported CLOSED."""
        payload = json.loads(json.dumps(CLOB_MARKET_PAYLOAD))
        payload["closed"] = True
        payload["active"] = False
        payload["tokens"][1]["winner"] = True
        payload["tokens"][0]["price"] = 0
        payload["tokens"][1]["price"] = 1

        market = polymarket._parse_clob_market(payload)
        assert market.status == MarketStatus.RESOLVED
        assert market.resolved_outcome_id == "1"
        assert market.outcomes[1].is_winning_outcome is True
        assert market.winning_outcome is market.outcomes[1]

    def test_clob_market_closed_without_winner(self, polymarket: Polymarket) -> None:
        payload = json.loads(json.dumps(CLOB_MARKET_PAYLOAD))
        payload["closed"] = True
        payload["active"] = False
        market = polymarket._parse_clob_market(payload)
        assert market.status == MarketStatus.CLOSED
        assert market.resolved_outcome_id is None

    def test_gamma_market_active(self, polymarket: Polymarket) -> None:
        market = polymarket._parse_gamma_market(GAMMA_MARKET_PAYLOAD)
        assert market.market_id == CONDITION_ID
        assert market.status == MarketStatus.ACTIVE
        assert market.total_volume == Decimal("1091701.53")
        assert market.total_liquidity == Decimal("302845.92")
        assert market.outcomes[0].outcome_tokens[0].token_id == TOKEN_ID_YES
        assert market.outcomes[1].outcome_tokens[0].current_price == Decimal("0.35")
        assert market.creation_date.year == 2025

    def test_gamma_market_resolved_with_winner(self, polymarket: Polymarket) -> None:
        payload = dict(GAMMA_MARKET_PAYLOAD)
        payload["closed"] = True
        payload["active"] = False
        payload["umaResolutionStatus"] = "resolved"
        payload["outcomePrices"] = '["1", "0"]'
        market = polymarket._parse_gamma_market(payload)
        assert market.status == MarketStatus.RESOLVED
        assert market.resolved_outcome_id == "0"
        assert market.outcomes[0].is_winning_outcome is True

    def test_gamma_market_resolved_without_winner_downgrades_to_closed(
        self, polymarket: Polymarket
    ) -> None:
        """Regression: resolved-without-winner used to raise ValidationError."""
        payload = dict(GAMMA_MARKET_PAYLOAD)
        payload["umaResolutionStatus"] = "resolved"
        payload["outcomePrices"] = '["0.5", "0.5"]'
        market = polymarket._parse_gamma_market(payload)
        assert market.status == MarketStatus.CLOSED
        assert market.resolved_outcome_id is None

    def test_gamma_market_tolerates_list_form_fields(
        self, polymarket: Polymarket
    ) -> None:
        payload = dict(GAMMA_MARKET_PAYLOAD)
        payload["outcomes"] = ["Yes", "No"]
        payload["outcomePrices"] = ["0.65", "0.35"]
        payload["clobTokenIds"] = [TOKEN_ID_YES, TOKEN_ID_NO]
        market = polymarket._parse_gamma_market(payload)
        assert market.outcomes[0].outcome_tokens[0].token_id == TOKEN_ID_YES

    def test_position_parsing(self, polymarket: Polymarket) -> None:
        position = polymarket._parse_position_data(DATA_API_POSITION_PAYLOAD)
        assert isinstance(position, BettingPosition)
        assert position.market_id == CONDITION_ID
        assert position.outcome_token.token_id == TOKEN_ID_YES
        assert position.outcome_token.outcome_name == "Yes"
        assert position.shares_owned == Decimal("100")
        assert position.average_price == Decimal("0.55")
        assert position.total_invested == Decimal("55.00")
        assert position.current_value == Decimal("65.00")
        assert position.unrealized_pnl == Decimal("10.00")
        assert position.protocol == "Polymarket"


# === Read path over the HTTP boundary ===


class TestReadPath:
    def make_polymarket(
        self,
        blockchain: EthereumBlockchain,
        clob_responses: dict[tuple[str, str], Any] | None = None,
        http_responses: dict[tuple[str, str], Any] | None = None,
    ) -> tuple[Polymarket, FakeHttpSession, FakeHttpSession]:
        clob_fake = FakeHttpSession(clob_responses or {})
        http_fake = FakeHttpSession(http_responses or {})
        strategy = Polymarket(
            PolymarketConfiguration(fee_rate=Decimal("0.02")),
            blockchain,
            clob_client=ClobClient(session=as_session(clob_fake)),
            session=as_session(http_fake),
        )
        return strategy, clob_fake, http_fake

    async def test_get_market_uses_clob_host(
        self, polygon_blockchain: EthereumBlockchain
    ) -> None:
        strategy, clob_fake, _ = self.make_polymarket(
            polygon_blockchain,
            clob_responses={("GET", f"/markets/{CONDITION_ID}"): CLOB_MARKET_PAYLOAD},
        )
        market = await strategy.get_market(CONDITION_ID)
        assert market.market_id == CONDITION_ID
        assert clob_fake.requests[0]["url"] == (
            f"https://clob.polymarket.com/markets/{CONDITION_ID}"
        )

    async def test_get_markets_bool_params_serialized(
        self, polygon_blockchain: EthereumBlockchain
    ) -> None:
        """Regression: status filters used to pass Python bools to aiohttp."""
        strategy, _, http_fake = self.make_polymarket(
            polygon_blockchain,
            http_responses={("GET", "/markets"): [GAMMA_MARKET_PAYLOAD]},
        )
        markets = await strategy.get_markets(status="active", limit=10)
        assert len(markets) == 1
        request = http_fake.requests[0]
        assert request["url"].startswith("https://gamma-api.polymarket.com/markets?")
        assert request["params"] == {
            "limit": "10",
            "offset": "0",
            "active": "true",
            "closed": "false",
        }

    async def test_get_markets_closed_filter(
        self, polygon_blockchain: EthereumBlockchain
    ) -> None:
        strategy, _, http_fake = self.make_polymarket(
            polygon_blockchain, http_responses={("GET", "/markets"): []}
        )
        assert await strategy.get_markets(status="closed") == []
        assert http_fake.requests[0]["params"]["closed"] == "true"

    async def test_get_user_positions_uses_data_api(
        self, polygon_blockchain: EthereumBlockchain
    ) -> None:
        strategy, _, http_fake = self.make_polymarket(
            polygon_blockchain,
            http_responses={("GET", "/positions"): [DATA_API_POSITION_PAYLOAD]},
        )
        positions = await strategy.get_user_positions(TEST_ADDRESS, CONDITION_ID)
        assert len(positions) == 1
        assert positions[0].shares_owned == Decimal("100")
        request = http_fake.requests[0]
        assert request["url"].startswith("https://data-api.polymarket.com/positions?")
        assert request["params"] == {"user": TEST_ADDRESS, "market": CONDITION_ID}

    async def test_get_outcome_token_price_uses_midpoint(
        self, polygon_blockchain: EthereumBlockchain
    ) -> None:
        strategy, clob_fake, _ = self.make_polymarket(
            polygon_blockchain, clob_responses={("GET", "/midpoint"): {"mid": "0.65"}}
        )
        price = await strategy.get_outcome_token_price(CONDITION_ID, TOKEN_ID_YES)
        assert price == Decimal("0.65")
        assert clob_fake.requests[0]["params"] == {"token_id": TOKEN_ID_YES}

    async def test_calculate_buy_quote(
        self, polygon_blockchain: EthereumBlockchain
    ) -> None:
        strategy, _, _ = self.make_polymarket(
            polygon_blockchain, clob_responses={("GET", "/midpoint"): {"mid": "0.65"}}
        )
        shares, total_cost = await strategy.calculate_buy_quote(
            CONDITION_ID, TOKEN_ID_YES, Decimal("100")
        )
        assert shares == Decimal("100") / Decimal("0.65")
        assert total_cost == Decimal("102")  # 100 + 2% fee

    async def test_calculate_sell_quote(
        self, polygon_blockchain: EthereumBlockchain
    ) -> None:
        strategy, _, _ = self.make_polymarket(
            polygon_blockchain, clob_responses={("GET", "/midpoint"): {"mid": "0.65"}}
        )
        payout, fees = await strategy.calculate_sell_quote(
            CONDITION_ID, TOKEN_ID_YES, Decimal("100")
        )
        assert fees == Decimal("100") * Decimal("0.65") * Decimal("0.02")
        assert payout == Decimal("100") * Decimal("0.65") - fees

    async def test_api_error_propagates(
        self, polygon_blockchain: EthereumBlockchain
    ) -> None:
        strategy, _, http_fake = self.make_polymarket(
            polygon_blockchain,
            http_responses={("GET", "/markets"): (500, {"error": "upstream"})},
        )
        with pytest.raises(ValueError, match="status 500"):
            await strategy.get_markets()

    async def test_session_lifecycle(
        self, polygon_blockchain: EthereumBlockchain
    ) -> None:
        strategy, clob_fake, http_fake = self.make_polymarket(polygon_blockchain)
        # Injected CLOB client stays caller-owned; the gamma session is closed
        async with strategy:
            pass
        assert http_fake.closed is True
        assert clob_fake.closed is False
        assert strategy._session is None


# === Order building (facade write path, off-chain) ===


class TestOrderBuilding:
    def test_build_order_requires_wallet(self, polymarket: Polymarket) -> None:
        with pytest.raises(ValueError, match="No wallet is bound"):
            polymarket.build_order(
                TOKEN_ID_YES, OrderSide.BUY, Decimal("0.6"), Decimal("100")
            )

    def test_set_wallet_rejects_wrong_type(self, polymarket: Polymarket) -> None:
        with pytest.raises(TypeError, match="requires an EthereumWallet"):
            polymarket.set_wallet(object())  # type: ignore[arg-type]

    async def test_build_order_matches_pinned_signature(
        self, polymarket: Polymarket, ethereum_wallet: Any
    ) -> None:
        """End-to-end: wallet-bound order building reproduces the pinned vector."""
        polymarket.set_wallet(ethereum_wallet)
        signed = polymarket.build_order(
            TOKEN_ID_YES,
            OrderSide.BUY,
            price=Decimal("0.60"),
            size=Decimal("100"),
            salt=PINNED_SALT,
        )
        assert signed.order.maker == TEST_ADDRESS
        assert signed.order.signer == TEST_ADDRESS
        assert signed.order.maker_amount == PINNED_MAKER_AMOUNT
        assert signed.order.taker_amount == PINNED_TAKER_AMOUNT
        assert signed.order.fee_rate_bps == 0
        assert signed.order.signature_type == SignatureType.EOA
        assert signed.signature == PINNED_SIGNATURE

    async def test_fee_rate_wired_into_orders(
        self, polygon_blockchain: EthereumBlockchain, ethereum_wallet: Any
    ) -> None:
        strategy = Polymarket(
            PolymarketConfiguration(fee_rate=Decimal("0.02")),
            polygon_blockchain,
            wallet=ethereum_wallet,
        )
        signed = strategy.build_order(
            TOKEN_ID_YES, OrderSide.SELL, Decimal("0.5"), Decimal("10")
        )
        assert signed.order.fee_rate_bps == 200

    async def test_build_buy_transaction_wraps_signed_order(
        self, polymarket: Polymarket, ethereum_wallet: Any
    ) -> None:
        polymarket.set_wallet(ethereum_wallet)
        transaction = await polymarket.build_buy_transaction(
            market_id=CONDITION_ID,
            outcome_token_id=TOKEN_ID_YES,
            amount=Decimal("60"),
            max_price=Decimal("0.60"),
            user_address=TEST_ADDRESS,
        )

        assert isinstance(transaction, EthereumTransaction)
        assert transaction.current_state == BlockchainTransactionState.PENDING_BROADCAST
        assert transaction.signed_transaction is None
        assert transaction.owner_identifier == ethereum_wallet.identifier
        assert transaction.client_operation_id.startswith("polymarket_buy_")

        payload = transaction.other_data["clob_order"]
        assert transaction.other_data["market_id"] == CONDITION_ID
        # amount / max_price = 100 shares at 0.60 -> BUY 60 USDC for 100 shares
        assert payload["side"] == "BUY"
        assert payload["makerAmount"] == "60000000"
        assert payload["takerAmount"] == "100000000"
        assert payload["tokenId"] == TOKEN_ID_YES
        assert payload["maker"] == TEST_ADDRESS

        # The embedded signature must cryptographically verify for the wallet
        order = ClobOrder(
            salt=payload["salt"],
            maker=payload["maker"],
            signer=payload["signer"],
            taker=payload["taker"],
            token_id=int(payload["tokenId"]),
            maker_amount=int(payload["makerAmount"]),
            taker_amount=int(payload["takerAmount"]),
            expiration=int(payload["expiration"]),
            nonce=int(payload["nonce"]),
            fee_rate_bps=int(payload["feeRateBps"]),
            side=OrderSide.BUY,
            signature_type=SignatureType(payload["signatureType"]),
        )
        typed = build_order_typed_data(order, chain_id=137)
        recovered = Account.recover_message(
            encode_typed_data(full_message=typed), signature=payload["signature"]
        )
        assert recovered == TEST_ADDRESS

    async def test_build_sell_transaction_amounts(
        self, polymarket: Polymarket, ethereum_wallet: Any
    ) -> None:
        polymarket.set_wallet(ethereum_wallet)
        transaction = await polymarket.build_sell_transaction(
            market_id=CONDITION_ID,
            outcome_token_id=TOKEN_ID_YES,
            shares=Decimal("100"),
            min_price=Decimal("0.60"),
            user_address=TEST_ADDRESS,
        )
        payload = transaction.other_data["clob_order"]
        assert payload["side"] == "SELL"
        assert payload["makerAmount"] == "100000000"  # 100 shares
        assert payload["takerAmount"] == "60000000"  # 60 USDC
        assert transaction.client_operation_id.startswith("polymarket_sell_")

    async def test_build_buy_requires_wallet(self, polymarket: Polymarket) -> None:
        with pytest.raises(ValueError, match="No wallet is bound"):
            await polymarket.build_buy_transaction(
                CONDITION_ID,
                TOKEN_ID_YES,
                Decimal("60"),
                Decimal("0.60"),
                TEST_ADDRESS,
            )

    async def test_build_buy_rejects_foreign_address(
        self, polymarket: Polymarket, ethereum_wallet: Any
    ) -> None:
        polymarket.set_wallet(ethereum_wallet)
        with pytest.raises(ValueError, match="does not match the bound wallet"):
            await polymarket.build_buy_transaction(
                CONDITION_ID,
                TOKEN_ID_YES,
                Decimal("60"),
                Decimal("0.60"),
                OTHER_ADDRESS,
            )

    async def test_place_buy_posts_signed_order(
        self, polygon_blockchain: EthereumBlockchain, ethereum_wallet: Any
    ) -> None:
        clob_fake = FakeHttpSession({("POST", "/order"): ORDER_POST_RESPONSE_PAYLOAD})
        credentials = ClobCredentials(
            api_key="key-123", secret=PINNED_SECRET, passphrase="pass-456"
        )
        strategy = Polymarket(
            PolymarketConfiguration(fee_rate=Decimal("0"), credentials=credentials),
            polygon_blockchain,
            wallet=ethereum_wallet,
            clob_client=ClobClient(
                credentials=credentials, session=as_session(clob_fake)
            ),
        )

        response = await strategy.place_buy(
            TOKEN_ID_YES, price=Decimal("0.60"), size=Decimal("100")
        )
        assert response.success is True
        assert response.order_id == "0x" + "77" * 32

        body = json.loads(clob_fake.requests[0]["body"])
        assert body["orderType"] == "GTC"
        assert body["order"]["side"] == "BUY"
        assert body["order"]["makerAmount"] == "60000000"
        assert body["order"]["takerAmount"] == "100000000"
        assert body["order"]["maker"] == TEST_ADDRESS


# === On-chain write path: redeem + approvals ===


class TestRedeemTransaction:
    async def test_requires_wallet(self, polymarket: Polymarket) -> None:
        with pytest.raises(ValueError, match="No wallet is bound"):
            await polymarket.build_redeem_transaction(CONDITION_ID, TEST_ADDRESS)

    async def test_rejects_invalid_condition_id(
        self, polymarket: Polymarket, ethereum_wallet: Any
    ) -> None:
        polymarket.set_wallet(ethereum_wallet)
        with pytest.raises(ValueError, match="32-byte condition id"):
            await polymarket.build_redeem_transaction("market-123", TEST_ADDRESS)

    async def test_builds_exact_redeem_calldata(
        self,
        polymarket: Polymarket,
        ethereum_wallet: Any,
        rpc_provider: FakeRPCProvider,
    ) -> None:
        polymarket.set_wallet(ethereum_wallet)
        transaction = await polymarket.build_redeem_transaction(
            CONDITION_ID, TEST_ADDRESS
        )

        assert isinstance(transaction, EthereumTransaction)
        assert transaction.current_state == BlockchainTransactionState.PENDING_BROADCAST
        assert transaction.signed_transaction is None
        assert transaction.client_operation_id.startswith("polymarket_redeem_")

        # redeemPositions(USDC, bytes32(0), conditionId, [1, 2])
        expected_data = (
            SEL_REDEEM_POSITIONS
            + encode(
                ["address", "bytes32", "bytes32", "uint256[]"],
                [USDC_ADDRESS, b"\x00" * 32, bytes.fromhex(CONDITION_ID[2:]), [1, 2]],
            ).hex()
        )

        tx_data = transaction.other_data["tx_data"]
        assert str(tx_data["data"]).lower() == expected_data
        assert tx_data["to"] == CONDITIONAL_TOKENS_ADDRESS
        assert tx_data["from"] == TEST_ADDRESS
        assert tx_data["chainId"] == 137
        # Gas fees come from the mocked node (10 gwei base fee, 1 gwei tip)
        assert tx_data["maxFeePerGas"] == 21_000_000_000
        assert tx_data["maxPriorityFeePerGas"] == 1_000_000_000

        # The calldata was estimated against the real RPC boundary
        estimate_calls = rpc_provider.calls_for("eth_estimateGas")
        assert estimate_calls
        assert str(estimate_calls[0][0]["data"]).lower() == expected_data

    async def test_gas_cap_applied(
        self,
        polymarket_config: PolymarketConfiguration,
        polygon_blockchain: EthereumBlockchain,
        ethereum_wallet: Any,
    ) -> None:
        strategy = Polymarket(
            polymarket_config,
            polygon_blockchain,
            wallet=ethereum_wallet,
            max_gas_price_gwei=10,
        )
        transaction = await strategy.build_redeem_transaction(
            CONDITION_ID, TEST_ADDRESS
        )
        tx_data = transaction.other_data["tx_data"]
        # Uncapped fee would be 21 gwei; the 10 gwei cap must apply
        assert tx_data["maxFeePerGas"] == 10_000_000_000
        assert tx_data["maxPriorityFeePerGas"] == 1_000_000_000


class TestApprovals:
    async def test_approve_collateral_builds_exact_calldata(
        self,
        polymarket: Polymarket,
        ethereum_wallet: Any,
        rpc_provider: FakeRPCProvider,
    ) -> None:
        polymarket.set_wallet(ethereum_wallet)
        transaction = await polymarket.approve_collateral(
            Decimal("75"), client_operation_id="approve-usdc-1"
        )
        await drain_background_tasks(ethereum_wallet)

        assert transaction.current_state == BlockchainTransactionState.BROADCASTED
        # approve(ctf_exchange, 75 USDC) with 6 decimals = 75,000,000 raw
        expected_data = (
            SEL_APPROVE
            + CTF_EXCHANGE_ADDRESS[2:].lower().rjust(64, "0")
            + f"{75_000_000:064x}"
        )
        raw_tx_hex = rpc_provider.calls_for("eth_sendRawTransaction")[0][0]
        decoded = TypedTransaction.from_bytes(HexBytes(raw_tx_hex)).as_dict()
        assert HexBytes(decoded["data"]).to_0x_hex() == expected_data
        assert HexBytes(decoded["to"]).to_0x_hex().lower() == USDC_ADDRESS.lower()
        assert decoded["chainId"] == 137

    async def test_conditional_tokens_approval_builds_exact_calldata(
        self,
        polymarket: Polymarket,
        ethereum_wallet: Any,
        rpc_provider: FakeRPCProvider,
    ) -> None:
        polymarket.set_wallet(ethereum_wallet)
        transaction = await polymarket.place_conditional_tokens_approval(
            client_operation_id="ctf-approval-1"
        )
        await drain_background_tasks(ethereum_wallet)

        assert transaction.current_state == BlockchainTransactionState.BROADCASTED
        # setApprovalForAll(ctf_exchange, true)
        expected_data = (
            SEL_SET_APPROVAL_FOR_ALL
            + CTF_EXCHANGE_ADDRESS[2:].lower().rjust(64, "0")
            + f"{1:064x}"
        )
        raw_tx_hex = rpc_provider.calls_for("eth_sendRawTransaction")[0][0]
        decoded = TypedTransaction.from_bytes(HexBytes(raw_tx_hex)).as_dict()
        assert HexBytes(decoded["data"]).to_0x_hex() == expected_data
        assert (
            HexBytes(decoded["to"]).to_0x_hex().lower()
            == CONDITIONAL_TOKENS_ADDRESS.lower()
        )

    async def test_approvals_require_wallet(self, polymarket: Polymarket) -> None:
        with pytest.raises(ValueError, match="No wallet is bound"):
            await polymarket.approve_collateral(Decimal("1"))
        with pytest.raises(ValueError, match="No wallet is bound"):
            await polymarket.place_conditional_tokens_approval()


# === Facade wiring ===


class TestFacade:
    @pytest.fixture
    def facade(self, polygon_blockchain: EthereumBlockchain) -> PolymarketBettingMarket:
        configuration = EVMBettingMarketConfiguration(
            platform=polygon_blockchain.platform,
            protocols=[PolymarketConfiguration(fee_rate=Decimal("0"))],
            max_gas_price_gwei=25,
        )
        with patch(
            "financepype.operators.dapps.dapp.OperatorFactory.get",
            return_value=polygon_blockchain,
        ):
            return PolymarketBettingMarket(configuration)

    def test_strategies_initialized(self, facade: PolymarketBettingMarket) -> None:
        assert facade.supported_protocols == ["Polymarket"]
        strategy = facade._protocol_strategies["Polymarket"]
        assert isinstance(strategy, Polymarket)
        assert strategy._max_gas_price_gwei == 25

    def test_set_wallet_forwards_to_strategies(
        self, facade: PolymarketBettingMarket, ethereum_wallet: Any
    ) -> None:
        facade.set_wallet(ethereum_wallet)
        strategy = facade._protocol_strategies["Polymarket"]
        assert isinstance(strategy, Polymarket)
        assert strategy.wallet is ethereum_wallet

    async def test_close_forwards_to_strategies(
        self, facade: PolymarketBettingMarket
    ) -> None:
        strategy = facade._protocol_strategies["Polymarket"]
        assert isinstance(strategy, Polymarket)
        fake = FakeHttpSession({})
        strategy._session = as_session(fake)
        await facade.close()
        assert fake.closed is True


# === Configuration ===


class TestPolymarketConfiguration:
    def test_defaults(self) -> None:
        config = PolymarketConfiguration()
        assert config.protocol_name == "Polymarket"
        assert config.contract_address == CTF_EXCHANGE_ADDRESS
        assert config.api_base_url == "https://clob.polymarket.com"
        assert config.gamma_api_url == "https://gamma-api.polymarket.com"
        assert config.data_api_url == "https://data-api.polymarket.com"
        assert config.conditional_tokens_address == CONDITIONAL_TOKENS_ADDRESS
        assert config.collateral_token_address == USDC_ADDRESS
        assert config.credentials is None
        assert config.signature_type == SignatureType.EOA

    def test_custom_values_preserved(self) -> None:
        config = PolymarketConfiguration(
            protocol_name="Custom Polymarket",
            api_base_url="https://custom.example.com",
            fee_rate=Decimal("0.025"),
        )
        assert config.protocol_name == "Custom Polymarket"
        assert config.api_base_url == "https://custom.example.com"
        assert config.fee_rate == Decimal("0.025")

    def test_secret_not_leaked_in_repr(self) -> None:
        credentials = ClobCredentials(
            api_key="key", secret=PINNED_SECRET, passphrase="pass"
        )
        assert PINNED_SECRET not in repr(credentials)
        assert PINNED_SECRET not in str(credentials)
