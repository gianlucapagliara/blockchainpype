"""Register wallets and build them through the ``WalletFactory``.

The wallet subsystem mirrors the blockchain one:

* :class:`~blockchainpype.initializer.WalletsInitializer` registers the wallet
  class to use per blockchain type (``EthereumWallet`` for EVM,
  ``SolanaWallet`` for Solana). It ships with the library — there is no need to
  hand-roll it.
* :class:`~blockchainpype.factory.WalletRegistry` stores wallet configurations
  by identifier (``"<platform>:<name or address>"``).
* :class:`~blockchainpype.factory.WalletFactory` turns a registered
  configuration into a wallet instance, caching one instance per identifier.

A wallet without a signer configuration is read-only (balances, transaction
tracking); adding an :class:`EthereumSignerConfiguration` /
:class:`SolanaSignerConfiguration` is what enables signing.

Environment variables (all optional — public placeholder addresses are used
when they are unset, which keeps this example read-only):

* ``ETHEREUM_WALLET_ADDRESS`` / ``ETHEREUM_PRIVATE_KEY``
* ``SOLANA_WALLET_ADDRESS`` / ``SOLANA_PRIVATE_KEY`` (base58 keypair string)

Never commit a private key: pass it through the environment (or a ``.env`` file
that is git-ignored).

Building a wallet performs no I/O **as long as it happens outside a running
event loop**: ``add_tracked_assets`` only schedules balance refreshes when a
loop is running, which is why ``main()`` here is deliberately synchronous.

Run it with::

    uv run python -m examples.basic.configure_wallets
"""

from __future__ import annotations

import os
from datetime import timedelta

from financepype.owners.wallet import BlockchainWallet
from pydantic import SecretStr

from blockchainpype.evm.blockchain.blockchain import (
    EthereumBlockchain,
    EthereumBlockchainType,
)
from blockchainpype.evm.blockchain.identifier import EthereumAddress
from blockchainpype.evm.dapp.erc20 import (
    ERC20Contract,
    ERC20ContractConfiguration,
    ERC20Token,
)
from blockchainpype.evm.wallet.identifier import EthereumWalletIdentifier
from blockchainpype.evm.wallet.signer import EthereumSignerConfiguration
from blockchainpype.evm.wallet.wallet import EthereumWalletConfiguration
from blockchainpype.factory import BlockchainFactory, WalletFactory, WalletRegistry
from blockchainpype.initializer import WalletsInitializer
from blockchainpype.solana.blockchain.blockchain import SolanaBlockchainType
from blockchainpype.solana.blockchain.identifier import SolanaAddress
from blockchainpype.solana.wallet.identifier import SolanaWalletIdentifier
from blockchainpype.solana.wallet.signer import SolanaSignerConfiguration
from blockchainpype.solana.wallet.wallet import SolanaWalletConfiguration
from examples.basic.configure import configure_blockchains, load_environment

ETHEREUM_ADDRESS_ENV = "ETHEREUM_WALLET_ADDRESS"
ETHEREUM_PRIVATE_KEY_ENV = "ETHEREUM_PRIVATE_KEY"
SOLANA_ADDRESS_ENV = "SOLANA_WALLET_ADDRESS"
SOLANA_PRIVATE_KEY_ENV = "SOLANA_PRIVATE_KEY"

#: Well-known public address (vitalik.eth), used as a read-only stand-in.
DEFAULT_ETHEREUM_ADDRESS = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"
#: The Solana System Program address: a valid pubkey with no private key.
DEFAULT_SOLANA_ADDRESS = "11111111111111111111111111111111"

ETHEREUM_WALLET_NAME = "main-evm"
SOLANA_WALLET_NAME = "main-solana"

#: Tracked alongside the native asset, to show how ``tracked_assets`` is used.
USDC_ETHEREUM_ADDRESS = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"


def build_erc20_token(blockchain: EthereumBlockchain, address: str) -> ERC20Token:
    """Build an ERC-20 asset handle for a token contract.

    Only the address is needed upfront: name, symbol and decimals are fetched
    from the chain by ``await token.initialize_data()`` (or by the first wallet
    balance refresh), so building the handle stays offline.
    """
    token_address = EthereumAddress.from_string(address)
    return ERC20Token(
        platform=blockchain.platform,
        identifier=token_address,
        contract=ERC20Contract(
            ERC20ContractConfiguration(
                platform=blockchain.platform,
                address=token_address,
            )
        ),
    )


