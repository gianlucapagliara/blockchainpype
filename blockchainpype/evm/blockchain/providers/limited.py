"""
This module provides a rate-limited HTTP JSON-RPC provider. It throttles
outgoing requests client-side so applications can stay within the request
quotas of public or paid RPC endpoints.
"""

import asyncio
import time
from typing import Any

from eth_typing import URI
from web3.providers import AsyncHTTPProvider
from web3.types import RPCEndpoint, RPCResponse


class LimitedHTTPProvider(AsyncHTTPProvider):
    """
    AsyncHTTPProvider enforcing a client-side request rate limit.

    Requests are throttled to at most ``max_request_per_second`` using a
    min-interval scheduler: each request reserves the next free time slot
    (slots are spaced ``1 / max_request_per_second`` apart) and sleeps until
    that slot is due, so bursts are smoothed out instead of dropped. Slot
    reservation is concurrency-safe; concurrent requests queue up behind
    evenly spaced slots.

    Optionally, ``max_request`` caps the total number of requests served over
    the provider's lifetime (a request budget); once exhausted, further
    requests raise instead of hitting the endpoint.

    Attributes:
        max_request (int | None): Lifetime request budget, None for unlimited
        max_request_per_second (float): Maximum request rate per second
    """

    def __init__(
        self,
        endpoint_uri: URI | str | None = None,
        request_kwargs: Any | None = None,
        max_request: int | None = None,
        max_request_per_second: float = 100,
        **kwargs: Any,
    ) -> None:
        """
        Initialize the rate-limited provider.

        Args:
            endpoint_uri (URI | str | None): The HTTP endpoint of the RPC node
            request_kwargs (Any | None): Extra kwargs for the HTTP requests
            max_request (int | None): Lifetime request budget, None (default)
                for unlimited
            max_request_per_second (float): Maximum request rate per second
            **kwargs: Additional arguments forwarded to AsyncHTTPProvider

        Raises:
            ValueError: If max_request_per_second is not positive or
                max_request is set below 1
        """
        super().__init__(endpoint_uri, request_kwargs, **kwargs)

        if max_request_per_second <= 0:
            raise ValueError("max_request_per_second must be positive")
        if max_request is not None and max_request < 1:
            raise ValueError("max_request must be at least 1 when set")

        self.max_request = max_request
        self.max_request_per_second = max_request_per_second

        self._min_interval = 1.0 / max_request_per_second
        self._slot_lock = asyncio.Lock()
        # Monotonic time of the next free request slot; 0.0 means "now".
        self._next_slot = 0.0
        self._request_count = 0

    @property
    def request_count(self) -> int:
        """Total number of requests issued through this provider so far."""
        return self._request_count

    async def _acquire_slot(self) -> None:
        """
        Reserve the next request slot, sleeping until it is due.

        The lock only guards the slot bookkeeping; the wait happens outside it
        so concurrent requests each reserve their own slot instead of
        serializing on the network call.

        Raises:
            RuntimeError: If the lifetime request budget is exhausted
        """
        async with self._slot_lock:
            if self.max_request is not None and self._request_count >= self.max_request:
                raise RuntimeError(
                    f"Request budget exhausted for {self.endpoint_uri}: "
                    f"max_request={self.max_request}"
                )
            self._request_count += 1

            now = time.monotonic()
            scheduled = max(now, self._next_slot)
            self._next_slot = scheduled + self._min_interval
            delay = scheduled - now

        if delay > 0:
            await asyncio.sleep(delay)

    async def make_request(self, method: RPCEndpoint, params: Any) -> RPCResponse:
        """
        Make a JSON-RPC request, throttled to the configured rate.

        Args:
            method (RPCEndpoint): The JSON-RPC method name
            params (Any): The JSON-RPC parameters

        Returns:
            RPCResponse: The JSON-RPC response
        """
        await self._acquire_slot()
        return await super().make_request(method, params)

    async def make_batch_request(
        self, batch_requests: list[tuple[RPCEndpoint, Any]]
    ) -> list[RPCResponse] | RPCResponse:
        """
        Make a batched JSON-RPC request, throttled as a single request.

        Args:
            batch_requests (list[tuple[RPCEndpoint, Any]]): The batched requests

        Returns:
            list[RPCResponse] | RPCResponse: The batched responses, or a single
                error response
        """
        await self._acquire_slot()
        return await super().make_batch_request(batch_requests)
