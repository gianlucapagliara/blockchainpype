# EVM guide

Everything under `blockchainpype.evm` targets Ethereum and EVM-compatible
chains through `web3.py` v7 async providers.

## Blockchain configuration

`EthereumBlockchainConfiguration` is the single object an `EthereumBlockchain`
is built from:

| Field | Type | Purpose |
| --- | --- | --- |
| `platform` | `BlockchainPlatform` | Identity: identifier, blockchain type, chain id, `testnet` / `local` flags |
| `connectivity` | `EthereumConnectivityConfiguration` | RPC provider, ENS, middleware, extra web3 modules |
| `native_asset` | `EthereumNativeAssetConfiguration` | Name/symbol/decimals of the native coin (defaults to ETH, 18 decimals) |
| `explorer` | `EtherscanConfiguration \| None` | Explorer links and ABI downloads |
| `gas_strategy` | `GasStrategy` | `EIP1559` (default) or `LEGACY` |

```python
from financepype.platforms.blockchain import BlockchainPlatform
from web3 import AsyncHTTPProvider

from blockchainpype.evm.blockchain.blockchain import (
    EthereumBlockchain,
    EthereumBlockchainType,
)
from blockchainpype.evm.blockchain.configuration import (
    EthereumBlockchainConfiguration,
    EthereumConnectivityConfiguration,
    EthereumNativeAssetConfiguration,
)
from blockchainpype.evm.blockchain.gas import GasStrategy
from blockchainpype.evm.explorer.etherscan import EtherscanConfiguration

configuration = EthereumBlockchainConfiguration(
    platform=BlockchainPlatform(
        identifier="polygon",
        type=EthereumBlockchainType,
        chain_id=137,
    ),
    native_asset=EthereumNativeAssetConfiguration(
        name="Polygon", symbol="POL", decimals=18
    ),
    connectivity=EthereumConnectivityConfiguration(
        rpc_provider=AsyncHTTPProvider("https://polygon-rpc.com"),
    ),
    explorer=EtherscanConfiguration(
        base_url="https://polygonscan.com",
        api_url="https://api.etherscan.io/v2/api",
        chain_id=137,
    ),
    gas_strategy=GasStrategy.EIP1559,
)
blockchain = EthereumBlockchain(configuration=configuration)
```

### Providers

Any `web3.providers.async_base.AsyncJSONBaseProvider` works. Two extra
providers ship with the library.

#### `MultipleHTTPProvider` — failover across endpoints

Keeps two pools: **retrieval** providers serve reads (balances, blocks,
receipts) and **execution** providers serve transaction broadcasts
(`eth_sendRawTransaction`, `eth_sendTransaction`), so sends can be pinned to a
private or MEV-protected relay.

Each request goes to the pool's current provider. On a connection-level failure
(`aiohttp.ClientError`, `OSError` — which covers `TimeoutError` — or web3's
`ProviderConnectionError`) the pool rotates to the next endpoint and retries,
up to `max_attempts` providers per request; the last error is raised when all
of them fail. Rotation is *sticky*: the next request starts from the last
healthy provider. Any other error (a JSON-RPC revert, a bad request) propagates
immediately without rotation.

```python
from web3 import AsyncHTTPProvider

from blockchainpype.evm.blockchain.providers import MultipleHTTPProvider

provider = MultipleHTTPProvider(
    retrieval_providers=[
        AsyncHTTPProvider("https://primary.example/rpc"),
        AsyncHTTPProvider("https://secondary.example/rpc"),
    ],
    execution_providers=[AsyncHTTPProvider("https://rpc.flashbots.net")],
    max_attempts=3,
)
print(provider.current_retrieval_provider.endpoint_uri)
```

Batched requests are routed to the execution pool when *any* request in the
batch is an execution method, otherwise to the retrieval pool.

#### `LimitedHTTPProvider` — client-side rate limiting

