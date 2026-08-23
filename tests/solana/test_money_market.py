"""
Unit tests for the Solend money market implementation.

This module tests:
- Binary parsers for the on-chain Reserve and Obligation account layouts
  (byte fixtures crafted at the documented offsets, wad 1e18 scaling)
- PDA/derived-address helpers (lending market authority, obligation, ATA)
- Exact instruction encoding (single-byte discriminant + u64 LE amounts) and
  exact AccountMeta lists for the four implemented Solend instructions
- Obligation/collateral-account bootstrap: the exact encoding of the
  SystemProgram CreateAccountWithSeed, InitObligation and idempotent-ATA
  instructions, and the on-chain existence checks that decide whether
  build_supply_transaction prepends them
- Build-only transaction construction (unsigned, real blockhash/fee payer)
- Market data / user account data / positions computed from parsed accounts
- Facade dispatch through the MoneyMarket base class
- Wallet binding (set_wallet / unbound ValueError) and sign-and-send placement

All RPC interaction is mocked at the rpc_client boundary with realistic
solders response payloads; no network access is performed.
"""

import asyncio
import struct
from decimal import Decimal

import pytest
from financepype.operations.transactions.models import BlockchainTransactionState
from financepype.operators.blockchains.models import BlockchainPlatform
from pydantic import SecretStr, ValidationError
from solders import sysvar
from solders.account import Account
from solders.hash import Hash
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.rpc.responses import (
    GetAccountInfoResp,
    GetBalanceResp,
    GetLatestBlockhashResp,
    GetMinimumBalanceForRentExemptionResp,
    RpcBlockhash,
    RpcResponseContext,
    SendTransactionResp,
)
from solders.signature import Signature
from solders.system_program import ID as SYSTEM_PROGRAM_ID
from solders.system_program import decode_create_account_with_seed
from spl.token.constants import ASSOCIATED_TOKEN_PROGRAM_ID, TOKEN_PROGRAM_ID
from spl.token.instructions import (
    create_idempotent_associated_token_account,
    get_associated_token_address,
)

from blockchainpype.dapps.money_market import (
    CollateralMode,
    InterestRateMode,
    ProtocolConfiguration,
    ProtocolImplementation,
)
from blockchainpype.initializer import BlockchainsInitializer, SupportedBlockchainType
from blockchainpype.solana.asset import SolanaAssetData
from blockchainpype.solana.blockchain.blockchain import SolanaBlockchain
from blockchainpype.solana.blockchain.identifier import SolanaAddress
from blockchainpype.solana.dapp.idl import SolanaLocalFileIDL
from blockchainpype.solana.dapp.money_market import (
    NO_DEBT_HEALTH_FACTOR,
    WAD,
    SolanaMoneyMarketConfiguration,
    Solend,
    SolendConfiguration,
    SolendInstruction,
    SolendMoneyMarket,
    SolendObligationCollateral,
    SolendObligationLiquidity,
    SolendObligationState,
    SolendProgram,
    SolendReserveConfiguration,
    SolendReserveState,
)
from blockchainpype.solana.dapp.money_market.solend import (
    OBLIGATION_ACCOUNT_SIZE,
    OBLIGATION_SEED_LENGTH,
)
from blockchainpype.solana.dapp.token import SPLToken
from blockchainpype.solana.transaction import SolanaTransaction
from blockchainpype.solana.wallet.identifier import SolanaWalletIdentifier
from blockchainpype.solana.wallet.signer import SolanaSignerConfiguration
from blockchainpype.solana.wallet.wallet import SolanaWallet, SolanaWalletConfiguration

SOLEND_PROGRAM = "So1endDq2YkqhipRh3WViPa8hdiSpxWy6z3Z6tMCpAo"
# Solend main pool lending market and its (known) derived authority PDA.
MAIN_POOL_LENDING_MARKET = "4UpD2fh7xH3VP9QQaXtsS1YY3bxzWhtfpks7FatyKvdY"
MAIN_POOL_AUTHORITY = "DdZR6zRFiUt4S5mg7AV1uKB2z1f1WzcNYCaTEEWPAuby"

USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
WSOL_MINT = "So11111111111111111111111111111111111111112"

USER_ADDRESS = "11111111111111111111111111111112"
# create_with_seed(USER_ADDRESS, MAIN_POOL_LENDING_MARKET[:32], SOLEND_PROGRAM)
USER_OBLIGATION = "3qNhayr6NxXxjXFKwYJsRaVquYHWTee7y4M9uoN6N8oz"

FIXED_TIMESTAMP = 1_755_000_000.0


def det_pubkey(seed: int) -> Pubkey:
    """Deterministic 32-byte public key for crafted fixtures."""
    return Pubkey(bytes([seed]) * 32)


# === On-chain account byte fixtures (documented offsets) ===


def build_reserve_bytes(
    *,
    lending_market: Pubkey,
    liquidity_mint: Pubkey,
    liquidity_mint_decimals: int,
    liquidity_supply: Pubkey,
    pyth_oracle: Pubkey,
    switchboard_oracle: Pubkey,
    available_amount: int,
    borrowed_amount_wads: int,
    cumulative_borrow_rate_wads: int,
    market_price_wads: int,
    collateral_mint: Pubkey,
    collateral_mint_total_supply: int,
    collateral_supply: Pubkey,
    fee_receiver: Pubkey,
    optimal_utilization_rate: int = 80,
    loan_to_value_ratio: int = 75,
    liquidation_bonus: int = 5,
    liquidation_threshold: int = 80,
    min_borrow_rate: int = 0,
    optimal_borrow_rate: int = 8,
    max_borrow_rate: int = 30,
    borrow_fee_wad: int = 10**15,
    flash_loan_fee_wad: int = 3 * 10**15,
    host_fee_percentage: int = 20,
    deposit_limit: int = 10**18,
    borrow_limit: int = 10**18,
    protocol_liquidation_fee: int = 30,
    protocol_take_rate: int = 20,
    accumulated_protocol_fees_wads: int = 0,
    version: int = 1,
    last_update_slot: int = 123_456_789,
) -> bytes:
    """Pack a Solend Reserve account at the documented 619-byte offsets."""
    data = bytearray(619)
    data[0] = version
    data[1:9] = last_update_slot.to_bytes(8, "little")
    data[9] = 0  # last_update.stale
    data[10:42] = bytes(lending_market)
    data[42:74] = bytes(liquidity_mint)
    data[74] = liquidity_mint_decimals
    data[75:107] = bytes(liquidity_supply)
    data[107:139] = bytes(pyth_oracle)
    data[139:171] = bytes(switchboard_oracle)
    data[171:179] = available_amount.to_bytes(8, "little")
    data[179:195] = borrowed_amount_wads.to_bytes(16, "little")
    data[195:211] = cumulative_borrow_rate_wads.to_bytes(16, "little")
    data[211:227] = market_price_wads.to_bytes(16, "little")
    data[227:259] = bytes(collateral_mint)
    data[259:267] = collateral_mint_total_supply.to_bytes(8, "little")
    data[267:299] = bytes(collateral_supply)
    data[299] = optimal_utilization_rate
    data[300] = loan_to_value_ratio
    data[301] = liquidation_bonus
    data[302] = liquidation_threshold
    data[303] = min_borrow_rate
    data[304] = optimal_borrow_rate
    data[305] = max_borrow_rate
    data[306:314] = borrow_fee_wad.to_bytes(8, "little")
    data[314:322] = flash_loan_fee_wad.to_bytes(8, "little")
    data[322] = host_fee_percentage
    data[323:331] = deposit_limit.to_bytes(8, "little")
    data[331:339] = borrow_limit.to_bytes(8, "little")
    data[339:371] = bytes(fee_receiver)
    data[371] = protocol_liquidation_fee
    data[372] = protocol_take_rate
    data[373:389] = accumulated_protocol_fees_wads.to_bytes(16, "little")
    return bytes(data)


def build_obligation_bytes(
    *,
    owner: Pubkey,
    lending_market: Pubkey,
    deposited_value_wads: int,
    borrowed_value_wads: int,
    allowed_borrow_value_wads: int,
    unhealthy_borrow_value_wads: int,
    deposits: list[tuple[Pubkey, int, int]] | None = None,
    borrows: list[tuple[Pubkey, int, int, int]] | None = None,
    version: int = 1,
    last_update_slot: int = 987_654_321,
) -> bytes:
    """Pack a Solend Obligation account at the documented 1300-byte offsets.

    ``deposits`` entries are ``(deposit_reserve, deposited_amount,
    market_value_wads)``; ``borrows`` entries are ``(borrow_reserve,
    cumulative_borrow_rate_wads, borrowed_amount_wads, market_value_wads)``.
    """
    deposits = deposits or []
    borrows = borrows or []

    data = bytearray(1300)
    data[0] = version
    data[1:9] = last_update_slot.to_bytes(8, "little")
    data[9] = 0  # last_update.stale
    data[10:42] = bytes(lending_market)
    data[42:74] = bytes(owner)
    data[74:90] = deposited_value_wads.to_bytes(16, "little")
    data[90:106] = borrowed_value_wads.to_bytes(16, "little")
    data[106:122] = allowed_borrow_value_wads.to_bytes(16, "little")
    data[122:138] = unhealthy_borrow_value_wads.to_bytes(16, "little")
    data[202] = len(deposits)
    data[203] = len(borrows)

    offset = 204
    for reserve, amount, market_value in deposits:
        data[offset : offset + 32] = bytes(reserve)
        data[offset + 32 : offset + 40] = amount.to_bytes(8, "little")
        data[offset + 40 : offset + 56] = market_value.to_bytes(16, "little")
        offset += 88
    for reserve, cumulative_rate, borrowed, market_value in borrows:
        data[offset : offset + 32] = bytes(reserve)
        data[offset + 32 : offset + 48] = cumulative_rate.to_bytes(16, "little")
        data[offset + 48 : offset + 64] = borrowed.to_bytes(16, "little")
        data[offset + 64 : offset + 80] = market_value.to_bytes(16, "little")
        offset += 112
    return bytes(data)


# === Fixtures ===


@pytest.fixture(scope="session", autouse=True)
def setup_blockchains():
    """Setup blockchain configurations for testing."""
    from blockchainpype.factory import BlockchainFactory

    BlockchainFactory.reset()
    BlockchainsInitializer.configure()


@pytest.fixture
def test_platform():
    """Create a test Solana blockchain platform."""
    return BlockchainPlatform(
        identifier="solana",
        type=SupportedBlockchainType.SOLANA.value,
        chain_id=None,
    )


@pytest.fixture
def solend_protocol():
    """Create a Solend protocol configuration."""
    return ProtocolConfiguration(
        protocol_name="Solend",
        lending_pool_address=SOLEND_PROGRAM,
        data_provider_address=SOLEND_PROGRAM,
    )


@pytest.fixture
def usdc_reserve():
    """Crafted USDC reserve configuration (deterministic account addresses)."""
    return SolendReserveConfiguration(
        address=str(det_pubkey(1)),
        liquidity_mint=USDC_MINT,
        liquidity_supply=str(det_pubkey(2)),
        liquidity_fee_receiver=str(det_pubkey(3)),
        collateral_mint=str(det_pubkey(4)),
        collateral_supply=str(det_pubkey(5)),
        pyth_oracle=str(det_pubkey(6)),
        switchboard_oracle=str(det_pubkey(7)),
        symbol="USDC",
    )


