from typing import Any

from eth_typing import URI
from web3.providers import AsyncHTTPProvider


class LimitedHTTPProvider(AsyncHTTPProvider):
    def __init__(
        self,
        endpoint_uri: URI | str | None = None,
        request_kwargs: Any | None = None,
        max_request: int = 10000,
        max_request_per_second: int = 100,
    ) -> None:
        super().__init__(endpoint_uri, request_kwargs)
        self.max_request = max_request
        self.max_request_per_second = max_request_per_second

    async def make_request(self, method: str, params: Any) -> Any:
        return await super().make_request(method, params)