def build_ethereum_wallet_configuration() -> EthereumWalletConfiguration:
    """Build the Ethereum wallet configuration from the environment.

    Returns:
        EthereumWalletConfiguration: A configuration for the ``ethereum``
        platform, carrying a signer only when ``ETHEREUM_PRIVATE_KEY`` is set.
    """
    blockchain = BlockchainFactory.get_evm_blockchain_by_identifier("ethereum")

    address = os.getenv(ETHEREUM_ADDRESS_ENV, DEFAULT_ETHEREUM_ADDRESS)
    private_key = os.getenv(ETHEREUM_PRIVATE_KEY_ENV)

    return EthereumWalletConfiguration(
        identifier=EthereumWalletIdentifier(
            name=ETHEREUM_WALLET_NAME,
            platform=blockchain.platform,
            address=EthereumAddress.from_string(address),
        ),
        signer=(
            EthereumSignerConfiguration(private_key=SecretStr(private_key))
            if private_key
            else None
        ),
        # The native asset is always tracked; extra assets are opt-in.
        tracked_assets={build_erc20_token(blockchain, USDC_ETHEREUM_ADDRESS)},
        default_tx_wait=timedelta(minutes=2),
    )


def build_solana_wallet_configuration() -> SolanaWalletConfiguration:
    """Build the Solana wallet configuration from the environment.

    Returns:
        SolanaWalletConfiguration: A configuration for the ``solana`` platform,
        carrying a signer only when ``SOLANA_PRIVATE_KEY`` is set.
    """
    blockchain = BlockchainFactory.get_solana_blockchain_by_identifier("solana")

    address = os.getenv(SOLANA_ADDRESS_ENV, DEFAULT_SOLANA_ADDRESS)
    private_key = os.getenv(SOLANA_PRIVATE_KEY_ENV)

    return SolanaWalletConfiguration(
        identifier=SolanaWalletIdentifier(
            name=SOLANA_WALLET_NAME,
            platform=blockchain.platform,
            address=SolanaAddress.from_string(address),
        ),
        signer=(
            SolanaSignerConfiguration(private_key=SecretStr(private_key))
            if private_key
            else None
        ),
        default_tx_wait=timedelta(seconds=30),
    )


def configure_wallets() -> list[BlockchainWallet]:
    """Register the wallet classes and configurations, then build the wallets.

    Idempotent: configurations already present in the registry are left alone
    and the factory returns its cached instance for a known identifier.

    Returns:
        list[BlockchainWallet]: The Ethereum wallet followed by the Solana one.
    """
    configure_blockchains()
    WalletsInitializer.configure()

    wallets: list[BlockchainWallet] = []
    for configuration in (
        build_ethereum_wallet_configuration(),
        build_solana_wallet_configuration(),
    ):
        identifier = configuration.identifier.identifier
        if WalletRegistry.get(identifier) is None:
            WalletRegistry.register(configuration)
        wallets.append(WalletFactory.create(identifier))
    return wallets


def main() -> None:
    """Configure the wallets and print what was registered."""
    load_environment()
    wallets = configure_wallets()

    print("=== Registered wallet classes ===")
    for blockchain_type in (EthereumBlockchainType, SolanaBlockchainType):
        wallet_class = WalletFactory.get_wallet_class(blockchain_type)
        name = wallet_class.__name__ if wallet_class is not None else "<none>"
        print(f"- {blockchain_type.value}: {name}")

    print()
    print("=== Wallets ===")
    for wallet in wallets:
        can_sign = getattr(wallet, "signer", None) is not None
        print(f"- {wallet.identifier.identifier}")
        print(f"    address:  {wallet.address.string}")
        print(f"    class:    {type(wallet).__name__}")
        print(f"    can sign: {'yes' if can_sign else 'no (read-only)'}")
        print(f"    tx wait:  {wallet.configuration.default_tx_wait}")
        tracked = sorted(
            asset.identifier.string for asset in wallet.configuration.tracked_assets
        )
        print(f"    tracked:  {', '.join(tracked) if tracked else '(native only)'}")

    print()
    ethereum_identifier = wallets[0].identifier.identifier
    print("The factory caches one instance per identifier:")
    print(
        f"  WalletFactory.create({ethereum_identifier!r}) is wallets[0] -> "
        f"{WalletFactory.create(ethereum_identifier) is wallets[0]}"
    )

    print()
    print(
        f"Set {ETHEREUM_ADDRESS_ENV}/{ETHEREUM_PRIVATE_KEY_ENV} and "
        f"{SOLANA_ADDRESS_ENV}/{SOLANA_PRIVATE_KEY_ENV} to use your own wallets."
    )


if __name__ == "__main__":
    main()
