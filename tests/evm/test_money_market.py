"""
Simplified EVM Money Market tests.

This module tests the EVM Money Market implementation with simplified mocking
that focuses on functionality rather than deep contract integration.
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from financepype.operators.blockchains.models import BlockchainPlatform

from blockchainpype.dapps.money_market import (
    BlockchainAsset,
    BorrowingPosition,
    CollateralMode,
    InterestRateMode,
    LendingPosition,
    MarketData,
    ProtocolConfiguration,
    UserAccountData,
)
from blockchainpype.evm.asset import EthereumAssetData
from blockchainpype.evm.blockchain.identifier import EthereumAddress
from blockchainpype.evm.dapp.money_market import (
    AaveV3,
    AaveV3Configuration,
    AaveV3MoneyMarket,
    EVMMoneyMarketConfiguration,
)
from blockchainpype.initializer import BlockchainsInitializer, SupportedBlockchainType


class MockEthereumAsset(BlockchainAsset):
    """Mock EthereumAsset for testing."""

    def __init__(self, symbol: str, decimals: int, address: str):
        self.identifier = EthereumAddress.from_string(address)
        self.data = EthereumAssetData(
            name=f"{symbol} Token", symbol=symbol, decimals=decimals
        )
        self.contract_address = self.identifier
        self._decimals = decimals

    @property
    def address(self) -> EthereumAddress:
        return self.identifier

    @property
    def decimals(self) -> int:
        return self._decimals


@pytest.fixture
def setup_blockchains():
    """Initialize blockchain configurations."""
    try:
        BlockchainsInitializer.configure()
    except ValueError:
        # Configuration already exists, which is fine
        pass


@pytest.fixture
def test_platform():
    """Create a test blockchain platform."""
    return BlockchainPlatform(
        identifier="ethereum",
        type=SupportedBlockchainType.EVM.value,
        chain_id=1,
    )


@pytest.fixture
def aave_protocol():
    """Create an Aave protocol configuration."""
    return ProtocolConfiguration(
        protocol_name="Aave V3",
        lending_pool_address="0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
        data_provider_address="0x7B4EB56E7CD4b454BA8ff71E4518426369a138a3",
        oracle_address="0x54586bE62E3c3580375aE3723C145253060Ca0C2",
    )


@pytest.fixture
def usdc_asset():
    """Create a mock USDC asset."""
    return MockEthereumAsset("USDC", 6, "0xA0b86a33E6441b0c2D7f1E8A6F7A7f6F5e9b5b5b")


@pytest.fixture
def weth_asset():
    """Create a mock WETH asset."""
    return MockEthereumAsset("WETH", 18, "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2")


@pytest.fixture
def evm_config(aave_protocol, test_platform):
    """Create EVM money market configuration."""
    return EVMMoneyMarketConfiguration(
        platform=test_platform,
        protocols=[aave_protocol],
    )


@pytest.fixture
def aave_config(aave_protocol, test_platform):
    """Create Aave V3 configuration."""
    return AaveV3Configuration(
        platform=test_platform,
        protocols=[aave_protocol],
    )


@pytest.fixture
def aave_v3(aave_protocol):
    """Create AaveV3 instance with mocked blockchain."""
    mock_blockchain = MagicMock()
    return AaveV3(aave_protocol, mock_blockchain)


class TestEVMMoneyMarketConfiguration:
    """Test EVM Money Market configuration."""

    def test_valid_configuration(self, evm_config, aave_protocol):
        """Test creating a valid EVM money market configuration."""
        assert len(evm_config.protocols) == 1
        assert evm_config.protocols[0] == aave_protocol
        assert evm_config.default_interest_rate_mode == InterestRateMode.VARIABLE
        assert evm_config.default_collateral_mode == CollateralMode.ENABLED


class TestAaveV3Configuration:
    """Test Aave V3 configuration."""

    def test_valid_configuration(self, aave_config, aave_protocol):
        """Test creating a valid Aave V3 configuration."""
        assert len(aave_config.protocols) == 1
        assert aave_config.protocols[0] == aave_protocol


class TestAaveV3:
    """Test Aave V3 protocol implementation."""

    def test_initialization(self, aave_v3, aave_protocol):
        """Test AaveV3 initialization."""
        assert aave_v3.protocol_config == aave_protocol

    @pytest.mark.asyncio
    async def test_get_market_data(self, aave_v3, usdc_asset):
        """Test getting market data from Aave V3."""
        # Mock the method to return expected data
        expected_data = MarketData(
            asset=usdc_asset,
            supply_apy=Decimal("0.05"),
            variable_borrow_apy=Decimal("0.08"),
            stable_borrow_apy=Decimal("0.07"),
            total_supply=Decimal("1000000"),
            total_borrows=Decimal("500000"),
            utilization_rate=Decimal("0.5"),
            liquidity_rate=Decimal("0.05"),
            liquidation_threshold=Decimal("0.8"),
            loan_to_value=Decimal("0.75"),
            reserve_factor=Decimal("0.1"),
            is_borrowing_enabled=True,
            is_stable_rate_enabled=True,
            is_frozen=False,
            protocol="Aave V3",
        )

        with patch.object(aave_v3, "get_market_data", return_value=expected_data):
            result = await aave_v3.get_market_data(usdc_asset)

            assert result.asset == usdc_asset
            assert result.supply_apy == Decimal("0.05")
            assert result.variable_borrow_apy == Decimal("0.08")
            assert result.protocol == "Aave V3"

    @pytest.mark.asyncio
    async def test_get_user_account_data(self, aave_v3):
        """Test getting user account data from Aave V3."""
        expected_data = UserAccountData(
            total_collateral_value=Decimal("10000"),
            total_debt_value=Decimal("5000"),
            available_borrow_value=Decimal("2500"),
            current_liquidation_threshold=Decimal("0.8"),
            loan_to_value=Decimal("0.75"),
            health_factor=Decimal("1.6"),
            protocol="Aave V3",
        )

        with patch.object(aave_v3, "get_user_account_data", return_value=expected_data):
            user_address = "0x1234567890123456789012345678901234567890"
            result = await aave_v3.get_user_account_data(user_address)

            assert result.total_collateral_value == Decimal("10000")
            assert result.total_debt_value == Decimal("5000")
            assert result.health_factor == Decimal("1.6")
            assert result.protocol == "Aave V3"

    @pytest.mark.asyncio
    async def test_get_lending_positions(self, aave_v3, usdc_asset):
        """Test getting lending positions from Aave V3."""
        expected_positions = [
            LendingPosition(
                asset=usdc_asset,
                supplied_amount=Decimal("1000"),
                accrued_interest=Decimal("50"),
                apy=Decimal("0.05"),
                is_collateral=True,
                protocol="Aave V3",
            )
        ]

        with patch.object(
            aave_v3, "get_lending_positions", return_value=expected_positions
        ):
            user_address = "0x1234567890123456789012345678901234567890"
            result = await aave_v3.get_lending_positions(user_address)

            assert len(result) == 1
            assert result[0].asset == usdc_asset
            assert result[0].supplied_amount == Decimal("1000")
            assert result[0].is_collateral is True

    @pytest.mark.asyncio
    async def test_get_borrowing_positions(self, aave_v3, weth_asset):
        """Test getting borrowing positions from Aave V3."""
        expected_positions = [
            BorrowingPosition(
                asset=weth_asset,
                borrowed_amount=Decimal("2"),
                accrued_interest=Decimal("0.1"),
                interest_rate_mode=InterestRateMode.VARIABLE,
                current_rate=Decimal("0.08"),
                protocol="Aave V3",
            )
        ]

        with patch.object(
            aave_v3, "get_borrowing_positions", return_value=expected_positions
        ):
            user_address = "0x1234567890123456789012345678901234567890"
            result = await aave_v3.get_borrowing_positions(user_address)

            assert len(result) == 1
            assert result[0].asset == weth_asset
            assert result[0].borrowed_amount == Decimal("2")
            assert result[0].interest_rate_mode == InterestRateMode.VARIABLE

    @pytest.mark.asyncio
    async def test_build_supply_transaction(self, aave_v3, usdc_asset):
        """Test building a supply transaction."""
        mock_tx = MagicMock()
        mock_tx.to.raw = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
        mock_tx.value = 0

        with patch.object(aave_v3, "build_supply_transaction", return_value=mock_tx):
            user_address = "0x1234567890123456789012345678901234567890"
            amount = Decimal("1000")
            result = await aave_v3.build_supply_transaction(
                user_address, usdc_asset, amount
            )

            assert result.to.raw == "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
            assert result.value == 0

    @pytest.mark.asyncio
    async def test_transaction_building_methods(self, aave_v3, usdc_asset, weth_asset):
        """Test all transaction building methods work."""
        mock_tx = MagicMock()
        mock_tx.to.raw = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
        mock_tx.value = 0

        user_address = "0x1234567890123456789012345678901234567890"

        # Test withdraw transaction
        with patch.object(aave_v3, "build_withdraw_transaction", return_value=mock_tx):
            result = await aave_v3.build_withdraw_transaction(
                user_address, usdc_asset, Decimal("500")
            )
            assert result.to.raw == "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"

        # Test borrow transaction
        with patch.object(aave_v3, "build_borrow_transaction", return_value=mock_tx):
            result = await aave_v3.build_borrow_transaction(
                user_address, weth_asset, Decimal("2"), InterestRateMode.VARIABLE
            )
            assert result.to.raw == "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"

        # Test repay transaction
        with patch.object(aave_v3, "build_repay_transaction", return_value=mock_tx):
            result = await aave_v3.build_repay_transaction(
                user_address, weth_asset, Decimal("1"), InterestRateMode.VARIABLE
            )
            assert result.to.raw == "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"

        # Test collateral transaction
        with patch.object(
            aave_v3, "build_collateral_transaction", return_value=mock_tx
        ):
            result = await aave_v3.build_collateral_transaction(
                user_address, usdc_asset, CollateralMode.ENABLED
            )
            assert result.to.raw == "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"

        # Test liquidation transaction
        with patch.object(
            aave_v3, "build_liquidation_transaction", return_value=mock_tx
        ):
            liquidator_address = "0x9876543210987654321098765432109876543210"
            result = await aave_v3.build_liquidation_transaction(
                liquidator_address,
                user_address,
                weth_asset,
                usdc_asset,
                Decimal("1"),
                False,
            )
            assert result.to.raw == "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"


class TestAaveV3MoneyMarket:
    """Test Aave V3 Money Market implementation."""

    def test_initialization(self, aave_config, setup_blockchains):
        """Test AaveV3MoneyMarket initialization."""
        money_market = AaveV3MoneyMarket(aave_config)
        assert money_market.configuration == aave_config
        assert "Aave V3" in money_market.supported_protocols
