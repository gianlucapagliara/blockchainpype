"""
Unit tests for base MoneyMarket class.

This module tests:
- MoneyMarket initialization and configuration
- Protocol strategy management
- Core operation methods
- Error handling and validation
"""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from financepype.operators.blockchains.models import BlockchainPlatform

from blockchainpype.dapps.money_market import (
    BlockchainAsset,
    BorrowingPosition,
    CollateralMode,
    InterestRateMode,
    LendingPosition,
    MarketData,
    MoneyMarket,
    MoneyMarketConfiguration,
    ProtocolConfiguration,
    UserAccountData,
)
from blockchainpype.evm.asset import EthereumAssetData
from blockchainpype.evm.blockchain.identifier import EthereumAddress
from blockchainpype.initializer import BlockchainsInitializer, SupportedBlockchainType


class MockEthereumAsset(BlockchainAsset):
    """Mock EthereumAsset for testing."""

    def __init__(self, symbol: str, decimals: int, address: str):
        # Since BlockchainAsset is just a mock class, we can add attributes directly
        self.identifier = EthereumAddress.from_string(address)
        self.data = EthereumAssetData(
            name=f"{symbol} Token", symbol=symbol, decimals=decimals
        )

    @property
    def address(self) -> EthereumAddress:
        return self.identifier

    @address.setter
    def address(self, value: EthereumAddress) -> None:
        self.identifier = value


class MockProtocolImplementation:
    """Mock protocol implementation for testing."""

    def __init__(self, protocol_name: str):
        self.protocol_name = protocol_name

    async def get_market_data(self, asset) -> MarketData:
        return MarketData(
            asset=asset,
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
            protocol=self.protocol_name,
        )

    async def get_user_account_data(self, user_address: str) -> UserAccountData:
        return UserAccountData(
            total_collateral_value=Decimal("10000"),
            total_debt_value=Decimal("5000"),
            available_borrow_value=Decimal("2500"),
            current_liquidation_threshold=Decimal("0.8"),
            loan_to_value=Decimal("0.75"),
            health_factor=Decimal("1.6"),
            protocol=self.protocol_name,
        )

    async def get_lending_positions(self, user_address: str) -> list[LendingPosition]:
        return []

    async def get_borrowing_positions(
        self, user_address: str
    ) -> list[BorrowingPosition]:
        return []

    async def build_supply_transaction(
        self, asset, amount, user_address, enable_as_collateral=True
    ):
        return MagicMock()

    async def build_withdraw_transaction(self, asset, amount, user_address):
        return MagicMock()

    async def build_borrow_transaction(
        self, asset, amount, interest_rate_mode, user_address
    ):
        return MagicMock()

    async def build_repay_transaction(
        self, asset, amount, interest_rate_mode, user_address, repay_all=False
    ):
        return MagicMock()

    async def build_collateral_transaction(self, asset, mode, user_address):
        return MagicMock()

    async def build_liquidation_transaction(
        self,
        collateral_asset,
        debt_asset,
        user_to_liquidate,
        debt_to_cover,
        receive_collateral=True,
    ):
        return MagicMock()


