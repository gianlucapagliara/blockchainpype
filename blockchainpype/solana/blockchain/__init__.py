"""
This package provides the core Solana blockchain layer: the SolanaBlockchain
operator (in the ``blockchain`` module), its configuration models, and the
identifier types used across the Solana subsystem (addresses, public keys,
transaction signatures).

Only the leaf configuration and identifier types are re-exported here: the
``blockchain`` module itself imports the asset layer (which depends on the
identifiers in this package), so re-exporting it eagerly would create a
circular import. Import SolanaBlockchain from
``blockchainpype.solana.blockchain.blockchain`` directly.
"""

from blockchainpype.solana.blockchain.configuration import (
    SolanaBlockchainConfiguration,
    SolanaConnectivityConfiguration,
    SolanaNativeAssetConfiguration,
)
from blockchainpype.solana.blockchain.identifier import (
    NATIVE_SOL_SENTINEL_ADDRESS,
    WRAPPED_SOL_MINT_ADDRESS,
    SolanaAddress,
    SolanaNullAddress,
    SolanaPublicKey,
    SolanaTransactionSignature,
)

__all__ = [
    "NATIVE_SOL_SENTINEL_ADDRESS",
    "WRAPPED_SOL_MINT_ADDRESS",
    "SolanaAddress",
    "SolanaBlockchainConfiguration",
    "SolanaConnectivityConfiguration",
    "SolanaNativeAssetConfiguration",
    "SolanaNullAddress",
    "SolanaPublicKey",
    "SolanaTransactionSignature",
]
