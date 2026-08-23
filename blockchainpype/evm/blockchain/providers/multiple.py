"""
This module provides a failover-capable JSON-RPC provider that spreads requests
across multiple HTTP endpoints. Requests are routed to a category of providers
(retrieval for reads, execution for transaction broadcasts) and automatically
rotated to the next endpoint when the current one fails with a connection-level
error.
"""

import logging
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

from aiohttp import ClientError
from web3 import AsyncHTTPProvider
from web3.exceptions import ProviderConnectionError
from web3.providers.async_base import AsyncJSONBaseProvider
from web3.types import RPCEndpoint, RPCResponse

_T = TypeVar("_T")

# Errors indicating the endpoint (rather than the request itself) is unhealthy,
# so a different provider may succeed. Classification is by exception type:
# - ClientError: any aiohttp failure, including HTTP error statuses
#   (ClientResponseError) and connection failures (ClientConnectorError)
# - OSError: socket-level failures; also covers TimeoutError, which has been a
#   subclass since Python 3.10 and is what asyncio timeouts raise
# - ProviderConnectionError: web3's own connectivity error
RETRYABLE_PROVIDER_ERRORS: tuple[type[BaseException], ...] = (
    ClientError,
    OSError,
    ProviderConnectionError,
)


class MultipleHTTPProvider(AsyncJSONBaseProvider):
    """
    Async JSON-RPC provider with automatic failover across multiple endpoints.

    Two provider pools are maintained:
    - retrieval providers serve read traffic (balances, blocks, receipts, ...)
    - execution providers serve transaction broadcasts (see
      ``execution_methods``), so sends can be pinned to trusted or private
      endpoints (e.g. MEV-protected relays)

    Each request goes to the pool's current provider. When it fails with a
    connection-level error (``RETRYABLE_PROVIDER_ERRORS``), the pool rotates to
    the next endpoint and the request is retried, up to ``max_attempts``
    providers per request; the last error is raised when all of them fail. The
    rotation is sticky: subsequent requests start from the last healthy
    provider. Non-connection errors propagate immediately without rotation.

    Attributes:
        retrieval_providers (list[AsyncHTTPProvider]): Endpoints for read traffic
        execution_providers (list[AsyncHTTPProvider]): Endpoints for broadcasts,
            defaults to the retrieval providers
        max_attempts (int): Maximum providers tried per request
    """

    logger = logging.getLogger(
        "blockchainpype.evm.blockchain.providers.MultipleHTTPProvider"
    )

    execution_methods: frozenset[str] = frozenset(
        {
            "eth_sendRawTransaction",
            "eth_sendTransaction",
        }
    )

    def __init__(
        self,
        retrieval_providers: list[AsyncHTTPProvider],
        execution_providers: list[AsyncHTTPProvider] | None = None,
        max_attempts: int = 3,
    ) -> None:
        """
        Initialize the provider pools.

        Args:
            retrieval_providers (list[AsyncHTTPProvider]): Endpoints for read
                traffic, tried in order
            execution_providers (list[AsyncHTTPProvider] | None): Endpoints for
                transaction broadcasts; defaults to the retrieval providers
            max_attempts (int): Maximum providers tried per request, capped at
                the pool size

        Raises:
            ValueError: If no retrieval provider is given or max_attempts < 1
        """
        # web3 v7 reads provider state (e.g. _is_batching, request caching) on
        # every RPC call, so the base initializer must run.
        super().__init__()

        if not retrieval_providers:
            raise ValueError("At least one retrieval provider is required")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")

        self.retrieval_providers = retrieval_providers
        self.execution_providers = (
            execution_providers
            if execution_providers is not None
            else retrieval_providers
        )
        self.max_attempts = max_attempts

        self._retrieval_index = 0
        self._execution_index = 0

    @property
    def current_retrieval_provider(self) -> AsyncHTTPProvider:
        """The retrieval endpoint that will serve the next read request."""
        return self.retrieval_providers[self._retrieval_index]

    @property
    def current_execution_provider(self) -> AsyncHTTPProvider:
        """The execution endpoint that will serve the next broadcast request."""
        return self.execution_providers[self._execution_index]

    def is_execution_method(self, method: RPCEndpoint) -> bool:
        """
        Check whether a JSON-RPC method is routed to the execution providers.

        Args:
            method (RPCEndpoint): The JSON-RPC method name

        Returns:
            bool: True when the method broadcasts transactions
        """
        return method in self.execution_methods

    def _providers_for(self, execution: bool) -> list[AsyncHTTPProvider]:
        return self.execution_providers if execution else self.retrieval_providers

    def _current_provider(self, execution: bool) -> AsyncHTTPProvider:
        if execution:
            return self.current_execution_provider
        return self.current_retrieval_provider

    def _rotate_provider(self, execution: bool) -> None:
        """Advance the pool's current provider to the next endpoint (sticky)."""
        providers = self._providers_for(execution)
        if execution:
            self._execution_index = (self._execution_index + 1) % len(providers)
        else:
            self._retrieval_index = (self._retrieval_index + 1) % len(providers)
        self.logger.info(
            "Switched to provider %s", self._current_provider(execution).endpoint_uri
        )

    async def _with_failover(
        self,
        execution: bool,
        request: Callable[[AsyncHTTPProvider], Coroutine[Any, Any, _T]],
    ) -> _T:
        """
        Run a request against the pool, rotating providers on connection errors.

        Args:
            execution (bool): Whether to use the execution provider pool
            request: Coroutine factory performing the request on a provider

        Returns:
            _T: The provider response

        Raises:
            BaseException: The last connection error after all attempted
                providers failed, or immediately any non-connection error
        """
        providers = self._providers_for(execution)
        attempts = min(self.max_attempts, len(providers))
        last_exception: BaseException | None = None

        for _ in range(attempts):
            provider = self._current_provider(execution)
            try:
                return await request(provider)
            except RETRYABLE_PROVIDER_ERRORS as e:
                last_exception = e
                self.logger.warning(
                    "Provider %s failed (%s: %s); rotating to next provider",
                    provider.endpoint_uri,
                    type(e).__name__,
                    e,
                )
                self._rotate_provider(execution)

        if last_exception is None:  # pragma: no cover - attempts is always >= 1
            raise ProviderConnectionError("No provider available for the request")
        raise last_exception

    async def make_request(self, method: RPCEndpoint, params: Any) -> RPCResponse:
        """
        Make a JSON-RPC request with automatic provider failover.

        Args:
            method (RPCEndpoint): The JSON-RPC method name
            params (Any): The JSON-RPC parameters

        Returns:
            RPCResponse: The JSON-RPC response of the first healthy provider
        """
        return await self._with_failover(
            self.is_execution_method(method),
            lambda provider: provider.make_request(method, params),
        )

    async def make_batch_request(
        self, requests: list[tuple[RPCEndpoint, Any]]
    ) -> list[RPCResponse] | RPCResponse:
        """
        Make a batched JSON-RPC request with automatic provider failover.

        The batch is routed to the execution pool when any of its requests is
        an execution method, otherwise to the retrieval pool.

        Args:
            requests (list[tuple[RPCEndpoint, Any]]): The batched requests

        Returns:
            list[RPCResponse] | RPCResponse: The batched responses, or a single
                error response as returned by the serving provider
        """
        execution = any(self.is_execution_method(method) for method, _ in requests)
        return await self._with_failover(
            execution,
            lambda provider: provider.make_batch_request(requests),
        )
