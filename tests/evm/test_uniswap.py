"""
Unit tests for the Uniswap V2/V3 strategies and the UniswapDEX facade.

All tests are network-free: the JSON-RPC boundary is replaced by a fake
provider answering factory/pair/pool/quoter calls with realistic ABI-encoded
payloads, so the real contract plumbing (web3 calldata encoding, ABI loading,
wallet transaction building) is exercised end to end. Assertions are exact:
constant-product math vectors, decoded calldata selectors and arguments,
packed V3 paths, and slippage/deadline arithmetic.
"""

import asyncio
from decimal import Decimal
from typing import Any
from unittest.mock import PropertyMock, patch

import pytest
from eth_abi import decode, encode
from eth_account import Account
from eth_account.signers.local import LocalAccount
from financepype.operations.transactions.models import BlockchainTransactionState
from financepype.platforms.blockchain import BlockchainPlatform
from web3 import Web3

from blockchainpype.dapps.router.dex import ProtocolImplementation
from blockchainpype.dapps.router.models import SwapMode, SwapRoute
from blockchainpype.evm.asset import EthereumAsset, EthereumAssetData
from blockchainpype.evm.blockchain.blockchain import (
    EthereumBlockchain,
    EthereumBlockchainType,
)
from blockchainpype.evm.blockchain.configuration import (
    EthereumBlockchainConfiguration,
    EthereumConnectivityConfiguration,
    EthereumNativeAssetConfiguration,
)
from blockchainpype.evm.blockchain.identifier import EthereumAddress
from blockchainpype.evm.dapp.abi import EthereumLocalFileABI
from blockchainpype.evm.dapp.uniswap.dex import (
    DEFAULT_V3_QUOTER_ADDRESS,
    UniswapConfiguration,
    UniswapDEX,
)
from blockchainpype.evm.dapp.uniswap.v2 import ZERO_ADDRESS, UniswapV2
from blockchainpype.evm.dapp.uniswap.v3 import UniswapV3
from blockchainpype.evm.transaction import EthereumTransaction
from blockchainpype.evm.wallet.wallet import EthereumWallet
from tests.evm.test_wallet import (
    FakeRPCProvider,
    build_wallet,
    drain_background_tasks,
)

# === Canonical mainnet addresses (also used as fake-chain identities) ===

V2_FACTORY = "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f"
V2_ROUTER = "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D"
V3_FACTORY = "0x1F98431c8aD98523631AE4a59f267346ea31F984"
V3_ROUTER = "0xE592427A0AEce92De3Edee1F18E0157C05861564"
V3_QUOTER = DEFAULT_V3_QUOTER_ADDRESS

USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"  # 6 decimals
WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"  # 18 decimals
DAI = "0x6B175474E89094C44Da98b954EedeAC495271d0F"  # 18 decimals

PAIR_USDC_WETH = "0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc"
PAIR_USDC_DAI = "0xAE461cA67B15dc8dc81CE7615e0320dA1A9aB8D5"
PAIR_WETH_DAI = "0xA478c2975Ab1Ea89e8196811F51A7B7Ade33eB11"
POOL_USDC_WETH_500 = "0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640"
POOL_USDC_WETH_3000 = "0x8ad599c3A0ff1De082011EFDDc58f1908eb6e6D8"
POOL_WETH_DAI_500 = "0x60594a405d53811d3BC4766596EFD80fd545A270"

RECIPIENT = "0x5B38Da6a701c568545dCfcB03FcB875f56beddC4"
TX_HASH_HEX = "0x" + "ab" * 32
FIXED_TIMESTAMP = 1_700_000_000

# === Reserves and quotes served by the fake chain ===

R_USDC = 2_000_000 * 10**6  # USDC/WETH pair (2000 USDC per WETH)
R_WETH = 1_000 * 10**18
DIRECT_R_DAI = 10_000 * 10**18  # shallow direct USDC/DAI pair
DIRECT_R_USDC = 10_000 * 10**6
POOL_R_DAI = 2_000_000 * 10**18  # deep WETH/DAI pair
POOL_R_WETH = 1_000 * 10**18

Q_IN_USDC_WETH_500 = 490_000_000_000_000_000  # 0.49 WETH out
Q_IN_USDC_WETH_3000 = 500_000_000_000_000_000  # 0.50 WETH out (best)
Q_OUT_USDC_WETH_500 = 1_010_000_000  # 1010 USDC in
Q_OUT_USDC_WETH_3000 = 1_005_000_000  # 1005 USDC in (best)
Q_IN_WETH_DAI_500 = 995 * 10**18  # 995 DAI out
Q_OUT_WETH_DAI_500 = 500_000_000_000_000_000  # 0.5 WETH in

# Exact Uniswap V2 vectors precomputed with the canonical integer formulas
OUT_1000_USDC_RAW = 498_251_621_566_649_025  # getAmountOut(1000e6, R_USDC, R_WETH)
IN_FOR_1_WETH_RAW = 2_008_026_081  # getAmountIn(1e18, R_USDC, R_WETH)
DIRECT_USDC_DAI_OUT_RAW = 906_610_893_880_149_131_581
HOP2_WETH_DAI_OUT_RAW = 993_020_443_679_724_836_447

# === Selectors ===


def _selector(signature: str) -> str:
    return "0x" + Web3.keccak(text=signature)[:4].hex()


SEL_GET_PAIR = _selector("getPair(address,address)")
SEL_GET_RESERVES = _selector("getReserves()")
SEL_TOKEN0 = _selector("token0()")
SEL_GET_POOL = _selector("getPool(address,address,uint24)")
SEL_QUOTE_EXACT_INPUT_SINGLE = _selector(
    "quoteExactInputSingle(address,address,uint24,uint256,uint160)"
)
SEL_QUOTE_EXACT_OUTPUT_SINGLE = _selector(
    "quoteExactOutputSingle(address,address,uint24,uint256,uint160)"
)
SEL_SLOT0 = _selector("slot0()")
SEL_LIQUIDITY = _selector("liquidity()")

REVERTED = {"error": {"code": 3, "message": "execution reverted"}}


# === Reference implementations of the Uniswap V2 formulas ===


def v2_amount_out(amount_in: int, reserve_in: int, reserve_out: int) -> int:
    amount_in_with_fee = amount_in * 997
    return (amount_in_with_fee * reserve_out) // (
        reserve_in * 1000 + amount_in_with_fee
    )


def v2_amount_in(amount_out: int, reserve_in: int, reserve_out: int) -> int:
    return (reserve_in * amount_out * 1000) // ((reserve_out - amount_out) * 997) + 1


# === Fake chain ===


class UniswapFakeChain:
    """Mutable on-chain state answered through the fake JSON-RPC provider."""

    def __init__(self) -> None:
        self.v2_pairs: dict[frozenset[str], str] = {}
        # pair address (lower) -> (token0 lower, reserve0, reserve1)
        self.v2_pair_state: dict[str, tuple[str, int, int]] = {}
        self.v3_pools: dict[tuple[frozenset[str], int], str] = {}
        # pool address (lower) -> (token0 lower, sqrtPriceX96, liquidity)
        self.v3_pool_state: dict[str, tuple[str, int, int]] = {}
        # (tokenIn lower, tokenOut lower, fee) -> raw quote; None = revert
        self.exact_input_quotes: dict[tuple[str, str, int], int | None] = {}
        self.exact_output_quotes: dict[tuple[str, str, int], int | None] = {}
        # (mode, tokenIn lower, tokenOut lower, fee, raw amount)
        self.quoter_calls: list[tuple[str, str, str, int, int]] = []

    def add_v2_pair(
        self,
        pair_address: str,
        token0: str,
        reserve0: int,
        token1: str,
        reserve1: int,
    ) -> None:
        self.v2_pairs[frozenset({token0.lower(), token1.lower()})] = pair_address
        self.v2_pair_state[pair_address.lower()] = (token0.lower(), reserve0, reserve1)

    def add_v3_pool(
        self,
        pool_address: str,
        token_a: str,
        token_b: str,
        fee: int,
        token0: str | None = None,
        sqrt_price_x96: int = 0,
        liquidity: int = 0,
    ) -> None:
        key = (frozenset({token_a.lower(), token_b.lower()}), fee)
        self.v3_pools[key] = pool_address
        self.v3_pool_state[pool_address.lower()] = (
            (token0 or token_a).lower(),
            sqrt_price_x96,
            liquidity,
        )


