"""Bootstrap a smart contract and choose where its ABI comes from.

An :class:`~blockchainpype.evm.dapp.contract.EthereumSmartContract` is built
from an :class:`~blockchainpype.evm.dapp.contract.EthereumContractConfiguration`
carrying three things: the ``platform`` it lives on, the deployed ``address``,
and an ``abi_configuration``. ``await contract.initialize()`` resolves the ABI
and creates the underlying web3 ``AsyncContract``; ``contract.functions`` is
available afterwards.

Two ABI sources ship with the library:

* :class:`~blockchainpype.evm.dapp.abi.EthereumLocalFileABI` reads a JSON file
  from the bundled ``common/abi`` directory (plain ABI arrays and Hardhat
  artifacts are both accepted). It needs no network and no API key, which makes
  it the right default for standard interfaces such as ERC-20.
* :class:`~blockchainpype.evm.dapp.abi.EthereumEtherscanABI` downloads the
  verified ABI through the blockchain's explorer. It follows proxies: for a
  proxy deployment such as USDC, the *implementation* ABI is returned, so the
  real token functions show up instead of the proxy's fallback. Set
  ``ETHERSCAN_API_KEY`` to avoid the anonymous rate limit.

There is also :class:`~blockchainpype.evm.dapp.abi.EthereumDictABI` for an ABI
you already hold in memory, and
:class:`~blockchainpype.evm.dapp.erc20.ERC20Contract`, which defaults its ABI to
``ERC20.json`` and adds decimal-aware helpers on top of the raw calls.

Importing this module is side-effect free. ``main()`` only needs an RPC for the
on-chain reads (step 2) and ``ETHERSCAN_API_KEY`` for step 3; step 1 is offline.

Run it with::

    uv run python -m examples.contract_initialization_example
"""

from __future__ import annotations

import asyncio
import os
from typing import cast

from blockchainpype.evm.blockchain.blockchain import EthereumBlockchain
from blockchainpype.evm.blockchain.identifier import EthereumAddress
from blockchainpype.evm.dapp.abi import EthereumEtherscanABI, EthereumLocalFileABI
from blockchainpype.evm.dapp.contract import (
    EthereumContractConfiguration,
    EthereumSmartContract,
)
from blockchainpype.factory import BlockchainFactory
from examples.basic.configure import (
    ETHEREUM_RPC_URLS_ENV,
    configure_blockchains,
    load_environment,
)

WETH_ADDRESS = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"  # direct deployment
USDC_ADDRESS = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"  # proxy deployment

ETHERSCAN_API_KEY_ENV = "ETHERSCAN_API_KEY"

#: Bundled with the library; see ``common/abi`` for the full catalogue.
ERC20_ABI_FILE = "ERC20.json"


class ReadOnlyERC20(EthereumSmartContract):
    """Minimal ERC-20 wrapper showing how to expose contract calls."""

    async def get_symbol(self) -> str:
        return cast(str, await self.functions.symbol().call())

    async def get_decimals(self) -> int:
        return cast(int, await self.functions.decimals().call())

    async def get_raw_total_supply(self) -> int:
        return cast(int, await self.functions.totalSupply().call())


def build_local_abi_contract(
    blockchain: EthereumBlockchain, address: str
) -> ReadOnlyERC20:
    """Build a contract whose ABI is read from ``common/abi/ERC20.json``."""
    return ReadOnlyERC20(
        EthereumContractConfiguration(
            platform=blockchain.platform,
            address=EthereumAddress.from_string(address),
            abi_configuration=EthereumLocalFileABI(file_name=ERC20_ABI_FILE),
        )
    )


def build_etherscan_abi_contract(
    blockchain: EthereumBlockchain, address: str
) -> ReadOnlyERC20:
    """Build a contract whose ABI is downloaded from Etherscan.

    Raises:
        ValueError: If the blockchain was configured without an explorer.
    """
    explorer = blockchain.explorer
    if explorer is None:
        raise ValueError(
            "This blockchain has no explorer configured; "
            "an EtherscanConfiguration is required for EthereumEtherscanABI"
        )

    contract_address = EthereumAddress.from_string(address)
    return ReadOnlyERC20(
        EthereumContractConfiguration(
            platform=blockchain.platform,
            address=contract_address,
            abi_configuration=EthereumEtherscanABI(
                explorer=explorer,
                contract_address=contract_address,
                request_timeout_seconds=15,
            ),
        )
    )


def function_names(contract: EthereumSmartContract) -> list[str]:
    """List the callable function names of an initialized contract."""
    web3_contract = contract.contract
    if web3_contract is None:
        return []
    return sorted(function.fn_name for function in web3_contract.all_functions())


async def step_local_abi(blockchain: EthereumBlockchain) -> ReadOnlyERC20:
    """Initialize WETH from the bundled ERC-20 ABI. No network involved."""
    print("=== 1. Local file ABI (offline) ===")
    contract = build_local_abi_contract(blockchain, WETH_ADDRESS)
    print(f"initialized before: {contract.is_initialized}")

    await contract.initialize()

    print(f"initialized after:  {contract.is_initialized}")
    print(f"address:            {contract.address.string}")
    print(
        f"abi source:         {type(contract.configuration.abi_configuration).__name__}"
    )
    print(f"functions:          {', '.join(function_names(contract))}")
    return contract


async def step_on_chain_reads(contract: ReadOnlyERC20) -> None:
    """Call the initialized contract. This is the first step needing an RPC."""
    print()
    print("=== 2. On-chain reads (needs an Ethereum RPC) ===")
    symbol = await contract.get_symbol()
    decimals = await contract.get_decimals()
    raw_total_supply = await contract.get_raw_total_supply()
    print(f"symbol:       {symbol}")
    print(f"decimals:     {decimals}")
    print(f"total supply: {raw_total_supply / 10**decimals} {symbol}")


async def step_etherscan_abi(blockchain: EthereumBlockchain) -> None:
    """Initialize the USDC proxy from its verified (implementation) ABI."""
    print()
    print("=== 3. Etherscan ABI (needs ETHERSCAN_API_KEY) ===")
    if not os.getenv(ETHERSCAN_API_KEY_ENV):
        print(
            f"{ETHERSCAN_API_KEY_ENV} is not set: skipping. Anonymous requests "
            "are rate limited and usually fail."
        )
        return

    contract = build_etherscan_abi_contract(blockchain, USDC_ADDRESS)
    await contract.initialize()
    names = function_names(contract)
    print(f"address:   {contract.address.string} (proxy)")
    print(f"functions: {len(names)} resolved from the implementation ABI")
    print(f"symbol:    {await contract.get_symbol()}")


async def main() -> None:
    """Show both ABI sources, from the offline one to the remote one."""
    load_environment()
    configure_blockchains()
    blockchain = BlockchainFactory.get_evm_blockchain_by_identifier("ethereum")

    weth = await step_local_abi(blockchain)

    try:
        await step_on_chain_reads(weth)
        await step_etherscan_abi(blockchain)
    except Exception as error:
        print(f"Network step failed: {type(error).__name__}: {error}")
        print(f"Point {ETHEREUM_RPC_URLS_ENV} at a working Ethereum RPC endpoint.")


if __name__ == "__main__":
    asyncio.run(main())
