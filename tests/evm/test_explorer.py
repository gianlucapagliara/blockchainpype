import pytest
from hexbytes import HexBytes

from blockchainpype.evm.blockchain.identifier import EthereumTransactionHash
from blockchainpype.evm.explorer.etherscan import (
    EtherscanConfiguration,
    EtherscanExplorer,
)


@pytest.fixture
def etherscan_config() -> EtherscanConfiguration:
    """Fixture providing a test Etherscan configuration."""
    return EtherscanConfiguration(
        base_url="https://etherscan.io",
        api_url="https://api.etherscan.io/api",
    )


@pytest.fixture
def etherscan_explorer(etherscan_config: EtherscanConfiguration) -> EtherscanExplorer:
    """Fixture providing a test Etherscan explorer instance."""
    return EtherscanExplorer(configuration=etherscan_config)


def test_explorer_initialization(etherscan_explorer: EtherscanExplorer) -> None:
    """Test explorer initialization and basic properties."""
    assert etherscan_explorer.base_url == "https://etherscan.io"
    assert etherscan_explorer.api_url == "https://api.etherscan.io/api"


def test_transaction_link_generation(etherscan_explorer: EtherscanExplorer) -> None:
    """Test generating transaction links."""
    tx_hash = EthereumTransactionHash(
        raw=HexBytes(
            "0xa7c9d7e3d74e8a6a6b2e3f1c2d4b5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d"
        ),
        string="0xa7c9d7e3d74e8a6a6b2e3f1c2d4b5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d",
    )

    link = etherscan_explorer.get_transaction_link(tx_hash)
    assert link == f"{etherscan_explorer.base_url}/tx/{tx_hash.string}"
