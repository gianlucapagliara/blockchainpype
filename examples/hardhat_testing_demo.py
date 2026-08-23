"""Drive the local Hardhat environment used by the integration test-suite.

:class:`tests.evm.hardhat.HardhatTestEnvironment` starts a Hardhat node on a
free port, runs ``scripts/deploy-all.js`` (TestToken, TestToken2, a
TestUniswapV2Factory/Pair with seeded liquidity, a TestMultisig and the
UniswapV2Router02-compatible SimpleV2Router), and registers the resulting
``hardhat`` platform in the :class:`BlockchainFactory`. Everything the library
does against mainnet works against that node — this script proves it by running
an ETH transfer and a real token swap through
:class:`~blockchainpype.evm.dapp.uniswap.UniswapDEX`.

Prerequisites:

* Node.js and npm on the PATH.
* The Hardhat dependencies installed once::

      cd common/hardhat && npm install

The same environment powers ``tests/evm/test_hardhat.py``::

    uv run pytest tests/evm/test_hardhat.py -m "" --timeout=300

Importing this module is side-effect free: the node is only started by
``main()``, and always torn down again.

Run it with::

    uv run python -m examples.hardhat_testing_demo
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path

from pydantic import SecretStr
from web3 import Web3

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
from blockchainpype.evm.transaction import EthereumTransaction
from blockchainpype.evm.wallet.identifier import EthereumWalletIdentifier
from blockchainpype.evm.wallet.signer import EthereumSignerConfiguration
from blockchainpype.evm.wallet.wallet import (
    EthereumWallet,
    EthereumWalletConfiguration,
)
from tests.evm.hardhat import HardhatTestEnvironment, get_hardhat_private_keys

#: The Hardhat project shipped with the repository.
HARDHAT_DIR = Path(__file__).resolve().parent.parent / "common" / "hardhat"

TRANSFER_ETH = 5.0
SWAP_AMOUNT = Decimal("1")
MAX_SLIPPAGE = Decimal("0.005")  # 0.5%
DEADLINE_MINUTES = 10
RECEIPT_TIMEOUT_SECONDS = 60


def build_wallet(blockchain: EthereumBlockchain, account_index: int) -> EthereumWallet:
    """Build a signing wallet for one of Hardhat's deterministic accounts."""
    private_key = get_hardhat_private_keys()[account_index]
    account = Web3().eth.account.from_key(private_key)
    configuration = EthereumWalletConfiguration(
        identifier=EthereumWalletIdentifier(
            name=f"hardhat-{account_index}",
            platform=blockchain.platform,
            address=EthereumAddress.from_string(account.address),
        ),
        signer=EthereumSignerConfiguration(private_key=SecretStr(private_key)),
    )
    return EthereumWallet(configuration=configuration, blockchain=blockchain)


def build_token(blockchain: EthereumBlockchain, address: str) -> ERC20Token:
    """Build an ERC-20 handle for a token deployed on the local node."""
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


async def wait_for_receipt(
    blockchain: EthereumBlockchain, transaction: EthereumTransaction
) -> dict[str, object]:
    """Wait for a broadcast transaction to be mined and assert it succeeded.

    ``sign_and_send_transaction`` broadcasts in the background, so the receipt
    poll below is what actually waits for the node.

    Raises:
        RuntimeError: If the transaction is unsigned or reverted.
    """
    signed = transaction.signed_transaction
    if signed is None:
        raise RuntimeError(f"{transaction.client_operation_id} was not signed")

    receipt = await blockchain.web3.eth.wait_for_transaction_receipt(
        signed.hash, timeout=RECEIPT_TIMEOUT_SECONDS
    )
    if receipt["status"] != 1:
        raise RuntimeError(f"{transaction.client_operation_id} reverted")
    return dict(receipt)