class MockMoneyMarket(MoneyMarket):
    """Mock implementation of MoneyMarket for testing."""

    def _initialize_protocols(self) -> None:
        """Initialize mock protocol strategies."""
        for protocol_config in self.configuration.protocols:
            self._protocol_strategies[protocol_config.protocol_name] = (
                MockProtocolImplementation(protocol_config.protocol_name)
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
def sample_protocol():
    """Create a sample protocol configuration."""
    return ProtocolConfiguration(
        protocol_name="Test Protocol",
        lending_pool_address="0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
        data_provider_address="0x7B4EB56E7CD4b454BA8ff71E4518426369a138a3",
    )


@pytest.fixture(scope="session", autouse=True)
def setup_blockchains():
    """Setup blockchain configurations for testing."""
    from blockchainpype.factory import BlockchainFactory

    # Reset the factory to avoid configuration conflicts
    BlockchainFactory.reset()

    # Configure blockchains
    BlockchainsInitializer.configure()


@pytest.fixture
def test_platform():
    """Create a test blockchain platform."""
    return BlockchainPlatform(
        identifier="ethereum",
        type=SupportedBlockchainType.EVM.value,
        chain_id=1,
    )


@pytest.fixture
def money_market_config(sample_protocol, test_platform):
    """Create a money market configuration."""
    return MoneyMarketConfiguration(
        platform=test_platform,
        protocols=[sample_protocol],
        default_interest_rate_mode=InterestRateMode.VARIABLE,
        default_collateral_mode=CollateralMode.ENABLED,
    )


@pytest.fixture
def money_market(money_market_config):
    """Create a money market instance."""
    return MockMoneyMarket(money_market_config)


class TestMoneyMarketInitialization:
    """Test MoneyMarket initialization."""

    def test_initialization_with_valid_config(self, money_market_config):
        """Test initialization with valid configuration."""
        money_market = MockMoneyMarket(money_market_config)

        assert money_market.configuration == money_market_config
        assert len(money_market.supported_protocols) == 1
        assert "Test Protocol" in money_market.supported_protocols

    def test_configuration_property(self, money_market):
        """Test configuration property access."""
        config = money_market.configuration
        assert isinstance(config, MoneyMarketConfiguration)
        assert len(config.protocols) == 1

    def test_supported_protocols_property(self, money_market):
        """Test supported protocols property."""
        protocols = money_market.supported_protocols
        assert isinstance(protocols, list)
        assert "Test Protocol" in protocols


class TestMoneyMarketDataQueries:
    """Test MoneyMarket data query methods."""

    @pytest.mark.asyncio
    async def test_get_market_data_with_protocol(self, money_market, usdc_asset):
        """Test getting market data with specific protocol."""
        market_data = await money_market.get_market_data(
            usdc_asset, protocol="Test Protocol"
        )

        assert isinstance(market_data, MarketData)
        assert market_data.asset == usdc_asset
        assert market_data.protocol == "Test Protocol"
        assert market_data.supply_apy == Decimal("0.05")

    @pytest.mark.asyncio
    async def test_get_market_data_default_protocol(self, money_market, usdc_asset):
        """Test getting market data with default protocol."""
        market_data = await money_market.get_market_data(usdc_asset)

        assert isinstance(market_data, MarketData)
        assert market_data.asset == usdc_asset
        assert market_data.protocol == "Test Protocol"

    @pytest.mark.asyncio
    async def test_get_market_data_unsupported_protocol(self, money_market, usdc_asset):
        """Test getting market data with unsupported protocol."""
        with pytest.raises(ValueError, match="Unsupported protocol: Unknown"):
            await money_market.get_market_data(usdc_asset, protocol="Unknown")

    @pytest.mark.asyncio
    async def test_get_user_account_data_with_protocol(self, money_market):
        """Test getting user account data with specific protocol."""
        user_address = "0x1234567890123456789012345678901234567890"
        account_data = await money_market.get_user_account_data(
            user_address, protocol="Test Protocol"
        )

        assert isinstance(account_data, UserAccountData)
        assert account_data.protocol == "Test Protocol"
        assert account_data.health_factor == Decimal("1.6")

    @pytest.mark.asyncio
    async def test_get_user_account_data_default_protocol(self, money_market):
        """Test getting user account data with default protocol."""
        user_address = "0x1234567890123456789012345678901234567890"
        account_data = await money_market.get_user_account_data(user_address)

        assert isinstance(account_data, UserAccountData)
        assert account_data.protocol == "Test Protocol"

    @pytest.mark.asyncio
    async def test_get_lending_positions_with_protocol(self, money_market):
        """Test getting lending positions with specific protocol."""
        user_address = "0x1234567890123456789012345678901234567890"
        positions = await money_market.get_lending_positions(
            user_address, protocol="Test Protocol"
        )

        assert isinstance(positions, list)
        # Mock returns empty list
        assert len(positions) == 0

    @pytest.mark.asyncio
    async def test_get_lending_positions_aggregate(self, money_market):
        """Test getting lending positions aggregated across protocols."""
        user_address = "0x1234567890123456789012345678901234567890"
        positions = await money_market.get_lending_positions(user_address)

        assert isinstance(positions, list)
        # Mock returns empty list
        assert len(positions) == 0

    @pytest.mark.asyncio
    async def test_get_borrowing_positions_with_protocol(self, money_market):
        """Test getting borrowing positions with specific protocol."""
        user_address = "0x1234567890123456789012345678901234567890"
        positions = await money_market.get_borrowing_positions(
            user_address, protocol="Test Protocol"
        )

        assert isinstance(positions, list)
        # Mock returns empty list
        assert len(positions) == 0

    @pytest.mark.asyncio
    async def test_get_borrowing_positions_aggregate(self, money_market):
        """Test getting borrowing positions aggregated across protocols."""
        user_address = "0x1234567890123456789012345678901234567890"
        positions = await money_market.get_borrowing_positions(user_address)

        assert isinstance(positions, list)
        # Mock returns empty list
        assert len(positions) == 0


class TestMoneyMarketOperations:
    """Test MoneyMarket operation methods."""

    @pytest.mark.asyncio
    async def test_supply_with_defaults(self, money_market, usdc_asset):
        """Test supply operation with default parameters."""
        user_address = "0x1234567890123456789012345678901234567890"
        transaction = await money_market.supply(
            usdc_asset, Decimal("1000"), user_address
        )

        # Mock returns MagicMock
        assert transaction is not None

    @pytest.mark.asyncio
    async def test_supply_with_collateral_disabled(self, money_market, usdc_asset):
        """Test supply operation with collateral disabled."""
        user_address = "0x1234567890123456789012345678901234567890"
        transaction = await money_market.supply(
            usdc_asset, Decimal("1000"), user_address, enable_as_collateral=False
        )

        assert transaction is not None

    @pytest.mark.asyncio
    async def test_supply_with_specific_protocol(self, money_market, usdc_asset):
        """Test supply operation with specific protocol."""
        user_address = "0x1234567890123456789012345678901234567890"
        transaction = await money_market.supply(
            usdc_asset, Decimal("1000"), user_address, protocol="Test Protocol"
        )

        assert transaction is not None

    @pytest.mark.asyncio
    async def test_withdraw(self, money_market, usdc_asset):
        """Test withdraw operation."""
        user_address = "0x1234567890123456789012345678901234567890"
        transaction = await money_market.withdraw(
            usdc_asset, Decimal("500"), user_address
        )

        assert transaction is not None

    @pytest.mark.asyncio
    async def test_borrow_with_defaults(self, money_market, weth_asset):
        """Test borrow operation with default parameters."""
        user_address = "0x1234567890123456789012345678901234567890"
        transaction = await money_market.borrow(
            weth_asset, Decimal("0.5"), user_address
        )

        assert transaction is not None

    @pytest.mark.asyncio
    async def test_borrow_with_stable_rate(self, money_market, weth_asset):
        """Test borrow operation with stable interest rate."""
        user_address = "0x1234567890123456789012345678901234567890"
        transaction = await money_market.borrow(
            weth_asset,
            Decimal("0.5"),
            user_address,
            interest_rate_mode=InterestRateMode.STABLE,
        )

        assert transaction is not None

    @pytest.mark.asyncio
    async def test_repay(self, money_market, weth_asset):
        """Test repay operation."""
        user_address = "0x1234567890123456789012345678901234567890"
        transaction = await money_market.repay(weth_asset, Decimal("0.1"), user_address)

        assert transaction is not None

    @pytest.mark.asyncio
    async def test_repay_all(self, money_market, weth_asset):
        """Test repay all operation."""
        user_address = "0x1234567890123456789012345678901234567890"
        transaction = await money_market.repay(
            weth_asset, Decimal("0"), user_address, repay_all=True
        )

        assert transaction is not None

    @pytest.mark.asyncio
    async def test_set_collateral_mode_enable(self, money_market, usdc_asset):
        """Test enabling asset as collateral."""
        user_address = "0x1234567890123456789012345678901234567890"
        transaction = await money_market.set_collateral_mode(
            usdc_asset, CollateralMode.ENABLED, user_address
        )

        assert transaction is not None

    @pytest.mark.asyncio
    async def test_set_collateral_mode_disable(self, money_market, usdc_asset):
        """Test disabling asset as collateral."""
        user_address = "0x1234567890123456789012345678901234567890"
        transaction = await money_market.set_collateral_mode(
            usdc_asset, CollateralMode.DISABLED, user_address
        )

        assert transaction is not None

    @pytest.mark.asyncio
    async def test_liquidate(self, money_market, usdc_asset, weth_asset):
        """Test liquidation operation."""
        user_to_liquidate = "0x9876543210987654321098765432109876543210"
        transaction = await money_market.liquidate(
            weth_asset, usdc_asset, user_to_liquidate, Decimal("1000")
        )

        assert transaction is not None

    @pytest.mark.asyncio
    async def test_liquidate_receive_atoken(self, money_market, usdc_asset, weth_asset):
        """Test liquidation operation receiving aTokens."""
        user_to_liquidate = "0x9876543210987654321098765432109876543210"
        transaction = await money_market.liquidate(
            weth_asset,
            usdc_asset,
            user_to_liquidate,
            Decimal("1000"),
            receive_collateral=False,
        )

        assert transaction is not None


class TestMoneyMarketErrorHandling:
    """Test MoneyMarket error handling."""

    def test_no_protocols_configured(self, test_platform):
        """Test error when no protocols are configured."""
        empty_config = MoneyMarketConfiguration(platform=test_platform, protocols=[])
        money_market = MockMoneyMarket(empty_config)

        assert len(money_market.supported_protocols) == 0

    @pytest.mark.asyncio
    async def test_no_protocols_configured_market_data(self, test_platform):
        """Test error when getting market data with no protocols configured."""
        empty_config = MoneyMarketConfiguration(platform=test_platform, protocols=[])
        money_market = MockMoneyMarket(empty_config)

        with pytest.raises(ValueError, match="No protocols configured"):
            await money_market.get_market_data(MagicMock())

    @pytest.mark.asyncio
    async def test_unsupported_protocol_supply(self, money_market, usdc_asset):
        """Test error when using unsupported protocol for supply."""
        user_address = "0x1234567890123456789012345678901234567890"

        with pytest.raises(ValueError, match="Unsupported protocol: Unknown"):
            await money_market.supply(
                usdc_asset, Decimal("1000"), user_address, protocol="Unknown"
            )

    def test_get_protocol_implementation_unsupported(self, money_market):
        """Test _get_protocol_implementation with unsupported protocol."""
        with pytest.raises(ValueError, match="Unsupported protocol: Unknown"):
            money_market._get_protocol_implementation("Unknown")

    def test_get_protocol_implementation_no_protocols(self, test_platform):
        """Test _get_protocol_implementation with no protocols configured."""
        empty_config = MoneyMarketConfiguration(platform=test_platform, protocols=[])
        money_market = MockMoneyMarket(empty_config)

        with pytest.raises(ValueError, match="No protocols configured"):
            money_market._get_protocol_implementation(None)

    def test_get_protocol_implementation_default(self, money_market):
        """Test _get_protocol_implementation with default protocol."""
        impl = money_market._get_protocol_implementation(None)
        assert isinstance(impl, MockProtocolImplementation)
        assert impl.protocol_name == "Test Protocol"


class TestMoneyMarketMultiProtocol:
    """Test MoneyMarket with multiple protocols."""

    @pytest.fixture
    def multi_protocol_config(self, test_platform):
        """Create configuration with multiple protocols."""
        protocols = [
            ProtocolConfiguration(
                protocol_name="Aave V3",
                lending_pool_address="0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
                data_provider_address="0x7B4EB56E7CD4b454BA8ff71E4518426369a138a3",
            ),
            ProtocolConfiguration(
                protocol_name="Compound V3",
                lending_pool_address="0x1234567890123456789012345678901234567890",
                data_provider_address="0x0987654321098765432109876543210987654321",
            ),
        ]
        return MoneyMarketConfiguration(platform=test_platform, protocols=protocols)

    @pytest.fixture
    def multi_protocol_money_market(self, multi_protocol_config):
        """Create money market with multiple protocols."""
        return MockMoneyMarket(multi_protocol_config)

    def test_multiple_protocols_initialization(self, multi_protocol_money_market):
        """Test initialization with multiple protocols."""
        protocols = multi_protocol_money_market.supported_protocols
        assert len(protocols) == 2
        assert "Aave V3" in protocols
        assert "Compound V3" in protocols

    @pytest.mark.asyncio
    async def test_specific_protocol_selection(
        self, multi_protocol_money_market, usdc_asset
    ):
        """Test selecting specific protocol for operations."""
        market_data = await multi_protocol_money_market.get_market_data(
            usdc_asset, protocol="Compound V3"
        )
        assert market_data.protocol == "Compound V3"
