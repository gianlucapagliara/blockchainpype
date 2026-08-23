"""
Pytest configuration for money market tests.
"""

import pytest

from blockchainpype.dapps.money_market import ProtocolConfiguration


@pytest.fixture
def aave_mainnet_protocol():
    """Aave V3 Ethereum mainnet protocol configuration."""
    return ProtocolConfiguration(
        protocol_name="Aave V3",
        lending_pool_address="0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
        data_provider_address="0x7B4EB56E7CD4b454BA8ff71E4518426369a138a3",
        oracle_address="0x54586bE62E3c3580375aE3723C145253060Ca0C2",
        incentives_controller_address="0x8164Cc65827dcFe994AB23944CBC90e0aa80bFcb",
    )


@pytest.fixture
def sample_protocol():
    """Generic protocol configuration used by the dispatch tests."""
    return ProtocolConfiguration(
        protocol_name="Test Protocol",
        lending_pool_address="0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
        data_provider_address="0x7B4EB56E7CD4b454BA8ff71E4518426369a138a3",
    )
