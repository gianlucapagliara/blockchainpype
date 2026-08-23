"""
Unit tests for money market models.

This module tests:
- Model validation and constraints with REAL financepype assets
- Data consistency checks
- Property calculations
- Edge cases and error conditions
"""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from blockchainpype.dapps.money_market import (
    BorrowingPosition,
    CollateralMode,
    InterestRateMode,
    LendingPosition,
    MarketData,
    MoneyMarketAction,
    MoneyMarketConfiguration,
    ProtocolConfiguration,
    UserAccountData,
)


def make_market_data(asset, **overrides) -> MarketData:
    """Build a valid MarketData, overriding selected fields."""
    values = {
        "asset": asset,
        "supply_apy": Decimal("0.05"),
        "variable_borrow_apy": Decimal("0.08"),
        "stable_borrow_apy": Decimal("0.07"),
        "total_supply": Decimal("1000000"),
        "total_borrows": Decimal("500000"),
        "utilization_rate": Decimal("0.5"),
        "liquidity_rate": Decimal("0.05"),
        "liquidation_threshold": Decimal("0.8"),
        "loan_to_value": Decimal("0.75"),
        "reserve_factor": Decimal("0.1"),
        "is_borrowing_enabled": True,
        "is_stable_rate_enabled": True,
        "is_frozen": False,
        "protocol": "Aave V3",
    }
    values.update(overrides)
    return MarketData(**values)


class TestEnums:
    """Test enum definitions."""

    def test_interest_rate_mode_values(self):
        """Test InterestRateMode enum values."""
        assert InterestRateMode.STABLE == "stable"
        assert InterestRateMode.VARIABLE == "variable"

    def test_collateral_mode_values(self):
        """Test CollateralMode enum values."""
        assert CollateralMode.ENABLED == "enabled"
        assert CollateralMode.DISABLED == "disabled"

    def test_money_market_action_values(self):
        """Test MoneyMarketAction enum values."""
        assert MoneyMarketAction.SUPPLY == "supply"
        assert MoneyMarketAction.WITHDRAW == "withdraw"
        assert MoneyMarketAction.BORROW == "borrow"
        assert MoneyMarketAction.REPAY == "repay"
        assert MoneyMarketAction.ENABLE_COLLATERAL == "enable_collateral"
        assert MoneyMarketAction.DISABLE_COLLATERAL == "disable_collateral"
        assert MoneyMarketAction.LIQUIDATE == "liquidate"


class TestProtocolConfiguration:
    """Test ProtocolConfiguration model."""

    def test_valid_protocol_configuration(self):
        """Test creating a valid protocol configuration."""
        config = ProtocolConfiguration(
            protocol_name="Aave V3",
            lending_pool_address="0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
            data_provider_address="0x7B4EB56E7CD4b454BA8ff71E4518426369a138a3",
            oracle_address="0x54586bE62E3c3580375aE3723C145253060Ca0C2",
        )

        assert config.protocol_name == "Aave V3"
        assert (
            config.lending_pool_address == "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
        )
        assert (
            config.data_provider_address == "0x7B4EB56E7CD4b454BA8ff71E4518426369a138a3"
        )
        assert config.oracle_address == "0x54586bE62E3c3580375aE3723C145253060Ca0C2"

    def test_optional_fields(self):
        """Test protocol configuration with optional fields."""
        config = ProtocolConfiguration(
            protocol_name="Test Protocol",
            lending_pool_address="0x1234567890123456789012345678901234567890",
            data_provider_address="0x0987654321098765432109876543210987654321",
        )

        assert config.oracle_address is None
        assert config.incentives_controller_address is None


class TestMoneyMarketConfiguration:
    """Test MoneyMarketConfiguration model."""

    def test_valid_money_market_configuration(self, sample_protocol, dapp_platform):
        """Test creating a valid money market configuration."""
        config = MoneyMarketConfiguration(
            platform=dapp_platform,
            protocols=[sample_protocol],
            default_interest_rate_mode=InterestRateMode.VARIABLE,
            default_collateral_mode=CollateralMode.ENABLED,
            liquidation_threshold_buffer=Decimal("0.05"),
        )

        assert len(config.protocols) == 1
        assert config.default_interest_rate_mode == InterestRateMode.VARIABLE
        assert config.default_collateral_mode == CollateralMode.ENABLED
        assert config.liquidation_threshold_buffer == Decimal("0.05")

    def test_default_values(self, sample_protocol, dapp_platform):
        """Test default values in money market configuration."""
        config = MoneyMarketConfiguration(
            platform=dapp_platform, protocols=[sample_protocol]
        )

        assert config.default_interest_rate_mode == InterestRateMode.VARIABLE
        assert config.default_collateral_mode == CollateralMode.ENABLED
        assert config.liquidation_threshold_buffer == Decimal("0.05")