Throttles outgoing requests to `max_request_per_second` with a min-interval
scheduler: each request reserves the next free slot (slots are
`1 / max_request_per_second` apart) and sleeps until it is due, so bursts are
smoothed instead of dropped. Slot reservation is concurrency-safe. An optional
`max_request` caps the total number of requests over the provider's lifetime;
once exhausted, further requests raise `RuntimeError` instead of hitting the
endpoint. `provider.request_count` exposes the running total.

```python
from blockchainpype.evm.blockchain.providers import (
    LimitedHTTPProvider,
    MultipleHTTPProvider,
)

# The two compose: rate-limit every endpoint, then fail over between them.
provider = MultipleHTTPProvider(
    retrieval_providers=[
        LimitedHTTPProvider(url, max_request_per_second=5, max_request=100_000)
        for url in ("https://primary.example/rpc", "https://secondary.example/rpc")
    ]
)
```

Both raise `ValueError` on invalid settings: a non-positive
`max_request_per_second`, a `max_request` below 1, an empty retrieval pool, or
`max_attempts < 1`.

### Middleware, ENS and modules

`EthereumConnectivityConfiguration.middleware` follows web3's own convention:
`None` (the default) keeps the default middleware stack, while an explicit list
— including an empty one — **replaces** it entirely. `ens` binds an `AsyncENS`
instance to the web3 client, and `modules` / `external_modules` are forwarded
to the `AsyncWeb3` constructor.

```python
from web3 import AsyncHTTPProvider
from web3.middleware import ExtraDataToPOAMiddleware

from blockchainpype.evm.blockchain.configuration import (
    EthereumConnectivityConfiguration,
)

# Chains with >32-byte extraData (many PoA sidechains) need this middleware.
connectivity = EthereumConnectivityConfiguration(
    rpc_provider=AsyncHTTPProvider("https://your-poa-chain.example/rpc"),
    middleware=[ExtraDataToPOAMiddleware],
)
```

### Explorer

`EtherscanExplorer` builds transaction links and downloads verified ABIs
through the Etherscan **v2 multichain** API: when the configured `api_url`
contains `v2`, the configured `chain_id` is sent as the `chainid` query
parameter, so one API key serves every supported chain.

ABI download is proxy-aware. `fetch_contract_abi` first calls `getsourcecode`;
if the contract is flagged as a proxy it returns the inline
`ImplementationContractAbi` when present, otherwise it re-queries `getabi`
against the implementation address. That is what makes an ABI request for a
proxy such as USDC return the real token functions instead of the proxy's
fallback. Failures (HTTP errors, `status != "1"`, malformed JSON) surface as
`ValueError`.

```python
import asyncio

from blockchainpype.evm.blockchain.identifier import EthereumAddress
from blockchainpype.evm.explorer.etherscan import (
    EtherscanConfiguration,
    EtherscanExplorer,
)


async def main() -> None:
    explorer = EtherscanExplorer(EtherscanConfiguration())
    abi = await explorer.fetch_contract_abi(
        EthereumAddress.from_string("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48")
    )
    print(len(abi), "ABI entries")


asyncio.run(main())
```

## Identifiers

`EthereumAddress` normalizes to the EIP-55 checksummed form, so identifiers
built from differently-cased inputs of the same address compare equal.
`EthereumTransactionHash` (and its `EthereumBlockHash` subclass) validate the
32-byte length and normalize to the `0x`-prefixed lowercase hex form.
`EthereumNullAddress` is the zero address, used as the native asset's
identifier.

```python
from blockchainpype.evm.blockchain.identifier import (
    EthereumAddress,
    EthereumTransactionHash,
)

lower = EthereumAddress.from_string("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48")
mixed = EthereumAddress.from_string("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48")
assert lower == mixed
assert lower.string == "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"

assert EthereumTransactionHash.is_valid("0x" + "11" * 32)
assert not EthereumTransactionHash.is_valid("0x1234")
```

## Reading the chain

