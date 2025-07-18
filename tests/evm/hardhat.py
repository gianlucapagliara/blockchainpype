"""
Hardhat testing utilities for blockchain development testing.

This module provides utilities for:
- Managing Hardhat local blockchain nodes
- Deploying and interacting with test contracts
- Setting up test environments
- Managing test wallets and accounts
"""

import asyncio
import json
import os
import re
import subprocess
import time
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import cast

import pytest
import pytest_asyncio
from web3 import Web3

from blockchainpype.evm.blockchain.blockchain import EthereumBlockchain
from blockchainpype.factory import BlockchainFactory
from blockchainpype.initializer import BlockchainsInitializer


class HardhatNode:
    """Manages a Hardhat local blockchain node."""

    def __init__(self, hardhat_dir: str, port: int = 8545):
        self.hardhat_dir = Path(hardhat_dir)
        self.port = port
        self.process: subprocess.Popen | None = None
        self.deployments: dict[str, str] = {}

    async def start(self, timeout: int = 30) -> None:
        """Start the Hardhat node."""
        if self.process is not None:
            raise RuntimeError("Hardhat node is already running")

        # Check Node.js version compatibility
        await self._check_nodejs_version()

        # Check if port is available
        if await self._is_port_in_use(self.port):
            raise RuntimeError(
                f"Port {self.port} is already in use. Please stop the existing process or use a different port."
            )

        # Check if hardhat directory exists
        if not self.hardhat_dir.exists():
            raise RuntimeError(f"Hardhat directory does not exist: {self.hardhat_dir}")

        # Check if node_modules exists
        if not (self.hardhat_dir / "node_modules").exists():
            raise RuntimeError(
                f"Node modules not found. Please run 'npm install' in {self.hardhat_dir}"
            )

        print(f"🚀 Starting Hardhat node on port {self.port}...")
        print(f"📁 Working directory: {self.hardhat_dir}")

        # Start Hardhat node
        self.process = subprocess.Popen(
            ["npx", "hardhat", "node", "--port", str(self.port)],
            cwd=self.hardhat_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # Wait for node to be ready
        await self._wait_for_node_ready(timeout)

    async def _check_nodejs_version(self) -> None:
        """Check if Node.js version is compatible with Hardhat."""
        try:
            result = subprocess.run(
                ["node", "--version"], capture_output=True, text=True, timeout=5
            )
            if result.returncode != 0:
                raise RuntimeError("Node.js is not installed or not accessible")

            version_str = result.stdout.strip()
            # Extract major version number
            version_match = re.match(r"v(\d+)\.", version_str)
            if not version_match:
                raise RuntimeError(f"Could not parse Node.js version: {version_str}")

            major_version = int(version_match.group(1))

            # Check if version is supported (18.x or 20.x are recommended)
            if major_version < 18:
                raise RuntimeError(
                    f"Node.js version {version_str} is too old. Please use Node.js 18.x or 20.x"
                )
            elif major_version > 20:
                print(
                    f"⚠️  Warning: Node.js version {version_str} may not be fully supported by Hardhat."
                )
                print("   Recommended versions: 18.x or 20.x")
                print(
                    "   If you encounter issues, consider using a Node.js version manager like nvm."
                )

        except subprocess.TimeoutExpired:
            raise RuntimeError("Node.js check timed out")
        except Exception as e:
            raise RuntimeError(f"Failed to check Node.js version: {e}")

    async def _is_port_in_use(self, port: int) -> bool:
        """Check if a port is already in use."""
        try:
            result = subprocess.run(
                ["lsof", "-i", f":{port}"], capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            # If lsof is not available, try a different approach
            try:
                result = subprocess.run(
                    ["netstat", "-an"], capture_output=True, text=True, timeout=5
                )
                return f":{port}" in result.stdout
            except:
                # If both methods fail, assume port is available
                return False

    async def _wait_for_node_ready(self, timeout: int) -> None:
        """Wait for the Hardhat node to be ready."""
        start_time = time.time()
        last_error = None

        while time.time() - start_time < timeout:
            try:
                # Check if process is still running
                if self.process and self.process.poll() is not None:
                    # Process has terminated, get the error
                    stdout, stderr = self.process.communicate()
                    raise RuntimeError(
                        f"Hardhat node process terminated unexpectedly.\nSTDOUT: {stdout}\nSTDERR: {stderr}"
                    )

                # Try to connect to the node using a simple HTTP request
                result = await asyncio.create_subprocess_exec(
                    "curl",
                    "-s",
                    "-f",
                    "-X",
                    "POST",
                    "-H",
                    "Content-Type: application/json",
                    "--data",
                    '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}',
                    f"http://127.0.0.1:{self.port}",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await result.communicate()

                if result.returncode == 0:
                    # Check if we got a valid JSON-RPC response
                    response_text = stdout.decode()
                    if "result" in response_text or "error" in response_text:
                        print(f"✅ Hardhat node is ready on port {self.port}")
                        return

            except Exception as e:
                last_error = e

            await asyncio.sleep(1)

        # If we get here, the node failed to start
        error_msg = f"Hardhat node failed to start within {timeout} seconds"
        if last_error:
            error_msg += f"\nLast error: {last_error}"

        # Try to get process output for debugging
        if self.process:
            try:
                stdout, stderr = self.process.communicate(timeout=1)
                if stderr:
                    error_msg += f"\nProcess stderr: {stderr}"
                if stdout:
                    error_msg += f"\nProcess stdout: {stdout}"
            except:
                pass

        raise RuntimeError(error_msg)

    async def stop(self) -> None:
        """Stop the Hardhat node."""
        if self.process is not None:
            self.process.terminate()
            try:
                await asyncio.wait_for(
                    asyncio.create_task(asyncio.to_thread(self.process.wait)),
                    timeout=10,
                )
            except TimeoutError:
                self.process.kill()
                await asyncio.to_thread(self.process.wait)
            self.process = None
            print("🛑 Hardhat node stopped")

    async def deploy_contracts(
        self, script_name: str = "deploy-all.js"
    ) -> dict[str, str]:
        """Deploy contracts and return their addresses."""
        if self.process is None:
            raise RuntimeError("Hardhat node is not running")

        print(f"📦 Deploying contracts using {script_name}...")

        result = subprocess.run(
            [
                "npx",
                "hardhat",
                "run",
                f"scripts/{script_name}",
                "--network",
                "localhost",
            ],
            cwd=self.hardhat_dir,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            raise RuntimeError(f"Contract deployment failed: {result.stderr}")

        # Load deployments from file
        deployments_file = self.hardhat_dir / "deployments.json"
        if deployments_file.exists():
            with open(deployments_file) as f:
                self.deployments = json.load(f)

        print(f"✅ Contracts deployed: {list(self.deployments.keys())}")
        return self.deployments

    async def reset_node(self) -> None:
        """Reset the Hardhat node to initial state."""
        if self.process is None:
            raise RuntimeError("Hardhat node is not running")

        # Reset using Hardhat network helpers
        result = subprocess.run(
            ["npx", "hardhat", "run", "--network", "localhost", "-"],
            input="const { network } = require('hardhat'); network.provider.send('hardhat_reset', []);",
            cwd=self.hardhat_dir,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            print(f"⚠️  Reset failed: {result.stderr}")
        else:
            print("🔄 Node reset successfully")

    def get_contract_address(self, contract_name: str) -> str | None:
        """Get deployed contract address by name."""
        return self.deployments.get(contract_name)


class HardhatTestEnvironment:
    """Complete test environment for Hardhat development."""

    def __init__(self, hardhat_dir: str):
        self.hardhat_dir = hardhat_dir
        self.node = HardhatNode(hardhat_dir)
        self.blockchain: EthereumBlockchain | None = None
        self.test_accounts: list[str] = []

    async def setup(self) -> None:
        """Setup the complete test environment."""
        # Start Hardhat node
        await self.node.start()

        # Deploy contracts
        await self.node.deploy_contracts()

        # Initialize blockchain connection
        BlockchainsInitializer.configure()
        self.blockchain = cast(
            EthereumBlockchain, BlockchainFactory.get_by_identifier("hardhat")
        )

        # Get test accounts
        self.test_accounts = await self._get_test_accounts()

        print(f"🚀 Test environment ready with {len(self.test_accounts)} test accounts")

    async def teardown(self) -> None:
        """Teardown the test environment."""
        await self.node.stop()

    async def _get_test_accounts(self) -> list[str]:
        """Get available test accounts from the node."""
        if self.blockchain is None:
            return []

        try:
            # Get accounts from the blockchain
            accounts = await self.blockchain.web3.eth.accounts
            return [str(account) for account in accounts]
        except Exception as e:
            print(f"⚠️  Failed to get test accounts: {e}")
            return []

    async def get_account_balance(self, account: str) -> float:
        """Get ETH balance of an account."""
        if self.blockchain is None:
            return 0.0

        try:
            checksum_address = Web3.to_checksum_address(account)
            balance_wei = await self.blockchain.web3.eth.get_balance(checksum_address)
            return float(Web3.from_wei(balance_wei, "ether"))
        except Exception as e:
            print(f"⚠️  Failed to get balance for {account}: {e}")
            return 0.0

    async def send_eth(
        self, from_account: str, to_account: str, amount_eth: float
    ) -> str:
        """Send ETH from one account to another."""
        if self.blockchain is None:
            raise RuntimeError("Blockchain not initialized")

        try:
            from_address = Web3.to_checksum_address(from_account)
            to_address = Web3.to_checksum_address(to_account)
            tx_hash = await self.blockchain.web3.eth.send_transaction(
                {
                    "from": from_address,
                    "to": to_address,
                    "value": Web3.to_wei(amount_eth, "ether"),
                    "gas": 21000,
                }
            )
            # Ensure the transaction hash has the proper 0x prefix
            hash_hex = tx_hash.hex()
            if not hash_hex.startswith("0x"):
                hash_hex = "0x" + hash_hex
            return hash_hex
        except Exception as e:
            raise RuntimeError(f"Failed to send ETH: {e}")

    async def mine_blocks(self, count: int = 1) -> None:
        """Mine blocks on the local network."""
        if self.blockchain is None:
            raise RuntimeError("Blockchain not initialized")

        for _ in range(count):
            # Use proper Web3 method for mining blocks
            await self.blockchain.web3.provider.make_request("evm_mine", [])

    async def set_next_block_timestamp(self, timestamp: int) -> None:
        """Set the timestamp for the next block."""
        if self.blockchain is None:
            raise RuntimeError("Blockchain not initialized")

        await self.blockchain.web3.provider.make_request(
            "evm_setNextBlockTimestamp", [timestamp]
        )

    async def snapshot(self) -> str:
        """Take a snapshot of the current blockchain state."""
        if self.blockchain is None:
            raise RuntimeError("Blockchain not initialized")

        result = await self.blockchain.web3.provider.make_request("evm_snapshot", [])
        # Extract the actual snapshot ID from the response
        if isinstance(result, dict) and "result" in result:
            return result["result"]
        return str(result)

    async def revert_to_snapshot(self, snapshot_id: str) -> None:
        """Revert blockchain state to a snapshot."""
        if self.blockchain is None:
            raise RuntimeError("Blockchain not initialized")

        await self.blockchain.web3.provider.make_request("evm_revert", [snapshot_id])


# Pytest fixtures
@pytest.fixture(scope="session")
def hardhat_dir():
    """Path to the Hardhat directory."""
    return os.path.join(os.path.dirname(__file__), "..", "common", "hardhat")


@pytest.fixture(scope="session")
def event_loop():
    """Event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def hardhat_env(hardhat_dir) -> AsyncGenerator[HardhatTestEnvironment, None]:
    """Complete Hardhat test environment."""
    env = HardhatTestEnvironment(hardhat_dir)
    await env.setup()
    yield env
    await env.teardown()


@pytest_asyncio.fixture(scope="function")
async def blockchain(hardhat_env) -> EthereumBlockchain:
    """Get the blockchain instance."""
    return hardhat_env.blockchain


@pytest_asyncio.fixture(scope="function")
async def test_accounts(hardhat_env) -> list[str]:
    """Get test accounts."""
    return hardhat_env.test_accounts


@pytest_asyncio.fixture(scope="function")
async def deployed_contracts(hardhat_env) -> dict[str, str]:
    """Get deployed contract addresses."""
    return hardhat_env.node.deployments


@pytest_asyncio.fixture(scope="function")
async def blockchain_snapshot(hardhat_env) -> AsyncGenerator[str, None]:
    """Take a snapshot before test and revert after."""
    snapshot_id = await hardhat_env.snapshot()
    yield snapshot_id
    await hardhat_env.revert_to_snapshot(snapshot_id)


# Utility functions
def get_hardhat_accounts() -> list[str]:
    """Get the default Hardhat accounts (deterministic)."""
    return [
        "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
        "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
        "0x90F79bf6EB2c4f870365E785982E1f101E93b906",
        "0x15d34AAf54267DB7D7c367839AAf71A00a2C6A65",
        "0x9965507D1a55bcC2695C58ba16FB37d819B0A4dc",
        "0x976EA74026E726554dB657fA54763abd0C3a0aa9",
        "0x14dC79964da2C08b23698B3D3cc7Ca32193d9955",
        "0x23618e81E3f5cdF7f54C3d65f7FBc0aBf5B21E8f",
        "0xa0Ee7A142d267C1f36714E4a8F75612F20a79720",
    ]


def get_hardhat_private_keys() -> list[str]:
    """Get the default Hardhat private keys (deterministic)."""
    return [
        "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
        "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d",
        "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a",
        "0x7c852118294e51e653712a81e05800f419141751be58f605c371e15141b007a6",
        "0x47e179ec197488593b187f80a00eb0da91f1b9d0b13f8733639f19c30a34926a",
        "0x8b3a350cf5c34c9194ca85829a2df0ec3153be0318b5e2d3348e872092edffba",
        "0x92db14e403b83dfe3df233f83dfa3a0d7096f21ca9b0d6d6b8d88b2b4ec1564e",
        "0x4bbbf85ce3377467afe5d46f804f221813b2bb87f24d81f60f1fcdbf7cbf4356",
        "0xdbda1821b80551c9d65939329250298aa3472ba22feea921c0cf5d620ea67b97",
        "0x2a871d0798f97d79848a013d4936a73bf4cc922c825d33c1cf7073dff6d409c6",
    ]
