"""
Unit tests for the custom Web3 async providers: MultipleHTTPProvider failover
and LimitedHTTPProvider rate limiting. All network I/O is faked at the HTTP
boundary; no test needs network access.
"""

import asyncio
import json
import time
from typing import Any, cast

import pytest
from aiohttp import ClientError
from web3 import AsyncWeb3
from web3.providers import AsyncHTTPProvider
from web3.types import RPCEndpoint, RPCResponse

from blockchainpype.evm.blockchain.providers import (
    LimitedHTTPProvider,
    MultipleHTTPProvider,
)


class ScriptedProvider(AsyncHTTPProvider):
    """
    AsyncHTTPProvider double with scripted outcomes and no network access.

    Each entry in ``script`` is consumed per request: exceptions are raised,
    any other value is returned as the JSON-RPC result. When the script is
    exhausted, ``default_result`` is returned.
    """

    def __init__(
        self,
        endpoint_uri: str,
        script: list[Any] | None = None,
        default_result: Any = "0x1",
    ) -> None:
        super().__init__(endpoint_uri)
        self.script = list(script or [])
        self.default_result = default_result
        self.calls: list[tuple[str, Any]] = []
        self.batch_calls: list[list[str]] = []

    def _next_action(self) -> Any:
        return self.script.pop(0) if self.script else self.default_result

    async def make_request(self, method: RPCEndpoint, params: Any) -> RPCResponse:
        self.calls.append((str(method), params))
        action = self._next_action()
        if isinstance(action, BaseException):
            raise action
        return cast(
            RPCResponse,
            {"jsonrpc": "2.0", "id": next(self.request_counter), "result": action},
        )

    async def make_batch_request(
        self, batch_requests: list[tuple[RPCEndpoint, Any]]
    ) -> list[RPCResponse] | RPCResponse:
        self.batch_calls.append([str(method) for method, _ in batch_requests])
        action = self._next_action()
        if isinstance(action, BaseException):
            raise action
        return [
            cast(
                RPCResponse,
                {"jsonrpc": "2.0", "id": index, "result": action},
            )
            for index in range(len(batch_requests))
        ]


class TestMultipleHTTPProviderState:
    """Regression tests for the base provider state (super().__init__())."""

    def test_base_provider_state_initialized(self) -> None:
        provider = MultipleHTTPProvider([ScriptedProvider("http://a.invalid")])

        # web3 v7 dereferences _is_batching on every RPC call; before the
        # super().__init__() fix this raised AttributeError.
        assert provider._is_batching is False
        assert provider.cache_allowed_requests is False
        assert provider._request_cache is not None
        assert next(provider.request_counter) == 0

    def test_empty_retrieval_providers_rejected(self) -> None:
        with pytest.raises(ValueError, match="At least one retrieval provider"):
            MultipleHTTPProvider([])

    def test_invalid_max_attempts_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_attempts"):
            MultipleHTTPProvider([ScriptedProvider("http://a.invalid")], max_attempts=0)

    def test_execution_providers_default_to_retrieval_providers(self) -> None:
        providers = [ScriptedProvider("http://a.invalid")]
        provider = MultipleHTTPProvider(providers)

        assert provider.execution_providers is provider.retrieval_providers


