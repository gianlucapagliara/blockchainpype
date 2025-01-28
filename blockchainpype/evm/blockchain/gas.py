"""
This module provides comprehensive gas fee estimation and management for Ethereum transactions.
It supports both legacy and EIP-1559 gas fee models, with configurable speed modes and
retry mechanisms for reliable gas estimation.
"""

import math
from enum import Enum
from functools import reduce

from pydantic import BaseModel, Field
from web3 import AsyncWeb3
from web3.types import TxParams


class GasMode(Enum):
    """
    Enumeration of gas price modes for transaction speed preferences.

    Attributes:
        SLOW: Lowest gas price, longer confirmation time (>1 min)
        NORMAL: Balanced gas price, medium confirmation time (~30 sec)
        FAST: Higher gas price, faster confirmation time (<10 sec)
    """

    SLOW = "slow"
    NORMAL = "normal"
    FAST = "fast"


GAS_PRICE_MULTIPLIERS_BY_MODE: dict[GasMode, float] = {
    GasMode.SLOW: 1,
    GasMode.NORMAL: 1.25,
    GasMode.FAST: 1.5,
}

EIP1559_PERCENTILES_BY_MODE: dict[GasMode, list[float]] = {
    GasMode.SLOW: [10.0, 20.0, 30.0, 40.0, 50.0],  # <1min
    GasMode.NORMAL: [30.0, 50.0, 70.0, 90.0],  # <30sec
    GasMode.FAST: [70.0, 80.0, 90.0],  # <10sec
}


class GasStrategy(Enum):
    """
    Enumeration of gas fee calculation strategies.

    Attributes:
        EIP1559: Modern gas fee model with base fee and priority fee
        LEGACY: Traditional gas price model
    """

    EIP1559 = "eip1559"
    LEGACY = "legacy"


class GasConfiguration(BaseModel):
    """
    Configuration for gas fee estimation and limits.

    This class provides settings for gas fee calculation, including speed preferences,
    block sampling, and gas limits for different transaction types.

    Attributes:
        gas_mode (GasMode): Speed preference for transactions (default: NORMAL)
        n_blocks (int): Number of blocks to analyze for fee estimation (default: 10)
        max_gas (int): Maximum gas limit allowed (default: 800000)
        default_gas (int): Default gas limit for transactions (default: 800000)
        default_cancel_gas (int): Default gas limit for cancellation txs (default: 800000)
    """

    gas_mode: GasMode = Field(default=GasMode.NORMAL)
    n_blocks: int = Field(default=10)
    max_gas: int = Field(default=800000)
    default_gas: int = Field(default=800000)
    default_cancel_gas: int = Field(default=800000)

    async def get_gas(
        self,
        w3: AsyncWeb3,
        transaction_params: TxParams | None = None,
        gas_strategy: GasStrategy = GasStrategy.EIP1559,
    ) -> dict[str, int]:
        """
        Get gas fee estimates based on the specified strategy.

        Args:
            w3 (AsyncWeb3): Web3 instance for blockchain interaction
            transaction_params (TxParams | None): Optional transaction parameters
            gas_strategy (GasStrategy): Gas calculation strategy to use

        Returns:
            dict[str, int]: Estimated gas parameters based on the strategy
        """
        if gas_strategy == GasStrategy.EIP1559:
            return await self.estimate_eip1559_gas_fees(w3, transaction_params)
        else:
            return await self.estimate_legacy_gas_fees(w3, transaction_params)

    async def estimate_eip1559_gas_fees(
        self,
        w3: AsyncWeb3,
        transaction_params: TxParams | None = None,
        n_max_retries: int = 3,
    ) -> dict[str, int]:
        """
        Estimate gas fees for EIP-1559 transactions.

        This method calculates gas fees using the EIP-1559 model, which includes:
        - Base Fee: Set by blockchain, varies at each block, always burned
        - Priority Fee: User-set tip/reward paid to miners
        - Max Fee: Maximum total fee per gas unit

        The estimation process:
        1. Retrieves base fee from pending block
        2. Calculates next block's potential base fee
        3. Analyzes recent blocks for priority fee estimation
        4. Combines fees with safety margins

        Args:
            w3 (AsyncWeb3): Web3 instance for blockchain interaction
            transaction_params (TxParams | None): Optional transaction parameters
            n_max_retries (int): Maximum number of retry attempts

        Returns:
            dict[str, int]: Gas parameters including gas limit, max priority fee, and max fee

        Raises:
            ValueError: If fee estimation fails or parameters are invalid
        """
        retries = 0
        result: dict[str, int] | None = None

        while retries < n_max_retries:
            try:
                percentiles = EIP1559_PERCENTILES_BY_MODE[self.gas_mode]
                if percentiles is None:
                    raise ValueError("Invalid speed")

                if transaction_params is None:
                    gas = self.default_gas
                else:
                    if "gas" in transaction_params:
                        gas = transaction_params["gas"]
                    else:
                        gas = int(await w3.eth.estimate_gas(transaction_params))

                base_fee = (await w3.eth.get_block("pending")).get(
                    "baseFeePerGas", None
                )
                if base_fee is None:
                    raise ValueError("Failed to retrieve base fee")

                next_base_fee = base_fee * 2

                fee_history = await w3.eth.fee_history(
                    self.n_blocks, "pending", percentiles
                )
                reward_history = fee_history["reward"]
                rewards = reduce(lambda x, y: x + y, reward_history)
                avg_reward = sum(rewards) // len(rewards)

                result = {
                    "gas": int(math.ceil(gas * 1.3)),
                    "maxPriorityFeePerGas": avg_reward,
                    "maxFeePerGas": avg_reward + next_base_fee,
                }
            except Exception as e:
                retries += 1
                if retries >= n_max_retries:
                    raise e

        if result is None:
            raise ValueError("Failed to estimate gas fees")

        return result

    async def estimate_legacy_gas_fees(
        self,
        w3: AsyncWeb3,
        transaction_params: TxParams | None = None,
    ) -> dict[str, int]:
        """
        Estimate gas fees using the legacy gas price model.

        This method calculates gas fees using the pre-EIP-1559 model, where a single
        gas price is used. The price is adjusted based on the selected speed mode.

        Args:
            w3 (AsyncWeb3): Web3 instance for blockchain interaction
            transaction_params (TxParams | None): Optional transaction parameters

        Returns:
            dict[str, int]: Gas parameters including gas limit and gas price

        Raises:
            ValueError: If speed mode is invalid
        """
        gas_price_multiplier = GAS_PRICE_MULTIPLIERS_BY_MODE[self.gas_mode]
        if gas_price_multiplier is None:
            raise ValueError("Invalid speed")

        gas_price = await w3.eth.gas_price
        if transaction_params is None:
            gas = self.default_gas
        else:
            if "gas" in transaction_params:
                gas = transaction_params["gas"]
            else:
                gas = await w3.eth.estimate_gas(transaction_params)

        return {
            "gas": int(math.ceil(gas * 1.3)),
            "gasPrice": int(math.ceil(gas_price * gas_price_multiplier)),
        }

    @staticmethod
    def max_gas_payable(gas_fees: dict) -> int:
        """
        Calculate the maximum amount of gas payable for a transaction.

        This method calculates the maximum possible gas cost based on the provided
        gas parameters, supporting both legacy and EIP-1559 fee structures.

        Args:
            gas_fees (dict): Gas fee parameters

        Returns:
            int: Maximum possible gas cost in wei
        """
        if "gasPrice" in gas_fees:
            return gas_fees["gas"] * gas_fees["gasPrice"]
        else:
            return gas_fees["gas"] * gas_fees["maxFeePerGas"]
