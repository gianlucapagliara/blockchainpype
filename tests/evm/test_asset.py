from decimal import Decimal

import pytest
from financepype.platforms.blockchain import BlockchainPlatform

from blockchainpype.evm.asset import (
    EthereumAsset,
    EthereumAssetData,
    EthereumNativeAsset,
)
from blockchainpype.evm.blockchain.blockchain import EthereumBlockchainType
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
        type=EthereumBlockchainType,
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
        type=EthereumBlockchainType,
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


def test_ethereum_asset_is_abstract() -> None:
    """EthereumAsset cannot be instantiated without initialize_data."""
    platform = BlockchainPlatform(
        identifier="ethereum",
        type=EthereumBlockchainType,
        chain_id=1,
    )
    address = EthereumAddress.from_string("0x1234567890123456789012345678901234567890")

    with pytest.raises(TypeError, match="abstract"):
        EthereumAsset(platform=platform, identifier=address)  # type: ignore[abstract]


def test_asset_conversion_helpers() -> None:
    """convert_to_raw/convert_to_decimals use the asset decimals exactly."""
    platform = BlockchainPlatform(
        identifier="ethereum",
        type=EthereumBlockchainType,
        chain_id=1,
    )
    asset = MockEthereumAsset(
        platform=platform,
        identifier=EthereumAddress.from_string(
            "0x1234567890123456789012345678901234567890"
        ),
        data=EthereumAssetData(name="Test Token", symbol="TEST", decimals=6),
    )

    assert asset.convert_to_raw(Decimal("1.5")) == 1_500_000
    assert asset.convert_to_decimals(1_500_000) == Decimal("1.5")
    assert asset.convert_to_decimals(1) == Decimal("0.000001")


def test_asset_conversion_requires_initialized_data() -> None:
    """Conversions on an uninitialized asset raise a clear error, not AttributeError."""
    platform = BlockchainPlatform(
        identifier="ethereum",
        type=EthereumBlockchainType,
        chain_id=1,
    )
    asset = MockEthereumAsset(
        platform=platform,
        identifier=EthereumAddress.from_string(
            "0x1234567890123456789012345678901234567890"
        ),
    )
    assert asset.data is None

    with pytest.raises(ValueError, match="not initialized"):
        asset.convert_to_raw(Decimal("1"))
    with pytest.raises(ValueError, match="not initialized"):
        asset.convert_to_decimals(1)


async def test_native_asset_initialize_data_is_noop() -> None:
    platform = BlockchainPlatform(
        identifier="ethereum",
        type=EthereumBlockchainType,
        chain_id=1,
    )
    native_asset = EthereumNativeAsset(platform=platform)
    await native_asset.initialize_data()
    assert native_asset.data.decimals == 18
    assert native_asset.convert_to_decimals(10**18) == Decimal(1)
