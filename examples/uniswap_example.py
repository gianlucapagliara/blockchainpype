"""Quote and build Uniswap V2/V3 swaps on Ethereum mainnet.

:class:`~blockchainpype.evm.dapp.uniswap.UniswapDEX` is a facade over one
strategy per configured protocol (``uniswap_v2``, ``uniswap_v3``). It is bound
to an explicit :class:`EthereumBlockchain` at construction, so the same code
runs against mainnet, Polygon or a local hardhat node.

What this example shows:

1. Quoting a swap across every protocol and per protocol.
2. Multi-hop routing through the configured intermediate assets.
3. Reading pair reserves.
4. Binding a wallet and **building** (never sending) the swap transaction.

Beware: ``DecentralizedExchange.execute_swap`` is build-only. It returns an
unsigned :class:`~blockchainpype.evm.transaction.EthereumTransaction` whose
web3 parameters are read back with
:func:`~blockchainpype.evm.dapp.unsigned.unsigned_tx_params`; signing and
broadcasting stay an explicit, separate step (and a real swap also needs an
ERC-20 ``approve`` of the router first).

Importing this module is side-effect free — every network call lives in
``main()``, which needs an Ethereum mainnet RPC:

* ``ETHEREUM_RPC_URLS``: comma-separated mainnet endpoints (a public default is
  used when unset; public nodes are heavily rate limited).
* ``ETHEREUM_PRIVATE_KEY``: optional. When set, a wallet is bound and the
  transaction-building step runs; otherwise only the read-only steps run.

Run it with::

    ETHEREUM_RPC_URLS=https://your-node uv run python -m examples.uniswap_example
"""

from __future__ import annotations

import asyncio
import logging
import os
from decimal import Decimal

from eth_account import Account
from pydantic import SecretStr

from blockchainpype.dapps.router.models import SwapMode, SwapRoute
from blockchainpype.evm.blockchain.blockchain import EthereumBlockchain
from blockchainpype.evm.blockchain.identifier import EthereumAddress
from blockchainpype.evm.dapp.erc20 import (
    ERC20Contract,
    ERC20ContractConfiguration,
    ERC20Token,
)
from blockchainpype.evm.dapp.uniswap import UniswapConfiguration, UniswapDEX
from blockchainpype.evm.dapp.unsigned import unsigned_tx_params
from blockchainpype.evm.wallet.identifier import EthereumWalletIdentifier
from blockchainpype.evm.wallet.signer import EthereumSignerConfiguration
from blockchainpype.evm.wallet.wallet import (
    EthereumWallet,
    EthereumWalletConfiguration,
)
from blockchainpype.factory import BlockchainFactory
from examples.basic.configure import (
    ETHEREUM_RPC_URLS_ENV,
    configure_blockchains,
    load_environment,
)

# Canonical Ethereum mainnet token addresses.
USDC_ADDRESS = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"  # USDC, 6 decimals
WETH_ADDRESS = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"  # WETH, 18 decimals
DAI_ADDRESS = "0x6B175474E89094C44Da98b954EedeAC495271d0F"  # DAI, 18 decimals

PRIVATE_KEY_ENV = "ETHEREUM_PRIVATE_KEY"

SWAP_AMOUNT_USDC = Decimal("1000")
MAX_SLIPPAGE = Decimal("0.005")  # 0.5%
DEADLINE_MINUTES = 10


def build_token(blockchain: EthereumBlockchain, address: str) -> ERC20Token:
    """Build an ERC-20 asset handle; metadata is fetched by initialize_data()."""
    token_address = EthereumAddress.from_string(address)
    return ERC20Token(
        platform=blockchain.platform,
        identifier=token_address,
        contract=ERC20Contract(
            ERC20ContractConfiguration(
                platform=blockchain.platform,
                address=token_address,
            )
        ),
    )


def build_wallet(blockchain: EthereumBlockchain) -> EthereumWallet | None:
    """Build a wallet from ``ETHEREUM_PRIVATE_KEY``, or None when unset.

    The address is derived from the key, so only the key has to be provided.
    """
    private_key = os.getenv(PRIVATE_KEY_ENV)
    if not private_key:
        return None

    account = Account.from_key(private_key)
    configuration = EthereumWalletConfiguration(
        identifier=EthereumWalletIdentifier(
            name="uniswap-example",
            platform=blockchain.platform,
            address=EthereumAddress.from_string(account.address),
        ),
        signer=EthereumSignerConfiguration(private_key=SecretStr(private_key)),
    )
    return EthereumWallet(configuration=configuration, blockchain=blockchain)


def token_summary(token: ERC20Token) -> str:
    """Describe a token, whether or not its on-chain data was loaded."""
    data = token.data
    if data is None:
        return f"{token.address.string} (metadata not loaded)"
    return f"{data.symbol} ({data.decimals} decimals)"


def describe_route(route: SwapRoute) -> str:
    """One-line summary of a quoted route."""
    path = " -> ".join(
        [route.input_asset.data.symbol]
        + [hop.output_asset.data.symbol for hop in route.sequence]
    )
    return (
        f"{route.input_amount} {route.input_asset.data.symbol} -> "
        f"{route.output_amount} {route.output_asset.data.symbol} "
        f"[{route.protocol}, {len(route.sequence)} hop(s): {path}]"
    )


