import pytest
from financepype.platforms.blockchain import BlockchainPlatform, BlockchainType
from web3 import AsyncWeb3

from blockchainpype.evm.asset import EthereumNativeAsset
from blockchainpype.evm.blockchain.blockchain import EthereumBlockchain
from blockchainpype.evm.blockchain.configuration import (
    EthereumBlockchainConfiguration,
    EthereumConnectivityConfiguration,
    EthereumNativeAssetConfiguration,
)
from blockchainpype.evm.blockchain.identifier import EthereumAddress


@pytest.fixture
def ethereum_config() -> EthereumBlockchainConfiguration:
    """Fixture providing a test Ethereum blockchain configuration."""
    return EthereumBlockchainConfiguration(
        platform=BlockchainPlatform(
            identifier="ethereum",
            type=BlockchainType.EVM,
            chain_id=1,
        ),
        native_asset=EthereumNativeAssetConfiguration(),
        connectivity=EthereumConnectivityConfiguration(
            rpc_provider=AsyncWeb3.AsyncHTTPProvider("https://eth.llamarpc.com"),
        ),
        explorer=None,
    )


@pytest.fixture
def ethereum_blockchain(
    ethereum_config: EthereumBlockchainConfiguration,
) -> EthereumBlockchain:
    """Fixture providing a test Ethereum blockchain instance."""
    return EthereumBlockchain(configuration=ethereum_config)


@pytest.mark.asyncio
async def test_blockchain_initialization(
    ethereum_blockchain: EthereumBlockchain,
) -> None:
    """Test blockchain instance initialization and basic properties."""
    assert ethereum_blockchain.platform.identifier == "ethereum"
    assert ethereum_blockchain.platform.type == BlockchainType.EVM
    assert ethereum_blockchain.platform.chain_id == 1

    assert isinstance(ethereum_blockchain.native_asset, EthereumNativeAsset)
    assert ethereum_blockchain.native_asset.data.symbol == "ETH"
    assert ethereum_blockchain.native_asset.data.decimals == 18

    assert ethereum_blockchain.explorer is None


@pytest.mark.asyncio
async def test_fetch_block_number(ethereum_blockchain: EthereumBlockchain) -> None:
    """Test fetching the current block number."""
    block_number = await ethereum_blockchain.fetch_block_number()
    assert isinstance(block_number, int)
    assert block_number > 0


@pytest.mark.asyncio
async def test_fetch_native_asset_balance(
    ethereum_blockchain: EthereumBlockchain,
) -> None:
    """Test fetching native asset (ETH) balance."""
    # Using Ethereum Foundation's address as an example
    address = EthereumAddress.from_string("0xde0B295669a9FD93d5F28D9Ec85E40f4cb697BAe")
    balance = await ethereum_blockchain.fetch_native_asset_balance(address)

    assert balance is not None
    assert balance >= 0


@pytest.mark.asyncio
async def test_fetch_block_timestamp(ethereum_blockchain: EthereumBlockchain) -> None:
    """Test fetching block timestamp."""
    # Fetch current block number first
    block_number = await ethereum_blockchain.fetch_block_number()

    # Fetch timestamp for that block
    timestamp = await ethereum_blockchain.fetch_block_timestamp(block_number)

    assert timestamp is not None
    assert timestamp > 0  # Ethereum timestamps are Unix timestamps