@pytest.fixture
def wsol_reserve():
    """Crafted wSOL reserve configuration (deterministic account addresses)."""
    return SolendReserveConfiguration(
        address=str(det_pubkey(11)),
        liquidity_mint=WSOL_MINT,
        liquidity_supply=str(det_pubkey(12)),
        liquidity_fee_receiver=str(det_pubkey(13)),
        collateral_mint=str(det_pubkey(14)),
        collateral_supply=str(det_pubkey(15)),
        pyth_oracle=str(det_pubkey(16)),
        switchboard_oracle=str(det_pubkey(17)),
        symbol="wSOL",
    )


@pytest.fixture
def solend_configuration(test_platform, solend_protocol, usdc_reserve, wsol_reserve):
    return SolendConfiguration(
        platform=test_platform,
        protocols=[solend_protocol],
        lending_market=MAIN_POOL_LENDING_MARKET,
        reserves=[usdc_reserve, wsol_reserve],
    )


@pytest.fixture
def money_market(solend_configuration):
    return SolendMoneyMarket(solend_configuration)


@pytest.fixture
def solend(money_market) -> Solend:
    strategy = money_market._protocol_strategies["Solend"]
    assert isinstance(strategy, Solend)
    return strategy


@pytest.fixture
def usdc_asset(test_platform):
    """Real SPL token asset for USDC (6 decimals)."""
    mint = SolanaAddress.from_string(USDC_MINT)
    return SPLToken(
        platform=test_platform,
        identifier=mint,
        data=SolanaAssetData(name="USD Coin", symbol="USDC", decimals=6),
        mint=mint,
    )


@pytest.fixture
def sol_asset(test_platform):
    """Real SPL token asset for wrapped SOL (9 decimals)."""
    mint = SolanaAddress.from_string(WSOL_MINT)
    return SPLToken(
        platform=test_platform,
        identifier=mint,
        data=SolanaAssetData(name="Wrapped SOL", symbol="wSOL", decimals=9),
        mint=mint,
    )


@pytest.fixture
def user():
    return SolanaAddress.from_string(USER_ADDRESS)


@pytest.fixture
def blockhash():
    return Hash.new_unique()


@pytest.fixture
def usdc_reserve_bytes(usdc_reserve):
    """USDC reserve state: 500k available + 500k borrowed (utilization 0.5).

    With optimal utilization 80%, min/optimal/max rates 0/8/30% and protocol
    take rate 20% the model yields borrow APR (0.5/0.8)*0.08 = 0.05 and
    supply APR 0.05*0.5*0.8 = 0.02. Collateral supply of 800e9 against total
    liquidity of 1000e9 gives an exchange rate of 0.8 cToken per token.
    """
    return build_reserve_bytes(
        lending_market=Pubkey.from_string(MAIN_POOL_LENDING_MARKET),
        liquidity_mint=Pubkey.from_string(USDC_MINT),
        liquidity_mint_decimals=6,
        liquidity_supply=Pubkey.from_string(usdc_reserve.liquidity_supply),
        pyth_oracle=Pubkey.from_string(usdc_reserve.pyth_oracle),
        switchboard_oracle=Pubkey.from_string(usdc_reserve.switchboard_oracle),
        available_amount=500_000_000_000,
        borrowed_amount_wads=500_000_000_000 * WAD,
        cumulative_borrow_rate_wads=WAD,
        market_price_wads=WAD,
        collateral_mint=Pubkey.from_string(usdc_reserve.collateral_mint),
        collateral_mint_total_supply=800_000_000_000,
        collateral_supply=Pubkey.from_string(usdc_reserve.collateral_supply),
        fee_receiver=Pubkey.from_string(usdc_reserve.liquidity_fee_receiver),
    )


@pytest.fixture
def wsol_reserve_bytes(wsol_reserve):
    """wSOL reserve state: 10k SOL available + 30k borrowed (utilization 0.75).

    Borrow APR is (0.75/0.8)*0.08 = 0.075. The cumulative borrow rate is
    1.25 wad, used to test debt compounding against obligation entries.
    """
    return build_reserve_bytes(
        lending_market=Pubkey.from_string(MAIN_POOL_LENDING_MARKET),
        liquidity_mint=Pubkey.from_string(WSOL_MINT),
        liquidity_mint_decimals=9,
        liquidity_supply=Pubkey.from_string(wsol_reserve.liquidity_supply),
        pyth_oracle=Pubkey.from_string(wsol_reserve.pyth_oracle),
        switchboard_oracle=Pubkey.from_string(wsol_reserve.switchboard_oracle),
        available_amount=10_000 * 10**9,
        borrowed_amount_wads=30_000 * 10**9 * WAD,
        cumulative_borrow_rate_wads=WAD * 5 // 4,  # 1.25 wad
        market_price_wads=150 * WAD,
        collateral_mint=Pubkey.from_string(wsol_reserve.collateral_mint),
        collateral_mint_total_supply=20_000 * 10**9,
        collateral_supply=Pubkey.from_string(wsol_reserve.collateral_supply),
        fee_receiver=Pubkey.from_string(wsol_reserve.liquidity_fee_receiver),
    )


# Rent-exempt minimum the mocked node reports for a 1300-byte obligation.
OBLIGATION_RENT_EXEMPTION = 9_938_880

# SPL token account size; a cToken ATA holding collateral has this layout.
TOKEN_ACCOUNT_SIZE = 165


def build_token_account_bytes(*, mint: Pubkey, owner: Pubkey, amount: int = 0) -> bytes:
    """Pack an SPL token account at the documented 165-byte offsets.

    Only the fields the existence checks and a realistic response need:
    mint (0), owner (32), amount (64, u64 LE) and the initialized state byte
    (108); the COption tags and the remaining fields stay zeroed.
    """
    data = bytearray(TOKEN_ACCOUNT_SIZE)
    data[0:32] = bytes(mint)
    data[32:64] = bytes(owner)
    data[64:72] = amount.to_bytes(8, "little")
    data[108] = 1  # AccountState::Initialized
    return bytes(data)


def make_rpc_client(
    *,
    blockhash: Hash | None = None,
    accounts: dict[Pubkey, bytes] | None = None,
    send_signature: Signature | None = None,
    rent_exemption: int = OBLIGATION_RENT_EXEMPTION,
):
    """Build a mock rpc_client answering at the RPC boundary with realistic
    solders response payloads.

    Accounts absent from ``accounts`` are reported as nonexistent (a null
    ``value``), which is exactly how a node answers for a wallet that has
    never created its obligation or collateral token account. Token-program
    sized payloads are reported as owned by the SPL token program, everything
    else by the Solend program.
    """
    from unittest.mock import AsyncMock, MagicMock

    client = MagicMock(name="rpc_client")

    if blockhash is not None:
        client.get_latest_blockhash = AsyncMock(
            return_value=GetLatestBlockhashResp(
                context=RpcResponseContext(slot=1000),
                value=RpcBlockhash(
                    blockhash=blockhash, last_valid_block_height=100_000
                ),
            )
        )

    account_map = accounts or {}

    async def get_account_info(pubkey: Pubkey, commitment=None):
        data = account_map.get(pubkey)
        value = (
            None
            if data is None
            else Account(
                lamports=2_039_280,
                data=data,
                owner=TOKEN_PROGRAM_ID
                if len(data) == TOKEN_ACCOUNT_SIZE
                else Pubkey.from_string(SOLEND_PROGRAM),
                executable=False,
                rent_epoch=0,
            )
        )
        return GetAccountInfoResp(context=RpcResponseContext(slot=1000), value=value)

    client.get_account_info = AsyncMock(side_effect=get_account_info)

    client.get_minimum_balance_for_rent_exemption = AsyncMock(
        return_value=GetMinimumBalanceForRentExemptionResp(rent_exemption)
    )

    client.get_balance = AsyncMock(
        return_value=GetBalanceResp(
            context=RpcResponseContext(slot=1000), value=1_000_000_000
        )
    )

    if send_signature is not None:
        client.send_transaction = AsyncMock(
            return_value=SendTransactionResp(send_signature)
        )

    return client


def account_metas(instruction) -> list[tuple[Pubkey, bool, bool]]:
    """Project an instruction's metas to (pubkey, is_signer, is_writable)."""
    return [(a.pubkey, a.is_signer, a.is_writable) for a in instruction.accounts]


def ata(owner: SolanaAddress, mint: str) -> Pubkey:
    """Reference ATA derivation via the spl.token helper."""
    return get_associated_token_address(owner.raw, Pubkey.from_string(mint))


def compiled_instructions(transaction: SolanaTransaction):
    """The compiled instructions of a built (unsigned) transaction."""
    raw = transaction.raw_transaction
    assert raw is not None
    return raw.message.instructions


def compiled_data(transaction: SolanaTransaction) -> list[bytes]:
    """The exact data bytes of every instruction, in order."""
    return [bytes(compiled.data) for compiled in compiled_instructions(transaction)]


def compiled_program_ids(transaction: SolanaTransaction) -> list[Pubkey]:
    """The program each compiled instruction targets, in order."""
    raw = transaction.raw_transaction
    assert raw is not None
    message = raw.message
    return [
        message.account_keys[compiled.program_id_index]
        for compiled in message.instructions
    ]


def compiled_accounts(transaction: SolanaTransaction, index: int) -> list[Pubkey]:
    """Resolve one compiled instruction's account indices back to pubkeys."""
    raw = transaction.raw_transaction
    assert raw is not None
    message = raw.message
    compiled = message.instructions[index]
    return [message.account_keys[i] for i in compiled.accounts]


@pytest.fixture
def obligation_bytes(user, usdc_reserve, wsol_reserve):
    """Obligation with one USDC collateral deposit and one wSOL borrow.

    2 cUSDC deposited (6 decimals) and 4 SOL borrowed at cumulative rate
    1.0 wad (9 decimals).
    """
    return build_obligation_bytes(
        owner=user.raw,
        lending_market=Pubkey.from_string(MAIN_POOL_LENDING_MARKET),
        deposited_value_wads=10_000 * WAD,
        borrowed_value_wads=4_000 * WAD,
        allowed_borrow_value_wads=7_500 * WAD,
        unhealthy_borrow_value_wads=8_000 * WAD,
        deposits=[(Pubkey.from_string(usdc_reserve.address), 2_000_000, 2 * WAD)],
        borrows=[
            (Pubkey.from_string(wsol_reserve.address), WAD, 4 * 10**9 * WAD, 600 * WAD)
        ],
    )


@pytest.fixture
def obligation_rpc_client(
    blockhash,
    obligation_bytes,
    usdc_reserve,
    usdc_reserve_bytes,
    wsol_reserve,
    wsol_reserve_bytes,
):
    """RPC client serving the obligation and both reserve accounts."""
    return make_rpc_client(
        blockhash=blockhash,
        accounts={
            Pubkey.from_string(USER_OBLIGATION): obligation_bytes,
            Pubkey.from_string(usdc_reserve.address): usdc_reserve_bytes,
            Pubkey.from_string(wsol_reserve.address): wsol_reserve_bytes,
        },
    )


@pytest.fixture
def usdc_collateral_ata(user, usdc_reserve) -> Pubkey:
    """The user's associated token account for the USDC reserve's cToken."""
    return ata(user, usdc_reserve.collateral_mint)


@pytest.fixture
def supply_ready_rpc_client(
    blockhash, obligation_bytes, usdc_reserve, usdc_collateral_ata, user
):
    """RPC client for a wallet that already supplied before.

    Both accounts ``build_supply_transaction`` probes exist: the obligation
    and the collateral (cToken) associated token account.
    """
    return make_rpc_client(
        blockhash=blockhash,
        accounts={
            Pubkey.from_string(USER_OBLIGATION): obligation_bytes,
            usdc_collateral_ata: build_token_account_bytes(
                mint=Pubkey.from_string(usdc_reserve.collateral_mint),
                owner=user.raw,
                amount=2_000_000,
            ),
        },
    )


