"""
Tests for the public API surface of the dapps package.

Imports must fail loudly (no None fallbacks), and re-exports must be the
canonical objects from their defining modules.
"""

import blockchainpype.dapps as dapps
import blockchainpype.dapps.router as router
from blockchainpype.dapps.betting_market import betting_market as betting_market_module
from blockchainpype.dapps.betting_market import models as betting_market_models
from blockchainpype.dapps.money_market import models as money_market_models
from blockchainpype.dapps.money_market import money_market as money_market_module
from blockchainpype.dapps.router import dex as dex_module
from blockchainpype.dapps.router import models as router_models


class TestDappsExports:
    """Test the blockchainpype.dapps export surface."""

    def test_all_names_resolve(self):
        """Every name in __all__ must be importable and not None."""
        for name in dapps.__all__:
            assert getattr(dapps, name) is not None, name

    def test_money_market_exports_are_canonical(self):
        assert dapps.MoneyMarket is money_market_module.MoneyMarket
        assert (
            dapps.MoneyMarketProtocolImplementation
            is money_market_module.ProtocolImplementation
        )
        assert (
            dapps.MoneyMarketConfiguration
            is money_market_models.MoneyMarketConfiguration
        )
        assert dapps.LendingPosition is money_market_models.LendingPosition

    def test_betting_market_exports_are_canonical(self):
        assert dapps.BettingMarketDApp is betting_market_module.BettingMarket
        assert dapps.BettingMarketModel is betting_market_models.BettingMarket
        assert (
            dapps.BettingMarketProtocolImplementation
            is betting_market_module.ProtocolImplementation
        )

    def test_router_exports_are_canonical(self):
        assert dapps.DecentralizedExchange is dex_module.DecentralizedExchange
        assert dapps.DexConfiguration is dex_module.DexConfiguration
        assert dapps.DexProtocolConfiguration is dex_module.ProtocolConfiguration
        assert dapps.DexProtocolImplementation is dex_module.ProtocolImplementation
        assert dapps.SwapMode is router_models.SwapMode
        assert dapps.SwapRoute is router_models.SwapRoute
        assert dapps.SwapHop is router_models.SwapHop
        assert dapps.SlippageMode is router_models.SlippageMode

    def test_no_none_fallback_machinery(self):
        """The old try/except-ImportError None fallbacks must be gone."""
        assert not hasattr(dapps, "_betting_market_available")
        assert not hasattr(dapps, "_router_available")


class TestRouterPackageExports:
    """Test the blockchainpype.dapps.router export surface."""

    def test_all_names_resolve(self):
        for name in router.__all__:
            assert getattr(router, name) is not None, name

    def test_expected_names(self):
        assert set(router.__all__) == {
            "DecentralizedExchange",
            "DexConfiguration",
            "ProtocolConfiguration",
            "ProtocolImplementation",
            "SlippageMode",
            "SwapHop",
            "SwapMode",
            "SwapRoute",
        }


class TestBlockchainAssetIsReal:
    """The mock BlockchainAsset placeholders must be gone for good."""

    def test_money_market_asset_is_financepype(self):
        from financepype.assets.blockchain import BlockchainAsset

        from blockchainpype.dapps.money_market import BlockchainAsset as Exported
        from blockchainpype.dapps.money_market.models import (
            BlockchainAsset as FromModels,
        )

        assert FromModels is BlockchainAsset
        assert Exported is BlockchainAsset

    def test_betting_market_asset_is_financepype(self):
        from financepype.assets.blockchain import BlockchainAsset

        from blockchainpype.dapps.betting_market.models import (
            BlockchainAsset as FromModels,
        )

        assert FromModels is BlockchainAsset