def _answer_get_pair(chain: UniswapFakeChain, to: str, payload: bytes) -> Any:
    token_a, token_b = decode(["address", "address"], payload)
    pair = chain.v2_pairs.get(frozenset({token_a.lower(), token_b.lower()}))
    return "0x" + encode(["address"], [pair or ZERO_ADDRESS]).hex()


def _answer_get_reserves(chain: UniswapFakeChain, to: str, payload: bytes) -> Any:
    _, reserve0, reserve1 = chain.v2_pair_state[to]
    return (
        "0x" + encode(["uint112", "uint112", "uint32"], [reserve0, reserve1, 0]).hex()
    )


def _answer_token0(chain: UniswapFakeChain, to: str, payload: bytes) -> Any:
    state = chain.v2_pair_state.get(to) or chain.v3_pool_state[to]
    return "0x" + encode(["address"], [state[0]]).hex()


def _answer_get_pool(chain: UniswapFakeChain, to: str, payload: bytes) -> Any:
    token_a, token_b, fee = decode(["address", "address", "uint24"], payload)
    key = (frozenset({token_a.lower(), token_b.lower()}), int(fee))
    pool = chain.v3_pools.get(key)
    return "0x" + encode(["address"], [pool or ZERO_ADDRESS]).hex()


def _answer_quote(chain: UniswapFakeChain, mode: str, payload: bytes) -> Any:
    token_in, token_out, fee, amount, _ = decode(
        ["address", "address", "uint24", "uint256", "uint160"], payload
    )
    chain.quoter_calls.append(
        (mode, token_in.lower(), token_out.lower(), int(fee), amount)
    )
    quotes = (
        chain.exact_input_quotes if mode == "exact_input" else chain.exact_output_quotes
    )
    quote = quotes.get((token_in.lower(), token_out.lower(), int(fee)))
    if quote is None:
        return REVERTED
    return "0x" + encode(["uint256"], [quote]).hex()


def _answer_slot0(chain: UniswapFakeChain, to: str, payload: bytes) -> Any:
    _, sqrt_price_x96, _ = chain.v3_pool_state[to]
    return (
        "0x"
        + encode(
            ["uint160", "int24", "uint16", "uint16", "uint16", "uint8", "bool"],
            [sqrt_price_x96, 0, 0, 0, 0, 0, True],
        ).hex()
    )


def _answer_liquidity(chain: UniswapFakeChain, to: str, payload: bytes) -> Any:
    _, _, liquidity = chain.v3_pool_state[to]
    return "0x" + encode(["uint128"], [liquidity]).hex()


def make_eth_call_handler(chain: UniswapFakeChain) -> Any:
    """Answer factory/pair/pool/quoter eth_calls with ABI-encoded payloads."""
    dispatch = {
        SEL_GET_PAIR: _answer_get_pair,
        SEL_GET_RESERVES: _answer_get_reserves,
        SEL_TOKEN0: _answer_token0,
        SEL_GET_POOL: _answer_get_pool,
        SEL_QUOTE_EXACT_INPUT_SINGLE: (
            lambda c, to, payload: _answer_quote(c, "exact_input", payload)
        ),
        SEL_QUOTE_EXACT_OUTPUT_SINGLE: (
            lambda c, to, payload: _answer_quote(c, "exact_output", payload)
        ),
        SEL_SLOT0: _answer_slot0,
        SEL_LIQUIDITY: _answer_liquidity,
    }

    def handler(params: Any) -> Any:
        call = params[0]
        to = str(call["to"]).lower()
        data = str(call.get("data") or call.get("input"))
        selector = data[:10].lower()
        answer = dispatch.get(selector)
        if answer is None:
            raise AssertionError(f"Unexpected eth_call selector {selector} to {to}")
        return answer(chain, to, bytes.fromhex(data[10:]))

    return handler


def make_block_payload() -> dict[str, Any]:
    """Realistic post-London block payload with a 10 gwei base fee."""
    return {
        "number": "0x10",
        "hash": "0x" + "33" * 32,
        "parentHash": "0x" + "44" * 32,
        "sha3Uncles": "0x" + "55" * 32,
        "miner": ZERO_ADDRESS,
        "stateRoot": "0x" + "66" * 32,
        "transactionsRoot": "0x" + "77" * 32,
        "receiptsRoot": "0x" + "88" * 32,
        "logsBloom": "0x" + "00" * 256,
        "difficulty": "0x0",
        "totalDifficulty": "0x0",
        "extraData": "0x",
        "size": "0x220",
        "gasLimit": "0x1c9c380",
        "gasUsed": "0x5208",
        "timestamp": "0x60000000",
        "baseFeePerGas": "0x2540be400",  # 10 gwei
        "transactions": [],
        "uncles": [],
        "nonce": "0x0000000000000000",
        "mixHash": "0x" + "99" * 32,
    }


# === Assets ===


class StaticEthereumAsset(EthereumAsset):
    """Concrete EthereumAsset with statically provided metadata."""

    async def initialize_data(self) -> None:
        return


def make_asset(
    platform: BlockchainPlatform, symbol: str, decimals: int, address: str
) -> StaticEthereumAsset:
    return StaticEthereumAsset(
        platform=platform,
        identifier=EthereumAddress.from_string(address),
        data=EthereumAssetData(
            name=f"{symbol} Token", symbol=symbol, decimals=decimals
        ),
    )


def frozen_timestamp() -> Any:
    """Freeze the blockchain clock so deadline math is exactly assertable."""
    return patch.object(
        EthereumBlockchain,
        "current_timestamp",
        new_callable=PropertyMock,
        return_value=float(FIXED_TIMESTAMP),
    )


async def decode_router_call(
    tx_data: dict[str, Any], router_address: str, abi_file: str
) -> tuple[str, dict[str, Any]]:
    """Decode built calldata against the real router ABI."""
    abi = await EthereumLocalFileABI(file_name=abi_file).get_abi()
    contract = Web3().eth.contract(
        address=Web3.to_checksum_address(router_address), abi=abi
    )
    function, args = contract.decode_function_input(tx_data["data"])
    return function.fn_name, args


# === Fixtures ===


@pytest.fixture
def fake_chain() -> UniswapFakeChain:
    """Fake chain with V2 pairs, V3 pools and quoter answers pre-seeded."""
    chain = UniswapFakeChain()
    # V2: token0 is the numerically lower address, as on the real factory
    chain.add_v2_pair(PAIR_USDC_WETH, USDC, R_USDC, WETH, R_WETH)
    chain.add_v2_pair(PAIR_USDC_DAI, DAI, DIRECT_R_DAI, USDC, DIRECT_R_USDC)
    chain.add_v2_pair(PAIR_WETH_DAI, DAI, POOL_R_DAI, WETH, POOL_R_WETH)
    # V3 pools: USDC/WETH on the 500 and 3000 tiers, WETH/DAI on 500
    chain.add_v3_pool(POOL_USDC_WETH_500, USDC, WETH, 500)
    chain.add_v3_pool(
        POOL_USDC_WETH_3000,
        USDC,
        WETH,
        3000,
        token0=USDC,
        sqrt_price_x96=2 * 2**96,
        liquidity=10**12,
    )
    chain.add_v3_pool(POOL_WETH_DAI_500, WETH, DAI, 500)
    # Quoter answers
    chain.exact_input_quotes[(USDC.lower(), WETH.lower(), 500)] = Q_IN_USDC_WETH_500
    chain.exact_input_quotes[(USDC.lower(), WETH.lower(), 3000)] = Q_IN_USDC_WETH_3000
    chain.exact_output_quotes[(USDC.lower(), WETH.lower(), 500)] = Q_OUT_USDC_WETH_500
    chain.exact_output_quotes[(USDC.lower(), WETH.lower(), 3000)] = Q_OUT_USDC_WETH_3000
    chain.exact_input_quotes[(WETH.lower(), DAI.lower(), 500)] = Q_IN_WETH_DAI_500
    chain.exact_output_quotes[(WETH.lower(), DAI.lower(), 500)] = Q_OUT_WETH_DAI_500
    return chain


