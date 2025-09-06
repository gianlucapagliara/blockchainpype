from decimal import Decimal
from enum import Enum
from typing import Self

from financepype.operators.dapps.dapp import DecentralizedApplicationConfiguration
from pydantic import BaseModel, ConfigDict, model_validator


# Mock BlockchainAsset for testing purposes
class BlockchainAsset:
    """Mock blockchain asset class."""

    pass


class InterestRateMode(str, Enum):
    """Interest rate mode for borrowing positions."""

    STABLE = "stable"
    VARIABLE = "variable"


class CollateralMode(str, Enum):
    """Collateral usage mode for assets."""

    ENABLED = "enabled"
    DISABLED = "disabled"


class MoneyMarketAction(str, Enum):
    """Available actions in money market protocols."""

    SUPPLY = "supply"
    WITHDRAW = "withdraw"
    BORROW = "borrow"
    REPAY = "repay"
    ENABLE_COLLATERAL = "enable_collateral"
    DISABLE_COLLATERAL = "disable_collateral"
    LIQUIDATE = "liquidate"


class ProtocolConfiguration(BaseModel):
    """Configuration for a specific money market protocol."""

    protocol_name: str
    lending_pool_address: str
    data_provider_address: str
    oracle_address: str | None = None
    incentives_controller_address: str | None = None


class MoneyMarketConfiguration(DecentralizedApplicationConfiguration):
    """Configuration for money market DApp."""

    protocols: list[ProtocolConfiguration]
    default_interest_rate_mode: InterestRateMode = InterestRateMode.VARIABLE
    default_collateral_mode: CollateralMode = CollateralMode.ENABLED
    liquidation_threshold_buffer: Decimal = Decimal("0.05")  # 5% buffer


class LendingPosition(BaseModel):
    """Represents a lending position in a money market."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    asset: BlockchainAsset
    supplied_amount: Decimal
    accrued_interest: Decimal
    apy: Decimal
    is_collateral: bool
    protocol: str

    @property
    def total_balance(self) -> Decimal:
        """Total balance including accrued interest."""
        return self.supplied_amount + self.accrued_interest

    @property
    def collateral_value(self) -> Decimal:
        """Value available as collateral (if enabled)."""
        return self.total_balance if self.is_collateral else Decimal("0")


class BorrowingPosition(BaseModel):
    """Represents a borrowing position in a money market."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    asset: BlockchainAsset
    borrowed_amount: Decimal
    accrued_interest: Decimal
    interest_rate_mode: InterestRateMode
    current_rate: Decimal
    protocol: str

    @property
    def total_debt(self) -> Decimal:
        """Total debt including accrued interest."""
        return self.borrowed_amount + self.accrued_interest


class MarketData(BaseModel):
    """Market data for a specific asset in a money market protocol."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    asset: BlockchainAsset
    supply_apy: Decimal
    variable_borrow_apy: Decimal
    stable_borrow_apy: Decimal
    total_supply: Decimal
    total_borrows: Decimal
    utilization_rate: Decimal
    liquidity_rate: Decimal
    liquidation_threshold: Decimal
    loan_to_value: Decimal
    reserve_factor: Decimal
    is_borrowing_enabled: bool
    is_stable_rate_enabled: bool
    is_frozen: bool
    protocol: str

    @model_validator(mode="after")
    def validate_market_data(self) -> Self:
        """Validate market data consistency."""
        if self.utilization_rate < 0 or self.utilization_rate > 1:
            raise ValueError("Utilization rate must be between 0 and 1")

        if self.liquidation_threshold < 0 or self.liquidation_threshold > 1:
            raise ValueError("Liquidation threshold must be between 0 and 1")

        if self.loan_to_value < 0 or self.loan_to_value > 1:
            raise ValueError("Loan to value must be between 0 and 1")

        if self.loan_to_value > self.liquidation_threshold:
            raise ValueError("Loan to value cannot exceed liquidation threshold")

        return self


class UserAccountData(BaseModel):
    """User's account data across all positions."""

    total_collateral_value: Decimal
    total_debt_value: Decimal
    available_borrow_value: Decimal
    current_liquidation_threshold: Decimal
    loan_to_value: Decimal
    health_factor: Decimal
    protocol: str

    @property
    def is_healthy(self) -> bool:
        """Check if the account is above liquidation threshold."""
        return self.health_factor > Decimal("1.0")

    @property
    def liquidation_risk_level(self) -> str:
        """Get risk level based on health factor."""
        if self.health_factor > Decimal("2.0"):
            return "LOW"
        elif self.health_factor > Decimal("1.5"):
            return "MEDIUM"
        elif self.health_factor > Decimal("1.1"):
            return "HIGH"
        else:
            return "CRITICAL"