class TestLendingPosition:
    """Test LendingPosition model with real financepype assets."""

    def test_valid_lending_position(self, usdc_asset):
        """Test creating a valid lending position."""
        position = LendingPosition(
            asset=usdc_asset,
            supplied_amount=Decimal("1000.50"),
            accrued_interest=Decimal("25.75"),
            apy=Decimal("0.05"),
            is_collateral=True,
            protocol="Aave V3",
        )

        assert position.asset == usdc_asset
        assert position.asset.data.symbol == "USDC"
        assert position.asset.data.decimals == 6
        assert position.supplied_amount == Decimal("1000.50")
        assert position.accrued_interest == Decimal("25.75")
        assert position.apy == Decimal("0.05")
        assert position.is_collateral is True
        assert position.protocol == "Aave V3"

    def test_lending_position_with_evm_asset(self):
        """A real EVM asset (EthereumNativeAsset) must be accepted."""
        from blockchainpype.evm.asset import EthereumNativeAsset
        from tests.dapps.helpers import build_platform

        eth = EthereumNativeAsset(platform=build_platform())
        position = LendingPosition(
            asset=eth,
            supplied_amount=Decimal("2"),
            accrued_interest=Decimal("0.01"),
            apy=Decimal("0.02"),
            is_collateral=True,
            protocol="Aave V3",
        )

        assert position.asset is eth
        assert position.asset.data.symbol == "ETH"

    def test_non_asset_rejected(self):
        """Objects that are not financepype assets must be rejected."""

        class NotAnAsset:
            symbol = "FAKE"
            decimals = 18

        with pytest.raises(ValidationError):
            LendingPosition(
                asset=NotAnAsset(),
                supplied_amount=Decimal("1000"),
                accrued_interest=Decimal("25"),
                apy=Decimal("0.05"),
                is_collateral=True,
                protocol="Aave V3",
            )

    def test_asset_conversion_round_trip(self, usdc_asset):
        """The real asset exposes raw/decimal conversion helpers."""
        assert usdc_asset.convert_to_raw(Decimal("1.5")) == 1_500_000
        assert usdc_asset.convert_to_decimals(1_500_000) == Decimal("1.5")

    def test_total_balance_calculation(self, usdc_asset):
        """Test total balance calculation."""
        position = LendingPosition(
            asset=usdc_asset,
            supplied_amount=Decimal("1000"),
            accrued_interest=Decimal("25"),
            apy=Decimal("0.05"),
            is_collateral=True,
            protocol="Aave V3",
        )

        assert position.total_balance == Decimal("1025")

    def test_collateral_value_enabled(self, usdc_asset):
        """Test collateral value when collateral is enabled."""
        position = LendingPosition(
            asset=usdc_asset,
            supplied_amount=Decimal("1000"),
            accrued_interest=Decimal("25"),
            apy=Decimal("0.05"),
            is_collateral=True,
            protocol="Aave V3",
        )

        assert position.collateral_value == Decimal("1025")

    def test_collateral_value_disabled(self, usdc_asset):
        """Test collateral value when collateral is disabled."""
        position = LendingPosition(
            asset=usdc_asset,
            supplied_amount=Decimal("1000"),
            accrued_interest=Decimal("25"),
            apy=Decimal("0.05"),
            is_collateral=False,
            protocol="Aave V3",
        )

        assert position.collateral_value == Decimal("0")


class TestBorrowingPosition:
    """Test BorrowingPosition model with real financepype assets."""

    def test_valid_borrowing_position(self, weth_asset):
        """Test creating a valid borrowing position."""
        position = BorrowingPosition(
            asset=weth_asset,
            borrowed_amount=Decimal("0.5"),
            accrued_interest=Decimal("0.01"),
            interest_rate_mode=InterestRateMode.VARIABLE,
            current_rate=Decimal("0.08"),
            protocol="Aave V3",
        )

        assert position.asset == weth_asset
        assert position.asset.data.symbol == "WETH"
        assert position.borrowed_amount == Decimal("0.5")
        assert position.accrued_interest == Decimal("0.01")
        assert position.interest_rate_mode == InterestRateMode.VARIABLE
        assert position.current_rate == Decimal("0.08")
        assert position.protocol == "Aave V3"

    def test_total_debt_calculation(self, weth_asset):
        """Test total debt calculation."""
        position = BorrowingPosition(
            asset=weth_asset,
            borrowed_amount=Decimal("0.5"),
            accrued_interest=Decimal("0.01"),
            interest_rate_mode=InterestRateMode.VARIABLE,
            current_rate=Decimal("0.08"),
            protocol="Aave V3",
        )

        assert position.total_debt == Decimal("0.51")


