# Quickstart

This page takes you from an empty project to a signed transaction on both
chain families. Every snippet is a complete module — copy it, replace the
placeholder addresses and keys, and run it.

## 1. Install

Python **3.13 or later** is required.

```bash
uv add blockchainpype
```

```bash
pip install blockchainpype
```

## 2. Configure the blockchains

Two registries do the wiring:

* `BlockchainsInitializer` registers the blockchain **class** to use per
  blockchain type (`EthereumBlockchain` for EVM, `SolanaBlockchain` for Solana)
  and one **configuration** per platform in the `BlockchainFactory`.
* `WalletsInitializer` does the same for wallet classes (`EthereumWallet`,
  `SolanaWallet`) in the `WalletFactory`.

The stock configurations live in
`blockchainpype.initializer.BlockchainConfigurations` and point at public
endpoints. Subclassing it and overriding a single `*_configuration`
classmethod is the supported way to use your own RPCs: call `super()` first and
replace only the connectivity section, so platform identity, native asset and
explorer settings keep the library defaults.

```python
"""Register the blockchains and wallets this application talks to."""

from solana.rpc.async_api import AsyncClient

from blockchainpype.evm.blockchain.configuration import (
    EthereumBlockchainConfiguration,
    EthereumConnectivityConfiguration,
)
from blockchainpype.evm.blockchain.providers import (
    LimitedHTTPProvider,
    MultipleHTTPProvider,
)
from blockchainpype.factory import BlockchainFactory
from blockchainpype.initializer import (
    BlockchainConfigurations,
    BlockchainsInitializer,
    WalletsInitializer,
)
from blockchainpype.solana.blockchain.configuration import (
    SolanaBlockchainConfiguration,
    SolanaConnectivityConfiguration,
)

ETHEREUM_RPC_URLS = (
    "https://your-primary-node.example/rpc",
    "https://your-failover-node.example/rpc",
)
SOLANA_RPC_URL = "https://api.mainnet-beta.solana.com"


class MyConfigurations(BlockchainConfigurations):
    """Stock configurations with the RPC connectivity swapped out."""

    @classmethod
    def ethereum_configuration(cls) -> EthereumBlockchainConfiguration | None:
        config = super().ethereum_configuration()
        if config is None:
            return None
        return config.model_copy(
            update={
                "connectivity": EthereumConnectivityConfiguration(
                    rpc_provider=MultipleHTTPProvider(
                        retrieval_providers=[
                            LimitedHTTPProvider(url, max_request_per_second=5)
                            for url in ETHEREUM_RPC_URLS
                        ],
                        # Reads and broadcasts share the pool; pass an explicit
                        # list to pin sends to a private relay.
                        execution_providers=None,
                    )
                )
            }
        )

    @classmethod
    def solana_configuration(cls) -> SolanaBlockchainConfiguration | None:
        config = super().solana_configuration()
        if config is None:
            return None
        return config.model_copy(
            update={
                "connectivity": SolanaConnectivityConfiguration(
                    rpc_provider=AsyncClient(SOLANA_RPC_URL),
                )
            }
        )


def configure() -> None:
    """Register everything. Idempotent: known platforms are left alone."""
    BlockchainsInitializer.configure(configurations=MyConfigurations)
    WalletsInitializer.configure()


if __name__ == "__main__":
    configure()
    ethereum = BlockchainFactory.get_evm_blockchain_by_identifier("ethereum")
    solana = BlockchainFactory.get_solana_blockchain_by_identifier("solana")
    print(ethereum.platform.identifier, solana.platform.identifier)
```

Configuring is offline: it only builds provider objects, so no RPC call is made
until you actually read or write something.

The library ships three named configurations out of the box —
`ethereum` (chain id 1), `hardhat` (chain id 31337, local testnet) and
`solana`. Add your own by defining another `*_configuration` classmethod on
your subclass: every such method is discovered automatically. Also declare the
new name in `configuration_blockchain_types()` so that, when
`BlockchainsInitializer.configure(blockchain_types=[...])` filters by type, the
configuration can be skipped *before* it is built (and its RPC client
allocated); undeclared configurations are built first and filtered afterwards.

!!! note "Explorer API key"
    The stock Ethereum configuration reads `ETHERSCAN_API_KEY` from the
    environment and attaches it to the `EtherscanConfiguration`. It is optional
    — without it, explorer calls (ABI downloads) are anonymous and rate
    limited.

## 3. Build wallets

A wallet needs an identifier (platform + address, optionally a name) and — to
sign anything — a signer configuration holding the private key. Without a
signer the wallet is read-only: balances and transaction tracking still work.

You can either register configurations and let `WalletFactory` cache one
instance per identifier:

