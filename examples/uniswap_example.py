"""
Example demonstrating how to use the Uniswap DEX implementation.

This example shows how to:
1. Set up a Uniswap DEX instance
2. Quote swaps on both V2 and V3
3. Execute swaps
4. Get liquidity information
"""

import asyncio
import logging
from decimal import Decimal
from pprint import pprint as print
from typing import cast

from blockchainpype.dapps.router.models import SwapMode
from blockchainpype.evm.asset import EthereumNativeAsset
from blockchainpype.evm.blockchain.blockchain import EthereumBlockchain
from blockchainpype.evm.blockchain.identifier import EthereumAddress
from blockchainpype.evm.dapp.erc20 import (
    ERC20Contract,
    ERC20ContractConfiguration,
    ERC20Token,
)
from blockchainpype.evm.dapp.uniswap import UniswapDEX
from blockchainpype.factory import BlockchainFactory
from blockchainpype.initializer import BlockchainsInitializer
from examples.basic.configure import CustomBlockchainConfigurations

logger = logging.getLogger(__name__)


async def main():
    """Main example function."""
    BlockchainsInitializer.configure(
        configurations=CustomBlockchainConfigurations,
    )
    blockchain = cast(
        EthereumBlockchain, BlockchainFactory.get_by_identifier("ethereum")
    )

    # Create Uniswap DEX instance (uses default Ethereum mainnet configuration)
    uniswap = UniswapDEX(blockchain)

    # Define some common tokens
    usdt = ERC20Token(
        platform=blockchain.platform,
        identifier=EthereumAddress.from_string(
            "0xdAC17F958D2ee523a2206206994597C13D831ec7"
        ),  # USDC on mainnet
        contract=ERC20Contract(
            configuration=ERC20ContractConfiguration(
                platform=blockchain.platform,
                address=EthereumAddress.from_string(
                    "0xdAC17F958D2ee523a2206206994597C13D831ec7"
                ),
            )
        ),
    )
    await usdt.initialize_data()
    print(f"USDT: {usdt}")

    eth = EthereumNativeAsset(
        platform=blockchain.platform,
    )
    await eth.initialize_data()
    print(f"ETH: {eth}")

    weth = ERC20Token(
        platform=blockchain.platform,
        identifier=EthereumAddress.from_string(
            "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
        ),
        contract=ERC20Contract(
            configuration=ERC20ContractConfiguration(
                platform=blockchain.platform,
                address=EthereumAddress.from_string(
                    "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
                ),
            )
        ),
    )
    await weth.initialize_data()
    print(f"WETH: {weth}")

    # Example 1: Get a quote for swapping 100 USDC to WETH
    print("=== Getting swap quote ===")
    try:
        quote = await uniswap.quote_swap(
            input_asset=usdt,
            output_asset=weth,
            amount=Decimal("100"),  # 100 USDC
            mode=SwapMode.EXACT_INPUT,
        )
        print(f"Quote: {quote}")
        print(
            f"Quote: {quote.input_amount} {quote.input_asset.data.symbol} -> {quote.output_amount} {quote.output_asset.data.symbol}"
        )
        print(f"Protocol: {quote.protocol}")
        print(
            f"Price: {quote.price} {quote.output_asset.data.symbol} per {quote.input_asset.data.symbol}"
        )
        print(f"Max slippage: {quote.max_slippage * 100}%")
        print(f"Fees: {quote.taxes * 100}%")

    except Exception as e:
        logger.error(f"Error getting quote: {e}", exc_info=True)

    # Example 2: Get quotes from specific protocols
    print("=== Comparing V2 vs V3 quotes ===")
    try:
        # Get V2 quote
        v2_quote = await uniswap.quote_swap(
            input_asset=usdt,
            output_asset=weth,
            amount=Decimal("100"),
            protocol="uniswap_v2",
        )
        print(
            f"V2 Quote: {v2_quote.output_amount} {v2_quote.output_asset.data.symbol} (fees: {v2_quote.taxes * 100}%)"
        )

        # Get V3 quote
        v3_quote = await uniswap.quote_swap(
            input_asset=usdt,
            output_asset=weth,
            amount=Decimal("100"),
            protocol="uniswap_v3",
        )
        print(
            f"V3 Quote: {v3_quote.output_amount} {v3_quote.output_asset.data.symbol} (fees: {v3_quote.taxes * 100}%)"
        )

        # Compare
        if v2_quote.output_amount > v3_quote.output_amount:
            print("V2 offers better rate")
        else:
            print("V3 offers better rate")

    except Exception as e:
        logger.error(f"Error comparing quotes: {e}", exc_info=True)

    # Example 3: Get reserves/liquidity information
    print("=== Getting liquidity information ===")
    try:
        reserves = await uniswap.get_reserves(usdt, weth)
        print(
            f"Reserves: {reserves[0]} {usdt.data.symbol}, {reserves[1]} {weth.data.symbol}"
        )

        # Calculate current pool price
        pool_price = reserves[1] / reserves[0]  # WETH per USDC
        print(
            f"Current pool price: {pool_price} {weth.data.symbol} per {usdt.data.symbol}"
        )

    except Exception as e:
        logger.error(f"Error getting reserves: {e}", exc_info=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
