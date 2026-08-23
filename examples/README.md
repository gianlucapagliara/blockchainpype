# Examples

Runnable examples for `blockchainpype`. Every module is safe to import — all
side effects (RPC calls, wallet construction, node startup) live inside an
explicit `main()`, which is what `tests/test_examples.py` enforces.

Run them from the repository root:

```bash
uv run python -m examples.basic.configure
```

| Example | What it shows | Needs |
| --- | --- | --- |
| `basic/configure.py` | `BlockchainsInitializer` + `BlockchainFactory`, custom RPC endpoints with failover | nothing |
| `basic/configure_wallets.py` | `WalletsInitializer`, `WalletRegistry`, `WalletFactory`, tracked assets | nothing |
| `contract_initialization_example.py` | `EthereumSmartContract` with a local-file ABI (offline) or an Etherscan ABI | RPC for the reads, `ETHERSCAN_API_KEY` for the remote ABI |
| `uniswap_example.py` | `UniswapDEX` quoting, multi-hop routing, reserves, building a swap transaction | Ethereum RPC (mainnet) |
| `money_market_example.py` | Aave V3 market data, account health, unsigned supply/borrow calls | Ethereum RPC (mainnet) |
| `betting_market_example.py` | Polymarket market reads and offline EIP-712 order signing (nothing is posted) | Polymarket public APIs |
| `solana_example.py` | Solana wallet, associated token accounts, `transferChecked` instruction | Solana RPC for the balances |
| `hardhat_testing_demo.py` | The local Hardhat environment: ETH transfer and a real swap through the library | Node.js + `npm install` in `common/hardhat` |

## Configuration

Environment variables are read at call time; a `.env` file in the working
directory is picked up by `examples.basic.configure.load_environment()` (real
environment variables win).

| Variable | Used by | Meaning |
| --- | --- | --- |
| `ETHEREUM_RPC_URLS` | every EVM example | Comma-separated mainnet endpoints; the first serves reads, the rest are failover |
| `ETHERSCAN_API_KEY` | contract initialization | Authenticates explorer/ABI downloads |
| `ETHEREUM_PRIVATE_KEY` | uniswap, money market | Optional; enables the transaction-building steps |
| `ETHEREUM_WALLET_ADDRESS` | wallet configuration | Optional; a read-only address to inspect |
| `SOLANA_RPC_URL` | solana | Solana endpoint |
| `SOLANA_PRIVATE_KEY` | solana, wallet configuration | Optional base58 keypair string |
| `SOLANA_WALLET_ADDRESS` | wallet configuration | Optional; a read-only address to inspect |
| `POLYGON_RPC_URL` | betting market | Polygon endpoint for the Polymarket contracts |

Never commit a private key. The examples that can sign either take the key from
the environment or generate a throwaway one at runtime.
