"""
This package provides interfaces for interacting with Ethereum smart contracts and dApps.
It includes implementations for standard contracts like ERC-20 tokens and provides
base classes for building custom contract interfaces.

Protocol implementations (Aave, Polymarket, ...) are exported lazily via PEP 562:
they import :mod:`blockchainpype.evm.wallet`, which itself imports this package's
:mod:`~blockchainpype.evm.dapp.erc20` submodule, so importing them eagerly here
would create a circular import whenever the wallet module is imported first.
"""

from importlib import import_module
from typing import Any

from .gas import GasPriceCappedConfiguration, cap_gas_price_fields
from .unsigned import (
    UNSIGNED_TX_DATA_KEY,
    build_unsigned_transaction,
    unsigned_tx_params,
)

_LAZY_EXPORTS: dict[str, str] = {
    "AaveV3": ".money_market",
    "AaveV3Configuration": ".money_market",
    "AaveV3DataProviderContract": ".money_market",
    "AaveV3MoneyMarket": ".money_market",
    "AaveV3PoolContract": ".money_market",
    "EVMMoneyMarket": ".money_market",
    "EVMMoneyMarketConfiguration": ".money_market",
    "EVMBettingMarket": ".betting_market",
    "EVMBettingMarketConfiguration": ".betting_market",
    "Polymarket": ".betting_market",
    "PolymarketBettingMarket": ".betting_market",
    "PolymarketConfiguration": ".betting_market",
}

__all__ = [
    "UNSIGNED_TX_DATA_KEY",
    "GasPriceCappedConfiguration",
    "build_unsigned_transaction",
    "cap_gas_price_fields",
    "unsigned_tx_params",
    *sorted(_LAZY_EXPORTS),
]


def __getattr__(name: str) -> Any:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(module_name, __name__), name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_EXPORTS))
