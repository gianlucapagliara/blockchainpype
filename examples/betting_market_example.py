"""Read Polymarket markets and sign a CLOB order, without posting anything.

Polymarket trades off-chain on a central limit order book: orders are EIP-712
structs signed by the maker and posted to the CLOB REST API, while settlement
happens on Polygon through the CTF Exchange and the Gnosis ConditionalTokens
(ERC-1155) contracts.
:class:`~blockchainpype.evm.dapp.betting_market.Polymarket` wraps all of it.

Reading (markets, prices, positions) needs nothing but HTTP. Signing an order
needs a wallet with a private key but no network at all — it is pure local
EIP-712 signing, which is why this example can build a real signed order with a
throwaway key.

**Nothing is ever posted here.** ``place_buy`` / ``place_sell`` /
``post_order`` require L2 API credentials
(:class:`~blockchainpype.evm.dapp.betting_market.ClobCredentials`) and are
deliberately not called: the signed payload is only printed.

Polygon is not part of the stock configurations, so this example builds its own
:class:`EthereumBlockchain` for chain 137 and hands it to the strategy — no
global factory registration needed.

Importing this module is side-effect free. Environment variables:

* ``POLYGON_RPC_URL``: Polygon endpoint used for the on-chain contracts
  (a public default is used when unset). Building a wallet inside a running
  event loop schedules one background native-balance refresh against it; the
  on-chain flows (approvals, redemptions) are not exercised here.
* Nothing else: the market data comes from Polymarket's public APIs.

Run it with::

    uv run python -m examples.betting_market_example
"""

from __future__ import annotations

import asyncio
import json
import os
from decimal import Decimal

from eth_account import Account
from financepype.platforms.blockchain import BlockchainPlatform
from pydantic import SecretStr
from web3 import AsyncHTTPProvider

from blockchainpype.dapps.betting_market import BettingMarketModel
from blockchainpype.evm.blockchain.blockchain import (
    EthereumBlockchain,
    EthereumBlockchainType,
)
from blockchainpype.evm.blockchain.configuration import (
    EthereumBlockchainConfiguration,
    EthereumConnectivityConfiguration,
    EthereumNativeAssetConfiguration,
)
from blockchainpype.evm.blockchain.identifier import EthereumAddress
from blockchainpype.evm.dapp.betting_market import (
    OrderSide,
    Polymarket,
    PolymarketConfiguration,
)
from blockchainpype.evm.wallet.identifier import EthereumWalletIdentifier
from blockchainpype.evm.wallet.signer import EthereumSignerConfiguration
from blockchainpype.evm.wallet.wallet import (
    EthereumWallet,
    EthereumWalletConfiguration,
)
from examples.basic.configure import load_environment

POLYGON_RPC_URL_ENV = "POLYGON_RPC_URL"
DEFAULT_POLYGON_RPC_URL = "https://polygon-rpc.com"
POLYGON_CHAIN_ID = 137

#: A real outcome-token id, used to show the offline order-signing flow.
#: Token ids are globally unique on the CLOB, so no market lookup is needed.
EXAMPLE_OUTCOME_TOKEN_ID = (
    "71321045679252212594626385532706912750332728571942532289631379312455583992563"
)

ORDER_PRICE = Decimal("0.42")  # USDC per share, strictly between 0 and 1
ORDER_SIZE = Decimal("25")  # outcome shares
MARKETS_TO_LIST = 5


def build_polygon_blockchain() -> EthereumBlockchain:
    """Build the Polygon blockchain Polymarket's contracts live on."""
    rpc_url = os.getenv(POLYGON_RPC_URL_ENV, "").strip() or DEFAULT_POLYGON_RPC_URL
    return EthereumBlockchain(
        configuration=EthereumBlockchainConfiguration(
            platform=BlockchainPlatform(
                identifier="polygon",
                type=EthereumBlockchainType,
                chain_id=POLYGON_CHAIN_ID,
            ),
            native_asset=EthereumNativeAssetConfiguration(
                name="Polygon Ecosystem Token",
                symbol="POL",
                decimals=18,
            ),
            connectivity=EthereumConnectivityConfiguration(
                rpc_provider=AsyncHTTPProvider(rpc_url)
            ),
            explorer=None,
        )
    )


def build_throwaway_wallet(blockchain: EthereumBlockchain) -> EthereumWallet:
    """Create a fresh in-memory account and wrap it in a wallet.

    The key exists only for the lifetime of this process: it can sign orders
    (which is enough to show the payload) but holds no funds and no
    Polymarket credentials, so the orders it signs are unusable.
    """
    account = Account.create()
    configuration = EthereumWalletConfiguration(
        identifier=EthereumWalletIdentifier(
            name="polymarket-example",
            platform=blockchain.platform,
            address=EthereumAddress.from_string(account.address),
        ),
        signer=EthereumSignerConfiguration(
            private_key=SecretStr(account.key.hex()),
        ),
    )
    return EthereumWallet(configuration=configuration, blockchain=blockchain)


