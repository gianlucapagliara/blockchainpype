# Testing guide

The suite has two layers: fast, network-free **unit tests** that run by default,
and **integration tests** against a real local Hardhat node that are opted into
explicitly.

## Running the tests

```bash
# Unit tests (network- and integration-marked tests are skipped)
make test           # == uv run pytest

# Hardhat integration suite
make test-integration

# Lint + format check + mypy + unit tests
make check
```

Individual gates:

```bash
make lint          # uv run ruff check .
make format-check  # uv run ruff format --check .
make type-check    # uv run mypy --strict blockchainpype/
```

`mypy` runs in strict mode over the package (tests are excluded via
`[tool.mypy] exclude`), and CI additionally enforces `--cov-fail-under=80`.

## Test selection and markers

Two markers are declared in `pyproject.toml`:

| Marker | Meaning |
| --- | --- |
| `network` | Needs live network access (real RPC endpoints) |
| `integration` | Needs an external service (a Hardhat node, npm toolchain) |

The default selection comes from `addopts`, so plain `pytest` never touches the
network:

```toml
addopts = "-m 'not network and not integration'"
```

Override it on the command line to widen the selection:

```bash
uv run pytest -m ""                 # everything, including integration
uv run pytest -m integration        # only the integration tests
uv run pytest tests/evm/test_erc20.py -k allowance
```

Other relevant `pytest` settings: `asyncio_mode = "auto"` (async tests need no
decorator), `asyncio_default_fixture_loop_scope = "function"` and a global
30-second `timeout`.

## Unit-test philosophy

**Mock only at the RPC/HTTP boundary.** Everything above it — the blockchain
operator, wallet, contract, strategy and facade — is the real code under test.
Concretely:

* No production method is ever patched. Tests never replace the function they
  are meant to verify with a stub that returns the expected answer.
* Fake transports return **realistic payloads**: ABI-encoded `eth_call`
  results, complete post-London block and receipt objects, real protocol JSON.
  That keeps the decoding layer (web3 ABI decoding, tuple index maps, borsh
  parsing) inside the test's coverage.
* Assertions use **exact values** — the precise `Decimal`, the exact calldata
  selector and arguments, the exact number of raw units — instead of loose
  "is not None" checks.

### The fake JSON-RPC provider (EVM)

`tests/evm/test_wallet.py` defines a `FakeRPCProvider`, an
`AsyncJSONBaseProvider` that answers from a response table. Values can be a
plain result, a callable receiving the request params, an `Exception` to raise,
or a dict containing an `error` key (returned as a JSON-RPC error response).
Every call is recorded in `provider.calls`, and `calls_for(method)` retrieves
the params a method was invoked with. An unexpected method raises immediately,
so a test can never silently exercise an unstubbed code path.

Building a blockchain on top of it is exactly the production path, with the
provider swapped:

```python
from financepype.platforms.blockchain import BlockchainPlatform

from blockchainpype.evm.blockchain.blockchain import (
    EthereumBlockchain,
    EthereumBlockchainType,
)
from blockchainpype.evm.blockchain.configuration import (
    EthereumBlockchainConfiguration,
    EthereumConnectivityConfiguration,
    EthereumNativeAssetConfiguration,
)
from tests.evm.test_wallet import FakeRPCProvider


def build_blockchain() -> EthereumBlockchain:
    provider = FakeRPCProvider(
        {
            "eth_chainId": "0x1",
            "eth_getBalance": "0xde0b6b3a7640000",  # exactly 1 ETH
            "eth_getTransactionCount": "0x2",
        }
    )
    return EthereumBlockchain(
        configuration=EthereumBlockchainConfiguration(
            platform=BlockchainPlatform(
                identifier="ethereum",
                type=EthereumBlockchainType,
                chain_id=1,
            ),
            native_asset=EthereumNativeAssetConfiguration(),
            connectivity=EthereumConnectivityConfiguration(rpc_provider=provider),
            explorer=None,
        )
    )
```

Contract-level tests answer `eth_call` with a handler that dispatches on the
4-byte selector and encodes the reply with `eth_abi.encode`, so the real ABI
decoder runs:

```python
from typing import Any

from eth_abi import encode

SEL_DECIMALS = "0x313ce567"
SEL_BALANCE_OF = "0x70a08231"


def eth_call_handler(params: Any) -> str:
    call = params[0]
    data = str(call.get("data") or call.get("input"))
    selector = data[:10].lower()
    if selector == SEL_DECIMALS:
        return "0x" + encode(["uint8"], [6]).hex()
    if selector == SEL_BALANCE_OF:
        return "0x" + encode(["uint256"], [1_500_000]).hex()
    raise AssertionError(f"Unexpected eth_call selector: {selector}")
```

### Fake HTTP sessions

The Polymarket tests apply the same idea one layer up: a `FakeHttpSession`
answers `(method, path)` pairs with recorded CLOB / Gamma / Data-API payloads,
and is injected through the strategy's dependency-injection hooks
(`ClobClient(session=...)`, `Polymarket(..., clob_client=..., session=...)`)
rather than by patching `aiohttp`.

### Background tasks

`sign_and_send_transaction` schedules the broadcast as a background task, so
tests await a drain helper before asserting on the broadcast outcome — the
wallet keeps strong references to its in-flight tasks, which makes that
deterministic.

### Solana and dapp-layer tests