class TestMarketData:
    """Test MarketData model with real financepype assets."""

    def test_valid_market_data(self, usdc_asset):
        """Test creating valid market data."""
        market_data = make_market_data(usdc_asset)

        assert market_data.asset == usdc_asset
        assert market_data.supply_apy == Decimal("0.05")
        assert market_data.utilization_rate == Decimal("0.5")
        assert market_data.protocol == "Aave V3"

    def test_utilization_rate_validation_lower_bound(self, usdc_asset):
        """Test utilization rate validation - lower bound."""
        with pytest.raises(
            ValidationError, match="Utilization rate must be between 0 and 1"
        ):
            make_market_data(usdc_asset, utilization_rate=Decimal("-0.1"))

    def test_utilization_rate_validation_upper_bound(self, usdc_asset):
        """Test utilization rate validation - upper bound."""
        with pytest.raises(
            ValidationError, match="Utilization rate must be between 0 and 1"
        ):
            make_market_data(usdc_asset, utilization_rate=Decimal("1.5"))

    def test_liquidation_threshold_validation(self, usdc_asset):
        """Test liquidation threshold validation."""
        with pytest.raises(
            ValidationError, match="Liquidation threshold must be between 0 and 1"
        ):
            make_market_data(usdc_asset, liquidation_threshold=Decimal("1.5"))

    def test_loan_to_value_validation(self, usdc_asset):
        """Test loan to value validation."""
        with pytest.raises(
            ValidationError, match="Loan to value must be between 0 and 1"
        ):
            make_market_data(usdc_asset, loan_to_value=Decimal("-0.1"))

    def test_ltv_exceeds_liquidation_threshold_validation(self, usdc_asset):
        """Test that LTV cannot exceed liquidation threshold."""
        with pytest.raises(
            ValidationError, match="Loan to value cannot exceed liquidation threshold"
        ):
            make_market_data(
                usdc_asset,
                liquidation_threshold=Decimal("0.75"),
                loan_to_value=Decimal("0.8"),
            )


class TestUserAccountData:
    """Test UserAccountData model."""

    def test_valid_user_account_data(self):
        """Test creating valid user account data."""
        account_data = UserAccountData(
            total_collateral_value=Decimal("10000"),
            total_debt_value=Decimal("5000"),
            available_borrow_value=Decimal("2500"),
            current_liquidation_threshold=Decimal("0.8"),
            loan_to_value=Decimal("0.75"),
            health_factor=Decimal("1.6"),
            protocol="Aave V3",
        )

        assert account_data.total_collateral_value == Decimal("10000")
        assert account_data.total_debt_value == Decimal("5000")
        assert account_data.health_factor == Decimal("1.6")
        assert account_data.protocol == "Aave V3"

    def test_is_healthy_property(self):
        """Test is_healthy property calculation."""
        healthy_account = UserAccountData(
            total_collateral_value=Decimal("10000"),
            total_debt_value=Decimal("5000"),
            available_borrow_value=Decimal("2500"),
            current_liquidation_threshold=Decimal("0.8"),
            loan_to_value=Decimal("0.75"),
            health_factor=Decimal("1.6"),
            protocol="Aave V3",
        )
        assert healthy_account.is_healthy is True

        unhealthy_account = UserAccountData(
            total_collateral_value=Decimal("10000"),
            total_debt_value=Decimal("9500"),
            available_borrow_value=Decimal("0"),
            current_liquidation_threshold=Decimal("0.8"),
            loan_to_value=Decimal("0.75"),
            health_factor=Decimal("0.9"),
            protocol="Aave V3",
        )
        assert unhealthy_account.is_healthy is False

    @pytest.mark.parametrize(
        ("health_factor", "expected_level"),
        [
            (Decimal("2.5"), "LOW"),
            (Decimal("1.7"), "MEDIUM"),
            (Decimal("1.3"), "HIGH"),
            (Decimal("1.05"), "CRITICAL"),
        ],
    )
    def test_liquidation_risk_levels(self, health_factor, expected_level):
        """Test liquidation risk level boundaries."""
        account_data = UserAccountData(
            total_collateral_value=Decimal("10000"),
            total_debt_value=Decimal("5000"),
            available_borrow_value=Decimal("1000"),
            current_liquidation_threshold=Decimal("0.8"),
            loan_to_value=Decimal("0.75"),
            health_factor=health_factor,
            protocol="Aave V3",
        )
        assert account_data.liquidation_risk_level == expected_level
