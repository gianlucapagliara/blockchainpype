"""
Unit tests for base BettingMarket class.

This module tests:
- BettingMarket initialization, abstract contract and configuration
- Protocol strategy dispatch (named, first-available and aggregation)
- Trading operations delegating to the strategies (incl. slippage defaults)
- Error handling and validation
"""

from decimal import Decimal

import pytest
from financepype.owners.wallet import BlockchainWallet
from financepype.platforms.blockchain import BlockchainPlatform

from blockchainpype.dapps.betting_market import (
    MAX_DERIVED_OUTCOME_PRICE,
    MIN_DERIVED_OUTCOME_PRICE,
    BettingMarketConfiguration,
    BettingMarketDApp,
    BettingMarketModel,
    BettingPosition,
    MarketStatus,
    ProtocolConfiguration,
    ProtocolImplementation,
)
from tests.dapps.helpers import FIXED_TIMESTAMP, StubTransaction, make_transaction

USER_ADDRESS = "0x1234567890123456789012345678901234567890"


class StubProtocolImplementation:
    """Stub protocol implementation recording calls and returning real models."""

    def __init__(self, protocol_name: str, platform: BlockchainPlatform):
        self.protocol_name = protocol_name
        self.platform = platform
        self.token_price = Decimal("0.65")
        self.wallet: BlockchainWallet | None = None
        self._markets: dict[str, BettingMarketModel] = {}
        self._positions: dict[str, list[BettingPosition]] = {}
        self.buy_calls: list[tuple] = []
        self.sell_calls: list[tuple] = []
        self.redeem_calls: list[tuple] = []

    def set_wallet(self, wallet: BlockchainWallet | None) -> None:
        self.wallet = wallet

    async def get_market(self, market_id: str) -> BettingMarketModel:
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
        return list(self._markets.values())

    async def get_user_positions(
        self,
        user_address: str,
        market_id: str | None = None,
    ) -> list[BettingPosition]:
        return self._positions.get(user_address, [])

    async def get_outcome_token_price(
        self,
        market_id: str,
        outcome_token_id: str,
    ) -> Decimal:
        return self.token_price

    async def build_buy_transaction(
        self,
        market_id: str,
        outcome_token_id: str,
        amount: Decimal,
        max_price: Decimal,
        user_address: str,
    ) -> StubTransaction:
        self.buy_calls.append(
            (market_id, outcome_token_id, amount, max_price, user_address)
        )
        return make_transaction(self.platform)

    async def build_sell_transaction(
        self,
        market_id: str,
        outcome_token_id: str,
        shares: Decimal,
        min_price: Decimal,
        user_address: str,
    ) -> StubTransaction:
        self.sell_calls.append(
            (market_id, outcome_token_id, shares, min_price, user_address)
        )
        return make_transaction(self.platform)

    async def build_redeem_transaction(
        self,
        market_id: str,
        user_address: str,
    ) -> StubTransaction:
        self.redeem_calls.append((market_id, user_address))
        return make_transaction(self.platform)

    async def calculate_buy_quote(
        self,
        market_id: str,
        outcome_token_id: str,
        amount: Decimal,
    ) -> tuple[Decimal, Decimal]:
        expected_shares = amount / self.token_price
        fee = amount * Decimal("0.02")
        return expected_shares, amount + fee

    async def calculate_sell_quote(
        self,
        market_id: str,
        outcome_token_id: str,
        shares: Decimal,
    ) -> tuple[Decimal, Decimal]:
        gross_payout = shares * self.token_price
        fee = gross_payout * Decimal("0.02")
        return gross_payout - fee, fee

    def add_mock_market(self, market: BettingMarketModel) -> None:
        self._markets[market.market_id] = market

    def add_mock_positions(
        self, user_address: str, positions: list[BettingPosition]
    ) -> None:
        self._positions[user_address] = positions


class StubBettingMarket(BettingMarketDApp):
    """Concrete BettingMarket wiring one stub strategy per configured protocol."""

    def _initialize_protocols(self) -> None:
        for protocol_config in self.configuration.protocols:
            self._protocol_strategies[protocol_config.protocol_name] = (
                StubProtocolImplementation(
                    protocol_config.protocol_name, self.configuration.platform
                )
            )


@pytest.fixture
def test_protocol_config():
    """Create a test protocol configuration."""
    return ProtocolConfiguration(
        protocol_name="Test Protocol",
        contract_address="0x1234567890123456789012345678901234567890",
        fee_rate=Decimal("0.02"),
    )


