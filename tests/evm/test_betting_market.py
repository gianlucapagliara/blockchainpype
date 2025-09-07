"""
Unit tests for EVM Betting Market implementation.

This module tests:
- EVM-specific betting market functionality
- Polymarket protocol implementation
- Transaction building and API integration
- Error handling and edge cases
"""

from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from financepype.operators.blockchains.models import BlockchainPlatform

from blockchainpype.dapps.betting_market import (
    BettingMarketModel,
    BettingPosition,
    MarketOutcome,
    MarketStatus,
    OutcomeToken,
)
from blockchainpype.evm.asset import EthereumAssetData
from blockchainpype.evm.blockchain.identifier import EthereumAddress
from blockchainpype.evm.dapp.betting_market import (
    EVMBettingMarket,
    EVMBettingMarketConfiguration,
    Polymarket,
    PolymarketBettingMarket,
    PolymarketConfiguration,
)
from blockchainpype.evm.transaction import EthereumTransaction
from blockchainpype.initializer import BlockchainsInitializer, SupportedBlockchainType

# Initialize blockchain configurations for tests
try:
    BlockchainsInitializer.configure()
except ValueError:
    # Configuration already registered
    pass


# Import the BlockchainAsset from betting market models to ensure proper inheritance
from blockchainpype.dapps.betting_market.models import BlockchainAsset


class MockEthereumAsset(BlockchainAsset):
    """Mock EthereumAsset for testing."""

    def __init__(self, symbol: str, decimals: int, address: str):
        super().__init__()
        self.identifier = EthereumAddress.from_string(address)
        self.data = EthereumAssetData(
            name=f"{symbol} Token", symbol=symbol, decimals=decimals
        )
        self.contract_address = self.identifier
        self._decimals = decimals
        self.symbol = symbol

    @property
    def address(self) -> EthereumAddress:
        return self.identifier

    @property
    def decimals(self) -> int:
        return self._decimals


class MockEthereumBlockchain:
    """Mock EthereumBlockchain for testing."""

    def __init__(self):
        self.platform = BlockchainPlatform(
            identifier="ethereum",
            type=SupportedBlockchainType.EVM.value,
            chain_id=1,
        )


@pytest.fixture
def mock_blockchain():
    """Create a mock Ethereum blockchain."""
    return MockEthereumBlockchain()


@pytest.fixture
def usdc_asset():
    """Create a mock USDC asset."""
    return MockEthereumAsset("USDC", 6, "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")


@pytest.fixture
def polymarket_config():
    """Create a Polymarket configuration."""
    return PolymarketConfiguration(
        protocol_name="Polymarket",
        api_base_url="https://clob.polymarket.com",
        fee_rate=Decimal("0.02"),
    )


@pytest.fixture
def evm_betting_config(polymarket_config, mock_blockchain):
    """Create an EVM betting market configuration."""
    from financepype.operators.blockchains.models import BlockchainPlatform

    from blockchainpype.initializer import SupportedBlockchainType

    platform = BlockchainPlatform(
        identifier="ethereum",
        type=SupportedBlockchainType.EVM.value,
        chain_id=1,
    )

    return EVMBettingMarketConfiguration(
        platform=platform,
        protocols=[polymarket_config],
        default_slippage_tolerance=Decimal("0.01"),
    )


@pytest.fixture
def sample_market_data():
    """Sample market data from Polymarket API."""
    return {
        "id": "test_market_123",
        "question": "Will Bitcoin reach $100k by end of 2024?",
        "description": "Market resolves to Yes if Bitcoin reaches $100,000 USD by Dec 31, 2024",
        "category": "cryptocurrency",
        "outcomes": ["Yes", "No"],
        "prices": [0.65, 0.35],
        "volume": 50000,
        "liquidity": 25000,
        "created_at": datetime.now().isoformat(),
        "end_date": (datetime.now() + timedelta(days=30)).isoformat(),
        "closed": False,
        "resolved": False,
    }


@pytest.fixture
def sample_position_data():
    """Sample position data from Polymarket API."""
    return {
        "market_id": "test_market_123",
        "token_id": "yes_token_1",
        "outcome_name": "Yes",
        "shares": 100,
        "avg_price": 0.55,
        "current_price": 0.65,
    }


class TestPolymarketConfiguration:
    """Test Polymarket-specific configuration."""

    def test_default_configuration(self):
        """Test Polymarket configuration with defaults."""
        config = PolymarketConfiguration()

        assert config.protocol_name == "Polymarket"
        assert config.api_base_url == "https://clob.polymarket.com"
        assert config.fee_rate == Decimal("0.02")
        assert (
            config.conditional_tokens_address
            == "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
        )

    def test_custom_configuration(self):
        """Test Polymarket configuration with custom values."""
        config = PolymarketConfiguration(
            protocol_name="Custom Polymarket",
            api_base_url="https://custom.polymarket.com",
            fee_rate=Decimal("0.025"),
        )

        assert config.protocol_name == "Custom Polymarket"
        assert config.api_base_url == "https://custom.polymarket.com"
        assert config.fee_rate == Decimal("0.025")

    def test_contract_address_auto_assignment(self):
        """Test that contract address is auto-assigned from CTF exchange."""
        config = PolymarketConfiguration()

        assert config.contract_address == config.ctf_exchange_address