```python
import asyncio
from decimal import Decimal

from blockchainpype.evm.blockchain.identifier import (
    EthereumAddress,
    EthereumTransactionHash,
)
from blockchainpype.factory import BlockchainFactory


async def main() -> None:
    blockchain = BlockchainFactory.get_evm_blockchain_by_identifier("ethereum")
    address = EthereumAddress.from_string(
        "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"
    )

    block_number = await blockchain.fetch_block_number()
    timestamp = await blockchain.fetch_block_timestamp(block_number)
    nonce = await blockchain.fetch_transaction_count(address)

    # Decimal-adjusted (ETH, not wei).
    balance: Decimal = await blockchain.fetch_native_asset_balance(address)
    print(block_number, timestamp, nonce, balance)

    tx_hash = EthereumTransactionHash.from_string("0x" + "11" * 32)
    # Returns None when the node does not know the transaction.
    transaction = await blockchain.fetch_transaction(tx_hash)
    if transaction is not None:
        print(transaction.current_state, transaction.fee)


asyncio.run(main())
```

`fetch_transaction` reports a pending transaction as `BROADCASTED`, a mined one
as `CONFIRMED` or `FAILED` depending on the receipt status, and attaches the
paid fee plus an explorer link when an explorer is configured.
`fetch_transaction_receipt` and `fetch_raw_transaction` return `None` instead of
raising `TransactionNotFound`.

## Wallet lifecycle

### Nonce management

The wallet allocates nonces locally so several transactions can be signed
back-to-back without a round trip each:

* `await wallet.sync_nonce()` reads `eth_getTransactionCount` and stores it in
  `wallet.last_nonce`. Call it once before the first send.
* `wallet.allocate_nonce()` hands out `last_nonce` and increments it. It is
  synchronous (no awaits), so allocations are atomic within the event loop and
  concurrent callers always get distinct nonces. It returns `None` when the
  wallet has not been synced yet.
* On a rejected broadcast the wallet self-heals: if the rejection message
  mentions a nonce it re-syncs from the chain, otherwise it rolls the allocated
  nonce back — but only when it is still the most recently allocated one, so a
  later in-flight transaction can never end up with a duplicate.

### Building, signing and sending

```python
import asyncio
from datetime import timedelta
from decimal import Decimal

from web3.types import TxParams, Wei

from blockchainpype.evm.blockchain.identifier import EthereumAddress
from blockchainpype.evm.wallet.wallet import EthereumWallet


async def send(wallet: EthereumWallet) -> None:
    await wallet.sync_nonce()

    tx_params = await wallet.build_transaction(
        tx_data=TxParams(
            to=EthereumAddress.from_string(
                "0x5B38Da6a701c568545dCfcB03FcB875f56beddC4"
            ).raw,
            value=Wei(wallet.blockchain.native_asset.convert_to_raw(Decimal("0.5"))),
        )
    )

    transaction = wallet.sign_and_send_transaction(
        client_operation_id="payment-42",
        tx_data=dict(tx_params),
    )

    update = await wallet.get_transaction_update(
        transaction,
        timeout=timedelta(minutes=2),
        raise_timeout=False,
        poll_interval=1.0,
    )
    print(update.new_state, update.other_data.get("fee"))
```

