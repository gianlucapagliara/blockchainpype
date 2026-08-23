"""
Unit tests for the DecentralizedExchange base class.

This module tests:
- Protocol strategy dispatch (named protocol and best-across-protocols)
- EXACT_OUTPUT best-quote selection (input minimization)
- Per-strategy failure tolerance during quote aggregation
- update_quote, execute_swap and get_reserves dispatch
- Configuration knob propagation (default_slippage, default_deadline_minutes)
"""

import logging
from decimal import Decimal

import pytest
from financepype.assets.blockchain import BlockchainAsset
from financepype.platforms.blockchain import BlockchainPlatform

from blockchainpype.dapps.router import (
    DecentralizedExchange,
    DexConfiguration,
    ProtocolConfiguration,
    ProtocolImplementation,
    SwapHop,
    SwapMode,
    SwapRoute,
)
from tests.dapps.helpers import FIXED_TIMESTAMP, StubTransaction, make_transaction


class StubDexStrategy:
    """Test double implementing the ProtocolImplementation contract."""

    def __init__(
        self,
        name: str,
        platform: BlockchainPlatform,
        *,
        input_amount: Decimal | None = None,
        output_amount: Decimal | None = None,
        quote_error: Exception | None = None,
        reserves: tuple[Decimal, Decimal] | None = None,
        reserves_error: Exception | None = None,
    ):
        self.name = name
        self.platform = platform
        self.fixed_input_amount = input_amount
        self.fixed_output_amount = output_amount
        self.quote_error = quote_error
        self.reserves = reserves
        self.reserves_error = reserves_error
        self.wallet = None
        self.quote_calls: list[tuple] = []
        self.build_calls: list[tuple] = []

    def set_wallet(self, wallet) -> None:
        self.wallet = wallet

    async def quote_swap(
        self,
        input_asset: BlockchainAsset,
        output_asset: BlockchainAsset,
        amount: Decimal,
        mode: SwapMode = SwapMode.EXACT_INPUT,
        max_slippage: Decimal | None = None,
    ) -> SwapRoute:
        self.quote_calls.append((input_asset, output_asset, amount, mode, max_slippage))
        if self.quote_error is not None:
            raise self.quote_error

        if mode == SwapMode.EXACT_INPUT:
            input_amount = amount
            output_amount = self.fixed_output_amount
        else:
            input_amount = self.fixed_input_amount
            output_amount = amount

        hop = SwapHop(
            input_asset=input_asset,
            input_amount=input_amount,
            output_asset=output_asset,
            output_amount=output_amount,
            are_amounts_raw=False,
        )
        return SwapRoute(
            input_asset=input_asset,
            input_amount=input_amount,
            output_asset=output_asset,
            output_amount=output_amount,
            are_amounts_raw=False,
            sequence=[hop],
            mode=mode,
            max_slippage=max_slippage if max_slippage is not None else Decimal("0.005"),
            taxes=Decimal("0.003"),
            protocol=self.name,
        )

    async def get_reserves(
        self,
        asset_a: BlockchainAsset,
        asset_b: BlockchainAsset,
    ) -> tuple[Decimal, Decimal]:
        if self.reserves_error is not None:
            raise self.reserves_error
        assert self.reserves is not None
        return self.reserves

    async def build_swap_transaction(
        self,
        route: SwapRoute,
        recipient: str | None = None,
        deadline_minutes: int | None = None,
    ) -> StubTransaction:
        self.build_calls.append((route, recipient, deadline_minutes))
        return make_transaction(self.platform)


class StubDex(DecentralizedExchange):
    """Concrete DEX wired with externally provided stub strategies."""

    def __init__(
        self,
        configuration: DexConfiguration,
        strategies: dict[str, StubDexStrategy],
    ):
        self._strategies_to_register = strategies
        super().__init__(configuration)

    def _initialize_protocols(self) -> None:
        self._protocol_strategies.update(self._strategies_to_register)


@pytest.fixture
def dex_configuration(dapp_platform) -> DexConfiguration:
    return DexConfiguration(
        platform=dapp_platform,
        protocols=[
            ProtocolConfiguration(
                protocol_name="alpha",
                factory_address="0x1111111111111111111111111111111111111111",
                router_address="0x2222222222222222222222222222222222222222",
                fee_tiers=[Decimal("0.003")],
            ),
            ProtocolConfiguration(
                protocol_name="beta",
                factory_address="0x3333333333333333333333333333333333333333",
                router_address="0x4444444444444444444444444444444444444444",
                fee_tiers=[Decimal("0.003")],
            ),
        ],
    )


def make_dex(
    configuration: DexConfiguration,
    strategies: dict[str, StubDexStrategy],
) -> StubDex:
    return StubDex(configuration, strategies)


