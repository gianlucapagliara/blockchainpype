from typing import Any

from web3 import AsyncHTTPProvider
from web3.providers.async_base import AsyncJSONBaseProvider
from web3.types import RPCEndpoint, RPCResponse


class MultipleHTTPProvider(AsyncJSONBaseProvider):
    _logger = None

    public_methods: list[str] = ["eth_call"]

    def __init__(
        self,
        retrieval_providers: list[AsyncHTTPProvider],
        execution_providers: list[AsyncHTTPProvider] | None = None,
        max_attempts: int = 3,
    ) -> None:
        self.retrieval_providers = retrieval_providers
        self.current_retrieval_provider = retrieval_providers[0]

        if execution_providers is None:
            execution_providers = retrieval_providers
        self.execution_providers = execution_providers
        self.current_execution_provider = execution_providers[0]

        self.max_attempts = max_attempts

    def get_next_provider(self, method: RPCEndpoint) -> None:
        providers = (
            self.retrieval_providers
            if method in self.public_methods
            else self.execution_providers
        )
        provider = (
            self.current_retrieval_provider
            if method in self.public_methods
            else self.current_execution_provider
        )

        if len(providers) > 1:
            if provider == providers[-1]:
                provider = providers[0]
            else:
                provider = providers[providers.index(provider) + 1]

        if method in self.public_methods:
            self.current_retrieval_provider = provider
        else:
            self.current_execution_provider = provider

        self.logger.info(f"Switched to provider {provider.endpoint_uri}")

    def get_provider(self, method: RPCEndpoint) -> AsyncHTTPProvider:
        if method in self.public_methods or "get" in method:
            return self.current_retrieval_provider
        else:
            return self.current_execution_provider

    async def make_request(self, method: RPCEndpoint, params: Any) -> RPCResponse:
        tried_providers = set()
        exception = None
        retry = True
        counter = 0

        response = None

        while retry:
            provider = self.get_provider(method)

            # Check if not already tried
            if provider not in tried_providers:
                tried_providers.add(provider)
            elif provider in tried_providers or counter == self.max_attempts:
                raise exception

            try:
                counter += 1
                self.logger.debug(
                    f"Trying provider {provider.endpoint_uri} for method {method}..."
                )
                response = await provider.make_request(method, params)
                retry = False
            except Exception as e:
                self.logger.error(
                    f"Error making request to {provider.endpoint_uri} for method {method}: {type(e)}"
                )

                # Save the exception for later
                exception = e

                # Get the next provider
                if provider.endpoint_uri in str(e):
                    self.get_next_provider(method)

        return response
