"""
This module provides Solana wallet signing functionality, allowing for secure transaction
signing using private keys. It implements a secure configuration model and signer class
that extends Solders' Keypair functionality.
"""

from pydantic import BaseModel, SecretStr
from solders.keypair import Keypair


class SolanaSignerConfiguration(BaseModel):
    """
    Configuration class for Solana signer settings.

    This class securely stores the private key needed for transaction signing.
    It uses Pydantic's SecretStr to ensure the private key is never exposed in logs or str representations.

    Attributes:
        private_key (SecretStr): The private key for the Solana wallet, stored securely as a base58 string
    """

    private_key: SecretStr


class SolanaSigner:
    """
    Solana transaction signer that provides secure transaction signing capabilities.

    This class wraps Solders' Keypair to provide a secure way to sign
    Solana transactions using a private key. It ensures proper initialization
    of the signing account with the provided configuration.

    Args:
        configuration (SolanaSignerConfiguration): Configuration containing the private key
    """

    def __init__(self, configuration: SolanaSignerConfiguration):
        """
        Initialize the signer with the provided configuration.

        Args:
            configuration (SolanaSignerConfiguration): Configuration containing the private key
                used for transaction signing
        """
        self.keypair = Keypair.from_base58_string(
            configuration.private_key.get_secret_value()
        )
