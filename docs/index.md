# BlockchainPype

A Python library for interacting with multiple blockchain networks through one
typed, asynchronous interface. It builds on
[financepype](https://github.com/gianlucapagliara/financepype)'s operator model:
blockchains and wallets are *operators* created through factories, assets carry
their own decimal metadata, and every write path produces a trackable
transaction object.

Two chain families are supported today:

* **EVM** — Ethereum and any EVM-compatible chain (Polygon, BSC, L2s, local
  Hardhat nodes), through `web3.py` v7 async providers.
* **Solana** — through `solana-py` / `solders`.

## Feature matrix

| Capability | EVM | Solana |
| --- | --- | --- |
| Blockchain operator | `EthereumBlockchain` | `SolanaBlockchain` |
| RPC transport | `AsyncHTTPProvider`, plus bundled `MultipleHTTPProvider` (failover) and `LimitedHTTPProvider` (rate limiting) | `solana.rpc.async_api.AsyncClient` |
| Wallets | `EthereumWallet` — nonce management, sign & send, speed-up, cancel, receipt polling | `SolanaWallet` — legacy + versioned (v0) signing, sign & send, status polling |
| Signing | `eth-account` `LocalAccount` from a `SecretStr` private key | `solders` `Keypair` from a base58 secret |
| Native asset | ETH (18 decimals), decimal-adjusted balances | SOL (9 decimals), lamport conversion |
| Fungible tokens | ERC-20 (`ERC20Contract` / `ERC20Token`): balances, allowances, `transfer`, `transferFrom`, `approve` | SPL (`SPLTokenProgram` / `SPLToken`): ATA derivation, `transferChecked`, balances |
| Gas / fees | `GasConfiguration` with EIP-1559 and legacy strategies, slow/normal/fast modes, `GasPriceCappedConfiguration` | Fee reported from the transaction receipt |
| Contracts / programs | `EthereumSmartContract` with ABI from a local file, an in-memory dict, or Etherscan (proxy-aware) | `SolanaProgram` with IDL from a local file or dict, Anchor discriminators + borsh encoding |
| Explorer | `EtherscanExplorer` — transaction links and ABI download (v2 multichain API) | `SolscanExplorer` — transaction / account / token / block links |
| Transaction model | `EthereumTransaction` + receipt parsing, fee computation | `SolanaTransaction` + receipt parsing, confirmation status mapping |

## Protocol integrations

| Protocol | Chain | What is implemented |
| --- | --- | --- |
| **Uniswap V2** | EVM | On-chain pair discovery, exact constant-product integer quoting, `swapExactTokensForTokens` / `swapTokensForExactTokens` building, multi-hop paths |
| **Uniswap V3** | EVM | Quoter-driven quoting across fee tiers, `exactInputSingle` / `exactOutputSingle`, packed-path `exactInput` / `exactOutput` multi-hop, pool reserve approximation |
| **Uniswap facade** | EVM | `UniswapDEX` — quote across protocols, `find_best_route` with configurable intermediates, pool discovery, presets for Ethereum/Polygon and local networks |
| **Aave V3** | EVM | `supply`, `withdraw` (incl. withdraw-all), `borrow`, `repay` (incl. repay-all), collateral toggle, liquidation call, market data, user account data, lending/borrowing positions, health factor |
| **Polymarket** | EVM (Polygon) | CLOB REST client, EIP-712 order signing (regular **and** neg-risk exchanges), L2 HMAC auth, market/position reads via CLOB + Gamma + Data API, on-chain `redeemPositions`, USDC and ERC-1155 approvals |
| **Solend** | Solana | Real instruction building (deposit, withdraw, borrow, repay) with the exact on-chain account layouts, the required `RefreshReserve`/`RefreshObligation` instructions, binary parsers for the `Reserve` and `Obligation` accounts, market/account/position reads |

Protocol-agnostic abstractions sit above these: `DecentralizedExchange`,
`MoneyMarket` and `BettingMarket` facades dispatch to per-protocol strategies
that satisfy a runtime-checkable `ProtocolImplementation` contract. See the
[DApps guide](guides/dapps.md).

## Other utilities

* **IPFS helpers** (`blockchainpype.ipfs`) — convert `ipfs://` URIs (or bare
  CIDs) into HTTP gateway URLs and download their content, with the gateway
  configurable per call or through the `IPFS_GATEWAY` environment variable.
* **Bundled ABIs and IDLs** — `common/abi` (ERC-20/721, Uniswap V2 and V3,
  Aave V3, Polymarket ConditionalTokens and CTF Exchange, …) and `common/idl`
  (Solend, Jupiter DCA) ship inside the wheel and are resolved automatically by
  `EthereumLocalFileABI` / `SolanaLocalFileIDL`.
* **Hardhat integration test harness** — a session-scoped local node with
  contract deployment, snapshots and time control (see the
  [Testing guide](guides/testing.md)).

## Installation

Python **3.13 or later** is required.

```bash
uv add blockchainpype
```

```bash
pip install blockchainpype
```

## Next steps

* [Quickstart](quickstart.md) — configure a chain, build a wallet, send your
  first transactions.
* [EVM guide](guides/evm.md) — providers, gas, contracts, ERC-20.
* [Solana guide](guides/solana.md) — versioned transactions, SPL tokens, IDLs.
* [DApps guide](guides/dapps.md) — Uniswap, Aave, Polymarket, Solend.
* [Testing guide](guides/testing.md) — unit-test philosophy and the Hardhat
  integration suite.

## Links

* [GitHub repository](https://github.com/gianlucapagliara/blockchainpype)
* [PyPI package](https://pypi.org/project/blockchainpype/)
* [Issue tracker](https://github.com/gianlucapagliara/blockchainpype/issues)
