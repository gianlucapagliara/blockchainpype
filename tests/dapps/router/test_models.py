"""
Unit tests for router swap models.

This module tests:
- SwapHop validation and price properties
- SwapRoute sequence-consistency validation (hop chaining, endpoints, amounts)
- Edge cases and error conditions
"""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from blockchainpype.dapps.router.models import (
    SlippageMode,
    SwapHop,
    SwapMode,
    SwapRoute,
)


class TestEnums:
    """Test enum definitions."""

    def test_swap_mode_values(self):
        assert SwapMode.EXACT_INPUT == "exact_input"
        assert SwapMode.EXACT_OUTPUT == "exact_output"
        assert SwapMode.UNDEFINED == "undefined"

    def test_slippage_mode_values(self):
        assert SlippageMode.VALUE_PROTECTION.value == "real_value"
        assert SlippageMode.FRONTRUNNING_PROTECTION.value == "frontrunning_protection"


class TestSwapHop:
    """Test SwapHop model."""

    def test_valid_swap_hop(self, usdc_asset, weth_asset):
        hop = SwapHop(
            input_asset=usdc_asset,
            input_amount=Decimal("3000"),
            output_asset=weth_asset,
            output_amount=Decimal("1"),
            are_amounts_raw=False,
        )

        assert hop.input_asset == usdc_asset
        assert hop.output_asset == weth_asset
        assert hop.input_amount == Decimal("3000")
        assert hop.output_amount == Decimal("1")

    def test_same_assets_rejected(self, usdc_asset):
        with pytest.raises(
            ValidationError, match="Input and output assets must be different."
        ):
            SwapHop(
                input_asset=usdc_asset,
                input_amount=Decimal("100"),
                output_asset=usdc_asset,
                output_amount=Decimal("100"),
                are_amounts_raw=False,
            )

    def test_zero_input_amount_rejected(self, usdc_asset, weth_asset):
        with pytest.raises(ValidationError, match="Amounts must be greater than 0."):
            SwapHop(
                input_asset=usdc_asset,
                input_amount=Decimal("0"),
                output_asset=weth_asset,
                output_amount=Decimal("1"),
                are_amounts_raw=False,
            )

    def test_zero_output_amount_rejected(self, usdc_asset, weth_asset):
        with pytest.raises(ValidationError, match="Amounts must be greater than 0."):
            SwapHop(
                input_asset=usdc_asset,
                input_amount=Decimal("100"),
                output_asset=weth_asset,
                output_amount=Decimal("0"),
                are_amounts_raw=False,
            )

    def test_raw_amounts_must_be_integers(self, usdc_asset, weth_asset):
        with pytest.raises(ValidationError, match="Raw amounts must be integers."):
            SwapHop(
                input_asset=usdc_asset,
                input_amount=Decimal("100.5"),
                output_asset=weth_asset,
                output_amount=Decimal("1"),
                are_amounts_raw=True,
            )

    def test_raw_integer_amounts_accepted(self, usdc_asset, weth_asset):
        hop = SwapHop(
            input_asset=usdc_asset,
            input_amount=Decimal("3000000000"),
            output_asset=weth_asset,
            output_amount=Decimal("1000000000000000000"),
            are_amounts_raw=True,
        )
        assert hop.are_amounts_raw is True

    def test_price(self, usdc_asset, weth_asset):
        hop = SwapHop(
            input_asset=usdc_asset,
            input_amount=Decimal("3000"),
            output_asset=weth_asset,
            output_amount=Decimal("1.5"),
            are_amounts_raw=False,
        )

        assert hop.price == Decimal("0.0005")
        assert hop.price_inverted == Decimal("2000")

    def test_price_raw_amounts_error(self, usdc_asset, weth_asset):
        hop = SwapHop(
            input_asset=usdc_asset,
            input_amount=Decimal("3000000000"),
            output_asset=weth_asset,
            output_amount=Decimal("1000000000000000000"),
            are_amounts_raw=True,
        )

        with pytest.raises(
            ValueError, match="Amounts are raw, price cannot be calculated."
        ):
            _ = hop.price

    def test_price_inverted_raw_amounts_error(self, usdc_asset, weth_asset):
        """The inverted-price error must mention the inverted price, not price."""
        hop = SwapHop(
            input_asset=usdc_asset,
            input_amount=Decimal("3000000000"),
            output_asset=weth_asset,
            output_amount=Decimal("1000000000000000000"),
            are_amounts_raw=True,
        )

        with pytest.raises(
            ValueError,
            match="Amounts are raw, inverted price cannot be calculated.",
        ):
            _ = hop.price_inverted


def build_route(
    input_asset,
    output_asset,
    sequence,
    *,
    input_amount=Decimal("100"),
    output_amount=Decimal("99"),
    mode=SwapMode.EXACT_INPUT,
    max_slippage=Decimal("0.005"),
    taxes=Decimal("0.003"),
    protocol="stub_protocol",
    are_amounts_raw=False,
) -> SwapRoute:
    return SwapRoute(
        input_asset=input_asset,
        input_amount=input_amount,
        output_asset=output_asset,
        output_amount=output_amount,
        are_amounts_raw=are_amounts_raw,
        sequence=sequence,
        mode=mode,
        max_slippage=max_slippage,
        taxes=taxes,
        protocol=protocol,
    )


