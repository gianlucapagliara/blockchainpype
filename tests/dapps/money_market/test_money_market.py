"""
Unit tests for base MoneyMarket class.

This module tests:
- MoneyMarket initialization, abstract contract and configuration
- Protocol strategy dispatch (named, first-available and aggregation)
- Core operation methods delegating to the strategies
- Error handling and validation
"""

from decimal import Decimal

import pytest
from financepype.assets.blockchain import BlockchainAsset
from financepype.owners.wallet import BlockchainWallet
from financepype.platforms.blockchain import BlockchainPlatform

from blockchainpype.dapps.money_market import (
    BorrowingPosition,
    CollateralMode,
    InterestRateMode,
    LendingPosition,
    MarketData,
    MoneyMarket,
    MoneyMarketConfiguration,
    ProtocolConfiguration,
    ProtocolImplementation,
    UserAccountData,
)
from tests.dapps.helpers import FIXED_TIMESTAMP, StubTransaction, make_transaction


class StubProtocolImplementation:
    """Stub protocol implementation recording calls and returning real models."""

    def __init__(
        self,
        protocol_name: str,
        platform: BlockchainPlatform,
        position_asset: BlockchainAsset | None = None,
        supply_apy: Decimal = Decimal("0.05"),
    ):
        self.protocol_name = protocol_name
        self.platform = platform
        self.position_asset = position_asset
        self.supply_apy = supply_apy
        self.wallet: BlockchainWallet | None = None
        self.calls: list[tuple] = []

    def set_wallet(self, wallet: BlockchainWallet | None) -> None:
        self.calls.append(("set_wallet", wallet))
        self.wallet = wallet

    async def get_market_data(self, asset: BlockchainAsset) -> MarketData:
        self.calls.append(("get_market_data", asset))
        return MarketData(
            asset=asset,
            supply_apy=self.supply_apy,
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
        self.calls.append(("get_user_account_data", user_address))
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
        self.calls.append(("get_lending_positions", user_address))
        if self.position_asset is None:
            return []
        return [
            LendingPosition(
                asset=self.position_asset,
                supplied_amount=Decimal("1000"),
                accrued_interest=Decimal("25"),
                apy=self.supply_apy,
                is_collateral=True,
                protocol=self.protocol_name,
            )
        ]

    async def get_borrowing_positions(
        self, user_address: str
    ) -> list[BorrowingPosition]:
        self.calls.append(("get_borrowing_positions", user_address))
        if self.position_asset is None:
            return []
        return [
            BorrowingPosition(
                asset=self.position_asset,
                borrowed_amount=Decimal("0.5"),
                accrued_interest=Decimal("0.01"),
                interest_rate_mode=InterestRateMode.VARIABLE,
                current_rate=Decimal("0.08"),
                protocol=self.protocol_name,
            )
        ]

    async def build_supply_transaction(
        self,
        asset: BlockchainAsset,
        amount: Decimal,
        user_address: str,
        enable_as_collateral: bool = True,
    ) -> StubTransaction:
        self.calls.append(
            (
                "build_supply_transaction",
                asset,
                amount,
                user_address,
                enable_as_collateral,
            )
        )
        return make_transaction(self.platform)

    async def build_withdraw_transaction(
        self,
        asset: BlockchainAsset,
        amount: Decimal,
        user_address: str,
        withdraw_all: bool = False,
    ) -> StubTransaction:
        self.calls.append(
            (
                "build_withdraw_transaction",
                asset,
                amount,
                user_address,
                withdraw_all,
            )
        )
        return make_transaction(self.platform)

    async def build_borrow_transaction(
        self,
        asset: BlockchainAsset,
        amount: Decimal,
        interest_rate_mode: InterestRateMode,
        user_address: str,
    ) -> StubTransaction:
        self.calls.append(
            (
                "build_borrow_transaction",
                asset,
                amount,
                interest_rate_mode,
                user_address,
            )
        )
        return make_transaction(self.platform)

    async def build_repay_transaction(
        self,
        asset: BlockchainAsset,
        amount: Decimal,
        interest_rate_mode: InterestRateMode,
        user_address: str,
        repay_all: bool = False,
    ) -> StubTransaction:
        self.calls.append(
            (
                "build_repay_transaction",
                asset,
                amount,
                interest_rate_mode,
                user_address,
                repay_all,
            )
        )
        return make_transaction(self.platform)

    async def build_collateral_transaction(
        self, asset: BlockchainAsset, mode: CollateralMode, user_address: str
    ) -> StubTransaction:
        self.calls.append(("build_collateral_transaction", asset, mode, user_address))
        return make_transaction(self.platform)

    async def build_liquidation_transaction(
        self,
        collateral_asset: BlockchainAsset,
        debt_asset: BlockchainAsset,
        user_to_liquidate: str,
        debt_to_cover: Decimal,
        receive_collateral: bool = True,
    ) -> StubTransaction:
        self.calls.append(
            (
                "build_liquidation_transaction",
                collateral_asset,
                debt_asset,
                user_to_liquidate,
                debt_to_cover,
                receive_collateral,
            )
        )
        return make_transaction(self.platform)


class StubMoneyMarket(MoneyMarket):
    """Concrete MoneyMarket wiring one stub strategy per configured protocol."""

    def _initialize_protocols(self) -> None:
        for protocol_config in self.configuration.protocols:
            self._protocol_strategies[protocol_config.protocol_name] = (
                StubProtocolImplementation(
                    protocol_config.protocol_name, self.configuration.platform
                )
            )


USER_ADDRESS = "0x1234567890123456789012345678901234567890"


@pytest.fixture
def money_market_config(sample_protocol, dapp_platform):
    """Create a money market configuration."""
    return MoneyMarketConfiguration(
        platform=dapp_platform,
        protocols=[sample_protocol],
        default_interest_rate_mode=InterestRateMode.VARIABLE,
        default_collateral_mode=CollateralMode.ENABLED,
    )


@pytest.fixture
def money_market(money_market_config):
    """Create a money market instance."""
    return StubMoneyMarket(money_market_config)


@pytest.fixture
def stub_strategy(money_market) -> StubProtocolImplementation:
    """The single wired stub strategy."""
    return money_market._protocol_strategies["Test Protocol"]


class TestMoneyMarketInitialization:
    """Test MoneyMarket initialization."""

    def test_abstract_base_cannot_be_instantiated(self, money_market_config):
        """MoneyMarket must be a real ABC enforcing _initialize_protocols."""
        with pytest.raises(TypeError, match="_initialize_protocols"):
            MoneyMarket(money_market_config)

    def test_initialization_with_valid_config(self, money_market_config):
        """Test initialization with valid configuration."""
        money_market = StubMoneyMarket(money_market_config)

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

    def test_current_timestamp_from_blockchain(self, money_market):
        """current_timestamp must delegate to the resolved blockchain."""
        assert money_market.current_timestamp == FIXED_TIMESTAMP


class TestMoneyMarketDataQueries:
    """Test MoneyMarket data query methods."""

    async def test_get_market_data_with_protocol(self, money_market, usdc_asset):
        """Test getting market data with specific protocol."""
        market_data = await money_market.get_market_data(
            usdc_asset, protocol="Test Protocol"
        )

        assert isinstance(market_data, MarketData)
        assert market_data.asset == usdc_asset
        assert market_data.protocol == "Test Protocol"
        assert market_data.supply_apy == Decimal("0.05")

    async def test_get_market_data_default_protocol(self, money_market, usdc_asset):
        """Test getting market data with default protocol."""
        market_data = await money_market.get_market_data(usdc_asset)

        assert isinstance(market_data, MarketData)
        assert market_data.asset == usdc_asset
        assert market_data.protocol == "Test Protocol"

    async def test_get_market_data_unsupported_protocol(self, money_market, usdc_asset):
        """Test getting market data with unsupported protocol."""
        with pytest.raises(ValueError, match="Unsupported protocol: Unknown"):
            await money_market.get_market_data(usdc_asset, protocol="Unknown")

    async def test_get_user_account_data_with_protocol(self, money_market):
        """Test getting user account data with specific protocol."""
        account_data = await money_market.get_user_account_data(
            USER_ADDRESS, protocol="Test Protocol"
        )

        assert isinstance(account_data, UserAccountData)
        assert account_data.protocol == "Test Protocol"
        assert account_data.health_factor == Decimal("1.6")

    async def test_get_user_account_data_default_protocol(self, money_market):
        """Test getting user account data with default protocol."""
        account_data = await money_market.get_user_account_data(USER_ADDRESS)

        assert isinstance(account_data, UserAccountData)
        assert account_data.protocol == "Test Protocol"

    async def test_get_lending_positions_with_protocol(
        self, money_market, stub_strategy, usdc_asset
    ):
        """Test getting lending positions with specific protocol."""
        stub_strategy.position_asset = usdc_asset

        positions = await money_market.get_lending_positions(
            USER_ADDRESS, protocol="Test Protocol"
        )

        assert len(positions) == 1
        assert positions[0].asset == usdc_asset
        assert positions[0].protocol == "Test Protocol"

    async def test_get_borrowing_positions_with_protocol(
        self, money_market, stub_strategy, weth_asset
    ):
        """Test getting borrowing positions with specific protocol."""
        stub_strategy.position_asset = weth_asset

        positions = await money_market.get_borrowing_positions(
            USER_ADDRESS, protocol="Test Protocol"
        )

        assert len(positions) == 1
        assert positions[0].asset == weth_asset
        assert positions[0].total_debt == Decimal("0.51")


class TestMoneyMarketOperations:
    """Test MoneyMarket operation methods."""

    async def test_supply_with_defaults(self, money_market, stub_strategy, usdc_asset):
        """Supply must forward the configured default collateral mode."""
        transaction = await money_market.supply(
            usdc_asset, Decimal("1000"), USER_ADDRESS
        )

        assert isinstance(transaction, StubTransaction)
        assert stub_strategy.calls[-1] == (
            "build_supply_transaction",
            usdc_asset,
            Decimal("1000"),
            USER_ADDRESS,
            True,  # default_collateral_mode == ENABLED
        )

    async def test_supply_with_collateral_disabled(
        self, money_market, stub_strategy, usdc_asset
    ):
        """Test supply operation with collateral disabled."""
        await money_market.supply(
            usdc_asset, Decimal("1000"), USER_ADDRESS, enable_as_collateral=False
        )

        assert stub_strategy.calls[-1][4] is False

    async def test_supply_with_specific_protocol(self, money_market, usdc_asset):
        """Test supply operation with specific protocol."""
        transaction = await money_market.supply(
            usdc_asset, Decimal("1000"), USER_ADDRESS, protocol="Test Protocol"
        )

        assert isinstance(transaction, StubTransaction)

    async def test_withdraw(self, money_market, stub_strategy, usdc_asset):
        """Test withdraw operation."""
        transaction = await money_market.withdraw(
            usdc_asset, Decimal("500"), USER_ADDRESS
        )

        assert isinstance(transaction, StubTransaction)
        assert stub_strategy.calls[-1] == (
            "build_withdraw_transaction",
            usdc_asset,
            Decimal("500"),
            USER_ADDRESS,
            False,
        )

    async def test_withdraw_all(self, money_market, stub_strategy, usdc_asset):
        """withdraw_all must reach the strategy, mirroring repay_all."""
        transaction = await money_market.withdraw(
            usdc_asset, Decimal("0"), USER_ADDRESS, withdraw_all=True
        )

        assert isinstance(transaction, StubTransaction)
        assert stub_strategy.calls[-1] == (
            "build_withdraw_transaction",
            usdc_asset,
            Decimal("0"),
            USER_ADDRESS,
            True,
        )

    async def test_withdraw_with_specific_protocol(self, money_market, usdc_asset):
        """withdraw_all must also be forwarded on the named-protocol path."""
        transaction = await money_market.withdraw(
            usdc_asset,
            Decimal("0"),
            USER_ADDRESS,
            withdraw_all=True,
            protocol="Test Protocol",
        )

        assert isinstance(transaction, StubTransaction)

    async def test_borrow_with_defaults(self, money_market, stub_strategy, weth_asset):
        """Borrow must forward the configured default interest rate mode."""
        transaction = await money_market.borrow(
            weth_asset, Decimal("0.5"), USER_ADDRESS
        )

        assert isinstance(transaction, StubTransaction)
        assert stub_strategy.calls[-1] == (
            "build_borrow_transaction",
            weth_asset,
            Decimal("0.5"),
            InterestRateMode.VARIABLE,
            USER_ADDRESS,
        )

    async def test_borrow_with_stable_rate(
        self, money_market, stub_strategy, weth_asset
    ):
        """Test borrow operation with stable interest rate."""
        await money_market.borrow(
            weth_asset,
            Decimal("0.5"),
            USER_ADDRESS,
            interest_rate_mode=InterestRateMode.STABLE,
        )

        assert stub_strategy.calls[-1][3] == InterestRateMode.STABLE

    async def test_repay(self, money_market, stub_strategy, weth_asset):
        """Test repay operation."""
        transaction = await money_market.repay(weth_asset, Decimal("0.1"), USER_ADDRESS)

        assert isinstance(transaction, StubTransaction)
        assert stub_strategy.calls[-1] == (
            "build_repay_transaction",
            weth_asset,
            Decimal("0.1"),
            InterestRateMode.VARIABLE,
            USER_ADDRESS,
            False,
        )

    async def test_repay_all(self, money_market, stub_strategy, weth_asset):
        """Test repay all operation."""
        await money_market.repay(weth_asset, Decimal("0"), USER_ADDRESS, repay_all=True)

        assert stub_strategy.calls[-1][5] is True

    async def test_set_collateral_mode_enable(
        self, money_market, stub_strategy, usdc_asset
    ):
        """Test enabling asset as collateral."""
        await money_market.set_collateral_mode(
            usdc_asset, CollateralMode.ENABLED, USER_ADDRESS
        )

        assert stub_strategy.calls[-1] == (
            "build_collateral_transaction",
            usdc_asset,
            CollateralMode.ENABLED,
            USER_ADDRESS,
        )

    async def test_set_collateral_mode_disable(
        self, money_market, stub_strategy, usdc_asset
    ):
        """Test disabling asset as collateral."""
        await money_market.set_collateral_mode(
            usdc_asset, CollateralMode.DISABLED, USER_ADDRESS
        )

        assert stub_strategy.calls[-1][2] == CollateralMode.DISABLED

    async def test_liquidate(self, money_market, stub_strategy, usdc_asset, weth_asset):
        """Test liquidation operation."""
        user_to_liquidate = "0x9876543210987654321098765432109876543210"
        transaction = await money_market.liquidate(
            weth_asset, usdc_asset, user_to_liquidate, Decimal("1000")
        )

        assert isinstance(transaction, StubTransaction)
        assert stub_strategy.calls[-1] == (
            "build_liquidation_transaction",
            weth_asset,
            usdc_asset,
            user_to_liquidate,
            Decimal("1000"),
            True,
        )

    async def test_liquidate_receive_atoken(
        self, money_market, stub_strategy, usdc_asset, weth_asset
    ):
        """Test liquidation operation receiving aTokens."""
        user_to_liquidate = "0x9876543210987654321098765432109876543210"
        await money_market.liquidate(
            weth_asset,
            usdc_asset,
            user_to_liquidate,
            Decimal("1000"),
            receive_collateral=False,
        )

        assert stub_strategy.calls[-1][5] is False


class StrategyWithoutSetWallet:
    """Every money-market method except the wallet binding one."""

    async def get_market_data(self, asset): ...
    async def get_user_account_data(self, user_address): ...
    async def get_lending_positions(self, user_address): ...
    async def get_borrowing_positions(self, user_address): ...
    async def build_supply_transaction(self, *args, **kwargs): ...
    async def build_withdraw_transaction(self, *args, **kwargs): ...
    async def build_borrow_transaction(self, *args, **kwargs): ...
    async def build_repay_transaction(self, *args, **kwargs): ...
    async def build_collateral_transaction(self, *args, **kwargs): ...
    async def build_liquidation_transaction(self, *args, **kwargs): ...


class TestProtocolContract:
    """The money-market ProtocolImplementation is a runtime-checkable contract."""

    def test_protocol_is_runtime_checkable(self, stub_strategy):
        assert isinstance(stub_strategy, ProtocolImplementation)

    def test_incomplete_implementation_does_not_conform(self):
        assert not isinstance(StrategyWithoutSetWallet(), ProtocolImplementation)

    def test_set_wallet_is_part_of_the_contract(self, stub_strategy):
        sentinel = object()
        stub_strategy.set_wallet(sentinel)
        assert stub_strategy.wallet is sentinel

        stub_strategy.set_wallet(None)
        assert stub_strategy.wallet is None


class TestMoneyMarketErrorHandling:
    """Test MoneyMarket error handling."""

    def test_no_protocols_configured(self, dapp_platform):
        """Test error when no protocols are configured."""
        empty_config = MoneyMarketConfiguration(platform=dapp_platform, protocols=[])
        money_market = StubMoneyMarket(empty_config)

        assert len(money_market.supported_protocols) == 0

    async def test_no_protocols_configured_market_data(self, dapp_platform, usdc_asset):
        """Test error when getting market data with no protocols configured."""
        empty_config = MoneyMarketConfiguration(platform=dapp_platform, protocols=[])
        money_market = StubMoneyMarket(empty_config)

        with pytest.raises(ValueError, match="No protocols configured"):
            await money_market.get_market_data(usdc_asset)

    async def test_unsupported_protocol_supply(self, money_market, usdc_asset):
        """Test error when using unsupported protocol for supply."""
        with pytest.raises(ValueError, match="Unsupported protocol: Unknown"):
            await money_market.supply(
                usdc_asset, Decimal("1000"), USER_ADDRESS, protocol="Unknown"
            )

    def test_get_protocol_implementation_unsupported(self, money_market):
        """Test _get_protocol_implementation with unsupported protocol."""
        with pytest.raises(ValueError, match="Unsupported protocol: Unknown"):
            money_market._get_protocol_implementation("Unknown")

    def test_get_protocol_implementation_no_protocols(self, dapp_platform):
        """Test _get_protocol_implementation with no protocols configured."""
        empty_config = MoneyMarketConfiguration(platform=dapp_platform, protocols=[])
        money_market = StubMoneyMarket(empty_config)

        with pytest.raises(ValueError, match="No protocols configured"):
            money_market._get_protocol_implementation(None)

    def test_get_protocol_implementation_default(self, money_market):
        """Test _get_protocol_implementation with default protocol."""
        impl = money_market._get_protocol_implementation(None)
        assert isinstance(impl, StubProtocolImplementation)
        assert impl.protocol_name == "Test Protocol"

    async def test_strategy_error_propagates(
        self, money_market, stub_strategy, usdc_asset
    ):
        """Errors raised by a strategy must reach the caller unchanged."""

        async def failing_build(*args, **kwargs):
            raise ValueError("Insufficient allowance")

        stub_strategy.build_supply_transaction = failing_build

        with pytest.raises(ValueError, match="Insufficient allowance"):
            await money_market.supply(usdc_asset, Decimal("1000"), USER_ADDRESS)


class TestMoneyMarketMultiProtocol:
    """Test MoneyMarket with multiple protocols."""

    @pytest.fixture
    def multi_protocol_config(self, dapp_platform):
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
        return MoneyMarketConfiguration(platform=dapp_platform, protocols=protocols)

    @pytest.fixture
    def multi_protocol_money_market(self, multi_protocol_config):
        """Create money market with multiple protocols."""
        return StubMoneyMarket(multi_protocol_config)

    def test_multiple_protocols_initialization(self, multi_protocol_money_market):
        """Test initialization with multiple protocols."""
        protocols = multi_protocol_money_market.supported_protocols
        assert protocols == ["Aave V3", "Compound V3"]

    async def test_specific_protocol_selection(
        self, multi_protocol_money_market, usdc_asset
    ):
        """Test selecting specific protocol for operations."""
        market_data = await multi_protocol_money_market.get_market_data(
            usdc_asset, protocol="Compound V3"
        )
        assert market_data.protocol == "Compound V3"

    async def test_lending_positions_aggregate_across_protocols(
        self, multi_protocol_money_market, usdc_asset, weth_asset
    ):
        """Positions must be aggregated from every configured protocol."""
        strategies = multi_protocol_money_market._protocol_strategies
        strategies["Aave V3"].position_asset = usdc_asset
        strategies["Compound V3"].position_asset = weth_asset

        positions = await multi_protocol_money_market.get_lending_positions(
            USER_ADDRESS
        )

        assert len(positions) == 2
        assert {position.protocol for position in positions} == {
            "Aave V3",
            "Compound V3",
        }
        assert {position.asset for position in positions} == {usdc_asset, weth_asset}

    async def test_borrowing_positions_aggregate_across_protocols(
        self, multi_protocol_money_market, usdc_asset, weth_asset
    ):
        """Borrowing positions must be aggregated from every protocol."""
        strategies = multi_protocol_money_market._protocol_strategies
        strategies["Aave V3"].position_asset = usdc_asset
        strategies["Compound V3"].position_asset = weth_asset

        positions = await multi_protocol_money_market.get_borrowing_positions(
            USER_ADDRESS
        )

        assert len(positions) == 2
        assert {position.protocol for position in positions} == {
            "Aave V3",
            "Compound V3",
        }
