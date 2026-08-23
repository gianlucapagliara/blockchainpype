"""
This module provides base classes for interacting with Solana programs.
It implements program configuration, initialization, and instruction building capabilities
through Solders' interfaces, including Anchor-style instruction discriminators and a
minimal borsh encoder for instruction arguments.
"""

import hashlib
import re
import struct
from collections.abc import Mapping
from typing import Any, cast

from financepype.operators.dapps.dapp import (
    DecentralizedApplication,
    DecentralizedApplicationConfiguration,
)
from pydantic import ConfigDict
from solders.instruction import AccountMeta, Instruction
from solders.pubkey import Pubkey

from blockchainpype.solana.blockchain.blockchain import SolanaBlockchain
from blockchainpype.solana.blockchain.identifier import SolanaAddress
from blockchainpype.solana.dapp.idl import SolanaIDL, find_idl_instruction

# Borsh integer layouts: type name -> (byte size, signed)
_INT_LAYOUTS: dict[str, tuple[int, bool]] = {
    "u8": (1, False),
    "u16": (2, False),
    "u32": (4, False),
    "u64": (8, False),
    "u128": (16, False),
    "i8": (1, True),
    "i16": (2, True),
    "i32": (4, True),
    "i64": (8, True),
    "i128": (16, True),
}

_FLOAT_FORMATS: dict[str, str] = {"f32": "<f", "f64": "<d"}


def to_snake_case(name: str) -> str:
    """
    Convert a camelCase instruction name to snake_case.

    Anchor IDLs expose instruction names in camelCase (e.g. ``openDcaV2``)
    while discriminators are derived from the Rust snake_case method name
    (e.g. ``open_dca_v2``).

    Args:
        name (str): The camelCase (or already snake_case) name

    Returns:
        str: The snake_case name
    """
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def anchor_discriminator(instruction_name: str, namespace: str = "global") -> bytes:
    """
    Compute the 8-byte Anchor instruction discriminator.

    The discriminator is the first 8 bytes of
    ``sha256("<namespace>:<snake_case_instruction_name>")``.

    Args:
        instruction_name (str): The instruction name (camelCase or snake_case)
        namespace (str): The Anchor namespace, defaults to "global"

    Returns:
        bytes: The 8-byte discriminator
    """
    preimage = f"{namespace}:{to_snake_case(instruction_name)}"
    return hashlib.sha256(preimage.encode("utf-8")).digest()[:8]


def _encode_length_prefix(length: int) -> bytes:
    """Encode a borsh u32 little-endian length prefix."""
    return struct.pack("<I", length)


def _as_pubkey_bytes(value: Any) -> bytes:
    """Coerce a public key value (Pubkey, SolanaAddress, or str) to 32 bytes."""
    if isinstance(value, Pubkey):
        return bytes(value)
    if isinstance(value, SolanaAddress):
        return bytes(value.raw)
    if isinstance(value, str):
        return bytes(Pubkey.from_string(value))
    raise ValueError(f"Cannot encode {type(value).__name__} as a public key")


def _resolve_defined_type(
    type_name: str, defined_types: list[dict[str, Any]]
) -> dict[str, Any]:
    """Resolve a ``defined`` type reference against the IDL ``types`` section."""
    for defined in defined_types:
        if defined.get("name") == type_name:
            type_def = defined.get("type")
            if isinstance(type_def, dict):
                return type_def
            break
    raise ValueError(f"Unknown defined type: {type_name}")


def encode_borsh_value(
    value: Any,
    type_spec: str | dict[str, Any],
    defined_types: list[dict[str, Any]] | None = None,
) -> bytes:
    """
    Encode a single value using borsh serialization (little-endian).

    Supported type specs (as they appear in Anchor IDL JSON):

    - Primitive strings: ``u8``-``u128``, ``i8``-``i128``, ``f32``/``f64``,
      ``bool``, ``publicKey``/``pubkey``, ``string``, ``bytes``
    - Composite dicts: ``{"option": <inner>}``, ``{"vec": <inner>}``,
      ``{"array": [<inner>, <length>]}``, ``{"defined": <name>}`` (struct and
      unit-variant enum kinds)

    Args:
        value (Any): The value to encode
        type_spec (str | dict[str, Any]): The IDL type specification
        defined_types (list[dict[str, Any]] | None): The IDL ``types`` section,
            required to resolve ``defined`` type references

    Returns:
        bytes: The borsh-encoded value

    Raises:
        ValueError: If the type spec is unsupported or the value does not fit
    """
    if isinstance(type_spec, str):
        return _encode_borsh_primitive(value, type_spec)

    if isinstance(type_spec, dict):
        if "option" in type_spec:
            if value is None:
                return b"\x00"
            return b"\x01" + encode_borsh_value(
                value, type_spec["option"], defined_types
            )

        if "vec" in type_spec:
            items = list(value)
            encoded = b"".join(
                encode_borsh_value(item, type_spec["vec"], defined_types)
                for item in items
            )
            return _encode_length_prefix(len(items)) + encoded

        if "array" in type_spec:
            inner_spec, length = type_spec["array"]
            items = list(value)
            if len(items) != length:
                raise ValueError(
                    f"Array length mismatch: expected {length}, got {len(items)}"
                )
            return b"".join(
                encode_borsh_value(item, inner_spec, defined_types) for item in items
            )

        if "defined" in type_spec:
            defined_ref = type_spec["defined"]
            # Anchor >=0.30 uses {"defined": {"name": ...}}
            type_name = (
                defined_ref["name"] if isinstance(defined_ref, dict) else defined_ref
            )
            type_def = _resolve_defined_type(type_name, defined_types or [])
            return _encode_borsh_defined(value, type_name, type_def, defined_types)

    raise ValueError(f"Unsupported borsh type spec: {type_spec!r}")


