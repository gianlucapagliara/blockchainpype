"""
Integration tests for Money Market DApp.

This module tests:
- End-to-end workflows
- Multi-protocol scenarios
- Real-world usage patterns
- Error handling in complex scenarios
"""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from financepype.operators.blockchains.models import BlockchainPlatform

from blockchainpype.dapps.money_market import (
    CollateralMode,
    InterestRateMode,
    MoneyMarketConfiguration,
    ProtocolConfiguration,
)
from blockchainpype.evm.asset import EthereumAssetData
from blockchainpype.evm.blockchain.identifier import EthereumAddress
from blockchainpype.evm.dapp.money_market import AaveV3Configuration, AaveV3MoneyMarket
from blockchainpype.initializer import BlockchainsInitializer, SupportedBlockchainType


class MockEthereumAsset:
    """Mock EthereumAsset for testing."""

    def __init__(self, symbol: str, decimals: int, address: str):
        self.identifier = EthereumAddress.from_string(address)
        self.data = EthereumAssetData(
            name=f"{symbol} Token", symbol=symbol, decimals=decimals
        )
        self.contract_address = self.identifier

    @property
    def address(self) -> EthereumAddress:
        return self.identifier

    @property
    def decimals(self) -> int:
        return self.data.decimals

    @property
    def symbol(self) -> str:
        return self.data.symbol

    @address.setter
    def address(self, value: EthereumAddress) -> None:
        self.identifier = value


@pytest.fixture
def usdc_asset():
    """Create a mock USDC asset."""
    return MockEthereumAsset("USDC", 6, "0xA0b86a33E6441b0c2D7f1E8A6F7A7f6F5e9b5b5b")


@pytest.fixture
def weth_asset():
    """Create a mock WETH asset."""
    return MockEthereumAsset("WETH", 18, "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2")


@pytest.fixture
def dai_asset():
    """Create a mock DAI asset."""
    return MockEthereumAsset("DAI", 18, "0x6B175474E89094C44Da98b954EedeAC495271d0F")


@pytest.fixture(scope="session", autouse=True)
def setup_blockchains():
    """Initialize blockchain configurations."""
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
def aave_protocol():
    """Create an Aave protocol configuration."""
    return ProtocolConfiguration(
        protocol_name="Aave V3",
        lending_pool_address="0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
        data_provider_address="0x7B4EB56E7CD4b454BA8ff71E4518426369a138a3",
        oracle_address="0x54586bE62E3c3580375aE3723C145253060Ca0C2",
    )


@pytest.fixture
def compound_protocol():
    """Create a Compound protocol configuration."""
    return ProtocolConfiguration(
        protocol_name="Compound V3",
        lending_pool_address="0x1234567890123456789012345678901234567890",
        data_provider_address="0x0987654321098765432109876543210987654321",
    )


