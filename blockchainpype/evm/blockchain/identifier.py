"""
This module provides identifier classes for various Ethereum blockchain entities,
including addresses, transaction hashes, and block hashes. These classes handle
the validation, conversion, and string representation of Ethereum identifiers.
"""

from typing import Any, Self

from eth_typing import ChecksumAddress
from financepype.operators.blockchains.identifier import BlockchainIdentifier
from hexbytes import HexBytes
from pydantic import Field
from web3 import AsyncWeb3

TRANSACTION_HASH_LENGTH = 32


class EthereumAddress(BlockchainIdentifier):
    """
    Represents and validates Ethereum addresses.

    This class handles Ethereum address validation, conversion between string and
    checksum formats, and provides a standardized way to work with Ethereum addresses.

    The string representation is always the EIP-55 checksummed form, regardless
    of the casing of the input, so identifiers built from different casings of
    the same address compare equal.

    Attributes:
        raw (ChecksumAddress): The raw checksum address
        string (str): Checksummed string representation of the address
    """

    raw: ChecksumAddress
    string: str

    @classmethod
    def is_valid(cls, value: Any) -> bool:
        """
        Check if a value is a valid Ethereum address.

        Args:
            value (Any): The value to validate

        Returns:
            bool: True if the value is a valid Ethereum address, False otherwise
        """
        try:
            AsyncWeb3.to_checksum_address(value)
        except (TypeError, ValueError):
            return False
        return True

    @classmethod
    def from_string(cls, value: str) -> Self:
        """
        Create an address identifier from a string, normalizing to checksum form.

        Unlike the base implementation, the stored string is the EIP-55
        checksummed representation rather than the input verbatim, preserving
        string-based equality across differently-cased inputs.

        Args:
            value (str): The address string in any casing

        Returns:
            Self: A new identifier with a checksummed string representation

        Raises:
            ValueError: If the address string is invalid
        """
        raw = cls.id_from_string(value)
        return cls(raw=raw, string=cls.id_to_string(raw))

    @classmethod
    def id_from_string(cls, value: str) -> ChecksumAddress:
        """
        Convert a string to a checksum address.

        Args:
            value (str): The address string to convert

        Returns:
            ChecksumAddress: The checksum address

        Raises:
            ValueError: If the address string is invalid
        """
        if not cls.is_valid(value):
            raise ValueError(f"Invalid wallet id: {value}")

        return AsyncWeb3.to_checksum_address(value)

    @classmethod
    def id_to_string(cls, value: ChecksumAddress) -> str:
        """
        Convert a checksum address to its string representation.

        Args:
            value (ChecksumAddress): The checksum address to convert

        Returns:
            str: The string representation of the address
        """
        return str(value)


class EthereumNullAddress(EthereumAddress):
    """
    Represents the Ethereum null address (0x0000...0000).

    This class provides a standardized way to work with the Ethereum null address,
    which is commonly used to represent contract creation transactions or burning tokens.

    The null address is automatically initialized and cannot be modified.
    """

    raw: ChecksumAddress = Field(
        default_factory=lambda: AsyncWeb3.to_checksum_address(
            "0x0000000000000000000000000000000000000000"
        ),
        init=False,
    )
    string: str = Field(
        default="0x0000000000000000000000000000000000000000",
        init=False,
    )


class EthereumTransactionHash(BlockchainIdentifier):
    """
    Represents and validates Ethereum transaction hashes.

    This class handles Ethereum transaction hash validation, conversion between string
    and HexBytes formats, and provides a standardized way to work with transaction hashes.

    The string representation is always the 0x-prefixed lowercase hexadecimal
    form, so identifiers built from raw bytes and from strings compare equal.

    Attributes:
        raw (HexBytes): The raw transaction hash in bytes
        string (str): 0x-prefixed string representation of the transaction hash
    """

    raw: HexBytes
    string: str

    @classmethod
    def is_valid(cls, value: Any) -> bool:
        """
        Check if a value is a valid Ethereum transaction hash.

        A valid hash must be convertible to bytes and be exactly 32 bytes long.

        Args:
            value (Any): The value to validate

        Returns:
            bool: True if the value is a valid transaction hash, False otherwise
        """
        try:
            raw = HexBytes(value)
        except (TypeError, ValueError):
            return False
        return len(raw) == TRANSACTION_HASH_LENGTH

    @classmethod
    def from_string(cls, value: str) -> Self:
        """
        Create a transaction hash identifier from a string, normalizing the
        stored string to the 0x-prefixed lowercase form.

        Args:
            value (str): The hash string to convert

        Returns:
            Self: A new identifier with a normalized string representation

        Raises:
            ValueError: If the hash string is invalid
        """
        raw = cls.id_from_string(value)
        return cls(raw=raw, string=cls.id_to_string(raw))

    @classmethod
    def id_from_string(cls, value: str) -> HexBytes:
        """
        Convert a string to a transaction hash in HexBytes format.

        Args:
            value (str): The hash string to convert

        Returns:
            HexBytes: The transaction hash in bytes

        Raises:
            ValueError: If the hash string is invalid
        """
        if not cls.is_valid(value):
            raise ValueError(f"Invalid transaction id: {value}")

        return HexBytes(value)

    @classmethod
    def id_to_string(cls, value: HexBytes) -> str:
        """
        Convert a transaction hash from HexBytes to its string representation.

        Args:
            value (HexBytes): The transaction hash in bytes

        Returns:
            str: The 0x-prefixed hexadecimal string representation of the hash
        """
        # hexbytes>=1.0 .hex() drops the 0x prefix; explorer links and
        # string-based identifier equality require the prefixed form.
        return value.to_0x_hex()


class EthereumBlockHash(EthereumTransactionHash):
    """
    Represents and validates Ethereum block hashes.

    This class extends EthereumTransactionHash since block hashes follow the same
    format and validation rules as transaction hashes. It provides a semantic
    distinction for block-specific identifiers.
    """

    pass