@pytest.fixture
def rpc_provider(fake_chain: UniswapFakeChain) -> FakeRPCProvider:
    return FakeRPCProvider(
        {
            "eth_call": make_eth_call_handler(fake_chain),
            "eth_chainId": "0x1",
            "eth_getBalance": "0xde0b6b3a7640000",
            "eth_getTransactionCount": "0x2",
            "eth_sendRawTransaction": TX_HASH_HEX,
            "eth_estimateGas": "0x30d40",  # 200,000
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
def ethereum_platform() -> BlockchainPlatform:
    return BlockchainPlatform(
        identifier="ethereum", type=EthereumBlockchainType, chain_id=1
    )


@pytest.fixture
def ethereum_config(
    rpc_provider: FakeRPCProvider, ethereum_platform: BlockchainPlatform
) -> EthereumBlockchainConfiguration:
    return EthereumBlockchainConfiguration(
        platform=ethereum_platform,
        native_asset=EthereumNativeAssetConfiguration(),
        connectivity=EthereumConnectivityConfiguration(rpc_provider=rpc_provider),
        explorer=None,
    )


@pytest.fixture
def ethereum_blockchain(
    ethereum_config: EthereumBlockchainConfiguration,
) -> EthereumBlockchain:
    return EthereumBlockchain(configuration=ethereum_config)


@pytest.fixture
def test_account() -> LocalAccount:
    return Account.create()


@pytest.fixture
async def ethereum_wallet(
    ethereum_config: EthereumBlockchainConfiguration,
    test_account: LocalAccount,
    ethereum_blockchain: EthereumBlockchain,
) -> EthereumWallet:
    wallet = build_wallet(ethereum_config, test_account, ethereum_blockchain)
    if wallet._background_tasks:
        await asyncio.gather(*wallet._background_tasks, return_exceptions=True)
    return wallet


@pytest.fixture
def usdc(ethereum_platform: BlockchainPlatform) -> StaticEthereumAsset:
    return make_asset(ethereum_platform, "USDC", 6, USDC)


@pytest.fixture
def weth(ethereum_platform: BlockchainPlatform) -> StaticEthereumAsset:
    return make_asset(ethereum_platform, "WETH", 18, WETH)


@pytest.fixture
def dai(ethereum_platform: BlockchainPlatform) -> StaticEthereumAsset:
    return make_asset(ethereum_platform, "DAI", 18, DAI)


@pytest.fixture
def v2(ethereum_blockchain: EthereumBlockchain) -> UniswapV2:
    return UniswapV2(
        blockchain=ethereum_blockchain,
        factory_address=V2_FACTORY,
        router_address=V2_ROUTER,
    )


@pytest.fixture
def v3(ethereum_blockchain: EthereumBlockchain) -> UniswapV3:
    return UniswapV3(
        blockchain=ethereum_blockchain,
        factory_address=V3_FACTORY,
        router_address=V3_ROUTER,
        quoter_address=V3_QUOTER,
    )


# === Uniswap V2 constant-product math ===


class TestV2Math:
    def test_get_amount_out_canonical_vector(self) -> None:
        """The canonical UniswapV2Library vector: 1000 in, 5000/10000 reserves."""
        assert UniswapV2.get_amount_out(1000, 5000, 10000) == 1662

    def test_get_amount_in_canonical_round_trip(self) -> None:
        assert UniswapV2.get_amount_in(1662, 5000, 10000) == 1000

    def test_get_amount_out_realistic_reserves(self) -> None:
        assert (
            UniswapV2.get_amount_out(1000 * 10**6, R_USDC, R_WETH) == OUT_1000_USDC_RAW
        )

    def test_get_amount_in_realistic_reserves(self) -> None:
        assert UniswapV2.get_amount_in(10**18, R_USDC, R_WETH) == IN_FOR_1_WETH_RAW

    def test_get_amount_out_rejects_non_positive_amount(self) -> None:
        with pytest.raises(ValueError, match="Input amount must be positive"):
            UniswapV2.get_amount_out(0, 5000, 10000)

    def test_get_amount_out_rejects_empty_reserves(self) -> None:
        with pytest.raises(ValueError, match="reserves must be positive"):
            UniswapV2.get_amount_out(1000, 0, 10000)

    def test_get_amount_in_rejects_output_exceeding_reserves(self) -> None:
        with pytest.raises(ValueError, match="exceeds pool reserves"):
            UniswapV2.get_amount_in(10000, 5000, 10000)

    def test_get_amount_in_rejects_non_positive_amount(self) -> None:
        with pytest.raises(ValueError, match="Output amount must be positive"):
            UniswapV2.get_amount_in(-5, 5000, 10000)


# === Uniswap V2 quoting over the fake chain ===


class TestV2QuoteSwap:
    async def test_exact_input_scales_mixed_decimals(
        self, v2: UniswapV2, usdc: StaticEthereumAsset, weth: StaticEthereumAsset
    ) -> None:
        """1000 USDC (6 dec) -> WETH (18 dec) quotes with raw-integer math."""
        route = await v2.quote_swap(usdc, weth, Decimal("1000"))

        assert route.input_amount == Decimal("1000")
        assert route.output_amount == Decimal(OUT_1000_USDC_RAW) / Decimal(10**18)
        assert route.protocol == "uniswap_v2"
        assert route.taxes == Decimal("0.003")
        assert route.mode == SwapMode.EXACT_INPUT
        assert len(route.sequence) == 1
        assert route.sequence[0].input_amount == Decimal("1000")
        assert route.sequence[0].output_amount == route.output_amount

    async def test_exact_output_returns_requested_output(
        self, v2: UniswapV2, usdc: StaticEthereumAsset, weth: StaticEthereumAsset
    ) -> None:
        """Regression: EXACT_OUTPUT must keep the requested output amount."""
        route = await v2.quote_swap(
            usdc, weth, Decimal("1"), mode=SwapMode.EXACT_OUTPUT
        )

        assert route.output_amount == Decimal("1")
        assert route.input_amount == Decimal(IN_FOR_1_WETH_RAW) / Decimal(10**6)
        assert route.input_amount != route.output_amount
        assert route.mode == SwapMode.EXACT_OUTPUT

    async def test_default_slippage_embedded_when_not_forwarded(
        self, v2: UniswapV2, usdc: StaticEthereumAsset, weth: StaticEthereumAsset
    ) -> None:
        route = await v2.quote_swap(usdc, weth, Decimal("1000"))
        assert route.max_slippage == Decimal("0.005")

    async def test_forwarded_slippage_embedded_in_route(
        self, v2: UniswapV2, usdc: StaticEthereumAsset, weth: StaticEthereumAsset
    ) -> None:
        route = await v2.quote_swap(
            usdc, weth, Decimal("1000"), max_slippage=Decimal("0.02")
        )
        assert route.max_slippage == Decimal("0.02")

    async def test_missing_pair_raises(
        self,
        fake_chain: UniswapFakeChain,
        v2: UniswapV2,
        usdc: StaticEthereumAsset,
        weth: StaticEthereumAsset,
    ) -> None:
        fake_chain.v2_pairs.clear()
        with pytest.raises(ValueError, match="No Uniswap V2 pair found"):
            await v2.quote_swap(usdc, weth, Decimal("1000"))

    async def test_non_positive_amount_raises(
        self, v2: UniswapV2, usdc: StaticEthereumAsset, weth: StaticEthereumAsset
    ) -> None:
        with pytest.raises(ValueError, match="Swap amount must be positive"):
            await v2.quote_swap(usdc, weth, Decimal("0"))

    async def test_exact_output_insufficient_liquidity_raises(
        self, v2: UniswapV2, usdc: StaticEthereumAsset, weth: StaticEthereumAsset
    ) -> None:
        with pytest.raises(ValueError, match="Insufficient liquidity"):
            await v2.quote_swap(usdc, weth, Decimal("1000"), mode=SwapMode.EXACT_OUTPUT)

    async def test_get_reserves_returns_decimal_units(
        self, v2: UniswapV2, usdc: StaticEthereumAsset, weth: StaticEthereumAsset
    ) -> None:
        reserves = await v2.get_reserves(usdc, weth)
        assert reserves == (Decimal("2000000"), Decimal("1000"))

    async def test_get_reserves_orientation_when_asset_is_token1(
        self, v2: UniswapV2, usdc: StaticEthereumAsset, weth: StaticEthereumAsset
    ) -> None:
        """Asking WETH-first must flip the token0-oriented raw reserves."""
        reserves = await v2.get_reserves(weth, usdc)
        assert reserves == (Decimal("1000"), Decimal("2000000"))

    async def test_get_raw_reserves_returns_raw_integers(
        self, v2: UniswapV2, usdc: StaticEthereumAsset, weth: StaticEthereumAsset
    ) -> None:
        assert await v2.get_raw_reserves(usdc, weth) == (R_USDC, R_WETH)
        assert await v2.get_raw_reserves(weth, usdc) == (R_WETH, R_USDC)


# === Uniswap V3 quoting over the fake chain ===


class TestV3QuoteSwap:
    async def test_exact_input_selects_best_tier(
        self, v3: UniswapV3, usdc: StaticEthereumAsset, weth: StaticEthereumAsset
    ) -> None:
        route = await v3.quote_swap(usdc, weth, Decimal("1000"))

        assert route.output_amount == Decimal(Q_IN_USDC_WETH_3000) / Decimal(10**18)
        assert route.protocol == "uniswap_v3"  # the registered strategy key
        assert route.taxes == Decimal("0.003")  # 3000 tier as a fraction
        assert route.input_amount == Decimal("1000")

    async def test_exact_output_selects_min_input_tier(
        self, v3: UniswapV3, usdc: StaticEthereumAsset, weth: StaticEthereumAsset
    ) -> None:
        route = await v3.quote_swap(
            usdc, weth, Decimal("0.5"), mode=SwapMode.EXACT_OUTPUT
        )

        assert route.output_amount == Decimal("0.5")
        assert route.input_amount == Decimal(Q_OUT_USDC_WETH_3000) / Decimal(10**6)
        assert route.taxes == Decimal("0.003")

    async def test_quoter_receives_raw_amount_and_fee_args(
        self,
        fake_chain: UniswapFakeChain,
        v3: UniswapV3,
        usdc: StaticEthereumAsset,
        weth: StaticEthereumAsset,
    ) -> None:
        await v3.quote_swap(usdc, weth, Decimal("1000"))
        assert (
            "exact_input",
            USDC.lower(),
            WETH.lower(),
            500,
            1000 * 10**6,
        ) in fake_chain.quoter_calls
        assert (
            "exact_input",
            USDC.lower(),
            WETH.lower(),
            3000,
            1000 * 10**6,
        ) in fake_chain.quoter_calls

    async def test_honors_configured_fee_tiers(
        self,
        fake_chain: UniswapFakeChain,
        ethereum_blockchain: EthereumBlockchain,
        usdc: StaticEthereumAsset,
        weth: StaticEthereumAsset,
    ) -> None:
        strategy = UniswapV3(
            blockchain=ethereum_blockchain,
            factory_address=V3_FACTORY,
            router_address=V3_ROUTER,
            quoter_address=V3_QUOTER,
            fee_tiers=[500],
        )
        route = await strategy.quote_swap(usdc, weth, Decimal("1000"))

        assert route.output_amount == Decimal(Q_IN_USDC_WETH_500) / Decimal(10**18)
        assert route.taxes == Decimal("0.0005")
        queried_fees = {call[3] for call in fake_chain.quoter_calls}
        assert queried_fees == {500}

    async def test_reverting_tier_is_tolerated(
        self,
        fake_chain: UniswapFakeChain,
        v3: UniswapV3,
        usdc: StaticEthereumAsset,
        weth: StaticEthereumAsset,
    ) -> None:
        fake_chain.exact_input_quotes[(USDC.lower(), WETH.lower(), 3000)] = None
        route = await v3.quote_swap(usdc, weth, Decimal("1000"))
        assert route.output_amount == Decimal(Q_IN_USDC_WETH_500) / Decimal(10**18)
        assert route.taxes == Decimal("0.0005")

    async def test_no_pool_raises(
        self, v3: UniswapV3, usdc: StaticEthereumAsset, dai: StaticEthereumAsset
    ) -> None:
        with pytest.raises(ValueError, match="No valid Uniswap V3 pool"):
            await v3.quote_swap(usdc, dai, Decimal("1000"))

    async def test_invalid_fee_tier_rejected_at_construction(
        self, ethereum_blockchain: EthereumBlockchain
    ) -> None:
        with pytest.raises(ValueError, match="uint24"):
            UniswapV3(
                blockchain=ethereum_blockchain,
                factory_address=V3_FACTORY,
                router_address=V3_ROUTER,
                quoter_address=V3_QUOTER,
                fee_tiers=[2**24],
            )


# === Uniswap V3 reserves approximation ===


class TestV3Reserves:
    async def test_reserves_use_sqrt_price_formulas(
        self,
        fake_chain: UniswapFakeChain,
        v3: UniswapV3,
        usdc: StaticEthereumAsset,
        weth: StaticEthereumAsset,
    ) -> None:
        """amount0 = L / sqrtP, amount1 = L * sqrtP with sqrtP = 2, L = 1e12."""
        # Only the 3000-tier pool (with a real price and liquidity) remains
        del fake_chain.v3_pools[(frozenset({USDC.lower(), WETH.lower()}), 500)]
        reserve_usdc, reserve_weth = await v3.get_reserves(usdc, weth)

        # token0 = USDC: amount0 = 1e12 / 2 = 5e11 raw -> / 1e6 decimals
        assert reserve_usdc == Decimal(10**12) / 2 / Decimal(10**6)
        assert reserve_usdc == Decimal("500000")
        # token1 = WETH: amount1 = 1e12 * 2 = 2e12 raw -> / 1e18 decimals
        assert reserve_weth == Decimal(10**12) * 2 / Decimal(10**18)
        assert reserve_weth == Decimal("0.000002")

    async def test_reserves_orientation_when_asset_is_token1(
        self,
        fake_chain: UniswapFakeChain,
        v3: UniswapV3,
        usdc: StaticEthereumAsset,
        weth: StaticEthereumAsset,
    ) -> None:
        del fake_chain.v3_pools[(frozenset({USDC.lower(), WETH.lower()}), 500)]
        reserve_weth, reserve_usdc = await v3.get_reserves(weth, usdc)
        assert reserve_weth == Decimal("0.000002")
        assert reserve_usdc == Decimal("500000")

    async def test_uninitialized_pool_price_raises(
        self,
        fake_chain: UniswapFakeChain,
        v3: UniswapV3,
        usdc: StaticEthereumAsset,
        weth: StaticEthereumAsset,
    ) -> None:
        # Only the 500-tier pool (sqrtPriceX96 = 0) remains
        del fake_chain.v3_pools[(frozenset({USDC.lower(), WETH.lower()}), 3000)]
        with pytest.raises(ValueError, match="sqrtPriceX96 is zero"):
            await v3.get_reserves(usdc, weth)

    async def test_no_pool_raises(
        self, v3: UniswapV3, usdc: StaticEthereumAsset, dai: StaticEthereumAsset
    ) -> None:
        with pytest.raises(ValueError, match="No Uniswap V3 pool found"):
            await v3.get_reserves(usdc, dai)


# === V3 path encoding ===


class TestV3PathEncoding:
    def test_encode_path_exact_bytes(self) -> None:
        expected = bytes.fromhex(
            USDC[2:].lower() + "0001f4" + WETH[2:].lower() + "000bb8" + DAI[2:].lower()
        )
        assert UniswapV3.encode_path([USDC, WETH, DAI], [500, 3000]) == expected

    def test_encode_path_single_hop(self) -> None:
        expected = bytes.fromhex(USDC[2:].lower() + "000bb8" + WETH[2:].lower())
        assert UniswapV3.encode_path([USDC, WETH], [3000]) == expected

    def test_encode_path_rejects_mismatched_lengths(self) -> None:
        with pytest.raises(ValueError, match="one fee tier per hop"):
            UniswapV3.encode_path([USDC, WETH, DAI], [500])

    def test_encode_path_rejects_short_path(self) -> None:
        with pytest.raises(ValueError, match="at least two token addresses"):
            UniswapV3.encode_path([USDC], [])

    def test_encode_path_rejects_invalid_fee(self) -> None:
        with pytest.raises(ValueError, match="uint24"):
            UniswapV3.encode_path([USDC, WETH], [2**24])


# === ProtocolImplementation conformance and unsigned builds ===


class TestContractConformance:
    def test_strategies_satisfy_runtime_protocol(
        self, v2: UniswapV2, v3: UniswapV3
    ) -> None:
        assert isinstance(v2, ProtocolImplementation)
        assert isinstance(v3, ProtocolImplementation)

    async def test_build_without_wallet_raises(
        self,
        v2: UniswapV2,
        v3: UniswapV3,
        usdc: StaticEthereumAsset,
        weth: StaticEthereumAsset,
    ) -> None:
        v2_route = await v2.quote_swap(usdc, weth, Decimal("1000"))
        with pytest.raises(ValueError, match="No wallet is bound"):
            await v2.build_swap_transaction(v2_route)

        v3_route = await v3.quote_swap(usdc, weth, Decimal("1000"))
        with pytest.raises(ValueError, match="No wallet is bound"):
            await v3.build_swap_transaction(v3_route)

    async def test_set_wallet_none_unbinds(
        self,
        v2: UniswapV2,
        ethereum_wallet: EthereumWallet,
        usdc: StaticEthereumAsset,
        weth: StaticEthereumAsset,
    ) -> None:
        v2.set_wallet(ethereum_wallet)
        assert v2.wallet is ethereum_wallet
        v2.set_wallet(None)
        route = await v2.quote_swap(usdc, weth, Decimal("1000"))
        with pytest.raises(ValueError, match="No wallet is bound"):
            await v2.build_swap_transaction(route)

    def test_set_wallet_rejects_foreign_wallet_type(
        self, v2: UniswapV2, v3: UniswapV3
    ) -> None:
        with pytest.raises(TypeError, match="EthereumWallet"):
            v2.set_wallet(object())  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="EthereumWallet"):
            v3.set_wallet(object())  # type: ignore[arg-type]

    async def test_v2_build_returns_unsigned_transaction_with_exact_calldata(
        self,
        rpc_provider: FakeRPCProvider,
        v2: UniswapV2,
        ethereum_wallet: EthereumWallet,
        usdc: StaticEthereumAsset,
        weth: StaticEthereumAsset,
    ) -> None:
        v2.set_wallet(ethereum_wallet)
        route = await v2.quote_swap(
            usdc, weth, Decimal("1000"), max_slippage=Decimal("0.01")
        )
        with frozen_timestamp():
            transaction = await v2.build_swap_transaction(
                route, recipient=RECIPIENT, deadline_minutes=5
            )

        # Unsigned tracking object, nothing broadcast
        assert isinstance(transaction, EthereumTransaction)
        assert transaction.current_state == BlockchainTransactionState.PENDING_BROADCAST
        assert transaction.signed_transaction is None
        assert transaction.operator_operation_id is None
        assert transaction.owner_identifier == ethereum_wallet.identifier
        assert rpc_provider.calls_for("eth_sendRawTransaction") == []

        tx_data = transaction.other_data["tx_data"]
        assert tx_data["to"] == V2_ROUTER
        assert tx_data["from"] == ethereum_wallet.address.raw
        assert tx_data["chainId"] == 1

        name, args = await decode_router_call(
            tx_data, V2_ROUTER, "UniswapV2Router02.json"
        )
        assert name == "swapExactTokensForTokens"
        assert args["amountIn"] == 1000 * 10**6
        # min out = out_raw * (1 - 0.01), computed on the decimal route amount
        assert args["amountOutMin"] == int(
            Decimal(OUT_1000_USDC_RAW)
            / Decimal(10**18)
            * Decimal("0.99")
            * Decimal(10**18)
        )
        assert args["amountOutMin"] == 493_269_105_350_982_534
        assert args["path"] == [USDC, WETH]
        assert args["to"] == RECIPIENT
        assert args["deadline"] == FIXED_TIMESTAMP + 5 * 60

    async def test_v2_build_exact_output_calldata(
        self,
        v2: UniswapV2,
        ethereum_wallet: EthereumWallet,
        usdc: StaticEthereumAsset,
        weth: StaticEthereumAsset,
    ) -> None:
        v2.set_wallet(ethereum_wallet)
        route = await v2.quote_swap(
            usdc,
            weth,
            Decimal("1"),
            mode=SwapMode.EXACT_OUTPUT,
            max_slippage=Decimal("0.01"),
        )
        with frozen_timestamp():
            transaction = await v2.build_swap_transaction(route)

        name, args = await decode_router_call(
            transaction.other_data["tx_data"], V2_ROUTER, "UniswapV2Router02.json"
        )
        assert name == "swapTokensForExactTokens"
        assert args["amountOut"] == 10**18
        # max in = in_raw * (1 + 0.01), computed on the decimal route amount
        assert args["amountInMax"] == int(
            Decimal(IN_FOR_1_WETH_RAW)
            / Decimal(10**6)
            * Decimal("1.01")
            * Decimal(10**6)
        )
        assert args["path"] == [USDC, WETH]
        # Default recipient is the bound wallet, default deadline is 20 minutes
        assert args["to"] == ethereum_wallet.address.raw
        assert args["deadline"] == FIXED_TIMESTAMP + 20 * 60

    async def test_v3_build_single_hop_recovers_fee_from_taxes(
        self,
        v3: UniswapV3,
        ethereum_wallet: EthereumWallet,
        usdc: StaticEthereumAsset,
        weth: StaticEthereumAsset,
    ) -> None:
        v3.set_wallet(ethereum_wallet)
        route = await v3.quote_swap(
            usdc, weth, Decimal("1000"), max_slippage=Decimal("0.01")
        )
        with frozen_timestamp():
            transaction = await v3.build_swap_transaction(
                route, recipient=RECIPIENT, deadline_minutes=10
            )

        assert transaction.current_state == BlockchainTransactionState.PENDING_BROADCAST
        assert transaction.signed_transaction is None

        tx_data = transaction.other_data["tx_data"]
        assert tx_data["to"] == V3_ROUTER
        name, args = await decode_router_call(
            tx_data, V3_ROUTER, "uniswap_v3/ISwapRouter.json"
        )
        assert name == "exactInputSingle"
        params = args["params"]
        assert params["tokenIn"] == USDC
        assert params["tokenOut"] == WETH
        assert params["fee"] == 3000  # recovered from route.taxes * 1e6
        assert params["recipient"] == RECIPIENT
        assert params["deadline"] == FIXED_TIMESTAMP + 10 * 60
        assert params["amountIn"] == 1000 * 10**6
        assert params["amountOutMinimum"] == int(
            Decimal(Q_IN_USDC_WETH_3000)
            / Decimal(10**18)
            * Decimal("0.99")
            * Decimal(10**18)
        )
        assert params["amountOutMinimum"] == 495_000_000_000_000_000
        assert params["sqrtPriceLimitX96"] == 0

    async def test_v3_build_exact_output_single_calldata(
        self,
        v3: UniswapV3,
        ethereum_wallet: EthereumWallet,
        usdc: StaticEthereumAsset,
        weth: StaticEthereumAsset,
    ) -> None:
        v3.set_wallet(ethereum_wallet)
        route = await v3.quote_swap(
            usdc,
            weth,
            Decimal("0.5"),
            mode=SwapMode.EXACT_OUTPUT,
            max_slippage=Decimal("0.02"),
        )
        with frozen_timestamp():
            transaction = await v3.build_swap_transaction(route)

        name, args = await decode_router_call(
            transaction.other_data["tx_data"], V3_ROUTER, "uniswap_v3/ISwapRouter.json"
        )
        assert name == "exactOutputSingle"
        params = args["params"]
        assert params["fee"] == 3000
        assert params["amountOut"] == Q_OUT_WETH_DAI_500  # 0.5 WETH raw
        # 1005 USDC * 1.02 = 1025.1 USDC
        assert params["amountInMaximum"] == 1_025_100_000
        assert params["recipient"] == ethereum_wallet.address.raw
        assert params["deadline"] == FIXED_TIMESTAMP + 20 * 60


# === Multi-hop ===


class TestMultiHop:
    @pytest.fixture
    def v2_dex(
        self,
        ethereum_blockchain: EthereumBlockchain,
        ethereum_platform: BlockchainPlatform,
        weth: StaticEthereumAsset,
    ) -> UniswapDEX:
        configuration = UniswapConfiguration.local_network(
            platform=ethereum_platform,
            v2_factory_address=V2_FACTORY,
            v2_router_address=V2_ROUTER,
            intermediate_assets=[weth],
        )
        return UniswapDEX(blockchain=ethereum_blockchain, configuration=configuration)

    async def test_find_best_route_picks_through_route_on_crafted_reserves(
        self,
        v2_dex: UniswapDEX,
        usdc: StaticEthereumAsset,
        weth: StaticEthereumAsset,
        dai: StaticEthereumAsset,
    ) -> None:
        """The deep USDC->WETH->DAI route must beat the shallow direct pair."""
        route = await v2_dex.find_best_route(usdc, dai, Decimal("1000"))

        assert route.protocol == "uniswap_v2"
        assert len(route.sequence) == 2
        # Exact per-hop amounts from the constant-product formulas
        hop1, hop2 = route.sequence
        assert hop1.input_asset == usdc
        assert hop1.output_asset == weth
        assert hop1.output_amount == Decimal(OUT_1000_USDC_RAW) / Decimal(10**18)
        assert hop2.input_asset == weth
        assert hop2.input_amount == hop1.output_amount  # hops chain exactly
        assert hop2.output_asset == dai
        assert route.output_amount == Decimal(HOP2_WETH_DAI_OUT_RAW) / Decimal(10**18)
        # Better than the direct shallow pool
        assert route.output_amount > Decimal(DIRECT_USDC_DAI_OUT_RAW) / Decimal(10**18)
        # Taxes accumulate per hop
        assert route.taxes == Decimal("0.006")

    async def test_find_best_route_max_hops_1_restricts_to_direct(
        self,
        v2_dex: UniswapDEX,
        usdc: StaticEthereumAsset,
        dai: StaticEthereumAsset,
    ) -> None:
        route = await v2_dex.find_best_route(usdc, dai, Decimal("1000"), max_hops=1)
        assert len(route.sequence) == 1
        assert route.output_amount == Decimal(DIRECT_USDC_DAI_OUT_RAW) / Decimal(10**18)

    async def test_find_best_route_exact_output_chains_backward(
        self,
        v2_dex: UniswapDEX,
        usdc: StaticEthereumAsset,
        dai: StaticEthereumAsset,
    ) -> None:
        route = await v2_dex.find_best_route(
            usdc, dai, Decimal("900"), mode=SwapMode.EXACT_OUTPUT
        )
        assert route.output_amount == Decimal("900")
        assert route.mode == SwapMode.EXACT_OUTPUT
        for previous_hop, next_hop in zip(
            route.sequence, route.sequence[1:], strict=False
        ):
            assert previous_hop.output_asset == next_hop.input_asset
            assert previous_hop.output_amount == next_hop.input_amount

    async def test_find_best_route_invalid_max_hops(
        self, v2_dex: UniswapDEX, usdc: StaticEthereumAsset, dai: StaticEthereumAsset
    ) -> None:
        with pytest.raises(ValueError, match="max_hops must be at least 1"):
            await v2_dex.find_best_route(usdc, dai, Decimal("1000"), max_hops=0)

    async def test_v2_multi_hop_build_uses_full_path(
        self,
        v2_dex: UniswapDEX,
        ethereum_wallet: EthereumWallet,
        usdc: StaticEthereumAsset,
        dai: StaticEthereumAsset,
    ) -> None:
        v2_dex.set_wallet(ethereum_wallet)
        route = await v2_dex.find_best_route(usdc, dai, Decimal("1000"))
        with frozen_timestamp():
            transaction = await v2_dex.execute_swap(route, recipient=RECIPIENT)

        name, args = await decode_router_call(
            transaction.other_data["tx_data"], V2_ROUTER, "UniswapV2Router02.json"
        )
        assert name == "swapExactTokensForTokens"
        assert args["path"] == [USDC, WETH, DAI]
        assert args["amountIn"] == 1000 * 10**6
        assert args["to"] == RECIPIENT

    async def test_v3_multi_hop_exact_input_path_bytes(
        self,
        v3: UniswapV3,
        ethereum_wallet: EthereumWallet,
        usdc: StaticEthereumAsset,
        weth: StaticEthereumAsset,
        dai: StaticEthereumAsset,
    ) -> None:
        v3.set_wallet(ethereum_wallet)
        hop1 = await v3.quote_swap(
            usdc, weth, Decimal("1000"), max_slippage=Decimal("0.01")
        )
        hop2 = await v3.quote_swap(
            weth, dai, hop1.output_amount, max_slippage=Decimal("0.01")
        )
        route = v3.compose_multi_hop_route(
            [hop1, hop2], SwapMode.EXACT_INPUT, Decimal("0.01")
        )
        assert route.protocol == "uniswap_v3"
        assert route.taxes == Decimal("0.003") + Decimal("0.0005")

        with frozen_timestamp():
            transaction = await v3.build_swap_transaction(route, recipient=RECIPIENT)

        name, args = await decode_router_call(
            transaction.other_data["tx_data"], V3_ROUTER, "uniswap_v3/ISwapRouter.json"
        )
        assert name == "exactInput"
        params = args["params"]
        # token/fee/token packed path with the per-hop tiers (3000 then 500)
        assert params["path"] == bytes.fromhex(
            USDC[2:].lower() + "000bb8" + WETH[2:].lower() + "0001f4" + DAI[2:].lower()
        )
        assert params["amountIn"] == 1000 * 10**6
        assert params["amountOutMinimum"] == int(
            Decimal(Q_IN_WETH_DAI_500)
            / Decimal(10**18)
            * Decimal("0.99")
            * Decimal(10**18)
        )
        assert params["recipient"] == RECIPIENT
        assert params["deadline"] == FIXED_TIMESTAMP + 20 * 60

    async def test_v3_multi_hop_exact_output_reverses_path(
        self,
        v3: UniswapV3,
        ethereum_wallet: EthereumWallet,
        usdc: StaticEthereumAsset,
        weth: StaticEthereumAsset,
        dai: StaticEthereumAsset,
    ) -> None:
        v3.set_wallet(ethereum_wallet)
        hop2 = await v3.quote_swap(
            weth, dai, Decimal("995"), mode=SwapMode.EXACT_OUTPUT
        )
        hop1 = await v3.quote_swap(
            usdc, weth, hop2.input_amount, mode=SwapMode.EXACT_OUTPUT
        )
        route = v3.compose_multi_hop_route(
            [hop1, hop2], SwapMode.EXACT_OUTPUT, Decimal("0.01")
        )

        with frozen_timestamp():
            transaction = await v3.build_swap_transaction(route)

        name, args = await decode_router_call(
            transaction.other_data["tx_data"], V3_ROUTER, "uniswap_v3/ISwapRouter.json"
        )
        assert name == "exactOutput"
        params = args["params"]
        # exactOutput paths run output-first with reversed fee order (500, 3000)
        assert params["path"] == bytes.fromhex(
            DAI[2:].lower() + "0001f4" + WETH[2:].lower() + "000bb8" + USDC[2:].lower()
        )
        assert params["amountOut"] == 995 * 10**18
        # 1005 USDC * 1.01 = 1015.05 USDC
        assert params["amountInMaximum"] == 1_015_050_000

    async def test_v3_foreign_multi_hop_route_raises(
        self,
        v3: UniswapV3,
        ethereum_wallet: EthereumWallet,
        usdc: StaticEthereumAsset,
        weth: StaticEthereumAsset,
        dai: StaticEthereumAsset,
    ) -> None:
        """A hand-built multi-hop route has no recorded per-hop fee tiers."""
        v3.set_wallet(ethereum_wallet)
        hop1 = await v3.quote_swap(usdc, weth, Decimal("1000"))
        hop2 = await v3.quote_swap(weth, dai, hop1.output_amount)
        route = v3.compose_multi_hop_route(
            [hop1, hop2], SwapMode.EXACT_INPUT, Decimal("0.01")
        )
        foreign_route = SwapRoute(
            input_asset=route.input_asset,
            input_amount=route.input_amount * 2,
            output_asset=route.output_asset,
            output_amount=route.output_amount,
            are_amounts_raw=False,
            sequence=[
                route.sequence[0].model_copy(
                    update={"input_amount": route.input_amount * 2}
                ),
                route.sequence[1],
            ],
            mode=SwapMode.EXACT_INPUT,
            max_slippage=Decimal("0.01"),
            taxes=route.taxes,
            protocol="uniswap_v3",
        )
        with pytest.raises(ValueError, match="per-hop fee tiers"):
            await v3.build_swap_transaction(foreign_route)


# === UniswapDEX facade ===


def make_mainnet_dex(
    blockchain: EthereumBlockchain,
    platform: BlockchainPlatform,
    wallet: EthereumWallet | None = None,
    intermediate_assets: list[Any] | None = None,
) -> UniswapDEX:
    configuration = UniswapConfiguration.local_network(
        platform=platform,
        v2_factory_address=V2_FACTORY,
        v2_router_address=V2_ROUTER,
        v3_factory_address=V3_FACTORY,
        v3_router_address=V3_ROUTER,
        v3_quoter_address=V3_QUOTER,
        v3_fee_tiers=[Decimal("0.0005"), Decimal("0.003")],
        intermediate_assets=intermediate_assets or [],
    )
    return UniswapDEX(blockchain=blockchain, configuration=configuration, wallet=wallet)


class TestUniswapDEX:
    def test_auto_detects_ethereum_mainnet_configuration(
        self, ethereum_blockchain: EthereumBlockchain
    ) -> None:
        dex = UniswapDEX(blockchain=ethereum_blockchain)
        assert set(dex.supported_protocols) == {"uniswap_v2", "uniswap_v3"}
        assert dex.blockchain is ethereum_blockchain
        assert dex.configuration.platform == ethereum_blockchain.platform

    def test_platform_mismatch_raises(
        self, ethereum_blockchain: EthereumBlockchain
    ) -> None:
        configuration = UniswapConfiguration.polygon_mainnet()
        with pytest.raises(ValueError, match="does not match blockchain platform"):
            UniswapDEX(blockchain=ethereum_blockchain, configuration=configuration)

    def test_unknown_protocol_name_raises(
        self,
        ethereum_blockchain: EthereumBlockchain,
        ethereum_platform: BlockchainPlatform,
    ) -> None:
        from blockchainpype.dapps.router.dex import ProtocolConfiguration

        configuration = UniswapConfiguration(
            platform=ethereum_platform,
            protocols=[
                ProtocolConfiguration(
                    protocol_name="sushiswap",
                    factory_address=V2_FACTORY,
                    router_address=V2_ROUTER,
                    fee_tiers=[Decimal("0.003")],
                )
            ],
        )
        with pytest.raises(ValueError, match="Unsupported Uniswap protocol"):
            UniswapDEX(blockchain=ethereum_blockchain, configuration=configuration)

    def test_configured_fee_fractions_become_uint24_tiers(
        self,
        ethereum_blockchain: EthereumBlockchain,
        ethereum_platform: BlockchainPlatform,
    ) -> None:
        dex = make_mainnet_dex(ethereum_blockchain, ethereum_platform)
        v3_strategy = dex._protocol_strategies["uniswap_v3"]
        assert isinstance(v3_strategy, UniswapV3)
        assert v3_strategy.fee_tiers == [500, 3000]

    def test_local_network_requires_some_addresses(
        self, ethereum_platform: BlockchainPlatform
    ) -> None:
        with pytest.raises(ValueError, match="factory\\+router address pair"):
            UniswapConfiguration.local_network(platform=ethereum_platform)

    async def test_quote_swap_selects_best_protocol(
        self,
        ethereum_blockchain: EthereumBlockchain,
        ethereum_platform: BlockchainPlatform,
        usdc: StaticEthereumAsset,
        weth: StaticEthereumAsset,
    ) -> None:
        """V3's 0.5 WETH quote beats V2's 0.498... on the fake chain."""
        dex = make_mainnet_dex(ethereum_blockchain, ethereum_platform)
        route = await dex.quote_swap(usdc, weth, Decimal("1000"))
        assert route.protocol == "uniswap_v3"
        assert route.output_amount == Decimal("0.5")

    async def test_execute_swap_dispatches_v3_route_end_to_end(
        self,
        ethereum_blockchain: EthereumBlockchain,
        ethereum_platform: BlockchainPlatform,
        ethereum_wallet: EthereumWallet,
        usdc: StaticEthereumAsset,
        weth: StaticEthereumAsset,
    ) -> None:
        """Regression: V3 routes carry the registered 'uniswap_v3' key."""
        dex = make_mainnet_dex(
            ethereum_blockchain, ethereum_platform, wallet=ethereum_wallet
        )
        route = await dex.quote_swap(usdc, weth, Decimal("1000"), protocol="uniswap_v3")
        assert route.protocol == "uniswap_v3"

        with frozen_timestamp():
            transaction = await dex.execute_swap(route, recipient=RECIPIENT)

        assert isinstance(transaction, EthereumTransaction)
        name, args = await decode_router_call(
            transaction.other_data["tx_data"], V3_ROUTER, "uniswap_v3/ISwapRouter.json"
        )
        assert name == "exactInputSingle"
        assert args["params"]["fee"] == 3000
        # The facade forwards its configured default deadline (20 minutes)
        assert args["params"]["deadline"] == FIXED_TIMESTAMP + 20 * 60

    async def test_execute_swap_forwards_explicit_deadline(
        self,
        ethereum_blockchain: EthereumBlockchain,
        ethereum_platform: BlockchainPlatform,
        ethereum_wallet: EthereumWallet,
        usdc: StaticEthereumAsset,
        weth: StaticEthereumAsset,
    ) -> None:
        dex = make_mainnet_dex(
            ethereum_blockchain, ethereum_platform, wallet=ethereum_wallet
        )
        route = await dex.quote_swap(usdc, weth, Decimal("1000"), protocol="uniswap_v2")
        with frozen_timestamp():
            transaction = await dex.execute_swap(route, deadline_minutes=5)
        _, args = await decode_router_call(
            transaction.other_data["tx_data"], V2_ROUTER, "UniswapV2Router02.json"
        )
        assert args["deadline"] == FIXED_TIMESTAMP + 5 * 60

    async def test_execute_swap_unknown_protocol_raises(
        self,
        ethereum_blockchain: EthereumBlockchain,
        ethereum_platform: BlockchainPlatform,
        usdc: StaticEthereumAsset,
        weth: StaticEthereumAsset,
    ) -> None:
        dex = make_mainnet_dex(ethereum_blockchain, ethereum_platform)
        route = await dex.quote_swap(usdc, weth, Decimal("1000"))
        foreign_route = route.model_copy(update={"protocol": "unknown"})
        with pytest.raises(ValueError, match="Unsupported protocol: unknown"):
            await dex.execute_swap(foreign_route)

    async def test_execute_swap_without_wallet_raises(
        self,
        ethereum_blockchain: EthereumBlockchain,
        ethereum_platform: BlockchainPlatform,
        usdc: StaticEthereumAsset,
        weth: StaticEthereumAsset,
    ) -> None:
        dex = make_mainnet_dex(ethereum_blockchain, ethereum_platform)
        route = await dex.quote_swap(usdc, weth, Decimal("1000"))
        with pytest.raises(ValueError, match="No wallet is bound"):
            await dex.execute_swap(route)

    async def test_set_wallet_binds_all_strategies(
        self,
        ethereum_blockchain: EthereumBlockchain,
        ethereum_platform: BlockchainPlatform,
        ethereum_wallet: EthereumWallet,
    ) -> None:
        dex = make_mainnet_dex(ethereum_blockchain, ethereum_platform)
        dex.set_wallet(ethereum_wallet)
        for strategy in dex._protocol_strategies.values():
            assert isinstance(strategy, UniswapV2 | UniswapV3)
            assert strategy.wallet is ethereum_wallet

    async def test_slippage_forwarding_reaches_calldata(
        self,
        ethereum_blockchain: EthereumBlockchain,
        ethereum_platform: BlockchainPlatform,
        ethereum_wallet: EthereumWallet,
        usdc: StaticEthereumAsset,
        weth: StaticEthereumAsset,
    ) -> None:
        """default_slippage -> quote_swap -> route -> minimum-received math."""
        configuration = UniswapConfiguration.local_network(
            platform=ethereum_platform,
            v2_factory_address=V2_FACTORY,
            v2_router_address=V2_ROUTER,
            default_slippage=Decimal("0.02"),
        )
        dex = UniswapDEX(
            blockchain=ethereum_blockchain,
            configuration=configuration,
            wallet=ethereum_wallet,
        )
        route = await dex.quote_swap(usdc, weth, Decimal("1000"))
        assert route.max_slippage == Decimal("0.02")

        with frozen_timestamp():
            transaction = await dex.execute_swap(route)
        _, args = await decode_router_call(
            transaction.other_data["tx_data"], V2_ROUTER, "UniswapV2Router02.json"
        )
        assert args["amountOutMin"] == int(
            Decimal(OUT_1000_USDC_RAW)
            / Decimal(10**18)
            * Decimal("0.98")
            * Decimal(10**18)
        )

    async def test_get_supported_pools_enumerates_candidates(
        self,
        ethereum_blockchain: EthereumBlockchain,
        ethereum_platform: BlockchainPlatform,
        usdc: StaticEthereumAsset,
        weth: StaticEthereumAsset,
        dai: StaticEthereumAsset,
    ) -> None:
        dex = make_mainnet_dex(ethereum_blockchain, ethereum_platform)
        pools = await dex.get_supported_pools(candidate_assets=[usdc, weth, dai])
        # All three pairs exist on V2 in the fake chain
        assert list(pools) == [(usdc, weth), (usdc, dai), (weth, dai)]

    async def test_get_supported_pools_v3_only_uses_configured_tiers(
        self,
        ethereum_blockchain: EthereumBlockchain,
        ethereum_platform: BlockchainPlatform,
        usdc: StaticEthereumAsset,
        weth: StaticEthereumAsset,
        dai: StaticEthereumAsset,
    ) -> None:
        dex = make_mainnet_dex(ethereum_blockchain, ethereum_platform)
        pools = await dex.get_supported_pools(
            protocol="uniswap_v3", candidate_assets=[usdc, weth, dai]
        )
        # V3 has USDC/WETH (500, 3000) and WETH/DAI (500) pools, no USDC/DAI
        assert list(pools) == [(usdc, weth), (weth, dai)]

    async def test_get_supported_pools_requires_candidates(
        self,
        ethereum_blockchain: EthereumBlockchain,
        ethereum_platform: BlockchainPlatform,
    ) -> None:
        dex = make_mainnet_dex(ethereum_blockchain, ethereum_platform)
        with pytest.raises(ValueError, match="at least two candidate assets"):
            await dex.get_supported_pools()


# === Platform threading (hardhat/local support) ===


class TestPlatformThreading:
    @pytest.fixture
    def hardhat_platform(self) -> BlockchainPlatform:
        return BlockchainPlatform(
            identifier="hardhat",
            type=EthereumBlockchainType,
            chain_id=31337,
            local=True,
        )

    @pytest.fixture
    def hardhat_blockchain(
        self, rpc_provider: FakeRPCProvider, hardhat_platform: BlockchainPlatform
    ) -> EthereumBlockchain:
        return EthereumBlockchain(
            configuration=EthereumBlockchainConfiguration(
                platform=hardhat_platform,
                native_asset=EthereumNativeAssetConfiguration(),
                connectivity=EthereumConnectivityConfiguration(
                    rpc_provider=rpc_provider
                ),
                explorer=None,
            )
        )

    async def test_strategy_contracts_carry_the_real_platform(
        self,
        hardhat_blockchain: EthereumBlockchain,
        hardhat_platform: BlockchainPlatform,
    ) -> None:
        strategy = UniswapV2(
            blockchain=hardhat_blockchain,
            factory_address=V2_FACTORY,
            router_address=V2_ROUTER,
        )
        assert strategy.factory_contract.configuration.platform == hardhat_platform
        assert strategy.router_contract.configuration.platform == hardhat_platform
        assert strategy.factory_contract.blockchain is hardhat_blockchain
        assert strategy.router_contract.blockchain is hardhat_blockchain

    async def test_v3_contracts_carry_the_real_platform(
        self,
        hardhat_blockchain: EthereumBlockchain,
        hardhat_platform: BlockchainPlatform,
    ) -> None:
        strategy = UniswapV3(
            blockchain=hardhat_blockchain,
            factory_address=V3_FACTORY,
            router_address=V3_ROUTER,
            quoter_address=V3_QUOTER,
        )
        assert strategy.factory_contract.configuration.platform == hardhat_platform
        assert strategy.quoter_contract.configuration.platform == hardhat_platform
        assert strategy.quoter_contract.blockchain is hardhat_blockchain

    async def test_quote_works_against_hardhat_flavored_chain(
        self,
        hardhat_blockchain: EthereumBlockchain,
        hardhat_platform: BlockchainPlatform,
    ) -> None:
        """No OperatorFactory registration is needed for a local chain."""
        usdc_local = make_asset(hardhat_platform, "USDC", 6, USDC)
        weth_local = make_asset(hardhat_platform, "WETH", 18, WETH)
        strategy = UniswapV2(
            blockchain=hardhat_blockchain,
            factory_address=V2_FACTORY,
            router_address=V2_ROUTER,
        )
        route = await strategy.quote_swap(usdc_local, weth_local, Decimal("1000"))
        assert route.output_amount == Decimal(OUT_1000_USDC_RAW) / Decimal(10**18)

    async def test_facade_local_network_configuration(
        self,
        hardhat_blockchain: EthereumBlockchain,
        hardhat_platform: BlockchainPlatform,
    ) -> None:
        configuration = UniswapConfiguration.local_network(
            platform=hardhat_platform,
            v2_factory_address=V2_FACTORY,
            v2_router_address=V2_ROUTER,
        )
        dex = UniswapDEX(blockchain=hardhat_blockchain, configuration=configuration)
        assert dex.supported_protocols == ["uniswap_v2"]
        assert dex.blockchain is hardhat_blockchain

    def test_facade_unknown_chain_without_configuration_raises(
        self, hardhat_blockchain: EthereumBlockchain
    ) -> None:
        with pytest.raises(ValueError, match="No default Uniswap configuration"):
            UniswapDEX(blockchain=hardhat_blockchain)


# === Signing convenience path ===


class TestCreateSwapTransaction:
    async def test_create_signs_and_broadcasts_via_bound_wallet(
        self,
        rpc_provider: FakeRPCProvider,
        v2: UniswapV2,
        ethereum_wallet: EthereumWallet,
        usdc: StaticEthereumAsset,
        weth: StaticEthereumAsset,
    ) -> None:
        v2.set_wallet(ethereum_wallet)
        route = await v2.quote_swap(usdc, weth, Decimal("1000"))
        with frozen_timestamp():
            transaction = await v2.create_swap_transaction(
                route, client_operation_id="swap-op-1"
            )
        await drain_background_tasks(ethereum_wallet)

        assert transaction.client_operation_id == "swap-op-1"
        assert transaction.signed_transaction is not None
        assert transaction.current_state == BlockchainTransactionState.BROADCASTED
        assert len(rpc_provider.calls_for("eth_sendRawTransaction")) == 1

    async def test_execute_swap_quotes_and_sends(
        self,
        rpc_provider: FakeRPCProvider,
        v3: UniswapV3,
        ethereum_wallet: EthereumWallet,
        usdc: StaticEthereumAsset,
        weth: StaticEthereumAsset,
    ) -> None:
        with frozen_timestamp():
            transaction = await v3.execute_swap(
                usdc,
                weth,
                Decimal("1000"),
                wallet=ethereum_wallet,
                max_slippage=Decimal("0.01"),
            )
        await drain_background_tasks(ethereum_wallet)

        assert transaction.signed_transaction is not None
        assert transaction.current_state == BlockchainTransactionState.BROADCASTED
        assert transaction.client_operation_id.startswith("uniswap_v3_swap_")
        assert len(rpc_provider.calls_for("eth_sendRawTransaction")) == 1
