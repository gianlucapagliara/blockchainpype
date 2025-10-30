"""Ethereum ABI sourcing utilities."""

from __future__ import annotations

import json
import os
from abc import abstractmethod

import aiohttp
from pydantic import BaseModel, ConfigDict, Field

from blockchainpype import common_abi_path
from blockchainpype.evm.blockchain.identifier import EthereumAddress
from blockchainpype.evm.explorer.etherscan import EtherscanExplorer


class EthereumABI(BaseModel):
    """
    Abstract base class for Ethereum ABI handling.

    This class defines the interface for accessing contract ABIs, allowing for
    different implementations of ABI storage and retrieval methods.
    """

    @abstractmethod
    async def get_abi(self) -> list | dict:
        """
        Retrieve the contract ABI.

        Returns:
            list | dict: The contract ABI as a list of function/event definitions or dict

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

    async def get_abi(self) -> list | dict:
        """
        Retrieve the contract ABI from the stored dictionary.

        Returns:
            list | dict: The contract ABI
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

    async def get_abi(self) -> list | dict:
        """
        Load and retrieve the contract ABI from the local file.

        Returns:
            list | dict: The contract ABI as a list of function/event definitions or dict

        Raises:
            FileNotFoundError: If the ABI file doesn't exist
            json.JSONDecodeError: If the file contains invalid JSON
            KeyError: If the file doesn't contain a valid ABI structure
        """
        with open(self.file_path) as file:
            data = json.load(file)

            # Handle Hardhat artifact format
            if isinstance(data, dict) and "abi" in data:
                return data["abi"]

            # Handle direct ABI array format
            if isinstance(data, list):
                return data

            # If it's a dict but not a Hardhat artifact, assume it's the ABI itself
            if isinstance(data, dict):
                return data

            raise ValueError(
                f"Invalid ABI format in file {self.file_path}. Expected list or object with 'abi' field."
            )


class EthereumEtherscanABI(EthereumABI):
    """Fetch contract ABIs from the Etherscan API.

    This implementation delegates ABI retrieval to :class:`EtherscanExplorer` so
    contracts can bootstrap themselves without a bundled artifact. Future
    iterations may compose this remote source with local fallbacks.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    explorer: EtherscanExplorer
    contract_address: EthereumAddress
    request_timeout_seconds: float | None = 10.0

    async def get_abi(self) -> list | dict:
        """Retrieve and decode the remote ABI."""

        timeout = (
            aiohttp.ClientTimeout(total=self.request_timeout_seconds)
            if self.request_timeout_seconds is not None
            else None
        )

        try:
            return await self.explorer.fetch_contract_abi(
                self.contract_address, timeout=timeout
            )
        except ValueError as exc:  # pragma: no cover - wrapped for clarity
            raise ValueError(
                f"Unable to fetch ABI for {self.contract_address.string} from Etherscan: {exc}"
            ) from exc