class TestMultipleHTTPProviderFailover:
    async def test_request_routed_to_first_provider(self) -> None:
        provider_a = ScriptedProvider("http://a.invalid", default_result="0x10")
        provider_b = ScriptedProvider("http://b.invalid", default_result="0x20")
        provider = MultipleHTTPProvider([provider_a, provider_b])

        response = await provider.make_request(RPCEndpoint("eth_blockNumber"), [])

        assert response["result"] == "0x10"
        assert len(provider_a.calls) == 1
        assert len(provider_b.calls) == 0
        assert provider.current_retrieval_provider is provider_a

    async def test_failover_on_connection_error_is_sticky(self) -> None:
        provider_a = ScriptedProvider(
            "http://a.invalid", script=[ClientError("connection refused")]
        )
        provider_b = ScriptedProvider("http://b.invalid", default_result="0x20")
        provider = MultipleHTTPProvider([provider_a, provider_b])

        response = await provider.make_request(RPCEndpoint("eth_blockNumber"), [])

        assert response["result"] == "0x20"
        assert len(provider_a.calls) == 1
        assert len(provider_b.calls) == 1
        # Rotation is sticky: the next request starts from the healthy provider.
        assert provider.current_retrieval_provider is provider_b

        response = await provider.make_request(RPCEndpoint("eth_blockNumber"), [])
        assert response["result"] == "0x20"
        assert len(provider_a.calls) == 1
        assert len(provider_b.calls) == 2

    async def test_failover_on_timeout_error(self) -> None:
        provider_a = ScriptedProvider(
            "http://a.invalid", script=[TimeoutError("request timed out")]
        )
        provider_b = ScriptedProvider("http://b.invalid", default_result="0x20")
        provider = MultipleHTTPProvider([provider_a, provider_b])

        response = await provider.make_request(RPCEndpoint("eth_blockNumber"), [])

        assert response["result"] == "0x20"
        assert len(provider_a.calls) == 1
        assert len(provider_b.calls) == 1

    async def test_all_providers_fail_raises_last_error(self) -> None:
        error_a = ClientError("a is down")
        error_b = ClientError("b is down")
        provider_a = ScriptedProvider("http://a.invalid", script=[error_a])
        provider_b = ScriptedProvider("http://b.invalid", script=[error_b])
        provider = MultipleHTTPProvider([provider_a, provider_b])

        with pytest.raises(ClientError) as excinfo:
            await provider.make_request(RPCEndpoint("eth_blockNumber"), [])

        assert excinfo.value is error_b
        assert len(provider_a.calls) == 1
        assert len(provider_b.calls) == 1

    async def test_max_attempts_caps_providers_tried(self) -> None:
        error_a = ClientError("a is down")
        error_b = ClientError("b is down")
        provider_a = ScriptedProvider("http://a.invalid", script=[error_a])
        provider_b = ScriptedProvider("http://b.invalid", script=[error_b])
        provider_c = ScriptedProvider("http://c.invalid", default_result="0x30")
        provider = MultipleHTTPProvider(
            [provider_a, provider_b, provider_c], max_attempts=2
        )

        with pytest.raises(ClientError) as excinfo:
            await provider.make_request(RPCEndpoint("eth_blockNumber"), [])

        assert excinfo.value is error_b
        assert len(provider_c.calls) == 0
        # Two rotations happened, so the next request starts at provider C.
        assert provider.current_retrieval_provider is provider_c

    async def test_non_retryable_error_propagates_without_rotation(self) -> None:
        provider_a = ScriptedProvider(
            "http://a.invalid", script=[ValueError("bad request")]
        )
        provider_b = ScriptedProvider("http://b.invalid", default_result="0x20")
        provider = MultipleHTTPProvider([provider_a, provider_b])

        with pytest.raises(ValueError, match="bad request"):
            await provider.make_request(RPCEndpoint("eth_blockNumber"), [])

        assert len(provider_b.calls) == 0
        assert provider.current_retrieval_provider is provider_a

    async def test_single_provider_failure_raises(self) -> None:
        error = ClientError("down")
        provider_a = ScriptedProvider("http://a.invalid", script=[error])
        provider = MultipleHTTPProvider([provider_a], max_attempts=3)

        with pytest.raises(ClientError) as excinfo:
            await provider.make_request(RPCEndpoint("eth_blockNumber"), [])

        # A single provider is only tried once per request.
        assert excinfo.value is error
        assert len(provider_a.calls) == 1


class TestMultipleHTTPProviderRouting:
    async def test_execution_methods_use_execution_providers(self) -> None:
        retrieval = ScriptedProvider("http://read.invalid", default_result="0x10")
        execution = ScriptedProvider(
            "http://write.invalid", default_result="0x" + "ab" * 32
        )
        provider = MultipleHTTPProvider([retrieval], execution_providers=[execution])

        await provider.make_request(RPCEndpoint("eth_sendRawTransaction"), ["0x00"])
        assert len(execution.calls) == 1
        assert len(retrieval.calls) == 0

        await provider.make_request(RPCEndpoint("eth_blockNumber"), [])
        assert len(retrieval.calls) == 1
        assert len(execution.calls) == 1

    async def test_execution_failover_does_not_affect_retrieval_pool(self) -> None:
        retrieval = ScriptedProvider("http://read.invalid", default_result="0x10")
        execution_a = ScriptedProvider(
            "http://write-a.invalid", script=[ClientError("down")]
        )
        execution_b = ScriptedProvider(
            "http://write-b.invalid", default_result="0x" + "ab" * 32
        )
        provider = MultipleHTTPProvider(
            [retrieval], execution_providers=[execution_a, execution_b]
        )

        await provider.make_request(RPCEndpoint("eth_sendRawTransaction"), ["0x00"])

        assert provider.current_execution_provider is execution_b
        assert provider.current_retrieval_provider is retrieval

    async def test_batch_request_routed_with_failover(self) -> None:
        provider_a = ScriptedProvider("http://a.invalid", script=[ClientError("down")])
        provider_b = ScriptedProvider("http://b.invalid", default_result="0x20")
        provider = MultipleHTTPProvider([provider_a, provider_b])

        requests = [
            (RPCEndpoint("eth_blockNumber"), []),
            (RPCEndpoint("eth_chainId"), []),
        ]
        responses = await provider.make_batch_request(requests)

        assert isinstance(responses, list)
        assert len(responses) == 2
        assert provider_a.batch_calls == [["eth_blockNumber", "eth_chainId"]]
        assert provider_b.batch_calls == [["eth_blockNumber", "eth_chainId"]]

    async def test_batch_with_execution_method_uses_execution_pool(self) -> None:
        retrieval = ScriptedProvider("http://read.invalid")
        execution = ScriptedProvider("http://write.invalid")
        provider = MultipleHTTPProvider([retrieval], execution_providers=[execution])

        await provider.make_batch_request(
            [
                (RPCEndpoint("eth_blockNumber"), []),
                (RPCEndpoint("eth_sendRawTransaction"), ["0x00"]),
            ]
        )

        assert len(execution.batch_calls) == 1
        assert len(retrieval.batch_calls) == 0


