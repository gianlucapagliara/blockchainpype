"""
Hardhat testing utilities for blockchain development testing.

This module provides utilities for:
- Managing Hardhat local blockchain nodes
- Deploying and interacting with test contracts
- Setting up test environments
- Managing test wallets and accounts

The Hardhat node is started on a dynamically allocated free port, so multiple
test sessions can run concurrently without clashing on the default 8545 port.
The blockchain configuration registered for the ``hardhat`` platform is built
with the chosen port (see :class:`HardhatTestEnvironment`).

All async fixtures run in the session-scoped event loop
(``loop_scope="session"``); hardhat test modules must therefore mark their
tests with ``pytest.mark.asyncio(loop_scope="session")``.
"""

import asyncio
import json
import os
import re
import signal
import socket
import subprocess
import tempfile
import time
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import cast

import pytest
import pytest_asyncio
from financepype.platforms.blockchain import BlockchainPlatform
from web3 import AsyncHTTPProvider, Web3

from blockchainpype.evm.blockchain.blockchain import (
    EthereumBlockchain,
    EthereumBlockchainType,
)
from blockchainpype.evm.blockchain.configuration import (
    EthereumBlockchainConfiguration,
    EthereumConnectivityConfiguration,
    EthereumNativeAssetConfiguration,
)
from blockchainpype.factory import BlockchainFactory
from blockchainpype.initializer import (
    HARDHAT_CHAIN_ID,
    BlockchainConfigurations,
    BlockchainsInitializer,
)


