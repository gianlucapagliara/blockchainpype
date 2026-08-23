# Blockchain Pypeline

A Python library for interacting with multiple blockchain networks, providing a
unified, typed and asynchronous interface for EVM-compatible chains and Solana.
It covers wallets, transactions, gas, block explorers and a set of real DeFi
protocol integrations.

📖 **Documentation: <https://gianlucapagliara.github.io/blockchainpype/>**

## Overview

Blockchain Pypeline builds on [financepype](https://github.com/gianlucapagliara/financepype)'s
operator model: blockchains and wallets are operators created through
factories, assets carry their own decimal metadata, and every write path
returns a trackable transaction object. On top of that it provides:

- One interface across EVM chains (via `web3.py` v7 async providers) and Solana
  (via `solana-py` / `solders`)
- Type-safe interaction with smart contracts and programs, including ABI/IDL
  sourcing and Anchor discriminator + borsh encoding
- Wallet management with nonce handling, transaction signing, replacement and
  receipt/confirmation polling
- Protocol-agnostic DEX, money-market and betting-market abstractions with
  concrete implementations for Uniswap, Aave, Polymarket and Solend

## Features

- **Multi-chain support**
  - EVM-compatible chains: Ethereum, Polygon, BSC, L2s, local Hardhat nodes
  - Solana, including versioned (v0) transactions and address-table lookups
  - Extensible: register your own chain configurations through
    `BlockchainConfigurations`

- **Connectivity**
  - Any `web3.py` async provider, plus a bundled `MultipleHTTPProvider`
    (endpoint failover with separate read/broadcast pools) and
    `LimitedHTTPProvider` (client-side rate limiting and request budgets)
  - Configurable middleware, ENS and extra web3 modules

- **Wallet management**
  - Local nonce allocation with automatic re-sync and rollback on rejection
  - Transaction building, signing, broadcasting and tracking
  - Speed-up and cancel via fee-bumped replacement transactions (EVM)
  - Legacy and versioned transaction signing (Solana)
  - Private keys held in `SecretStr`; wallets without a signer are read-only

- **Asset operations**
  - Native ETH / SOL balances in decimal units
  - ERC-20: balances, allowances, `transfer`, `transferFrom`, `approve`
  - SPL: associated-token-account derivation, `transferChecked`, balances

- **Transaction handling**
  - Gas estimation with EIP-1559 and legacy strategies, slow/normal/fast
    modes and a gas-price cap
  - Receipt and confirmation-status polling with exact state mapping
  - Explorer integration: Etherscan (links + proxy-aware ABI download over the
    v2 multichain API) and Solscan (link building)

- **DApp integrations**
  - **Uniswap V2 & V3** — quoting across protocols and fee tiers, multi-hop
    routing (`find_best_route`), single-hop and packed-path swap building
  - **Aave V3** — supply, withdraw, borrow, repay, collateral toggle,
    liquidation, market/account data, positions and health factor
  - **Polymarket** — CLOB REST client with EIP-712 order signing (regular and
    neg-risk exchanges), L2 HMAC auth, market/position reads, on-chain
    `redeemPositions` and allowances
  - **Solend** — real instruction building with the exact on-chain account
    layouts, required refresh instructions, and binary `Reserve`/`Obligation`
    parsers

- **Utilities**
  - IPFS helpers: `ipfs://` URI → HTTP gateway URL, and content download
  - Bundled ABI and IDL assets, shipped inside the wheel
  - A Hardhat integration-test harness with a session-scoped local node,
    contract deployment, snapshots and time control

## Installation

The package requires Python 3.13 or later.

```bash
uv add blockchainpype
```

Or with pip:

```bash
pip install blockchainpype
```

## Quick Start

Configure a chain, build a wallet and transfer an ERC-20 token:

```python
import asyncio
from datetime import timedelta
from decimal import Decimal

from pydantic import SecretStr
from web3 import AsyncHTTPProvider

from blockchainpype.evm.blockchain.configuration import (
    EthereumBlockchainConfiguration,
    EthereumConnectivityConfiguration,
)
from blockchainpype.evm.blockchain.identifier import EthereumAddress
from blockchainpype.evm.dapp.erc20 import (
    ERC20Contract,
    ERC20ContractConfiguration,
    ERC20Token,
)
from blockchainpype.evm.wallet.identifier import EthereumWalletIdentifier
from blockchainpype.evm.wallet.signer import EthereumSignerConfiguration
from blockchainpype.evm.wallet.wallet import (
    EthereumWallet,
    EthereumWalletConfiguration,
)
from blockchainpype.factory import BlockchainFactory
from blockchainpype.initializer import (
    BlockchainConfigurations,
    BlockchainsInitializer,
    WalletsInitializer,
)

USDC_ADDRESS = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
RECIPIENT = "0x5B38Da6a701c568545dCfcB03FcB875f56beddC4"


class MyConfigurations(BlockchainConfigurations):
    """Stock configurations with the RPC endpoint swapped out."""

    @classmethod
    def ethereum_configuration(cls) -> EthereumBlockchainConfiguration | None:
        config = super().ethereum_configuration()
        if config is None:
            return None
        return config.model_copy(
            update={
                "connectivity": EthereumConnectivityConfiguration(
                    rpc_provider=AsyncHTTPProvider("https://your-node.example/rpc"),
                )
            }
        )


async def main() -> None:
    BlockchainsInitializer.configure(configurations=MyConfigurations)
    WalletsInitializer.configure()
    blockchain = BlockchainFactory.get_evm_blockchain_by_identifier("ethereum")

    wallet = EthereumWallet(
        configuration=EthereumWalletConfiguration(
            identifier=EthereumWalletIdentifier(
                name="main",
                platform=blockchain.platform,
                address=EthereumAddress.from_string(
                    "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"
                ),
            ),
            signer=EthereumSignerConfiguration(
                private_key=SecretStr("0x<your-private-key>")
            ),
        ),
        blockchain=blockchain,
    )

    token_address = EthereumAddress.from_string(USDC_ADDRESS)
    usdc = ERC20Token(
        platform=blockchain.platform,
        identifier=token_address,
        contract=ERC20Contract(
            ERC20ContractConfiguration(
                platform=blockchain.platform, address=token_address
            )
        ),
    )
    await usdc.initialize_data()  # name / symbol / decimals from the chain

    balance = await usdc.contract.get_balance_of(wallet.address)
    print(f"balance: {balance} {usdc.data.symbol}")

    # Amounts are decimal-adjusted; the contract scales them by `decimals`.
    transaction = await usdc.contract.place_transfer(
        wallet,
        recipient=EthereumAddress.from_string(RECIPIENT),
        amount=Decimal("25.5"),
    )
    update = await wallet.get_transaction_update(
        transaction, timeout=timedelta(minutes=2), raise_timeout=False
    )
    print(update.new_state, transaction.operator_operation_id)


if __name__ == "__main__":
    asyncio.run(main())
```

More: the [Quickstart](https://gianlucapagliara.github.io/blockchainpype/quickstart/)
covers Solana too, and the guides cover
[EVM](https://gianlucapagliara.github.io/blockchainpype/guides/evm/),
[Solana](https://gianlucapagliara.github.io/blockchainpype/guides/solana/),
[DApps](https://gianlucapagliara.github.io/blockchainpype/guides/dapps/) and
[Testing](https://gianlucapagliara.github.io/blockchainpype/guides/testing/).
Runnable examples live in [`examples/`](examples).

## Development

### Setup

1. Clone the repository:
```bash
git clone https://github.com/gianlucapagliara/blockchainpype.git
cd blockchainpype
```

2. Install dependencies and set up pre-commit hooks (requires [uv](https://docs.astral.sh/uv/)):
```bash
make install
```

### Testing

Run the test suite (network- and integration-marked tests are skipped by default):

```bash
make test
```

Run the Hardhat integration suite (requires Node.js and `npm install` in `common/hardhat`):

```bash
make test-integration
```

### Code Quality

The project uses several tools to maintain code quality:
- ruff for linting and code formatting
- mypy for static type checking
- pre-commit hooks for automated checks

### Documentation

The documentation is built with MkDocs from the `docs/` directory:

```bash
make docs-serve   # live-reloading local preview
make docs-build   # build the static site into site/
```

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.