@pytest.fixture
def betting_market_config(test_protocol_config, dapp_platform):
    """Create a test betting market configuration."""
    return BettingMarketConfiguration(
        platform=dapp_platform,
        protocols=[test_protocol_config],
        default_slippage_tolerance=Decimal("0.01"),
    )


@pytest.fixture
def betting_market(betting_market_config):
    """Create a test betting market instance."""
    return StubBettingMarket(betting_market_config)


@pytest.fixture
def stub_strategy(betting_market) -> StubProtocolImplementation:
    """The single wired stub strategy."""
    return betting_market._protocol_strategies["Test Protocol"]


class TestBettingMarketInitialization:
    """Test BettingMarket initialization and configuration."""

    def test_abstract_base_cannot_be_instantiated(self, betting_market_config):
        """BettingMarket must be a real ABC enforcing _initialize_protocols."""
        with pytest.raises(TypeError, match="_initialize_protocols"):
            BettingMarketDApp(betting_market_config)

    def test_initialization(self, betting_market_config):
        """Test BettingMarket initialization."""
        betting_market = StubBettingMarket(betting_market_config)

        assert betting_market.configuration == betting_market_config
        assert len(betting_market.supported_protocols) == 1
        assert "Test Protocol" in betting_market.supported_protocols

    def test_empty_protocols_configuration(self, dapp_platform):
        """Test error when no protocols are configured."""
        config = BettingMarketConfiguration(platform=dapp_platform, protocols=[])
        betting_market = StubBettingMarket(config)

        with pytest.raises(ValueError, match="No protocols configured"):
            betting_market._get_protocol_implementation(None)

    def test_current_timestamp_from_blockchain(self, betting_market):
        """current_timestamp must delegate to the resolved blockchain."""
        assert betting_market.current_timestamp == FIXED_TIMESTAMP


class TestMarketOperations:
    """Test market-related operations."""

    async def test_get_market(self, betting_market, stub_strategy, sample_market):
        """Test getting a specific market."""
        stub_strategy.add_mock_market(sample_market)

        market = await betting_market.get_market("test_market_1")

        assert market.market_id == "test_market_1"
        assert market.title == "Test Market"
        assert market.status == MarketStatus.ACTIVE
        assert market.collateral_asset.data.symbol == "USDC"

    async def test_get_market_not_found(self, betting_market):
        """Test getting a non-existent market."""
        with pytest.raises(ValueError, match="Market nonexistent not found"):
            await betting_market.get_market("nonexistent")

    async def test_get_markets(self, betting_market, stub_strategy, sample_market):
        """Test getting all markets."""
        stub_strategy.add_mock_market(sample_market)

        markets = await betting_market.get_markets()

        assert len(markets) == 1
        assert markets[0].market_id == "test_market_1"

    async def test_get_markets_with_filters(
        self, betting_market, stub_strategy, sample_market
    ):
        """Test getting markets with filters."""
        stub_strategy.add_mock_market(sample_market)

        markets = await betting_market.get_markets(
            category="test", status="active", limit=10
        )

        assert len(markets) == 1

    async def test_get_markets_aggregates_protocols(self, dapp_platform, sample_market):
        """Markets must be aggregated from every configured protocol."""
        config = BettingMarketConfiguration(
            platform=dapp_platform,
            protocols=[
                ProtocolConfiguration(
                    protocol_name="Protocol A",
                    contract_address="0x1111111111111111111111111111111111111111",
                ),
                ProtocolConfiguration(
                    protocol_name="Protocol B",
                    contract_address="0x2222222222222222222222222222222222222222",
                ),
            ],
        )
        betting_market = StubBettingMarket(config)
        betting_market._protocol_strategies["Protocol A"].add_mock_market(sample_market)
        second_market = sample_market.model_copy(
            update={"market_id": "test_market_2", "protocol": "Protocol B"}
        )
        betting_market._protocol_strategies["Protocol B"].add_mock_market(second_market)

        markets = await betting_market.get_markets()

        assert {market.market_id for market in markets} == {
            "test_market_1",
            "test_market_2",
        }


