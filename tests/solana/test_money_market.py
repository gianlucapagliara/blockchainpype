"""
Unit tests for Solana Money Market implementation.

This module tests:
- SolanaMoneyMarket base class
- Solend protocol implementation
- Program interactions and instruction building
- Error handling and edge cases
"""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from financepype.operators.blockchains.models import BlockchainPlatform
from solders.instruction import AccountMeta, Instruction
from solders.pubkey import Pubkey

from blockchainpype.dapps.money_market import (
    CollateralMode,
    InterestRateMode,
    ProtocolConfiguration,
)
from blockchainpype.initializer import BlockchainsInitializer, SupportedBlockchainType
from blockchainpype.solana.asset import SolanaAssetData
from blockchainpype.solana.blockchain.identifier import SolanaAddress
from blockchainpype.solana.dapp.money_market import (
    SolanaMoneyMarket,
    SolanaMoneyMarketConfiguration,
    Solend,
    SolendConfiguration,
    SolendMoneyMarket,
    SolendProgram,
)
from blockchainpype.solana.transaction import SolanaTransaction


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
    """Create a test Solana blockchain platform."""
    return BlockchainPlatform(
        identifier="solana",
        type=SupportedBlockchainType.SOLANA.value,
        chain_id=None,
    )


from blockchainpype.dapps.money_market.models import BlockchainAsset


class MockSolanaAsset(BlockchainAsset):
    """Mock SolanaAsset for testing."""

    def __init__(self, symbol: str, decimals: int, mint: str):
        self.data = SolanaAssetData(
            name=f"{symbol} Token", symbol=symbol, decimals=decimals
        )
        self.mint = SolanaAddress.from_string(mint)

    @property
    def decimals(self) -> int:
        return self.data.decimals


@pytest.fixture
def mock_blockchain():
    """Create a mock blockchain for testing."""
    blockchain = MagicMock()
    return blockchain


@pytest.fixture
def usdc_asset():
    """Create a mock USDC asset."""
    return MockSolanaAsset("USDC", 6, "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")


@pytest.fixture
def sol_asset():
    """Create a mock SOL asset."""
    return MockSolanaAsset("SOL", 9, "So11111111111111111111111111111111111111112")


@pytest.fixture
def solend_protocol():
    """Create a Solend protocol configuration."""
    return ProtocolConfiguration(
        protocol_name="Solend",
        lending_pool_address="So1endDq2YkqhipRh3WViPa8hdiSpxWy6z3Z6tMCpAo",
        data_provider_address="SLendK7ySfcEzyaFqy93gDnD3RtrpXJcnRwb6zFHJSh",
    )


class TestSolanaMoneyMarketConfiguration:
    """Test SolanaMoneyMarketConfiguration."""

    def test_initialization(self, solend_protocol, test_platform):
        """Test configuration initialization."""
        config = SolanaMoneyMarketConfiguration(
            platform=test_platform, protocols=[solend_protocol]
        )
        assert len(config.protocols) == 1
        assert config.protocols[0] == solend_protocol


class TestSolanaMoneyMarket:
    """Test SolanaMoneyMarket base class."""

    def test_initialization(self, solend_protocol, test_platform):
        """Test SolanaMoneyMarket initialization."""
        config = SolanaMoneyMarketConfiguration(
            platform=test_platform, protocols=[solend_protocol]
        )

        # Create a concrete implementation for testing
        class TestSolanaMoneyMarket(SolanaMoneyMarket):
            def _initialize_protocols(self):
                pass

        money_market = TestSolanaMoneyMarket(config)
        assert money_market.configuration == config

    def test_configuration_property(self, solend_protocol, test_platform):
        """Test configuration property."""
        config = SolanaMoneyMarketConfiguration(
            platform=test_platform, protocols=[solend_protocol]
        )

        class TestSolanaMoneyMarket(SolanaMoneyMarket):
            def _initialize_protocols(self):
                pass

        money_market = TestSolanaMoneyMarket(config)
        assert isinstance(money_market.configuration, SolanaMoneyMarketConfiguration)


class TestSolendConfiguration:
    """Test SolendConfiguration."""

    def test_valid_solend_configuration(self, solend_protocol, test_platform):
        """Test valid Solend configuration."""
        config = SolendConfiguration(
            platform=test_platform, protocols=[solend_protocol]
        )
        assert len(config.protocols) == 1
        assert config.protocols[0].protocol_name == "Solend"

    def test_invalid_solend_configuration_no_solend(self, test_platform):
        """Test invalid configuration without Solend protocol."""
        non_solend_protocol = ProtocolConfiguration(
            protocol_name="Mango",
            lending_pool_address="mv3ekLzLbnVPNxjSKvqBpU3ZeZXPQdEC3bp5MDEBG68",
            data_provider_address="MangoCzJ36AjZyKwVj3VnYU4GTonjfVEnJmvvWaxLac",
        )

        with pytest.raises(
            ValueError,
            match="SolendConfiguration requires at least one Solend protocol",
        ):
            SolendConfiguration(platform=test_platform, protocols=[non_solend_protocol])