# === Binary parsers ===


class TestReserveStateParsing:
    """Test the Reserve account binary parser against crafted fixtures."""

    def test_parses_every_documented_field(self, usdc_reserve, usdc_reserve_bytes):
        state = SolendReserveState.from_bytes(usdc_reserve_bytes)

        assert state.version == 1
        assert state.last_update_slot == 123_456_789
        assert state.lending_market == Pubkey.from_string(MAIN_POOL_LENDING_MARKET)
        assert state.liquidity_mint == Pubkey.from_string(USDC_MINT)
        assert state.liquidity_mint_decimals == 6
        assert state.liquidity_supply == Pubkey.from_string(
            usdc_reserve.liquidity_supply
        )
        assert state.pyth_oracle == Pubkey.from_string(usdc_reserve.pyth_oracle)
        assert state.switchboard_oracle == Pubkey.from_string(
            usdc_reserve.switchboard_oracle
        )
        assert state.available_amount == 500_000_000_000
        assert state.borrowed_amount_wads == 500_000_000_000 * WAD
        assert state.cumulative_borrow_rate_wads == WAD
        assert state.market_price_wads == WAD
        assert state.collateral_mint == Pubkey.from_string(usdc_reserve.collateral_mint)
        assert state.collateral_mint_total_supply == 800_000_000_000
        assert state.collateral_supply == Pubkey.from_string(
            usdc_reserve.collateral_supply
        )
        assert state.optimal_utilization_rate == 80
        assert state.loan_to_value_ratio == 75
        assert state.liquidation_bonus == 5
        assert state.liquidation_threshold == 80
        assert state.min_borrow_rate == 0
        assert state.optimal_borrow_rate == 8
        assert state.max_borrow_rate == 30
        assert state.borrow_fee_wad == 10**15
        assert state.flash_loan_fee_wad == 3 * 10**15
        assert state.host_fee_percentage == 20
        assert state.deposit_limit == 10**18
        assert state.borrow_limit == 10**18
        assert state.fee_receiver == Pubkey.from_string(
            usdc_reserve.liquidity_fee_receiver
        )
        assert state.protocol_liquidation_fee == 30
        assert state.protocol_take_rate == 20
        assert state.accumulated_protocol_fees_wads == 0

    def test_wad_scaling(self, usdc_reserve_bytes):
        state = SolendReserveState.from_bytes(usdc_reserve_bytes)
        # borrowed_amount_wads 500e9 * 1e18 -> 500e9 raw units
        assert state.borrowed_amount == Decimal(500_000_000_000)
        assert state.market_price == Decimal(1)
        assert state.total_liquidity_wads == 1_000_000_000_000 * WAD

    def test_rates_below_optimal_utilization(self, usdc_reserve_bytes):
        state = SolendReserveState.from_bytes(usdc_reserve_bytes)
        assert state.utilization_rate == Decimal("0.5")
        # (0.5 / 0.8) * (0.08 - 0.00) + 0.00
        assert state.borrow_apr == Decimal("0.05")
        # 0.05 * 0.5 * (1 - 0.20)
        assert state.supply_apr == Decimal("0.02")

    def test_rates_above_optimal_utilization(self, wsol_reserve):
        data = build_reserve_bytes(
            lending_market=Pubkey.from_string(MAIN_POOL_LENDING_MARKET),
            liquidity_mint=Pubkey.from_string(WSOL_MINT),
            liquidity_mint_decimals=9,
            liquidity_supply=Pubkey.from_string(wsol_reserve.liquidity_supply),
            pyth_oracle=Pubkey.from_string(wsol_reserve.pyth_oracle),
            switchboard_oracle=Pubkey.from_string(wsol_reserve.switchboard_oracle),
            available_amount=1_000_000_000,
            borrowed_amount_wads=9_000_000_000 * WAD,  # utilization 0.9
            cumulative_borrow_rate_wads=WAD,
            market_price_wads=WAD,
            collateral_mint=Pubkey.from_string(wsol_reserve.collateral_mint),
            collateral_mint_total_supply=10_000_000_000,
            collateral_supply=Pubkey.from_string(wsol_reserve.collateral_supply),
            fee_receiver=Pubkey.from_string(wsol_reserve.liquidity_fee_receiver),
        )
        state = SolendReserveState.from_bytes(data)
        assert state.utilization_rate == Decimal("0.9")
        # 0.08 + ((0.9 - 0.8) / 0.2) * (0.30 - 0.08)
        assert state.borrow_apr == Decimal("0.19")

    def test_collateral_conversions(self, usdc_reserve_bytes):
        state = SolendReserveState.from_bytes(usdc_reserve_bytes)
        # exchange rate: 800e9 collateral / 1000e9 liquidity = 0.8
        assert state.liquidity_to_collateral(1_000_000) == 800_000
        assert state.collateral_to_liquidity(800_000) == Decimal(1_000_000)

    def test_empty_reserve_conversion_raises(self, usdc_reserve):
        data = build_reserve_bytes(
            lending_market=Pubkey.from_string(MAIN_POOL_LENDING_MARKET),
            liquidity_mint=Pubkey.from_string(USDC_MINT),
            liquidity_mint_decimals=6,
            liquidity_supply=Pubkey.from_string(usdc_reserve.liquidity_supply),
            pyth_oracle=Pubkey.from_string(usdc_reserve.pyth_oracle),
            switchboard_oracle=Pubkey.from_string(usdc_reserve.switchboard_oracle),
            available_amount=0,
            borrowed_amount_wads=0,
            cumulative_borrow_rate_wads=WAD,
            market_price_wads=WAD,
            collateral_mint=Pubkey.from_string(usdc_reserve.collateral_mint),
            collateral_mint_total_supply=0,
            collateral_supply=Pubkey.from_string(usdc_reserve.collateral_supply),
            fee_receiver=Pubkey.from_string(usdc_reserve.liquidity_fee_receiver),
        )
        state = SolendReserveState.from_bytes(data)
        assert state.utilization_rate == Decimal(0)
        with pytest.raises(ValueError, match="no liquidity"):
            state.liquidity_to_collateral(1_000_000)

    def test_invalid_size_raises(self):
        with pytest.raises(ValueError, match="expected 619 bytes, got 100"):
            SolendReserveState.from_bytes(bytes(100))


class TestObligationStateParsing:
    """Test the Obligation account binary parser against crafted fixtures."""

    def test_parses_header_and_entries(self, user):
        deposit_reserve = det_pubkey(1)
        borrow_reserve = det_pubkey(11)
        data = build_obligation_bytes(
            owner=user.raw,
            lending_market=Pubkey.from_string(MAIN_POOL_LENDING_MARKET),
            deposited_value_wads=10_000 * WAD,
            borrowed_value_wads=4_000 * WAD,
            allowed_borrow_value_wads=7_500 * WAD,
            unhealthy_borrow_value_wads=8_000 * WAD,
            deposits=[(deposit_reserve, 2_000_000, 2 * WAD)],
            borrows=[(borrow_reserve, WAD, 4 * 10**9 * WAD, 600 * WAD)],
        )

        state = SolendObligationState.from_bytes(data)

        assert state.version == 1
        assert state.last_update_slot == 987_654_321
        assert state.lending_market == Pubkey.from_string(MAIN_POOL_LENDING_MARKET)
        assert state.owner == user.raw
        assert state.deposited_value_wads == 10_000 * WAD
        assert state.borrowed_value_wads == 4_000 * WAD
        assert state.allowed_borrow_value_wads == 7_500 * WAD
        assert state.unhealthy_borrow_value_wads == 8_000 * WAD

        assert state.deposits == [
            SolendObligationCollateral(
                deposit_reserve=deposit_reserve,
                deposited_amount=2_000_000,
                market_value_wads=2 * WAD,
            )
        ]
        assert state.borrows == [
            SolendObligationLiquidity(
                borrow_reserve=borrow_reserve,
                cumulative_borrow_rate_wads=WAD,
                borrowed_amount_wads=4 * 10**9 * WAD,
                market_value_wads=600 * WAD,
            )
        ]

    def test_multiple_entries_are_packed_sequentially(self, user):
        data = build_obligation_bytes(
            owner=user.raw,
            lending_market=Pubkey.from_string(MAIN_POOL_LENDING_MARKET),
            deposited_value_wads=0,
            borrowed_value_wads=0,
            allowed_borrow_value_wads=0,
            unhealthy_borrow_value_wads=0,
            deposits=[(det_pubkey(21), 111, 0), (det_pubkey(22), 222, 0)],
            borrows=[(det_pubkey(23), WAD, 333, 0)],
        )
        state = SolendObligationState.from_bytes(data)
        assert [d.deposited_amount for d in state.deposits] == [111, 222]
        assert state.deposits[1].deposit_reserve == det_pubkey(22)
        assert len(state.borrows) == 1
        assert state.borrows[0].borrowed_amount_wads == 333

    def test_invalid_size_raises(self):
        with pytest.raises(ValueError, match="expected 1300 bytes, got 10"):
            SolendObligationState.from_bytes(bytes(10))

    def test_overflowing_entry_counts_raise(self, user):
        data = bytearray(
            build_obligation_bytes(
                owner=user.raw,
                lending_market=Pubkey.from_string(MAIN_POOL_LENDING_MARKET),
                deposited_value_wads=0,
                borrowed_value_wads=0,
                allowed_borrow_value_wads=0,
                unhealthy_borrow_value_wads=0,
            )
        )
        data[202] = 13  # 13 * 88 = 1144 > 1096 available
        with pytest.raises(ValueError, match="overflow"):
            SolendObligationState.from_bytes(bytes(data))


# === Derivations ===


class TestDerivations:
    """Test PDA and account derivations against known values."""

    def test_lending_market_authority_matches_main_pool(self, solend):
        # Known Solend main-pool authority: PDA of [lending_market] seeds
        authority = solend.derive_lending_market_authority()
        assert authority.string == MAIN_POOL_AUTHORITY
        expected, _bump = Pubkey.find_program_address(
            [bytes(Pubkey.from_string(MAIN_POOL_LENDING_MARKET))],
            Pubkey.from_string(SOLEND_PROGRAM),
        )
        assert authority.raw == expected

    def test_obligation_address_derivation(self, solend, user):
        obligation = solend.derive_obligation_address(user)
        assert obligation.string == USER_OBLIGATION
        assert obligation.raw == Pubkey.create_with_seed(
            user.raw,
            MAIN_POOL_LENDING_MARKET[:32],
            Pubkey.from_string(SOLEND_PROGRAM),
        )

    def test_associated_token_account_derivation(self, solend, user):
        derived = solend.derive_associated_token_account(
            user, SolanaAddress.from_string(USDC_MINT)
        )
        assert derived.raw == ata(user, USDC_MINT)


# === Instruction encoding ===


