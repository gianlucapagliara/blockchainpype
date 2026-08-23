"""
Integration tests for the Hardhat testing framework.

This module shows how to:
- Use the hardhat testing fixtures
- Test basic blockchain operations
- Deploy and interact with contracts
- Use snapshots for test isolation
- Execute real token swaps through the SimpleV2Router

The whole module is marked ``integration`` (it needs a local Hardhat node and
npm dependencies), so it is skipped by the default pytest run. Run it with:

    uv run pytest tests/evm/test_hardhat.py -m "" --timeout=180

All async tests run in the session-scoped event loop so they share the
session-scoped ``hardhat_env`` fixture (and its single Hardhat node).
"""

import json
from pathlib import Path

import pytest
from web3 import Web3

from blockchainpype.evm.blockchain.identifier import EthereumAddress
from tests.evm.hardhat import get_hardhat_accounts, get_hardhat_private_keys

pytestmark = [pytest.mark.integration, pytest.mark.timeout(120)]

ONE_ETH_WEI = Web3.to_wei(1, "ether")


def load_artifact_abi(hardhat_dir: str, source_file: str, contract_name: str) -> list:
    """Load a contract ABI from the Hardhat build artifacts."""
    artifact_path = (
        Path(hardhat_dir)
        / "artifacts"
        / "contracts"
        / source_file
        / f"{contract_name}.json"
    )
    with open(artifact_path) as f:
        return json.load(f)["abi"]


class TestHardhatBasicOperations:
    """Test basic blockchain operations using Hardhat."""

    pytestmark = pytest.mark.asyncio(loop_scope="session")

    async def test_blockchain_connection(self, blockchain):
        """Test that we can connect to the blockchain."""
        block_number = await blockchain.fetch_block_number()
        assert block_number >= 0

    async def test_account_balances(self, hardhat_env, test_accounts):
        """Test that test accounts have expected balances."""
        assert len(test_accounts) > 0

        # First account should have plenty of ETH
        balance_wei = await hardhat_env.get_account_balance_wei(test_accounts[0])
        assert balance_wei > 1000 * ONE_ETH_WEI  # Should have more than 1000 ETH

    async def test_eth_transfer(self, hardhat_env, test_accounts):
        """Test ETH transfer between accounts."""
        sender = test_accounts[0]
        receiver = test_accounts[1]

        # Get initial balances (exact, in wei)
        initial_sender_balance = await hardhat_env.get_account_balance_wei(sender)
        initial_receiver_balance = await hardhat_env.get_account_balance_wei(receiver)

        # Send 1 ETH
        tx_hash = await hardhat_env.send_eth(sender, receiver, 1.0)
        assert tx_hash.startswith("0x")

        # Check final balances
        final_sender_balance = await hardhat_env.get_account_balance_wei(sender)
        final_receiver_balance = await hardhat_env.get_account_balance_wei(receiver)

        # Sender should have less (accounting for gas)
        assert final_sender_balance < initial_sender_balance - ONE_ETH_WEI
        # Receiver should have exactly 1 ETH more
        assert final_receiver_balance == initial_receiver_balance + ONE_ETH_WEI

    async def test_mining_blocks(self, hardhat_env, blockchain):
        """Test mining blocks manually."""
        initial_block = await blockchain.fetch_block_number()

        # Mine 5 blocks
        await hardhat_env.mine_blocks(5)

        final_block = await blockchain.fetch_block_number()
        assert final_block >= initial_block + 5

    async def test_snapshot_revert(self, hardhat_env, test_accounts):
        """Test blockchain snapshot and revert functionality."""
        sender = test_accounts[0]
        receiver = test_accounts[1]

        # Take snapshot
        snapshot_id = await hardhat_env.snapshot()

        # Get initial balance
        initial_balance = await hardhat_env.get_account_balance_wei(receiver)

        # Send some ETH
        await hardhat_env.send_eth(sender, receiver, 10.0)

        # Check balance changed
        new_balance = await hardhat_env.get_account_balance_wei(receiver)
        assert new_balance == initial_balance + 10 * ONE_ETH_WEI

        # Revert to snapshot
        await hardhat_env.revert_to_snapshot(snapshot_id)

        # Check balance is back to original
        reverted_balance = await hardhat_env.get_account_balance_wei(receiver)
        assert reverted_balance == initial_balance