class TestSolendProgram:
    """Test SolendProgram."""

    def test_initialization(self, test_platform):
        """Test program initialization."""
        address = SolanaAddress.from_string(
            "So1endDq2YkqhipRh3WViPa8hdiSpxWy6z3Z6tMCpAo"
        )
        program = SolendProgram(address, test_platform)

        assert program.address == address
        assert program.configuration.address == address


class TestSolend:
    """Test Solend protocol implementation."""

    @pytest.fixture
    def solend(self, solend_protocol, mock_blockchain, test_platform):
        """Create a Solend instance."""
        return Solend(solend_protocol, mock_blockchain, test_platform)

    def test_initialization(self, solend, solend_protocol):
        """Test Solend initialization."""
        assert solend.protocol_config == solend_protocol
        assert isinstance(solend.program, SolendProgram)

    @pytest.mark.asyncio
    async def test_get_market_data(self, solend, usdc_asset):
        """Test getting market data from Solend."""
        # Mock program initialization
        solend.program._idl = {"instructions": {}}

        market_data = await solend.get_market_data(usdc_asset)

        assert market_data.asset == usdc_asset
        assert market_data.protocol == "Solend"
        # Check dummy data values
        assert market_data.supply_apy == Decimal("0.05")
        assert market_data.variable_borrow_apy == Decimal("0.08")
        assert market_data.utilization_rate == Decimal("0.5")

    @pytest.mark.asyncio
    async def test_get_user_account_data(self, solend):
        """Test getting user account data from Solend."""
        # Mock program initialization
        solend.program._idl = {"instructions": {}}

        user_address = "11111111111111111111111111111112"
        account_data = await solend.get_user_account_data(user_address)

        assert account_data.protocol == "Solend"
        # Check dummy data values
        assert account_data.total_collateral_value == Decimal("10000")
        assert account_data.health_factor == Decimal("1.6")

    @pytest.mark.asyncio
    async def test_get_lending_positions(self, solend):
        """Test getting lending positions from Solend."""
        # Mock program initialization
        solend.program._idl = {"instructions": {}}

        user_address = "11111111111111111111111111111112"
        positions = await solend.get_lending_positions(user_address)

        # Currently returns empty list as noted in implementation
        assert isinstance(positions, list)
        assert len(positions) == 0

    @pytest.mark.asyncio
    async def test_get_borrowing_positions(self, solend):
        """Test getting borrowing positions from Solend."""
        # Mock program initialization
        solend.program._idl = {"instructions": {}}

        user_address = "11111111111111111111111111111112"
        positions = await solend.get_borrowing_positions(user_address)

        # Currently returns empty list as noted in implementation
        assert isinstance(positions, list)
        assert len(positions) == 0

    @pytest.mark.asyncio
    async def test_build_supply_transaction(self, solend, usdc_asset):
        """Test building supply transaction."""
        # Mock program initialization and instruction creation
        solend.program._idl = {"instructions": {"deposit": {}}}

        # Mock the create_instruction method
        mock_instruction = Instruction(
            program_id=Pubkey.from_string(
                "So1endDq2YkqhipRh3WViPa8hdiSpxWy6z3Z6tMCpAo"
            ),
            accounts=[],
            data=b"\x00" * 8,
        )
        solend.program.create_instruction = MagicMock(return_value=mock_instruction)

        user_address = "11111111111111111111111111111112"
        transaction = await solend.build_supply_transaction(
            usdc_asset, Decimal("1000"), user_address, True
        )

        assert isinstance(transaction, SolanaTransaction)
        assert transaction.client_operation_id == "test-operation"

    @pytest.mark.asyncio
    async def test_build_withdraw_transaction(self, solend, usdc_asset):
        """Test building withdraw transaction."""
        # Mock program initialization and instruction creation
        solend.program._idl = {"instructions": {"withdraw": {}}}

        mock_instruction = Instruction(
            program_id=Pubkey.from_string(
                "So1endDq2YkqhipRh3WViPa8hdiSpxWy6z3Z6tMCpAo"
            ),
            accounts=[],
            data=b"\x00" * 8,
        )
        solend.program.create_instruction = MagicMock(return_value=mock_instruction)

        user_address = "11111111111111111111111111111112"
        transaction = await solend.build_withdraw_transaction(
            usdc_asset, Decimal("500"), user_address
        )

        assert isinstance(transaction, SolanaTransaction)
        assert transaction.client_operation_id == "test-operation"

    @pytest.mark.asyncio
    async def test_build_borrow_transaction(self, solend, sol_asset):
        """Test building borrow transaction."""
        # Mock program initialization and instruction creation
        solend.program._idl = {"instructions": {"borrow": {}}}

        mock_instruction = Instruction(
            program_id=Pubkey.from_string(
                "So1endDq2YkqhipRh3WViPa8hdiSpxWy6z3Z6tMCpAo"
            ),
            accounts=[],
            data=b"\x00" * 8,
        )
        solend.program.create_instruction = MagicMock(return_value=mock_instruction)

        user_address = "11111111111111111111111111111112"
        transaction = await solend.build_borrow_transaction(
            sol_asset, Decimal("0.5"), InterestRateMode.VARIABLE, user_address
        )

        assert isinstance(transaction, SolanaTransaction)
        assert transaction.client_operation_id == "test-operation"

    @pytest.mark.asyncio
    async def test_build_repay_transaction(self, solend, sol_asset):
        """Test building repay transaction."""
        # Mock program initialization and instruction creation
        solend.program._idl = {"instructions": {"repay": {}}}

        mock_instruction = Instruction(
            program_id=Pubkey.from_string(
                "So1endDq2YkqhipRh3WViPa8hdiSpxWy6z3Z6tMCpAo"
            ),
            accounts=[],
            data=b"\x00" * 8,
        )
        solend.program.create_instruction = MagicMock(return_value=mock_instruction)

        user_address = "11111111111111111111111111111112"
        transaction = await solend.build_repay_transaction(
            sol_asset, Decimal("0.1"), InterestRateMode.VARIABLE, user_address, False
        )

        assert isinstance(transaction, SolanaTransaction)
        assert transaction.client_operation_id == "test-operation"

    @pytest.mark.asyncio
    async def test_build_repay_all_transaction(self, solend, sol_asset):
        """Test building repay all transaction."""
        # Mock program initialization and instruction creation
        solend.program._idl = {"instructions": {"repay": {}}}

        mock_instruction = Instruction(
            program_id=Pubkey.from_string(
                "So1endDq2YkqhipRh3WViPa8hdiSpxWy6z3Z6tMCpAo"
            ),
            accounts=[],
            data=b"\xff" * 8,
        )
        solend.program.create_instruction = MagicMock(return_value=mock_instruction)

        user_address = "11111111111111111111111111111112"
        transaction = await solend.build_repay_transaction(
            sol_asset, Decimal("0"), InterestRateMode.VARIABLE, user_address, True
        )

        assert isinstance(transaction, SolanaTransaction)
        assert transaction.client_operation_id == "test-operation"

    @pytest.mark.asyncio
    async def test_build_collateral_transaction_enable(self, solend, usdc_asset):
        """Test building enable collateral transaction."""
        # Mock program initialization and instruction creation
        solend.program._idl = {"instructions": {"enable_collateral": {}}}

        mock_instruction = Instruction(
            program_id=Pubkey.from_string(
                "So1endDq2YkqhipRh3WViPa8hdiSpxWy6z3Z6tMCpAo"
            ),
            accounts=[],
            data=b"",
        )
        solend.program.create_instruction = MagicMock(return_value=mock_instruction)

        user_address = "11111111111111111111111111111112"
        transaction = await solend.build_collateral_transaction(
            usdc_asset, CollateralMode.ENABLED, user_address
        )

        assert isinstance(transaction, SolanaTransaction)
        assert transaction.client_operation_id == "test-operation"

        # Verify correct instruction name was used
        call_args = solend.program.create_instruction.call_args
        assert call_args[1]["name"] == "enable_collateral"
        assert call_args[1]["data"] == b""

    @pytest.mark.asyncio
    async def test_build_collateral_transaction_disable(self, solend, usdc_asset):
        """Test building disable collateral transaction."""
        # Mock program initialization and instruction creation
        solend.program._idl = {"instructions": {"disable_collateral": {}}}

        mock_instruction = Instruction(
            program_id=Pubkey.from_string(
                "So1endDq2YkqhipRh3WViPa8hdiSpxWy6z3Z6tMCpAo"
            ),
            accounts=[],
            data=b"",
        )
        solend.program.create_instruction = MagicMock(return_value=mock_instruction)

        user_address = "11111111111111111111111111111112"
        transaction = await solend.build_collateral_transaction(
            usdc_asset, CollateralMode.DISABLED, user_address
        )

        assert isinstance(transaction, SolanaTransaction)
        assert transaction.client_operation_id == "test-operation"

    @pytest.mark.asyncio
    async def test_build_liquidation_transaction(self, solend, usdc_asset, sol_asset):
        """Test building liquidation transaction."""
        # Mock program initialization and instruction creation
        solend.program._idl = {"instructions": {"liquidate": {}}}

        mock_instruction = Instruction(
            program_id=Pubkey.from_string(
                "So1endDq2YkqhipRh3WViPa8hdiSpxWy6z3Z6tMCpAo"
            ),
            accounts=[],
            data=b"\x00" * 8,
        )
        solend.program.create_instruction = MagicMock(return_value=mock_instruction)

        user_to_liquidate = "22222222222222222222222222222222222222222222"
        transaction = await solend.build_liquidation_transaction(
            sol_asset, usdc_asset, user_to_liquidate, Decimal("1000"), True
        )

        assert isinstance(transaction, SolanaTransaction)
        assert transaction.client_operation_id == "test-operation"


