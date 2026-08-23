"""
Solend protocol implementation for money market operations on Solana.

Solend (program ``So1endDq2YkqhipRh3WViPa8hdiSpxWy6z3Z6tMCpAo``) is a fork of
the SPL token-lending program and is NOT an Anchor program: every instruction
is encoded as a single-byte instruction discriminant (the instruction enum
index) followed by little-endian fields. This module implements:

- Real instruction building for the four core obligation flows
  (deposit-and-collateralize, withdraw-and-redeem, borrow, repay) with the
  exact account layouts of the on-chain program
- The ``RefreshReserve`` / ``RefreshObligation`` instructions the program
  requires in the same transaction as withdraw/borrow/repay (see
  :meth:`Solend.build_refresh_reserve_instruction` and
  :meth:`Solend.build_refresh_obligation_instruction`)
- The one-time account creation a first-time supplier needs: the
  ``SystemProgram::CreateAccountWithSeed`` + ``InitObligation`` pair that
  brings the derived obligation account into existence, and the idempotent
  associated-token-account creation of the user's collateral (cToken)
  account (see :meth:`Solend.build_supply_prerequisite_instructions`)
- Binary parsers for the on-chain ``Reserve`` and ``Obligation`` account
  layouts (documented field offsets below)
- Market data, account data, and position reads derived from those accounts

All ``build_*`` methods are build-only: they return an UNSIGNED
:class:`~blockchainpype.solana.transaction.SolanaTransaction` carrying the raw
legacy message, and never sign or broadcast. A wallet is only needed by the
:meth:`Solend.place_transaction` execution helper.

On-chain fixed-point conventions:

- ``wad`` values are scaled by 1e18 (``WAD``)
- interest-rate config fields (``min/optimal/max_borrow_rate``,
  ``optimal_utilization_rate``, LTV, liquidation threshold) are integer
  percentages (0-100)
"""

import uuid
from collections.abc import Sequence
from decimal import Decimal
from enum import IntEnum
from typing import TYPE_CHECKING, Self, cast

from financepype.assets.blockchain import BlockchainAsset
from financepype.owners.wallet import BlockchainWallet
from financepype.platforms.platform import Platform
from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from solders import sysvar
from solders.instruction import AccountMeta, Instruction
from solders.message import Message
from solders.pubkey import Pubkey
from solders.system_program import CreateAccountWithSeedParams, create_account_with_seed
from solders.transaction import Transaction
from spl.token.constants import TOKEN_PROGRAM_ID
from spl.token.instructions import create_idempotent_associated_token_account

from blockchainpype.dapps.money_market import (
    BorrowingPosition,
    CollateralMode,
    InterestRateMode,
    LendingPosition,
    MarketData,
    ProtocolConfiguration,
    UserAccountData,
)
from blockchainpype.solana.asset import SolanaAsset, SolanaAssetData
from blockchainpype.solana.blockchain.blockchain import SolanaBlockchain
from blockchainpype.solana.blockchain.identifier import SolanaAddress
from blockchainpype.solana.dapp.idl import SolanaLocalFileIDL
from blockchainpype.solana.dapp.program import SolanaProgram, SolanaProgramConfiguration
from blockchainpype.solana.dapp.token import SPLToken
from blockchainpype.solana.transaction import SolanaRawTransaction, SolanaTransaction
from blockchainpype.solana.wallet.identifier import SolanaWalletIdentifier

from .money_market import SolanaMoneyMarket, SolanaMoneyMarketConfiguration

if TYPE_CHECKING:
    from blockchainpype.solana.wallet.wallet import SolanaWallet

# Solend's deployed lending program id (mainnet).
SOLEND_PROGRAM_ID = "So1endDq2YkqhipRh3WViPa8hdiSpxWy6z3Z6tMCpAo"

# Fixed-point scale used by all wad-encoded on-chain values.
WAD = 10**18

# Sentinel for u64::MAX, understood on-chain as "the full amount".
U64_MAX = 2**64 - 1

# Reported health factor when an account has no outstanding debt. The on-chain
# value would be infinite; pydantic Decimal fields reject non-finite values so
# a documented large sentinel is used instead.
NO_DEBT_HEALTH_FACTOR = Decimal(10**6)

# On-chain account sizes (bytes) of the solend-program state structs.
RESERVE_ACCOUNT_SIZE = 619
OBLIGATION_ACCOUNT_SIZE = 1300
OBLIGATION_COLLATERAL_SIZE = 88
OBLIGATION_LIQUIDITY_SIZE = 112

# User obligation addresses for a lending market are conventionally derived
# with Pubkey.create_with_seed(owner, seed, program) where the seed is the
# first 32 characters of the lending market's base58 address (the solend-sdk
# convention for the per-market obligation account).
OBLIGATION_SEED_LENGTH = 32


class SolendInstruction(IntEnum):
    """Single-byte instruction discriminants of the solend-program enum.

    Values are the enum indices from solend-program
    ``token-lending/program/src/instruction.rs``.
    """

    REFRESH_RESERVE = 3
    INIT_OBLIGATION = 6
    REFRESH_OBLIGATION = 7
    BORROW_OBLIGATION_LIQUIDITY = 10
    REPAY_OBLIGATION_LIQUIDITY = 11
    DEPOSIT_RESERVE_LIQUIDITY_AND_OBLIGATION_COLLATERAL = 14
    WITHDRAW_OBLIGATION_COLLATERAL_AND_REDEEM_RESERVE_COLLATERAL = 15


def _u8(data: bytes, offset: int) -> int:
    """Read an unsigned byte at ``offset``."""
    return data[offset]


def _u64(data: bytes, offset: int) -> int:
    """Read a little-endian u64 at ``offset``."""
    return int.from_bytes(data[offset : offset + 8], "little")


def _u128(data: bytes, offset: int) -> int:
    """Read a little-endian u128 at ``offset``."""
    return int.from_bytes(data[offset : offset + 16], "little")


def _pubkey(data: bytes, offset: int) -> Pubkey:
    """Read a 32-byte public key at ``offset``."""
    return Pubkey(data[offset : offset + 32])