class TestHardhatContracts:
    """Test contract deployment and interaction."""

    pytestmark = pytest.mark.asyncio(loop_scope="session")

    async def test_deployed_contracts(self, deployed_contracts):
        """Test that contracts are deployed correctly."""
        expected_contracts = [
            "TestToken",
            "TestToken2",
            "TestMultisig",
            "TestUniswapV2Factory",
            "TestUniswapV2Pair",
            "SimpleV2Router",
        ]

        for contract_name in expected_contracts:
            assert contract_name in deployed_contracts
            address = deployed_contracts[contract_name]
            assert Web3.is_address(address)

    async def test_token_contract_interaction(
        self, blockchain, deployed_contracts, test_accounts
    ):
        """Test interaction with ERC20 token contract."""
        token_address = deployed_contracts["TestToken"]

        # Check that token contract is deployed
        assert Web3.is_address(token_address)

        # Check that the token contract exists by checking if it has code
        code = await blockchain.web3.eth.get_code(
            Web3.to_checksum_address(token_address)
        )
        assert len(code) > 0  # Contract should have bytecode

    async def test_multisig_contract(self, blockchain, deployed_contracts):
        """Test multisig contract is properly configured."""
        multisig_address = deployed_contracts["TestMultisig"]

        # Check multisig has some ETH
        balance = await blockchain.fetch_native_asset_balance(
            EthereumAddress.from_string(multisig_address)
        )
        assert balance > 0

    async def test_uniswap_factory(self, blockchain, deployed_contracts):
        """Test Uniswap factory created a pair."""
        pair_address = deployed_contracts["TestUniswapV2Pair"]

        # Pair should exist
        assert Web3.is_address(pair_address)

        # Pair should have zero native (ETH) balance
        balance = await blockchain.fetch_native_asset_balance(
            EthereumAddress.from_string(pair_address)
        )
        assert balance == 0


class TestHardhatRouter:
    """Test the SimpleV2Router against the seeded TestToken/TestToken2 pair."""

    pytestmark = pytest.mark.asyncio(loop_scope="session")

    async def test_pair_has_liquidity(
        self, blockchain, deployed_contracts, hardhat_dir
    ):
        """The deploy script seeds reserves into the TestToken/TestToken2 pair."""
        pair = blockchain.web3.eth.contract(
            address=Web3.to_checksum_address(deployed_contracts["TestUniswapV2Pair"]),
            abi=load_artifact_abi(
                hardhat_dir, "TestUniswapV2.sol", "TestUniswapV2Pair"
            ),
        )
        reserve0, reserve1, _ = await pair.functions.getReserves().call()
        assert reserve0 > 0
        assert reserve1 > 0

    async def test_get_amounts_out_matches_constant_product(
        self, blockchain, deployed_contracts, hardhat_dir
    ):
        """Router quotes must match the 0.3%-fee constant product formula."""
        web3 = blockchain.web3
        token_a = Web3.to_checksum_address(deployed_contracts["TestToken"])
        token_b = Web3.to_checksum_address(deployed_contracts["TestToken2"])

        router = web3.eth.contract(
            address=Web3.to_checksum_address(deployed_contracts["SimpleV2Router"]),
            abi=load_artifact_abi(hardhat_dir, "SimpleV2Router.sol", "SimpleV2Router"),
        )
        pair = web3.eth.contract(
            address=Web3.to_checksum_address(deployed_contracts["TestUniswapV2Pair"]),
            abi=load_artifact_abi(
                hardhat_dir, "TestUniswapV2.sol", "TestUniswapV2Pair"
            ),
        )

        reserve0, reserve1, _ = await pair.functions.getReserves().call()
        token0 = await pair.functions.token0().call()
        reserve_in, reserve_out = (
            (reserve0, reserve1) if token_a == token0 else (reserve1, reserve0)
        )

        amount_in = ONE_ETH_WEI
        amounts = await router.functions.getAmountsOut(
            amount_in, [token_a, token_b]
        ).call()

        amount_in_with_fee = amount_in * 997
        expected_out = (amount_in_with_fee * reserve_out) // (
            reserve_in * 1000 + amount_in_with_fee
        )
        assert amounts == [amount_in, expected_out]
        assert expected_out > 0

    async def test_swap_exact_tokens_for_tokens(
        self,
        blockchain_snapshot,
        blockchain,
        deployed_contracts,
        test_accounts,
        hardhat_dir,
    ):
        """Execute a real swap through the router and verify exact amounts."""
        web3 = blockchain.web3
        account = Web3.to_checksum_address(test_accounts[0])
        token_a_address = Web3.to_checksum_address(deployed_contracts["TestToken"])
        token_b_address = Web3.to_checksum_address(deployed_contracts["TestToken2"])
        router_address = Web3.to_checksum_address(deployed_contracts["SimpleV2Router"])

        token_abi = load_artifact_abi(hardhat_dir, "TestToken.sol", "TestToken")
        token_a = web3.eth.contract(address=token_a_address, abi=token_abi)
        token_b = web3.eth.contract(address=token_b_address, abi=token_abi)
        router = web3.eth.contract(
            address=router_address,
            abi=load_artifact_abi(hardhat_dir, "SimpleV2Router.sol", "SimpleV2Router"),
        )

        amount_in = ONE_ETH_WEI
        quoted = await router.functions.getAmountsOut(
            amount_in, [token_a_address, token_b_address]
        ).call()
        expected_out = quoted[-1]
        assert expected_out > 0

        initial_a = await token_a.functions.balanceOf(account).call()
        initial_b = await token_b.functions.balanceOf(account).call()
        assert initial_a >= amount_in

        # Approve the router (the sender account is unlocked on the node)
        approve_tx = await token_a.functions.approve(
            router_address, amount_in
        ).transact({"from": account})
        approve_receipt = await web3.eth.wait_for_transaction_receipt(approve_tx)
        assert approve_receipt["status"] == 1

        latest_block = await web3.eth.get_block("latest")
        deadline = latest_block["timestamp"] + 600

        swap_tx = await router.functions.swapExactTokensForTokens(
            amount_in,
            expected_out,  # exact quote as minimum: any slippage fails the test
            [token_a_address, token_b_address],
            account,
            deadline,
        ).transact({"from": account})
        swap_receipt = await web3.eth.wait_for_transaction_receipt(swap_tx)
        assert swap_receipt["status"] == 1

        final_a = await token_a.functions.balanceOf(account).call()
        final_b = await token_b.functions.balanceOf(account).call()
        assert final_a == initial_a - amount_in
        assert final_b == initial_b + expected_out