class TestProtocolConformance:
    """The stub strategies must satisfy the runtime-checkable Protocol."""

    def test_stub_satisfies_protocol(self, dapp_platform):
        strategy = StubDexStrategy("alpha", dapp_platform)
        assert isinstance(strategy, ProtocolImplementation)

    def test_set_wallet_binding(self, dapp_platform):
        strategy = StubDexStrategy("alpha", dapp_platform)
        sentinel = object()
        strategy.set_wallet(sentinel)
        assert strategy.wallet is sentinel
        strategy.set_wallet(None)
        assert strategy.wallet is None


class TestInitialization:
    """Test DEX initialization and abstractness."""

    def test_abstract_base_cannot_be_instantiated(self, dex_configuration):
        with pytest.raises(TypeError, match="_initialize_protocols"):
            DecentralizedExchange(dex_configuration)

    def test_supported_protocols(self, dapp_platform, dex_configuration):
        dex = make_dex(
            dex_configuration,
            {
                "alpha": StubDexStrategy("alpha", dapp_platform),
                "beta": StubDexStrategy("beta", dapp_platform),
            },
        )
        assert dex.supported_protocols == ["alpha", "beta"]

    def test_configuration_defaults(self, dex_configuration):
        assert dex_configuration.default_slippage == Decimal("0.005")
        assert dex_configuration.default_deadline_minutes == 20

    def test_current_timestamp_from_blockchain(self, dapp_platform, dex_configuration):
        dex = make_dex(
            dex_configuration, {"alpha": StubDexStrategy("alpha", dapp_platform)}
        )
        assert dex.current_timestamp == FIXED_TIMESTAMP


class TestQuoteSwap:
    """Test quote_swap dispatch and best-quote selection."""

    async def test_named_protocol_dispatch(
        self, dapp_platform, dex_configuration, usdc_asset, weth_asset
    ):
        alpha = StubDexStrategy("alpha", dapp_platform, output_amount=Decimal("0.95"))
        beta = StubDexStrategy("beta", dapp_platform, output_amount=Decimal("1.05"))
        dex = make_dex(dex_configuration, {"alpha": alpha, "beta": beta})

        quote = await dex.quote_swap(
            usdc_asset, weth_asset, Decimal("3000"), protocol="alpha"
        )

        assert quote.protocol == "alpha"
        assert quote.output_amount == Decimal("0.95")
        assert len(alpha.quote_calls) == 1
        assert len(beta.quote_calls) == 0

    async def test_named_protocol_unknown_raises(
        self, dapp_platform, dex_configuration, usdc_asset, weth_asset
    ):
        dex = make_dex(
            dex_configuration, {"alpha": StubDexStrategy("alpha", dapp_platform)}
        )

        with pytest.raises(ValueError, match="Unsupported protocol: unknown"):
            await dex.quote_swap(
                usdc_asset, weth_asset, Decimal("3000"), protocol="unknown"
            )

    async def test_exact_input_selects_max_output(
        self, dapp_platform, dex_configuration, usdc_asset, weth_asset
    ):
        alpha = StubDexStrategy("alpha", dapp_platform, output_amount=Decimal("0.95"))
        beta = StubDexStrategy("beta", dapp_platform, output_amount=Decimal("1.05"))
        dex = make_dex(dex_configuration, {"alpha": alpha, "beta": beta})

        quote = await dex.quote_swap(
            usdc_asset, weth_asset, Decimal("3000"), mode=SwapMode.EXACT_INPUT
        )

        assert quote.protocol == "beta"
        assert quote.output_amount == Decimal("1.05")

    async def test_exact_output_selects_min_input(
        self, dapp_platform, dex_configuration, usdc_asset, weth_asset
    ):
        """EXACT_OUTPUT must minimize the input amount, not maximize output."""
        alpha = StubDexStrategy("alpha", dapp_platform, input_amount=Decimal("3200"))
        beta = StubDexStrategy("beta", dapp_platform, input_amount=Decimal("3000"))
        dex = make_dex(dex_configuration, {"alpha": alpha, "beta": beta})

        quote = await dex.quote_swap(
            usdc_asset, weth_asset, Decimal("1"), mode=SwapMode.EXACT_OUTPUT
        )

        assert quote.protocol == "beta"
        assert quote.input_amount == Decimal("3000")
        assert quote.output_amount == Decimal("1")

    async def test_default_slippage_forwarded_to_strategies(
        self, dapp_platform, dex_configuration, usdc_asset, weth_asset
    ):
        alpha = StubDexStrategy("alpha", dapp_platform, output_amount=Decimal("0.95"))
        dex = make_dex(dex_configuration, {"alpha": alpha})

        quote = await dex.quote_swap(usdc_asset, weth_asset, Decimal("3000"))

        assert alpha.quote_calls[0][4] == dex_configuration.default_slippage
        assert quote.max_slippage == dex_configuration.default_slippage

    async def test_explicit_slippage_overrides_default(
        self, dapp_platform, dex_configuration, usdc_asset, weth_asset
    ):
        alpha = StubDexStrategy("alpha", dapp_platform, output_amount=Decimal("0.95"))
        dex = make_dex(dex_configuration, {"alpha": alpha})

        quote = await dex.quote_swap(
            usdc_asset,
            weth_asset,
            Decimal("3000"),
            max_slippage=Decimal("0.01"),
        )

        assert alpha.quote_calls[0][4] == Decimal("0.01")
        assert quote.max_slippage == Decimal("0.01")

    async def test_failing_protocol_is_tolerated(
        self, dapp_platform, dex_configuration, usdc_asset, weth_asset, caplog
    ):
        """One protocol without the pair must not abort the aggregation."""
        alpha = StubDexStrategy(
            "alpha",
            dapp_platform,
            quote_error=ValueError("No pair found for USDC/WETH"),
        )
        beta = StubDexStrategy("beta", dapp_platform, output_amount=Decimal("1.05"))
        dex = make_dex(dex_configuration, {"alpha": alpha, "beta": beta})

        with caplog.at_level(logging.WARNING):
            quote = await dex.quote_swap(usdc_asset, weth_asset, Decimal("3000"))

        assert quote.protocol == "beta"
        assert "Failed to quote swap on protocol 'alpha'" in caplog.text

    async def test_all_protocols_failing_reraises_first_error(
        self, dapp_platform, dex_configuration, usdc_asset, weth_asset
    ):
        alpha_error = ValueError("No pair found for USDC/WETH on alpha")
        beta_error = RuntimeError("RPC unavailable")
        alpha = StubDexStrategy("alpha", dapp_platform, quote_error=alpha_error)
        beta = StubDexStrategy("beta", dapp_platform, quote_error=beta_error)
        dex = make_dex(dex_configuration, {"alpha": alpha, "beta": beta})

        with pytest.raises(ValueError) as exc_info:
            await dex.quote_swap(usdc_asset, weth_asset, Decimal("3000"))

        assert exc_info.value is alpha_error

    async def test_no_strategies_raises_no_valid_route(
        self, dex_configuration, usdc_asset, weth_asset
    ):
        dex = make_dex(dex_configuration, {})

        with pytest.raises(ValueError, match="No valid route found"):
            await dex.quote_swap(usdc_asset, weth_asset, Decimal("3000"))


