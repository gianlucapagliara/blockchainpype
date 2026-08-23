"""
This module provides Ethereum wallet signing functionality, allowing for secure transaction
signing using private keys. It implements a secure configuration model and signer class
that extends the eth-account's LocalAccount functionality.
"""

from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_keys.datatypes import PrivateKey
from pydantic import BaseModel, SecretStr


class EthereumSignerConfiguration(BaseModel):
    """
    Configuration class for Ethereum signer settings.

    This class securely stores the private key needed for transaction signing.
    It uses Pydantic's SecretStr to ensure the private key is never exposed in logs or str representations.

    Attributes:
        private_key (SecretStr): The private key for the Ethereum wallet, stored securely
    """

    private_key: SecretStr


class EthereumSigner(LocalAccount):
    """
    Ethereum transaction signer that provides secure transaction signing capabilities.

    This class extends eth-account's LocalAccount to provide a secure way to sign
    Ethereum transactions using a private key. It ensures proper initialization
    of the signing account with the provided configuration.

    Args:
        configuration (EthereumSignerConfiguration): Configuration containing the private key

    Raises:
        ValueError: If the configured private key is malformed
    """

    def __init__(self, configuration: EthereumSignerConfiguration):
        """
        Initialize the signer with the provided configuration.

        Args:
            configuration (EthereumSignerConfiguration): Configuration containing the private key
                used for transaction signing
        """
        # Account.from_key is the public parsing/validation entry point (it
        # accepts hex strings, bytes and ints); LocalAccount.__init__ itself
        # requires an eth_keys PrivateKey instance.
        parsed = Account.from_key(configuration.private_key.get_secret_value())
        super().__init__(PrivateKey(bytes(parsed.key)), account=Account())