class TestInstructionEncoding:
    """Test exact data bytes and AccountMeta lists per instruction."""

    @pytest.fixture(autouse=True)
    async def initialize_program(self, solend):
        await solend.program.initialize()

    def test_deposit_instruction(self, solend, usdc_reserve, user):
        raw_amount = 1_500_000
        instruction = solend.build_deposit_instruction(usdc_reserve, user, raw_amount)

        assert instruction.program_id == Pubkey.from_string(SOLEND_PROGRAM)
        assert instruction.data == bytes([14]) + raw_amount.to_bytes(8, "little")

        authority = Pubkey.from_string(MAIN_POOL_AUTHORITY)
        obligation = Pubkey.from_string(USER_OBLIGATION)
        assert account_metas(instruction) == [
            (ata(user, USDC_MINT), False, True),
            (ata(user, usdc_reserve.collateral_mint), False, True),
            (Pubkey.from_string(usdc_reserve.address), False, True),
            (Pubkey.from_string(usdc_reserve.liquidity_supply), False, True),
            (Pubkey.from_string(usdc_reserve.collateral_mint), False, True),
            (Pubkey.from_string(MAIN_POOL_LENDING_MARKET), False, False),
            (authority, False, False),
            (Pubkey.from_string(usdc_reserve.collateral_supply), False, True),
            (obligation, False, True),
            (user.raw, True, False),
            (Pubkey.from_string(usdc_reserve.pyth_oracle), False, False),
            (Pubkey.from_string(usdc_reserve.switchboard_oracle), False, False),
            (user.raw, True, False),
            (TOKEN_PROGRAM_ID, False, False),
        ]

    def test_withdraw_instruction(self, solend, usdc_reserve, user):
        raw_collateral = 800_000
        instruction = solend.build_withdraw_instruction(
            usdc_reserve, user, raw_collateral
        )

        assert instruction.data == bytes([15]) + raw_collateral.to_bytes(8, "little")

        assert account_metas(instruction) == [
            (Pubkey.from_string(usdc_reserve.collateral_supply), False, True),
            (ata(user, usdc_reserve.collateral_mint), False, True),
            (Pubkey.from_string(usdc_reserve.address), False, True),
            (Pubkey.from_string(USER_OBLIGATION), False, True),
            (Pubkey.from_string(MAIN_POOL_LENDING_MARKET), False, False),
            (Pubkey.from_string(MAIN_POOL_AUTHORITY), False, False),
            (ata(user, USDC_MINT), False, True),
            (Pubkey.from_string(usdc_reserve.collateral_mint), False, True),
            (Pubkey.from_string(usdc_reserve.liquidity_supply), False, True),
            (user.raw, True, False),
            (user.raw, True, False),
            (TOKEN_PROGRAM_ID, False, False),
        ]

    def test_borrow_instruction(self, solend, wsol_reserve, user):
        raw_amount = 500_000_000
        instruction = solend.build_borrow_instruction(wsol_reserve, user, raw_amount)

        assert instruction.data == bytes([10]) + raw_amount.to_bytes(8, "little")

        assert account_metas(instruction) == [
            (Pubkey.from_string(wsol_reserve.liquidity_supply), False, True),
            (ata(user, WSOL_MINT), False, True),
            (Pubkey.from_string(wsol_reserve.address), False, True),
            (Pubkey.from_string(wsol_reserve.liquidity_fee_receiver), False, True),
            (Pubkey.from_string(USER_OBLIGATION), False, True),
            (Pubkey.from_string(MAIN_POOL_LENDING_MARKET), False, False),
            (Pubkey.from_string(MAIN_POOL_AUTHORITY), False, False),
            (user.raw, True, False),
            (TOKEN_PROGRAM_ID, False, False),
        ]

    def test_repay_instruction(self, solend, usdc_reserve, user):
        raw_amount = 123_456_789
        instruction = solend.build_repay_instruction(usdc_reserve, user, raw_amount)

        assert instruction.data == bytes([11]) + raw_amount.to_bytes(8, "little")

        assert account_metas(instruction) == [
            (ata(user, USDC_MINT), False, True),
            (Pubkey.from_string(usdc_reserve.liquidity_supply), False, True),
            (Pubkey.from_string(usdc_reserve.address), False, True),
            (Pubkey.from_string(USER_OBLIGATION), False, True),
            (Pubkey.from_string(MAIN_POOL_LENDING_MARKET), False, False),
            (user.raw, True, False),
            (TOKEN_PROGRAM_ID, False, False),
        ]

    def test_refresh_reserve_instruction(self, solend, usdc_reserve):
        instruction = solend.build_refresh_reserve_instruction(usdc_reserve)

        assert instruction.program_id == Pubkey.from_string(SOLEND_PROGRAM)
        # No arguments: the discriminant byte alone
        assert instruction.data == bytes([3])
        assert account_metas(instruction) == [
            (Pubkey.from_string(usdc_reserve.address), False, True),
            (Pubkey.from_string(usdc_reserve.pyth_oracle), False, False),
            (Pubkey.from_string(usdc_reserve.switchboard_oracle), False, False),
        ]

    def test_refresh_obligation_instruction(
        self, solend, user, usdc_reserve, wsol_reserve
    ):
        instruction = solend.build_refresh_obligation_instruction(
            user, [usdc_reserve, wsol_reserve]
        )

        assert instruction.program_id == Pubkey.from_string(SOLEND_PROGRAM)
        assert instruction.data == bytes([7])
        # Obligation (writable) followed by its reserves, read-only, in order
        assert account_metas(instruction) == [
            (Pubkey.from_string(USER_OBLIGATION), False, True),
            (Pubkey.from_string(usdc_reserve.address), False, False),
            (Pubkey.from_string(wsol_reserve.address), False, False),
        ]

    def test_refresh_obligation_without_reserves(self, solend, user):
        instruction = solend.build_refresh_obligation_instruction(user, [])
        assert account_metas(instruction) == [
            (Pubkey.from_string(USER_OBLIGATION), False, True)
        ]

    def test_refresh_prefix_deduplicates_reserves(
        self, solend, user, usdc_reserve, wsol_reserve
    ):
        """A reserve used as both collateral and borrow is refreshed once."""
        obligation = SolendObligationState.from_bytes(
            build_obligation_bytes(
                owner=user.raw,
                lending_market=Pubkey.from_string(MAIN_POOL_LENDING_MARKET),
                deposited_value_wads=WAD,
                borrowed_value_wads=WAD,
                allowed_borrow_value_wads=WAD,
                unhealthy_borrow_value_wads=WAD,
                deposits=[(Pubkey.from_string(usdc_reserve.address), 1, WAD)],
                borrows=[(Pubkey.from_string(usdc_reserve.address), WAD, WAD, WAD)],
            )
        )

        instructions = solend.build_obligation_refresh_instructions(
            user, obligation, wsol_reserve
        )

        # RefreshReserve(usdc), RefreshReserve(wsol target), RefreshObligation
        assert [bytes(i.data) for i in instructions] == [
            bytes([3]),
            bytes([3]),
            bytes([7]),
        ]
        assert instructions[0].accounts[0].pubkey == Pubkey.from_string(
            usdc_reserve.address
        )
        assert instructions[1].accounts[0].pubkey == Pubkey.from_string(
            wsol_reserve.address
        )
        # The obligation still lists the duplicated reserve twice, as stored
        assert account_metas(instructions[2]) == [
            (Pubkey.from_string(USER_OBLIGATION), False, True),
            (Pubkey.from_string(usdc_reserve.address), False, False),
            (Pubkey.from_string(usdc_reserve.address), False, False),
        ]

    def test_discriminants_match_solend_program_enum(self):
        assert SolendInstruction.REFRESH_RESERVE == 3
        assert SolendInstruction.INIT_OBLIGATION == 6
        assert SolendInstruction.REFRESH_OBLIGATION == 7
        assert SolendInstruction.BORROW_OBLIGATION_LIQUIDITY == 10
        assert SolendInstruction.REPAY_OBLIGATION_LIQUIDITY == 11
        assert (
            SolendInstruction.DEPOSIT_RESERVE_LIQUIDITY_AND_OBLIGATION_COLLATERAL == 14
        )
        assert (
            SolendInstruction.WITHDRAW_OBLIGATION_COLLATERAL_AND_REDEEM_RESERVE_COLLATERAL
            == 15
        )


# === Obligation / collateral account bootstrap ===


class TestObligationBootstrapInstructions:
    """Exact encoding of the account-creation instructions of a first supply."""

    @pytest.fixture(autouse=True)
    async def initialize_program(self, solend):
        await solend.program.initialize()

    def test_obligation_seed_is_the_market_address_prefix(self, solend):
        assert solend.obligation_seed == MAIN_POOL_LENDING_MARKET[:32]
        assert len(solend.obligation_seed) == OBLIGATION_SEED_LENGTH

    def test_create_account_with_seed_fields(self, solend, user):
        instruction = solend.build_create_obligation_account_instruction(
            user, OBLIGATION_RENT_EXEMPTION
        )

        assert instruction.program_id == SYSTEM_PROGRAM_ID
        # funder (signer, writable), created account (writable), seed base
        assert account_metas(instruction) == [
            (user.raw, True, True),
            (Pubkey.from_string(USER_OBLIGATION), False, True),
            (user.raw, True, False),
        ]

        params = decode_create_account_with_seed(instruction)
        assert params["from_pubkey"] == user.raw
        assert params["to_pubkey"] == Pubkey.from_string(USER_OBLIGATION)
        assert params["base"] == user.raw
        assert params["seed"] == MAIN_POOL_LENDING_MARKET[:32]
        assert params["lamports"] == OBLIGATION_RENT_EXEMPTION
        assert params["space"] == OBLIGATION_ACCOUNT_SIZE == 1300
        assert params["owner"] == Pubkey.from_string(SOLEND_PROGRAM)

    def test_create_account_with_seed_exact_bytes(self, solend, user):
        instruction = solend.build_create_obligation_account_instruction(
            user, OBLIGATION_RENT_EXEMPTION
        )
        seed = MAIN_POOL_LENDING_MARKET[:32].encode()

        # SystemInstruction::CreateAccountWithSeed is enum index 3 (u32 LE),
        # followed by base, the borsh-style u64-prefixed seed, lamports,
        # space and the owner program.
        assert bytes(instruction.data) == (
            struct.pack("<I", 3)
            + bytes(user.raw)
            + struct.pack("<Q", len(seed))
            + seed
            + struct.pack("<Q", OBLIGATION_RENT_EXEMPTION)
            + struct.pack("<Q", OBLIGATION_ACCOUNT_SIZE)
            + bytes(Pubkey.from_string(SOLEND_PROGRAM))
        )

    def test_created_address_matches_the_derivation(self, solend, user):
        """The created account lands exactly where every other builder looks."""
        params = decode_create_account_with_seed(
            solend.build_create_obligation_account_instruction(user, 1)
        )
        assert params["to_pubkey"] == solend.derive_obligation_address(user).raw
        assert params["to_pubkey"] == Pubkey.create_with_seed(
            params["base"], params["seed"], params["owner"]
        )

    def test_init_obligation_instruction(self, solend, user):
        instruction = solend.build_init_obligation_instruction(user)

        assert instruction.program_id == Pubkey.from_string(SOLEND_PROGRAM)
        # No arguments: the discriminant byte alone
        assert bytes(instruction.data) == b"\x06"
        assert account_metas(instruction) == [
            (Pubkey.from_string(USER_OBLIGATION), False, True),
            (Pubkey.from_string(MAIN_POOL_LENDING_MARKET), False, False),
            (user.raw, True, False),
            (sysvar.CLOCK, False, False),
            (sysvar.RENT, False, False),
            (TOKEN_PROGRAM_ID, False, False),
        ]

    def test_create_collateral_account_is_idempotent(
        self, solend, usdc_reserve, user, usdc_collateral_ata
    ):
        instruction = solend.build_create_collateral_account_instruction(
            usdc_reserve, user
        )

        assert instruction.program_id == ASSOCIATED_TOKEN_PROGRAM_ID
        # The idempotent variant is discriminant 1; the plain one has no data
        assert bytes(instruction.data) == b"\x01"
        assert account_metas(instruction) == [
            (user.raw, True, True),
            (usdc_collateral_ata, False, True),
            (user.raw, False, False),
            (Pubkey.from_string(usdc_reserve.collateral_mint), False, False),
            (SYSTEM_PROGRAM_ID, False, False),
            (TOKEN_PROGRAM_ID, False, False),
        ]
        assert instruction == create_idempotent_associated_token_account(
            payer=user.raw,
            owner=user.raw,
            mint=Pubkey.from_string(usdc_reserve.collateral_mint),
        )

    def test_collateral_account_targets_the_deposit_destination(
        self, solend, usdc_reserve, user
    ):
        """The created ATA is the deposit's destination-collateral account."""
        creation = solend.build_create_collateral_account_instruction(
            usdc_reserve, user
        )
        deposit = solend.build_deposit_instruction(usdc_reserve, user, 1)
        assert creation.accounts[1].pubkey == deposit.accounts[1].pubkey