class TestUpdateQuote:
    """Test update_quote re-dispatch."""

    async def test_update_exact_input_quote(
        self, dapp_platform, dex_configuration, usdc_asset, weth_asset
    ):
        alpha = StubDexStrategy("alpha", dapp_platform, output_amount=Decimal("0.95"))
        dex = make_dex(dex_configuration, {"alpha": alpha})

        quote = await dex.quote_swap(
            usdc_asset,
            weth_asset,
            Decimal("3000"),
            max_slippage=Decimal("0.02"),
        )

        alpha.fixed_output_amount = Decimal("0.97")
        updated = await dex.update_quote(quote)

        assert updated.input_amount == Decimal("3000")
        assert updated.output_amount == Decimal("0.97")
        # The requoted amount and slippage must match the original quote
        assert alpha.quote_calls[1][2] == Decimal("3000")
        assert alpha.quote_calls[1][3] == SwapMode.EXACT_INPUT
        assert alpha.quote_calls[1][4] == Decimal("0.02")

    async def test_update_exact_output_quote(
        self, dapp_platform, dex_configuration, usdc_asset, weth_asset
    ):
        alpha = StubDexStrategy("alpha", dapp_platform, input_amount=Decimal("3000"))
        dex = make_dex(dex_configuration, {"alpha": alpha})

        quote = await dex.quote_swap(
            usdc_asset, weth_asset, Decimal("1"), mode=SwapMode.EXACT_OUTPUT
        )

        alpha.fixed_input_amount = Decimal("3100")
        updated = await dex.update_quote(quote)

        assert updated.output_amount == Decimal("1")
        assert updated.input_amount == Decimal("3100")
        assert alpha.quote_calls[1][2] == Decimal("1")
        assert alpha.quote_calls[1][3] == SwapMode.EXACT_OUTPUT


