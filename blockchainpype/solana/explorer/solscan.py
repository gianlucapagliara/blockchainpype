"""
This module provides integration with the Solscan blockchain explorer.
It supports generating web links to view transactions, accounts, tokens, and
blocks on Solscan. No network access is performed: Solscan's paid HTTP API is
intentionally not wrapped here, so the explorer is a pure link builder.
"""

from pydantic import BaseModel

from blockchainpype.solana.blockchain.identifier import (
    SolanaPublicKey,
    SolanaTransactionSignature,
)


class SolscanConfiguration(BaseModel):
    """
    Configuration for Solscan explorer integration.

    This class defines the settings needed to build links to Solscan's web
    interface.

    Attributes:
        base_url (str): Base URL for Solscan web interface, defaults to mainnet
    """

    base_url: str = "https://solscan.io"


class SolscanExplorer:
    """
    Interface for generating Solscan blockchain explorer links.

    This class provides methods for building web links to view transactions,
    accounts, tokens, and blocks on Solscan.

    Attributes:
        configuration (SolscanConfiguration): Explorer configuration
    """

    def __init__(self, configuration: SolscanConfiguration):
        """
        Initialize the Solscan explorer interface.

        Args:
            configuration (SolscanConfiguration): Explorer configuration
                including the web interface base URL
        """
        self.configuration = configuration

    @property
    def base_url(self) -> str:
        return self.configuration.base_url

    def get_transaction_link(self, transaction_hash: SolanaTransactionSignature) -> str:
        """
        Generate a web link to view a transaction on Solscan.

        Args:
            transaction_hash (SolanaTransactionSignature): The transaction signature

        Returns:
            str: URL to view the transaction on Solscan
        """
        return f"{self.base_url}/tx/{transaction_hash}"

    def get_address_link(self, address: SolanaPublicKey) -> str:
        """
        Generate a web link to view an account on Solscan.

        Args:
            address (SolanaPublicKey): The account address (public key)

        Returns:
            str: URL to view the account on Solscan
        """
        return f"{self.base_url}/account/{address}"

    def get_token_link(self, mint: SolanaPublicKey) -> str:
        """
        Generate a web link to view an SPL token on Solscan.

        Args:
            mint (SolanaPublicKey): The token's mint address

        Returns:
            str: URL to view the token on Solscan
        """
        return f"{self.base_url}/token/{mint}"

    def get_block_link(self, slot: int) -> str:
        """
        Generate a web link to view a block on Solscan.

        Args:
            slot (int): The block's slot number

        Returns:
            str: URL to view the block on Solscan
        """
        return f"{self.base_url}/block/{slot}"