class TestSupplyPrerequisites:
    """The on-chain existence checks that gate the creation instructions."""

    @pytest.fixture(autouse=True)
    async def initialize_program(self, solend):
        await solend.program.initialize()

    async def test_fresh_wallet_needs_both_accounts(
        self, solend, usdc_reserve, user, usdc_collateral_ata, blockhash, monkeypatch
    ):
        client = make_rpc_client(blockhash=blockhash, accounts={})
        monkeypatch.setattr(solend.blockchain, "rpc_client", client)

        instructions = await solend.build_supply_prerequisite_instructions(
            usdc_reserve, user
        )

        assert [bytes(i.data) for i in instructions] == [
            bytes(
                solend.build_create_obligation_account_instruction(
                    user, OBLIGATION_RENT_EXEMPTION
                ).data
            ),
            b"\x06",
            b"\x01",
        ]
        # Exactly one probe per account, at the blockchain's commitment
        assert [call.args[0] for call in client.get_account_info.await_args_list] == [
            Pubkey.from_string(USER_OBLIGATION),
            usdc_collateral_ata,
        ]
        for call in client.get_account_info.await_args_list:
            assert call.kwargs == {"commitment": solend.blockchain.commitment}
        # The rent-exempt minimum is read for the obligation's exact size
        client.get_minimum_balance_for_rent_exemption.assert_awaited_once_with(
            OBLIGATION_ACCOUNT_SIZE, commitment=solend.blockchain.commitment
        )

    async def test_existing_accounts_need_nothing(
        self, solend, usdc_reserve, user, supply_ready_rpc_client, monkeypatch
    ):
        monkeypatch.setattr(solend.blockchain, "rpc_client", supply_ready_rpc_client)

        instructions = await solend.build_supply_prerequisite_instructions(
            usdc_reserve, user
        )

        assert instructions == []
        supply_ready_rpc_client.get_minimum_balance_for_rent_exemption.assert_not_awaited()

    async def test_existing_obligation_missing_collateral_account(
        self, solend, usdc_reserve, user, obligation_bytes, blockhash, monkeypatch
    ):
        """Only the ATA is created when the obligation is already on-chain."""
        client = make_rpc_client(
            blockhash=blockhash,
            accounts={Pubkey.from_string(USER_OBLIGATION): obligation_bytes},
        )
        monkeypatch.setattr(solend.blockchain, "rpc_client", client)

        instructions = await solend.build_supply_prerequisite_instructions(
            usdc_reserve, user
        )

        assert [bytes(i.data) for i in instructions] == [b"\x01"]
        client.get_minimum_balance_for_rent_exemption.assert_not_awaited()

    async def test_existing_collateral_account_missing_obligation(
        self,
        solend,
        usdc_reserve,
        user,
        usdc_collateral_ata,
        blockhash,
        monkeypatch,
    ):
        """Only the obligation pair is created when the ATA already exists."""
        client = make_rpc_client(
            blockhash=blockhash,
            accounts={
                usdc_collateral_ata: build_token_account_bytes(
                    mint=Pubkey.from_string(usdc_reserve.collateral_mint),
                    owner=user.raw,
                )
            },
        )
        monkeypatch.setattr(solend.blockchain, "rpc_client", client)

        instructions = await solend.build_supply_prerequisite_instructions(
            usdc_reserve, user
        )

        assert [i.program_id for i in instructions] == [
            SYSTEM_PROGRAM_ID,
            Pubkey.from_string(SOLEND_PROGRAM),
        ]
        assert bytes(instructions[1].data) == b"\x06"

    async def test_rent_exemption_uses_the_reported_lamports(
        self, solend, usdc_reserve, user, blockhash, monkeypatch
    ):
        """The funded lamports come from the node, not from a constant."""
        client = make_rpc_client(
            blockhash=blockhash, accounts={}, rent_exemption=12_345_678
        )
        monkeypatch.setattr(solend.blockchain, "rpc_client", client)

        instructions = await solend.build_supply_prerequisite_instructions(
            usdc_reserve, user
        )

        params = decode_create_account_with_seed(instructions[0])
        assert params["lamports"] == 12_345_678

    async def test_account_exists_reflects_the_rpc(
        self, solend, user, obligation_bytes, blockhash, monkeypatch
    ):
        client = make_rpc_client(
            blockhash=blockhash,
            accounts={Pubkey.from_string(USER_OBLIGATION): obligation_bytes},
        )
        monkeypatch.setattr(solend.blockchain, "rpc_client", client)

        assert await solend.account_exists(SolanaAddress.from_string(USER_OBLIGATION))
        assert not await solend.account_exists(
            SolanaAddress.from_raw(Pubkey.new_unique())
        )


# === Transaction building (build-only) ===


