"""
Pytest configuration for betting market tests.
"""

from decimal import Decimal

import pytest
from financepype.operators.blockchains.models import BlockchainPlatform

from blockchainpype.dapps.betting_market import ProtocolConfiguration
from blockchainpype.evm.dapp.betting_market import PolymarketConfiguration
from blockchainpype.initializer import SupportedBlockchainType


@pytest.fixture
def test_platform():
    """Create a test blockchain platform."""
    return BlockchainPlatform(
        identifier="ethereum",
        type=SupportedBlockchainType.EVM.value,
        chain_id=1,
    )


@pytest.fixture
def polymarket_protocol():
    """Polymarket protocol configuration."""
    return PolymarketConfiguration(
        protocol_name="Polymarket",
        contract_address="0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
        api_base_url="https://clob.polymarket.com",
        conditional_tokens_address="0x4D97DCd97eC945f40cF65F87097ACe5EA0476045",
        collateral_token_address="0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
        fee_rate=Decimal("0.02"),
    )


@pytest.fixture
def generic_betting_protocol():
    """Generic betting market protocol configuration."""
    return ProtocolConfiguration(
        protocol_name="Generic Betting Market",
        contract_address="0x1234567890123456789012345678901234567890",
        conditional_tokens_address="0xabcdefabcdefabcdefabcdefabcdefabcdefabcd",
        collateral_token_address="0xfedcbafedcbafedcbafedcbafedcbafedcbafedcba",
        fee_rate=Decimal("0.025"),
    )
