"""
Unit tests for EVM gas fee estimation (GasConfiguration).

All Web3 interactions are mocked at the RPC boundary with realistic payloads;
no network access is required.
"""

from collections.abc import Generator
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from web3.types import TxParams, Wei

from blockchainpype.evm.blockchain.gas import (
    EIP1559_PERCENTILES_BY_MODE,
    GasConfiguration,
    GasMode,
    GasStrategy,
)

GWEI = 10**9


class AwaitableValue:
    """Reusable awaitable mimicking web3's async ``eth.gas_price`` property."""

    def __init__(self, value: Any) -> None:
        self.value = value
        self.await_count = 0

    def __await__(self) -> Generator[Any, None, Any]:
        async def _get() -> Any:
            self.await_count += 1
            return self.value

        return _get().__await__()


def build_pending_block(base_fee: int | None) -> dict[str, Any]:
    """Build a realistic (partial) ``eth_getBlockByNumber('pending')`` payload."""
    block: dict[str, Any] = {
        "number": 19_000_000,
        "timestamp": 1_700_000_000,
        "gasLimit": 30_000_000,
        "gasUsed": 12_345_678,
    }
    if base_fee is not None:
        block["baseFeePerGas"] = Wei(base_fee)
    return block


def build_fee_history(rewards: list[list[Wei]], base_fee: int) -> dict[str, Any]:
    """Build a realistic ``eth_feeHistory`` payload."""
    return {
        "baseFeePerGas": [Wei(base_fee)] * (len(rewards) + 1),
        "gasUsedRatio": [0.4] * len(rewards),
        "oldestBlock": 18_999_990,
        "reward": rewards,
    }


def build_web3_mock(
    *,
    base_fee: int | None = 10 * GWEI,
    rewards: list[list[Wei]] | None = None,
    gas_price: int = 20 * GWEI,
    estimated_gas: int = 21_000,
) -> MagicMock:
    """Build an AsyncWeb3 stand-in mocked at the RPC boundary."""
    if rewards is None:
        rewards = [
            [Wei(1 * GWEI), Wei(2 * GWEI)],
            [Wei(3 * GWEI), Wei(4 * GWEI)],
        ]

    w3 = MagicMock()
    w3.eth = MagicMock()
    w3.eth.get_block = AsyncMock(return_value=build_pending_block(base_fee))
    w3.eth.fee_history = AsyncMock(
        return_value=build_fee_history(rewards, base_fee or 0)
    )
    w3.eth.estimate_gas = AsyncMock(return_value=estimated_gas)
    w3.eth.gas_price = AwaitableValue(Wei(gas_price))
    return w3