class TestHardhatSnapshots:
    """Test snapshot functionality for test isolation."""

    pytestmark = pytest.mark.asyncio(loop_scope="session")

    async def test_with_snapshot_fixture(
        self, blockchain_snapshot, hardhat_env, test_accounts
    ):
        """Test using the snapshot fixture for automatic test isolation."""
        sender = test_accounts[0]
        receiver = test_accounts[1]

        # Any state changes in this test will be reverted automatically
        initial_balance = await hardhat_env.get_account_balance_wei(receiver)

        # Send ETH
        await hardhat_env.send_eth(sender, receiver, 5.0)

        # Balance should change
        new_balance = await hardhat_env.get_account_balance_wei(receiver)
        assert new_balance == initial_balance + 5 * ONE_ETH_WEI

        # After test completes, snapshot will be reverted automatically

    async def test_snapshot_is_reverted(
        self, blockchain_snapshot, hardhat_env, test_accounts
    ):
        """Test that the previous test's changes were reverted."""
        receiver = test_accounts[1]

        # This test should see a balance unaffected by the previous test's
        # 5 ETH transfer (accounts start at 10000 ETH; earlier tests in this
        # session may have deliberately transferred a few ETH to this account).
        balance = await hardhat_env.get_account_balance_wei(receiver)

        # Should be close to the original hardhat balance (10000 ETH)
        assert balance > 9999 * ONE_ETH_WEI


class TestHardhatUtilities:
    """Test utility functions."""

    def test_get_hardhat_accounts(self):
        """Test getting deterministic Hardhat accounts."""
        accounts = get_hardhat_accounts()
        assert len(accounts) == 10
        assert all(Web3.is_address(account) for account in accounts)
        # First account should be the known Hardhat account
        assert accounts[0] == "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"

    def test_get_hardhat_private_keys(self):
        """Test getting deterministic Hardhat private keys."""
        keys = get_hardhat_private_keys()
        assert len(keys) == 10
        assert all(key.startswith("0x") for key in keys)
        assert all(len(key) == 66 for key in keys)  # 0x + 64 hex characters

    def test_accounts_keys_correspondence(self):
        """Test that accounts and keys correspond to each other."""
        accounts = get_hardhat_accounts()
        keys = get_hardhat_private_keys()

        # Test first account/key pair
        from eth_account import Account

        derived_account = Account.from_key(keys[0])
        assert derived_account.address == accounts[0]


# Integration test using all features
class TestHardhatIntegration:
    """Integration test demonstrating full workflow."""

    pytestmark = pytest.mark.asyncio(loop_scope="session")

    async def test_complete_workflow(
        self, hardhat_env, blockchain, deployed_contracts, test_accounts
    ):
        """Test a complete workflow with multiple operations."""
        # 1. Check initial state
        initial_block = await blockchain.fetch_block_number()
        assert initial_block >= 0

        # 2. Check deployed contracts
        assert len(deployed_contracts) >= 5

        # 3. Test token balance
        token_address = deployed_contracts["TestToken"]

        # Check that token contract is deployed and has code
        assert Web3.is_address(token_address)
        code = await blockchain.web3.eth.get_code(
            Web3.to_checksum_address(token_address)
        )
        assert len(code) > 0  # Contract should have bytecode

        # 4. Test ETH transfers
        sender = test_accounts[0]
        receiver = test_accounts[1]

        initial_balance = await hardhat_env.get_account_balance_wei(receiver)
        await hardhat_env.send_eth(sender, receiver, 2.0)
        final_balance = await hardhat_env.get_account_balance_wei(receiver)

        assert final_balance == initial_balance + 2 * ONE_ETH_WEI

        # 5. Mine some blocks
        await hardhat_env.mine_blocks(3)
        final_block = await blockchain.fetch_block_number()
        assert final_block >= initial_block + 3

        # 6. Test multisig has funds
        multisig_address = deployed_contracts["TestMultisig"]
        multisig_balance = await blockchain.fetch_native_asset_balance(
            EthereumAddress.from_string(multisig_address)
        )
        assert multisig_balance > 0
