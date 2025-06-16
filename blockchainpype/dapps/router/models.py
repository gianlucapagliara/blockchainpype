from decimal import Decimal
from enum import Enum
from typing import Self

from financepype.assets.blockchain import BlockchainAsset
from pydantic import BaseModel, model_validator


class SwapMode(str, Enum):
    EXACT_INPUT = "exact_input"
    EXACT_OUTPUT = "exact_output"
    UNDEFINED = "undefined"


class SlippageMode(Enum):
    VALUE_PROTECTION = "real_value"
    FRONTRUNNING_PROTECTION = "frontrunning_protection"


class SwapHop(BaseModel):
    input_asset: BlockchainAsset
    input_amount: Decimal
    output_asset: BlockchainAsset
    output_amount: Decimal
    are_amounts_raw: bool

    @model_validator(mode="after")
    def validate_swap_hop(self) -> Self:
        if self.input_asset == self.output_asset:
            raise ValueError("Input and output assets must be different.")

        if self.input_amount == 0 or self.output_amount == 0:
            raise ValueError("Amounts must be greater than 0.")

        if self.are_amounts_raw and (
            self.input_amount % 1 != 0 or self.output_amount % 1 != 0
        ):
            raise ValueError("Raw amounts must be integers.")

        return self

    @property
    def price(self) -> Decimal:
        if self.are_amounts_raw:
            raise ValueError("Amounts are raw, price cannot be calculated.")
        return self.output_amount / self.input_amount

    @property
    def price_inverted(self) -> Decimal:
        return 1 / self.price


class SwapRoute(SwapHop):
    sequence: list[SwapHop]
    mode: SwapMode
    max_slippage: Decimal
    taxes: Decimal
    protocol: str

    @model_validator(mode="after")
    def validate_swap_route(self) -> Self:
        if self.mode == SwapMode.UNDEFINED:
            raise ValueError("Swap mode must be defined.")

        if len(self.sequence) == 0:
            raise ValueError("Path must have at least 1 edges.")

        if self.are_amounts_raw:
            raise ValueError("Amounts of a path cannot be raw.")

        return self

    @property
    def max_taxed_slippage(self) -> Decimal:
        return self.max_slippage + self.taxes