class TestBasicLendingWorkflow:
    """Test basic lending workflow scenarios."""

    @pytest.fixture
    def money_market(self, aave_protocol, test_platform):
        """Create a money market with mocked strategies."""
        config = AaveV3Configuration(platform=test_platform, protocols=[aave_protocol])
        money_market = AaveV3MoneyMarket(config)

        # Mock the protocol strategy
        mock_strategy = AsyncMock()

        # Mock market data
        mock_strategy.get_market_data.return_value = MagicMock(
            supply_apy=Decimal("0.05"),
            variable_borrow_apy=Decimal("0.08"),
            protocol="Aave V3",
        )

        # Mock account data
        mock_strategy.get_user_account_data.return_value = MagicMock(
            total_collateral_value=Decimal("0"),
            total_debt_value=Decimal("0"),
            health_factor=Decimal("999"),
            is_healthy=True,
            liquidation_risk_level="LOW",
        )

        # Mock transactions
        mock_strategy.build_supply_transaction.return_value = MagicMock()
        mock_strategy.build_withdraw_transaction.return_value = MagicMock()
        mock_strategy.build_borrow_transaction.return_value = MagicMock()
        mock_strategy.build_repay_transaction.return_value = MagicMock()

        money_market._protocol_strategies["Aave V3"] = mock_strategy
        return money_market

    @pytest.mark.asyncio
    async def test_supply_and_withdraw_workflow(self, money_market, usdc_asset):
        """Test complete supply and withdraw workflow."""
        user_address = "0x1234567890123456789012345678901234567890"

        # Step 1: Check initial account state
        initial_account = await money_market.get_user_account_data(user_address)
        assert initial_account.total_collateral_value == Decimal("0")

        # Step 2: Supply USDC
        supply_amount = Decimal("1000")
        supply_tx = await money_market.supply(
            usdc_asset, supply_amount, user_address, enable_as_collateral=True
        )
        assert supply_tx is not None

        # Step 3: Check market data
        market_data = await money_market.get_market_data(usdc_asset)
        assert market_data.supply_apy == Decimal("0.05")

        # Step 4: Withdraw partial amount
        withdraw_amount = Decimal("500")
        withdraw_tx = await money_market.withdraw(
            usdc_asset, withdraw_amount, user_address
        )
        assert withdraw_tx is not None

    @pytest.mark.asyncio
    async def test_borrow_and_repay_workflow(
        self, money_market, usdc_asset, weth_asset
    ):
        """Test complete borrow and repay workflow."""
        user_address = "0x1234567890123456789012345678901234567890"

        # Step 1: Supply collateral (USDC)
        collateral_amount = Decimal("2000")
        supply_tx = await money_market.supply(
            usdc_asset, collateral_amount, user_address, enable_as_collateral=True
        )
        assert supply_tx is not None

        # Step 2: Borrow against collateral (WETH)
        borrow_amount = Decimal("0.5")
        borrow_tx = await money_market.borrow(
            weth_asset,
            borrow_amount,
            user_address,
            interest_rate_mode=InterestRateMode.VARIABLE,
        )
        assert borrow_tx is not None

        # Step 3: Partial repay
        repay_amount = Decimal("0.2")
        repay_tx = await money_market.repay(
            weth_asset,
            repay_amount,
            user_address,
            interest_rate_mode=InterestRateMode.VARIABLE,
        )
        assert repay_tx is not None

        # Step 4: Full repay
        full_repay_tx = await money_market.repay(
            weth_asset,
            Decimal("0"),
            user_address,
            interest_rate_mode=InterestRateMode.VARIABLE,
            repay_all=True,
        )
        assert full_repay_tx is not None

    @pytest.mark.asyncio
    async def test_collateral_management_workflow(
        self, money_market, usdc_asset, dai_asset
    ):
        """Test collateral management workflow."""
        user_address = "0x1234567890123456789012345678901234567890"

        # Step 1: Supply USDC as collateral
        await money_market.supply(
            usdc_asset, Decimal("1000"), user_address, enable_as_collateral=True
        )

        # Step 2: Supply DAI without collateral
        await money_market.supply(
            dai_asset, Decimal("500"), user_address, enable_as_collateral=False
        )

        # Step 3: Enable DAI as collateral
        enable_tx = await money_market.set_collateral_mode(
            dai_asset, CollateralMode.ENABLED, user_address
        )
        assert enable_tx is not None

        # Step 4: Disable USDC as collateral
        disable_tx = await money_market.set_collateral_mode(
            usdc_asset, CollateralMode.DISABLED, user_address
        )
        assert disable_tx is not None