async def quote_across_protocols(
    uniswap: UniswapDEX, usdc: ERC20Token, weth: ERC20Token
) -> SwapRoute:
    """Quote USDC -> WETH on the best protocol, then on each one separately."""
    print("=== Best quote across protocols ===")
    best = await uniswap.quote_swap(
        input_asset=usdc,
        output_asset=weth,
        amount=SWAP_AMOUNT_USDC,
        mode=SwapMode.EXACT_INPUT,
        max_slippage=MAX_SLIPPAGE,
    )
    print(describe_route(best))
    print(f"price:        {best.price} WETH per USDC")
    print(f"max slippage: {best.max_slippage * 100}%")
    print(f"protocol fee: {best.taxes * 100}%")

    print()
    print("=== Per-protocol quotes ===")
    for protocol in uniswap.supported_protocols:
        try:
            quote = await uniswap.quote_swap(
                input_asset=usdc,
                output_asset=weth,
                amount=SWAP_AMOUNT_USDC,
                protocol=protocol,
                max_slippage=MAX_SLIPPAGE,
            )
        except Exception as error:  # a protocol may simply have no pool
            print(f"{protocol}: no route ({error})")
            continue
        print(f"{protocol}: {quote.output_amount} WETH (fee {quote.taxes * 100}%)")

    return best


async def show_multi_hop_route(
    uniswap: UniswapDEX, usdc: ERC20Token, dai: ERC20Token
) -> None:
    """Let the facade pick between the direct pair and a route through WETH."""
    print()
    print("=== Best route USDC -> DAI (up to 2 hops) ===")
    route = await uniswap.find_best_route(
        input_asset=usdc,
        output_asset=dai,
        amount=SWAP_AMOUNT_USDC,
        mode=SwapMode.EXACT_INPUT,
        max_hops=2,
    )
    print(describe_route(route))


async def show_reserves(
    uniswap: UniswapDEX, usdc: ERC20Token, weth: ERC20Token
) -> None:
    """Print the decimal-adjusted reserves of the V2 USDC/WETH pair."""
    print()
    print("=== USDC/WETH reserves (uniswap_v2) ===")
    usdc_reserve, weth_reserve = await uniswap.get_reserves(
        usdc, weth, protocol="uniswap_v2"
    )
    print(f"{usdc_reserve} USDC / {weth_reserve} WETH")
    print(f"pool price: {usdc_reserve / weth_reserve} USDC per WETH")


async def build_swap_transaction(uniswap: UniswapDEX, route: SwapRoute) -> None:
    """Build (without signing or broadcasting) the transaction for a route."""
    print()
    print("=== Building the swap transaction ===")
    transaction = await uniswap.execute_swap(
        route=route,
        deadline_minutes=DEADLINE_MINUTES,
    )
    params = unsigned_tx_params(transaction)
    calldata = str(params["data"])
    print(f"operation id: {transaction.client_operation_id}")
    print(f"state:        {transaction.current_state.name} (unsigned)")
    print(f"router:       {params['to']}")
    print(f"from:         {params['from']}")
    print(f"calldata:     {calldata[:10]}... ({len(calldata) // 2 - 1} bytes)")
    print()
    print("To send it: approve the router for the input token, then")
    print("  wallet.sign_and_send_transaction(")
    print("      client_operation_id=transaction.client_operation_id,")
    print("      tx_data=dict(unsigned_tx_params(transaction)),")
    print("  )")


async def main() -> None:
    """Run the read-only steps, plus the build step when a key is available."""
    load_environment()
    configure_blockchains()
    blockchain = BlockchainFactory.get_evm_blockchain_by_identifier("ethereum")
    print(f"Using the Ethereum endpoints from {ETHEREUM_RPC_URLS_ENV}")

    # Building assets, wallet and facade is offline; only the calls below are not.
    usdc = build_token(blockchain, USDC_ADDRESS)
    weth = build_token(blockchain, WETH_ADDRESS)
    dai = build_token(blockchain, DAI_ADDRESS)
    wallet = build_wallet(blockchain)
    uniswap = UniswapDEX(
        blockchain,
        # WETH is the multi-hop intermediate considered by find_best_route.
        UniswapConfiguration.ethereum_mainnet(intermediate_assets=[weth]),
        wallet=wallet,
    )

    try:
        for token in (usdc, weth, dai):
            # Fetches name/symbol/decimals from the token contract.
            await token.initialize_data()
            print(f"loaded {token_summary(token)}")
        print()

        best_route = await quote_across_protocols(uniswap, usdc, weth)
        await show_multi_hop_route(uniswap, usdc, dai)
        await show_reserves(uniswap, usdc, weth)

        if wallet is None:
            print()
            print(
                f"Set {PRIVATE_KEY_ENV} to also build the swap transaction "
                "(a wallet supplies the sender, nonce and gas fees)."
            )
            return
        await build_swap_transaction(uniswap, best_route)
    except Exception as error:
        print(f"Network step failed: {type(error).__name__}: {error}")
        print(f"Point {ETHEREUM_RPC_URLS_ENV} at a working Ethereum RPC endpoint.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