class SolendReserveState(BaseModel):
    """Parsed Solend ``Reserve`` account (state struct of solend-program).

    Field offsets of the 619-byte packed layout (`state/reserve.rs`)::

        offset size field
             0    1 version
             1    8 last_update.slot (u64 LE)
             9    1 last_update.stale (bool)
            10   32 lending_market
            42   32 liquidity.mint_pubkey
            74    1 liquidity.mint_decimals
            75   32 liquidity.supply_pubkey
           107   32 liquidity.pyth_oracle_pubkey
           139   32 liquidity.switchboard_oracle_pubkey
           171    8 liquidity.available_amount (u64 LE)
           179   16 liquidity.borrowed_amount_wads (u128 LE, wad)
           195   16 liquidity.cumulative_borrow_rate_wads (u128 LE, wad)
           211   16 liquidity.market_price (u128 LE, wad USD)
           227   32 collateral.mint_pubkey
           259    8 collateral.mint_total_supply (u64 LE)
           267   32 collateral.supply_pubkey
           299    1 config.optimal_utilization_rate (percent)
           300    1 config.loan_to_value_ratio (percent)
           301    1 config.liquidation_bonus (percent)
           302    1 config.liquidation_threshold (percent)
           303    1 config.min_borrow_rate (percent)
           304    1 config.optimal_borrow_rate (percent)
           305    1 config.max_borrow_rate (percent)
           306    8 config.fees.borrow_fee_wad (u64 LE, wad)
           314    8 config.fees.flash_loan_fee_wad (u64 LE, wad)
           322    1 config.fees.host_fee_percentage
           323    8 config.deposit_limit (u64 LE)
           331    8 config.borrow_limit (u64 LE)
           339   32 config.fee_receiver
           371    1 config.protocol_liquidation_fee (Solend extension)
           372    1 config.protocol_take_rate (percent, Solend extension)
           373   16 liquidity.accumulated_protocol_fees_wads (u128 LE, wad,
                    Solend extension)
           389  230 padding

    The three "Solend extension" fields live where the original SPL
    token-lending layout kept zero padding, so parsing them is also correct
    (as zeros) for reserves packed with the original layout.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    version: int
    last_update_slot: int
    lending_market: Pubkey
    liquidity_mint: Pubkey
    liquidity_mint_decimals: int
    liquidity_supply: Pubkey
    pyth_oracle: Pubkey
    switchboard_oracle: Pubkey
    available_amount: int
    borrowed_amount_wads: int
    cumulative_borrow_rate_wads: int
    market_price_wads: int
    collateral_mint: Pubkey
    collateral_mint_total_supply: int
    collateral_supply: Pubkey
    optimal_utilization_rate: int
    loan_to_value_ratio: int
    liquidation_bonus: int
    liquidation_threshold: int
    min_borrow_rate: int
    optimal_borrow_rate: int
    max_borrow_rate: int
    borrow_fee_wad: int
    flash_loan_fee_wad: int
    host_fee_percentage: int
    deposit_limit: int
    borrow_limit: int
    fee_receiver: Pubkey
    protocol_liquidation_fee: int
    protocol_take_rate: int
    accumulated_protocol_fees_wads: int

    @classmethod
    def from_bytes(cls, data: bytes) -> Self:
        """Parse a Reserve account's raw data.

        Args:
            data (bytes): The 619-byte reserve account data

        Returns:
            Self: The parsed reserve state

        Raises:
            ValueError: If the data does not have the Reserve account size
        """
        if len(data) != RESERVE_ACCOUNT_SIZE:
            raise ValueError(
                f"Invalid Solend reserve account size: expected "
                f"{RESERVE_ACCOUNT_SIZE} bytes, got {len(data)}"
            )
        return cls(
            version=_u8(data, 0),
            last_update_slot=_u64(data, 1),
            lending_market=_pubkey(data, 10),
            liquidity_mint=_pubkey(data, 42),
            liquidity_mint_decimals=_u8(data, 74),
            liquidity_supply=_pubkey(data, 75),
            pyth_oracle=_pubkey(data, 107),
            switchboard_oracle=_pubkey(data, 139),
            available_amount=_u64(data, 171),
            borrowed_amount_wads=_u128(data, 179),
            cumulative_borrow_rate_wads=_u128(data, 195),
            market_price_wads=_u128(data, 211),
            collateral_mint=_pubkey(data, 227),
            collateral_mint_total_supply=_u64(data, 259),
            collateral_supply=_pubkey(data, 267),
            optimal_utilization_rate=_u8(data, 299),
            loan_to_value_ratio=_u8(data, 300),
            liquidation_bonus=_u8(data, 301),
            liquidation_threshold=_u8(data, 302),
            min_borrow_rate=_u8(data, 303),
            optimal_borrow_rate=_u8(data, 304),
            max_borrow_rate=_u8(data, 305),
            borrow_fee_wad=_u64(data, 306),
            flash_loan_fee_wad=_u64(data, 314),
            host_fee_percentage=_u8(data, 322),
            deposit_limit=_u64(data, 323),
            borrow_limit=_u64(data, 331),
            fee_receiver=_pubkey(data, 339),
            protocol_liquidation_fee=_u8(data, 371),
            protocol_take_rate=_u8(data, 372),
            accumulated_protocol_fees_wads=_u128(data, 373),
        )

    @property
    def borrowed_amount(self) -> Decimal:
        """Borrowed liquidity in raw token units (wad-unscaled)."""
        return Decimal(self.borrowed_amount_wads) / WAD

    @property
    def market_price(self) -> Decimal:
        """Oracle market price (USD per whole token, wad-unscaled)."""
        return Decimal(self.market_price_wads) / WAD

    @property
    def total_liquidity_wads(self) -> int:
        """Total liquidity supply in wad-scaled raw units.

        Mirrors on-chain ``ReserveLiquidity::total_supply``:
        ``available + borrowed - accumulated_protocol_fees``.
        """
        return (
            self.available_amount * WAD
            + self.borrowed_amount_wads
            - self.accumulated_protocol_fees_wads
        )

    @property
    def utilization_rate(self) -> Decimal:
        """Current utilization: ``borrowed / total_supply`` (0 when empty)."""
        total = self.total_liquidity_wads
        if total <= 0:
            return Decimal(0)
        return Decimal(self.borrowed_amount_wads) / Decimal(total)

    @property
    def borrow_apr(self) -> Decimal:
        """Current borrow rate from the reserve's interest-rate model.

        Mirrors on-chain ``Reserve::current_borrow_rate`` (the documented
        3-point piecewise-linear model):

        - below optimal utilization (or when optimal utilization is 100%):
          ``min + (utilization / optimal) * (optimal_rate - min_rate)``
        - above optimal utilization:
          ``optimal_rate + normalized_excess * (max_rate - optimal_rate)``

        Returned as an annualized rate (e.g. ``Decimal("0.08")`` for 8% APR),
        without slot-compounding to an APY.
        """
        utilization = self.utilization_rate
        optimal_utilization = Decimal(self.optimal_utilization_rate) / 100
        min_rate = Decimal(self.min_borrow_rate) / 100
        optimal_rate = Decimal(self.optimal_borrow_rate) / 100
        max_rate = Decimal(self.max_borrow_rate) / 100

        if utilization < optimal_utilization or self.optimal_utilization_rate == 100:
            normalized = utilization / optimal_utilization
            return min_rate + normalized * (optimal_rate - min_rate)

        excess_span = Decimal(100 - self.optimal_utilization_rate) / 100
        normalized = (utilization - optimal_utilization) / excess_span
        return optimal_rate + normalized * (max_rate - optimal_rate)

    @property
    def supply_apr(self) -> Decimal:
        """Current supply rate.

        Mirrors on-chain ``Reserve::current_supply_apr``:
        ``borrow_rate * utilization * (1 - protocol_take_rate)``.
        Returned as an annualized rate, without slot-compounding to an APY.
        """
        take_rate = Decimal(self.protocol_take_rate) / 100
        return self.borrow_apr * self.utilization_rate * (1 - take_rate)

    def liquidity_to_collateral(self, raw_liquidity: int) -> int:
        """Convert raw liquidity units to collateral (cToken) units.

        Mirrors the on-chain collateral exchange rate
        ``collateral_supply / total_liquidity`` with floor rounding.

        Args:
            raw_liquidity (int): Amount in raw liquidity token units

        Returns:
            int: The equivalent amount in raw collateral token units

        Raises:
            ValueError: If the reserve holds no liquidity or collateral
        """
        total = self.total_liquidity_wads
        if total <= 0 or self.collateral_mint_total_supply <= 0:
            raise ValueError(
                "Reserve holds no liquidity/collateral: cannot convert amounts"
            )
        return raw_liquidity * WAD * self.collateral_mint_total_supply // total

    def collateral_to_liquidity(self, raw_collateral: int) -> Decimal:
        """Convert collateral (cToken) units to raw liquidity units.

        Inverse of :meth:`liquidity_to_collateral` (exact Decimal result).

        Args:
            raw_collateral (int): Amount in raw collateral token units

        Returns:
            Decimal: The equivalent amount in raw liquidity token units

        Raises:
            ValueError: If the reserve holds no liquidity or collateral
        """
        total = self.total_liquidity_wads
        if total <= 0 or self.collateral_mint_total_supply <= 0:
            raise ValueError(
                "Reserve holds no liquidity/collateral: cannot convert amounts"
            )
        return (
            Decimal(raw_collateral)
            * Decimal(total)
            / Decimal(WAD)
            / Decimal(self.collateral_mint_total_supply)
        )


class SolendObligationCollateral(BaseModel):
    """One deposit entry of an Obligation (``ObligationCollateral``).

    Packed entry layout (88 bytes)::

        offset size field
             0   32 deposit_reserve
            32    8 deposited_amount (u64 LE, collateral/cToken units)
            40   16 market_value (u128 LE, wad USD)
            56   32 padding
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    deposit_reserve: Pubkey
    deposited_amount: int
    market_value_wads: int

    @classmethod
    def from_bytes(cls, data: bytes) -> Self:
        """Parse one 88-byte ObligationCollateral entry."""
        if len(data) != OBLIGATION_COLLATERAL_SIZE:
            raise ValueError(
                f"Invalid ObligationCollateral size: expected "
                f"{OBLIGATION_COLLATERAL_SIZE} bytes, got {len(data)}"
            )
        return cls(
            deposit_reserve=_pubkey(data, 0),
            deposited_amount=_u64(data, 32),
            market_value_wads=_u128(data, 40),
        )


class SolendObligationLiquidity(BaseModel):
    """One borrow entry of an Obligation (``ObligationLiquidity``).

    Packed entry layout (112 bytes)::

        offset size field
             0   32 borrow_reserve
            32   16 cumulative_borrow_rate_wads (u128 LE, wad)
            48   16 borrowed_amount_wads (u128 LE, wad raw liquidity units)
            64   16 market_value (u128 LE, wad USD)
            80   32 padding
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    borrow_reserve: Pubkey
    cumulative_borrow_rate_wads: int
    borrowed_amount_wads: int
    market_value_wads: int

    @classmethod
    def from_bytes(cls, data: bytes) -> Self:
        """Parse one 112-byte ObligationLiquidity entry."""
        if len(data) != OBLIGATION_LIQUIDITY_SIZE:
            raise ValueError(
                f"Invalid ObligationLiquidity size: expected "
                f"{OBLIGATION_LIQUIDITY_SIZE} bytes, got {len(data)}"
            )
        return cls(
            borrow_reserve=_pubkey(data, 0),
            cumulative_borrow_rate_wads=_u128(data, 32),
            borrowed_amount_wads=_u128(data, 48),
            market_value_wads=_u128(data, 64),
        )


class SolendObligationState(BaseModel):
    """Parsed Solend ``Obligation`` account (state struct of solend-program).

    Field offsets of the 1300-byte packed layout (`state/obligation.rs`)::

        offset size field
             0    1 version
             1    8 last_update.slot (u64 LE)
             9    1 last_update.stale (bool)
            10   32 lending_market
            42   32 owner
            74   16 deposited_value (u128 LE, wad USD)
            90   16 borrowed_value (u128 LE, wad USD)
           106   16 allowed_borrow_value (u128 LE, wad USD)
           122   16 unhealthy_borrow_value (u128 LE, wad USD)
           138   64 padding
           202    1 deposits_len
           203    1 borrows_len
           204 1096 data_flat: deposits_len ObligationCollateral entries
                    (88 bytes each) immediately followed by borrows_len
                    ObligationLiquidity entries (112 bytes each)
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    version: int
    last_update_slot: int
    lending_market: Pubkey
    owner: Pubkey
    deposited_value_wads: int
    borrowed_value_wads: int
    allowed_borrow_value_wads: int
    unhealthy_borrow_value_wads: int
    deposits: list[SolendObligationCollateral]
    borrows: list[SolendObligationLiquidity]

    @classmethod
    def from_bytes(cls, data: bytes) -> Self:
        """Parse an Obligation account's raw data.

        Args:
            data (bytes): The 1300-byte obligation account data

        Returns:
            Self: The parsed obligation state

        Raises:
            ValueError: If the data does not have the Obligation account size
                or the entry counts overflow the flat data section
        """
        if len(data) != OBLIGATION_ACCOUNT_SIZE:
            raise ValueError(
                f"Invalid Solend obligation account size: expected "
                f"{OBLIGATION_ACCOUNT_SIZE} bytes, got {len(data)}"
            )

        deposits_len = _u8(data, 202)
        borrows_len = _u8(data, 203)
        flat = data[204:]
        needed = (
            deposits_len * OBLIGATION_COLLATERAL_SIZE
            + borrows_len * OBLIGATION_LIQUIDITY_SIZE
        )
        if needed > len(flat):
            raise ValueError(
                f"Obligation entry counts overflow the account data: "
                f"{deposits_len} deposits + {borrows_len} borrows need "
                f"{needed} bytes, only {len(flat)} available"
            )

        deposits: list[SolendObligationCollateral] = []
        offset = 0
        for _ in range(deposits_len):
            deposits.append(
                SolendObligationCollateral.from_bytes(
                    flat[offset : offset + OBLIGATION_COLLATERAL_SIZE]
                )
            )
            offset += OBLIGATION_COLLATERAL_SIZE

        borrows: list[SolendObligationLiquidity] = []
        for _ in range(borrows_len):
            borrows.append(
                SolendObligationLiquidity.from_bytes(
                    flat[offset : offset + OBLIGATION_LIQUIDITY_SIZE]
                )
            )
            offset += OBLIGATION_LIQUIDITY_SIZE

        return cls(
            version=_u8(data, 0),
            last_update_slot=_u64(data, 1),
            lending_market=_pubkey(data, 10),
            owner=_pubkey(data, 42),
            deposited_value_wads=_u128(data, 74),
            borrowed_value_wads=_u128(data, 90),
            allowed_borrow_value_wads=_u128(data, 106),
            unhealthy_borrow_value_wads=_u128(data, 122),
            deposits=deposits,
            borrows=borrows,
        )