```python
"""Register wallet configurations and build them through the factory."""

from pydantic import SecretStr

from blockchainpype.evm.blockchain.identifier import EthereumAddress
from blockchainpype.evm.wallet.identifier import EthereumWalletIdentifier
from blockchainpype.evm.wallet.signer import EthereumSignerConfiguration
from blockchainpype.evm.wallet.wallet import (
    EthereumWallet,
    EthereumWalletConfiguration,
)
from blockchainpype.factory import BlockchainFactory, WalletFactory, WalletRegistry

ETHEREUM_ADDRESS = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"
ETHEREUM_PRIVATE_KEY = "0x<your-private-key>"


def build_wallet() -> EthereumWallet:
    blockchain = BlockchainFactory.get_evm_blockchain_by_identifier("ethereum")
    configuration = EthereumWalletConfiguration(
        identifier=EthereumWalletIdentifier(
            name="main-evm",
            platform=blockchain.platform,
            address=EthereumAddress.from_string(ETHEREUM_ADDRESS),
        ),
        signer=EthereumSignerConfiguration(
            private_key=SecretStr(ETHEREUM_PRIVATE_KEY)
        ),
    )

    identifier = configuration.identifier.identifier  # "ethereum:main-evm"
    if WalletRegistry.get(identifier) is None:
        WalletRegistry.register(configuration)

    wallet = WalletFactory.create(identifier)
    assert isinstance(wallet, EthereumWallet)
    return wallet
```

…or construct the wallet directly, passing the blockchain explicitly (handy in
tests and scripts, where nothing has to be globally registered):

```python
from blockchainpype.evm.wallet.wallet import EthereumWallet


def build_wallet_directly(blockchain, configuration) -> EthereumWallet:
    return EthereumWallet(configuration=configuration, blockchain=blockchain)
```

!!! warning "Never commit a private key"
    Read it from the environment or a git-ignored `.env` file. `SecretStr`
    keeps it out of logs and `repr()`, but it cannot protect a key that is in
    your repository.

## 4. EVM: send ETH and transfer an ERC-20

`wallet.build_transaction()` fills in the sender, the chain id and the gas
fields (estimated with the wallet's `GasConfiguration` and the chain's
`GasStrategy`). `wallet.sign_and_send_transaction()` is **synchronous**: it
signs, records the transaction in the wallet's tracker and schedules the
broadcast as a background task on the running event loop, so it must be called
from inside a coroutine.

```python
"""Send ETH and transfer an ERC-20, then wait for both receipts."""

import asyncio
from datetime import timedelta
from decimal import Decimal

from web3.types import TxParams, Wei

from blockchainpype.evm.blockchain.identifier import EthereumAddress
from blockchainpype.evm.dapp.erc20 import (
    ERC20Contract,
    ERC20ContractConfiguration,
    ERC20Token,
)
from blockchainpype.evm.wallet.wallet import EthereumWallet

RECIPIENT = "0x5B38Da6a701c568545dCfcB03FcB875f56beddC4"
USDC_ADDRESS = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"


async def send_native(wallet: EthereumWallet) -> None:
    blockchain = wallet.blockchain
    recipient = EthereumAddress.from_string(RECIPIENT)

    # Nonces are allocated locally; sync once before the first send.
    await wallet.sync_nonce()

    tx_params = await wallet.build_transaction(
        tx_data=TxParams(
            to=recipient.raw,
            value=Wei(blockchain.native_asset.convert_to_raw(Decimal("0.01"))),
        )
    )
    transaction = wallet.sign_and_send_transaction(
        client_operation_id="send-eth-1",
        tx_data=dict(tx_params),
    )

    update = await wallet.get_transaction_update(
        transaction, timeout=timedelta(minutes=2), raise_timeout=False
    )
    print(update.new_state, transaction.operator_operation_id)


async def transfer_erc20(wallet: EthereumWallet) -> None:
    blockchain = wallet.blockchain
    token_address = EthereumAddress.from_string(USDC_ADDRESS)
    token = ERC20Token(
        platform=blockchain.platform,
        identifier=token_address,
        contract=ERC20Contract(
            ERC20ContractConfiguration(
                platform=blockchain.platform,
                address=token_address,
            )
        ),
    )
    # Fetches name / symbol / decimals from the token contract.
    await token.initialize_data()

    balance = await token.contract.get_balance_of(wallet.address)
    print(f"balance: {balance} {token.data.symbol}")

    # Amounts are decimal-adjusted; the contract scales them by `decimals`.
    transaction = await token.contract.place_transfer(
        wallet,
        recipient=EthereumAddress.from_string(RECIPIENT),
        amount=Decimal("25.5"),
    )
    update = await wallet.get_transaction_update(
        transaction, timeout=timedelta(minutes=2), raise_timeout=False
    )
    print(update.new_state)


async def main() -> None:
    # build_wallet() is the helper from step 3.
    wallet = build_wallet()
    await send_native(wallet)
    await transfer_erc20(wallet)


if __name__ == "__main__":
    asyncio.run(main())
```

`place_transfer` (and its siblings `place_transfer_from` and `place_approve`)
initializes the contract, syncs the nonce when needed, builds the call through
the wallet and returns the tracked transaction — the whole ERC-20 write path in
one call. All read helpers (`get_balance_of`, `get_total_supply`,
`get_allowance`) return decimal-adjusted amounts; their `get_raw_*`
counterparts return the untouched on-chain integers.

