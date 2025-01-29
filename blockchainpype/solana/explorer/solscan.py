"""
This module provides integration with the Etherscan blockchain explorer.
It supports generating transaction links and interacting with the Etherscan API
for retrieving blockchain data and transaction information.
"""

from pydantic import BaseModel, SecretStr

from blockchainpype.solana.blockchain.identifier import SolanaTransactionSignature


class SolscanConfiguration(BaseModel):
    """
    Configuration for Solscan explorer integration.

    This class defines the settings needed to interact with Solscan's web interface
    and API, including URLs and optional API key for extended functionality.

    Attributes:
        base_url (str): Base URL for Solscan web interface, defaults to mainnet
        api_key (str | None): Optional API key for accessing Solscan API
        api_url (str): Base URL for Solscan API endpoints
    """

    base_url: str = "https://solscan.io"
    api_url: str = "https://public-api.solscan.io/"
    api_key: SecretStr | None = None


class SolscanExplorer:
    def __init__(self, configuration: SolscanConfiguration):
        self.configuration = configuration

    @property
    def base_url(self) -> str:
        return self.configuration.base_url

    @property
    def api_url(self) -> str:
        return self.configuration.api_url

    def get_transaction_link(self, transaction_hash: SolanaTransactionSignature) -> str:
        """
        Generate a web link to view a transaction on Solscan.

        Args:
            transaction_hash (SolanaTransactionSignature): The transaction hash

        Returns:
            str: URL to view the transaction on Solscan
        """
        return f"{self.base_url}/tx/{transaction_hash}"