class TestBuildTransactions:
    """Test build_* methods produce real, unsigned SolanaTransactions."""

    @pytest.fixture(autouse=True)
    def fixed_timestamp(self, monkeypatch):
        monkeypatch.setattr(
            SolanaBlockchain,
            "current_timestamp",
            property(lambda self: FIXED_TIMESTAMP),
        )

    async def test_build_supply_transaction(
        self, solend, usdc_asset, user, blockhash, supply_ready_rpc_client, monkeypatch
    ):
        """A wallet whose obligation and collateral ATA exist supplies alone."""
        monkeypatch.setattr(solend.blockchain, "rpc_client", supply_ready_rpc_client)

        transaction = await solend.build_supply_transaction(
            usdc_asset, Decimal("1.5"), USER_ADDRESS
        )

        assert isinstance(transaction, SolanaTransaction)
        # Build-only: unsigned, pending broadcast
        assert transaction.signed_transaction is None
        assert not transaction.is_signed
        assert transaction.current_state == BlockchainTransactionState.PENDING_BROADCAST
        assert transaction.creation_timestamp == FIXED_TIMESTAMP
        assert transaction.client_operation_id.startswith(f"solend-supply-{USDC_MINT}")
        assert transaction.owner_identifier is not None
        assert transaction.owner_identifier.name == USER_ADDRESS

        raw = transaction.raw_transaction
        assert raw is not None
        assert raw.fee_payer == user.raw
        assert raw.recent_blockhash == blockhash
        assert raw.is_versioned is False

        message = raw.message
        assert message.recent_blockhash == blockhash
        assert message.account_keys[0] == user.raw
        assert len(message.instructions) == 1
        compiled = message.instructions[0]
        # Decimal("1.5") at 6 decimals -> 1_500_000 raw units
        assert compiled.data == bytes([14]) + (1_500_000).to_bytes(8, "little")
        assert message.account_keys[compiled.program_id_index] == Pubkey.from_string(
            SOLEND_PROGRAM
        )

    async def test_build_supply_custom_operation_id(
        self, solend, usdc_asset, blockhash, monkeypatch
    ):
        client = make_rpc_client(blockhash=blockhash)
        monkeypatch.setattr(solend.blockchain, "rpc_client", client)

        transaction = await solend.build_supply_transaction(
            usdc_asset,
            Decimal("1"),
            USER_ADDRESS,
            client_operation_id="my-supply-1",
        )
        assert transaction.client_operation_id == "my-supply-1"

    async def test_build_withdraw_converts_to_collateral_units(
        self,
        solend,
        usdc_asset,
        usdc_reserve,
        obligation_rpc_client,
        monkeypatch,
    ):
        monkeypatch.setattr(solend.blockchain, "rpc_client", obligation_rpc_client)

        transaction = await solend.build_withdraw_transaction(
            usdc_asset, Decimal("1"), USER_ADDRESS
        )

        # 1 USDC = 1_000_000 raw liquidity -> 800_000 cTokens at rate 0.8
        assert compiled_data(transaction)[-1] == bytes([15]) + (800_000).to_bytes(
            8, "little"
        )
        # The obligation is read first (for the refresh set), then the reserve
        # whose exchange rate converts the amount - both at the blockchain's
        # commitment
        assert [
            call.args[0]
            for call in obligation_rpc_client.get_account_info.await_args_list
        ] == [
            Pubkey.from_string(USER_OBLIGATION),
            Pubkey.from_string(usdc_reserve.address),
        ]
        for call in obligation_rpc_client.get_account_info.await_args_list:
            assert call.kwargs == {"commitment": solend.blockchain.commitment}

    async def test_build_withdraw_all_uses_the_deposited_collateral(
        self, solend, usdc_asset, obligation_rpc_client, monkeypatch
    ):
        """withdraw_all resolves the obligation's exact deposited cToken amount."""
        monkeypatch.setattr(solend.blockchain, "rpc_client", obligation_rpc_client)

        transaction = await solend.build_withdraw_transaction(
            usdc_asset, Decimal("0"), USER_ADDRESS, withdraw_all=True
        )

        # The obligation deposit entry holds 2_000_000 cUSDC
        assert compiled_data(transaction)[-1] == bytes([15]) + (2_000_000).to_bytes(
            8, "little"
        )
        # No reserve-state read is needed: only the obligation is fetched
        assert [
            call.args[0]
            for call in obligation_rpc_client.get_account_info.await_args_list
        ] == [Pubkey.from_string(USER_OBLIGATION)]

    async def test_build_withdraw_all_without_deposit_raises(
        self, solend, sol_asset, obligation_rpc_client, monkeypatch
    ):
        """wSOL is borrowed, not deposited, so there is nothing to withdraw."""
        monkeypatch.setattr(solend.blockchain, "rpc_client", obligation_rpc_client)

        with pytest.raises(ValueError, match="holds no collateral"):
            await solend.build_withdraw_transaction(
                sol_asset, Decimal("0"), USER_ADDRESS, withdraw_all=True
            )

    async def test_build_withdraw_without_obligation_raises(
        self, solend, usdc_asset, blockhash, monkeypatch
    ):
        client = make_rpc_client(blockhash=blockhash, accounts={})
        monkeypatch.setattr(solend.blockchain, "rpc_client", client)

        with pytest.raises(ValueError, match="No Solend obligation account"):
            await solend.build_withdraw_transaction(
                usdc_asset, Decimal("1"), USER_ADDRESS
            )

    async def test_build_borrow_scales_nine_decimals(
        self, solend, sol_asset, obligation_rpc_client, monkeypatch
    ):
        monkeypatch.setattr(solend.blockchain, "rpc_client", obligation_rpc_client)

        transaction = await solend.build_borrow_transaction(
            sol_asset, Decimal("0.5"), InterestRateMode.VARIABLE, USER_ADDRESS
        )

        # Decimal("0.5") at 9 decimals -> 500_000_000 raw units
        assert compiled_data(transaction)[-1] == bytes([10]) + (500_000_000).to_bytes(
            8, "little"
        )
        assert transaction.client_operation_id.startswith(f"solend-borrow-{WSOL_MINT}")

    async def test_build_borrow_without_obligation_raises(
        self, solend, sol_asset, blockhash, monkeypatch
    ):
        client = make_rpc_client(blockhash=blockhash, accounts={})
        monkeypatch.setattr(solend.blockchain, "rpc_client", client)

        with pytest.raises(ValueError, match="No Solend obligation account"):
            await solend.build_borrow_transaction(
                sol_asset, Decimal("1"), InterestRateMode.VARIABLE, USER_ADDRESS
            )

    async def test_build_repay_transaction(
        self, solend, usdc_asset, blockhash, monkeypatch
    ):
        client = make_rpc_client(blockhash=blockhash)
        monkeypatch.setattr(solend.blockchain, "rpc_client", client)

        transaction = await solend.build_repay_transaction(
            usdc_asset, Decimal("0.000001"), InterestRateMode.VARIABLE, USER_ADDRESS
        )
        # Smallest representable unit at 6 decimals -> 1 raw unit
        assert compiled_data(transaction)[-1] == bytes([11]) + (1).to_bytes(8, "little")
        # Repay needs no obligation read: only the repay reserve is refreshed
        client.get_account_info.assert_not_awaited()

    async def test_build_repay_all_uses_u64_max(
        self, solend, usdc_asset, blockhash, monkeypatch
    ):
        client = make_rpc_client(blockhash=blockhash)
        monkeypatch.setattr(solend.blockchain, "rpc_client", client)

        transaction = await solend.build_repay_transaction(
            usdc_asset,
            Decimal("0"),
            InterestRateMode.VARIABLE,
            USER_ADDRESS,
            repay_all=True,
        )
        assert compiled_data(transaction)[-1] == bytes([11]) + (2**64 - 1).to_bytes(
            8, "little"
        )

    async def test_supply_holds_only_the_deposit_instruction(
        self, solend, usdc_asset, supply_ready_rpc_client, monkeypatch
    ):
        """Deposit carries its own oracles, so it refreshes the reserve itself.

        With both prerequisite accounts already on-chain, nothing is prepended
        and no ``RefreshReserve`` is needed either.
        """
        monkeypatch.setattr(solend.blockchain, "rpc_client", supply_ready_rpc_client)

        transaction = await solend.build_supply_transaction(
            usdc_asset, Decimal("1.5"), USER_ADDRESS
        )

        assert compiled_data(transaction) == [
            bytes([14]) + (1_500_000).to_bytes(8, "little")
        ]
        # No rent lookup happens when there is no account to create
        supply_ready_rpc_client.get_minimum_balance_for_rent_exemption.assert_not_awaited()

    async def test_fresh_wallet_supply_prepends_account_creation(
        self,
        solend,
        usdc_asset,
        usdc_reserve,
        user,
        usdc_collateral_ata,
        blockhash,
        monkeypatch,
    ):
        """A first-time wallet gets obligation + ATA creation in one transaction."""
        client = make_rpc_client(blockhash=blockhash, accounts={})
        monkeypatch.setattr(solend.blockchain, "rpc_client", client)

        transaction = await solend.build_supply_transaction(
            usdc_asset, Decimal("1.5"), USER_ADDRESS
        )

        data = compiled_data(transaction)
        assert len(data) == 4
        # CreateAccountWithSeed, InitObligation, idempotent ATA, deposit
        assert struct.unpack("<I", data[0][:4])[0] == 3
        assert data[1] == b"\x06"
        assert data[2] == b"\x01"
        assert data[3] == bytes([14]) + (1_500_000).to_bytes(8, "little")

        assert compiled_program_ids(transaction) == [
            SYSTEM_PROGRAM_ID,
            Pubkey.from_string(SOLEND_PROGRAM),
            ASSOCIATED_TOKEN_PROGRAM_ID,
            Pubkey.from_string(SOLEND_PROGRAM),
        ]

        # The created obligation and ATA are the very accounts the deposit uses
        assert compiled_accounts(transaction, 0)[1] == Pubkey.from_string(
            USER_OBLIGATION
        )
        assert compiled_accounts(transaction, 1) == [
            Pubkey.from_string(USER_OBLIGATION),
            Pubkey.from_string(MAIN_POOL_LENDING_MARKET),
            user.raw,
            sysvar.CLOCK,
            sysvar.RENT,
            TOKEN_PROGRAM_ID,
        ]
        assert compiled_accounts(transaction, 2)[1] == usdc_collateral_ata
        deposit_accounts = compiled_accounts(transaction, 3)
        assert deposit_accounts[1] == usdc_collateral_ata
        assert deposit_accounts[8] == Pubkey.from_string(USER_OBLIGATION)

        # The obligation is funded with the rent-exempt minimum the node reports
        assert data[0] == bytes(
            solend.build_create_obligation_account_instruction(
                user, OBLIGATION_RENT_EXEMPTION
            ).data
        )
        client.get_minimum_balance_for_rent_exemption.assert_awaited_once_with(
            OBLIGATION_ACCOUNT_SIZE, commitment=solend.blockchain.commitment
        )
        # The ATA created is the one derived for the reserve's collateral mint
        assert usdc_collateral_ata == ata(user, usdc_reserve.collateral_mint)

    async def test_supply_with_existing_accounts_adds_nothing(
        self, solend, usdc_asset, supply_ready_rpc_client, monkeypatch
    ):
        """Both accounts on-chain: the transaction is the deposit alone."""
        monkeypatch.setattr(solend.blockchain, "rpc_client", supply_ready_rpc_client)

        transaction = await solend.build_supply_transaction(
            usdc_asset, Decimal("2"), USER_ADDRESS
        )

        assert compiled_data(transaction) == [
            bytes([14]) + (2_000_000).to_bytes(8, "little")
        ]

    async def test_withdraw_and_borrow_never_create_the_obligation(
        self, solend, usdc_asset, sol_asset, blockhash, monkeypatch
    ):
        """Only supply bootstraps: the other flows still require an obligation."""
        client = make_rpc_client(blockhash=blockhash, accounts={})
        monkeypatch.setattr(solend.blockchain, "rpc_client", client)

        with pytest.raises(ValueError, match="No Solend obligation account"):
            await solend.build_withdraw_transaction(
                usdc_asset, Decimal("1"), USER_ADDRESS
            )
        with pytest.raises(ValueError, match="No Solend obligation account"):
            await solend.build_borrow_transaction(
                sol_asset, Decimal("1"), InterestRateMode.VARIABLE, USER_ADDRESS
            )
        client.get_minimum_balance_for_rent_exemption.assert_not_awaited()

    async def test_withdraw_instruction_sequence(
        self,
        solend,
        usdc_asset,
        usdc_reserve,
        wsol_reserve,
        obligation_rpc_client,
        monkeypatch,
    ):
        """RefreshReserve per obligation reserve, RefreshObligation, withdraw."""
        monkeypatch.setattr(solend.blockchain, "rpc_client", obligation_rpc_client)

        transaction = await solend.build_withdraw_transaction(
            usdc_asset, Decimal("1"), USER_ADDRESS
        )

        assert compiled_data(transaction) == [
            bytes([3]),
            bytes([3]),
            bytes([7]),
            bytes([15]) + (800_000).to_bytes(8, "little"),
        ]
        # Deposit reserve first, then the borrow reserve (obligation order)
        assert compiled_accounts(transaction, 0) == [
            Pubkey.from_string(usdc_reserve.address),
            Pubkey.from_string(usdc_reserve.pyth_oracle),
            Pubkey.from_string(usdc_reserve.switchboard_oracle),
        ]
        assert compiled_accounts(transaction, 1) == [
            Pubkey.from_string(wsol_reserve.address),
            Pubkey.from_string(wsol_reserve.pyth_oracle),
            Pubkey.from_string(wsol_reserve.switchboard_oracle),
        ]
        assert compiled_accounts(transaction, 2) == [
            Pubkey.from_string(USER_OBLIGATION),
            Pubkey.from_string(usdc_reserve.address),
            Pubkey.from_string(wsol_reserve.address),
        ]

    async def test_borrow_instruction_sequence(
        self,
        solend,
        sol_asset,
        usdc_reserve,
        wsol_reserve,
        obligation_rpc_client,
        monkeypatch,
    ):
        monkeypatch.setattr(solend.blockchain, "rpc_client", obligation_rpc_client)

        transaction = await solend.build_borrow_transaction(
            sol_asset, Decimal("0.5"), InterestRateMode.VARIABLE, USER_ADDRESS
        )

        assert compiled_data(transaction) == [
            bytes([3]),
            bytes([3]),
            bytes([7]),
            bytes([10]) + (500_000_000).to_bytes(8, "little"),
        ]
        assert compiled_accounts(transaction, 0)[0] == Pubkey.from_string(
            usdc_reserve.address
        )
        assert compiled_accounts(transaction, 1)[0] == Pubkey.from_string(
            wsol_reserve.address
        )
        assert compiled_accounts(transaction, 2) == [
            Pubkey.from_string(USER_OBLIGATION),
            Pubkey.from_string(usdc_reserve.address),
            Pubkey.from_string(wsol_reserve.address),
        ]

    async def test_repay_instruction_sequence(
        self, solend, usdc_asset, usdc_reserve, blockhash, monkeypatch
    ):
        """Repay only needs the repay reserve refreshed."""
        client = make_rpc_client(blockhash=blockhash)
        monkeypatch.setattr(solend.blockchain, "rpc_client", client)

        transaction = await solend.build_repay_transaction(
            usdc_asset, Decimal("1"), InterestRateMode.VARIABLE, USER_ADDRESS
        )

        assert compiled_data(transaction) == [
            bytes([3]),
            bytes([11]) + (1_000_000).to_bytes(8, "little"),
        ]
        assert compiled_accounts(transaction, 0) == [
            Pubkey.from_string(usdc_reserve.address),
            Pubkey.from_string(usdc_reserve.pyth_oracle),
            Pubkey.from_string(usdc_reserve.switchboard_oracle),
        ]

    async def test_withdraw_refreshes_a_reserve_outside_the_obligation(
        self,
        solend,
        sol_asset,
        usdc_reserve,
        wsol_reserve,
        obligation_rpc_client,
        monkeypatch,
    ):
        """The target reserve is refreshed even when only borrowed, not deposited."""
        monkeypatch.setattr(solend.blockchain, "rpc_client", obligation_rpc_client)

        transaction = await solend.build_withdraw_transaction(
            sol_asset, Decimal("0.5"), USER_ADDRESS
        )

        # wSOL is already an obligation (borrow) reserve, so it is refreshed once
        assert compiled_data(transaction)[:3] == [bytes([3]), bytes([3]), bytes([7])]
        assert [compiled_accounts(transaction, i)[0] for i in (0, 1)] == [
            Pubkey.from_string(usdc_reserve.address),
            Pubkey.from_string(wsol_reserve.address),
        ]

    async def test_zero_amount_raises(self, solend, usdc_asset):
        with pytest.raises(ValueError, match="must be positive"):
            await solend.build_supply_transaction(
                usdc_asset, Decimal("0"), USER_ADDRESS
            )

    async def test_unconfigured_mint_raises(self, solend, test_platform):
        mint = SolanaAddress.from_raw(Pubkey.new_unique())
        unknown_asset = SPLToken(
            platform=test_platform,
            identifier=mint,
            data=SolanaAssetData(name="X", symbol="X", decimals=6),
            mint=mint,
        )
        with pytest.raises(ValueError, match="No Solend reserve configured"):
            await solend.build_supply_transaction(
                unknown_asset, Decimal("1"), USER_ADDRESS
            )

    async def test_collateral_toggle_unsupported(self, solend, usdc_asset):
        with pytest.raises(NotImplementedError, match="no collateral toggle"):
            await solend.build_collateral_transaction(
                usdc_asset, CollateralMode.ENABLED, USER_ADDRESS
            )

    async def test_liquidation_not_implemented(self, solend, usdc_asset, sol_asset):
        with pytest.raises(NotImplementedError, match="liquidation"):
            await solend.build_liquidation_transaction(
                sol_asset, usdc_asset, USER_ADDRESS, Decimal("100")
            )