class TestMultiProtocolScenarios:
    """Test scenarios with multiple protocols."""

    @pytest.fixture
    def multi_protocol_money_market(
        self, aave_protocol, compound_protocol, test_platform
    ):
        """Create money market with multiple protocols."""
        config = MoneyMarketConfiguration(
            platform=test_platform, protocols=[aave_protocol, compound_protocol]
        )

        # Create a test implementation
        class TestMultiProtocolMoneyMarket:
            def __init__(self, configuration):
                self.configuration = configuration
                self._protocol_strategies = {}
                self._initialize_protocols()

            def _initialize_protocols(self):
                for protocol_config in self.configuration.protocols:
                    mock_strategy = AsyncMock()
                    mock_strategy.get_market_data.return_value = MagicMock(
                        supply_apy=Decimal("0.04")
                        if protocol_config.protocol_name == "Aave V3"
                        else Decimal("0.06"),
                        protocol=protocol_config.protocol_name,
                    )
                    self._protocol_strategies[protocol_config.protocol_name] = (
                        mock_strategy
                    )

            @property
            def supported_protocols(self):
                return list(self._protocol_strategies.keys())

            async def get_market_data(self, asset, protocol=None):
                if protocol:
                    return await self._protocol_strategies[protocol].get_market_data(
                        asset
                    )
                # Return best rate
                best_data = None
                best_apy = Decimal("0")
                for strategy in self._protocol_strategies.values():
                    data = await strategy.get_market_data(asset)
                    if data.supply_apy > best_apy:
                        best_apy = data.supply_apy
                        best_data = data
                return best_data

        return TestMultiProtocolMoneyMarket(config)

    @pytest.mark.asyncio
    async def test_protocol_comparison(self, multi_protocol_money_market, usdc_asset):
        """Test comparing rates across protocols."""
        # Get rates from specific protocols
        aave_data = await multi_protocol_money_market.get_market_data(
            usdc_asset, protocol="Aave V3"
        )
        compound_data = await multi_protocol_money_market.get_market_data(
            usdc_asset, protocol="Compound V3"
        )

        assert aave_data.supply_apy == Decimal("0.04")
        assert compound_data.supply_apy == Decimal("0.06")

        # Get best rate automatically
        best_data = await multi_protocol_money_market.get_market_data(usdc_asset)
        assert best_data.supply_apy == Decimal("0.06")  # Compound has better rate

    def test_supported_protocols(self, multi_protocol_money_market):
        """Test supported protocols listing."""
        protocols = multi_protocol_money_market.supported_protocols
        assert len(protocols) == 2
        assert "Aave V3" in protocols
        assert "Compound V3" in protocols


class TestErrorHandlingScenarios:
    """Test error handling in complex scenarios."""

    @pytest.fixture
    def error_prone_money_market(self, aave_protocol, test_platform):
        """Create money market that simulates various error conditions."""
        config = AaveV3Configuration(platform=test_platform, protocols=[aave_protocol])
        money_market = AaveV3MoneyMarket(config)

        # Mock strategy with error conditions
        mock_strategy = AsyncMock()

        # Simulate contract call failures
        mock_strategy.get_market_data.side_effect = Exception("Contract call failed")
        mock_strategy.build_supply_transaction.side_effect = ValueError(
            "Insufficient allowance"
        )

        money_market._protocol_strategies["Aave V3"] = mock_strategy
        return money_market

    @pytest.mark.asyncio
    async def test_market_data_error_handling(
        self, error_prone_money_market, usdc_asset
    ):
        """Test error handling when market data fails."""
        with pytest.raises(Exception, match="Contract call failed"):
            await error_prone_money_market.get_market_data(usdc_asset)

    @pytest.mark.asyncio
    async def test_transaction_building_error_handling(
        self, error_prone_money_market, usdc_asset
    ):
        """Test error handling when transaction building fails."""
        user_address = "0x1234567890123456789012345678901234567890"

        with pytest.raises(ValueError, match="Insufficient allowance"):
            await error_prone_money_market.supply(
                usdc_asset, Decimal("1000"), user_address
            )

    @pytest.mark.asyncio
    async def test_unsupported_protocol_error(self, aave_protocol, test_platform):
        """Test error when using unsupported protocol."""
        config = AaveV3Configuration(platform=test_platform, protocols=[aave_protocol])
        money_market = AaveV3MoneyMarket(config)

        with pytest.raises(ValueError, match="Unsupported protocol: Unknown Protocol"):
            await money_market.get_market_data(
                MockEthereumAsset(
                    "USDC", 6, "0x1234567890123456789012345678901234567890"
                ),
                protocol="Unknown Protocol",
            )