class SolendReserveConfiguration(BaseModel):
    """Static account configuration of one Solend reserve.

    These are the reserve-specific accounts required by the lending
    instructions that cannot be derived on the client and must therefore be
    supplied by configuration (they are published per market by Solend).

    Attributes:
        address (str): The reserve state account
        liquidity_mint (str): Mint of the underlying liquidity token
        liquidity_supply (str): Reserve's liquidity supply SPL token account
        liquidity_fee_receiver (str): Reserve's borrow-fee receiver account
        collateral_mint (str): Mint of the reserve's collateral (cToken)
        collateral_supply (str): Reserve's collateral supply SPL token account
        pyth_oracle (str): Pyth price oracle account
        switchboard_oracle (str): Switchboard price feed oracle account
        symbol (str | None): Optional display symbol of the liquidity token
    """

    address: str
    liquidity_mint: str
    liquidity_supply: str
    liquidity_fee_receiver: str
    collateral_mint: str
    collateral_supply: str
    pyth_oracle: str
    switchboard_oracle: str
    symbol: str | None = None

    @field_validator(
        "address",
        "liquidity_mint",
        "liquidity_supply",
        "liquidity_fee_receiver",
        "collateral_mint",
        "collateral_supply",
        "pyth_oracle",
        "switchboard_oracle",
    )
    @classmethod
    def validate_pubkey(cls, value: str) -> str:
        """Validate that the field is a well-formed base58 public key."""
        if not SolanaAddress.is_valid(value):
            raise ValueError(f"Invalid Solana public key: {value}")
        return value


class SolendConfiguration(SolanaMoneyMarketConfiguration):
    """Configuration for the Solend money market.

    Attributes:
        lending_market (str): The lending market account (e.g. the Solend
            main pool ``4UpD2fh7xH3VP9QQaXtsS1YY3bxzWhtfpks7FatyKvdY``)
        reserves (list[SolendReserveConfiguration]): The reserves of the
            lending market this instance can operate on
    """

    lending_market: str
    reserves: list[SolendReserveConfiguration] = []

    @field_validator("lending_market")
    @classmethod
    def validate_lending_market(cls, value: str) -> str:
        """Validate that the lending market is a well-formed public key."""
        if not SolanaAddress.is_valid(value):
            raise ValueError(f"Invalid Solana public key: {value}")
        return value

    @model_validator(mode="after")
    def validate_solend_protocol(self) -> Self:
        """Require at least one 'solend' entry among the protocols."""
        if not any(p.protocol_name.lower() == "solend" for p in self.protocols):
            raise ValueError(
                "SolendConfiguration requires at least one Solend protocol "
                "configuration"
            )
        return self


class SolendProgram(SolanaProgram):
    """Solend program interface.

    Uses the simplified dict-form IDL in ``common/idl/solend.json`` purely as
    an instruction-name registry: Solend is not an Anchor program, so all
    instruction data is pre-encoded by the callers (single-byte discriminant
    plus little-endian fields) and passed through unchanged.
    """

    def __init__(self, address: SolanaAddress, platform: Platform) -> None:
        """
        Initialize the Solend program interface.

        Args:
            address (SolanaAddress): The deployed program's address
            platform (Platform): The Solana platform the program runs on
        """
        config = SolanaProgramConfiguration(
            platform=platform,
            address=address,
            idl_configuration=SolanaLocalFileIDL(file_name="solend.json"),
        )
        super().__init__(config)