class TestEstimateEip1559GasFees:
    async def test_returns_after_single_successful_estimation(self) -> None:
        """Regression: the retry loop must exit after the first success.

        A call counter caps the iterations: any re-entry of the estimation
        loop after a successful pass fails the test immediately instead of
        hanging until the pytest timeout.
        """
        configuration = GasConfiguration()
        w3 = build_web3_mock()

        pending_block = build_pending_block(10 * GWEI)
        get_block_calls = 0

        async def capped_get_block(block_identifier: Any) -> dict[str, Any]:
            nonlocal get_block_calls
            get_block_calls += 1
            if get_block_calls > 1:
                raise AssertionError(
                    "estimate_eip1559_gas_fees re-entered the estimation loop "
                    "after a successful pass"
                )
            return pending_block

        w3.eth.get_block = capped_get_block

        result = await configuration.estimate_eip1559_gas_fees(w3)

        # avg reward of [1, 2, 3, 4] gwei = 2.5 gwei; next base fee = 2 * 10 gwei
        assert result == {
            "gas": 800_000,  # ceil(default_gas 800000 * 1.3) clamped to max_gas
            "maxPriorityFeePerGas": 2_500_000_000,
            "maxFeePerGas": 22_500_000_000,
        }
        assert get_block_calls == 1
        assert w3.eth.fee_history.await_count == 1

    async def test_uses_gas_from_transaction_params(self) -> None:
        configuration = GasConfiguration()
        w3 = build_web3_mock()

        result = await configuration.estimate_eip1559_gas_fees(w3, {"gas": 50_000})

        assert result["gas"] == 65_000  # ceil(50000 * 1.3)
        w3.eth.estimate_gas.assert_not_awaited()

    async def test_estimates_gas_when_params_have_no_gas(self) -> None:
        configuration = GasConfiguration()
        w3 = build_web3_mock(estimated_gas=21_000)
        transaction_params: dict[str, Any] = {
            "from": "0xA1E4380A3B1f749673E270229993eE55F35663b4",
            "to": "0x5DF9B87991262F6BA471F09758CDE1c0FC1De734",
            "value": Wei(10**18),
        }

        result = await configuration.estimate_eip1559_gas_fees(
            w3,
            transaction_params,  # type: ignore[arg-type]
        )

        assert result["gas"] == 27_300  # ceil(21000 * 1.3)
        w3.eth.estimate_gas.assert_awaited_once_with(transaction_params)

    async def test_percentiles_follow_gas_mode(self) -> None:
        configuration = GasConfiguration(gas_mode=GasMode.FAST, n_blocks=5)
        w3 = build_web3_mock()

        await configuration.estimate_eip1559_gas_fees(w3)

        assert w3.eth.fee_history.await_args == call(
            5, "pending", EIP1559_PERCENTILES_BY_MODE[GasMode.FAST]
        )

    async def test_retry_then_success(self) -> None:
        configuration = GasConfiguration()
        w3 = build_web3_mock()
        w3.eth.get_block = AsyncMock(
            side_effect=[
                TimeoutError("RPC timeout"),
                build_pending_block(10 * GWEI),
            ]
        )

        result = await configuration.estimate_eip1559_gas_fees(w3)

        assert result == {
            "gas": 800_000,
            "maxPriorityFeePerGas": 2_500_000_000,
            "maxFeePerGas": 22_500_000_000,
        }
        assert w3.eth.get_block.await_count == 2
        assert w3.eth.fee_history.await_count == 1

    async def test_retry_exhaustion_reraises_last_error(self) -> None:
        configuration = GasConfiguration()
        w3 = build_web3_mock()
        w3.eth.get_block = AsyncMock(side_effect=ValueError("RPC unavailable"))

        with pytest.raises(ValueError, match="RPC unavailable"):
            await configuration.estimate_eip1559_gas_fees(w3)

        assert w3.eth.get_block.await_count == 3  # default n_max_retries

    async def test_custom_max_retries(self) -> None:
        configuration = GasConfiguration()
        w3 = build_web3_mock()
        w3.eth.get_block = AsyncMock(side_effect=ConnectionError("down"))

        with pytest.raises(ConnectionError, match="down"):
            await configuration.estimate_eip1559_gas_fees(w3, n_max_retries=1)

        assert w3.eth.get_block.await_count == 1

    async def test_missing_base_fee_raises_after_retries(self) -> None:
        """A pre-London block payload (no baseFeePerGas) cannot be estimated."""
        configuration = GasConfiguration()
        w3 = build_web3_mock(base_fee=None)

        with pytest.raises(ValueError, match="Failed to retrieve base fee"):
            await configuration.estimate_eip1559_gas_fees(w3)

        assert w3.eth.get_block.await_count == 3

    async def test_reward_averaging_uses_all_blocks(self) -> None:
        configuration = GasConfiguration()
        rewards = [
            [Wei(1 * GWEI)],
            [Wei(2 * GWEI)],
            [Wei(6 * GWEI)],
        ]
        w3 = build_web3_mock(base_fee=7 * GWEI, rewards=rewards)

        result = await configuration.estimate_eip1559_gas_fees(w3)

        assert result["maxPriorityFeePerGas"] == 3 * GWEI  # (1 + 2 + 6) // 3
        assert result["maxFeePerGas"] == 3 * GWEI + 2 * 7 * GWEI


