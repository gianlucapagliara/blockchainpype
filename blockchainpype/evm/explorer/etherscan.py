"""
This module provides integration with the Etherscan blockchain explorer.
It supports generating transaction links and interacting with the Etherscan API
for retrieving blockchain data and transaction information.
"""

from pydantic import BaseModel, SecretStr

from blockchainpype.evm.blockchain.identifier import EthereumTransactionHash


class EtherscanConfiguration(BaseModel):
    """
    Configuration for Etherscan explorer integration.

    This class defines the settings needed to interact with Etherscan's web interface
    and API, including URLs and optional API key for extended functionality.

    Attributes:
        base_url (str): Base URL for Etherscan web interface, defaults to mainnet
        api_key (str | None): Optional API key for accessing Etherscan API
        api_url (str): Base URL for Etherscan API endpoints
    """

    base_url: str = "https://etherscan.io"
    api_url: str = "https://api.etherscan.io/api"
    api_key: SecretStr | None = None


class EtherscanExplorer:
    """
    Interface for interacting with Etherscan blockchain explorer.

    This class provides methods for generating explorer links and interacting
    with Etherscan's API to retrieve blockchain data.

    Attributes:
        api_key (str | None): API key for Etherscan API access
        base_url (str): Base URL for web interface
        api_url (str): Base URL for API endpoints
    """

    def __init__(self, configuration: EtherscanConfiguration):
        """
        Initialize the Etherscan explorer interface.

        Args:
            configuration (EtherscanConfiguration): Explorer configuration including
                URLs and optional API key
        """
        self.configuration = configuration

    @property
    def base_url(self) -> str:
        return self.configuration.base_url

    @property
    def api_url(self) -> str:
        return self.configuration.api_url

    def get_transaction_link(self, transaction_hash: EthereumTransactionHash) -> str:
        """
        Generate a web link to view a transaction on Etherscan.

        Args:
            transaction_hash (EthereumTransactionHash): The transaction hash

        Returns:
            str: URL to view the transaction on Etherscan
        """
        return f"{self.base_url}/tx/{transaction_hash}"
