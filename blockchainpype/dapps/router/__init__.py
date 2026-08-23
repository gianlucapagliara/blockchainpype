"""
This package provides the abstract DEX router layer: swap models and the
DecentralizedExchange facade dispatching to protocol-specific strategies.
"""

from .dex import (
    DecentralizedExchange,
    DexConfiguration,
    ProtocolConfiguration,
    ProtocolImplementation,
)
from .models import SlippageMode, SwapHop, SwapMode, SwapRoute

__all__ = [
    "DecentralizedExchange",
    "DexConfiguration",
    "ProtocolConfiguration",
    "ProtocolImplementation",
    "SlippageMode",
    "SwapHop",
    "SwapMode",
    "SwapRoute",
]
