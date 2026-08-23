# DApps guide

The `blockchainpype.dapps` package defines three chain-agnostic abstractions —
a **DEX**, a **money market** and a **betting market**. Each is a facade that
dispatches to per-protocol *strategies*. The chain-specific packages
(`blockchainpype.evm.dapp`, `blockchainpype.solana.dapp`) implement those
strategies against real protocols.

## The shared strategy contract

Every abstraction declares a runtime-checkable `ProtocolImplementation`
`Protocol` containing only method signatures. Three rules are common to all
three:

**1. Reads work without a wallet.** Quoting, market data, positions and prices
never need a signer.

**2. `build_*` methods are build-only.** They return an *unsigned*
transaction object and never sign or broadcast it. They raise `ValueError`
when a wallet is required but none is bound.

**3. Wallet binding goes through `set_wallet`.** Strategies that need a wallet
to build (EVM implementations need a sender address, chain id and gas
estimation) accept an optional `wallet` keyword at construction and must
implement `set_wallet(wallet | None)` so the owning facade — or application
code — can bind or replace it later. Strategies that never need one implement
it as a no-op or use it only for their own execution helpers.

```python
from blockchainpype.dapps.money_market import ProtocolImplementation
from blockchainpype.evm.dapp.money_market import AaveV3


def check(strategy: AaveV3) -> None:
    # The protocol is runtime-checkable: a strategy either satisfies it or not.
    assert isinstance(strategy, ProtocolImplementation)
```

### Unsigned EVM transactions

Every EVM `build_*` method returns an `EthereumTransaction` in
`PENDING_BROADCAST` state whose web3 parameters live under a single agreed key
of `other_data`, `UNSIGNED_TX_DATA_KEY` (`"tx_data"`). Producers use
`build_unsigned_transaction()`, consumers read it back with
`unsigned_tx_params()` — the convention is identical across Uniswap, Aave and
Polymarket.

```python
from blockchainpype.evm.dapp.unsigned import (
    UNSIGNED_TX_DATA_KEY,
    unsigned_tx_params,
)
from blockchainpype.evm.transaction import EthereumTransaction
from blockchainpype.evm.wallet.wallet import EthereumWallet

assert UNSIGNED_TX_DATA_KEY == "tx_data"


def sign_and_send(
    wallet: EthereumWallet, transaction: EthereumTransaction
) -> EthereumTransaction:
    """Turn a built transaction into a broadcast one."""
    params = unsigned_tx_params(transaction)  # raises if it carries none
    return wallet.sign_and_send_transaction(
        client_operation_id=transaction.client_operation_id,
        tx_data=dict(params),
    )
```

Solana strategies follow the same build-only rule but carry a raw unsigned
legacy `Transaction` in `SolanaTransaction.raw_transaction` instead; Solend's
`place_transaction()` is the matching execution helper.

### Slippage and deadline forwarding

The facades own the defaults and always forward an explicit value to the
strategy, so a strategy never has to guess:

* `DexConfiguration.default_slippage` (0.5 %) is passed to `quote_swap` as
  `max_slippage` and **embedded in the returned `SwapRoute`**, which is what
  the build step later uses to compute the minimum received / maximum spent.
* `DexConfiguration.default_deadline_minutes` (20) is passed to
  `build_swap_transaction` by `execute_swap`.
* `BettingMarketConfiguration.default_slippage_tolerance` (1 %) derives
  `max_price` / `min_price` when the caller omits them.

## DEX abstraction

```text
DecentralizedExchange (facade)
├── quote_swap(...)        → best route across protocols, or a named protocol
├── update_quote(route)    → re-quote an existing route
├── find_best_route(...)   → direct + multi-hop candidates (subclass hook)
├── execute_swap(route)    → BUILD the transaction for the route's protocol
├── get_reserves(a, b)     → first protocol that answers
└── get_supported_pools()  → pool discovery (subclass hook)
```

`quote_swap` without a `protocol` aggregates: it quotes every registered
strategy, logs and tolerates individual failures, and picks the best result —
highest output for `EXACT_INPUT`, lowest input for `EXACT_OUTPUT`. If **all**
protocols fail, the first error is re-raised; if none produced a route,
`ValueError("No valid route found")`.

`SwapRoute` is a `SwapHop` (input/output asset and amount) extended with the
hop `sequence`, the `mode`, the embedded `max_slippage`, the `taxes` (protocol
fee) and the `protocol` key that later selects the strategy. Its validator
enforces that hops chain correctly and that route-level amounts match the first
and last hop.

## Uniswap (EVM)