def _encode_borsh_int(value: Any, type_name: str) -> bytes:
    """Encode a borsh integer value identified by its IDL type name."""
    size, signed = _INT_LAYOUTS[type_name]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Expected int for {type_name}, got {type(value).__name__}")
    int_value = int(value)
    try:
        return int_value.to_bytes(size, byteorder="little", signed=signed)
    except OverflowError as exc:
        raise ValueError(f"Value {value} out of range for {type_name}") from exc


def _encode_borsh_primitive(value: Any, type_name: str) -> bytes:
    """Encode a primitive borsh value identified by its IDL type name."""
    if type_name == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"Expected bool, got {type(value).__name__}")
        return b"\x01" if value else b"\x00"

    if type_name in _INT_LAYOUTS:
        return _encode_borsh_int(value, type_name)

    if type_name in _FLOAT_FORMATS:
        return struct.pack(_FLOAT_FORMATS[type_name], float(value))

    if type_name in ("publicKey", "pubkey"):
        return _as_pubkey_bytes(value)

    if type_name == "string":
        if not isinstance(value, str):
            raise ValueError(f"Expected str, got {type(value).__name__}")
        encoded = value.encode("utf-8")
        return _encode_length_prefix(len(encoded)) + encoded

    if type_name == "bytes":
        raw = bytes(value)
        return _encode_length_prefix(len(raw)) + raw

    raise ValueError(f"Unsupported borsh type: {type_name}")


def _encode_borsh_defined(
    value: Any,
    type_name: str,
    type_def: dict[str, Any],
    defined_types: list[dict[str, Any]] | None,
) -> bytes:
    """Encode a value against a ``defined`` type (struct or unit-variant enum)."""
    kind = type_def.get("kind")

    if kind == "struct":
        if not isinstance(value, Mapping):
            raise ValueError(
                f"Expected mapping for struct {type_name}, got {type(value).__name__}"
            )
        encoded = b""
        for field in type_def.get("fields", []):
            field_name = field["name"]
            if field_name not in value:
                raise ValueError(f"Missing field '{field_name}' for struct {type_name}")
            encoded += encode_borsh_value(
                value[field_name], field["type"], defined_types
            )
        return encoded

    if kind == "enum":
        variants = type_def.get("variants", [])
        for index, variant in enumerate(variants):
            if variant.get("name") == value:
                if variant.get("fields"):
                    raise ValueError(
                        f"Enum variant '{value}' of {type_name} has fields; "
                        "only unit variants are supported"
                    )
                return struct.pack("<B", index)
        raise ValueError(f"Unknown variant {value!r} for enum {type_name}")

    raise ValueError(f"Unsupported defined type kind for {type_name}: {kind!r}")


