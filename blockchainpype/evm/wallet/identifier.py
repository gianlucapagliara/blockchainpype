"""
This module provides classes for handling Ethereum wallet identifiers, supporting both
address-based and name-based wallet identification. It extends the base OwnerIdentifier
functionality to work with Ethereum-specific wallet addresses.
"""

from financepype.owners.wallet import BlockchainWalletIdentifier

from blockchainpype.evm.blockchain.identifier import EthereumAddress


class EthereumWalletIdentifier(BlockchainWalletIdentifier):
    """
    Base class for Ethereum wallet identification using addresses.

    This class provides functionality to identify Ethereum wallets using their
    blockchain addresses. It combines the platform identifier with the wallet
    address to create unique identifiers.

    Attributes:
        address (EthereumAddress): The Ethereum address associated with the wallet
    """

    address: EthereumAddress
