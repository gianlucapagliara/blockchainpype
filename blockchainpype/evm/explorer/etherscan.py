"""Etherscan explorer utilities.

This module provides integration with the Etherscan blockchain explorer.
It supports generating transaction links and interacting with the Etherscan API
for retrieving blockchain data, transaction information, and (optionally) remote
contract metadata such as ABIs.
"""

from __future__ import annotations

import json
from typing import Any

import aiohttp
from aiohttp import ClientSession
from pydantic import BaseModel, SecretStr

from blockchainpype.evm.blockchain.identifier import (
    EthereumAddress,
    EthereumTransactionHash,
)


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
    api_url: str = "https://api.etherscan.io/v2/api"
    chain_id: int | None = 1
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

    async def fetch_contract_abi(
        self,
        address: EthereumAddress | str,
        *,
        session: ClientSession | None = None,
        timeout: aiohttp.ClientTimeout | None = None,
    ) -> list | dict:
        """Retrieve a contract ABI from the Etherscan API.

        Args:
            address: Contract address, either as ``EthereumAddress`` or string.
            session: Optional ``aiohttp`` session to reuse across calls.
            timeout: Optional request timeout (only used when creating the session).

        Returns:
            The decoded ABI as a list or dictionary.

        Raises:
            ValueError: If the address is invalid or the API request fails.

        Notes:
            Future enhancements may wrap this call in a higher-level ABI strategy
            that chains local and remote sources.
        """

        checksum_address = self._normalize_address(address)

        close_session = False
        client_session = session
        if client_session is None:
            close_session = True
            client_session = aiohttp.ClientSession(
                timeout=timeout or aiohttp.ClientTimeout(total=10)
            )

        try:
            target_address, inline_abi = await self._resolve_target_contract(
                checksum_address,
                client_session,
            )

            if inline_abi is not None:
                return inline_abi

            payload = await self._perform_api_request(
                client_session,
                self._build_contract_params(
                    action="getabi",
                    address=target_address,
                ),
            )
        except aiohttp.ClientResponseError as exc:
            raise ValueError(
                f"Failed to fetch ABI from Etherscan: HTTP {exc.status}"
            ) from exc
        except aiohttp.ClientError as exc:
            raise ValueError("Failed to fetch ABI from Etherscan") from exc
        finally:
            if close_session:
                await client_session.close()

        return self._decode_abi_payload(payload)

    async def _resolve_target_contract(
        self,
        checksum_address: str,
        session: ClientSession,
    ) -> tuple[str, list | dict | None]:
        try:
            metadata = await self._fetch_contract_metadata(
                checksum_address, session=session
            )
        except ValueError:
            return checksum_address, None

        if metadata is None:
            return checksum_address, None

        if metadata.get("Proxy") != "1":
            return checksum_address, None

        inline_abi = metadata.get("ImplementationContractAbi")
        if isinstance(inline_abi, str):
            inline_abi = inline_abi.strip()
            if inline_abi and inline_abi not in {"[]", "Not Verified"}:
                try:
                    return checksum_address, self._decode_abi_json_string(inline_abi)
                except ValueError:
                    pass

        implementation_address = metadata.get("Implementation")
        if isinstance(implementation_address, str) and implementation_address:
            try:
                impl_checksum = EthereumAddress.id_to_string(
                    EthereumAddress.id_from_string(implementation_address)
                )
                return impl_checksum, None
            except ValueError:
                return checksum_address, None

        return checksum_address, None

    async def _fetch_contract_metadata(
        self, checksum_address: str, session: ClientSession
    ) -> dict[str, Any] | None:
        payload = await self._perform_api_request(
            session,
            self._build_contract_params(
                action="getsourcecode",
                address=checksum_address,
            ),
        )

        if not isinstance(payload, dict):
            raise ValueError(
                "Unexpected metadata response format returned by Etherscan"
            )

        if payload.get("status") != "1":
            raise ValueError(
                payload.get("result")
                or payload.get("message")
                or "Metadata fetch failed"
            )

        result = payload.get("result")
        if not isinstance(result, list) or not result:
            raise ValueError("Etherscan metadata payload did not include a result list")

        first_entry = result[0]
        if not isinstance(first_entry, dict):
            raise ValueError("Etherscan metadata entry is not a dictionary")

        return first_entry

    @staticmethod
    def _normalize_address(address: EthereumAddress | str) -> str:
        if isinstance(address, EthereumAddress):
            return address.string

        checksum = EthereumAddress.id_from_string(str(address))
        return EthereumAddress.id_to_string(checksum)

    @staticmethod
    def _decode_abi_payload(payload: Any) -> list | dict:
        if not isinstance(payload, dict):
            raise ValueError("Unexpected response format returned by Etherscan")

        status = payload.get("status")
        result = payload.get("result")

        if status != "1":
            error_detail = result or payload.get("message") or "Unknown error"
            raise ValueError(f"Etherscan ABI fetch failed: {error_detail}")

        if not isinstance(result, str):
            raise ValueError("Etherscan ABI payload is not a JSON string")

        try:
            abi = json.loads(result)
        except json.JSONDecodeError as exc:
            raise ValueError("Etherscan returned malformed ABI JSON") from exc

        if not isinstance(abi, list | dict):
            raise ValueError("Etherscan ABI must decode to a list or dict")

        return abi

    @staticmethod
    def _decode_abi_json_string(raw_abi: str) -> list | dict:
        try:
            abi = json.loads(raw_abi)
        except json.JSONDecodeError as exc:  # pragma: no cover - defensive
            raise ValueError("Etherscan inline ABI JSON is malformed") from exc

        if not isinstance(abi, list | dict):
            raise ValueError("Etherscan inline ABI must decode to a list or dict")

        return abi

    def _build_contract_params(self, *, action: str, address: str) -> dict[str, str]:
        params: dict[str, str] = {
            "module": "contract",
            "action": action,
            "address": address,
        }

        if "v2" in self.api_url and self.configuration.chain_id is not None:
            params["chainid"] = str(self.configuration.chain_id)

        if self.configuration.api_key is not None:
            params["apikey"] = self.configuration.api_key.get_secret_value()

        return params

    async def _perform_api_request(
        self, session: ClientSession, params: dict[str, str]
    ) -> Any:
        async with session.get(self.api_url, params=params) as response:
            response.raise_for_status()
            return await response.json(content_type=None)
