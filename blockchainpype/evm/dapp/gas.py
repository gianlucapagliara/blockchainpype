"""
Gas-price capping for dapp-built EVM transactions.

Protocol configurations (e.g.
:attr:`~blockchainpype.dapps.betting_market.models.BettingMarketConfiguration.max_gas_price_gwei`)
express an upper bound on what a strategy is willing to pay per gas unit. The
wallet's :class:`~blockchainpype.evm.blockchain.gas.GasConfiguration` estimates
fees from the chain and knows nothing about such a bound, so this module wraps
it: :class:`GasPriceCappedConfiguration` clamps every fee field of an estimate
at the configured maximum and can be handed to
``EthereumWallet.build_transaction(gas_configuration=...)`` — including through
the ERC-20 ``place_*`` helpers.
"""

from collections.abc import Mapping
from typing import Any, Final

from web3 import AsyncWeb3
from web3.types import TxParams

from blockchainpype.evm.blockchain.gas import GasConfiguration, GasStrategy

__all__ = [
    "GAS_PRICE_FIELDS",
    "GWEI",
    "GasPriceCappedConfiguration",
    "cap_gas_price_fields",
]

#: Wei per gwei.
GWEI: Final[int] = 10**9

#: The transaction fields expressing a price per gas unit. ``gasPrice`` is the
#: legacy field; the two ``max*`` fields are the EIP-1559 pair.
GAS_PRICE_FIELDS: Final[tuple[str, ...]] = (
    "gasPrice",
    "maxFeePerGas",
    "maxPriorityFeePerGas",
)


def cap_gas_price_fields(
    values: Mapping[str, Any], max_gas_price_gwei: int | None
) -> dict[str, Any]:
    """Clamp the gas-price fields of a fee/parameter mapping at a maximum.

    Only the integer fields listed in :data:`GAS_PRICE_FIELDS` are touched, and
    only when they exceed the cap; every other entry is copied unchanged. The
    EIP-1559 invariant ``maxPriorityFeePerGas <= maxFeePerGas`` is preserved
    because both fields are clamped at the same value.

    Args:
        values: Fee estimates or full transaction parameters
        max_gas_price_gwei: The cap in gwei; ``None`` disables capping

    Returns:
        dict[str, Any]: A new mapping with the capped values
    """
    capped = dict(values)
    if max_gas_price_gwei is None:
        return capped

    cap = max_gas_price_gwei * GWEI
    for field in GAS_PRICE_FIELDS:
        value = capped.get(field)
        if isinstance(value, int) and not isinstance(value, bool) and value > cap:
            capped[field] = cap
    return capped


class GasPriceCappedConfiguration(GasConfiguration):
    """A :class:`GasConfiguration` whose estimates never exceed a price cap.

    The estimation logic (gas mode, block sampling, gas limits) is inherited
    unchanged; only the resulting fee fields are clamped at
    ``max_gas_price_gwei``.

    Attributes:
        max_gas_price_gwei (int): Maximum price per gas unit, in gwei
    """

    max_gas_price_gwei: int

    @classmethod
    def from_configuration(
        cls, configuration: GasConfiguration, max_gas_price_gwei: int
    ) -> "GasPriceCappedConfiguration":
        """Wrap an existing gas configuration with a price cap.

        Args:
            configuration: The estimation settings to reuse (typically the
                wallet's own gas configuration). May itself be capped: its
                own ``max_gas_price_gwei`` is replaced, and the stricter of
                the two caps wins.
            max_gas_price_gwei: Maximum price per gas unit, in gwei

        Returns:
            GasPriceCappedConfiguration: The capped configuration
        """
        settings = configuration.model_dump()
        existing_cap = settings.pop("max_gas_price_gwei", None)
        if existing_cap is not None:
            max_gas_price_gwei = min(max_gas_price_gwei, existing_cap)
        return cls(**settings, max_gas_price_gwei=max_gas_price_gwei)

    async def get_gas(
        self,
        w3: AsyncWeb3[Any],
        transaction_params: TxParams | None = None,
        gas_strategy: GasStrategy = GasStrategy.EIP1559,
    ) -> dict[str, int]:
        """Estimate gas fees and clamp every price field at the cap."""
        fees = await super().get_gas(w3, transaction_params, gas_strategy)
        return {
            key: int(value)
            for key, value in cap_gas_price_fields(
                fees, self.max_gas_price_gwei
            ).items()
        }
