import json
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from web3 import AsyncWeb3

from blockchainpype.evm.blockchain.identifier import EthereumAddress
from blockchainpype.evm.dapp.abi import EthereumEtherscanABI
from blockchainpype.evm.explorer.etherscan import (
    EtherscanConfiguration,
    EtherscanExplorer,
)


@dataclass
class _StubResponse:
    payload: Any

    async def __aenter__(self) -> "_StubResponse":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    async def json(self, content_type: str | None = None) -> Any:
        return self.payload

    def raise_for_status(self) -> None:
        return None


class _StubSession:
    def __init__(self, responses: list[_StubResponse]):
        self._responses = list(responses)
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.close_calls = 0

    def get(self, *args: Any, **kwargs: Any) -> _StubResponse:
        if not self._responses:
            raise AssertionError("No more responses configured for stub session")

        response = self._responses.pop(0)
        self.calls.append((args, kwargs))
        return response

    async def close(self) -> None:
        self.close_calls += 1


@pytest.fixture
def sample_contract_address() -> EthereumAddress:
    """Return a deterministic contract address for testing."""

    checksum = AsyncWeb3.to_checksum_address(
        "0x000000000000000000000000000000000000dEaD"
    )
    return EthereumAddress(raw=checksum, string=str(checksum))


@pytest.fixture
def etherscan_config() -> EtherscanConfiguration:
    return EtherscanConfiguration(
        base_url="https://etherscan.io",
        api_url="https://api.etherscan.io/v2/api",
    )


@pytest.fixture
def explorer(etherscan_config: EtherscanConfiguration) -> EtherscanExplorer:
    return EtherscanExplorer(configuration=etherscan_config)


@pytest.mark.asyncio
async def test_fetch_contract_abi_success(
    explorer: EtherscanExplorer, sample_contract_address: EthereumAddress
) -> None:
    abi_payload = [{"type": "function", "name": "balanceOf"}]
    response_payload = {
        "status": "1",
        "message": "OK",
        "result": json.dumps(abi_payload),
    }

    metadata_payload = {
        "status": "1",
        "result": [
            {
                "Proxy": "0",
            }
        ],
    }

    stub_session = _StubSession(
        responses=[
            _StubResponse(payload=metadata_payload),
            _StubResponse(payload=response_payload),
        ]
    )

    with patch(
        "blockchainpype.evm.explorer.etherscan.aiohttp.ClientSession",
        return_value=stub_session,
    ) as mock_session_cls:
        result = await explorer.fetch_contract_abi(sample_contract_address)

    assert result == abi_payload
    mock_session_cls.assert_called_once()
    assert len(stub_session.calls) == 2
    actions = [call[1]["params"]["action"] for call in stub_session.calls]
    assert actions == ["getsourcecode", "getabi"]
    assert stub_session.close_calls == 1


@pytest.mark.asyncio
async def test_fetch_contract_abi_raises_when_status_not_ok(
    explorer: EtherscanExplorer, sample_contract_address: EthereumAddress
) -> None:
    response_payload = {
        "status": "0",
        "message": "NOTOK",
        "result": "Contract source code not verified",
    }

    metadata_payload = {
        "status": "1",
        "result": [
            {
                "Proxy": "0",
            }
        ],
    }

    stub_session = _StubSession(
        responses=[
            _StubResponse(payload=metadata_payload),
            _StubResponse(payload=response_payload),
        ]
    )

    with patch(
        "blockchainpype.evm.explorer.etherscan.aiohttp.ClientSession",
        return_value=stub_session,
    ):
        with pytest.raises(ValueError, match="Contract source code not verified"):
            await explorer.fetch_contract_abi(sample_contract_address)


@pytest.mark.asyncio
async def test_fetch_contract_abi_uses_existing_session(
    explorer: EtherscanExplorer, sample_contract_address: EthereumAddress
) -> None:
    abi_payload = [{"type": "function", "name": "totalSupply"}]
    response_payload = {
        "status": "1",
        "message": "OK",
        "result": json.dumps(abi_payload),
    }

    metadata_payload = {
        "status": "1",
        "result": [
            {
                "Proxy": "0",
            }
        ],
    }

    stub_session = _StubSession(
        responses=[
            _StubResponse(payload=metadata_payload),
            _StubResponse(payload=response_payload),
        ]
    )

    result = await explorer.fetch_contract_abi(
        sample_contract_address, session=stub_session
    )

    assert result == abi_payload
    assert len(stub_session.calls) == 2
    assert stub_session.close_calls == 0


@pytest.mark.asyncio
async def test_fetch_contract_abi_invalid_json(
    explorer: EtherscanExplorer, sample_contract_address: EthereumAddress
) -> None:
    response_payload = {
        "status": "1",
        "message": "OK",
        "result": "not-json",
    }

    metadata_payload = {
        "status": "1",
        "result": [
            {
                "Proxy": "0",
            }
        ],
    }

    stub_session = _StubSession(
        responses=[
            _StubResponse(payload=metadata_payload),
            _StubResponse(payload=response_payload),
        ]
    )

    with patch(
        "blockchainpype.evm.explorer.etherscan.aiohttp.ClientSession",
        return_value=stub_session,
    ):
        with pytest.raises(ValueError, match="malformed ABI JSON"):
            await explorer.fetch_contract_abi(sample_contract_address)