* **`build_transaction(function=None, tx_data=None, gas_configuration=None)`**
  merges in `from` (the wallet address) and `chainId` (the platform chain id),
  encodes the call when a web3 `AsyncContractFunction` is passed, then adds the
  gas fields estimated by `gas_configuration` (the wallet's own by default)
  under the chain's `GasStrategy`.
* **`sign_and_send_transaction(...)`** is synchronous. It signs (allocating a
  nonce unless `auto_assign_nonce=False`), records the transaction in the
  wallet's tracker and schedules the broadcast as a background task on the
  running event loop. Calling it again with the `client_operation_id` of an
  already-signed transaction is an idempotent retry: the tracked transaction is
  returned unchanged, without re-signing or re-broadcasting.
* **`get_transaction_update(transaction, timeout, raise_timeout, **kwargs)`**
  polls `eth_getTransactionReceipt` every 2 s (override with
  `poll_interval`) until the receipt appears. Status 1 maps to `CONFIRMED`,
  status 0 to `FAILED`, and the paid fee lands in `update.other_data["fee"]` as
  a `BlockchainTransactionFee`. On timeout it either raises `TimeoutError` or —
  with `raise_timeout=False` — returns an update carrying the unchanged state.

### Speeding up and cancelling

Both are replacement transactions reusing the **original nonce** with bumped
fees, built by `modify_transaction`:

```python
from blockchainpype.evm.blockchain.identifier import EthereumTransactionHash
from blockchainpype.evm.wallet.wallet import EthereumWallet


async def rescue(wallet: EthereumWallet, tx_hash: EthereumTransactionHash) -> None:
    # Re-broadcast the same call with +13% fees.
    faster = await wallet.speedup_transaction(tx_hash, gas_increase_percentage=0.13)

    # Or replace it with a zero-value self-transfer, which cancels it.
    cancelled = await wallet.cancel_transaction(tx_hash)
    print(faster.client_operation_id, cancelled.client_operation_id)
```

`modify_transaction` re-reads the original transaction from the chain (raising
`ValueError` when it is unknown), keeps its nonce, `to`, `value` and calldata,
and bumps whichever fee fields it carries: EIP-1559 transactions get both
`maxFeePerGas` and `maxPriorityFeePerGas` increased, legacy ones get
`gasPrice`. A transaction with no fee information at all raises `ValueError`.
The percentage is applied through `Decimal`, so common values such as `0.12`
do not drift. `cancel_transaction` additionally overrides `to` with the
wallet's own address, `value` with 0, `data` with empty bytes and `gas` with
`gas_configuration.default_cancel_gas`.

### Balances

`wallet.fetch_balance(asset)` returns decimal-adjusted amounts for the native
asset and for `ERC20Token` assets (lazily initializing the token contract),
and raises `ValueError` for an asset of another platform or an unsupported
type. `wallet.update_balance(asset)` writes the result into the wallet's
balance tracker as both `TOTAL` and `AVAILABLE`. Adding assets with
`add_tracked_assets` schedules a refresh per new asset — but only when an event
loop is running, so the same call is safe from synchronous startup code.

## Gas configuration

`GasConfiguration` drives fee estimation:

| Field | Default | Meaning |
| --- | --- | --- |
| `gas_mode` | `GasMode.NORMAL` | Speed preference: `SLOW`, `NORMAL`, `FAST` |
| `n_blocks` | `10` | Blocks sampled by `eth_feeHistory` for the priority fee |
| `max_gas` | `800000` | Hard ceiling on the gas limit: both estimators clamp the buffered estimate to this value |
| `default_gas` | `800000` | Gas limit used when no transaction is given to estimate |
| `default_cancel_gas` | `800000` | Gas limit used by `cancel_transaction` |

The chain's `GasStrategy` selects the model:

**`GasStrategy.EIP1559`** (`estimate_eip1559_gas_fees`) reads the pending
block's `baseFeePerGas`, doubles it as a worst-case bound (the base fee can
grow at most 12.5 % per block, so 2× covers several blocks of inclusion delay),
samples `eth_feeHistory` over `n_blocks` at the percentiles of the selected
mode (`SLOW` → 10–50th, `NORMAL` → 30–90th, `FAST` → 70–90th), and averages the
rewards. It returns `gas` (the estimate × 1.3), `maxPriorityFeePerGas` (the
average reward) and `maxFeePerGas` (average reward + 2 × base fee). Estimation
is retried up to `n_max_retries` (3) times before the last error is re-raised.

**`GasStrategy.LEGACY`** (`estimate_legacy_gas_fees`) reads `eth_gasPrice` and
multiplies it by the mode's factor (`SLOW` 1.0, `NORMAL` 1.25, `FAST` 1.5),
returning `gas` (estimate × 1.3) and `gasPrice`.

In both cases the gas limit comes from `transaction_params["gas"]` when
present, otherwise from `eth_estimateGas`, otherwise from `default_gas`.
`GasConfiguration.max_gas_payable(fees)` computes the worst-case cost in wei
from either fee shape.

```python
from blockchainpype.evm.blockchain.gas import GasConfiguration, GasMode

fast = GasConfiguration(gas_mode=GasMode.FAST, n_blocks=20, default_gas=300_000)
assert GasConfiguration.max_gas_payable({"gas": 21_000, "gasPrice": 20_000_000_000}) == (
    21_000 * 20_000_000_000
)
```