class TestPolymarket:
    """Test Polymarket protocol implementation."""

    @pytest.fixture
    def polymarket(self, polymarket_config, mock_blockchain):
        """Create a Polymarket instance."""
        return Polymarket(polymarket_config, mock_blockchain)

    @pytest.mark.asyncio
    async def test_initialization(self, polymarket):
        """Test Polymarket initialization."""
        assert polymarket.configuration.protocol_name == "Polymarket"
        assert polymarket.blockchain is not None
        assert polymarket._session is None

    @pytest.mark.asyncio
    async def test_session_creation(self, polymarket):
        """Test HTTP session creation."""
        session = await polymarket._get_session()
        assert session is not None

        # Test that same session is returned on subsequent calls
        session2 = await polymarket._get_session()
        assert session is session2

    @pytest.mark.asyncio
    @patch("aiohttp.ClientSession.get")
    async def test_make_api_request_success(
        self, mock_get, polymarket, sample_market_data
    ):
        """Test successful API request."""
        # Mock the aiohttp response
        mock_response = AsyncMock()
        mock_response.json.return_value = sample_market_data
        mock_response.raise_for_status.return_value = None
        mock_get.return_value.__aenter__.return_value = mock_response

        result = await polymarket._make_api_request("/test")

        assert result == sample_market_data
        mock_get.assert_called_once()

    @pytest.mark.asyncio
    @patch("aiohttp.ClientSession.get")
    async def test_make_api_request_error(self, mock_get, polymarket):
        """Test API request error handling."""
        # Mock a failed request
        import aiohttp

        mock_get.side_effect = aiohttp.ClientError("HTTP Error")

        with pytest.raises(ValueError, match="API request failed"):
            await polymarket._make_api_request("/test")

    @pytest.mark.asyncio
    @patch.object(Polymarket, "_make_api_request")
    async def test_get_market(self, mock_api_request, polymarket, sample_market_data):
        """Test getting a specific market."""
        mock_api_request.return_value = sample_market_data

        market = await polymarket.get_market("test_market_123")

        assert isinstance(market, BettingMarketModel)
        assert market.market_id == "test_market_123"
        assert market.title == "Will Bitcoin reach $100k by end of 2024?"
        assert market.status == MarketStatus.ACTIVE
        assert len(market.outcomes) == 2

        mock_api_request.assert_called_once_with("/markets/test_market_123")

    @pytest.mark.asyncio
    @patch.object(Polymarket, "_make_api_request")
    async def test_get_markets(self, mock_api_request, polymarket, sample_market_data):
        """Test getting multiple markets."""
        mock_api_request.return_value = {"data": [sample_market_data]}

        markets = await polymarket.get_markets(category="crypto", limit=10)

        assert len(markets) == 1
        assert markets[0].market_id == "test_market_123"

        mock_api_request.assert_called_once_with(
            "/markets", {"limit": 10, "offset": 0, "category": "crypto"}
        )

    @pytest.mark.asyncio
    @patch.object(Polymarket, "_make_api_request")
    async def test_get_user_positions(
        self, mock_api_request, polymarket, sample_position_data
    ):
        """Test getting user positions."""
        mock_api_request.return_value = {"data": [sample_position_data]}

        positions = await polymarket.get_user_positions(
            "0x1234567890123456789012345678901234567890"
        )

        assert len(positions) == 1
        assert isinstance(positions[0], BettingPosition)
        assert positions[0].market_id == "test_market_123"
        assert positions[0].shares_owned == Decimal("100")

    @pytest.mark.asyncio
    @patch.object(Polymarket, "get_market")
    async def test_get_outcome_token_price(
        self, mock_get_market, polymarket, usdc_asset
    ):
        """Test getting outcome token price."""
        # Create a mock market with outcome tokens
        yes_token = OutcomeToken(
            token_id="yes_token_1",
            outcome_name="Yes",
            current_price=Decimal("0.65"),
            total_supply=Decimal("10000"),
            probability=Decimal("0.65"),
        )

        yes_outcome = MarketOutcome(
            outcome_id="outcome_yes",
            outcome_text="Yes",
            outcome_tokens=[yes_token],
        )

        mock_market = BettingMarketModel(
            market_id="test_market_123",
            title="Test Market",
            description="Test",
            category="test",
            status=MarketStatus.ACTIVE,
            collateral_asset=usdc_asset,
            outcomes=[yes_outcome],
            total_volume=Decimal("1000"),
            total_liquidity=Decimal("500"),
            creation_date=datetime.now(),
            protocol="Polymarket",
        )

        mock_get_market.return_value = mock_market

        price = await polymarket.get_outcome_token_price(
            "test_market_123", "yes_token_1"
        )

        assert price == Decimal("0.65")

    @pytest.mark.asyncio
    @patch.object(Polymarket, "get_market")
    async def test_get_outcome_token_price_not_found(
        self, mock_get_market, polymarket, usdc_asset
    ):
        """Test getting price for non-existent outcome token."""
        mock_market = BettingMarketModel(
            market_id="test_market_123",
            title="Test Market",
            description="Test",
            category="test",
            status=MarketStatus.ACTIVE,
            collateral_asset=usdc_asset,
            outcomes=[],
            total_volume=Decimal("1000"),
            total_liquidity=Decimal("500"),
            creation_date=datetime.now(),
            protocol="Polymarket",
        )

        mock_get_market.return_value = mock_market

        with pytest.raises(ValueError, match="Outcome token nonexistent not found"):
            await polymarket.get_outcome_token_price("test_market_123", "nonexistent")

    @pytest.mark.asyncio
    async def test_build_buy_transaction(self, polymarket):
        """Test building a buy transaction."""
        transaction = await polymarket.build_buy_transaction(
            market_id="test_market_123",
            outcome_token_id="yes_token_1",
            amount=Decimal("100"),
            max_price=Decimal("0.70"),
            user_address="0x1234567890123456789012345678901234567890",
        )

        assert isinstance(transaction, EthereumTransaction)
        assert transaction.client_operation_id.startswith("buy_")

    @pytest.mark.asyncio
    async def test_build_sell_transaction(self, polymarket):
        """Test building a sell transaction."""
        transaction = await polymarket.build_sell_transaction(
            market_id="test_market_123",
            outcome_token_id="yes_token_1",
            shares=Decimal("50"),
            min_price=Decimal("0.60"),
            user_address="0x1234567890123456789012345678901234567890",
        )

        assert isinstance(transaction, EthereumTransaction)
        assert transaction.client_operation_id.startswith("sell_")

    @pytest.mark.asyncio
    async def test_build_redeem_transaction(self, polymarket):
        """Test building a redeem transaction."""
        transaction = await polymarket.build_redeem_transaction(
            market_id="test_market_123",
            user_address="0x1234567890123456789012345678901234567890",
        )

        assert isinstance(transaction, EthereumTransaction)
        assert transaction.client_operation_id.startswith("redeem_")

    @pytest.mark.asyncio
    @patch.object(Polymarket, "get_outcome_token_price")
    async def test_calculate_buy_quote(self, mock_get_price, polymarket):
        """Test calculating buy quote."""
        mock_get_price.return_value = Decimal("0.65")

        expected_shares, total_cost = await polymarket.calculate_buy_quote(
            "test_market_123", "yes_token_1", Decimal("100")
        )

        # Expected: shares = 100/0.65, cost = 100 + 2% fee
        expected_shares_calc = Decimal("100") / Decimal("0.65")
        expected_cost_calc = Decimal("100") + (Decimal("100") * Decimal("0.02"))

        assert expected_shares == expected_shares_calc
        assert total_cost == expected_cost_calc

    @pytest.mark.asyncio
    @patch.object(Polymarket, "get_outcome_token_price")
    async def test_calculate_sell_quote(self, mock_get_price, polymarket):
        """Test calculating sell quote."""
        mock_get_price.return_value = Decimal("0.65")

        net_payout, fees = await polymarket.calculate_sell_quote(
            "test_market_123", "yes_token_1", Decimal("100")
        )

        # Expected: gross = 100 * 0.65 = 65, fee = 65 * 0.02 = 1.3, net = 65 - 1.3 = 63.7
        gross_payout = Decimal("100") * Decimal("0.65")
        expected_fee = gross_payout * Decimal("0.02")
        expected_net = gross_payout - expected_fee

        assert net_payout == expected_net
        assert fees == expected_fee

    def test_parse_market_data(self, polymarket, sample_market_data):
        """Test parsing market data from API response."""
        market = polymarket._parse_market_data(sample_market_data)

        assert isinstance(market, BettingMarketModel)
        assert market.market_id == "test_market_123"
        assert market.title == "Will Bitcoin reach $100k by end of 2024?"
        assert market.status == MarketStatus.ACTIVE
        assert len(market.outcomes) == 2
        assert market.outcomes[0].outcome_text == "Yes"
        assert market.outcomes[1].outcome_text == "No"

    def test_parse_position_data(self, polymarket, sample_position_data):
        """Test parsing position data from API response."""
        position = polymarket._parse_position_data(sample_position_data)

        assert isinstance(position, BettingPosition)
        assert position.market_id == "test_market_123"
        assert position.outcome_token.token_id == "yes_token_1"
        assert position.shares_owned == Decimal("100")
        assert position.average_price == Decimal("0.55")
        assert position.current_value == Decimal("65")  # 100 * 0.65
        assert position.unrealized_pnl == Decimal("10")  # 65 - 55

    @pytest.mark.asyncio
    async def test_close_session(self, polymarket):
        """Test closing HTTP session."""
        # Create a session first
        await polymarket._get_session()
        assert polymarket._session is not None

        # Mock the close method
        polymarket._session.close = AsyncMock()

        await polymarket.close()
        polymarket._session.close.assert_called_once()