# === Reads ===


class TestMarketData:
    """Test market data computed from the parsed reserve account."""

    async def test_get_market_data(
        self, solend, usdc_asset, usdc_reserve, usdc_reserve_bytes, monkeypatch
    ):
        client = make_rpc_client(
            accounts={Pubkey.from_string(usdc_reserve.address): usdc_reserve_bytes},
        )
        monkeypatch.setattr(solend.blockchain, "rpc_client", client)

        market_data = await solend.get_market_data(usdc_asset)

        assert market_data.asset is usdc_asset
        assert market_data.protocol == "Solend"
        assert market_data.supply_apy == Decimal("0.02")
        assert market_data.variable_borrow_apy == Decimal("0.05")
        assert market_data.stable_borrow_apy == Decimal("0")
        # 500e9 raw available + 500e9 raw borrowed at 6 decimals
        assert market_data.total_supply == Decimal("1000000")
        assert market_data.total_borrows == Decimal("500000")
        assert market_data.utilization_rate == Decimal("0.5")
        assert market_data.liquidity_rate == Decimal("0.02")
        assert market_data.liquidation_threshold == Decimal("0.8")
        assert market_data.loan_to_value == Decimal("0.75")
        assert market_data.reserve_factor == Decimal("0.2")
        assert market_data.is_borrowing_enabled is True
        assert market_data.is_stable_rate_enabled is False
        assert market_data.is_frozen is False

    async def test_missing_reserve_account_raises(
        self, solend, usdc_asset, monkeypatch
    ):
        client = make_rpc_client(accounts={})
        monkeypatch.setattr(solend.blockchain, "rpc_client", client)

        with pytest.raises(ValueError, match="not found"):
            await solend.get_market_data(usdc_asset)


class TestUserAccountData:
    """Test user account data computed from the parsed obligation header."""

    def obligation_bytes(self, user, **overrides) -> bytes:
        values = {
            "deposited_value_wads": 10_000 * WAD,
            "borrowed_value_wads": 4_000 * WAD,
            "allowed_borrow_value_wads": 7_500 * WAD,
            "unhealthy_borrow_value_wads": 8_000 * WAD,
        }
        values.update(overrides)
        return build_obligation_bytes(
            owner=user.raw,
            lending_market=Pubkey.from_string(MAIN_POOL_LENDING_MARKET),
            **values,
        )

    async def test_get_user_account_data(self, solend, user, monkeypatch):
        client = make_rpc_client(
            accounts={Pubkey.from_string(USER_OBLIGATION): self.obligation_bytes(user)},
        )
        monkeypatch.setattr(solend.blockchain, "rpc_client", client)

        account_data = await solend.get_user_account_data(USER_ADDRESS)

        assert account_data.protocol == "Solend"
        assert account_data.total_collateral_value == Decimal("10000")
        assert account_data.total_debt_value == Decimal("4000")
        assert account_data.available_borrow_value == Decimal("3500")
        assert account_data.current_liquidation_threshold == Decimal("0.8")
        assert account_data.loan_to_value == Decimal("0.75")
        assert account_data.health_factor == Decimal("2")
        assert account_data.is_healthy

    async def test_no_obligation_reports_no_debt(self, solend, monkeypatch):
        client = make_rpc_client(accounts={})
        monkeypatch.setattr(solend.blockchain, "rpc_client", client)

        account_data = await solend.get_user_account_data(USER_ADDRESS)

        assert account_data.total_collateral_value == Decimal("0")
        assert account_data.total_debt_value == Decimal("0")
        assert account_data.available_borrow_value == Decimal("0")
        assert account_data.health_factor == NO_DEBT_HEALTH_FACTOR

    async def test_debt_free_obligation_reports_no_debt_health(
        self, solend, user, monkeypatch
    ):
        data = self.obligation_bytes(
            user, borrowed_value_wads=0, unhealthy_borrow_value_wads=8_000 * WAD
        )
        client = make_rpc_client(accounts={Pubkey.from_string(USER_OBLIGATION): data})
        monkeypatch.setattr(solend.blockchain, "rpc_client", client)

        account_data = await solend.get_user_account_data(USER_ADDRESS)
        assert account_data.health_factor == NO_DEBT_HEALTH_FACTOR
        assert account_data.available_borrow_value == Decimal("7500")


class TestPositions:
    """Test lending/borrowing positions derived from the obligation entries."""

    @pytest.fixture
    def rpc_client(
        self,
        obligation_bytes,
        usdc_reserve,
        usdc_reserve_bytes,
        wsol_reserve,
        wsol_reserve_bytes,
    ):
        return make_rpc_client(
            accounts={
                Pubkey.from_string(USER_OBLIGATION): obligation_bytes,
                Pubkey.from_string(usdc_reserve.address): usdc_reserve_bytes,
                Pubkey.from_string(wsol_reserve.address): wsol_reserve_bytes,
            },
        )

    async def test_lending_positions(self, solend, rpc_client, monkeypatch):
        monkeypatch.setattr(solend.blockchain, "rpc_client", rpc_client)

        positions = await solend.get_lending_positions(USER_ADDRESS)

        assert len(positions) == 1
        position = positions[0]
        # 2_000_000 cTokens at exchange rate 0.8 -> 2_500_000 raw -> 2.5 USDC
        assert position.supplied_amount == Decimal("2.5")
        assert position.accrued_interest == Decimal("0")
        assert position.apy == Decimal("0.02")
        assert position.is_collateral is True
        assert position.protocol == "Solend"

        asset = position.asset
        assert isinstance(asset, SPLToken)
        assert asset.mint.string == USDC_MINT
        assert asset.data is not None
        assert asset.data.symbol == "USDC"
        # Decimals come from the on-chain reserve state
        assert asset.data.decimals == 6

    async def test_borrowing_positions(self, solend, rpc_client, monkeypatch):
        monkeypatch.setattr(solend.blockchain, "rpc_client", rpc_client)

        positions = await solend.get_borrowing_positions(USER_ADDRESS)

        assert len(positions) == 1
        position = positions[0]
        # Stored debt: 4e9 raw wads at 9 decimals -> 4 SOL
        assert position.borrowed_amount == Decimal("4")
        # Reserve cumulative rate 1.25 vs entry rate 1.0 -> debt 5, accrued 1
        assert position.accrued_interest == Decimal("1")
        assert position.total_debt == Decimal("5")
        assert position.interest_rate_mode == InterestRateMode.VARIABLE
        # wSOL reserve utilization 0.75 -> (0.75/0.8)*0.08
        assert position.current_rate == Decimal("0.075")
        assert position.protocol == "Solend"

        asset = position.asset
        assert isinstance(asset, SPLToken)
        assert asset.mint.string == WSOL_MINT
        assert asset.data is not None
        assert asset.data.decimals == 9

    async def test_no_obligation_returns_empty(self, solend, monkeypatch):
        client = make_rpc_client(accounts={})
        monkeypatch.setattr(solend.blockchain, "rpc_client", client)

        assert await solend.get_lending_positions(USER_ADDRESS) == []
        assert await solend.get_borrowing_positions(USER_ADDRESS) == []

    async def test_unconfigured_reserve_in_obligation_raises(
        self, solend, user, monkeypatch
    ):
        data = build_obligation_bytes(
            owner=user.raw,
            lending_market=Pubkey.from_string(MAIN_POOL_LENDING_MARKET),
            deposited_value_wads=WAD,
            borrowed_value_wads=0,
            allowed_borrow_value_wads=0,
            unhealthy_borrow_value_wads=0,
            deposits=[(Pubkey.new_unique(), 1, WAD)],
        )
        client = make_rpc_client(accounts={Pubkey.from_string(USER_OBLIGATION): data})
        monkeypatch.setattr(solend.blockchain, "rpc_client", client)

        with pytest.raises(ValueError, match="No Solend reserve configured"):
            await solend.get_lending_positions(USER_ADDRESS)


# === Configuration ===


class TestConfiguration:
    """Test SolendConfiguration / SolendReserveConfiguration validation."""

    def test_valid_configuration(self, solend_configuration):
        assert solend_configuration.lending_market == MAIN_POOL_LENDING_MARKET
        assert len(solend_configuration.reserves) == 2

    def test_requires_solend_protocol(self, test_platform):
        other = ProtocolConfiguration(
            protocol_name="Mango",
            lending_pool_address=SOLEND_PROGRAM,
            data_provider_address=SOLEND_PROGRAM,
        )
        with pytest.raises(ValueError, match="requires at least one Solend protocol"):
            SolendConfiguration(
                platform=test_platform,
                protocols=[other],
                lending_market=MAIN_POOL_LENDING_MARKET,
            )

    def test_invalid_lending_market_rejected(self, test_platform, solend_protocol):
        with pytest.raises(ValidationError, match="Invalid Solana public key"):
            SolendConfiguration(
                platform=test_platform,
                protocols=[solend_protocol],
                lending_market="not-a-pubkey",
            )

    def test_invalid_reserve_pubkey_rejected(self):
        with pytest.raises(ValidationError, match="Invalid Solana public key"):
            SolendReserveConfiguration(
                address="nope",
                liquidity_mint=USDC_MINT,
                liquidity_supply=str(det_pubkey(2)),
                liquidity_fee_receiver=str(det_pubkey(3)),
                collateral_mint=str(det_pubkey(4)),
                collateral_supply=str(det_pubkey(5)),
                pyth_oracle=str(det_pubkey(6)),
                switchboard_oracle=str(det_pubkey(7)),
            )

    def test_solana_money_market_configuration(self, test_platform, solend_protocol):
        config = SolanaMoneyMarketConfiguration(
            platform=test_platform, protocols=[solend_protocol]
        )
        assert config.protocols == [solend_protocol]


# === Facade dispatch ===


class TestProtocolConformance:
    """Solend fulfils the runtime-checkable money-market protocol."""

    def test_strategy_conforms(self, solend):
        assert isinstance(solend, ProtocolImplementation)

    def test_registered_strategies_conform(self, money_market):
        strategies = list(money_market._protocol_strategies.values())
        assert strategies
        for strategy in strategies:
            assert isinstance(strategy, ProtocolImplementation)