### Capping the gas price

`GasPriceCappedConfiguration` inherits the estimation logic and clamps every
price-per-gas field (`gasPrice`, `maxFeePerGas`, `maxPriorityFeePerGas`) at a
maximum expressed in gwei. Because both EIP-1559 fields are clamped at the same
value, the `maxPriorityFeePerGas <= maxFeePerGas` invariant is preserved.

```python
from blockchainpype.evm.blockchain.gas import GasConfiguration
from blockchainpype.evm.dapp.gas import (
    GasPriceCappedConfiguration,
    cap_gas_price_fields,
)

capped = GasPriceCappedConfiguration.from_configuration(
    GasConfiguration(), max_gas_price_gwei=50
)

# The same clamping is available as a plain function.
fees = cap_gas_price_fields(
    {"gas": 21_000, "maxFeePerGas": 10**12, "maxPriorityFeePerGas": 10**11},
    max_gas_price_gwei=50,
)
assert fees["maxFeePerGas"] == 50 * 10**9
assert fees["gas"] == 21_000  # non-price fields are copied unchanged
```

Pass it wherever a gas configuration is accepted —
`wallet.build_transaction(gas_configuration=capped)` or the ERC-20 `place_*`
helpers. It is what the Polymarket strategy uses to honour
`BettingMarketConfiguration.max_gas_price_gwei`.

## Smart contracts and ABI sources

An `EthereumSmartContract` is built from an `EthereumContractConfiguration`
carrying the `platform`, the deployed `address` and an `abi_configuration`.
`await contract.initialize()` resolves the ABI and creates the underlying web3
`AsyncContract`; `contract.functions` is available afterwards and raises
`ValueError` before. Initialization is idempotent — `is_initialized` guards it.

Three ABI sources ship with the library:

| Source | Use it for |
| --- | --- |
| `EthereumLocalFileABI(file_name=..., folder_path=common_abi_path)` | Standard interfaces bundled in `common/abi`. Accepts both plain ABI arrays and Hardhat artifacts (`{"abi": [...]}`). No network, no API key. |
| `EthereumDictABI(abi=...)` | An ABI you already hold in memory. |
| `EthereumEtherscanABI(explorer=..., contract_address=..., request_timeout_seconds=10.0)` | Verified ABIs downloaded on demand, following proxies to the implementation. |

```python
import asyncio
from typing import cast

from blockchainpype.evm.blockchain.identifier import EthereumAddress
from blockchainpype.evm.dapp.abi import EthereumEtherscanABI, EthereumLocalFileABI
from blockchainpype.evm.dapp.contract import (
    EthereumContractConfiguration,
    EthereumSmartContract,
)
from blockchainpype.factory import BlockchainFactory


class ReadOnlyERC20(EthereumSmartContract):
    async def get_symbol(self) -> str:
        return cast(str, await self.functions.symbol().call())


async def main() -> None:
    blockchain = BlockchainFactory.get_evm_blockchain_by_identifier("ethereum")
    weth = EthereumAddress.from_string("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2")

    # Bundled ABI file: offline, no API key.
    local = ReadOnlyERC20(
        EthereumContractConfiguration(
            platform=blockchain.platform,
            address=weth,
            abi_configuration=EthereumLocalFileABI(file_name="ERC20.json"),
        )
    )
    await local.initialize()
    print(await local.get_symbol())

    # Remote ABI: follows proxies to the implementation contract.
    explorer = blockchain.explorer
    if explorer is not None:
        usdc = EthereumAddress.from_string(
            "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
        )
        remote = ReadOnlyERC20(
            EthereumContractConfiguration(
                platform=blockchain.platform,
                address=usdc,
                abi_configuration=EthereumEtherscanABI(
                    explorer=explorer,
                    contract_address=usdc,
                    request_timeout_seconds=15,
                ),
            )
        )
        await remote.initialize()
        print(await remote.get_symbol())


asyncio.run(main())
```