def find_free_port() -> int:
    """Ask the OS for a free TCP port on the loopback interface."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _unregister_hardhat_platform() -> None:
    """Remove any existing 'hardhat' registration from the BlockchainFactory.

    OperatorFactory has no public unregister API, so this touches the private
    registries directly. It is only used by the test environment to make sure
    the 'hardhat' configuration points at the node started for this session
    (whose port is dynamic) rather than at a stale default (port 8545).
    """
    for registry_name in ("_configurations", "_platform_class_mapping"):
        registry = getattr(BlockchainFactory, registry_name)
        for platform in [p for p in registry if p.identifier == "hardhat"]:
            del registry[platform]
    cache = BlockchainFactory._cache
    for key in [k for k in cache if k[1].identifier == "hardhat"]:
        del cache[key]


class HardhatNode:
    """Manages a Hardhat local blockchain node."""

    def __init__(self, hardhat_dir: str, port: int | None = None):
        self.hardhat_dir = Path(hardhat_dir)
        self.port = port
        self.process: subprocess.Popen | None = None
        self.deployments: dict[str, str] = {}

    async def start(self, timeout: int = 60) -> None:
        """Start the Hardhat node.

        When no explicit port was requested, a free port is allocated
        dynamically so concurrent test sessions never clash.
        """
        if self.process is not None:
            raise RuntimeError("Hardhat node is already running")

        # Check Node.js version compatibility
        await self._check_nodejs_version()

        if self.port is None:
            self.port = find_free_port()
        elif await self._is_port_in_use(self.port):
            raise RuntimeError(
                f"Port {self.port} is already in use. Please stop the existing "
                "process or use a different port."
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

        # Start the Hardhat node in its own process group (start_new_session)
        # so teardown can kill the whole tree: `npx` spawns the actual node
        # process as a child, and terminating only the npx wrapper would leak
        # the node itself.
        self.process = subprocess.Popen(
            ["npx", "hardhat", "node", "--port", str(self.port)],
            cwd=self.hardhat_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
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

            # Hardhat supports LTS releases; anything below 18 is unusable.
            if major_version < 18:
                raise RuntimeError(
                    f"Node.js version {version_str} is too old. "
                    "Please use Node.js 18.x or newer (LTS recommended)"
                )
            elif major_version > 22:
                print(
                    f"⚠️  Warning: Node.js version {version_str} may not be fully supported by Hardhat."
                )
                print("   Recommended versions: 18.x, 20.x, or 22.x")

        except subprocess.TimeoutExpired as e:
            raise RuntimeError("Node.js check timed out") from e
        except Exception as e:
            raise RuntimeError(f"Failed to check Node.js version: {e}") from e

    async def _is_port_in_use(self, port: int) -> bool:
        """Check if a port is already in use."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            return sock.connect_ex(("127.0.0.1", port)) == 0

    def _check_process_alive(self) -> None:
        """Check if the Hardhat process is still running, raise if terminated."""
        if self.process and self.process.poll() is not None:
            stdout, stderr = self.process.communicate()
            raise RuntimeError(
                f"Hardhat node process terminated unexpectedly.\nSTDOUT: {stdout}\nSTDERR: {stderr}"
            )

    async def _rpc_request(self, method: str, params: list | None = None) -> dict:
        """Issue a JSON-RPC request to the node via curl. Returns the response."""
        payload = json.dumps(
            {"jsonrpc": "2.0", "method": method, "params": params or [], "id": 1}
        )
        result = await asyncio.create_subprocess_exec(
            "curl",
            "-s",
            "-f",
            "-X",
            "POST",
            "-H",
            "Content-Type: application/json",
            "--data",
            payload,
            f"http://127.0.0.1:{self.port}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await result.communicate()
        if result.returncode != 0:
            raise RuntimeError(
                f"JSON-RPC request '{method}' failed (curl exit {result.returncode})"
            )
        return cast(dict, json.loads(stdout.decode()))

    async def _probe_node(self) -> bool:
        """Probe the node with a JSON-RPC request. Returns True if node is ready."""
        try:
            response = await self._rpc_request("eth_blockNumber")
        except Exception:
            return False
        return "result" in response or "error" in response

    def _build_timeout_error_msg(
        self, timeout: int, last_error: Exception | None
    ) -> str:
        """Build an error message for node startup timeout."""
        error_msg = f"Hardhat node failed to start within {timeout} seconds"
        if last_error:
            error_msg += f"\nLast error: {last_error}"
        if self.process:
            try:
                stdout, stderr = self.process.communicate(timeout=1)
                if stderr:
                    error_msg += f"\nProcess stderr: {stderr}"
                if stdout:
                    error_msg += f"\nProcess stdout: {stdout}"
            except Exception:
                pass
        return error_msg

    async def _wait_for_node_ready(self, timeout: int) -> None:
        """Wait for the Hardhat node to be ready."""
        start_time = time.time()
        last_error = None

        while time.time() - start_time < timeout:
            try:
                self._check_process_alive()
                if await self._probe_node():
                    print(f"✅ Hardhat node is ready on port {self.port}")
                    return
            except Exception as e:
                last_error = e

            await asyncio.sleep(1)

        raise RuntimeError(self._build_timeout_error_msg(timeout, last_error))

    async def stop(self) -> None:
        """Stop the Hardhat node and every process it spawned.

        The node was started in its own process group, so the whole group is
        signalled: terminating only the ``npx`` wrapper would leak the actual
        ``hardhat node`` child process (and its port).
        """
        if self.process is None:
            return

        def _signal_group(sig: int) -> None:
            assert self.process is not None
            try:
                os.killpg(os.getpgid(self.process.pid), sig)
            except (ProcessLookupError, PermissionError):
                pass

        _signal_group(signal.SIGTERM)
        try:
            await asyncio.wait_for(asyncio.to_thread(self.process.wait), timeout=10)
        except TimeoutError:
            _signal_group(signal.SIGKILL)
            await asyncio.to_thread(self.process.wait)
        finally:
            # One last sweep in case children detached from the group between
            # the SIGTERM and their own shutdown.
            _signal_group(signal.SIGKILL)
            self.process = None
        print("🛑 Hardhat node stopped")

    async def deploy_contracts(
        self, script_name: str = "deploy-all.js"
    ) -> dict[str, str]:
        """Deploy contracts and return their addresses."""
        if self.process is None:
            raise RuntimeError("Hardhat node is not running")

        print(f"📦 Deploying contracts using {script_name}...")

        # Write the deployment addresses to a session-private file so
        # concurrent test sessions do not race on the shared deployments.json.
        fd, deployments_path = tempfile.mkstemp(
            prefix="hardhat-deployments-", suffix=".json"
        )
        os.close(fd)
        env = {
            **os.environ,
            # hardhat.config.js points the 'localhost' network at this port.
            "HARDHAT_LOCALHOST_PORT": str(self.port),
            "DEPLOYMENTS_FILE": deployments_path,
        }

        try:
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
                env=env,
            )

            if result.returncode != 0:
                raise RuntimeError(
                    f"Contract deployment failed: {result.stderr}\n{result.stdout}"
                )

            with open(deployments_path) as f:
                self.deployments = json.load(f)
        finally:
            try:
                os.unlink(deployments_path)
            except OSError:
                pass

        print(f"✅ Contracts deployed: {list(self.deployments.keys())}")
        return self.deployments

    async def reset_node(self) -> None:
        """Reset the Hardhat node to its initial state via ``hardhat_reset``.

        Note: this wipes all state, including deployed contracts. Prefer
        snapshots (``evm_snapshot`` / ``evm_revert``) for per-test isolation.
        """
        if self.process is None:
            raise RuntimeError("Hardhat node is not running")

        response = await self._rpc_request("hardhat_reset", [])
        if response.get("result") is True:
            print("🔄 Node reset successfully")
        else:
            raise RuntimeError(f"Node reset failed: {response}")

    def get_contract_address(self, contract_name: str) -> str | None:
        """Get deployed contract address by name."""
        return self.deployments.get(contract_name)