class Solend:
    """Solend protocol strategy for the money-market facade.

    Implements the money-market ``ProtocolImplementation`` structural contract
    on Solana. Read paths and all ``build_*`` methods work without a wallet;
    a wallet bound via :meth:`set_wallet` is only needed by
    :meth:`place_transaction` to sign and broadcast a built transaction.

    Notes on protocol semantics:

    - Deposits use ``DepositReserveLiquidityAndObligationCollateral`` so the
      supplied liquidity is always deposited as obligation collateral; the
      ``enable_as_collateral`` flag is therefore ignored (Solend has no
      collateral toggle) and ``build_collateral_transaction`` is unsupported.
    - Account creation: the obligation account and the user's collateral
      (cToken) associated token account are the caller's responsibility on
      Solend, so :meth:`build_supply_transaction` checks both on-chain and
      prepends the missing creations (see
      :meth:`build_supply_prerequisite_instructions`). The withdraw/borrow
      paths deliberately do not create anything: they read the obligation's
      state to build their refresh prefix and therefore require an existing,
      already-funded obligation.
    - Solend only has variable-rate borrows; ``interest_rate_mode`` is
      accepted for contract compatibility and ignored.
    - Staleness: the program rejects an instruction touching a reserve (or an
      obligation) that has not been refreshed in the current slot, so
      :meth:`build_withdraw_transaction`, :meth:`build_borrow_transaction` and
      :meth:`build_repay_transaction` prepend the required
      ``RefreshReserve``/``RefreshObligation`` instructions to the same
      transaction. :meth:`build_supply_transaction` needs none: Solend's
      ``DepositReserveLiquidityAndObligationCollateral`` carries the reserve's
      Pyth and Switchboard oracle accounts in its own account list (see
      ``common/idl/solend.json``) and refreshes the reserve itself, which is
      exactly why the withdraw/borrow/repay layouts do not list oracles.
    """

    def __init__(
        self,
        protocol_config: ProtocolConfiguration,
        blockchain: SolanaBlockchain,
        lending_market: str,
        reserves: Sequence[SolendReserveConfiguration],
        wallet: "SolanaWallet | None" = None,
        platform: Platform | None = None,
    ) -> None:
        """
        Initialize the Solend strategy.

        Args:
            protocol_config (ProtocolConfiguration): Generic protocol entry;
                its ``lending_pool_address`` is the Solend program id
            blockchain (SolanaBlockchain): The blockchain used for RPC reads
            lending_market (str): The lending market account address
            reserves (Sequence[SolendReserveConfiguration]): The reserves this
                strategy can operate on
            wallet (SolanaWallet | None): Optional wallet to bind immediately
            platform (Platform | None): Platform override; defaults to the
                blockchain's platform
        """
        self.protocol_config = protocol_config
        self._blockchain = blockchain
        self._lending_market: SolanaAddress = SolanaAddress.from_string(lending_market)
        self._reserves = list(reserves)
        self._platform: Platform = (
            platform if platform is not None else blockchain.platform
        )
        self._wallet: SolanaWallet | None = None

        self.program = SolendProgram(
            SolanaAddress.from_string(protocol_config.lending_pool_address),
            self._platform,
        )

        if wallet is not None:
            self.set_wallet(wallet)

    # === Wallet binding ===

    def set_wallet(self, wallet: BlockchainWallet | None) -> None:
        """Bind (or unbind, with ``None``) the wallet used to place transactions.

        Args:
            wallet: The wallet to bind, or None to unbind

        Raises:
            ValueError: If the wallet is not a SolanaWallet
        """
        from blockchainpype.solana.wallet.wallet import SolanaWallet

        if wallet is not None and not isinstance(wallet, SolanaWallet):
            raise ValueError(
                f"Solend requires a SolanaWallet, got {type(wallet).__name__}"
            )
        self._wallet = wallet

    @property
    def wallet(self) -> "SolanaWallet":
        """The bound wallet.

        Returns:
            SolanaWallet: The wallet bound via :meth:`set_wallet`

        Raises:
            ValueError: If no wallet is bound
        """
        if self._wallet is None:
            raise ValueError(
                "No wallet is bound to the Solend strategy; call set_wallet() first"
            )
        return self._wallet

    # === Accessors and derivations ===

    @property
    def blockchain(self) -> SolanaBlockchain:
        """The blockchain used for RPC reads."""
        return self._blockchain

    @property
    def lending_market(self) -> SolanaAddress:
        """The lending market account address."""
        return self._lending_market

    @property
    def reserves(self) -> list[SolendReserveConfiguration]:
        """The configured reserves."""
        return list(self._reserves)

    def derive_lending_market_authority(self) -> SolanaAddress:
        """Derive the lending market authority PDA.

        The authority is ``find_program_address([lending_market], program)``,
        i.e. the PDA whose single seed is the lending market address bytes.

        Returns:
            SolanaAddress: The derived lending market authority
        """
        authority, _bump = Pubkey.find_program_address(
            [bytes(self._lending_market.raw)], self.program.address.raw
        )
        authority_address: SolanaAddress = SolanaAddress.from_raw(authority)
        return authority_address

    @property
    def obligation_seed(self) -> str:
        """The deterministic seed of this market's obligation accounts.

        The solend-sdk convention is the first
        :data:`OBLIGATION_SEED_LENGTH` characters of the lending market's
        base58 address. The same string is used both to derive the obligation
        address (:meth:`derive_obligation_address`) and to create the account
        on-chain (:meth:`build_create_obligation_account_instruction`), so the
        two can never drift apart.
        """
        return self._lending_market.string[:OBLIGATION_SEED_LENGTH]

    def derive_obligation_address(self, owner: SolanaAddress) -> SolanaAddress:
        """Derive a user's obligation account address for this lending market.

        Follows the solend-sdk convention:
        ``create_with_seed(owner, lending_market_base58[:32], program)``.

        Args:
            owner (SolanaAddress): The obligation owner's wallet address

        Returns:
            SolanaAddress: The derived obligation account address
        """
        obligation = Pubkey.create_with_seed(
            owner.raw, self.obligation_seed, self.program.address.raw
        )
        obligation_address: SolanaAddress = SolanaAddress.from_raw(obligation)
        return obligation_address

    @staticmethod
    def derive_associated_token_account(
        owner: SolanaAddress, mint: SolanaAddress
    ) -> SolanaAddress:
        """Derive the associated token account for an owner and mint."""
        return SolanaBlockchain.derive_associated_token_account(owner, mint)

    def reserve_configuration_for_asset(
        self, asset: BlockchainAsset
    ) -> SolendReserveConfiguration:
        """Find the configured reserve whose liquidity mint matches an asset.

        Args:
            asset (BlockchainAsset): The asset (its identifier is the mint)

        Returns:
            SolendReserveConfiguration: The matching reserve configuration

        Raises:
            ValueError: If the asset is not a Solana asset or no reserve is
                configured for its mint
        """
        solana_asset = self._as_solana_asset(asset)
        mint = solana_asset.identifier.string
        for reserve in self._reserves:
            if reserve.liquidity_mint == mint:
                return reserve
        raise ValueError(f"No Solend reserve configured for mint {mint}")

    def reserve_configuration_for_address(
        self, reserve_address: Pubkey
    ) -> SolendReserveConfiguration:
        """Find the configured reserve with the given reserve account address.

        Args:
            reserve_address (Pubkey): The reserve state account address

        Returns:
            SolendReserveConfiguration: The matching reserve configuration

        Raises:
            ValueError: If no reserve with that address is configured
        """
        for reserve in self._reserves:
            if reserve.address == str(reserve_address):
                return reserve
        raise ValueError(f"No Solend reserve configured with address {reserve_address}")

    @staticmethod
    def _as_solana_asset(asset: BlockchainAsset) -> SolanaAsset:
        """Narrow a generic asset to a SolanaAsset or raise."""
        if not isinstance(asset, SolanaAsset):
            raise ValueError(
                f"Solend requires Solana assets, got {type(asset).__name__}"
            )
        return asset

    def _asset_for_reserve(
        self, reserve: SolendReserveConfiguration, state: SolendReserveState
    ) -> SPLToken:
        """Build the SPL token asset of a reserve's underlying liquidity.

        Decimals come from the on-chain reserve state; name/symbol come from
        the reserve configuration (falling back to the mint address).
        """
        mint = SolanaAddress.from_string(reserve.liquidity_mint)
        symbol = reserve.symbol if reserve.symbol is not None else mint.string
        return SPLToken(
            platform=self._platform,
            identifier=mint,
            data=SolanaAssetData(
                name=symbol,
                symbol=symbol,
                decimals=state.liquidity_mint_decimals,
            ),
            mint=mint,
        )

    # === Account fetching ===

    async def _ensure_program(self) -> None:
        """Initialize the program's IDL registry once."""
        if not self.program.is_initialized:
            await self.program.initialize()

    async def _fetch_account_data(self, address: SolanaAddress) -> bytes | None:
        """Fetch an account's raw data, or None when it does not exist."""
        response = await self._blockchain.rpc_client.get_account_info(
            address.raw, commitment=self._blockchain.commitment
        )
        account = response.value
        if account is None:
            return None
        return bytes(account.data)

    async def account_exists(self, address: SolanaAddress) -> bool:
        """Check whether an account exists on-chain.

        Uses the same ``getAccountInfo`` read (at the blockchain's commitment)
        the state readers use: the RPC reports a nonexistent account with a
        null value, which :meth:`_fetch_account_data` maps to ``None``.

        Args:
            address (SolanaAddress): The account to probe

        Returns:
            bool: True when the account exists at the current commitment
        """
        return await self._fetch_account_data(address) is not None

    async def fetch_obligation_rent_exemption(self) -> int:
        """Fetch the rent-exempt minimum balance of an obligation account.

        The obligation is a fixed-size (:data:`OBLIGATION_ACCOUNT_SIZE`)
        account, so the lamports it must be funded with are whatever the
        cluster currently charges for that data length. The value is read from
        the node instead of being hard-coded because the rent rate is a
        cluster parameter.

        Returns:
            int: The rent-exempt minimum in lamports
        """
        response = (
            await self._blockchain.rpc_client.get_minimum_balance_for_rent_exemption(
                OBLIGATION_ACCOUNT_SIZE, commitment=self._blockchain.commitment
            )
        )
        return int(response.value)

    async def get_reserve_state(
        self, reserve: SolendReserveConfiguration
    ) -> SolendReserveState:
        """Fetch and parse a reserve's on-chain state.

        Args:
            reserve (SolendReserveConfiguration): The reserve to fetch

        Returns:
            SolendReserveState: The parsed reserve state

        Raises:
            ValueError: If the reserve account does not exist
        """
        data = await self._fetch_account_data(
            SolanaAddress.from_string(reserve.address)
        )
        if data is None:
            raise ValueError(f"Solend reserve account {reserve.address} not found")
        return SolendReserveState.from_bytes(data)

    async def require_obligation_state(
        self, owner: SolanaAddress
    ) -> SolendObligationState:
        """Fetch and parse a user's obligation, raising when it is missing.

        Args:
            owner (SolanaAddress): The obligation owner's wallet address

        Returns:
            SolendObligationState: The parsed obligation state

        Raises:
            ValueError: If the user has no obligation account for this market
        """
        obligation = await self.get_obligation_state(owner)
        if obligation is None:
            raise ValueError(
                f"No Solend obligation account found for {owner.string} in "
                f"lending market {self._lending_market.string}"
            )
        return obligation

    async def get_obligation_state(
        self, owner: SolanaAddress
    ) -> SolendObligationState | None:
        """Fetch and parse a user's obligation, or None when it doesn't exist.

        Args:
            owner (SolanaAddress): The obligation owner's wallet address

        Returns:
            SolendObligationState | None: The parsed obligation state, or None
                when the user has no obligation account for this market
        """
        obligation_address = self.derive_obligation_address(owner)
        data = await self._fetch_account_data(obligation_address)
        if data is None:
            return None
        return SolendObligationState.from_bytes(data)

    # === Reads ===

    async def get_market_data(self, asset: BlockchainAsset) -> MarketData:
        """Get market data for a specific asset from its Solend reserve.

        All figures are computed from the parsed on-chain reserve account:
        supply/borrows/utilization from the liquidity balances, rates from the
        reserve's interest-rate model, and risk parameters from the reserve
        configuration bytes. Rates are annualized APRs (no slot compounding).
        Solend has no stable-rate borrowing, so the stable APY is reported as
        zero and stable rates as disabled.
        """
        reserve = self.reserve_configuration_for_asset(asset)
        state = await self.get_reserve_state(reserve)

        scale = Decimal(10) ** state.liquidity_mint_decimals
        total_borrows = state.borrowed_amount / scale
        total_supply = Decimal(state.total_liquidity_wads) / WAD / scale

        return MarketData(
            asset=asset,
            supply_apy=state.supply_apr,
            variable_borrow_apy=state.borrow_apr,
            stable_borrow_apy=Decimal(0),
            total_supply=total_supply,
            total_borrows=total_borrows,
            utilization_rate=state.utilization_rate,
            liquidity_rate=state.supply_apr,
            liquidation_threshold=Decimal(state.liquidation_threshold) / 100,
            loan_to_value=Decimal(state.loan_to_value_ratio) / 100,
            reserve_factor=Decimal(state.protocol_take_rate) / 100,
            is_borrowing_enabled=state.borrow_limit > 0,
            is_stable_rate_enabled=False,
            is_frozen=False,
            protocol=self.protocol_config.protocol_name,
        )

    async def get_user_account_data(self, user_address: str) -> UserAccountData:
        """Get a user's account data from their obligation header.

        Values come from the obligation's wad-encoded USD aggregates as of its
        last on-chain refresh. A user without an obligation account is
        reported with all-zero balances and the no-debt health factor.
        The health factor is ``unhealthy_borrow_value / borrowed_value``
        (:data:`NO_DEBT_HEALTH_FACTOR` when there is no debt).
        """
        obligation = await self.get_obligation_state(
            SolanaAddress.from_string(user_address)
        )
        if obligation is None:
            return UserAccountData(
                total_collateral_value=Decimal(0),
                total_debt_value=Decimal(0),
                available_borrow_value=Decimal(0),
                current_liquidation_threshold=Decimal(0),
                loan_to_value=Decimal(0),
                health_factor=NO_DEBT_HEALTH_FACTOR,
                protocol=self.protocol_config.protocol_name,
            )

        deposited = Decimal(obligation.deposited_value_wads) / WAD
        borrowed = Decimal(obligation.borrowed_value_wads) / WAD
        allowed = Decimal(obligation.allowed_borrow_value_wads) / WAD
        unhealthy = Decimal(obligation.unhealthy_borrow_value_wads) / WAD

        available_borrow = max(allowed - borrowed, Decimal(0))
        liquidation_threshold = unhealthy / deposited if deposited > 0 else Decimal(0)
        loan_to_value = allowed / deposited if deposited > 0 else Decimal(0)
        health_factor = unhealthy / borrowed if borrowed > 0 else NO_DEBT_HEALTH_FACTOR

        return UserAccountData(
            total_collateral_value=deposited,
            total_debt_value=borrowed,
            available_borrow_value=available_borrow,
            current_liquidation_threshold=liquidation_threshold,
            loan_to_value=loan_to_value,
            health_factor=health_factor,
            protocol=self.protocol_config.protocol_name,
        )

    async def get_lending_positions(self, user_address: str) -> list[LendingPosition]:
        """Get a user's lending positions from their obligation deposits.

        Each ``ObligationCollateral`` entry stores the deposited amount in
        collateral (cToken) units; it is converted to underlying liquidity
        units with the reserve's current collateral exchange rate, so accrued
        deposit interest is included in ``supplied_amount``. Solend tracks no
        principal/interest split for deposits (interest accrues through the
        exchange rate), so ``accrued_interest`` is reported as zero.

        Raises:
            ValueError: If the obligation references a reserve that is not
                present in this strategy's configuration
        """
        obligation = await self.get_obligation_state(
            SolanaAddress.from_string(user_address)
        )
        if obligation is None:
            return []

        positions: list[LendingPosition] = []
        for deposit in obligation.deposits:
            reserve = self.reserve_configuration_for_address(deposit.deposit_reserve)
            state = await self.get_reserve_state(reserve)
            scale = Decimal(10) ** state.liquidity_mint_decimals
            supplied_raw = state.collateral_to_liquidity(deposit.deposited_amount)
            positions.append(
                LendingPosition(
                    asset=self._asset_for_reserve(reserve, state),
                    supplied_amount=supplied_raw / scale,
                    accrued_interest=Decimal(0),
                    apy=state.supply_apr,
                    is_collateral=True,
                    protocol=self.protocol_config.protocol_name,
                )
            )
        return positions

    async def get_borrowing_positions(
        self, user_address: str
    ) -> list[BorrowingPosition]:
        """Get a user's borrowing positions from their obligation borrows.

        ``borrowed_amount`` is the wad-encoded debt as of the obligation's
        last refresh; ``accrued_interest`` is the additional interest
        compounded since then, computed by scaling the stored debt with
        ``reserve.cumulative_borrow_rate / entry.cumulative_borrow_rate``
        (the on-chain ``ObligationLiquidity::accrue_interest`` formula).

        Raises:
            ValueError: If the obligation references a reserve that is not
                present in this strategy's configuration
        """
        obligation = await self.get_obligation_state(
            SolanaAddress.from_string(user_address)
        )
        if obligation is None:
            return []

        positions: list[BorrowingPosition] = []
        for borrow in obligation.borrows:
            reserve = self.reserve_configuration_for_address(borrow.borrow_reserve)
            state = await self.get_reserve_state(reserve)
            scale = Decimal(10) ** state.liquidity_mint_decimals

            borrowed = Decimal(borrow.borrowed_amount_wads) / WAD / scale
            if borrow.cumulative_borrow_rate_wads > 0:
                current_debt = (
                    borrowed
                    * Decimal(state.cumulative_borrow_rate_wads)
                    / Decimal(borrow.cumulative_borrow_rate_wads)
                )
            else:
                current_debt = borrowed
            accrued = max(current_debt - borrowed, Decimal(0))

            positions.append(
                BorrowingPosition(
                    asset=self._asset_for_reserve(reserve, state),
                    borrowed_amount=borrowed,
                    accrued_interest=accrued,
                    interest_rate_mode=InterestRateMode.VARIABLE,
                    current_rate=state.borrow_apr,
                    protocol=self.protocol_config.protocol_name,
                )
            )
        return positions

    # === Instruction building ===

    def _program_instruction(
        self, accounts: list[AccountMeta], data: bytes
    ) -> Instruction:
        """Build a raw instruction for the Solend program.

        The simplified IDL in ``common/idl/solend.json`` is only a name
        registry for the four core obligation instructions, so the refresh and
        ``InitObligation`` instructions - which carry no arguments beyond
        their discriminant - are constructed directly against the program id
        instead of going through :meth:`SolanaProgram.create_instruction`.
        Their account layouts are documented in the IDL's
        ``directInstructions`` section.
        """
        return Instruction(
            program_id=self.program.address.raw, accounts=accounts, data=data
        )

    def build_refresh_reserve_instruction(
        self, reserve: SolendReserveConfiguration
    ) -> Instruction:
        """Build ``RefreshReserve`` (index 3) for a single reserve.

        Accrues the reserve's interest and re-reads its oracle prices. The
        program rejects any instruction touching a reserve that has not been
        refreshed in the current slot.

        Data: ``[3]`` (no arguments). Account layout::

            0 [w] reserve
            1 [ ] pyth price oracle
            2 [ ] switchboard price feed oracle
        """
        accounts = [
            AccountMeta(SolanaAddress.from_string(reserve.address).raw, False, True),
            AccountMeta(
                SolanaAddress.from_string(reserve.pyth_oracle).raw, False, False
            ),
            AccountMeta(
                SolanaAddress.from_string(reserve.switchboard_oracle).raw, False, False
            ),
        ]
        return self._program_instruction(
            accounts, bytes([SolendInstruction.REFRESH_RESERVE])
        )

    def build_refresh_obligation_instruction(
        self,
        user: SolanaAddress,
        reserves: Sequence[SolendReserveConfiguration],
    ) -> Instruction:
        """Build ``RefreshObligation`` (index 7) for a user's obligation.

        Recomputes the obligation's collateral/borrow values from the
        (already refreshed) reserves it references.

        Data: ``[7]`` (no arguments). Account layout::

            0 [w] obligation
            1.. [ ] every deposit reserve, then every borrow reserve, in the
                    exact order they are stored in the obligation account

        Args:
            user: The obligation owner's wallet address
            reserves: The obligation's reserves, deposits first then borrows,
                in obligation order (see :meth:`obligation_reserves`)
        """
        accounts = [AccountMeta(self.derive_obligation_address(user).raw, False, True)]
        accounts.extend(
            AccountMeta(SolanaAddress.from_string(reserve.address).raw, False, False)
            for reserve in reserves
        )
        return self._program_instruction(
            accounts, bytes([SolendInstruction.REFRESH_OBLIGATION])
        )

    def build_create_obligation_account_instruction(
        self, user: SolanaAddress, lamports: int
    ) -> Instruction:
        """Build the ``SystemProgram::CreateAccountWithSeed`` for the obligation.

        The obligation account is not a PDA: it is a plain system-created
        account at the deterministic address
        ``create_with_seed(user, obligation_seed, solend_program)``, so it is
        the client that has to allocate and fund it before the program can
        initialize it. The parameters mirror
        :meth:`derive_obligation_address` exactly - same base (the user's
        wallet), same seed (:attr:`obligation_seed`), same owner program - so
        the created account lands on the address every other instruction of
        this module derives.

        Args:
            user (SolanaAddress): The obligation owner; pays for and bases the
                seeded address (it must sign the transaction)
            lamports (int): Lamports to fund the account with; must be at
                least the rent-exempt minimum for
                :data:`OBLIGATION_ACCOUNT_SIZE` bytes (see
                :meth:`fetch_obligation_rent_exemption`)

        Returns:
            Instruction: The system-program instruction creating the account
                with ``space = OBLIGATION_ACCOUNT_SIZE`` owned by the Solend
                program
        """
        return create_account_with_seed(
            CreateAccountWithSeedParams(
                from_pubkey=user.raw,
                to_pubkey=self.derive_obligation_address(user).raw,
                base=user.raw,
                seed=self.obligation_seed,
                lamports=lamports,
                space=OBLIGATION_ACCOUNT_SIZE,
                owner=self.program.address.raw,
            )
        )

    def build_init_obligation_instruction(self, user: SolanaAddress) -> Instruction:
        """Build ``InitObligation`` (index 6) for a user's obligation.

        Writes the empty ``Obligation`` state struct into the freshly created
        (still zeroed) account, binding it to this lending market and owner.
        It must run after
        :meth:`build_create_obligation_account_instruction` and before any
        instruction that unpacks the obligation.

        Like the refresh instructions it carries no arguments beyond its
        discriminant and is built directly against the program id; its account
        layout is documented under ``directInstructions`` in
        ``common/idl/solend.json``.

        Data: ``[6]`` (no arguments). Account layout::

            0 [w] obligation (created, uninitialized)
            1 [ ] lending market
            2 [s] obligation owner (user)
            3 [ ] clock sysvar
            4 [ ] rent sysvar
            5 [ ] SPL token program

        Args:
            user (SolanaAddress): The obligation owner (must sign)

        Returns:
            Instruction: The ``InitObligation`` instruction
        """
        accounts = [
            AccountMeta(self.derive_obligation_address(user).raw, False, True),
            AccountMeta(self._lending_market.raw, False, False),
            AccountMeta(user.raw, True, False),
            AccountMeta(sysvar.CLOCK, False, False),
            AccountMeta(sysvar.RENT, False, False),
            AccountMeta(TOKEN_PROGRAM_ID, False, False),
        ]
        return self._program_instruction(
            accounts, bytes([SolendInstruction.INIT_OBLIGATION])
        )

    def build_create_collateral_account_instruction(
        self, reserve: SolendReserveConfiguration, user: SolanaAddress
    ) -> Instruction:
        """Build the idempotent ATA creation for a reserve's collateral token.

        The deposit instruction credits the reserve's collateral (cToken) mint
        to the user's associated token account, which does not exist for a
        first-time supplier. The *idempotent* associated-token-account
        instruction (data ``[1]``) is used rather than the plain one (data
        ``[]``) so that the transaction still succeeds when the account was
        created between this module's existence check and execution.

        Args:
            reserve (SolendReserveConfiguration): The reserve whose collateral
                mint the account is for
            user (SolanaAddress): The account owner; also the fee payer of the
                account rent (it must sign the transaction)

        Returns:
            Instruction: The associated-token-program instruction
        """
        collateral_mint = SolanaAddress.from_string(reserve.collateral_mint)
        return create_idempotent_associated_token_account(
            payer=user.raw, owner=user.raw, mint=collateral_mint.raw
        )

    def build_deposit_instruction(
        self,
        reserve: SolendReserveConfiguration,
        user: SolanaAddress,
        raw_liquidity_amount: int,
    ) -> Instruction:
        """Build ``DepositReserveLiquidityAndObligationCollateral`` (index 14).

        Data: ``[14] + liquidity_amount (u64 LE)``. Account layout (from
        solend-program ``instruction.rs``)::

             0 [w] source liquidity (user's liquidity ATA)
             1 [w] destination collateral (user's collateral ATA)
             2 [w] reserve
             3 [w] reserve liquidity supply
             4 [w] reserve collateral mint
             5 [ ] lending market
             6 [ ] lending market authority (PDA)
             7 [w] reserve collateral supply (obligation deposit destination)
             8 [w] obligation
             9 [s] obligation owner (user)
            10 [ ] pyth price oracle
            11 [ ] switchboard price feed oracle
            12 [s] user transfer authority (user)
            13 [ ] SPL token program
        """
        liquidity_mint = SolanaAddress.from_string(reserve.liquidity_mint)
        collateral_mint = SolanaAddress.from_string(reserve.collateral_mint)
        accounts = [
            AccountMeta(
                self.derive_associated_token_account(user, liquidity_mint).raw,
                False,
                True,
            ),
            AccountMeta(
                self.derive_associated_token_account(user, collateral_mint).raw,
                False,
                True,
            ),
            AccountMeta(SolanaAddress.from_string(reserve.address).raw, False, True),
            AccountMeta(
                SolanaAddress.from_string(reserve.liquidity_supply).raw, False, True
            ),
            AccountMeta(collateral_mint.raw, False, True),
            AccountMeta(self._lending_market.raw, False, False),
            AccountMeta(self.derive_lending_market_authority().raw, False, False),
            AccountMeta(
                SolanaAddress.from_string(reserve.collateral_supply).raw, False, True
            ),
            AccountMeta(self.derive_obligation_address(user).raw, False, True),
            AccountMeta(user.raw, True, False),
            AccountMeta(
                SolanaAddress.from_string(reserve.pyth_oracle).raw, False, False
            ),
            AccountMeta(
                SolanaAddress.from_string(reserve.switchboard_oracle).raw, False, False
            ),
            AccountMeta(user.raw, True, False),
            AccountMeta(TOKEN_PROGRAM_ID, False, False),
        ]
        data = bytes(
            [SolendInstruction.DEPOSIT_RESERVE_LIQUIDITY_AND_OBLIGATION_COLLATERAL]
        ) + raw_liquidity_amount.to_bytes(8, "little")
        return self.program.create_instruction(
            name="deposit", accounts=accounts, data=data
        )

    def build_withdraw_instruction(
        self,
        reserve: SolendReserveConfiguration,
        user: SolanaAddress,
        raw_collateral_amount: int,
    ) -> Instruction:
        """Build ``WithdrawObligationCollateralAndRedeemReserveCollateral`` (15).

        Data: ``[15] + collateral_amount (u64 LE)``. Account layout::

             0 [w] reserve collateral supply (source collateral)
             1 [w] user's collateral ATA (destination collateral)
             2 [w] withdraw reserve
             3 [w] obligation
             4 [ ] lending market
             5 [ ] lending market authority (PDA)
             6 [w] user's liquidity ATA (destination liquidity)
             7 [w] reserve collateral mint
             8 [w] reserve liquidity supply
             9 [s] obligation owner (user)
            10 [s] user transfer authority (user)
            11 [ ] SPL token program
        """
        liquidity_mint = SolanaAddress.from_string(reserve.liquidity_mint)
        collateral_mint = SolanaAddress.from_string(reserve.collateral_mint)
        accounts = [
            AccountMeta(
                SolanaAddress.from_string(reserve.collateral_supply).raw, False, True
            ),
            AccountMeta(
                self.derive_associated_token_account(user, collateral_mint).raw,
                False,
                True,
            ),
            AccountMeta(SolanaAddress.from_string(reserve.address).raw, False, True),
            AccountMeta(self.derive_obligation_address(user).raw, False, True),
            AccountMeta(self._lending_market.raw, False, False),
            AccountMeta(self.derive_lending_market_authority().raw, False, False),
            AccountMeta(
                self.derive_associated_token_account(user, liquidity_mint).raw,
                False,
                True,
            ),
            AccountMeta(collateral_mint.raw, False, True),
            AccountMeta(
                SolanaAddress.from_string(reserve.liquidity_supply).raw, False, True
            ),
            AccountMeta(user.raw, True, False),
            AccountMeta(user.raw, True, False),
            AccountMeta(TOKEN_PROGRAM_ID, False, False),
        ]
        data = bytes(
            [
                SolendInstruction.WITHDRAW_OBLIGATION_COLLATERAL_AND_REDEEM_RESERVE_COLLATERAL
            ]
        ) + raw_collateral_amount.to_bytes(8, "little")
        return self.program.create_instruction(
            name="withdraw", accounts=accounts, data=data
        )

    def build_borrow_instruction(
        self,
        reserve: SolendReserveConfiguration,
        user: SolanaAddress,
        raw_liquidity_amount: int,
    ) -> Instruction:
        """Build ``BorrowObligationLiquidity`` (index 10).

        Data: ``[10] + liquidity_amount (u64 LE)``. Account layout::

            0 [w] reserve liquidity supply (source liquidity)
            1 [w] user's liquidity ATA (destination liquidity)
            2 [w] borrow reserve
            3 [w] reserve liquidity fee receiver
            4 [w] obligation
            5 [ ] lending market
            6 [ ] lending market authority (PDA)
            7 [s] obligation owner (user)
            8 [ ] SPL token program

        The optional trailing host-fee-receiver account is not appended.
        """
        liquidity_mint = SolanaAddress.from_string(reserve.liquidity_mint)
        accounts = [
            AccountMeta(
                SolanaAddress.from_string(reserve.liquidity_supply).raw, False, True
            ),
            AccountMeta(
                self.derive_associated_token_account(user, liquidity_mint).raw,
                False,
                True,
            ),
            AccountMeta(SolanaAddress.from_string(reserve.address).raw, False, True),
            AccountMeta(
                SolanaAddress.from_string(reserve.liquidity_fee_receiver).raw,
                False,
                True,
            ),
            AccountMeta(self.derive_obligation_address(user).raw, False, True),
            AccountMeta(self._lending_market.raw, False, False),
            AccountMeta(self.derive_lending_market_authority().raw, False, False),
            AccountMeta(user.raw, True, False),
            AccountMeta(TOKEN_PROGRAM_ID, False, False),
        ]
        data = bytes(
            [SolendInstruction.BORROW_OBLIGATION_LIQUIDITY]
        ) + raw_liquidity_amount.to_bytes(8, "little")
        return self.program.create_instruction(
            name="borrow", accounts=accounts, data=data
        )

    def build_repay_instruction(
        self,
        reserve: SolendReserveConfiguration,
        user: SolanaAddress,
        raw_liquidity_amount: int,
    ) -> Instruction:
        """Build ``RepayObligationLiquidity`` (index 11).

        Data: ``[11] + liquidity_amount (u64 LE)`` (``u64::MAX`` repays the
        full outstanding debt). Account layout::

            0 [w] user's liquidity ATA (source liquidity)
            1 [w] reserve liquidity supply (destination liquidity)
            2 [w] repay reserve
            3 [w] obligation
            4 [ ] lending market
            5 [s] user transfer authority (user)
            6 [ ] SPL token program
        """
        liquidity_mint = SolanaAddress.from_string(reserve.liquidity_mint)
        accounts = [
            AccountMeta(
                self.derive_associated_token_account(user, liquidity_mint).raw,
                False,
                True,
            ),
            AccountMeta(
                SolanaAddress.from_string(reserve.liquidity_supply).raw, False, True
            ),
            AccountMeta(SolanaAddress.from_string(reserve.address).raw, False, True),
            AccountMeta(self.derive_obligation_address(user).raw, False, True),
            AccountMeta(self._lending_market.raw, False, False),
            AccountMeta(user.raw, True, False),
            AccountMeta(TOKEN_PROGRAM_ID, False, False),
        ]
        data = bytes(
            [SolendInstruction.REPAY_OBLIGATION_LIQUIDITY]
        ) + raw_liquidity_amount.to_bytes(8, "little")
        return self.program.create_instruction(
            name="repay", accounts=accounts, data=data
        )

    # === Transaction building (build-only, unsigned) ===

    def _operation_id(self, action: str, mint: str) -> str:
        """Generate a unique client operation id for a built transaction."""
        return f"solend-{action}-{mint}-{uuid.uuid4().hex}"

    def obligation_reserves(
        self, obligation: SolendObligationState
    ) -> list[SolendReserveConfiguration]:
        """Resolve an obligation's reserves: deposits first, then borrows.

        The order mirrors the obligation account's own entry order, which is
        exactly the order ``RefreshObligation`` expects its reserve accounts
        in.

        Args:
            obligation (SolendObligationState): The parsed obligation

        Returns:
            list[SolendReserveConfiguration]: The referenced reserves, in
                obligation order (a reserve used both as collateral and as a
                borrow appears twice, as it does on-chain)

        Raises:
            ValueError: If the obligation references a reserve that is not
                present in this strategy's configuration
        """
        reserves = [
            self.reserve_configuration_for_address(deposit.deposit_reserve)
            for deposit in obligation.deposits
        ]
        reserves.extend(
            self.reserve_configuration_for_address(borrow.borrow_reserve)
            for borrow in obligation.borrows
        )
        return reserves

    def build_obligation_refresh_instructions(
        self,
        user: SolanaAddress,
        obligation: SolendObligationState,
        target: SolendReserveConfiguration,
    ) -> list[Instruction]:
        """Build the refresh instructions an obligation instruction requires.

        One ``RefreshReserve`` per distinct reserve involved (every obligation
        reserve plus the instruction's own ``target`` reserve, which is
        refreshed too even when the obligation does not reference it yet),
        followed by a single ``RefreshObligation`` listing the obligation's
        reserves in their stored order.

        Args:
            user (SolanaAddress): The obligation owner
            obligation (SolendObligationState): The parsed obligation
            target (SolendReserveConfiguration): The reserve the following
                instruction operates on

        Returns:
            list[Instruction]: The refresh instructions, in the order they
                must precede the operation
        """
        referenced_reserves = self.obligation_reserves(obligation)

        distinct: list[SolendReserveConfiguration] = []
        seen: set[str] = set()
        for reserve in [*referenced_reserves, target]:
            if reserve.address not in seen:
                seen.add(reserve.address)
                distinct.append(reserve)

        instructions = [
            self.build_refresh_reserve_instruction(reserve) for reserve in distinct
        ]
        instructions.append(
            self.build_refresh_obligation_instruction(user, referenced_reserves)
        )
        return instructions

    async def _wrap_instructions(
        self,
        instructions: Sequence[Instruction],
        user: SolanaAddress,
        client_operation_id: str,
    ) -> SolanaTransaction:
        """Wrap instructions into an unsigned tracked SolanaTransaction.

        A legacy message is built with the user as fee payer and the current
        recent blockhash; the result carries the raw (unsigned) transaction
        and is never signed or broadcast here.
        """
        recent_blockhash = await self._blockchain.fetch_recent_blockhash()
        message = Message.new_with_blockhash(
            list(instructions), user.raw, recent_blockhash
        )
        unsigned = Transaction.new_unsigned(message)

        return SolanaTransaction(
            client_operation_id=client_operation_id,
            owner_identifier=SolanaWalletIdentifier(
                platform=self._platform,
                name=user.string,
                address=user,
            ),
            creation_timestamp=self._blockchain.current_timestamp,
            raw_transaction=SolanaRawTransaction.from_raw(unsigned),
        )

    def _raw_amount(self, asset: BlockchainAsset, amount: Decimal) -> int:
        """Convert a decimal amount to raw units using the asset's decimals.

        Raises:
            ValueError: If the asset is not a Solana asset, its data is not
                initialized, or the amount is not positive
        """
        solana_asset = self._as_solana_asset(asset)
        raw_amount = solana_asset.convert_to_raw(amount)
        if raw_amount <= 0:
            raise ValueError(f"Amount must be positive, got {amount}")
        return raw_amount

    async def build_supply_prerequisite_instructions(
        self, reserve: SolendReserveConfiguration, user: SolanaAddress
    ) -> list[Instruction]:
        """Build the account creations a supply needs, based on chain state.

        ``DepositReserveLiquidityAndObligationCollateral`` unpacks the user's
        obligation account and credits the reserve's cTokens to the user's
        collateral associated token account. Neither exists for a wallet that
        has never supplied to this lending market, and the program creates
        neither, so a first-time deposit alone always fails on-chain. This
        method probes both accounts (two ``getAccountInfo`` reads at the
        blockchain's commitment) and returns only the creations that are
        actually missing, in the order they must run:

        1. ``SystemProgram::CreateAccountWithSeed`` allocating and funding the
           derived obligation account (skipped when the obligation exists)
        2. ``InitObligation`` writing its initial state (same condition)
        3. the idempotent creation of the user's collateral ATA (skipped when
           that account exists)

        The user's *liquidity* ATA is deliberately not created: it is the
        deposit's source account and must already hold the supplied amount.

        Mere existence is the right predicate for both probes: the creation
        instructions fail on an account that already exists ("account already
        in use"), and Solana transactions are atomic, so an obligation account
        can never be left created-but-uninitialized by this builder. The ATA
        creation is additionally emitted in its idempotent form, so it also
        tolerates the account appearing between the probe and execution.

        Args:
            reserve (SolendReserveConfiguration): The reserve being supplied to
            user (SolanaAddress): The supplying user

        Returns:
            list[Instruction]: The creation instructions to prepend to the
                deposit; empty when both accounts already exist
        """
        instructions: list[Instruction] = []

        obligation_address = self.derive_obligation_address(user)
        if not await self.account_exists(obligation_address):
            lamports = await self.fetch_obligation_rent_exemption()
            instructions.append(
                self.build_create_obligation_account_instruction(user, lamports)
            )
            instructions.append(self.build_init_obligation_instruction(user))

        collateral_account = self.derive_associated_token_account(
            user, SolanaAddress.from_string(reserve.collateral_mint)
        )
        if not await self.account_exists(collateral_account):
            instructions.append(
                self.build_create_collateral_account_instruction(reserve, user)
            )

        return instructions

    async def build_supply_transaction(
        self,
        asset: BlockchainAsset,
        amount: Decimal,
        user_address: str,
        enable_as_collateral: bool = True,
        *,
        client_operation_id: str | None = None,
    ) -> SolanaTransaction:
        """Build the (unsigned) supply transaction.

        Uses ``DepositReserveLiquidityAndObligationCollateral``, so supplied
        liquidity is always deposited as obligation collateral;
        ``enable_as_collateral`` is ignored (Solend has no collateral toggle).

        The deposit needs no ``RefreshReserve``: it takes the reserve's Pyth
        and Switchboard oracle accounts and refreshes the reserve itself. It
        does, however, need the user's obligation account and collateral
        (cToken) associated token account to exist. Both are checked on-chain
        and the missing ones are created by instructions prepended to the same
        transaction (see :meth:`build_supply_prerequisite_instructions`), so a
        fresh wallet can supply in one transaction; a wallet that already has
        both gets the single deposit instruction as before.

        Args:
            asset: The asset to supply (mint must have a configured reserve)
            amount: The amount to supply, in decimal token units
            user_address: The supplying user (fee payer, obligation owner,
                and transfer authority)
            enable_as_collateral: Ignored; deposits are always collateral
            client_operation_id: Optional tracking id; generated when omitted

        Returns:
            SolanaTransaction: The unsigned, trackable transaction
        """
        await self._ensure_program()
        reserve = self.reserve_configuration_for_asset(asset)
        raw_amount = self._raw_amount(asset, amount)
        user = SolanaAddress.from_string(user_address)

        instructions = await self.build_supply_prerequisite_instructions(reserve, user)
        instructions.append(self.build_deposit_instruction(reserve, user, raw_amount))
        return await self._wrap_instructions(
            instructions,
            user,
            client_operation_id or self._operation_id("supply", reserve.liquidity_mint),
        )

    async def build_withdraw_transaction(
        self,
        asset: BlockchainAsset,
        amount: Decimal,
        user_address: str,
        withdraw_all: bool = False,
        *,
        client_operation_id: str | None = None,
    ) -> SolanaTransaction:
        """Build the (unsigned) withdraw transaction.

        Uses ``WithdrawObligationCollateralAndRedeemReserveCollateral``. The
        requested ``amount`` is denominated in the underlying liquidity token
        and converted to collateral (cToken) units with the reserve's current
        on-chain exchange rate.

        With ``withdraw_all`` the collateral amount is the obligation's exact
        deposited amount for the reserve, read from the obligation account.
        Solend's withdraw instruction takes a collateral (cToken) amount and
        the ``u64::MAX`` "full amount" sentinel is only documented for repay,
        so the full position is resolved client-side instead of relying on it.

        The instruction sequence is ``RefreshReserve`` for every reserve of the
        obligation (plus this one), then ``RefreshObligation``, then the
        withdraw itself: the program rejects stale reserves/obligations.

        Args:
            asset: The asset to withdraw
            amount: The amount to withdraw, in decimal liquidity token units
                (ignored when ``withdraw_all`` is True)
            user_address: The withdrawing user
            withdraw_all: Whether to withdraw the full deposited position
            client_operation_id: Optional tracking id; generated when omitted

        Returns:
            SolanaTransaction: The unsigned, trackable transaction

        Raises:
            ValueError: If the user has no obligation, the amount converts to
                zero collateral units, or ``withdraw_all`` is requested for a
                reserve the obligation holds no collateral in
        """
        await self._ensure_program()
        reserve = self.reserve_configuration_for_asset(asset)
        user = SolanaAddress.from_string(user_address)
        obligation = await self.require_obligation_state(user)

        if withdraw_all:
            raw_collateral = self._deposited_collateral(obligation, reserve)
        else:
            raw_amount = self._raw_amount(asset, amount)
            state = await self.get_reserve_state(reserve)
            raw_collateral = state.liquidity_to_collateral(raw_amount)
            if raw_collateral <= 0:
                raise ValueError(
                    f"Withdraw amount {amount} converts to zero collateral units"
                )

        instructions = self.build_obligation_refresh_instructions(
            user, obligation, reserve
        )
        instructions.append(
            self.build_withdraw_instruction(reserve, user, raw_collateral)
        )
        return await self._wrap_instructions(
            instructions,
            user,
            client_operation_id
            or self._operation_id("withdraw", reserve.liquidity_mint),
        )

    @staticmethod
    def _deposited_collateral(
        obligation: SolendObligationState, reserve: SolendReserveConfiguration
    ) -> int:
        """Sum the obligation's deposited collateral for one reserve.

        Raises:
            ValueError: If the obligation holds no collateral in the reserve
        """
        deposited = sum(
            deposit.deposited_amount
            for deposit in obligation.deposits
            if str(deposit.deposit_reserve) == reserve.address
        )
        if deposited <= 0:
            raise ValueError(
                f"Obligation holds no collateral in Solend reserve "
                f"{reserve.address}: nothing to withdraw"
            )
        return deposited

    async def build_borrow_transaction(
        self,
        asset: BlockchainAsset,
        amount: Decimal,
        interest_rate_mode: InterestRateMode,
        user_address: str,
        *,
        client_operation_id: str | None = None,
    ) -> SolanaTransaction:
        """Build the (unsigned) borrow transaction.

        Uses ``BorrowObligationLiquidity``. Solend only supports variable-rate
        borrowing; ``interest_rate_mode`` is accepted for contract
        compatibility and ignored.

        The instruction sequence is ``RefreshReserve`` for every reserve of the
        obligation (plus the borrow reserve), then ``RefreshObligation``, then
        the borrow itself: the program rejects stale reserves/obligations.

        Args:
            asset: The asset to borrow
            amount: The amount to borrow, in decimal token units
            interest_rate_mode: Ignored (variable only)
            user_address: The borrowing user (obligation owner)
            client_operation_id: Optional tracking id; generated when omitted

        Returns:
            SolanaTransaction: The unsigned, trackable transaction

        Raises:
            ValueError: If the user has no obligation account
        """
        await self._ensure_program()
        reserve = self.reserve_configuration_for_asset(asset)
        raw_amount = self._raw_amount(asset, amount)
        user = SolanaAddress.from_string(user_address)
        obligation = await self.require_obligation_state(user)

        instructions = self.build_obligation_refresh_instructions(
            user, obligation, reserve
        )
        instructions.append(self.build_borrow_instruction(reserve, user, raw_amount))
        return await self._wrap_instructions(
            instructions,
            user,
            client_operation_id or self._operation_id("borrow", reserve.liquidity_mint),
        )

    async def build_repay_transaction(
        self,
        asset: BlockchainAsset,
        amount: Decimal,
        interest_rate_mode: InterestRateMode,
        user_address: str,
        repay_all: bool = False,
        *,
        client_operation_id: str | None = None,
    ) -> SolanaTransaction:
        """Build the (unsigned) repay transaction.

        Uses ``RepayObligationLiquidity``. When ``repay_all`` is True the
        amount is encoded as ``u64::MAX``, which the program interprets as the
        full outstanding debt. ``interest_rate_mode`` is ignored (variable
        only).

        The repay instruction only reads the repay reserve (it does not
        recompute obligation values), so the transaction is a single
        ``RefreshReserve`` for that reserve followed by the repay.

        Args:
            asset: The asset to repay
            amount: The amount to repay, in decimal token units (ignored when
                ``repay_all`` is True)
            interest_rate_mode: Ignored (variable only)
            user_address: The repaying user (transfer authority)
            repay_all: Whether to repay the full outstanding debt
            client_operation_id: Optional tracking id; generated when omitted

        Returns:
            SolanaTransaction: The unsigned, trackable transaction
        """
        await self._ensure_program()
        reserve = self.reserve_configuration_for_asset(asset)
        raw_amount = U64_MAX if repay_all else self._raw_amount(asset, amount)
        user = SolanaAddress.from_string(user_address)

        instructions = [
            self.build_refresh_reserve_instruction(reserve),
            self.build_repay_instruction(reserve, user, raw_amount),
        ]
        return await self._wrap_instructions(
            instructions,
            user,
            client_operation_id or self._operation_id("repay", reserve.liquidity_mint),
        )

    async def build_collateral_transaction(
        self,
        asset: BlockchainAsset,
        mode: CollateralMode,
        user_address: str,
    ) -> SolanaTransaction:
        """Unsupported on Solend.

        Solend has no collateral toggle instruction: liquidity deposited via
        ``DepositReserveLiquidityAndObligationCollateral`` is always active
        obligation collateral.

        Raises:
            NotImplementedError: Always
        """
        raise NotImplementedError(
            "Solend has no collateral toggle: deposited liquidity is always "
            "active obligation collateral"
        )

    async def build_liquidation_transaction(
        self,
        collateral_asset: BlockchainAsset,
        debt_asset: BlockchainAsset,
        user_to_liquidate: str,
        debt_to_cover: Decimal,
        receive_collateral: bool = True,
    ) -> SolanaTransaction:
        """Not implemented.

        Liquidations on Solend use ``LiquidateObligation`` (instruction 12) /
        ``LiquidateObligationAndRedeemReserveCollateral``, which additionally
        require the refreshed states of every reserve involved; building them
        is not implemented.

        Raises:
            NotImplementedError: Always
        """
        raise NotImplementedError("Solend liquidation building is not implemented")

    # === Execution (requires a bound wallet) ===

    def place_transaction(self, transaction: SolanaTransaction) -> SolanaTransaction:
        """Sign and broadcast a previously built transaction.

        The bound wallet signs the transaction's raw legacy message and hands
        it to its sign-and-send API for broadcasting and tracking.

        Args:
            transaction (SolanaTransaction): A transaction produced by one of
                the ``build_*`` methods

        Returns:
            SolanaTransaction: The wallet-tracked transaction

        Raises:
            ValueError: If no wallet is bound, the transaction carries no raw
                transaction, or the wallet is not the transaction's fee payer
        """
        wallet = self.wallet

        raw = transaction.raw_transaction
        if raw is None:
            raise ValueError("Transaction carries no raw transaction to sign")
        message = raw.message
        if not isinstance(message, Message):
            raise ValueError("Only legacy (non-versioned) messages are supported")
        if raw.fee_payer != wallet.address.raw:
            raise ValueError(
                f"Bound wallet {wallet.address.string} is not the transaction "
                f"fee payer {raw.fee_payer}"
            )

        return wallet.sign_and_send_transaction(
            client_operation_id=transaction.client_operation_id,
            transaction=Transaction.new_unsigned(message),
            recent_blockhash=raw.recent_blockhash,
        )


class SolendMoneyMarket(SolanaMoneyMarket):
    """Solend money market facade.

    Registers a :class:`Solend` strategy for every protocol entry named
    "solend" in the configuration, wiring in the lending market and reserve
    account configuration.
    """

    @property
    def configuration(self) -> SolendConfiguration:
        return cast(SolendConfiguration, super().configuration)

    def _initialize_protocols(self) -> None:
        """Initialize Solend protocol strategies."""
        configuration = self.configuration
        for protocol_config in configuration.protocols:
            if protocol_config.protocol_name.lower() == "solend":
                self._protocol_strategies[protocol_config.protocol_name] = Solend(
                    protocol_config=protocol_config,
                    blockchain=self.blockchain,
                    lending_market=configuration.lending_market,
                    reserves=configuration.reserves,
                    platform=configuration.platform,
                )
