"""
This module provides classes for handling Solana wallet identifiers, supporting both
address-based and name-based wallet identification. It extends the base OwnerIdentifier
functionality to work with Solana-specific wallet addresses.
"""

from financepype.owners.wallet import BlockchainWalletIdentifier

from blockchainpype.solana.blockchain.identifier import SolanaAddress


class SolanaWalletIdentifier(BlockchainWalletIdentifier):
    """
    Base class for Solana wallet identification using addresses.

    This class provides functionality to identify Solana wallets using their
    blockchain addresses (public keys). It combines the platform identifier with
    the wallet address to create unique identifiers.

    Attributes:
        address (SolanaAddress): The Solana address (public key) associated with the wallet
    """

    address: SolanaAddress