async def show_environment(env: HardhatTestEnvironment) -> EthereumBlockchain:
    """Print what the environment brought up."""
    blockchain = env.blockchain
    if blockchain is None:
        raise RuntimeError("Environment setup did not register a blockchain")

    print("=== Environment ===")
    print(f"platform:     {blockchain.platform.identifier}")
    print(f"chain id:     {blockchain.platform.chain_id}")
    print(f"rpc port:     {env.node.port}")
    print(f"block number: {await blockchain.fetch_block_number()}")
    print(f"accounts:     {len(env.test_accounts)}")

    print()
    print("=== Deployed contracts ===")
    for name, address in env.node.deployments.items():
        print(f"- {name}: {address}")
    return blockchain


async def transfer_eth(env: HardhatTestEnvironment) -> None:
    """Move ETH between two unlocked node accounts and check the balances."""
    sender, receiver = env.test_accounts[0], env.test_accounts[1]

    before = await env.get_account_balance_wei(receiver)
    tx_hash = await env.send_eth(sender, receiver, TRANSFER_ETH)
    after = await env.get_account_balance_wei(receiver)

    print()
    print("=== ETH transfer ===")
    print(f"tx hash:  {tx_hash}")
    print(f"received: {Web3.from_wei(after - before, 'ether')} ETH")


async def swap_tokens(
    blockchain: EthereumBlockchain, deployments: dict[str, str]
) -> None:
    """Quote, approve and execute a TestToken -> TestToken2 swap."""
    wallet = build_wallet(blockchain, account_index=0)
    await wallet.sync_nonce()

    token_in = build_token(blockchain, deployments["TestToken"])
    token_out = build_token(blockchain, deployments["TestToken2"])
    await token_in.initialize_data()
    await token_out.initialize_data()

    router_address = deployments["SimpleV2Router"]
    uniswap = UniswapDEX(
        blockchain,
        UniswapConfiguration.local_network(
            platform=blockchain.platform,
            v2_factory_address=deployments["TestUniswapV2Factory"],
            v2_router_address=router_address,
            default_slippage=MAX_SLIPPAGE,
            default_deadline_minutes=DEADLINE_MINUTES,
        ),
        wallet=wallet,
    )

    print()
    print("=== Swap ===")
    print(f"wallet: {wallet.address.string}")

    route: SwapRoute = await uniswap.quote_swap(
        input_asset=token_in,
        output_asset=token_out,
        amount=SWAP_AMOUNT,
        mode=SwapMode.EXACT_INPUT,
    )
    print(f"quote:  {route.input_amount} -> {route.output_amount} via {route.protocol}")

    # The router pulls the input token with transferFrom, so it needs an
    # allowance first. place_approve signs and broadcasts straight away.
    approval = await token_in.contract.place_approve(
        wallet,
        spender=EthereumAddress.from_string(router_address),
        amount=SWAP_AMOUNT,
    )
    await wait_for_receipt(blockchain, approval)
    print(f"approved router for {SWAP_AMOUNT} tokens")

    balance_before = await token_out.contract.get_balance_of(wallet.address)

    # execute_swap only builds the transaction; sending it stays explicit.
    unsigned = await uniswap.execute_swap(route, deadline_minutes=DEADLINE_MINUTES)
    sent = wallet.sign_and_send_transaction(
        client_operation_id=unsigned.client_operation_id,
        tx_data=dict(unsigned_tx_params(unsigned)),
    )
    receipt = await wait_for_receipt(blockchain, sent)

    balance_after = await token_out.contract.get_balance_of(wallet.address)
    print(f"gas used: {receipt['gasUsed']}")
    print(f"received: {balance_after - balance_before} {token_out.data.symbol}")
    print(f"expected: {route.output_amount} (quoted)")


async def main() -> None:
    """Start the environment, exercise it, and always tear it down."""
    print(f"Starting Hardhat from {HARDHAT_DIR}")
    print("(run 'npm install' in common/hardhat first if this fails)")
    env = HardhatTestEnvironment(str(HARDHAT_DIR))

    await env.setup()
    try:
        blockchain = await show_environment(env)
        await transfer_eth(env)
        await swap_tokens(blockchain, env.node.deployments)
    finally:
        print()
        print("Tearing down the environment...")
        await env.teardown()


if __name__ == "__main__":
    asyncio.run(main())
