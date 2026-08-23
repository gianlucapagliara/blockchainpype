"""
This package provides EVM-specific implementations for betting market protocols.
It includes the Polymarket integration: the CLOB client with EIP-712 order
signing (off-chain trading) and the on-chain ConditionalTokens/allowance flows.
"""

from .clob import (
    ClobApiError,
    ClobClient,
    ClobCredentials,
    ClobOrder,
    OrderPostResponse,
    OrderSide,
    SignatureType,
    SignedClobOrder,
    build_hmac_signature,
    build_order_typed_data,
    compute_order_amounts,
    serialize_query_params,
    sign_clob_order,
)
from .polymarket import (
    EVMBettingMarket,
    EVMBettingMarketConfiguration,
    Polymarket,
    PolymarketBettingMarket,
    PolymarketConfiguration,
)

__all__ = [
    "ClobApiError",
    "ClobClient",
    "ClobCredentials",
    "ClobOrder",
    "EVMBettingMarket",
    "EVMBettingMarketConfiguration",
    "OrderPostResponse",
    "OrderSide",
    "Polymarket",
    "PolymarketBettingMarket",
    "PolymarketConfiguration",
    "SignatureType",
    "SignedClobOrder",
    "build_hmac_signature",
    "build_order_typed_data",
    "compute_order_amounts",
    "serialize_query_params",
    "sign_clob_order",
]