class SolanaProgramConfiguration(DecentralizedApplicationConfiguration):
    """
    Configuration class for Solana programs.

    This class defines the essential parameters needed to interact with a Solana
    program, including its address and IDL configuration.

    Attributes:
        address (SolanaAddress): The deployed program's address
        idl_configuration (SolanaIDL): The program's IDL configuration
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    address: SolanaAddress
    idl_configuration: SolanaIDL


class SolanaProgram(DecentralizedApplication):
    """
    Base class for interacting with Solana programs.

    This class provides the foundation for program interaction, including program
    initialization, instruction building, and state management. It uses Solders'
    interfaces for program operations.

    Attributes:
        _idl (dict | None): The program's IDL data
    """

    def __init__(self, configuration: SolanaProgramConfiguration):
        """
        Initialize the program interface.

        Args:
            configuration (SolanaProgramConfiguration): Program configuration
                including address and IDL
        """
        super().__init__(configuration)

        self._idl: dict[str, Any] | None = None

    @property
    def configuration(self) -> SolanaProgramConfiguration:
        """
        Get the program's configuration.

        Returns:
            SolanaProgramConfiguration: The program configuration
        """
        return cast(SolanaProgramConfiguration, super().configuration)

    @property
    def blockchain(self) -> SolanaBlockchain:
        """
        Get the blockchain instance.

        Returns:
            SolanaBlockchain: The blockchain interface
        """
        return cast(SolanaBlockchain, super().blockchain)

    @property
    def address(self) -> SolanaAddress:
        """
        Get the program's address.

        Returns:
            SolanaAddress: The deployed program's address
        """
        return self.configuration.address

    @property
    def idl(self) -> dict[str, Any] | None:
        """
        Get the program's IDL data.

        Returns:
            dict | None: The IDL data if initialized, None otherwise
        """
        return self._idl

    @property
    def is_initialized(self) -> bool:
        """
        Check if the program is initialized.

        Returns:
            bool: True if the program is initialized, False otherwise
        """
        return self.idl is not None

    async def initialize(self) -> None:
        """
        Initialize the program by loading its IDL.

        This method fetches the program's IDL and stores it for use in instruction
        building. It only initializes once, subsequent calls have no effect.
        """
        if self.is_initialized:
            return

        self._idl = await self.configuration.idl_configuration.get_idl()

    def create_instruction(
        self,
        name: str,
        accounts: list[AccountMeta],
        data: bytes | None = None,
        args: Mapping[str, Any] | None = None,
    ) -> Instruction:
        """
        Create a program instruction.

        For real Anchor IDLs (list-form ``instructions``), the instruction data
        is built as the 8-byte Anchor discriminator followed by the
        borsh-encoded arguments declared in the IDL. Simplified dict-form IDLs
        carry no argument specs, so callers must pass pre-encoded ``data``
        (including any protocol-specific discriminant) for instructions with
        payloads.

        Args:
            name (str): The instruction name from the IDL
            accounts (list[AccountMeta]): The accounts required for the instruction
            data (bytes | None): Complete pre-encoded instruction data. When
                provided it is used as-is (mutually exclusive with ``args``)
            args (Mapping[str, Any] | None): Argument values to borsh-encode
                according to the IDL argument specs (Anchor IDLs only)

        Returns:
            Instruction: The created program instruction

        Raises:
            ValueError: If the program is not initialized, the instruction name
                is invalid, or the data/args combination is inconsistent
        """
        if not self.is_initialized:
            raise ValueError("Program is not initialized")

        if data is not None and args is not None:
            raise ValueError(
                "Pass either pre-encoded 'data' or 'args' to encode, not both"
            )

        idl: dict[str, Any] = self.idl or {}
        instruction_def = find_idl_instruction(idl, name)
        if instruction_def is None:
            raise ValueError(f"Invalid instruction name: {name}")

        if data is not None:
            payload = data
        elif isinstance(idl.get("instructions"), list):
            payload = self._encode_anchor_instruction_data(
                idl, instruction_def, args or {}
            )
        else:
            # Simplified dict-form IDLs have no argument specs to encode against
            if args:
                raise ValueError(
                    f"Instruction '{name}' comes from a simplified IDL without "
                    "argument specs; pass pre-encoded 'data' instead of 'args'"
                )
            payload = b""

        return Instruction(
            program_id=self.address.raw,
            accounts=accounts,
            data=payload,
        )

    def _encode_anchor_instruction_data(
        self,
        idl: dict[str, Any],
        instruction_def: dict[str, Any],
        args: Mapping[str, Any],
    ) -> bytes:
        """
        Encode Anchor instruction data: discriminator plus borsh-encoded args.

        Args:
            idl (dict[str, Any]): The full IDL document (for ``types`` lookups)
            instruction_def (dict[str, Any]): The instruction definition
            args (Mapping[str, Any]): Argument values keyed by IDL argument name

        Returns:
            bytes: The encoded instruction data

        Raises:
            ValueError: If arguments are missing, unknown, or cannot be encoded
        """
        name = instruction_def.get("name", "")

        explicit_discriminator = instruction_def.get("discriminator")
        if explicit_discriminator is not None:
            discriminator = bytes(explicit_discriminator)
        else:
            discriminator = anchor_discriminator(name)

        arg_specs: list[dict[str, Any]] = instruction_def.get("args", []) or []
        spec_names = [spec["name"] for spec in arg_specs]
        unknown_args = set(args) - set(spec_names)
        if unknown_args:
            raise ValueError(
                f"Unknown argument(s) for instruction '{name}': {sorted(unknown_args)}"
            )

        defined_types = idl.get("types", []) or []

        encoded_args = b""
        for spec in arg_specs:
            arg_name = spec["name"]
            arg_type = spec["type"]
            is_optional = isinstance(arg_type, dict) and "option" in arg_type
            if arg_name not in args and not is_optional:
                raise ValueError(
                    f"Missing argument '{arg_name}' for instruction '{name}'"
                )
            value = args.get(arg_name)
            try:
                encoded_args += encode_borsh_value(value, arg_type, defined_types)
            except ValueError as exc:
                raise ValueError(
                    f"Cannot encode argument '{arg_name}' of instruction "
                    f"'{name}': {exc}"
                ) from exc

        return discriminator + encoded_args
