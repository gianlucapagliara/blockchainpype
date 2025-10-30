"""Demonstrate how to bootstrap a smart contract with dynamic ABI loading.

This example walks through the lifecycle of creating an ``EthereumSmartContract``
instance, loading its ABI (preferably from Etherscan), and running a couple of
read-only calls once the contract is initialized. It showcases both a direct
implementation contract (WETH) and a proxied deployment (USDC), exercising the
proxy-aware ABI lookup logic.

The script tries the following ABI sources, in order:

1. ``EthereumEtherscanABI`` if the configured blockchain exposes an
   ``EtherscanExplorer`` (set ``ETHERSCAN_API_KEY`` in your environment to avoid
   rate limits).
2. ``EthereumLocalFileABI`` as a generic ERC-20 ABI fallback.

The target contract is the canonical WETH deployment on Ethereum mainnet. Any
ERC-20 contract address can be swapped in.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from importlib import import_module
from importlib.util import find_spec

from blockchainpype.evm.blockchain.identifier import EthereumAddress
from blockchainpype.evm.dapp.abi import EthereumEtherscanABI, EthereumLocalFileABI
from blockchainpype.evm.dapp.contract import (
    EthereumContractConfiguration,
    EthereumSmartContract,
)
from blockchainpype.factory import BlockchainFactory
from blockchainpype.initializer import BlockchainsInitializer

load_dotenv = None
dotenv_spec = find_spec("dotenv")
if dotenv_spec is not None:  # pragma: no branch
    dotenv_module = import_module("dotenv")
    load_dotenv = getattr(dotenv_module, "load_dotenv", None)


if load_dotenv is not None:  # pragma: no branch - side-effect only
    load_dotenv(override=True)


class ReadOnlyERC20(EthereumSmartContract):
    """Minimal ERC-20 wrapper showcasing contract initialization."""

    async def get_symbol(self) -> str:
        return await self.functions.symbol().call()

    async def get_decimals(self) -> int:
        return await self.functions.decimals().call()

    async def get_total_supply(self) -> int:
        return await self.functions.totalSupply().call()


async def initialize_with_fallbacks(
    configurations: Iterable[tuple[str, EthereumContractConfiguration]],
) -> ReadOnlyERC20:
    """Attempt contract initialization with multiple ABI sources."""

    last_error: Exception | None = None
    for label, configuration in configurations:
        contract = ReadOnlyERC20(configuration=configuration)
        try:
            await contract.initialize()
        except Exception as exc:  # pragma: no cover - exercised in example run
            print(f"❌ Initialization via {label} failed: {exc}")
            last_error = exc
            continue

        print(f"✅ Contract initialized using {label} ABI source")
        return contract

    if last_error is not None:
        raise RuntimeError("All ABI sources failed") from last_error

    raise RuntimeError("No ABI sources provided")


async def main() -> None:
    print("=== Contract Initialization Example ===")

    BlockchainFactory.reset()
    BlockchainsInitializer.configure()

    blockchain = BlockchainFactory.get_evm_blockchain_by_identifier("ethereum")
    explorer = blockchain.explorer
    platform = blockchain.platform

    targets = [
        (
            "WETH",
            EthereumAddress.from_string("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"),
        ),
        (
            "USDC (proxy)",
            EthereumAddress.from_string("0xA0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"),
        ),
    ]

    for label, address in targets:
        print(f"\n--- Initializing {label} ---")

        configurations: list[tuple[str, EthereumContractConfiguration]] = []

        if explorer is not None:
            configurations.append(
                (
                    "Etherscan",
                    EthereumContractConfiguration(
                        platform=platform,
                        address=address,
                        abi_configuration=EthereumEtherscanABI(
                            explorer=explorer,
                            contract_address=address,
                            request_timeout_seconds=15,
                        ),
                    ),
                )
            )

        configurations.append(
            (
                "Local ERC20 ABI",
                EthereumContractConfiguration(
                    platform=platform,
                    address=address,
                    abi_configuration=EthereumLocalFileABI(
                        file_name="ERC20Mock.json",
                    ),
                ),
            )
        )

        contract = await initialize_with_fallbacks(configurations)

        print(f"Symbol: {await contract.get_symbol()}")
        print(f"Decimals: {await contract.get_decimals()}")
        print(f"Total supply (raw units): {await contract.get_total_supply()}")


if __name__ == "__main__":
    asyncio.run(main())
