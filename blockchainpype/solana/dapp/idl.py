"""
This module provides classes for handling Solana program IDLs (Interface Description Language).
It supports loading IDLs from different sources, including direct dictionaries and local files,
with an extensible base class for implementing additional IDL sources.
"""

import json
import os
from abc import abstractmethod

from pydantic import BaseModel, Field

from blockchainpype import common_idl_path


class SolanaIDL(BaseModel):
    """
    Abstract base class for Solana IDL handling.

    This class defines the interface for accessing program IDLs, allowing for
    different implementations of IDL storage and retrieval methods.
    """

    @abstractmethod
    async def get_idl(self) -> dict:
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

    idl: dict

    async def get_idl(self) -> dict:
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

    async def get_idl(self) -> dict:
        """
        Load and retrieve the program IDL from the local file.

        Returns:
            dict: The program IDL loaded from the JSON file

        Raises:
            FileNotFoundError: If the IDL file doesn't exist
            json.JSONDecodeError: If the file contains invalid JSON
        """
        with open(self.file_path) as file:
            return json.load(file)
