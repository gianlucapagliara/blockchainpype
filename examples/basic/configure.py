"""Configure the blockchains the library can talk to.

``BlockchainsInitializer`` does two things: it registers the blockchain classes
per blockchain type (``EthereumBlockchain`` for EVM, ``SolanaBlockchain`` for
Solana) and it registers one configuration per platform in the
``BlockchainFactory``. Afterwards ``BlockchainFactory.get_by_identifier(...)``
returns a ready-to-use blockchain instance.

The stock configurations live in
:class:`~blockchainpype.initializer.BlockchainConfigurations`; subclassing it
and overriding a single ``*_configuration`` classmethod is the supported way to
point the library at your own RPC endpoints, as
:class:`CustomBlockchainConfigurations` below does.

Environment variables:

* ``ETHEREUM_RPC_URLS``: comma-separated Ethereum HTTP endpoints. The first one
  serves reads; the others are failover endpoints (see
  :class:`~blockchainpype.evm.blockchain.providers.multiple.MultipleHTTPProvider`).
* ``SOLANA_RPC_URL``: a single Solana HTTP endpoint.
* ``ETHERSCAN_API_KEY``: read by the stock Ethereum configuration to
  authenticate explorer calls (ABI downloads); optional but rate limited
  without it.

:func:`load_environment` loads a ``.env`` file from the working directory (real
environment variables win), so the examples can be configured without exporting
anything by hand.

Nothing here performs I/O: configuring and instantiating a blockchain only
builds provider objects, so this module runs fine offline.

Run it with::

    uv run python -m examples.basic.configure
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from financepype.operators.blockchains.models import BlockchainConfiguration
from financepype.platforms.blockchain import BlockchainPlatform
from solana.rpc.async_api import AsyncClient

from blockchainpype.evm.blockchain.configuration import (
    EthereumBlockchainConfiguration,
    EthereumConnectivityConfiguration,
)
from blockchainpype.evm.blockchain.providers.limited import LimitedHTTPProvider
from blockchainpype.evm.blockchain.providers.multiple import MultipleHTTPProvider
from blockchainpype.factory import BlockchainFactory
from blockchainpype.initializer import (
    BlockchainConfigurations,
    BlockchainsInitializer,
)
from blockchainpype.solana.blockchain.configuration import (
    SolanaBlockchainConfiguration,
    SolanaConnectivityConfiguration,
)

ETHEREUM_RPC_URLS_ENV = "ETHEREUM_RPC_URLS"
SOLANA_RPC_URL_ENV = "SOLANA_RPC_URL"

#: Public endpoints used when ``ETHEREUM_RPC_URLS`` is not set. Public nodes are
#: heavily rate limited; use your own for anything beyond a demo.
DEFAULT_ETHEREUM_RPC_URLS = (
    "https://eth.llamarpc.com",
    "https://rpc.mevblocker.io",
    "https://rpc.flashbots.net/",
)
DEFAULT_SOLANA_RPC_URL = "https://api.mainnet-beta.solana.com"

#: Client-side throttle applied to every Ethereum endpoint.
MAX_REQUESTS_PER_SECOND = 5.0


def load_environment() -> None:
    """Load a ``.env`` file from the working directory, when present.

    Real environment variables always win (``override=False``). Call it at the
    start of an entry point, never at import time.
    """
    load_dotenv(override=False)


def ethereum_rpc_urls() -> list[str]:
    """Read the Ethereum endpoints from the environment.

    Returns:
        list[str]: The endpoints from ``ETHEREUM_RPC_URLS`` (comma-separated,
        blank entries dropped), or :data:`DEFAULT_ETHEREUM_RPC_URLS` when the
        variable is unset or empty.
    """
    raw = os.getenv(ETHEREUM_RPC_URLS_ENV, "")
    urls = [url.strip() for url in raw.split(",") if url.strip()]
    return urls or list(DEFAULT_ETHEREUM_RPC_URLS)


def solana_rpc_url() -> str:
    """Read the Solana endpoint from ``SOLANA_RPC_URL`` (with a fallback)."""
    return os.getenv(SOLANA_RPC_URL_ENV, "").strip() or DEFAULT_SOLANA_RPC_URL


class CustomBlockchainConfigurations(BlockchainConfigurations):
    """Stock configurations with the RPC connectivity swapped out.

    Each override calls ``super()`` first and only replaces the connectivity
    section, so everything else (platform identity, native asset, explorer and
    its ``ETHERSCAN_API_KEY`` handling) keeps the library defaults.
    """

    @classmethod
    def ethereum_configuration(cls) -> EthereumBlockchainConfiguration | None:
        """Ethereum mainnet over the configured endpoints, with failover."""
        config = super().ethereum_configuration()
        if config is None:
            return None

        return config.model_copy(
            update={
                "connectivity": EthereumConnectivityConfiguration(
                    rpc_provider=MultipleHTTPProvider(
                        retrieval_providers=[
                            LimitedHTTPProvider(
                                url,
                                max_request_per_second=MAX_REQUESTS_PER_SECOND,
                            )
                            for url in ethereum_rpc_urls()
                        ],
                        # Reads and broadcasts share the same pool here; pass an
                        # explicit list to pin sends to a private relay.
                        execution_providers=None,
                    )
                )
            }
        )

    @classmethod
    def solana_configuration(cls) -> SolanaBlockchainConfiguration | None:
        """Solana mainnet over the configured endpoint."""
        config = super().solana_configuration()
        if config is None:
            return None

        return config.model_copy(
            update={
                "connectivity": SolanaConnectivityConfiguration(
                    rpc_provider=AsyncClient(solana_rpc_url()),
                )
            }
        )


def configure_blockchains() -> dict[BlockchainPlatform, BlockchainConfiguration]:
    """Register the blockchain classes and the custom configurations.

    The call is idempotent: platforms that are already registered are skipped,
    so importing this helper from several examples is safe.

    Returns:
        dict[BlockchainPlatform, BlockchainConfiguration]: Every configuration
        known to the factory, keyed by platform.
    """
    BlockchainsInitializer.configure(configurations=CustomBlockchainConfigurations)
    return BlockchainFactory.list_configurations()


def main() -> None:
    """Configure the blockchains and print what was registered."""
    load_environment()
    configurations = configure_blockchains()

    print("=== Registered blockchain configurations ===")
    for platform, configuration in sorted(
        configurations.items(), key=lambda item: item[0].identifier
    ):
        flags = []
        if platform.testnet:
            flags.append("testnet")
        if platform.local:
            flags.append("local")
        print(
            f"- {platform.identifier}: type={platform.type.value} "
            f"chain_id={platform.chain_id} "
            f"config={type(configuration).__name__}"
            + (f" [{', '.join(flags)}]" if flags else "")
        )

    print()
    print("=== Ethereum endpoints in use ===")
    for url in ethereum_rpc_urls():
        print(f"- {url}")
    print(f"(override with {ETHEREUM_RPC_URLS_ENV}=url1,url2,...)")
    print()
    print(f"Solana endpoint: {solana_rpc_url()}")
    print(f"(override with {SOLANA_RPC_URL_ENV}=url)")

    # Instantiating a blockchain is offline too: it only wires up the provider.
    ethereum = BlockchainFactory.get_evm_blockchain_by_identifier("ethereum")
    print()
    print(
        f"BlockchainFactory returned {type(ethereum).__name__} for 'ethereum' "
        f"(native asset: {ethereum.native_asset.data.symbol})"
    )


if __name__ == "__main__":
    main()