`UniswapDEX` wires the `uniswap_v2` and `uniswap_v3` strategies against an
explicit `EthereumBlockchain`, so the same code runs on mainnet, Polygon or a
local Hardhat node — every contract is bound directly to that blockchain
instead of being resolved through the global operator factory.

### Configuration

`UniswapConfiguration` extends `DexConfiguration` with `intermediate_assets`
(the multi-hop candidates) and an optional `v3_quoter_address` override
(defaulting to the canonical `IQuoter` deployment
`0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6`). Three constructors:

| Constructor | Protocols |
| --- | --- |
| `UniswapConfiguration.ethereum_mainnet(intermediate_assets=None)` | V2 (0.3 % fee) + V3 (0.01/0.05/0.3/1 % tiers) |
| `UniswapConfiguration.polygon_mainnet(intermediate_assets=None)` | V3 only |
| `UniswapConfiguration.local_network(platform, v2_factory_address=..., v2_router_address=..., v3_factory_address=..., v3_router_address=..., v3_quoter_address=..., ...)` | Whichever complete factory+router pairs you supply (at least one, else `ValueError`) |

Passing no configuration at all auto-detects from the chain id (1 → Ethereum,
137 → Polygon); any other chain raises `ValueError` telling you to pass one. A
configuration whose `platform` does not match the blockchain's is rejected.

### Quoting and swapping

```python
import asyncio
from decimal import Decimal

from blockchainpype.dapps.router.models import SwapMode
from blockchainpype.evm.blockchain.blockchain import EthereumBlockchain
from blockchainpype.evm.blockchain.identifier import EthereumAddress
from blockchainpype.evm.dapp.erc20 import (
    ERC20Contract,
    ERC20ContractConfiguration,
    ERC20Token,
)
from blockchainpype.evm.dapp.uniswap import UniswapConfiguration, UniswapDEX
from blockchainpype.evm.dapp.unsigned import unsigned_tx_params
from blockchainpype.evm.wallet.wallet import EthereumWallet

USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
DAI = "0x6B175474E89094C44Da98b954EedeAC495271d0F"


def token(blockchain: EthereumBlockchain, address: str) -> ERC20Token:
    token_address = EthereumAddress.from_string(address)
    return ERC20Token(
        platform=blockchain.platform,
        identifier=token_address,
        contract=ERC20Contract(
            ERC20ContractConfiguration(
                platform=blockchain.platform, address=token_address
            )
        ),
    )


async def swap(blockchain: EthereumBlockchain, wallet: EthereumWallet) -> None:
    usdc, weth, dai = (token(blockchain, a) for a in (USDC, WETH, DAI))
    for asset in (usdc, weth, dai):
        await asset.initialize_data()  # name / symbol / decimals

    uniswap = UniswapDEX(
        blockchain,
        UniswapConfiguration.ethereum_mainnet(intermediate_assets=[weth]),
        wallet=wallet,
    )
    print(uniswap.supported_protocols)  # ['uniswap_v2', 'uniswap_v3']

    # Best quote across V2 and V3.
    route = await uniswap.quote_swap(
        input_asset=usdc,
        output_asset=weth,
        amount=Decimal("1000"),
        mode=SwapMode.EXACT_INPUT,
        max_slippage=Decimal("0.005"),
    )
    print(route.protocol, route.output_amount, route.price)

    # Pin a protocol.
    v3_route = await uniswap.quote_swap(
        usdc, weth, Decimal("1000"), protocol="uniswap_v3"
    )
    print(v3_route.taxes)  # fee tier as a fraction, e.g. 0.0005

    # Direct pair *and* routes through the configured intermediates.
    best = await uniswap.find_best_route(
        input_asset=usdc,
        output_asset=dai,
        amount=Decimal("1000"),
        mode=SwapMode.EXACT_INPUT,
        max_hops=2,
    )
    print(len(best.sequence), "hop(s)")

    # Build (never send) the transaction for the winning route.
    transaction = await uniswap.execute_swap(best, deadline_minutes=10)
    params = unsigned_tx_params(transaction)
    print(params["to"], str(params["data"])[:10])

    # Reserves: V2 reads getReserves; V3 approximates from liquidity + slot0.
    usdc_reserve, weth_reserve = await uniswap.get_reserves(
        usdc, weth, protocol="uniswap_v2"
    )
    print(usdc_reserve, weth_reserve)


assert asyncio.iscoroutinefunction(swap)
```