Solana tests stub the `AsyncClient` responses (account data, token balances,
signature statuses) and let the real binary parsers run over byte payloads laid
out exactly like the on-chain structs. The abstract dapp tests in
`tests/dapps/` build real `financepype` assets through
`tests/dapps/helpers.py` and register a blockchain for the test platform, so
the facades exercise their real dispatch logic.

## Hardhat integration environment

### One-time setup

The harness needs Node.js (CI uses Node 22) and the npm dependencies:

```bash
cd common/hardhat
npm install
```

Then run the suite from the repository root:

```bash
make test-integration
# == uv run pytest tests/evm/test_hardhat.py -m "" --timeout=300
```

`-m ""` clears the default marker filter; `--timeout` raises the 30 s global
limit, since starting a node and deploying contracts takes longer.

### What the harness does

`tests/evm/hardhat.py` provides `HardhatNode` and `HardhatTestEnvironment`:

1. Checks the Node.js version, then starts `npx hardhat node` on a **free port
   allocated dynamically**, so concurrent test sessions never clash on 8545.
2. Deploys the test contracts (`scripts/deploy-all.js`), writing the addresses
   to a session-private temporary file rather than the shared
   `deployments.json`.
3. Registers a `hardhat` platform in the `BlockchainFactory` pointing at that
   port — the library default hardcodes 8545, so the environment registers a
   `BlockchainConfigurations` subclass overriding just
   `hardhat_configuration()`, after dropping any stale registration.
4. Reads the deterministic test accounts (20 accounts funded with 10 000 ETH,
   chain id 31337).
5. On teardown, disconnects the provider and unregisters the platform so a
   later environment starts clean.

### Fixtures

| Fixture | Scope | What you get |
| --- | --- | --- |
| `hardhat_dir` | session | Path to `common/hardhat` |
| `hardhat_env` | session (session loop) | The running `HardhatTestEnvironment` |
| `blockchain` | function (session loop) | The `EthereumBlockchain` bound to the node |
| `test_accounts` | function (session loop) | The funded account addresses |
| `deployed_contracts` | function (session loop) | Contract name → deployed address |
| `blockchain_snapshot` | function (session loop) | Snapshots before the test and reverts after |

`HardhatTestEnvironment` also exposes `get_account_balance_wei`,
`get_account_balance`, `send_eth`, `mine_blocks`,
`set_next_block_timestamp`, `snapshot`, `revert_to_snapshot` and
`reset_node`; `get_hardhat_accounts()` / `get_hardhat_private_keys()` return
the deterministic account list and its keys for signing.

### The session event loop

All async fixtures run in the **session-scoped** event loop
(`loop_scope="session"`), so the node, the provider and web3's cached aiohttp
session all live on one loop. Tests using them must opt into the same loop, or
pytest-asyncio will run them on a fresh function-scoped loop and the awaits
will fail:

```python
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.timeout(120)]


class TestSomething:
    pytestmark = pytest.mark.asyncio(loop_scope="session")

    async def test_block_number(self, blockchain):
        assert await blockchain.fetch_block_number() >= 0

    async def test_transfer(self, hardhat_env, test_accounts):
        sender, receiver = test_accounts[0], test_accounts[1]
        before = await hardhat_env.get_account_balance_wei(receiver)
        await hardhat_env.send_eth(sender, receiver, 1.0)
        after = await hardhat_env.get_account_balance_wei(receiver)
        assert after == before + 10**18  # exact, in wei
```

Two integration modules ship with the library:
`tests/evm/test_hardhat.py` (node lifecycle, ETH transfers, mining, snapshots,
contract deployment and a real token swap through `SimpleV2Router`) and
`tests/evm/test_uniswap_hardhat_integration.py` (the whole stack end-to-end:
wallet lifecycle, `ERC20Contract` against the deployed TestToken, the
`UniswapV2` strategy against the deployed factory + router, and the
`UniswapDEX` facade configured for the local network). The latter cross-checks
its quotes both against the canonical constant-product formula and against the
router's own `getAmountsOut`/`getAmountsIn`, and state-changing tests take the
`blockchain_snapshot` fixture so the seeded deployment state is restored
afterwards. Both modules are marked `integration` and skipped by the default
run:

```bash
uv run pytest tests/evm/test_uniswap_hardhat_integration.py -m "" --timeout=300
```

### Forked networks

The Hardhat configuration also supports forking mainnet or an L2 (set
`INFURA_API_KEY`/`ALCHEMY_API_KEY` and `FORK_CHAIN`, or a chain-specific
`*_FORK_URL`), which lets integration tests run against real protocol
deployments. See `common/hardhat/README.md` and
`common/hardhat/MULTI_CHAIN_FORKING.md`.

## Testing your own code

The same building blocks work for applications built on the library:

* Construct blockchains and wallets **explicitly**
  (`EthereumWallet(configuration=..., blockchain=...)`,
  `UniswapDEX(blockchain, configuration, wallet=wallet)`) so no global registry
  state leaks between tests. `BlockchainFactory.reset()`,
  `WalletFactory.reset()` and `WalletRegistry.reset()` clear the registries
  when you do rely on them.
* Swap in a fake provider or a fake HTTP session at the transport boundary and
  keep the rest of the stack real.
* Mark anything that reaches a live endpoint with `network`, and anything
  needing a node or another service with `integration`, so the default run
  stays fast and offline.

## See also

* [EVM guide](evm.md) and [Solana guide](solana.md) — the APIs under test.
* [DApps guide](dapps.md) — protocol strategies and their build-only contract.