class TestExecuteSwap:
    """Test execute_swap dispatch."""

    async def test_dispatches_to_route_protocol(
        self, dapp_platform, dex_configuration, usdc_asset, weth_asset
    ):
        alpha = StubDexStrategy("alpha", dapp_platform, output_amount=Decimal("0.95"))
        beta = StubDexStrategy("beta", dapp_platform, output_amount=Decimal("1.05"))
        dex = make_dex(dex_configuration, {"alpha": alpha, "beta": beta})

        quote = await dex.quote_swap(usdc_asset, weth_asset, Decimal("3000"))
        assert quote.protocol == "beta"

        transaction = await dex.execute_swap(quote, recipient="0xrecipient")

        assert isinstance(transaction, StubTransaction)
        assert len(beta.build_calls) == 1
        assert len(alpha.build_calls) == 0
        route, recipient, deadline_minutes = beta.build_calls[0]
        assert route is quote
        assert recipient == "0xrecipient"
        # The configuration default deadline must be forwarded
        assert deadline_minutes == dex_configuration.default_deadline_minutes

    async def test_explicit_deadline_overrides_default(
        self, dapp_platform, dex_configuration, usdc_asset, weth_asset
    ):
        alpha = StubDexStrategy("alpha", dapp_platform, output_amount=Decimal("0.95"))
        dex = make_dex(dex_configuration, {"alpha": alpha})

        quote = await dex.quote_swap(usdc_asset, weth_asset, Decimal("3000"))
        await dex.execute_swap(quote, deadline_minutes=5)

        assert alpha.build_calls[0][2] == 5

    async def test_unknown_route_protocol_raises(
        self, dapp_platform, dex_configuration, usdc_asset, weth_asset
    ):
        alpha = StubDexStrategy("alpha", dapp_platform, output_amount=Decimal("0.95"))
        dex = make_dex(dex_configuration, {"alpha": alpha})

        quote = await dex.quote_swap(usdc_asset, weth_asset, Decimal("3000"))
        foreign_route = quote.model_copy(update={"protocol": "unknown"})

        with pytest.raises(ValueError, match="Unsupported protocol: unknown"):
            await dex.execute_swap(foreign_route)


class TestGetReserves:
    """Test get_reserves dispatch and fallback."""

    async def test_named_protocol(
        self, dapp_platform, dex_configuration, usdc_asset, weth_asset
    ):
        alpha = StubDexStrategy(
            "alpha",
            dapp_platform,
            reserves=(Decimal("1000000"), Decimal("500")),
        )
        dex = make_dex(dex_configuration, {"alpha": alpha})

        reserves = await dex.get_reserves(usdc_asset, weth_asset, protocol="alpha")

        assert reserves == (Decimal("1000000"), Decimal("500"))

    async def test_named_protocol_unknown_raises(
        self, dapp_platform, dex_configuration, usdc_asset, weth_asset
    ):
        dex = make_dex(
            dex_configuration, {"alpha": StubDexStrategy("alpha", dapp_platform)}
        )

        with pytest.raises(ValueError, match="Unsupported protocol: unknown"):
            await dex.get_reserves(usdc_asset, weth_asset, protocol="unknown")

    async def test_first_available_fallback_logs_failures(
        self, dapp_platform, dex_configuration, usdc_asset, weth_asset, caplog
    ):
        alpha = StubDexStrategy(
            "alpha",
            dapp_platform,
            reserves_error=ValueError("No pair found"),
        )
        beta = StubDexStrategy(
            "beta",
            dapp_platform,
            reserves=(Decimal("2000000"), Decimal("1000")),
        )
        dex = make_dex(dex_configuration, {"alpha": alpha, "beta": beta})

        with caplog.at_level(logging.WARNING):
            reserves = await dex.get_reserves(usdc_asset, weth_asset)

        assert reserves == (Decimal("2000000"), Decimal("1000"))
        assert "Failed to fetch reserves on protocol 'alpha'" in caplog.text

    async def test_all_failing_raises(
        self, dapp_platform, dex_configuration, usdc_asset, weth_asset
    ):
        alpha = StubDexStrategy(
            "alpha", dapp_platform, reserves_error=ValueError("No pair found")
        )
        dex = make_dex(dex_configuration, {"alpha": alpha})

        with pytest.raises(ValueError, match="No reserves found for pair"):
            await dex.get_reserves(usdc_asset, weth_asset)


class TestUnimplementedExtensionPoints:
    """Optional extension points stay unimplemented in the base class."""

    async def test_find_best_route_not_implemented(
        self, dapp_platform, dex_configuration, usdc_asset, weth_asset
    ):
        dex = make_dex(
            dex_configuration, {"alpha": StubDexStrategy("alpha", dapp_platform)}
        )

        with pytest.raises(NotImplementedError):
            await dex.find_best_route(usdc_asset, weth_asset, Decimal("3000"))

    async def test_get_supported_pools_not_implemented(
        self, dapp_platform, dex_configuration
    ):
        dex = make_dex(
            dex_configuration, {"alpha": StubDexStrategy("alpha", dapp_platform)}
        )

        with pytest.raises(NotImplementedError):
            await dex.get_supported_pools()