class TestPositionOperations:
    """Test position-related operations."""

    async def test_get_user_positions_empty(self, betting_market):
        """Test getting positions for user with no positions."""
        positions = await betting_market.get_user_positions(USER_ADDRESS)
        assert len(positions) == 0

    async def test_get_user_positions_with_data(
        self, betting_market, stub_strategy, sample_market
    ):
        """Test getting positions for user with positions."""
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
        stub_strategy.add_mock_positions(USER_ADDRESS, [position])

        positions = await betting_market.get_user_positions(USER_ADDRESS)

        assert len(positions) == 1
        assert positions[0].market_id == "test_market_1"
        assert positions[0].shares_owned == Decimal("100")


class TestPricingOperations:
    """Test pricing-related operations."""

    async def test_get_outcome_token_price(self, betting_market):
        """Test getting outcome token price."""
        price = await betting_market.get_outcome_token_price(
            "test_market_1", "yes_token_1"
        )

        assert price == Decimal("0.65")

    async def test_get_buy_quote(self, betting_market):
        """Test getting buy quote."""
        expected_shares, total_cost = await betting_market.get_buy_quote(
            "test_market_1", "yes_token_1", Decimal("100")
        )

        assert expected_shares == Decimal("100") / Decimal("0.65")
        assert total_cost == Decimal("102")  # 100 + 2% fee

    async def test_get_sell_quote(self, betting_market):
        """Test getting sell quote."""
        net_payout, fees = await betting_market.get_sell_quote(
            "test_market_1", "yes_token_1", Decimal("100")
        )

        gross_payout = Decimal("100") * Decimal("0.65")
        expected_fee = gross_payout * Decimal("0.02")

        assert net_payout == gross_payout - expected_fee
        assert fees == expected_fee


class TestTradingOperations:
    """Test trading-related operations."""

    async def test_buy_outcome_tokens_applies_default_slippage(
        self, betting_market, stub_strategy
    ):
        """Without max_price, current price plus slippage must be forwarded."""
        stub_strategy.token_price = Decimal("0.60")

        transaction = await betting_market.buy_outcome_tokens(
            market_id="test_market_1",
            outcome_token_id="yes_token_1",
            amount=Decimal("100"),
            user_address=USER_ADDRESS,
        )

        assert isinstance(transaction, StubTransaction)
        # 0.60 * (1 + 1% slippage) = 0.606
        assert stub_strategy.buy_calls == [
            (
                "test_market_1",
                "yes_token_1",
                Decimal("100"),
                Decimal("0.60") * (1 + Decimal("0.01")),
                USER_ADDRESS,
            )
        ]

    async def test_buy_outcome_tokens_with_max_price(
        self, betting_market, stub_strategy
    ):
        """An explicit max price must be forwarded untouched."""
        transaction = await betting_market.buy_outcome_tokens(
            market_id="test_market_1",
            outcome_token_id="yes_token_1",
            amount=Decimal("100"),
            user_address=USER_ADDRESS,
            max_price=Decimal("0.70"),
        )

        assert isinstance(transaction, StubTransaction)
        assert stub_strategy.buy_calls[0][3] == Decimal("0.70")

    async def test_sell_outcome_tokens_applies_default_slippage(
        self, betting_market, stub_strategy
    ):
        """Without min_price, current price minus slippage must be forwarded."""
        stub_strategy.token_price = Decimal("0.60")

        transaction = await betting_market.sell_outcome_tokens(
            market_id="test_market_1",
            outcome_token_id="yes_token_1",
            shares=Decimal("50"),
            user_address=USER_ADDRESS,
        )

        assert isinstance(transaction, StubTransaction)
        # 0.60 * (1 - 1% slippage) = 0.594
        assert stub_strategy.sell_calls == [
            (
                "test_market_1",
                "yes_token_1",
                Decimal("50"),
                Decimal("0.60") * (1 - Decimal("0.01")),
                USER_ADDRESS,
            )
        ]

    async def test_sell_outcome_tokens_with_min_price(
        self, betting_market, stub_strategy
    ):
        """An explicit min price must be forwarded untouched."""
        transaction = await betting_market.sell_outcome_tokens(
            market_id="test_market_1",
            outcome_token_id="yes_token_1",
            shares=Decimal("50"),
            user_address=USER_ADDRESS,
            min_price=Decimal("0.60"),
        )

        assert isinstance(transaction, StubTransaction)
        assert stub_strategy.sell_calls[0][3] == Decimal("0.60")

    async def test_redeem_winnings(self, betting_market, stub_strategy):
        """Test redeeming winnings."""
        transaction = await betting_market.redeem_winnings(
            market_id="test_market_1",
            user_address=USER_ADDRESS,
        )

        assert isinstance(transaction, StubTransaction)
        assert stub_strategy.redeem_calls == [("test_market_1", USER_ADDRESS)]


