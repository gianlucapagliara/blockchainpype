"""
This module provides classes for handling Ethereum smart contract ABIs (Application Binary Interfaces).
It supports loading ABIs from different sources, including direct dictionaries and local files,
with an extensible base class for implementing additional ABI sources.
"""

import json
import os
from abc import abstractmethod

from pydantic import BaseModel, Field

from blockchainpype import common_abi_path


class EthereumABI(BaseModel):
    """
    Abstract base class for Ethereum ABI handling.

    This class defines the interface for accessing contract ABIs, allowing for
    different implementations of ABI storage and retrieval methods.
    """

    @abstractmethod
    async def get_abi(self) -> dict:
        """
        Retrieve the contract ABI.

        Returns:
            dict: The contract ABI as a dictionary

        Raises:
            NotImplementedError: This method must be implemented by subclasses
        """
        raise NotImplementedError


class EthereumDictABI(EthereumABI):
    """
    Implementation of ABI handling using a direct dictionary.

    This class allows for direct specification of an ABI as a dictionary,
    useful for in-memory ABI storage or testing purposes.

    Attributes:
        abi (dict): The contract ABI stored as a dictionary
    """

    abi: dict

    async def get_abi(self) -> dict:
        """
        Retrieve the contract ABI from the stored dictionary.

        Returns:
            dict: The contract ABI
        """
        return self.abi


class EthereumLocalFileABI(EthereumABI):
    """
    Implementation of ABI handling using local file storage.

    This class loads ABIs from JSON files stored in the local filesystem.
    It supports configurable file paths and uses a common ABI directory by default.

    Attributes:
        file_name (str): Name of the ABI JSON file
        folder_path (str): Directory containing the ABI file, defaults to common_abi_path
    """

    file_name: str
    folder_path: str = Field(default=common_abi_path)

    @property
    def file_path(self) -> str:
        """
        Get the full path to the ABI file.

        Returns:
            str: Absolute path to the ABI JSON file
        """
        return os.path.join(self.folder_path, self.file_name)

    async def get_abi(self) -> dict:
        """
        Load and retrieve the contract ABI from the local file.

        Returns:
            dict: The contract ABI loaded from the JSON file

        Raises:
            FileNotFoundError: If the ABI file doesn't exist
            json.JSONDecodeError: If the file contains invalid JSON
        """
        with open(self.file_path) as file:
            return json.load(file)