class TestSolendMoneyMarket:
    """Test SolendMoneyMarket integration."""

    @pytest.fixture
    def solend_money_market(self, solend_protocol, test_platform):
        """Create a SolendMoneyMarket instance."""
        config = SolendConfiguration(
            platform=test_platform, protocols=[solend_protocol]
        )
        return SolendMoneyMarket(config)

    def test_initialization(self, solend_money_market):
        """Test SolendMoneyMarket initialization."""
        assert isinstance(solend_money_market.configuration, SolendConfiguration)
        assert len(solend_money_market.supported_protocols) == 1
        assert "Solend" in solend_money_market.supported_protocols

    def test_protocol_strategies_initialized(self, solend_money_market):
        """Test that protocol strategies are properly initialized."""
        strategies = solend_money_market._protocol_strategies
        assert "Solend" in strategies
        assert isinstance(strategies["Solend"], Solend)

    @pytest.mark.asyncio
    async def test_supply_operation(self, solend_money_market, usdc_asset):
        """Test supply operation through SolendMoneyMarket."""
        # Mock the protocol strategy
        mock_strategy = AsyncMock()
        mock_transaction = MagicMock()
        mock_strategy.build_supply_transaction.return_value = mock_transaction

        solend_money_market._protocol_strategies["Solend"] = mock_strategy

        user_address = "11111111111111111111111111111112"
        result = await solend_money_market.supply(
            usdc_asset, Decimal("1000"), user_address
        )

        assert result == mock_transaction
        mock_strategy.build_supply_transaction.assert_called_once_with(
            usdc_asset, Decimal("1000"), user_address, True
        )

    @pytest.mark.asyncio
    async def test_borrow_operation(self, solend_money_market, sol_asset):
        """Test borrow operation through SolendMoneyMarket."""
        # Mock the protocol strategy
        mock_strategy = AsyncMock()
        mock_transaction = MagicMock()
        mock_strategy.build_borrow_transaction.return_value = mock_transaction

        solend_money_market._protocol_strategies["Solend"] = mock_strategy

        user_address = "11111111111111111111111111111112"
        result = await solend_money_market.borrow(
            sol_asset, Decimal("0.5"), user_address
        )

        assert result == mock_transaction
        mock_strategy.build_borrow_transaction.assert_called_once_with(
            sol_asset, Decimal("0.5"), InterestRateMode.VARIABLE, user_address
        )