class TestDerivedPriceClamping:
    """Derived max/min prices must stay inside the (0, 1) probability range."""

    @pytest.fixture
    def wide_slippage_market(self, test_protocol_config, dapp_platform):
        """A 5% slippage tolerance, enough to push 0.99 above 1.0."""
        return StubBettingMarket(
            BettingMarketConfiguration(
                platform=dapp_platform,
                protocols=[test_protocol_config],
                default_slippage_tolerance=Decimal("0.05"),
            )
        )

    async def test_high_probability_buy_clamps_max_price(self, wide_slippage_market):
        """Regression: 0.99 * 1.05 = 1.0395 was forwarded and rejected."""
        strategy = wide_slippage_market._protocol_strategies["Test Protocol"]
        strategy.token_price = Decimal("0.99")

        transaction = await wide_slippage_market.buy_outcome_tokens(
            market_id="test_market_1",
            outcome_token_id="yes_token_1",
            amount=Decimal("100"),
            user_address=USER_ADDRESS,
        )

        assert isinstance(transaction, StubTransaction)
        forwarded_max_price = strategy.buy_calls[0][3]
        assert forwarded_max_price == MAX_DERIVED_OUTCOME_PRICE
        assert forwarded_max_price == Decimal("0.999")
        assert Decimal(0) < forwarded_max_price < Decimal(1)

    async def test_buy_below_the_clamp_is_untouched(self, wide_slippage_market):
        strategy = wide_slippage_market._protocol_strategies["Test Protocol"]
        strategy.token_price = Decimal("0.50")

        await wide_slippage_market.buy_outcome_tokens(
            market_id="test_market_1",
            outcome_token_id="yes_token_1",
            amount=Decimal("100"),
            user_address=USER_ADDRESS,
        )

        assert strategy.buy_calls[0][3] == Decimal("0.525")

    async def test_explicit_max_price_is_not_clamped(self, wide_slippage_market):
        """An explicit caller value is forwarded untouched, even above 1."""
        strategy = wide_slippage_market._protocol_strategies["Test Protocol"]

        await wide_slippage_market.buy_outcome_tokens(
            market_id="test_market_1",
            outcome_token_id="yes_token_1",
            amount=Decimal("100"),
            user_address=USER_ADDRESS,
            max_price=Decimal("1.5"),
        )

        assert strategy.buy_calls[0][3] == Decimal("1.5")

    async def test_low_probability_sell_clamps_min_price(self, wide_slippage_market):
        """The mirror clamp: a near-zero price must stay strictly positive."""
        strategy = wide_slippage_market._protocol_strategies["Test Protocol"]
        strategy.token_price = Decimal("0")

        await wide_slippage_market.sell_outcome_tokens(
            market_id="test_market_1",
            outcome_token_id="yes_token_1",
            shares=Decimal("50"),
            user_address=USER_ADDRESS,
        )

        forwarded_min_price = strategy.sell_calls[0][3]
        assert forwarded_min_price == MIN_DERIVED_OUTCOME_PRICE
        assert forwarded_min_price == Decimal("0.001")

    async def test_sell_above_the_clamp_is_untouched(self, wide_slippage_market):
        strategy = wide_slippage_market._protocol_strategies["Test Protocol"]
        strategy.token_price = Decimal("0.50")

        await wide_slippage_market.sell_outcome_tokens(
            market_id="test_market_1",
            outcome_token_id="yes_token_1",
            shares=Decimal("50"),
            user_address=USER_ADDRESS,
        )

        assert strategy.sell_calls[0][3] == Decimal("0.475")


class StrategyWithoutSetWallet:
    """Every betting-market method except the wallet binding one."""

    async def get_market(self, market_id): ...
    async def get_markets(self, *args, **kwargs): ...
    async def get_user_positions(self, *args, **kwargs): ...
    async def get_outcome_token_price(self, *args, **kwargs): ...
    async def build_buy_transaction(self, *args, **kwargs): ...
    async def build_sell_transaction(self, *args, **kwargs): ...
    async def build_redeem_transaction(self, *args, **kwargs): ...
    async def calculate_buy_quote(self, *args, **kwargs): ...
    async def calculate_sell_quote(self, *args, **kwargs): ...


class TestProtocolContract:
    """The betting-market ProtocolImplementation is a runtime-checkable contract."""

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
        assert protocols == ["Test Protocol"]
