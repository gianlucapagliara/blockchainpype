from blockchainpype.evm.blockchain.configuration import (
    EthereumBlockchainConfiguration,
    EthereumConnectivityConfiguration,
)
from blockchainpype.evm.blockchain.providers.limited import LimitedHTTPProvider
from blockchainpype.evm.blockchain.providers.multiple import MultipleHTTPProvider
from blockchainpype.initializer import BlockchainConfigurations


class CustomBlockchainConfigurations(BlockchainConfigurations):
    @classmethod
    def ethereum_configuration(cls) -> EthereumBlockchainConfiguration | None:
        config = super().ethereum_configuration()
        if not config:
            return None

        return config.model_copy(
            update={
                "connectivity": EthereumConnectivityConfiguration(
                    rpc_provider=MultipleHTTPProvider(
                        retrieval_providers=[
                            LimitedHTTPProvider("https://rpc.flashbots.net/"),
                            LimitedHTTPProvider("https://eth.llamarpc.com"),
                            LimitedHTTPProvider("https://rpc.mevblocker.io"),
                        ],
                        execution_providers=None,
                    )
                )
            }
        )