def describe_market(market: BettingMarketModel) -> None:
    """Print a market and its outcome prices."""
    print(f"- {market.title}")
    print(f"    id:        {market.market_id}")
    print(f"    status:    {market.status.value} (active: {market.is_active})")
    print(f"    volume:    {market.total_volume}")
    for outcome in market.outcomes:
        prices = ", ".join(
            f"{token.outcome_name} {token.current_price}"
            for token in outcome.outcome_tokens
        )
        print(f"    outcome:   {outcome.outcome_text} -> {prices}")


def sign_example_order(polymarket: Polymarket, wallet: EthereumWallet) -> None:
    """Build and EIP-712-sign a BUY order locally. Nothing is sent."""
    print()
    print("=== Signing an order (offline) ===")
    print(f"maker: {wallet.address.string} (throwaway key)")

    signed = polymarket.build_order(
        outcome_token_id=EXAMPLE_OUTCOME_TOKEN_ID,
        side=OrderSide.BUY,
        price=ORDER_PRICE,
        size=ORDER_SIZE,
    )
    order = signed.order
    print(f"side:         {order.side.name}")
    print(f"price/size:   {ORDER_PRICE} USDC x {ORDER_SIZE} shares")
    print(f"maker amount: {order.maker_amount} (USDC, 6 decimals)")
    print(f"taker amount: {order.taker_amount} (shares, 6 decimals)")
    print(f"fee rate:     {order.fee_rate_bps} bps")
    print(f"verifying:    {polymarket.verifying_contract(neg_risk=False)}")
    print(f"signature:    {signed.signature[:20]}...")
    print()
    print("POST /order payload:")
    print(json.dumps(signed.to_api_payload(), indent=2))
    print()
    print("Posting it would need ClobCredentials (api key/secret/passphrase);")
    print("place_buy()/post_order() are intentionally not called here.")


async def show_markets(polymarket: Polymarket) -> None:
    """Fetch active markets and one market's live prices (needs HTTP)."""
    print()
    print(f"=== Active markets (first {MARKETS_TO_LIST}) ===")
    markets = await polymarket.get_markets(status="active", limit=MARKETS_TO_LIST)
    if not markets:
        print("no active markets returned")
        return

    for market in markets:
        describe_market(market)

    first = markets[0]
    token_ids = [
        token.token_id for outcome in first.outcomes for token in outcome.outcome_tokens
    ]
    if not token_ids:
        return

    print()
    print(f"=== Live quote for {first.title} ===")
    token_id = token_ids[0]
    midpoint = await polymarket.get_outcome_token_price(first.market_id, token_id)
    shares, cost = await polymarket.calculate_buy_quote(
        first.market_id, token_id, Decimal("10")
    )
    print(f"token id:  {token_id}")
    print(f"midpoint:  {midpoint}")
    print(f"10 USDC -> {shares} shares (total cost {cost} USDC incl. fees)")


async def main() -> None:
    """Sign an order offline, then read live markets over HTTP."""
    load_environment()
    blockchain = build_polygon_blockchain()
    wallet = build_throwaway_wallet(blockchain)

    configuration = PolymarketConfiguration()
    polymarket = Polymarket(configuration, blockchain, wallet=wallet)

    print("=== Configuration ===")
    print(f"protocol:            {configuration.protocol_name}")
    print(f"chain id:            {blockchain.platform.chain_id}")
    print(f"CLOB API:            {configuration.api_base_url}")
    print(f"Gamma API:           {configuration.gamma_api_url}")
    print(f"Data API:            {configuration.data_api_url}")
    print(f"CTF exchange:        {configuration.ctf_exchange_address}")
    print(f"NegRisk exchange:    {configuration.neg_risk_ctf_exchange_address}")
    print(f"conditional tokens:  {configuration.conditional_tokens_address}")
    print(f"collateral (USDC):   {configuration.collateral_token_address}")
    print(f"credentials:         {'set' if configuration.credentials else 'none'}")

    sign_example_order(polymarket, wallet)

    try:
        await show_markets(polymarket)
    except Exception as error:
        print(f"Market lookup failed: {type(error).__name__}: {error}")
        print("The Polymarket public APIs must be reachable for the read steps.")
    finally:
        # Releases the CLOB/Gamma HTTP sessions owned by the strategy, then the
        # web3 provider session opened by the wallet's balance refresh.
        await polymarket.close()
        await blockchain.web3.provider.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
