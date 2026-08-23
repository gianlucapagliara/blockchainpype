"""Read Aave V3 markets and build supply/borrow transactions.

:class:`~blockchainpype.evm.dapp.money_market.aave.AaveV3MoneyMarket` is the
money-market equivalent of the DEX facade: it registers one
:class:`~blockchainpype.evm.dapp.money_market.aave.AaveV3` strategy per
configured protocol and dispatches to it.
:class:`~blockchainpype.evm.dapp.money_market.aave.AaveV3Configuration` defaults
to the Aave V3 Ethereum mainnet deployment (the Pool and the
AaveProtocolDataProvider); pass explicit ``protocols`` entries for other
networks.

Units are normalized by the strategy, so what comes back is directly usable:
rates are decimal fractions (``0.0425`` = 4.25%), amounts are decimal-adjusted
by the reserve's decimals, and the health factor is a plain
:class:`~decimal.Decimal` (below 1 means liquidatable).

Reads work without a wallet. Every ``supply``/``withdraw``/``borrow``/``repay``
call **builds an unsigned transaction** — it never signs or broadcasts — so a
wallet is only needed to supply the sender, nonce and gas fields. Send it
yourself with ``wallet.sign_and_send_transaction(...)`` (and remember an ERC-20
``approve`` of the Pool before a real supply or repay).

Importing this module is side-effect free. ``main()`` needs an Ethereum mainnet
RPC (``ETHEREUM_RPC_URLS``); ``ETHEREUM_PRIVATE_KEY`` is optional and enables
the transaction-building step.

Run it with::

    ETHEREUM_RPC_URLS=https://your-node uv run python -m examples.money_market_example
"""

from __future__ import annotations

import asyncio
import os
from decimal import Decimal

from eth_account import Account
from pydantic import SecretStr

from blockchainpype.dapps.money_market import InterestRateMode, MarketData
from blockchainpype.evm.blockchain.blockchain import EthereumBlockchain
from blockchainpype.evm.blockchain.identifier import EthereumAddress
from blockchainpype.evm.dapp.erc20 import (
    ERC20Contract,
    ERC20ContractConfiguration,
    ERC20Token,
)
from blockchainpype.evm.dapp.money_market.aave import (
    AaveV3Configuration,
    AaveV3MoneyMarket,
)
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

USDC_ADDRESS = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
WETH_ADDRESS = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"

PRIVATE_KEY_ENV = "ETHEREUM_PRIVATE_KEY"
#: Inspected when no private key is provided; any address works for reads.
DEFAULT_USER_ADDRESS = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"

SUPPLY_AMOUNT = Decimal("100")  # USDC
BORROW_AMOUNT = Decimal("0.01")  # WETH


def build_token(blockchain: EthereumBlockchain, address: str) -> ERC20Token:
    """Build an ERC-20 asset handle for a reserve token."""
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
    """Build a wallet from ``ETHEREUM_PRIVATE_KEY``, or None when unset."""
    private_key = os.getenv(PRIVATE_KEY_ENV)
    if not private_key:
        return None

    account = Account.from_key(private_key)
    configuration = EthereumWalletConfiguration(
        identifier=EthereumWalletIdentifier(
            name="aave-example",
            platform=blockchain.platform,
            address=EthereumAddress.from_string(account.address),
        ),
        signer=EthereumSignerConfiguration(private_key=SecretStr(private_key)),
    )
    return EthereumWallet(configuration=configuration, blockchain=blockchain)


def percent(fraction: Decimal) -> str:
    """Format a decimal fraction as a percentage with two decimals."""
    return f"{fraction * 100:.2f}%"


def describe_market(market_data: MarketData) -> None:
    """Print the normalized reserve metrics."""
    print(f"protocol:            {market_data.protocol}")
    print(f"supply APY:          {percent(market_data.supply_apy)}")
    print(f"variable borrow APY: {percent(market_data.variable_borrow_apy)}")
    print(f"total supply:        {market_data.total_supply}")
    print(f"total borrows:       {market_data.total_borrows}")
    print(f"utilization:         {percent(market_data.utilization_rate)}")
    print(f"loan to value:       {percent(market_data.loan_to_value)}")
    print(f"liq. threshold:      {percent(market_data.liquidation_threshold)}")
    print(f"borrowing enabled:   {market_data.is_borrowing_enabled}")
    print(f"frozen:              {market_data.is_frozen}")


