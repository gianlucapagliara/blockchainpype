"""
This module provides classes for handling Solana program IDLs (Interface Description Language).
It supports loading IDLs from different sources, including direct dictionaries and local files,
with an extensible base class for implementing additional IDL sources.

Two IDL layouts are supported throughout the dapp layer:

- Real Anchor IDLs, where ``instructions`` is a **list** of instruction
  definitions (e.g. ``common/idl/jupiter_dca.json``)
- Simplified hand-written IDLs, where ``instructions`` is a **dict** keyed by
  instruction name (e.g. ``common/idl/solend.json``)
"""

import json
import os
from abc import abstractmethod
from typing import Any

from pydantic import BaseModel, Field

from blockchainpype import common_idl_path


def find_idl_instruction(idl: dict[str, Any], name: str) -> dict[str, Any] | None:
    """
    Look up an instruction definition in an IDL by name.

    Supports both real Anchor IDLs (``instructions`` as a list of definition
    dicts) and simplified IDLs (``instructions`` as a dict keyed by name).

    Args:
        idl (dict[str, Any]): The parsed IDL document
        name (str): The instruction name to look up

    Returns:
        dict[str, Any] | None: The instruction definition, or None if not found
    """
    instructions = idl.get("instructions")

    if isinstance(instructions, list):
        for instruction in instructions:
            if isinstance(instruction, dict) and instruction.get("name") == name:
                return instruction
        return None

    if isinstance(instructions, dict):
        if name not in instructions:
            return None
        instruction = instructions[name]
        if isinstance(instruction, dict):
            return instruction
        # Bare entries (e.g. {"transfer": null}) still identify a valid name
        return {"name": name}

    return None


class SolanaIDL(BaseModel):
    """
    Abstract base class for Solana IDL handling.

    This class defines the interface for accessing program IDLs, allowing for
    different implementations of IDL storage and retrieval methods.
    """

    @abstractmethod
    async def get_idl(self) -> dict[str, Any]:
        """
        Retrieve the program IDL.

        Returns:
            dict: The program IDL as a dictionary

        Raises:
            NotImplementedError: This method must be implemented by subclasses
        """
        raise NotImplementedError


class SolanaDictIDL(SolanaIDL):
    """
    Implementation of IDL handling using a direct dictionary.

    This class allows for direct specification of an IDL as a dictionary,
    useful for in-memory IDL storage or testing purposes.

    Attributes:
        idl (dict): The program IDL stored as a dictionary
    """

    idl: dict[str, Any]

    async def get_idl(self) -> dict[str, Any]:
        """
        Retrieve the program IDL from the stored dictionary.

        Returns:
            dict: The program IDL
        """
        return self.idl


class SolanaLocalFileIDL(SolanaIDL):
    """
    Implementation of IDL handling using local file storage.

    This class loads IDLs from JSON files stored in the local filesystem.
    It supports configurable file paths and uses a common IDL directory by default.

    Attributes:
        file_name (str): Name of the IDL JSON file
        folder_path (str): Directory containing the IDL file, defaults to common_idl_path
    """

    file_name: str
    folder_path: str = Field(default=common_idl_path)

    @property
    def file_path(self) -> str:
        """
        Get the full path to the IDL file.

        Returns:
            str: Absolute path to the IDL JSON file
        """
        return os.path.join(self.folder_path, self.file_name)

    async def get_idl(self) -> dict[str, Any]:
        """
        Load and retrieve the program IDL from the local file.

        Returns:
            dict: The program IDL loaded from the JSON file

        Raises:
            FileNotFoundError: If the IDL file doesn't exist
            json.JSONDecodeError: If the file contains invalid JSON
            ValueError: If the file doesn't contain a JSON object
        """
        with open(self.file_path) as file:
            data: Any = json.load(file)

        if not isinstance(data, dict):
            raise ValueError(
                f"Invalid IDL format in file {self.file_path}. "
                "Expected a JSON object with an 'instructions' field."
            )

        result: dict[str, Any] = data
        return result