class TestEVMBettingMarket:
    """Test EVM betting market implementation."""

    def test_initialization(self, evm_betting_config):
        """Test EVM betting market initialization."""
        betting_market = EVMBettingMarket(evm_betting_config)

        assert betting_market.configuration == evm_betting_config
        assert len(betting_market.supported_protocols) == 1
        assert "Polymarket" in betting_market.supported_protocols

    def test_protocol_strategy_initialization(self, evm_betting_config):
        """Test that protocol strategies are properly initialized."""
        betting_market = EVMBettingMarket(evm_betting_config)

        # Check that Polymarket strategy was created
        assert "Polymarket" in betting_market._protocol_strategies
        strategy = betting_market._protocol_strategies["Polymarket"]
        assert isinstance(strategy, Polymarket)


class TestPolymarketBettingMarket:
    """Test Polymarket-specific betting market implementation."""

    def test_initialization(self, evm_betting_config):
        """Test Polymarket betting market initialization."""
        betting_market = PolymarketBettingMarket(evm_betting_config)

        assert betting_market.configuration == evm_betting_config
        assert len(betting_market.supported_protocols) == 1
        assert "Polymarket" in betting_market.supported_protocols

    def test_polymarket_specific_initialization(self, evm_betting_config):
        """Test Polymarket-specific protocol initialization."""
        betting_market = PolymarketBettingMarket(evm_betting_config)

        # Verify that only Polymarket protocols are initialized
        for protocol_name, strategy in betting_market._protocol_strategies.items():
            assert isinstance(strategy, Polymarket)
            assert protocol_name == "Polymarket"