class TestSwapRoute:
    """Test SwapRoute model."""

    @pytest.fixture
    def single_hop(self, usdc_asset, weth_asset) -> SwapHop:
        return SwapHop(
            input_asset=usdc_asset,
            input_amount=Decimal("100"),
            output_asset=weth_asset,
            output_amount=Decimal("99"),
            are_amounts_raw=False,
        )

    def test_valid_single_hop_route(self, usdc_asset, weth_asset, single_hop):
        route = build_route(usdc_asset, weth_asset, [single_hop])

        assert route.protocol == "stub_protocol"
        assert route.mode == SwapMode.EXACT_INPUT
        assert len(route.sequence) == 1

    def test_valid_multi_hop_route(self, usdc_asset, weth_asset, dai_asset):
        hop1 = SwapHop(
            input_asset=usdc_asset,
            input_amount=Decimal("100"),
            output_asset=weth_asset,
            output_amount=Decimal("0.05"),
            are_amounts_raw=False,
        )
        hop2 = SwapHop(
            input_asset=weth_asset,
            input_amount=Decimal("0.05"),
            output_asset=dai_asset,
            output_amount=Decimal("99"),
            are_amounts_raw=False,
        )

        route = build_route(usdc_asset, dai_asset, [hop1, hop2])

        assert route.sequence == [hop1, hop2]

    def test_undefined_mode_rejected(self, usdc_asset, weth_asset, single_hop):
        with pytest.raises(ValidationError, match="Swap mode must be defined."):
            build_route(usdc_asset, weth_asset, [single_hop], mode=SwapMode.UNDEFINED)

    def test_empty_sequence_rejected(self, usdc_asset, weth_asset):
        with pytest.raises(ValidationError, match="Path must have at least 1 edge."):
            build_route(usdc_asset, weth_asset, [])

    def test_raw_route_amounts_rejected(self, usdc_asset, weth_asset, single_hop):
        with pytest.raises(ValidationError, match="Amounts of a path cannot be raw."):
            build_route(
                usdc_asset,
                weth_asset,
                [single_hop],
                input_amount=Decimal("100"),
                output_amount=Decimal("99"),
                are_amounts_raw=True,
            )

    def test_first_hop_input_asset_mismatch_rejected(
        self, usdc_asset, weth_asset, dai_asset
    ):
        hop = SwapHop(
            input_asset=dai_asset,
            input_amount=Decimal("100"),
            output_asset=weth_asset,
            output_amount=Decimal("99"),
            are_amounts_raw=False,
        )

        with pytest.raises(
            ValidationError,
            match="First hop input asset must match the route input asset.",
        ):
            build_route(usdc_asset, weth_asset, [hop])

    def test_last_hop_output_asset_mismatch_rejected(
        self, usdc_asset, weth_asset, dai_asset
    ):
        hop = SwapHop(
            input_asset=usdc_asset,
            input_amount=Decimal("100"),
            output_asset=dai_asset,
            output_amount=Decimal("99"),
            are_amounts_raw=False,
        )

        with pytest.raises(
            ValidationError,
            match="Last hop output asset must match the route output asset.",
        ):
            build_route(usdc_asset, weth_asset, [hop])

    def test_broken_hop_chaining_rejected(self, usdc_asset, weth_asset, dai_asset):
        """hop[i+1] must consume exactly what hop[i] produced."""
        hop1 = SwapHop(
            input_asset=usdc_asset,
            input_amount=Decimal("100"),
            output_asset=dai_asset,
            output_amount=Decimal("100"),
            are_amounts_raw=False,
        )
        # Breaks the chain: consumes USDC instead of hop1's DAI output
        hop2 = SwapHop(
            input_asset=usdc_asset,
            input_amount=Decimal("100"),
            output_asset=weth_asset,
            output_amount=Decimal("99"),
            are_amounts_raw=False,
        )

        with pytest.raises(
            ValidationError,
            match="Each hop input asset must match the previous hop output asset.",
        ):
            build_route(usdc_asset, weth_asset, [hop1, hop2])

    def test_first_hop_input_amount_mismatch_rejected(
        self, usdc_asset, weth_asset, single_hop
    ):
        with pytest.raises(
            ValidationError,
            match="First hop input amount must match the route input amount.",
        ):
            build_route(
                usdc_asset,
                weth_asset,
                [single_hop],
                input_amount=Decimal("150"),
                output_amount=Decimal("99"),
            )

    def test_last_hop_output_amount_mismatch_rejected(
        self, usdc_asset, weth_asset, single_hop
    ):
        with pytest.raises(
            ValidationError,
            match="Last hop output amount must match the route output amount.",
        ):
            build_route(
                usdc_asset,
                weth_asset,
                [single_hop],
                input_amount=Decimal("100"),
                output_amount=Decimal("42"),
            )

    def test_raw_hop_amounts_skip_endpoint_amount_checks(self, usdc_asset, weth_asset):
        """Raw hop amounts are in smallest units and are not compared with
        the route's decimal amounts, but asset chaining is still enforced."""
        raw_hop = SwapHop(
            input_asset=usdc_asset,
            input_amount=Decimal("100000000"),
            output_asset=weth_asset,
            output_amount=Decimal("99000000000000000000"),
            are_amounts_raw=True,
        )

        route = build_route(
            usdc_asset,
            weth_asset,
            [raw_hop],
            input_amount=Decimal("100"),
            output_amount=Decimal("99"),
        )

        assert route.sequence[0].are_amounts_raw is True

    def test_max_taxed_slippage(self, usdc_asset, weth_asset, single_hop):
        route = build_route(
            usdc_asset,
            weth_asset,
            [single_hop],
            max_slippage=Decimal("0.01"),
            taxes=Decimal("0.003"),
        )

        assert route.max_taxed_slippage == Decimal("0.013")
