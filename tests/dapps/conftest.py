"""
Shared pytest fixtures for the abstract dapp-layer tests.
"""

import pytest
from financepype.assets.blockchain import BlockchainAsset
from financepype.platforms.blockchain import BlockchainPlatform

from tests.dapps.helpers import (
    build_platform,
    ensure_blockchain_registered,
    make_asset,
)


@pytest.fixture
def dapp_platform() -> BlockchainPlatform:
    """The dapp-test platform, with its blockchain registered in the factory."""
    platform = build_platform()
    ensure_blockchain_registered(platform)
    return platform


@pytest.fixture
def usdc_asset(dapp_platform: BlockchainPlatform) -> BlockchainAsset:
    """A real financepype USDC-like asset (6 decimals)."""
    return make_asset(
        dapp_platform, "USDC", 6, "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
    )


@pytest.fixture
def weth_asset(dapp_platform: BlockchainPlatform) -> BlockchainAsset:
    """A real financepype WETH-like asset (18 decimals)."""
    return make_asset(
        dapp_platform, "WETH", 18, "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
    )


@pytest.fixture
def dai_asset(dapp_platform: BlockchainPlatform) -> BlockchainAsset:
    """A real financepype DAI-like asset (18 decimals)."""
    return make_asset(
        dapp_platform, "DAI", 18, "0x6B175474E89094C44Da98b954EedeAC495271d0F"
    )