class TestIntegrationScenarios:
    """Test integration scenarios and edge cases."""

    @pytest.mark.asyncio
    @patch.object(Polymarket, "_make_api_request")
    async def test_full_market_workflow(
        self, mock_api_request, evm_betting_config, sample_market_data
    ):
        """Test a complete market interaction workflow."""
        betting_market = PolymarketBettingMarket(evm_betting_config)

        # Mock API responses
        mock_api_request.return_value = sample_market_data

        # Get market
        market = await betting_market.get_market("test_market_123")
        assert market.market_id == "test_market_123"

        # Get price
        price = await betting_market.get_outcome_token_price(
            "test_market_123", "test_market_123_0"
        )
        assert price == Decimal("0.65")

        # Get buy quote
        expected_shares, total_cost = await betting_market.get_buy_quote(
            "test_market_123", "test_market_123_0", Decimal("100")
        )
        assert expected_shares > 0
        assert total_cost > Decimal("100")  # Should include fees

    def test_error_handling_no_protocols(self):
        """Test error handling when no protocols are configured."""
        from financepype.operators.blockchains.models import BlockchainPlatform

        from blockchainpype.initializer import SupportedBlockchainType

        platform = BlockchainPlatform(
            identifier="ethereum",
            type=SupportedBlockchainType.EVM.value,
            chain_id=1,
        )

        config = EVMBettingMarketConfiguration(platform=platform, protocols=[])
        betting_market = EVMBettingMarket(config)

        with pytest.raises(ValueError, match="No protocols configured"):
            betting_market._get_protocol_implementation(None)

    def test_multiple_protocol_support(self):
        """Test support for multiple protocols."""
        from financepype.operators.blockchains.models import BlockchainPlatform

        from blockchainpype.initializer import SupportedBlockchainType

        platform = BlockchainPlatform(
            identifier="ethereum",
            type=SupportedBlockchainType.EVM.value,
            chain_id=1,
        )

        protocol1 = PolymarketConfiguration(protocol_name="Polymarket 1")
        protocol2 = PolymarketConfiguration(protocol_name="Polymarket 2")

        config = EVMBettingMarketConfiguration(
            platform=platform, protocols=[protocol1, protocol2]
        )
        betting_market = EVMBettingMarket(config)

        assert len(betting_market.supported_protocols) == 2
        assert "Polymarket 1" in betting_market.supported_protocols
        assert "Polymarket 2" in betting_market.supported_protocols