@pytest.mark.asyncio
async def test_fetch_contract_abi_proxy_uses_inline_implementation_abi(
    explorer: EtherscanExplorer, sample_contract_address: EthereumAddress
) -> None:
    inline_abi = json.dumps(
        [
            {"type": "function", "name": "symbol"},
        ]
    )

    metadata_payload = {
        "status": "1",
        "result": [
            {
                "Proxy": "1",
                "Implementation": sample_contract_address.string,
                "ImplementationContractAbi": inline_abi,
            }
        ],
    }

    stub_session = _StubSession(
        responses=[
            _StubResponse(payload=metadata_payload),
        ]
    )

    with patch(
        "blockchainpype.evm.explorer.etherscan.aiohttp.ClientSession",
        return_value=stub_session,
    ):
        abi = await explorer.fetch_contract_abi(sample_contract_address)

    assert abi == json.loads(inline_abi)
    assert len(stub_session.calls) == 1
    assert stub_session.calls[0][1]["params"]["action"] == "getsourcecode"


@pytest.mark.asyncio
async def test_fetch_contract_abi_proxy_fetches_implementation_address(
    explorer: EtherscanExplorer, sample_contract_address: EthereumAddress
) -> None:
    implementation_address = AsyncWeb3.to_checksum_address(
        "0x000000000000000000000000000000000000bEEF"
    )

    metadata_payload = {
        "status": "1",
        "result": [
            {
                "Proxy": "1",
                "Implementation": implementation_address,
                "ImplementationContractAbi": "[]",
            }
        ],
    }

    abi_payload = {
        "status": "1",
        "message": "OK",
        "result": json.dumps(
            [
                {"type": "function", "name": "decimals"},
            ]
        ),
    }

    stub_session = _StubSession(
        responses=[
            _StubResponse(payload=metadata_payload),
            _StubResponse(payload=abi_payload),
        ]
    )

    with patch(
        "blockchainpype.evm.explorer.etherscan.aiohttp.ClientSession",
        return_value=stub_session,
    ):
        abi = await explorer.fetch_contract_abi(sample_contract_address)

    assert abi == json.loads(abi_payload["result"])
    assert len(stub_session.calls) == 2
    assert stub_session.calls[1][1]["params"]["address"] == implementation_address


@pytest.mark.asyncio
async def test_fetch_contract_abi_metadata_failure_falls_back(
    explorer: EtherscanExplorer, sample_contract_address: EthereumAddress
) -> None:
    metadata_payload = {
        "status": "0",
        "message": "NOTOK",
        "result": "Unable to locate contract at address",
    }

    abi_payload = {
        "status": "1",
        "message": "OK",
        "result": json.dumps(
            [
                {"type": "function", "name": "totalSupply"},
            ]
        ),
    }

    stub_session = _StubSession(
        responses=[
            _StubResponse(payload=metadata_payload),
            _StubResponse(payload=abi_payload),
        ]
    )

    with patch(
        "blockchainpype.evm.explorer.etherscan.aiohttp.ClientSession",
        return_value=stub_session,
    ):
        abi = await explorer.fetch_contract_abi(sample_contract_address)

    assert abi == json.loads(abi_payload["result"])
    assert len(stub_session.calls) == 2
    assert (
        stub_session.calls[1][1]["params"]["address"] == sample_contract_address.string
    )


@pytest.mark.asyncio
async def test_etherscan_abi_fetches_remote(
    sample_contract_address: EthereumAddress,
) -> None:
    explorer = AsyncMock(spec=EtherscanExplorer)
    explorer.fetch_contract_abi.return_value = [{"name": "balanceOf"}]

    abi_source = EthereumEtherscanABI(
        explorer=explorer,
        contract_address=sample_contract_address,
        request_timeout_seconds=5,
    )

    abi = await abi_source.get_abi()

    assert abi == [{"name": "balanceOf"}]
    explorer.fetch_contract_abi.assert_awaited_once()
    args, kwargs = explorer.fetch_contract_abi.call_args
    assert args[0] == sample_contract_address
    assert "timeout" in kwargs
    assert isinstance(kwargs["timeout"], aiohttp.ClientTimeout)
    assert kwargs["timeout"].total == 5


@pytest.mark.asyncio
async def test_etherscan_abi_raises_with_context(
    sample_contract_address: EthereumAddress,
) -> None:
    explorer = AsyncMock(spec=EtherscanExplorer)
    explorer.fetch_contract_abi.side_effect = ValueError("not verified")

    abi_source = EthereumEtherscanABI(
        explorer=explorer,
        contract_address=sample_contract_address,
    )

    with pytest.raises(ValueError, match="not verified") as exc:
        await abi_source.get_abi()

    assert sample_contract_address.string in str(exc.value)