class TestEstimateLegacyGasFees:
    async def test_default_gas_and_normal_multiplier(self) -> None:
        configuration = GasConfiguration()  # NORMAL -> 1.25x
        w3 = build_web3_mock(gas_price=20 * GWEI)

        result = await configuration.estimate_legacy_gas_fees(w3)

        assert result == {
            "gas": 800_000,  # ceil(800000 * 1.3) clamped to max_gas
            "gasPrice": 25_000_000_000,  # ceil(20 gwei * 1.25)
        }

    @pytest.mark.parametrize(
        ("gas_mode", "expected_gas_price"),
        [
            (GasMode.SLOW, 20 * GWEI),  # 1x
            (GasMode.NORMAL, 25 * GWEI),  # 1.25x
            (GasMode.FAST, 30 * GWEI),  # 1.5x
        ],
    )
    async def test_gas_price_multiplier_math(
        self, gas_mode: GasMode, expected_gas_price: int
    ) -> None:
        configuration = GasConfiguration(gas_mode=gas_mode)
        w3 = build_web3_mock(gas_price=20 * GWEI)

        result = await configuration.estimate_legacy_gas_fees(w3)

        assert result["gasPrice"] == expected_gas_price

    async def test_gas_price_rounded_up(self) -> None:
        configuration = GasConfiguration(gas_mode=GasMode.NORMAL)
        w3 = build_web3_mock(gas_price=3)

        result = await configuration.estimate_legacy_gas_fees(w3)

        assert result["gasPrice"] == 4  # ceil(3 * 1.25) = ceil(3.75)

    async def test_uses_gas_from_transaction_params(self) -> None:
        configuration = GasConfiguration()
        w3 = build_web3_mock()

        result = await configuration.estimate_legacy_gas_fees(w3, {"gas": 21_000})

        assert result["gas"] == 27_300  # ceil(21000 * 1.3)
        w3.eth.estimate_gas.assert_not_awaited()

    async def test_estimates_gas_when_params_have_no_gas(self) -> None:
        configuration = GasConfiguration()
        w3 = build_web3_mock(estimated_gas=52_000)
        transaction_params: dict[str, Any] = {
            "to": "0x5DF9B87991262F6BA471F09758CDE1c0FC1De734",
            "value": Wei(1),
        }

        result = await configuration.estimate_legacy_gas_fees(
            w3,
            transaction_params,  # type: ignore[arg-type]
        )

        assert result["gas"] == 67_600  # ceil(52000 * 1.3)
        w3.eth.estimate_gas.assert_awaited_once_with(transaction_params)


class TestGetGas:
    async def test_dispatches_to_eip1559_by_default(self) -> None:
        configuration = GasConfiguration()
        w3 = build_web3_mock()

        result = await configuration.get_gas(w3)

        assert result == {
            "gas": 800_000,
            "maxPriorityFeePerGas": 2_500_000_000,
            "maxFeePerGas": 22_500_000_000,
        }
        assert "gasPrice" not in result

    async def test_dispatches_to_legacy(self) -> None:
        configuration = GasConfiguration()
        w3 = build_web3_mock(gas_price=20 * GWEI)

        result = await configuration.get_gas(w3, gas_strategy=GasStrategy.LEGACY)

        assert result == {"gas": 800_000, "gasPrice": 25_000_000_000}
        assert "maxFeePerGas" not in result


class TestMaxGasPayable:
    def test_legacy_fees(self) -> None:
        fees = {"gas": 21_000, "gasPrice": 25 * GWEI}
        assert GasConfiguration.max_gas_payable(fees) == 21_000 * 25 * GWEI

    def test_eip1559_fees(self) -> None:
        fees = {
            "gas": 21_000,
            "maxPriorityFeePerGas": 2 * GWEI,
            "maxFeePerGas": 30 * GWEI,
        }
        assert GasConfiguration.max_gas_payable(fees) == 21_000 * 30 * GWEI

    def test_gas_price_takes_precedence_over_max_fee(self) -> None:
        fees = {"gas": 10, "gasPrice": 7, "maxFeePerGas": 9}
        assert GasConfiguration.max_gas_payable(fees) == 70


class TestMaxGasClamp:
    """The configured max_gas is a hard ceiling on the buffered gas limit."""

    async def test_eip1559_gas_clamped_to_max_gas(self) -> None:
        configuration = GasConfiguration(max_gas=100_000)
        w3 = build_web3_mock(base_fee=7 * GWEI, estimated_gas=200_000)

        result = await configuration.estimate_eip1559_gas_fees(
            w3,
            transaction_params=cast(
                TxParams, {"to": "0x5DF9B87991262F6BA471F09758CDE1c0FC1De734"}
            ),
        )

        assert result["gas"] == 100_000  # ceil(200000 * 1.3) clamped

    async def test_eip1559_gas_below_max_gas_unclamped(self) -> None:
        configuration = GasConfiguration(max_gas=100_000)
        w3 = build_web3_mock(base_fee=7 * GWEI, estimated_gas=21_000)

        result = await configuration.estimate_eip1559_gas_fees(
            w3,
            transaction_params=cast(
                TxParams, {"to": "0x5DF9B87991262F6BA471F09758CDE1c0FC1De734"}
            ),
        )

        assert result["gas"] == 27_300  # ceil(21000 * 1.3), under the ceiling

    async def test_legacy_gas_clamped_to_max_gas(self) -> None:
        configuration = GasConfiguration(max_gas=100_000)
        w3 = build_web3_mock(gas_price=20 * GWEI, estimated_gas=200_000)

        result = await configuration.estimate_legacy_gas_fees(
            w3,
            transaction_params=cast(
                TxParams, {"to": "0x5DF9B87991262F6BA471F09758CDE1c0FC1De734"}
            ),
        )

        assert result["gas"] == 100_000
