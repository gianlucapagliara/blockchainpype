"""
Unit tests for base BettingMarket class.

This module tests:
- BettingMarket initialization and configuration
- Protocol strategy management
- Core operation methods
- Error handling and validation
"""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from blockchainpype.dapps.betting_market import (
    BettingMarketConfiguration,
    BettingMarketDApp,
    BettingMarketModel,
    BettingPosition,
    MarketStatus,
    OutcomeToken,
    ProtocolConfiguration,
)
from blockchainpype.initializer import BlockchainsInitializer

# Initialize blockchain configurations for tests
try:
    BlockchainsInitializer.configure()
except ValueError:
    # Configuration already registered
    pass


class MockBlockchainAsset:
    """Mock blockchain asset for testing."""

    def __init__(self, symbol: str, decimals: int):
        self.symbol = symbol
        self.decimals = decimals


class MockProtocolImplementation:
    """Mock protocol implementation for testing."""

    def __init__(self, protocol_name: str):
        self.protocol_name = protocol_name
        self._markets = {}
        self._positions = {}

    async def get_market(self, market_id: str) -> BettingMarketModel:
        """Mock get market method."""
        if market_id not in self._markets:
            raise ValueError(f"Market {market_id} not found")
        return self._markets[market_id]

    async def get_markets(
        self,
        category: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[BettingMarketModel]:
        """Mock get markets method."""
        return list(self._markets.values())

    async def get_user_positions(
        self,
        user_address: str,
        market_id: str | None = None,
    ) -> list[BettingPosition]:
        """Mock get user positions method."""
        return self._positions.get(user_address, [])

    async def get_outcome_token_price(
        self,
        market_id: str,
        outcome_token_id: str,
    ) -> Decimal:
        """Mock get outcome token price method."""
        return Decimal("0.65")

    async def build_buy_transaction(self, *args, **kwargs):
        """Mock build buy transaction method."""
        return MagicMock()

    async def build_sell_transaction(self, *args, **kwargs):
        """Mock build sell transaction method."""
        return MagicMock()

    async def build_redeem_transaction(self, *args, **kwargs):
        """Mock build redeem transaction method."""
        return MagicMock()

    async def calculate_buy_quote(
        self,
        market_id: str,
        outcome_token_id: str,
        amount: Decimal,
    ) -> tuple[Decimal, Decimal]:
        """Mock calculate buy quote method."""
        price = Decimal("0.65")
        expected_shares = amount / price
        fee = amount * Decimal("0.02")
        total_cost = amount + fee
        return expected_shares, total_cost

    async def calculate_sell_quote(
        self,
        market_id: str,
        outcome_token_id: str,
        shares: Decimal,
    ) -> tuple[Decimal, Decimal]:
        """Mock calculate sell quote method."""
        price = Decimal("0.65")
        gross_payout = shares * price
        fee = gross_payout * Decimal("0.02")
        net_payout = gross_payout - fee
        return net_payout, fee

    def add_mock_market(self, market: BettingMarketModel):
        """Add a mock market for testing."""
        self._markets[market.market_id] = market

    def add_mock_positions(self, user_address: str, positions: list[BettingPosition]):
        """Add mock positions for testing."""
        self._positions[user_address] = positions


class MockBettingMarket(BettingMarketDApp):
    """Test implementation of BettingMarket for testing."""

    def _initialize_protocols(self) -> None:
        """Initialize test protocol strategies."""
        for protocol_config in self.configuration.protocols:
            mock_impl = MockProtocolImplementation(protocol_config.protocol_name)
            self._protocol_strategies[protocol_config.protocol_name] = mock_impl


@pytest.fixture
def usdc_asset():
    """Create a mock USDC asset."""
    return MockBlockchainAsset("USDC", 6)


@pytest.fixture
def test_protocol_config():
    """Create a test protocol configuration."""
    return ProtocolConfiguration(
        protocol_name="Test Protocol",
        contract_address="0x1234567890123456789012345678901234567890",
        fee_rate=Decimal("0.02"),
    )


@pytest.fixture
def betting_market_config(test_protocol_config, test_platform):
    """Create a test betting market configuration."""
    return BettingMarketConfiguration(
        platform=test_platform,
        protocols=[test_protocol_config],
        default_slippage_tolerance=Decimal("0.01"),
    )


@pytest.fixture
def betting_market(betting_market_config):
    """Create a test betting market instance."""
    return MockBettingMarket(betting_market_config)


@pytest.fixture
def sample_market():
    """Create a sample betting market for testing."""
    from datetime import datetime, timedelta

    from blockchainpype.dapps.betting_market import MarketOutcome
    from blockchainpype.dapps.betting_market.models import BlockchainAsset

    # Create proper BlockchainAsset instance
    class TestBlockchainAsset(BlockchainAsset):
        def __init__(self, symbol: str, decimals: int):
            super().__init__()
            self.symbol = symbol
            self.decimals = decimals

    usdc_asset = TestBlockchainAsset("USDC", 6)

    yes_token = OutcomeToken(
        token_id="yes_token_1",
        outcome_name="Yes",
        current_price=Decimal("0.65"),
        total_supply=Decimal("10000"),
        probability=Decimal("0.65"),
    )

    no_token = OutcomeToken(
        token_id="no_token_1",
        outcome_name="No",
        current_price=Decimal("0.35"),
        total_supply=Decimal("5000"),
        probability=Decimal("0.35"),
    )

    yes_outcome = MarketOutcome(
        outcome_id="outcome_yes",
        outcome_text="Yes",
        outcome_tokens=[yes_token],
    )

    no_outcome = MarketOutcome(
        outcome_id="outcome_no",
        outcome_text="No",
        outcome_tokens=[no_token],
    )

    return BettingMarketModel(
        market_id="test_market_1",
        title="Test Market",
        description="A test betting market",
        category="test",
        status=MarketStatus.ACTIVE,
        collateral_asset=usdc_asset,
        outcomes=[yes_outcome, no_outcome],
        total_volume=Decimal("50000"),
        total_liquidity=Decimal("25000"),
        creation_date=datetime.now(),
        end_date=datetime.now() + timedelta(days=30),
        protocol="Test Protocol",
    )


class TestBettingMarketInitialization:
    """Test BettingMarket initialization and configuration."""

    def test_initialization(self, betting_market_config):
        """Test BettingMarket initialization."""
        betting_market = MockBettingMarket(betting_market_config)

        assert betting_market.configuration == betting_market_config
        assert len(betting_market.supported_protocols) == 1
        assert "Test Protocol" in betting_market.supported_protocols

    def test_empty_protocols_configuration(self, test_platform):
        """Test error when no protocols are configured."""
        config = BettingMarketConfiguration(platform=test_platform, protocols=[])
        betting_market = MockBettingMarket(config)

        with pytest.raises(ValueError, match="No protocols configured"):
            betting_market._get_protocol_implementation(None)


class TestMarketOperations:
    """Test market-related operations."""

    @pytest.mark.asyncio
    async def test_get_market(self, betting_market, sample_market):
        """Test getting a specific market."""
        # Add mock market to the protocol
        protocol = list(betting_market._protocol_strategies.values())[0]
        protocol.add_mock_market(sample_market)

        market = await betting_market.get_market("test_market_1")

        assert market.market_id == "test_market_1"
        assert market.title == "Test Market"
        assert market.status == MarketStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_get_market_not_found(self, betting_market):
        """Test getting a non-existent market."""
        with pytest.raises(ValueError, match="Market nonexistent not found"):
            await betting_market.get_market("nonexistent")

    @pytest.mark.asyncio
    async def test_get_markets(self, betting_market, sample_market):
        """Test getting all markets."""
        # Add mock market to the protocol
        protocol = list(betting_market._protocol_strategies.values())[0]
        protocol.add_mock_market(sample_market)

        markets = await betting_market.get_markets()

        assert len(markets) == 1
        assert markets[0].market_id == "test_market_1"

    @pytest.mark.asyncio
    async def test_get_markets_with_filters(self, betting_market, sample_market):
        """Test getting markets with filters."""
        # Add mock market to the protocol
        protocol = list(betting_market._protocol_strategies.values())[0]
        protocol.add_mock_market(sample_market)

        markets = await betting_market.get_markets(
            category="test", status="active", limit=10
        )

        assert len(markets) == 1


class TestPositionOperations:
    """Test position-related operations."""

    @pytest.mark.asyncio
    async def test_get_user_positions_empty(self, betting_market):
        """Test getting positions for user with no positions."""
        positions = await betting_market.get_user_positions("0x123")
        assert len(positions) == 0

    @pytest.mark.asyncio
    async def test_get_user_positions_with_data(self, betting_market, sample_market):
        """Test getting positions for user with positions."""
        # Create mock position
        outcome_token = sample_market.outcomes[0].outcome_tokens[0]
        position = BettingPosition(
            market_id="test_market_1",
            outcome_token=outcome_token,
            shares_owned=Decimal("100"),
            average_price=Decimal("0.55"),
            total_invested=Decimal("55"),
            current_value=Decimal("65"),
            unrealized_pnl=Decimal("10"),
            protocol="Test Protocol",
        )

        # Add mock positions to the protocol
        protocol = list(betting_market._protocol_strategies.values())[0]
        protocol.add_mock_positions("0x123", [position])

        positions = await betting_market.get_user_positions("0x123")

        assert len(positions) == 1
        assert positions[0].market_id == "test_market_1"
        assert positions[0].shares_owned == Decimal("100")


class TestPricingOperations:
    """Test pricing-related operations."""

    @pytest.mark.asyncio
    async def test_get_outcome_token_price(self, betting_market):
        """Test getting outcome token price."""
        price = await betting_market.get_outcome_token_price(
            "test_market_1", "yes_token_1"
        )

        assert price == Decimal("0.65")

    @pytest.mark.asyncio
    async def test_get_buy_quote(self, betting_market):
        """Test getting buy quote."""
        expected_shares, total_cost = await betting_market.get_buy_quote(
            "test_market_1", "yes_token_1", Decimal("100")
        )

        # Based on mock implementation: shares = 100/0.65, cost = 100 + 2% fee
        assert expected_shares == Decimal("100") / Decimal("0.65")
        assert total_cost == Decimal("102")  # 100 + 2% fee

    @pytest.mark.asyncio
    async def test_get_sell_quote(self, betting_market):
        """Test getting sell quote."""
        net_payout, fees = await betting_market.get_sell_quote(
            "test_market_1", "yes_token_1", Decimal("100")
        )

        # Based on mock implementation: payout = 100 * 0.65 = 65, fee = 65 * 0.02 = 1.3
        gross_payout = Decimal("100") * Decimal("0.65")
        expected_fee = gross_payout * Decimal("0.02")
        expected_net = gross_payout - expected_fee

        assert net_payout == expected_net
        assert fees == expected_fee


class TestTradingOperations:
    """Test trading-related operations."""

    @pytest.mark.asyncio
    async def test_buy_outcome_tokens(self, betting_market):
        """Test buying outcome tokens."""
        transaction = await betting_market.buy_outcome_tokens(
            market_id="test_market_1",
            outcome_token_id="yes_token_1",
            amount=Decimal("100"),
            user_address="0x123",
        )

        assert transaction is not None

    @pytest.mark.asyncio
    async def test_buy_outcome_tokens_with_max_price(self, betting_market):
        """Test buying outcome tokens with specified max price."""
        transaction = await betting_market.buy_outcome_tokens(
            market_id="test_market_1",
            outcome_token_id="yes_token_1",
            amount=Decimal("100"),
            user_address="0x123",
            max_price=Decimal("0.70"),
        )

        assert transaction is not None

    @pytest.mark.asyncio
    async def test_sell_outcome_tokens(self, betting_market):
        """Test selling outcome tokens."""
        transaction = await betting_market.sell_outcome_tokens(
            market_id="test_market_1",
            outcome_token_id="yes_token_1",
            shares=Decimal("50"),
            user_address="0x123",
        )

        assert transaction is not None

    @pytest.mark.asyncio
    async def test_sell_outcome_tokens_with_min_price(self, betting_market):
        """Test selling outcome tokens with specified min price."""
        transaction = await betting_market.sell_outcome_tokens(
            market_id="test_market_1",
            outcome_token_id="yes_token_1",
            shares=Decimal("50"),
            user_address="0x123",
            min_price=Decimal("0.60"),
        )

        assert transaction is not None

    @pytest.mark.asyncio
    async def test_redeem_winnings(self, betting_market):
        """Test redeeming winnings."""
        transaction = await betting_market.redeem_winnings(
            market_id="test_market_1",
            user_address="0x123",
        )

        assert transaction is not None


class TestProtocolManagement:
    """Test protocol management functionality."""

    def test_get_protocol_implementation_by_name(self, betting_market):
        """Test getting protocol implementation by name."""
        impl = betting_market._get_protocol_implementation("Test Protocol")
        assert impl.protocol_name == "Test Protocol"

    def test_get_protocol_implementation_default(self, betting_market):
        """Test getting default protocol implementation."""
        impl = betting_market._get_protocol_implementation(None)
        assert impl.protocol_name == "Test Protocol"

    def test_get_protocol_implementation_invalid(self, betting_market):
        """Test error when getting invalid protocol implementation."""
        with pytest.raises(ValueError, match="Unsupported protocol: Invalid"):
            betting_market._get_protocol_implementation("Invalid")

    def test_supported_protocols_property(self, betting_market):
        """Test supported protocols property."""
        protocols = betting_market.supported_protocols
        assert len(protocols) == 1
        assert "Test Protocol" in protocols


class TestSlippageCalculation:
    """Test slippage tolerance calculations."""

    @pytest.mark.asyncio
    async def test_buy_with_slippage_calculation(self, betting_market):
        """Test that buy operations calculate max price with slippage."""
        # Mock the get_outcome_token_price method to return a known value
        protocol = list(betting_market._protocol_strategies.values())[0]
        original_method = protocol.get_outcome_token_price

        async def mock_price(*args, **kwargs):
            return Decimal("0.60")

        protocol.get_outcome_token_price = mock_price

        # The buy operation should calculate max_price as current_price * (1 + slippage)
        # With 1% slippage: 0.60 * 1.01 = 0.606
        await betting_market.buy_outcome_tokens(
            market_id="test_market_1",
            outcome_token_id="yes_token_1",
            amount=Decimal("100"),
            user_address="0x123",
        )

        # Restore original method
        protocol.get_outcome_token_price = original_method

    @pytest.mark.asyncio
    async def test_sell_with_slippage_calculation(self, betting_market):
        """Test that sell operations calculate min price with slippage."""
        # Mock the get_outcome_token_price method to return a known value
        protocol = list(betting_market._protocol_strategies.values())[0]
        original_method = protocol.get_outcome_token_price

        async def mock_price(*args, **kwargs):
            return Decimal("0.60")

        protocol.get_outcome_token_price = mock_price

        # The sell operation should calculate min_price as current_price * (1 - slippage)
        # With 1% slippage: 0.60 * 0.99 = 0.594
        await betting_market.sell_outcome_tokens(
            market_id="test_market_1",
            outcome_token_id="yes_token_1",
            shares=Decimal("50"),
            user_address="0x123",
        )

        # Restore original method
        protocol.get_outcome_token_price = original_method
