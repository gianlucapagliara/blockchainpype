"""
This module provides identifier classes for various Solana blockchain entities,
including addresses (public keys), transaction signatures, and block hashes.
These classes handle the validation, conversion, and string representation of
Solana identifiers.
"""

from typing import Any

from financepype.operators.blockchains.identifier import BlockchainIdentifier
from pydantic import Field
from solders.pubkey import Pubkey
from solders.signature import Signature


class SolanaTransactionSignature(BlockchainIdentifier):
    """
    Represents and validates Solana transaction signatures.

    This class handles Solana transaction signature validation, conversion between string
    and Signature formats, and provides a standardized way to work with transaction signatures.

    Attributes:
        raw (Signature): The raw Solana transaction signature
        string (str): String representation of the signature
    """

    raw: Signature
    string: str

    @classmethod
    def is_valid(cls, value: Any) -> bool:
        """
        Check if a value is a valid Solana transaction signature.

        Args:
            value (Any): The value to validate

        Returns:
            bool: True if the value is a valid transaction signature, False otherwise
        """
        if isinstance(value, Signature):
            return True
        try:
            Signature.from_string(str(value))
            return True
        except ValueError:
            return False

    @classmethod
    def id_from_string(cls, value: str) -> Signature:
        """
        Convert a string to a Solana transaction signature.

        Args:
            value (str): The signature string to convert

        Returns:
            Signature: The Solana transaction signature

        Raises:
            ValueError: If the signature string is invalid
        """
        if not cls.is_valid(value):
            raise ValueError(f"Invalid transaction signature: {value}")

        return Signature.from_string(value)

    @classmethod
    def id_to_string(cls, value: Signature) -> str:
        """
        Convert a Solana transaction signature to its string representation.

        Args:
            value (Signature): The Solana transaction signature to convert

        Returns:
            str: The string representation of the signature
        """
        return str(value)


class SolanaPublicKey(BlockchainIdentifier):
    """
    Represents and validates Solana public keys.

    This class handles Solana public key validation, conversion between string
    and Pubkey formats, and provides a standardized way to work with public keys.

    Attributes:
        raw (Pubkey): The raw Solana public key
        string (str): String representation of the public key
    """

    raw: Pubkey
    string: str

    @classmethod
    def is_valid(cls, value: Any) -> bool:
        """
        Check if a value is a valid Solana public key.

        Args:
            value (Any): The value to validate

        Returns:
            bool: True if the value is a valid public key, False otherwise
        """
        if isinstance(value, Pubkey):
            return True
        try:
            Pubkey.from_string(str(value))
            return True
        except ValueError:
            return False

    @classmethod
    def id_from_string(cls, value: str) -> Pubkey:
        """
        Convert a string to a Solana public key.

        Args:
            value (str): The public key string to convert

        Returns:
            Pubkey: The Solana public key

        Raises:
            ValueError: If the public key string is invalid
        """
        if not cls.is_valid(value):
            raise ValueError(f"Invalid public key: {value}")

        return Pubkey.from_string(value)

    @classmethod
    def id_to_string(cls, value: Pubkey) -> str:
        """
        Convert a Solana public key to its string representation.

        Args:
            value (Pubkey): The Solana public key to convert

        Returns:
            str: The string representation of the public key
        """
        return str(value)


class SolanaAddress(SolanaPublicKey):
    """
    Represents and validates Solana addresses.
    """

    pass


class SolanaNullAddress(SolanaAddress):
    """
    Represents the null Solana address.
    """

    raw: Pubkey = Field(
        default_factory=lambda: Pubkey.from_string(
            "So11111111111111111111111111111111111111111"
        ),
        init=False,
    )
    string: str = Field(
        default_factory=lambda: "So11111111111111111111111111111111111111111",
        init=False,
    )
