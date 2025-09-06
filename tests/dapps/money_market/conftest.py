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
def aave_polygon_protocol():
    """Aave V3 Polygon protocol configuration."""
    return ProtocolConfiguration(
        protocol_name="Aave V3 Polygon",
        lending_pool_address="0x794a61358D6845594F94dc1DB02A252b5b4814aD",
        data_provider_address="0x69FA688f1Dc47d4B5d8029D5a35FB7a548310654",
        oracle_address="0xb023e699F5a33916Ea823A16485e259257cA8Bd1",
    )


@pytest.fixture
def solend_mainnet_protocol():
    """Solend Solana mainnet protocol configuration."""
    return ProtocolConfiguration(
        protocol_name="Solend",
        lending_pool_address="So1endDq2YkqhipRh3WViPa8hdiSpxWy6z3Z6tMCpAo",
        data_provider_address="SLendK7ySfcEzyaFqy93gDnD3RtrpXJcnRwb6zFHJSh",
    )
