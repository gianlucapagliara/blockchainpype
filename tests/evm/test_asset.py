import pytest
from financepype.platforms.blockchain import BlockchainPlatform, BlockchainType

from blockchainpype.evm.asset import (
    EthereumAsset,
    EthereumAssetData,
    EthereumNativeAsset,
)
from blockchainpype.evm.blockchain.identifier import (
    EthereumAddress,
    EthereumNullAddress,
)


class MockEthereumAsset(EthereumAsset):
    """Mock EthereumAsset class for testing."""

    async def initialize_data(self) -> None:
        """Mock initialize_data method."""
        pass


def test_ethereum_asset_data() -> None:
    """Test EthereumAssetData creation and properties."""
    asset_data = EthereumAssetData(
        name="Test Token",
        symbol="TEST",
        decimals=18,
    )

    assert asset_data.name == "Test Token"
    assert asset_data.symbol == "TEST"
    assert asset_data.decimals == 18


def test_ethereum_asset() -> None:
    """Test EthereumAsset creation and properties."""
    asset_data = EthereumAssetData(
        name="Test Token",
        symbol="TEST",
        decimals=18,
    )
    address = EthereumAddress.from_string("0x1234567890123456789012345678901234567890")
    platform = BlockchainPlatform(
        identifier="ethereum",
        type=BlockchainType.EVM,
        chain_id=1,
    )

    asset = MockEthereumAsset(
        platform=platform,
        identifier=address,
        data=asset_data,
    )

    assert asset.identifier == address
    assert asset.data == asset_data
    assert asset.data.name == "Test Token"
    assert asset.data.symbol == "TEST"
    assert asset.data.decimals == 18


def test_ethereum_native_asset() -> None:
    """Test EthereumNativeAsset creation and properties."""
    platform = BlockchainPlatform(
        identifier="ethereum",
        type=BlockchainType.EVM,
        chain_id=1,
    )
    native_asset = EthereumNativeAsset(platform=platform)

    # Check identifier is null address
    assert isinstance(native_asset.identifier, EthereumNullAddress)

    # Check predefined data
    assert native_asset.data.name == "Ethereum"
    assert native_asset.data.symbol == "ETH"
    assert native_asset.data.decimals == 18


def test_ethereum_address_validation() -> None:
    """Test validation of Ethereum addresses."""
    # Valid address
    valid_address = "0x1234567890123456789012345678901234567890"
    address = EthereumAddress.from_string(valid_address)
    assert str(address) == valid_address.lower()

    # Invalid address
    with pytest.raises(ValueError):
        EthereumAddress.from_string("0xinvalid")