class TestSolendErrorHandling:
    """Test Solend error handling."""

    @pytest.fixture
    def solend_no_init(self, solend_protocol, mock_blockchain, test_platform):
        """Create a Solend instance without initialization."""
        return Solend(solend_protocol, mock_blockchain, test_platform)

    @pytest.mark.asyncio
    async def test_uninitialized_program_market_data(self, solend_no_init, usdc_asset):
        """Test error handling when program is not initialized."""
        # Program is not initialized (idl is None)
        assert not solend_no_init.program.is_initialized

        # Should still work as it initializes the program
        market_data = await solend_no_init.get_market_data(usdc_asset)
        assert market_data.protocol == "Solend"

    @pytest.mark.asyncio
    async def test_instruction_creation_with_accounts(
        self, solend_protocol, mock_blockchain, test_platform
    ):
        """Test instruction creation with proper account handling."""
        solend = Solend(solend_protocol, mock_blockchain, test_platform)
        solend.program._idl = {"instructions": {"deposit": {}}}

        # Mock create_instruction to verify account structure
        def mock_create_instruction(name, accounts, data):
            assert name == "deposit"
            assert isinstance(accounts, list)
            assert len(accounts) > 0
            assert isinstance(accounts[0], AccountMeta)
            return Instruction(
                program_id=Pubkey.from_string(
                    "So1endDq2YkqhipRh3WViPa8hdiSpxWy6z3Z6tMCpAo"
                ),
                accounts=accounts,
                data=data,
            )

        solend.program.create_instruction = mock_create_instruction

        user_address = "11111111111111111111111111111112"
        usdc_asset = MockSolanaAsset(
            "USDC", 6, "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
        )

        transaction = await solend.build_supply_transaction(
            usdc_asset, Decimal("1000"), user_address
        )

        assert isinstance(transaction, SolanaTransaction)
        assert transaction.client_operation_id == "test-operation"
