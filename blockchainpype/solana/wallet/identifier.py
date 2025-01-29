"""
This module provides classes for handling Solana wallet identifiers, supporting both
address-based and name-based wallet identification. It extends the base OwnerIdentifier
functionality to work with Solana-specific wallet addresses.
"""

from financepype.owners.owner import OwnerIdentifier

from blockchainpype.solana.blockchain.identifier import SolanaAddress


class SolanaWalletIdentifier(OwnerIdentifier):
    """
    Base class for Solana wallet identification using addresses.

    This class provides functionality to identify Solana wallets using their
    blockchain addresses (public keys). It combines the platform identifier with
    the wallet address to create unique identifiers.

    Attributes:
        address (SolanaAddress): The Solana address (public key) associated with the wallet
    """

    address: SolanaAddress

    @property
    def identifier(self) -> str:
        """
        Get the unique identifier string for the wallet.

        The identifier combines the platform identifier and wallet address
        in the format "platform:address".

        Returns:
            str: The combined unique identifier string
        """
        return f"{self.platform.identifier}:{self.address.string}"


class NamedSolanaWalletIdentifier(SolanaWalletIdentifier):
    """
    Extended wallet identifier that uses a human-readable name instead of an address.

    This class allows for more user-friendly wallet identification by using a custom
    name while still maintaining the underlying address-based functionality.

    Attributes:
        name (str): The human-readable name for the wallet
        address (SolanaAddress): The Solana address (public key) associated with the wallet
    """

    name: str

    @property
    def identifier(self) -> str:
        """
        Get the unique identifier string using the wallet's name.

        The identifier combines the platform identifier and wallet name
        in the format "platform:name".

        Returns:
            str: The combined unique identifier string using the wallet name
        """
        return f"{self.platform.identifier}:{self.name}"