!!! warning "Approve the router first"
    `execute_swap` only builds the router call. A real swap needs an ERC-20
    `approve` of the router for the input token beforehand (see
    `ERC20Contract.place_approve` in the [EVM guide](evm.md#erc-20-operations)),
    and the built transaction still has to be signed and broadcast.

### Multi-hop routing

`find_best_route` collects candidates and keeps the best under the route's
mode. It considers the direct pair on every selected protocol, plus paths
through `configuration.intermediate_assets` — each candidate stays on a single
protocol, so it remains executable as one transaction. `max_hops` bounds the
length: `1` restricts to direct swaps, `N` allows up to `N - 1` intermediates.
`max_hops < 1` raises `ValueError`.

Hops are quoted one at a time: forward for `EXACT_INPUT` (each hop's output
feeds the next), backward for `EXACT_OUTPUT` (each hop's required input feeds
the previous). The strategy then composes them with
`compose_multi_hop_route()`, which sums the per-hop `taxes` and rejects hops
belonging to another protocol.

Building differs per version:

* **V2** encodes the path as `[first input, *every hop output]` and calls
  `swapExactTokensForTokens` or `swapTokensForExactTokens`.
* **V3** single-hop calls `exactInputSingle` / `exactOutputSingle`; multi-hop
  packs the path as `token (20 B) | fee (3 B) | token | …` and calls
  `exactInput` / `exactOutput` — with the path **reversed** for
  `exactOutput`, as the router expects.

Because `SwapRoute` has no protocol-specific fields, the V3 pool fee travels in
`SwapRoute.taxes` as a fraction (`fee_tier / 1_000_000`, so the 3000 tier is
`0.003`). Single-hop builds recover the tier with `taxes * 1e6`; multi-hop
routes need one tier per hop, which a single sum cannot express, so
`compose_multi_hop_route` records the per-hop tiers in an internal route-keyed
map. A multi-hop V3 route that this strategy did not compose therefore raises
`ValueError` at build time.

```python
from decimal import Decimal

from blockchainpype.evm.dapp.uniswap import UniswapV2, UniswapV3

# Exact Uniswap V2 integer formulas over raw reserves.
assert UniswapV2.get_amount_out(10**18, 10**21, 10**21) == 996006981039903216
assert UniswapV3.fee_tier_from_fraction(Decimal("0.003")) == 3000

path = UniswapV3.encode_path(
    [
        "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        "0x6B175474E89094C44Da98b954EedeAC495271d0F",
    ],
    [500, 3000],
)
assert len(path) == 20 * 3 + 3 * 2
```

### One-call execution helpers

Both strategies also expose convenience methods that **do** sign and broadcast:
`create_swap_transaction(route, wallet=None, ...)` and
`execute_swap(input_asset, output_asset, amount, wallet=None, ...)` — note that
the strategy-level `execute_swap` quotes *and* sends, unlike the facade's
build-only `execute_swap(route)`.

### Pool discovery

Uniswap factories expose no enumeration cheap enough for client calls, so
`get_supported_pools(protocol=None, candidate_assets=None)` is scoped to
explicit candidates (defaulting to `intermediate_assets`). Every unordered pair
is checked — V2 via `getPair`, V3 via `getPool` per configured fee tier — and
included once if any pool exists. Fewer than two candidates raises
`ValueError`.

## Money market abstraction

```text
MoneyMarket (facade)
├── get_market_data(asset)          → first protocol, or a named one
├── get_user_account_data(address)  → aggregated collateral/debt/health factor
├── get_lending_positions(address)  → aggregated across protocols when unnamed
├── get_borrowing_positions(address)
├── supply(asset, amount, user_address, enable_as_collateral=None)
├── withdraw(asset, amount, user_address, withdraw_all=False)
├── borrow(asset, amount, user_address, interest_rate_mode=None)
├── repay(asset, amount, user_address, interest_rate_mode=None, repay_all=False)
├── set_collateral_mode(asset, mode, user_address)
└── liquidate(collateral_asset, debt_asset, user, debt_to_cover, ...)
```

All the write methods **build** unsigned transactions. Defaults come from
`MoneyMarketConfiguration`: `default_interest_rate_mode` (`VARIABLE`) and
`default_collateral_mode` (`ENABLED`) fill in omitted arguments.
`withdraw_all` and `repay_all` withdraw/repay the entire position, ignoring
`amount`.

The chain-specific facades add `is_position_safe(user_address, protocol=None)`,
which is stricter than the protocol's own liquidation point: it requires a
health factor of at least `1 + liquidation_threshold_buffer` (5 % by default),
leaving room to react before liquidation becomes possible.

Models: `MarketData` (APYs, supply/borrows, utilization, LTV, liquidation
threshold, reserve factor, flags — validated to keep rates in `[0, 1]` and LTV
≤ liquidation threshold), `LendingPosition` (with `total_balance` and
`collateral_value`), `BorrowingPosition` (with `total_debt`) and
`UserAccountData` (with `is_healthy` and a `liquidation_risk_level` of
`LOW`/`MEDIUM`/`HIGH`/`CRITICAL`).

## Aave V3 (EVM)

`AaveV3` talks to the Pool and PoolDataProvider contracts through the vendored
official ABIs (`common/abi/aave_v3_pool.json`,
`common/abi/aave_v3_data_provider.json`). `AaveV3Configuration` defaults to the
Ethereum mainnet deployment (Pool `0x87870Bca…4fA4E2`, data provider
`0x7B4EB56E…a138a3`) and requires at least one protocol whose name contains
`aave`.

### Unit conventions

| On-chain value | Scale | Reported as |
| --- | --- | --- |
| `liquidityRate`, `variableBorrowRate`, `stableBorrowRate` | ray (1e27) | `liquidity_rate` is the raw APR fraction; the `*_apy` fields are per-second compounded: `(1 + apr / 31_536_000) ** 31_536_000 - 1` |
| LTV, liquidation threshold, reserve factor | bps (1e4) | decimal fractions |
| `getUserAccountData` amounts | base currency, 8 decimals | decimal values |
| health factor | wad (1e18) | decimal |

```python
from decimal import Decimal

from blockchainpype.evm.dapp.money_market.aave import (
    apr_to_apy,
    base_currency_to_decimal,
    bps_to_decimal,
    ray_to_decimal,
    wad_to_decimal,
)

assert ray_to_decimal(10**27) == Decimal(1)
assert bps_to_decimal(8000) == Decimal("0.8")
assert wad_to_decimal(2 * 10**18) == Decimal(2)
assert base_currency_to_decimal(150_000_000) == Decimal("1.5")
assert apr_to_apy(Decimal(0)) == Decimal(0)
```

### Usage

```python
import asyncio
from decimal import Decimal

from blockchainpype.dapps.money_market import CollateralMode, InterestRateMode
from blockchainpype.evm.dapp.money_market import AaveV3Configuration, AaveV3MoneyMarket
from blockchainpype.evm.dapp.unsigned import unsigned_tx_params
from blockchainpype.evm.wallet.wallet import EthereumWallet
from financepype.assets.blockchain import BlockchainAsset


async def lend(wallet: EthereumWallet, usdc: BlockchainAsset) -> None:
    market = AaveV3MoneyMarket(
        AaveV3Configuration(platform=wallet.blockchain.platform),
        wallet=wallet,
    )
    user = wallet.address.string

    # --- reads (no wallet needed) ---
    data = await market.get_market_data(usdc)
    print(data.supply_apy, data.variable_borrow_apy, data.utilization_rate)

    account = await market.get_user_account_data(user)
    print(account.health_factor, account.liquidation_risk_level, account.is_healthy)
    print(await market.is_position_safe(user))  # health factor >= 1.05

    for position in await market.get_lending_positions(user):
        print(position.asset, position.total_balance, position.is_collateral)
    for debt in await market.get_borrowing_positions(user):
        print(debt.asset, debt.total_debt, debt.interest_rate_mode)

    # --- builds (unsigned) ---
    supply_tx = await market.supply(usdc, Decimal("500"), user)
    borrow_tx = await market.borrow(
        usdc, Decimal("100"), user, interest_rate_mode=InterestRateMode.VARIABLE
    )
    repay_all_tx = await market.repay(usdc, Decimal(0), user, repay_all=True)
    withdraw_all_tx = await market.withdraw(usdc, Decimal(0), user, withdraw_all=True)
    collateral_tx = await market.set_collateral_mode(
        usdc, CollateralMode.DISABLED, user
    )

    for transaction in (
        supply_tx,
        borrow_tx,
        repay_all_tx,
        withdraw_all_tx,
        collateral_tx,
    ):
        params = unsigned_tx_params(transaction)
        wallet.sign_and_send_transaction(
            client_operation_id=transaction.client_operation_id,
            tx_data=dict(params),
        )


assert asyncio.iscoroutinefunction(lend)
```

Protocol details worth knowing:

* **Supply** calls `Pool.supply(asset, amount, onBehalfOf, 0)`. Aave enables a
  first-time supply as collateral automatically, so `enable_as_collateral=True`
  needs no extra action; to opt out, follow up with
  `set_collateral_mode(asset, CollateralMode.DISABLED, user)`.
* **Withdraw-all / repay-all** send `type(uint256).max` as the amount, which
  Aave interprets as the full aToken balance / entire outstanding debt.
* **Collateral toggle** calls `setUserUseReserveAsCollateral`, which the Pool
  applies to `msg.sender`; `user_address` is part of the abstract contract but
  has no on-chain argument here.
* **Liquidation** calls `liquidationCall`; `receive_collateral=True` means the
  liquidator wants the underlying, so the on-chain `receiveAToken` flag is its
  **inverse**.
* **Lending positions**: aToken balances rebase, so `supplied_amount` is the
  current balance (interest included) and `accrued_interest` is `0` — the
  original principal is not exposed on-chain. **Borrowing positions**: a
  reserve can hold both variable and stable debt and then yields two
  positions; only stable debt has an on-chain principal, so only it reports a
  non-zero `accrued_interest`.

## Solend (Solana)

Solend is a fork of the SPL token-lending program, **not** an Anchor program:
every instruction is a single-byte enum discriminant followed by little-endian
fields. `blockchainpype.solana.dapp.money_market.solend` implements the four
core obligation flows with the exact on-chain account layouts, plus the binary
parsers for the `Reserve` (619 bytes) and `Obligation` (1300 bytes) accounts.

### Configuration

`SolendConfiguration` needs the `lending_market` account and one
`SolendReserveConfiguration` per reserve you want to operate on. Reserve
accounts cannot be derived client-side, so they must be supplied (Solend
publishes them per market): `address`, `liquidity_mint`, `liquidity_supply`,
`liquidity_fee_receiver`, `collateral_mint`, `collateral_supply`, `pyth_oracle`
and `switchboard_oracle`. Every field is validated as a base58 public key, and
at least one protocol entry must be named `solend`.

```python
from blockchainpype.dapps.money_market import ProtocolConfiguration
from blockchainpype.solana.dapp.money_market import (
    SOLEND_PROGRAM_ID,
    SolendConfiguration,
    SolendMoneyMarket,
    SolendReserveConfiguration,
)

MAIN_POOL = "4UpD2fh7xH3VP9QQaXtsS1YY3bxzWhtfpks7FatyKvdY"


def build_market(platform, usdc_reserve: SolendReserveConfiguration):
    configuration = SolendConfiguration(
        platform=platform,
        protocols=[
            ProtocolConfiguration(
                protocol_name="solend",
                lending_pool_address=SOLEND_PROGRAM_ID,
                data_provider_address=SOLEND_PROGRAM_ID,
            )
        ],
        lending_market=MAIN_POOL,
        reserves=[usdc_reserve],
    )
    return SolendMoneyMarket(configuration)
```

### Address derivation

* The **lending market authority** is the PDA of the lending market under the
  Solend program (`derive_lending_market_authority()`).
* The **obligation account** follows the solend-sdk convention:
  `Pubkey.create_with_seed(owner, seed, program)` where the seed is the first
  32 characters of the lending market's base58 address
  (`derive_obligation_address(owner)`).

### Refresh instructions

The program rejects any instruction touching a reserve — or an obligation —
that has not been refreshed in the current slot. `RefreshReserve` (index 3)
accrues interest and re-reads the Pyth and Switchboard oracles;
`RefreshObligation` (index 7) recomputes the obligation's values from the
already-refreshed reserves, listing them **in the exact order they are stored
in the obligation** (deposits first, then borrows —
`obligation_reserves(obligation)` produces that order).

`build_obligation_refresh_instructions(user, obligation, target)` assembles
one `RefreshReserve` per distinct reserve involved (every obligation reserve
plus the target, deduplicated) followed by a single `RefreshObligation`.
`build_withdraw_transaction`, `build_borrow_transaction` and
`build_repay_transaction` prepend them automatically.

`build_supply_transaction` needs none: Solend's
`DepositReserveLiquidityAndObligationCollateral` (index 14) carries the
reserve's oracle accounts in its own account list and refreshes the reserve
itself — which is exactly why the withdraw/borrow/repay layouts do not list
oracles.

### Usage

The `SolendMoneyMarket` facade is the multi-protocol entry point (it resolves
its blockchain through the `BlockchainFactory` using the configuration's
platform, so register it first). The `Solend` strategy can also be used on its
own — that is the public way to reach `place_transaction`, the execution helper
that signs and broadcasts a previously built transaction:

```python
import asyncio
from decimal import Decimal

from blockchainpype.dapps.money_market import InterestRateMode, ProtocolConfiguration
from blockchainpype.solana.blockchain.blockchain import SolanaBlockchain
from blockchainpype.solana.dapp.money_market import (
    SOLEND_PROGRAM_ID,
    Solend,
    SolendReserveConfiguration,
)
from blockchainpype.solana.wallet.wallet import SolanaWallet
from financepype.assets.blockchain import BlockchainAsset

MAIN_POOL = "4UpD2fh7xH3VP9QQaXtsS1YY3bxzWhtfpks7FatyKvdY"


async def lend(
    blockchain: SolanaBlockchain,
    wallet: SolanaWallet,
    usdc: BlockchainAsset,
    usdc_reserve: SolendReserveConfiguration,
) -> None:
    strategy = Solend(
        protocol_config=ProtocolConfiguration(
            protocol_name="solend",
            lending_pool_address=SOLEND_PROGRAM_ID,
            data_provider_address=SOLEND_PROGRAM_ID,
        ),
        blockchain=blockchain,
        lending_market=MAIN_POOL,
        reserves=[usdc_reserve],
        wallet=wallet,
    )
    user = wallet.address.string

    # Reads (a wallet is not needed for these).
    data = await strategy.get_market_data(usdc)
    print(data.supply_apy, data.variable_borrow_apy, data.utilization_rate)
    account = await strategy.get_user_account_data(user)
    print(account.health_factor)

    # Build-only: unsigned SolanaTransaction objects.
    supply_tx = await strategy.build_supply_transaction(usdc, Decimal("500"), user)
    borrow_tx = await strategy.build_borrow_transaction(
        usdc, Decimal("100"), InterestRateMode.VARIABLE, user
    )
    withdraw_all_tx = await strategy.build_withdraw_transaction(
        usdc, Decimal(0), user, withdraw_all=True
    )

    # Execution: sign and broadcast each built transaction with the bound wallet.
    for transaction in (supply_tx, borrow_tx, withdraw_all_tx):
        strategy.place_transaction(transaction)


assert asyncio.iscoroutinefunction(lend)
```

Protocol semantics:

* Deposits always become obligation collateral (Solend has no collateral
  toggle), so `enable_as_collateral` is ignored and
  `build_collateral_transaction` raises `NotImplementedError`.
* Only variable-rate borrowing exists; `interest_rate_mode` is accepted for
  contract compatibility and ignored.
* `withdraw_all` resolves the exact deposited collateral from the obligation
  account rather than relying on a sentinel: Solend's withdraw takes a
  **collateral (cToken)** amount, and the `u64::MAX` "full amount" sentinel is
  only documented for repay. A normal withdraw converts the requested
  liquidity amount to collateral units with the reserve's current exchange
  rate.
* `build_liquidation_transaction` is not implemented.
* `place_transaction` requires the bound wallet to be the transaction's fee
  payer and the message to be a legacy (non-versioned) one; it raises
  `ValueError` otherwise.

## Betting market abstraction

```text
BettingMarket (facade)
├── get_market(market_id)                → first protocol, or a named one
├── get_markets(category, status, ...)   → aggregated across protocols
├── get_user_positions(address, ...)     → aggregated across protocols
├── get_outcome_token_price(...)
├── get_buy_quote / get_sell_quote(...)  → (shares, cost) / (payout, fees)
├── buy_outcome_tokens(...)              → BUILD a buy
├── sell_outcome_tokens(...)             → BUILD a sell
└── redeem_winnings(market_id, address)  → BUILD a redemption
```

### Derived price clamping

Outcome-token prices are probabilities in the **open** interval (0, 1): an
order priced at exactly 1.0 or 0.0 is rejected by the protocols. When
`max_price` / `min_price` are omitted, the facade derives them from the current
price plus/minus `default_slippage_tolerance` and clamps the result into
`[MIN_DERIVED_OUTCOME_PRICE, MAX_DERIVED_OUTCOME_PRICE]` — the extremes of the
default 0.001 price tick. Without the clamp, a 0.99 outcome with 5 % slippage
would derive a price above 1.0. An **explicit** `max_price` / `min_price` is
forwarded untouched and may be rejected by the protocol.

```python
from decimal import Decimal

from blockchainpype.dapps.betting_market import (
    MAX_DERIVED_OUTCOME_PRICE,
    MIN_DERIVED_OUTCOME_PRICE,
)

assert MAX_DERIVED_OUTCOME_PRICE == Decimal("0.999")
assert MIN_DERIVED_OUTCOME_PRICE == Decimal("0.001")
```

## Polymarket (EVM / Polygon)

Polymarket's architecture is hybrid, and the integration mirrors it exactly.

**Off-chain trading.** Buy/sell orders are EIP-712-signed and posted to the
CLOB REST API — they are *not* on-chain transactions. The facade-facing
`build_buy_transaction` / `build_sell_transaction` sign the order and wrap it
in a tracking `EthereumTransaction` carrying the signed order under
`other_data["clob_order"]` (plus `market_id` and the `neg_risk` flag);
"broadcasting" such a transaction means posting it with `post_order()`.

**On-chain settlement.** `build_redeem_transaction` produces a genuine unsigned
`ConditionalTokens.redeemPositions` call, and the allowance helpers
(`approve_collateral`, `place_conditional_tokens_approval`) sign and broadcast
real transactions.

**Reads** are wallet-less and hit the real public APIs: CLOB
(`clob.polymarket.com`) for markets/books/prices, Gamma
(`gamma-api.polymarket.com`) for discovery/metadata, and the Data API
(`data-api.polymarket.com`) for user positions.

### CLOB orders

`ClobOrder` is the EIP-712 `Order` struct — the field order in
`ORDER_STRUCT_FIELDS` is authoritative, since any reordering changes the type
hash and breaks verification. The domain is
`name="Polymarket CTF Exchange"`, `version="1"`, with the exchange contract as
`verifyingContract`.

`compute_order_amounts(side, price, size, tick_size)` converts a (price, size)
pair into raw maker/taker amounts with the official rounding conventions: size
rounds **down** to the 0.01-share step, price rounds half-up to the market
tick, and the final USDC conversion rounds down so the maker can never be asked
to spend more than `price * size`. USDC and CTF outcome tokens both use 6
decimals. Prices outside (0, 1), unsupported tick sizes and sub-step sizes
raise `ValueError`.

```python
from decimal import Decimal

from blockchainpype.evm.dapp.betting_market.clob import (
    DEFAULT_TICK_SIZE,
    VALID_TICK_SIZES,
    OrderSide,
    build_hmac_signature,
    compute_order_amounts,
)

# BUY spends USDC for shares: (usdc_in, shares_out)
assert compute_order_amounts(OrderSide.BUY, Decimal("0.6"), Decimal("100")) == (
    60_000_000,
    100_000_000,
)
# SELL is the reverse: (shares_in, usdc_out)
assert compute_order_amounts(OrderSide.SELL, Decimal("0.6"), Decimal("100")) == (
    100_000_000,
    60_000_000,
)
assert DEFAULT_TICK_SIZE in VALID_TICK_SIZES

# L2 auth: HMAC-SHA256 over timestamp + method + path (+ body), base64url.
signature = build_hmac_signature(
    secret="c2VjcmV0LWtleQ==",
    timestamp="1700000000",
    method="POST",
    request_path="/order",
    body='{"order": {}}',
)
print(signature)
```

`ClobClient` serves the public read endpoints (`/markets`,
`/markets/{condition_id}`, `/book`, `/price`, `/midpoint`) without credentials.
`post_order` and `cancel_order` require `ClobCredentials` (api key, base64url
HMAC secret, passphrase) and attach the `POLY_*` L2 headers automatically. The
JSON body is serialized **once** and that exact string is both signed and sent,
so signature and payload can never drift apart. Transport failures and non-2xx
responses raise `ClobApiError` carrying the status and decoded payload.

### Neg-risk markets

Neg-risk markets — multi-outcome events whose outcomes are mutually exclusive —
settle on their **own** exchange, so their orders must be EIP-712-signed
against the NegRisk CTF Exchange (`0xC5d563A3…20f80a`) instead of the regular
CTF Exchange (`0x4bFb41d5…B8982E`). Signing against the wrong one produces a
signature the exchange rejects.

`verifying_contract(neg_risk)` returns the right address, and
`build_buy_transaction` / `build_sell_transaction` resolve the flag from the
market when `neg_risk` is not passed explicitly. The flag is read from the CLOB
market payload and **cached per condition id**, so markets already parsed via
`get_market()` / `get_markets()` cost no extra request. The lower-level
`place_buy` / `place_sell` take `neg_risk` as a plain argument (defaulting to
`False`).

### Usage

Through the facade (which resolves its blockchain from the `BlockchainFactory`
using the configuration's platform, so register it first):

```python
import asyncio
from decimal import Decimal

from blockchainpype.evm.blockchain.blockchain import EthereumBlockchain
from blockchainpype.evm.dapp.betting_market import (
    EVMBettingMarketConfiguration,
    PolymarketBettingMarket,
    PolymarketConfiguration,
)
from blockchainpype.evm.dapp.unsigned import unsigned_tx_params
from blockchainpype.evm.wallet.wallet import EthereumWallet

CONDITION_ID = "0x" + "ab" * 32
TOKEN_ID_YES = "1234567890"


async def trade(blockchain: EthereumBlockchain, wallet: EthereumWallet) -> None:
    market = PolymarketBettingMarket(
        EVMBettingMarketConfiguration(
            platform=blockchain.platform,
            protocols=[PolymarketConfiguration(fee_rate=Decimal("0.02"))],
            max_gas_price_gwei=50,
            default_slippage_tolerance=Decimal("0.01"),
        )
    )
    market.set_wallet(wallet)
    user = wallet.address.string

    # Reads
    detail = await market.get_market(CONDITION_ID)
    print(detail.title, detail.status, detail.is_active)
    price = await market.get_outcome_token_price(CONDITION_ID, TOKEN_ID_YES)
    shares, cost = await market.get_buy_quote(
        CONDITION_ID, TOKEN_ID_YES, Decimal("100")
    )
    print(price, shares, cost)

    # Build a buy: max_price omitted -> derived from the price + slippage and
    # clamped at 0.999. The result wraps the SIGNED CLOB ORDER PAYLOAD, not a
    # chain transaction.
    buy = await market.buy_outcome_tokens(
        market_id=CONDITION_ID,
        outcome_token_id=TOKEN_ID_YES,
        amount=Decimal("100"),
        user_address=user,
    )
    order_payload = buy.other_data["clob_order"]
    print(order_payload["side"], order_payload["makerAmount"], buy.other_data["neg_risk"])

    # Redemption IS a real on-chain transaction.
    redeem = await market.redeem_winnings(CONDITION_ID, user)
    wallet.sign_and_send_transaction(
        client_operation_id=redeem.client_operation_id,
        tx_data=dict(unsigned_tx_params(redeem)),
    )

    await market.close()  # release the CLOB / Gamma HTTP sessions


assert asyncio.iscoroutinefunction(trade)
```

Posting an order goes through the `Polymarket` strategy, which can be
constructed directly against a blockchain — it also carries the approval
helpers and the lower-level order API:

```python
import asyncio
from decimal import Decimal

from pydantic import SecretStr

from blockchainpype.evm.blockchain.blockchain import EthereumBlockchain
from blockchainpype.evm.dapp.betting_market import (
    ClobCredentials,
    OrderSide,
    Polymarket,
    PolymarketConfiguration,
)
from blockchainpype.evm.wallet.wallet import EthereumWallet

TOKEN_ID_YES = "1234567890"


async def post_orders(blockchain: EthereumBlockchain, wallet: EthereumWallet) -> None:
    strategy = Polymarket(
        PolymarketConfiguration(
            fee_rate=Decimal("0.02"),
            credentials=ClobCredentials(
                api_key="<uuid>",
                secret=SecretStr("<base64url-hmac-secret>"),
                passphrase="<passphrase>",
            ),
        ),
        blockchain,
        wallet=wallet,
        max_gas_price_gwei=50,
    )

    # Allowances: USDC for the exchange, then ERC-1155 operator approval
    # (required before SELL orders can settle). Both sign and broadcast.
    await strategy.approve_collateral(Decimal("1000"))
    await strategy.place_conditional_tokens_approval(True)

    # Sign and post in one step...
    response = await strategy.place_buy(
        TOKEN_ID_YES, price=Decimal("0.60"), size=Decimal("100"), neg_risk=False
    )
    print(response.success, response.order_id, response.error_msg)

    # ...or sign first and post later.
    signed = strategy.build_order(
        TOKEN_ID_YES, OrderSide.SELL, price=Decimal("0.62"), size=Decimal("100")
    )
    await strategy.post_order(signed, order_type="GTC")

    await strategy.close()


assert asyncio.iscoroutinefunction(post_orders)
```

Configuration knobs wired through:

* `ProtocolConfiguration.fee_rate` becomes `feeRateBps` on every order (the
  maximum fee rate the maker accepts) and is also used by the quote helpers.
* `BettingMarketConfiguration.max_gas_price_gwei` is forwarded by the facade
  and caps the gas-price fields of every transaction the strategy builds —
  including the ERC-20 approvals it delegates to `ERC20Contract`, via
  `GasPriceCappedConfiguration`.
* `PolymarketConfiguration.signature_type` selects the on-chain verification
  scheme (`EOA`, `POLY_PROXY`, `POLY_GNOSIS_SAFE`).

Before trading you generally need two approvals: `approve_collateral(amount)`
(USDC → CTF Exchange) and `place_conditional_tokens_approval(True)` (ERC-1155
operator approval, required before SELL orders can settle). Both sign and
broadcast immediately and honour the gas-price cap.

`build_redeem_transaction` redeems both binary outcome slots (index sets
`[1, 2]`) of the market's condition against the USDC collateral with the null
parent collection — `redeemPositions` pays out only for held balances, so
redeeming both slots is always safe. `market_id` must be the 0x-prefixed
32-byte condition id; anything else raises `ValueError`, as does a
`user_address` that does not match the bound wallet.

## See also

* [EVM guide](evm.md) — wallets, gas and the ERC-20 helpers these strategies
  build on.
* [Solana guide](solana.md) — programs, IDLs and SPL tokens behind Solend.
* [Testing guide](testing.md) — the Uniswap strategies are exercised
  end-to-end against a local Hardhat deployment.