class TestMultipleHTTPProviderWithAsyncWeb3:
    async def test_request_through_async_web3(self) -> None:
        """End-to-end regression: web3 v7 reads provider state on every call."""
        provider_a = ScriptedProvider("http://a.invalid", default_result="0x10")
        w3 = AsyncWeb3(MultipleHTTPProvider([provider_a]))

        block_number = await w3.eth.block_number

        assert block_number == 16
        # web3 forwards params as a tuple
        assert provider_a.calls == [("eth_blockNumber", ())]

    async def test_failover_through_async_web3(self) -> None:
        provider_a = ScriptedProvider(
            "http://a.invalid", script=[ClientError("connection refused")]
        )
        provider_b = ScriptedProvider("http://b.invalid", default_result="0x10")
        w3 = AsyncWeb3(MultipleHTTPProvider([provider_a, provider_b]))

        block_number = await w3.eth.block_number

        assert block_number == 16
        assert len(provider_a.calls) == 1
        assert len(provider_b.calls) == 1


def make_limited_provider(
    monkeypatch: pytest.MonkeyPatch, **kwargs: Any
) -> LimitedHTTPProvider:
    """Build a LimitedHTTPProvider whose HTTP layer is faked in-memory."""
    provider = LimitedHTTPProvider("http://localhost:1", **kwargs)

    async def fake_post(endpoint_uri: Any, data: bytes, **kw: Any) -> bytes:
        payload = json.loads(data)
        if isinstance(payload, list):
            response = [
                {"jsonrpc": "2.0", "id": request["id"], "result": "0x1"}
                for request in payload
            ]
        else:
            response = {"jsonrpc": "2.0", "id": payload["id"], "result": "0x1"}
        return json.dumps(response).encode()

    monkeypatch.setattr(
        provider._request_session_manager, "async_make_post_request", fake_post
    )
    return provider


class TestLimitedHTTPProvider:
    def test_invalid_rate_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_request_per_second"):
            LimitedHTTPProvider("http://localhost:1", max_request_per_second=0)

    def test_invalid_budget_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_request"):
            LimitedHTTPProvider("http://localhost:1", max_request=0)

    async def test_sequential_requests_are_rate_limited(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = make_limited_provider(monkeypatch, max_request_per_second=50)

        start = time.monotonic()
        for _ in range(5):
            response = await provider.make_request(RPCEndpoint("eth_blockNumber"), [])
            assert response["result"] == "0x1"
        elapsed = time.monotonic() - start

        # 5 requests at 50 req/s: the 5th slot is 4 * 20ms after the first.
        assert elapsed >= 4 * 0.02 * 0.9
        assert provider.request_count == 5

    async def test_concurrent_requests_are_rate_limited(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = make_limited_provider(monkeypatch, max_request_per_second=50)

        start = time.monotonic()
        responses = await asyncio.gather(
            *(
                provider.make_request(RPCEndpoint("eth_blockNumber"), [])
                for _ in range(5)
            )
        )
        elapsed = time.monotonic() - start

        assert all(response["result"] == "0x1" for response in responses)
        assert elapsed >= 4 * 0.02 * 0.9
        assert provider.request_count == 5

    async def test_requests_under_rate_are_not_delayed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = make_limited_provider(monkeypatch, max_request_per_second=10_000)

        start = time.monotonic()
        for _ in range(5):
            await provider.make_request(RPCEndpoint("eth_blockNumber"), [])
        elapsed = time.monotonic() - start

        assert elapsed < 1.0
        assert provider.request_count == 5

    async def test_request_budget_enforced(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = make_limited_provider(
            monkeypatch, max_request=3, max_request_per_second=10_000
        )

        for _ in range(3):
            await provider.make_request(RPCEndpoint("eth_blockNumber"), [])

        with pytest.raises(RuntimeError, match="Request budget exhausted"):
            await provider.make_request(RPCEndpoint("eth_blockNumber"), [])

        assert provider.request_count == 3

    async def test_batch_request_counts_as_single_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = make_limited_provider(monkeypatch, max_request_per_second=10_000)

        responses = await provider.make_batch_request(
            [
                (RPCEndpoint("eth_blockNumber"), []),
                (RPCEndpoint("eth_chainId"), []),
            ]
        )

        assert isinstance(responses, list)
        assert len(responses) == 2
        assert provider.request_count == 1