class TestRealWorldScenarios:
    """Test realistic usage scenarios."""

    @pytest.fixture
    def realistic_money_market(self, aave_protocol, test_platform):
        """Create money market with realistic data."""
        config = AaveV3Configuration(platform=test_platform, protocols=[aave_protocol])
        money_market = AaveV3MoneyMarket(config)

        mock_strategy = AsyncMock()

        # Realistic market data
        def mock_market_data(asset):
            if asset.symbol == "USDC":
                return MagicMock(
                    supply_apy=Decimal("0.025"),  # 2.5% APY
                    variable_borrow_apy=Decimal("0.045"),  # 4.5% APY
                    loan_to_value=Decimal("0.8"),  # 80% LTV
                    liquidation_threshold=Decimal("0.85"),  # 85% liquidation threshold
                    protocol="Aave V3",
                )
            elif asset.symbol == "WETH":
                return MagicMock(
                    supply_apy=Decimal("0.015"),  # 1.5% APY
                    variable_borrow_apy=Decimal("0.025"),  # 2.5% APY
                    loan_to_value=Decimal("0.825"),  # 82.5% LTV
                    liquidation_threshold=Decimal(
                        "0.875"
                    ),  # 87.5% liquidation threshold
                    protocol="Aave V3",
                )
            return MagicMock(protocol="Aave V3")

        mock_strategy.get_market_data.side_effect = mock_market_data

        # Realistic account progression
        account_states = {
            "initial": MagicMock(
                total_collateral_value=Decimal("0"),
                total_debt_value=Decimal("0"),
                health_factor=Decimal("999"),
                liquidation_risk_level="LOW",
            ),
            "after_supply": MagicMock(
                total_collateral_value=Decimal("10000"),
                total_debt_value=Decimal("0"),
                health_factor=Decimal("999"),
                liquidation_risk_level="LOW",
            ),
            "after_borrow": MagicMock(
                total_collateral_value=Decimal("10000"),
                total_debt_value=Decimal("4000"),
                health_factor=Decimal("2.125"),  # (10000 * 0.85) / 4000
                liquidation_risk_level="LOW",
            ),
            "high_risk": MagicMock(
                total_collateral_value=Decimal("10000"),
                total_debt_value=Decimal("7000"),
                health_factor=Decimal("1.214"),  # (10000 * 0.85) / 7000
                liquidation_risk_level="HIGH",
            ),
        }

        current_state = ["initial"]

        def mock_account_data(user_address):
            return account_states[current_state[0]]

        mock_strategy.get_user_account_data.side_effect = mock_account_data
        mock_strategy.build_supply_transaction.return_value = MagicMock()
        mock_strategy.build_borrow_transaction.return_value = MagicMock()

        # Helper to change state
        def change_state(new_state):
            current_state[0] = new_state

        money_market._protocol_strategies["Aave V3"] = mock_strategy
        money_market._change_state = change_state
        return money_market

    @pytest.mark.asyncio
    async def test_progressive_borrowing_scenario(
        self, realistic_money_market, usdc_asset, weth_asset
    ):
        """Test a progressive borrowing scenario with risk management."""
        user_address = "0x1234567890123456789012345678901234567890"

        # Step 1: Check initial state
        account = await realistic_money_market.get_user_account_data(user_address)
        assert account.total_collateral_value == Decimal("0")
        assert account.liquidation_risk_level == "LOW"

        # Step 2: Supply collateral
        await realistic_money_market.supply(
            usdc_asset, Decimal("10000"), user_address, enable_as_collateral=True
        )
        realistic_money_market._change_state("after_supply")

        account = await realistic_money_market.get_user_account_data(user_address)
        assert account.total_collateral_value == Decimal("10000")

        # Step 3: Conservative borrow (40% of collateral value)
        await realistic_money_market.borrow(
            weth_asset,
            Decimal("2"),
            user_address,  # ~$4000 worth
        )
        realistic_money_market._change_state("after_borrow")

        account = await realistic_money_market.get_user_account_data(user_address)
        assert account.health_factor > Decimal("2.0")
        assert account.liquidation_risk_level == "LOW"

        # Step 4: Aggressive borrowing leading to high risk
        realistic_money_market._change_state("high_risk")
        account = await realistic_money_market.get_user_account_data(user_address)
        assert account.health_factor < Decimal("1.5")
        assert account.liquidation_risk_level == "HIGH"

    @pytest.mark.asyncio
    async def test_yield_optimization_scenario(
        self, realistic_money_market, usdc_asset, weth_asset
    ):
        """Test yield optimization decision making."""
        # Compare supply APYs
        usdc_market = await realistic_money_market.get_market_data(usdc_asset)
        weth_market = await realistic_money_market.get_market_data(weth_asset)

        assert usdc_market.supply_apy == Decimal("0.025")  # 2.5%
        assert weth_market.supply_apy == Decimal("0.015")  # 1.5%

        # USDC offers better supply yield
        assert usdc_market.supply_apy > weth_market.supply_apy

        # But WETH has better borrowing terms
        assert weth_market.variable_borrow_apy < usdc_market.variable_borrow_apy
        assert weth_market.loan_to_value > usdc_market.loan_to_value

    @pytest.mark.asyncio
    async def test_liquidation_scenario(
        self, realistic_money_market, usdc_asset, weth_asset
    ):
        """Test liquidation scenario."""
        liquidator_address = "0x9876543210987654321098765432109876543210"
        user_to_liquidate = "0x1111111111111111111111111111111111111111"

        # Mock liquidation transaction
        mock_strategy = realistic_money_market._protocol_strategies["Aave V3"]
        mock_strategy.build_liquidation_transaction.return_value = MagicMock()

        # Execute liquidation
        liquidation_tx = await realistic_money_market.liquidate(
            collateral_asset=usdc_asset,
            debt_asset=weth_asset,
            user_to_liquidate=user_to_liquidate,
            debt_to_cover=Decimal("1000"),  # Cover $1000 of debt
            receive_collateral=True,
        )

        assert liquidation_tx is not None
        mock_strategy.build_liquidation_transaction.assert_called_once()