A contract resolves its blockchain through the global `OperatorFactory` using
`configuration.platform`. The Uniswap and Polymarket strategies instead use a
`BlockchainBoundContract` subclass that overrides `initialize_blockchain()` to
return an explicitly passed blockchain — that is how they work against a local
Hardhat node without any global registration.

The bundled ABI catalogue in `common/abi` includes ERC-20/721, the full
Uniswap V2 set, Uniswap V3 under `uniswap_v3/`, `aave_v3_pool.json`,
`aave_v3_data_provider.json`, `polymarket_conditional_tokens.json` and
`polymarket_ctf_exchange.json`. It ships inside the wheel (resolved by
`blockchainpype.common_abi_path`), so installed packages find it too.

## ERC-20 operations

`ERC20Contract` defaults its ABI to the bundled `ERC20.json` and layers
decimal-aware helpers on top of the raw calls. The token's `decimals` is
fetched once and cached.

| Read | Returns |
| --- | --- |
| `get_name()`, `get_symbol()`, `get_decimals()` | Token metadata |
| `get_total_supply()`, `get_balance_of(addr)`, `get_allowance(owner, spender)` | Decimal-adjusted amounts |
| `get_raw_total_supply()`, `get_raw_balance_of(addr)`, `get_raw_allowance(owner, spender)` | Untouched on-chain integers |

| Write (signs **and** broadcasts) | Call |
| --- | --- |
| `transfer` | `place_transfer(wallet, recipient, amount, client_operation_id=None, gas_configuration=None)` |
| `transferFrom` | `place_transfer_from(wallet, sender, recipient, amount, ...)` |
| `approve` | `place_approve(wallet, spender, amount, ...)` |

The signing wallet is always passed explicitly — the contract holds no wallet
reference. Each `place_*` lazily initializes the contract, syncs the wallet's
nonce when needed, converts the decimal `amount` to raw units and returns the
tracked `EthereumTransaction`. `gas_configuration` overrides the wallet's own
for that one transaction.

```python
import asyncio
from decimal import Decimal

from blockchainpype.evm.blockchain.gas import GasConfiguration
from blockchainpype.evm.blockchain.identifier import EthereumAddress
from blockchainpype.evm.dapp.erc20 import (
    ERC20Contract,
    ERC20ContractConfiguration,
    ERC20Token,
)
from blockchainpype.evm.dapp.gas import GasPriceCappedConfiguration
from blockchainpype.evm.wallet.wallet import EthereumWallet


async def approve_router(wallet: EthereumWallet) -> None:
    platform = wallet.blockchain.platform
    address = EthereumAddress.from_string(
        "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
    )
    token = ERC20Token(
        platform=platform,
        identifier=address,
        contract=ERC20Contract(
            ERC20ContractConfiguration(platform=platform, address=address)
        ),
    )
    await token.initialize_data()

    allowance = await token.contract.get_allowance(
        wallet.address,
        EthereumAddress.from_string("0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D"),
    )
    if allowance < Decimal("1000"):
        await token.contract.place_approve(
            wallet,
            spender=EthereumAddress.from_string(
                "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D"
            ),
            amount=Decimal("1000"),
            gas_configuration=GasPriceCappedConfiguration.from_configuration(
                GasConfiguration(), max_gas_price_gwei=40
            ),
        )


async def main(wallet: EthereumWallet) -> None:
    await approve_router(wallet)


if __name__ == "__main__":
    # Build the wallet as shown in the Quickstart, then:
    #     asyncio.run(main(wallet))
    print(asyncio.iscoroutinefunction(main))
```

`ERC20Token` is the asset wrapper: it carries the contract and, once
`initialize_data()` has run, the `EthereumAssetData` (name, symbol, decimals)
used by `convert_to_raw` / `convert_to_decimals`. Wallets track it like any
other asset, and the DEX and money-market strategies accept it directly.

## See also

* [DApps guide](dapps.md) — Uniswap, Aave V3 and Polymarket build on
  everything above.
* [Testing guide](testing.md) — the Hardhat harness runs this same EVM stack
  against a real local node.