async def show_market_data(market: AaveV3MoneyMarket, usdc: ERC20Token) -> None:
    """Fetch and print the USDC reserve data."""
    print()
    print("=== USDC market data ===")
    describe_market(await market.get_market_data(usdc))


async def show_user_positions(market: AaveV3MoneyMarket, user_address: str) -> None:
    """Print the account summary and the open positions of an address."""
    print()
    print(f"=== Account {user_address} ===")
    account = await market.get_user_account_data(user_address)
    print(f"collateral (USD):    {account.total_collateral_value}")
    print(f"debt (USD):          {account.total_debt_value}")
    print(f"available to borrow: {account.available_borrow_value}")
    print(f"health factor:       {account.health_factor}")
    print(f"healthy:             {account.is_healthy}")
    print(f"risk level:          {account.liquidation_risk_level}")
    print(f"clears the buffer:   {await market.is_position_safe(user_address)}")

    lending = await market.get_lending_positions(user_address)
    borrowing = await market.get_borrowing_positions(user_address)
    print(f"lending positions:   {len(lending)}")
    for position in lending:
        print(
            f"  - {position.asset.data.symbol}: {position.total_balance} "
            f"(collateral: {position.is_collateral})"
        )
    print(f"borrowing positions: {len(borrowing)}")
    for position in borrowing:
        print(
            f"  - {position.asset.data.symbol}: {position.total_debt} "
            f"({position.interest_rate_mode.value})"
        )


async def build_transactions(
    market: AaveV3MoneyMarket,
    usdc: ERC20Token,
    weth: ERC20Token,
    user_address: str,
) -> None:
    """Build (never send) one supply and one borrow transaction."""
    print()
    print("=== Building transactions (unsigned) ===")

    supply = await market.supply(
        asset=usdc,
        amount=SUPPLY_AMOUNT,
        user_address=user_address,
        enable_as_collateral=True,
    )
    supply_params = unsigned_tx_params(supply)
    print(f"supply {SUPPLY_AMOUNT} USDC -> pool {supply_params['to']}")
    print(f"  calldata: {str(supply_params['data'])[:10]}...")

    borrow = await market.borrow(
        asset=weth,
        amount=BORROW_AMOUNT,
        user_address=user_address,
        interest_rate_mode=InterestRateMode.VARIABLE,
    )
    borrow_params = unsigned_tx_params(borrow)
    print(f"borrow {BORROW_AMOUNT} WETH -> pool {borrow_params['to']}")
    print(f"  calldata: {str(borrow_params['data'])[:10]}...")
    print()
    print("Both stay in PENDING_BROADCAST: sign and send them explicitly with")
    print("wallet.sign_and_send_transaction(...) once the Pool is approved.")


async def main() -> None:
    """Configure Aave V3, read a reserve and an account, then build calls."""
    load_environment()
    configure_blockchains()
    blockchain = BlockchainFactory.get_evm_blockchain_by_identifier("ethereum")

    wallet = build_wallet(blockchain)
    user_address = wallet.address.string if wallet is not None else DEFAULT_USER_ADDRESS

    configuration = AaveV3Configuration(platform=blockchain.platform)
    market = AaveV3MoneyMarket(configuration, wallet=wallet)

    print("=== Configuration ===")
    print(f"protocols: {market.supported_protocols}")
    for protocol in configuration.protocols:
        print(f"- {protocol.protocol_name}")
        print(f"    pool:          {protocol.lending_pool_address}")
        print(f"    data provider: {protocol.data_provider_address}")
    print(f"default rate mode: {configuration.default_interest_rate_mode.value}")
    print(f"safety buffer:     {percent(configuration.liquidation_threshold_buffer)}")

    usdc = build_token(blockchain, USDC_ADDRESS)
    weth = build_token(blockchain, WETH_ADDRESS)

    try:
        await show_market_data(market, usdc)
        await show_user_positions(market, user_address)
        if wallet is None:
            print()
            print(f"Set {PRIVATE_KEY_ENV} to also build supply/borrow transactions.")
            return
        await build_transactions(market, usdc, weth, user_address)
    except Exception as error:
        print(f"Network step failed: {type(error).__name__}: {error}")
        print(f"Point {ETHEREUM_RPC_URLS_ENV} at a working Ethereum RPC endpoint.")


if __name__ == "__main__":
    asyncio.run(main())