## 5. Solana: check a balance and transfer an SPL token

Solana wallets take a base58 secret key. Transaction building is explicit: you
assemble instructions, wrap them in a message with a recent blockhash, and hand
the result to the wallet.

```python
"""Read SOL / SPL balances and transfer an SPL token."""

import asyncio
from datetime import timedelta
from decimal import Decimal

from pydantic import SecretStr
from solders.message import Message
from solders.system_program import TransferParams, transfer
from solders.transaction import Transaction

from blockchainpype.factory import BlockchainFactory
from blockchainpype.solana.asset import SolanaAssetData
from blockchainpype.solana.blockchain.identifier import SolanaAddress
from blockchainpype.solana.dapp.token import (
    SPLToken,
    SPLTokenProgram,
    SPLTokenProgramConfiguration,
)
from blockchainpype.solana.wallet.identifier import SolanaWalletIdentifier
from blockchainpype.solana.wallet.signer import SolanaSignerConfiguration
from blockchainpype.solana.wallet.wallet import SolanaWallet, SolanaWalletConfiguration

SOLANA_ADDRESS = "11111111111111111111111111111112"
SOLANA_PRIVATE_KEY = "<your-base58-keypair>"
RECIPIENT = "11111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def build_wallet() -> SolanaWallet:
    blockchain = BlockchainFactory.get_solana_blockchain_by_identifier("solana")
    return SolanaWallet(
        configuration=SolanaWalletConfiguration(
            identifier=SolanaWalletIdentifier(
                name="main-solana",
                platform=blockchain.platform,
                address=SolanaAddress.from_string(SOLANA_ADDRESS),
            ),
            signer=SolanaSignerConfiguration(
                private_key=SecretStr(SOLANA_PRIVATE_KEY)
            ),
        ),
        blockchain=blockchain,
    )


async def show_balances(wallet: SolanaWallet) -> None:
    blockchain = wallet.blockchain
    sol_balance = await blockchain.fetch_native_asset_balance(wallet.address)
    usdc_balance = await blockchain.fetch_spl_token_balance(
        wallet.address, SolanaAddress.from_string(USDC_MINT)
    )
    # A missing associated token account is reported as a zero balance.
    print(f"{sol_balance} SOL / {usdc_balance} USDC")


async def send_sol(wallet: SolanaWallet) -> None:
    blockchain = wallet.blockchain
    recipient = SolanaAddress.from_string(RECIPIENT)

    instruction = transfer(
        TransferParams(
            from_pubkey=wallet.address.raw,
            to_pubkey=recipient.raw,
            lamports=blockchain.native_asset.convert_to_raw(Decimal("0.1")),
        )
    )
    recent_blockhash = await blockchain.fetch_recent_blockhash()
    message = Message.new_with_blockhash(
        [instruction], wallet.address.raw, recent_blockhash
    )

    transaction = wallet.sign_and_send_transaction(
        client_operation_id="send-sol-1",
        transaction=Transaction.new_unsigned(message),
        recent_blockhash=recent_blockhash,
    )
    update = await wallet.get_transaction_update(
        transaction, timeout=timedelta(seconds=60), raise_timeout=False
    )
    print(update.new_state, transaction.operator_operation_id)


async def transfer_spl(wallet: SolanaWallet) -> None:
    blockchain = wallet.blockchain
    mint = SolanaAddress.from_string(USDC_MINT)

    program = SPLTokenProgram(
        SPLTokenProgramConfiguration(platform=blockchain.platform)
    )
    token = SPLToken(
        platform=blockchain.platform,
        identifier=mint,
        mint=mint,
        program=program,
        # Omit `data` to fetch the mint's decimals with initialize_data().
        data=SolanaAssetData(name="USD Coin", symbol="USDC", decimals=6),
    )

    # transferChecked between the two owners' associated token accounts.
    transaction = await program.place_transfer(
        wallet,
        token,
        destination=SolanaAddress.from_string(RECIPIENT),
        amount=Decimal("25.5"),
    )
    update = await wallet.get_transaction_update(
        transaction, timeout=timedelta(seconds=60), raise_timeout=False
    )
    print(update.new_state)


async def main() -> None:
    wallet = build_wallet()
    await show_balances(wallet)
    await send_sol(wallet)
    await transfer_spl(wallet)


if __name__ == "__main__":
    asyncio.run(main())
```

`SPLTokenProgram` resolves its blockchain through the `BlockchainFactory` using
the `platform` in its configuration, so run `BlockchainsInitializer.configure()`
(step 2) first.

## Next steps

* [EVM guide](guides/evm.md) — provider failover and rate limiting, gas
  strategies, ABI sources, the full wallet lifecycle.
* [Solana guide](guides/solana.md) — versioned transactions, ATAs, Anchor IDLs.
* [DApps guide](guides/dapps.md) — swap on Uniswap, lend on Aave or Solend,
  trade on Polymarket.
* [Testing guide](guides/testing.md) — how the library is tested, and how to
  test code that builds on it.
