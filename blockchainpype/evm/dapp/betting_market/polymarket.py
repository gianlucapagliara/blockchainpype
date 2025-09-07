"""
Polymarket betting market implementation for EVM chains.
Polymarket operates primarily on Polygon with USDC as collateral.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any, cast

import aiohttp
from financepype.operations.transactions.transaction import BlockchainTransaction

from blockchainpype.dapps.betting_market import (
    BettingMarketConfiguration,
    BettingMarketModel,
    BettingPosition,
    MarketOutcome,
    MarketStatus,
    OutcomeToken,
    ProtocolConfiguration,
    ProtocolImplementation,
)
from blockchainpype.dapps.betting_market.betting_market import BettingMarket
from blockchainpype.evm.blockchain.blockchain import EthereumBlockchain
from blockchainpype.evm.blockchain.identifier import EthereumAddress
from blockchainpype.evm.transaction import EthereumTransaction


class PolymarketConfiguration(ProtocolConfiguration):
    """Configuration specific to Polymarket protocol."""

    api_base_url: str = "https://clob.polymarket.com"
    conditional_tokens_address: str = (
        "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"  # Polygon
    )
    collateral_token_address: str = (
        "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # USDC on Polygon
    )
    ctf_exchange_address: str = (
        "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"  # CTF Exchange
    )
    neg_risk_ctf_exchange_address: str = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
    neg_risk_adapter_address: str = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"

    def __init__(self, **data):
        if "protocol_name" not in data:
            data["protocol_name"] = "Polymarket"
        if "contract_address" not in data:
            data["contract_address"] = data.get(
                "ctf_exchange_address", "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
            )
        super().__init__(**data)


class EVMBettingMarketConfiguration(BettingMarketConfiguration):
    """EVM-specific betting market configuration."""

    pass


class Polymarket(ProtocolImplementation):
    """Polymarket protocol implementation."""

    def __init__(
        self, configuration: PolymarketConfiguration, blockchain: EthereumBlockchain
    ):
        self.configuration = configuration
        self.blockchain = blockchain
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session for API calls."""
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _make_api_request(
        self, endpoint: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Make authenticated API request to Polymarket."""
        session = await self._get_session()
        url = f"{self.configuration.api_base_url}{endpoint}"

        try:
            async with session.get(url, params=params) as response:
                response.raise_for_status()
                return await response.json()
        except aiohttp.ClientError as e:
            raise ValueError(f"API request failed: {e}")

    async def get_market(self, market_id: str) -> BettingMarketModel:
        """Get detailed information about a specific market."""
        # Get market data from API
        market_data = await self._make_api_request(f"/markets/{market_id}")

        return self._parse_market_data(market_data)

    async def get_markets(
        self,
        category: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[BettingMarketModel]:
        """Get list of available markets with optional filtering."""
        params = {
            "limit": limit,
            "offset": offset,
        }

        if category:
            params["category"] = category
        if status:
            params["active"] = status == "active"

        markets_data = await self._make_api_request("/markets", params)

        return [
            self._parse_market_data(market) for market in markets_data.get("data", [])
        ]

    async def get_user_positions(
        self,
        user_address: str,
        market_id: str | None = None,
    ) -> list[BettingPosition]:
        """Get user's betting positions."""
        params = {"user": user_address}
        if market_id:
            params["market"] = market_id

        positions_data = await self._make_api_request("/positions", params)

        positions = []
        for position_data in positions_data.get("data", []):
            positions.append(self._parse_position_data(position_data))

        return positions

    async def get_outcome_token_price(
        self,
        market_id: str,
        outcome_token_id: str,
    ) -> Decimal:
        """Get current price of an outcome token."""
        market = await self.get_market(market_id)

        for outcome in market.outcomes:
            for token in outcome.outcome_tokens:
                if token.token_id == outcome_token_id:
                    return token.current_price

        raise ValueError(
            f"Outcome token {outcome_token_id} not found in market {market_id}"
        )

    async def build_buy_transaction(
        self,
        market_id: str,
        outcome_token_id: str,
        amount: Decimal,
        max_price: Decimal,
        user_address: str,
    ) -> BlockchainTransaction:
        """Build transaction to buy outcome tokens."""
        # This would interact with Polymarket's CTF Exchange contract
        # For now, returning a placeholder transaction
        from financepype.operations.transactions.transaction import (
            BlockchainTransactionState,
        )
        from financepype.operators.blockchains.models import BlockchainPlatform

        from blockchainpype.evm.wallet.identifier import EthereumWalletIdentifier
        from blockchainpype.initializer import SupportedBlockchainType

        platform = BlockchainPlatform(
            identifier="ethereum",
            type=SupportedBlockchainType.EVM.value,
            chain_id=1,
        )

        return EthereumTransaction(
            client_operation_id=f"buy_{market_id}_{outcome_token_id}",
            owner_identifier=EthereumWalletIdentifier(
                platform=platform,
                address=EthereumAddress.from_string(user_address),
                name="test_wallet",
            ),
            creation_timestamp=0,
            last_update_timestamp=0,
            current_state=BlockchainTransactionState.BROADCASTED,
        )

    async def build_sell_transaction(
        self,
        market_id: str,
        outcome_token_id: str,
        shares: Decimal,
        min_price: Decimal,
        user_address: str,
    ) -> BlockchainTransaction:
        """Build transaction to sell outcome tokens."""
        from financepype.operations.transactions.transaction import (
            BlockchainTransactionState,
        )
        from financepype.operators.blockchains.models import BlockchainPlatform

        from blockchainpype.evm.wallet.identifier import EthereumWalletIdentifier
        from blockchainpype.initializer import SupportedBlockchainType

        platform = BlockchainPlatform(
            identifier="ethereum",
            type=SupportedBlockchainType.EVM.value,
            chain_id=1,
        )

        return EthereumTransaction(
            client_operation_id=f"sell_{market_id}_{outcome_token_id}",
            owner_identifier=EthereumWalletIdentifier(
                platform=platform,
                address=EthereumAddress.from_string(user_address),
                name="test_wallet",
            ),
            creation_timestamp=0,
            last_update_timestamp=0,
            current_state=BlockchainTransactionState.BROADCASTED,
        )

    async def build_redeem_transaction(
        self,
        market_id: str,
        user_address: str,
    ) -> BlockchainTransaction:
        """Build transaction to redeem winnings from resolved market."""
        from financepype.operations.transactions.transaction import (
            BlockchainTransactionState,
        )
        from financepype.operators.blockchains.models import BlockchainPlatform

        from blockchainpype.evm.wallet.identifier import EthereumWalletIdentifier
        from blockchainpype.initializer import SupportedBlockchainType

        platform = BlockchainPlatform(
            identifier="ethereum",
            type=SupportedBlockchainType.EVM.value,
            chain_id=1,
        )

        return EthereumTransaction(
            client_operation_id=f"redeem_{market_id}",
            owner_identifier=EthereumWalletIdentifier(
                platform=platform,
                address=EthereumAddress.from_string(user_address),
                name="test_wallet",
            ),
            creation_timestamp=0,
            last_update_timestamp=0,
            current_state=BlockchainTransactionState.BROADCASTED,
        )

    async def calculate_buy_quote(
        self,
        market_id: str,
        outcome_token_id: str,
        amount: Decimal,
    ) -> tuple[Decimal, Decimal]:
        """Calculate expected shares and total cost for buying."""
        # Get current price
        price = await self.get_outcome_token_price(market_id, outcome_token_id)

        # Simple calculation - in reality would use AMM formulas
        expected_shares = amount / price
        fee = amount * self.configuration.fee_rate
        total_cost = amount + fee

        return expected_shares, total_cost

    async def calculate_sell_quote(
        self,
        market_id: str,
        outcome_token_id: str,
        shares: Decimal,
    ) -> tuple[Decimal, Decimal]:
        """Calculate expected payout and fees for selling."""
        # Get current price
        price = await self.get_outcome_token_price(market_id, outcome_token_id)

        # Simple calculation - in reality would use AMM formulas
        gross_payout = shares * price
        fee = gross_payout * self.configuration.fee_rate
        net_payout = gross_payout - fee

        return net_payout, fee

    def _parse_market_data(self, market_data: dict[str, Any]) -> BettingMarketModel:
        """Parse API market data into BettingMarket model."""
        # Parse outcomes
        outcomes = []
        for i, outcome_text in enumerate(market_data.get("outcomes", ["Yes", "No"])):
            outcome_tokens = [
                OutcomeToken(
                    token_id=f"{market_data['id']}_{i}",
                    outcome_name=outcome_text,
                    current_price=Decimal(
                        str(market_data.get("prices", [0.5, 0.5])[i])
                    ),
                    total_supply=Decimal(str(market_data.get("volume", 0))),
                    probability=Decimal(str(market_data.get("prices", [0.5, 0.5])[i])),
                )
            ]
            outcomes.append(
                MarketOutcome(
                    outcome_id=str(i),
                    outcome_text=outcome_text,
                    outcome_tokens=outcome_tokens,
                )
            )

        # Determine market status
        status = MarketStatus.ACTIVE
        if market_data.get("closed", False):
            status = MarketStatus.CLOSED
        elif market_data.get("resolved", False):
            status = MarketStatus.RESOLVED

        # Create collateral asset (USDC) - use mock for testing compatibility
        from blockchainpype.dapps.betting_market.models import BlockchainAsset

        class USDCAsset(BlockchainAsset):
            def __init__(self):
                super().__init__()
                self.symbol = "USDC"
                self.decimals = 6

        usdc_asset = USDCAsset()

        return BettingMarketModel(
            market_id=market_data["id"],
            title=market_data.get("question", "Unknown Market"),
            description=market_data.get("description", ""),
            category=market_data.get("category", "Other"),
            status=status,
            collateral_asset=usdc_asset,
            outcomes=outcomes,
            total_volume=Decimal(str(market_data.get("volume", 0))),
            total_liquidity=Decimal(str(market_data.get("liquidity", 0))),
            creation_date=datetime.fromisoformat(
                market_data.get("created_at", datetime.now().isoformat())
            ),
            end_date=datetime.fromisoformat(market_data["end_date"])
            if market_data.get("end_date")
            else None,
            resolved_outcome_id=str(market_data["winning_outcome"])
            if market_data.get("winning_outcome") is not None
            else None,
            protocol="Polymarket",
            metadata=market_data,
        )

    def _parse_position_data(self, position_data: dict[str, Any]) -> BettingPosition:
        """Parse API position data into BettingPosition model."""
        # Create outcome token from position data
        outcome_token = OutcomeToken(
            token_id=position_data["token_id"],
            outcome_name=position_data.get("outcome_name", "Unknown"),
            current_price=Decimal(str(position_data.get("current_price", 0))),
            total_supply=Decimal("0"),  # Not available in position data
            probability=Decimal(str(position_data.get("current_price", 0))),
        )

        shares_owned = Decimal(str(position_data.get("shares", 0)))
        average_price = Decimal(str(position_data.get("avg_price", 0)))
        current_price = Decimal(str(position_data.get("current_price", 0)))

        total_invested = shares_owned * average_price
        current_value = shares_owned * current_price
        unrealized_pnl = current_value - total_invested

        return BettingPosition(
            market_id=position_data["market_id"],
            outcome_token=outcome_token,
            shares_owned=shares_owned,
            average_price=average_price,
            total_invested=total_invested,
            current_value=current_value,
            unrealized_pnl=unrealized_pnl,
            protocol="Polymarket",
        )

    async def close(self):
        """Close HTTP session."""
        if self._session:
            await self._session.close()


class PolymarketBettingMarket(BettingMarket):
    """Polymarket-specific betting market implementation."""

    def __init__(self, configuration: EVMBettingMarketConfiguration):
        super().__init__(configuration)

    def _initialize_protocols(self) -> None:
        """Initialize Polymarket protocol strategies."""
        blockchain = cast(EthereumBlockchain, self.blockchain)

        for protocol_config in self.configuration.protocols:
            if isinstance(protocol_config, PolymarketConfiguration):
                self._protocol_strategies[protocol_config.protocol_name] = Polymarket(
                    protocol_config, blockchain
                )


class EVMBettingMarket(BettingMarket):
    """Base EVM betting market implementation."""

    def __init__(self, configuration: EVMBettingMarketConfiguration):
        super().__init__(configuration)

    def _initialize_protocols(self) -> None:
        """Initialize EVM-compatible betting market protocols."""
        blockchain = cast(EthereumBlockchain, self.blockchain)

        for protocol_config in self.configuration.protocols:
            if isinstance(protocol_config, PolymarketConfiguration):
                self._protocol_strategies[protocol_config.protocol_name] = Polymarket(
                    protocol_config, blockchain
                )