class TestFacadeDispatch:
    """Test dispatch through the MoneyMarket base class."""

    def test_initialization(self, money_market, solend_configuration):
        assert money_market.configuration is solend_configuration
        assert money_market.supported_protocols == ["Solend"]
        assert isinstance(money_market._protocol_strategies["Solend"], Solend)
        assert isinstance(money_market.blockchain, SolanaBlockchain)

    def test_solend_program_wiring(self, solend):
        assert isinstance(solend.program, SolendProgram)
        assert solend.program.address.string == SOLEND_PROGRAM
        assert solend.lending_market.string == MAIN_POOL_LENDING_MARKET

    async def test_supply_dispatches_to_solend(
        self, money_market, usdc_asset, supply_ready_rpc_client, monkeypatch
    ):
        monkeypatch.setattr(
            money_market.blockchain, "rpc_client", supply_ready_rpc_client
        )

        transaction = await money_market.supply(
            usdc_asset, Decimal("1.5"), USER_ADDRESS
        )

        assert isinstance(transaction, SolanaTransaction)
        raw = transaction.raw_transaction
        assert raw is not None
        compiled = raw.message.instructions[0]
        assert compiled.data == bytes([14]) + (1_500_000).to_bytes(8, "little")

    async def test_borrow_dispatches_with_default_rate_mode(
        self, money_market, sol_asset, obligation_rpc_client, monkeypatch
    ):
        monkeypatch.setattr(
            money_market.blockchain, "rpc_client", obligation_rpc_client
        )

        transaction = await money_market.borrow(sol_asset, Decimal("2"), USER_ADDRESS)

        assert compiled_data(transaction)[-1] == bytes([10]) + (2_000_000_000).to_bytes(
            8, "little"
        )

    async def test_withdraw_all_dispatches_to_solend(
        self, money_market, usdc_asset, obligation_rpc_client, monkeypatch
    ):
        """The facade's withdraw_all reaches the Solend strategy."""
        monkeypatch.setattr(
            money_market.blockchain, "rpc_client", obligation_rpc_client
        )

        transaction = await money_market.withdraw(
            usdc_asset, Decimal("0"), USER_ADDRESS, withdraw_all=True
        )

        assert compiled_data(transaction)[-1] == bytes([15]) + (2_000_000).to_bytes(
            8, "little"
        )

    async def test_unknown_protocol_raises(self, money_market, usdc_asset):
        with pytest.raises(ValueError, match="Unsupported protocol"):
            await money_market.supply(
                usdc_asset, Decimal("1"), USER_ADDRESS, protocol="unknown"
            )

    async def test_set_collateral_mode_unsupported(self, money_market, usdc_asset):
        with pytest.raises(NotImplementedError, match="no collateral toggle"):
            await money_market.set_collateral_mode(
                usdc_asset, CollateralMode.DISABLED, USER_ADDRESS
            )

    async def test_is_position_safe(self, money_market, user, monkeypatch):
        data = build_obligation_bytes(
            owner=user.raw,
            lending_market=Pubkey.from_string(MAIN_POOL_LENDING_MARKET),
            deposited_value_wads=10_000 * WAD,
            borrowed_value_wads=4_000 * WAD,
            allowed_borrow_value_wads=7_500 * WAD,
            unhealthy_borrow_value_wads=8_000 * WAD,
        )
        client = make_rpc_client(accounts={Pubkey.from_string(USER_OBLIGATION): data})
        monkeypatch.setattr(money_market.blockchain, "rpc_client", client)

        # Health factor 2.0 >= 1 + default 5% buffer
        assert await money_market.is_position_safe(USER_ADDRESS) is True

    async def test_is_position_safe_below_buffer(self, money_market, user, monkeypatch):
        data = build_obligation_bytes(
            owner=user.raw,
            lending_market=Pubkey.from_string(MAIN_POOL_LENDING_MARKET),
            deposited_value_wads=10_000 * WAD,
            borrowed_value_wads=4_000 * WAD,
            allowed_borrow_value_wads=7_500 * WAD,
            unhealthy_borrow_value_wads=4_100 * WAD,  # health factor 1.025
        )
        client = make_rpc_client(accounts={Pubkey.from_string(USER_OBLIGATION): data})
        monkeypatch.setattr(money_market.blockchain, "rpc_client", client)

        assert await money_market.is_position_safe(USER_ADDRESS) is False


# === Wallet binding and placement ===


class TestWalletBinding:
    """Test the set_wallet / ValueError-when-unbound contract."""

    def test_wallet_unbound_raises(self, solend):
        with pytest.raises(ValueError, match="No wallet is bound"):
            _ = solend.wallet

    def test_set_wallet_rejects_non_solana_wallets(self, solend):
        with pytest.raises(ValueError, match="requires a SolanaWallet"):
            solend.set_wallet(object())  # type: ignore[arg-type]

    def test_place_transaction_requires_wallet(self, solend, test_platform, user):
        transaction = SolanaTransaction(
            client_operation_id="op-1",
            owner_identifier=SolanaWalletIdentifier(
                platform=test_platform, name=USER_ADDRESS, address=user
            ),
            creation_timestamp=FIXED_TIMESTAMP,
        )
        with pytest.raises(ValueError, match="No wallet is bound"):
            solend.place_transaction(transaction)

    def test_facade_set_wallet_forwards_and_unbinds(
        self, money_market, solend, test_platform, monkeypatch
    ):
        keypair = Keypair()
        address = SolanaAddress.from_raw(keypair.pubkey())
        client = make_rpc_client()
        monkeypatch.setattr(money_market.blockchain, "rpc_client", client)
        wallet = SolanaWallet(
            SolanaWalletConfiguration(
                identifier=SolanaWalletIdentifier(
                    platform=test_platform, name="test", address=address
                ),
                signer=SolanaSignerConfiguration(private_key=SecretStr(str(keypair))),
            ),
            blockchain=money_market.blockchain,
        )

        money_market.set_wallet(wallet)
        assert solend.wallet is wallet

        money_market.set_wallet(None)
        with pytest.raises(ValueError, match="No wallet is bound"):
            _ = solend.wallet


class TestPlaceTransaction:
    """Test signing and broadcasting of built transactions via the wallet."""

    @pytest.fixture
    def keypair(self):
        return Keypair()

    @pytest.fixture
    def wallet_address(self, keypair):
        return SolanaAddress.from_raw(keypair.pubkey())

    @pytest.fixture
    def signature(self):
        return Signature.default()

    @pytest.fixture
    def rpc_client(self, blockhash, signature):
        return make_rpc_client(blockhash=blockhash, send_signature=signature)

    @pytest.fixture
    def wallet(
        self, keypair, wallet_address, test_platform, solend, rpc_client, monkeypatch
    ):
        monkeypatch.setattr(solend.blockchain, "rpc_client", rpc_client)
        return SolanaWallet(
            SolanaWalletConfiguration(
                identifier=SolanaWalletIdentifier(
                    platform=test_platform, name="solend-test", address=wallet_address
                ),
                signer=SolanaSignerConfiguration(private_key=SecretStr(str(keypair))),
            ),
            blockchain=solend.blockchain,
        )

    async def test_place_transaction_signs_and_broadcasts(
        self, solend, usdc_asset, wallet, wallet_address, keypair, rpc_client
    ):
        solend.set_wallet(wallet)

        built = await solend.build_supply_transaction(
            usdc_asset, Decimal("1"), wallet_address.string
        )
        assert not built.is_signed

        placed = solend.place_transaction(built)

        assert isinstance(placed, SolanaTransaction)
        assert placed.client_operation_id == built.client_operation_id
        assert placed.is_signed
        assert placed.signed_transaction is not None
        # The fee payer's signature slot is filled by the wallet's keypair
        assert placed.signed_transaction.signatures[0] != Signature.default()
        assert placed.signed_transaction.message.account_keys[0] == keypair.pubkey()

        # Let the fire-and-forget broadcast task run to completion
        for _ in range(10):
            await asyncio.sleep(0)
        rpc_client.send_transaction.assert_awaited_once()
        assert placed.current_state == BlockchainTransactionState.BROADCASTED

    async def test_place_transaction_rejects_foreign_fee_payer(
        self, solend, usdc_asset, wallet
    ):
        solend.set_wallet(wallet)

        # Built for a different user than the bound wallet
        built = await solend.build_supply_transaction(
            usdc_asset, Decimal("1"), USER_ADDRESS
        )
        with pytest.raises(ValueError, match="not the transaction fee payer"):
            solend.place_transaction(built)

    async def test_place_transaction_requires_raw_transaction(
        self, solend, wallet, wallet_address, test_platform
    ):
        solend.set_wallet(wallet)
        transaction = SolanaTransaction(
            client_operation_id="op-2",
            owner_identifier=SolanaWalletIdentifier(
                platform=test_platform,
                name=wallet_address.string,
                address=wallet_address,
            ),
            creation_timestamp=FIXED_TIMESTAMP,
        )
        with pytest.raises(ValueError, match="no raw transaction"):
            solend.place_transaction(transaction)


# === IDL document ===


class TestSolendIDLDocument:
    """The simplified IDL must stay truthful to the implemented encoding."""

    async def test_idl_matches_implemented_discriminants(self):
        idl = await SolanaLocalFileIDL(file_name="solend.json").get_idl()
        instructions = idl["instructions"]

        # The names the Solend strategy builds against must all resolve
        for name in ("deposit", "withdraw", "borrow", "repay"):
            assert name in instructions

        assert instructions["withdraw"]["discriminant"] == int(
            SolendInstruction.WITHDRAW_OBLIGATION_COLLATERAL_AND_REDEEM_RESERVE_COLLATERAL
        )
        assert instructions["borrow"]["discriminant"] == int(
            SolendInstruction.BORROW_OBLIGATION_LIQUIDITY
        )
        assert instructions["repay"]["discriminant"] == int(
            SolendInstruction.REPAY_OBLIGATION_LIQUIDITY
        )

        # Account counts of the documented instruction layouts
        assert len(instructions["withdraw"]["accounts"]) == 12
        assert len(instructions["borrow"]["accounts"]) == 9
        assert len(instructions["repay"]["accounts"]) == 7

        # The registry keys stay frozen: instructions built directly against
        # the program id are documented in a sibling section instead.
        assert "init_obligation" not in instructions

    async def test_idl_documents_the_directly_built_instructions(self):
        idl = await SolanaLocalFileIDL(file_name="solend.json").get_idl()
        direct = idl["directInstructions"]

        assert direct["init_obligation"]["discriminant"] == int(
            SolendInstruction.INIT_OBLIGATION
        )
        assert direct["refresh_reserve"]["discriminant"] == int(
            SolendInstruction.REFRESH_RESERVE
        )
        assert direct["refresh_obligation"]["discriminant"] == int(
            SolendInstruction.REFRESH_OBLIGATION
        )

    async def test_idl_init_obligation_accounts_match_the_builder(self, solend, user):
        """The documented InitObligation layout is the one actually built."""
        idl = await SolanaLocalFileIDL(file_name="solend.json").get_idl()
        documented = idl["directInstructions"]["init_obligation"]["accounts"]

        instruction = solend.build_init_obligation_instruction(user)

        assert [a["name"] for a in documented] == [
            "obligation",
            "lendingMarket",
            "obligationOwner",
            "clockSysvar",
            "rentSysvar",
            "tokenProgram",
        ]
        assert [(a["isSigner"], a["isMut"]) for a in documented] == [
            (meta.is_signer, meta.is_writable) for meta in instruction.accounts
        ]

    async def test_idl_refresh_reserve_accounts_match_the_builder(
        self, solend, usdc_reserve
    ):
        idl = await SolanaLocalFileIDL(file_name="solend.json").get_idl()
        documented = idl["directInstructions"]["refresh_reserve"]["accounts"]

        instruction = solend.build_refresh_reserve_instruction(usdc_reserve)

        assert [a["name"] for a in documented] == [
            "reserve",
            "pythOracle",
            "switchboardOracle",
        ]
        assert [(a["isSigner"], a["isMut"]) for a in documented] == [
            (meta.is_signer, meta.is_writable) for meta in instruction.accounts
        ]