class TestPerformanceScenarios:
    """Test performance-related scenarios."""

    @pytest.fixture
    def performance_money_market(self, aave_protocol, test_platform):
        """Create money market for performance testing."""
        config = AaveV3Configuration(platform=test_platform, protocols=[aave_protocol])
        money_market = AaveV3MoneyMarket(config)

        # Mock fast responses
        mock_strategy = AsyncMock()
        mock_strategy.get_market_data.return_value = MagicMock(protocol="Aave V3")
        mock_strategy.get_user_account_data.return_value = MagicMock(protocol="Aave V3")
        mock_strategy.get_lending_positions.return_value = []
        mock_strategy.get_borrowing_positions.return_value = []

        money_market._protocol_strategies["Aave V3"] = mock_strategy
        return money_market

    @pytest.mark.asyncio
    async def test_concurrent_operations(
        self, performance_money_market, usdc_asset, weth_asset
    ):
        """Test concurrent operations performance."""
        import asyncio

        user_address = "0x1234567890123456789012345678901234567890"

        # Execute multiple operations concurrently
        tasks = [
            performance_money_market.get_market_data(usdc_asset),
            performance_money_market.get_market_data(weth_asset),
            performance_money_market.get_user_account_data(user_address),
            performance_money_market.get_lending_positions(user_address),
            performance_money_market.get_borrowing_positions(user_address),
        ]

        results = await asyncio.gather(*tasks)

        # All operations should complete successfully
        assert len(results) == 5
        assert all(result is not None for result in results)

    @pytest.mark.asyncio
    async def test_batch_position_queries(self, performance_money_market):
        """Test batch querying of positions."""
        users = [
            "0x1111111111111111111111111111111111111111",
            "0x2222222222222222222222222222222222222222",
            "0x3333333333333333333333333333333333333333",
        ]

        # Query all users concurrently
        import asyncio

        tasks = []
        for user in users:
            tasks.extend(
                [
                    performance_money_market.get_lending_positions(user),
                    performance_money_market.get_borrowing_positions(user),
                    performance_money_market.get_user_account_data(user),
                ]
            )

        results = await asyncio.gather(*tasks)

        # Should get 3 results per user (9 total)
        assert len(results) == 9
        assert all(result is not None for result in results)