class HardhatTestEnvironment:
    """Complete test environment for Hardhat development."""

    def __init__(self, hardhat_dir: str, port: int | None = None):
        self.hardhat_dir = hardhat_dir
        self.node = HardhatNode(hardhat_dir, port=port)
        self.blockchain: EthereumBlockchain | None = None
        self.test_accounts: list[str] = []

    async def setup(self) -> None:
        """Setup the complete test environment."""
        # Start Hardhat node (dynamic port unless one was requested)
        await self.node.start()

        # Deploy contracts
        await self.node.deploy_contracts()

        # Register a 'hardhat' blockchain configuration pointing at our node
        self._register_blockchain()
        self.blockchain = cast(
            EthereumBlockchain, BlockchainFactory.get_by_identifier("hardhat")
        )

        # Get test accounts
        self.test_accounts = await self._get_test_accounts()

        print(f"🚀 Test environment ready with {len(self.test_accounts)} test accounts")

    def _register_blockchain(self) -> None:
        """Register the 'hardhat' platform with this node's RPC endpoint.

        The library default (BlockchainConfigurations.hardhat_configuration)
        hardcodes port 8545; the node started here runs on a dynamic port, so
        a subclass overriding just that configuration is registered instead.
        BlockchainsInitializer.register_blockchain_configurations skips
        platforms that are already registered, so any stale 'hardhat' entry is
        dropped first.
        """
        port = self.node.port

        class _HardhatNodeConfigurations(BlockchainConfigurations):
            @classmethod
            def hardhat_configuration(cls) -> EthereumBlockchainConfiguration | None:
                return EthereumBlockchainConfiguration(
                    platform=BlockchainPlatform(
                        identifier="hardhat",
                        type=EthereumBlockchainType,
                        local=True,
                        testnet=True,
                        chain_id=HARDHAT_CHAIN_ID,
                    ),
                    native_asset=EthereumNativeAssetConfiguration(),
                    connectivity=EthereumConnectivityConfiguration(
                        rpc_provider=AsyncHTTPProvider(f"http://127.0.0.1:{port}/"),
                    ),
                    explorer=None,
                )

        _unregister_hardhat_platform()
        BlockchainsInitializer.configure(configurations=_HardhatNodeConfigurations)

    async def teardown(self) -> None:
        """Teardown the test environment."""
        if self.blockchain is not None:
            try:
                await self.blockchain.web3.provider.disconnect()
            except Exception:
                pass
            self.blockchain = None
        # Drop the 'hardhat' registration so a later environment (with a new
        # port) starts clean.
        _unregister_hardhat_platform()
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

    async def get_account_balance_wei(self, account: str) -> int:
        """Get the exact ETH balance of an account in wei."""
        if self.blockchain is None:
            raise RuntimeError("Blockchain not initialized")

        checksum_address = Web3.to_checksum_address(account)
        return int(await self.blockchain.web3.eth.get_balance(checksum_address))

    async def get_account_balance(self, account: str) -> float:
        """Get ETH balance of an account (approximate, in ether).

        Use :meth:`get_account_balance_wei` when exact comparisons are needed.
        """
        try:
            balance_wei = await self.get_account_balance_wei(account)
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
            raise RuntimeError(f"Failed to send ETH: {e}") from e

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
#
# The hardhat_env fixture is session-scoped AND runs in the session-scoped
# event loop (loop_scope="session"). pytest-asyncio 1.x runs tests in a
# function-scoped loop by default, which would re-create the session fixture
# per loop (spawning one node per test class); hardhat test modules must
# therefore set:
#
#     pytestmark = pytest.mark.asyncio(loop_scope="session")
#
# The function-scoped fixtures below also declare loop_scope="session" so all
# awaits (and web3's cached aiohttp session) stay on the same loop.


@pytest.fixture(scope="session")
def hardhat_dir():
    """Path to the Hardhat directory."""
    return os.path.join(os.path.dirname(__file__), "..", "..", "common", "hardhat")


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def hardhat_env(hardhat_dir) -> AsyncGenerator[HardhatTestEnvironment]:
    """Complete Hardhat test environment."""
    env = HardhatTestEnvironment(hardhat_dir)
    try:
        await env.setup()
        yield env
    finally:
        await env.teardown()


@pytest_asyncio.fixture(loop_scope="session")
async def blockchain(hardhat_env) -> EthereumBlockchain:
    """Get the blockchain instance."""
    return hardhat_env.blockchain


@pytest_asyncio.fixture(loop_scope="session")
async def test_accounts(hardhat_env) -> list[str]:
    """Get test accounts."""
    return hardhat_env.test_accounts


@pytest_asyncio.fixture(loop_scope="session")
async def deployed_contracts(hardhat_env) -> dict[str, str]:
    """Get deployed contract addresses."""
    return hardhat_env.node.deployments


@pytest_asyncio.fixture(loop_scope="session")
async def blockchain_snapshot(hardhat_env) -> AsyncGenerator[str]:
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
