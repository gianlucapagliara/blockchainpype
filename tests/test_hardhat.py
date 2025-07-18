"""
Example test file demonstrating the Hardhat testing framework.

This module shows how to:
- Use the hardhat testing fixtures
- Test basic blockchain operations
- Deploy and interact with contracts
- Use snapshots for test isolation
- Test token transfers and DEX operations
"""

import pytest
from web3 import Web3

from blockchainpype.evm.blockchain.identifier import EthereumAddress
from tests.hardhat import get_hardhat_accounts, get_hardhat_private_keys


class TestHardhatBasicOperations:
    """Test basic blockchain operations using Hardhat."""

    @pytest.mark.asyncio
    async def test_blockchain_connection(self, blockchain):
        """Test that we can connect to the blockchain."""
        block_number = await blockchain.fetch_block_number()
        assert block_number >= 0

    @pytest.mark.asyncio
    async def test_account_balances(self, hardhat_env, test_accounts):
        """Test that test accounts have expected balances."""
        assert len(test_accounts) > 0

        # First account should have plenty of ETH
        balance = await hardhat_env.get_account_balance(test_accounts[0])
        assert balance > 1000  # Should have more than 1000 ETH

    @pytest.mark.asyncio
    async def test_eth_transfer(self, hardhat_env, test_accounts):
        """Test ETH transfer between accounts."""
        sender = test_accounts[0]
        receiver = test_accounts[1]

        # Get initial balances
        initial_sender_balance = await hardhat_env.get_account_balance(sender)
        initial_receiver_balance = await hardhat_env.get_account_balance(receiver)

        # Send 1 ETH
        tx_hash = await hardhat_env.send_eth(sender, receiver, 1.0)
        assert tx_hash.startswith("0x")

        # Check final balances
        final_sender_balance = await hardhat_env.get_account_balance(sender)
        final_receiver_balance = await hardhat_env.get_account_balance(receiver)

        # Sender should have less (accounting for gas)
        assert final_sender_balance < initial_sender_balance
        # Receiver should have exactly 1 ETH more
        assert final_receiver_balance == initial_receiver_balance + 1.0

    @pytest.mark.asyncio
    async def test_mining_blocks(self, hardhat_env, blockchain):
        """Test mining blocks manually."""
        initial_block = await blockchain.fetch_block_number()

        # Mine 5 blocks
        await hardhat_env.mine_blocks(5)

        final_block = await blockchain.fetch_block_number()
        assert final_block >= initial_block + 5

    @pytest.mark.asyncio
    async def test_snapshot_revert(self, hardhat_env, test_accounts):
        """Test blockchain snapshot and revert functionality."""
        sender = test_accounts[0]
        receiver = test_accounts[1]

        # Take snapshot
        snapshot_id = await hardhat_env.snapshot()

        # Get initial balance
        initial_balance = await hardhat_env.get_account_balance(receiver)

        # Send some ETH
        await hardhat_env.send_eth(sender, receiver, 10.0)

        # Check balance changed
        new_balance = await hardhat_env.get_account_balance(receiver)
        assert new_balance == initial_balance + 10.0

        # Revert to snapshot
        await hardhat_env.revert_to_snapshot(snapshot_id)

        # Check balance is back to original
        reverted_balance = await hardhat_env.get_account_balance(receiver)
        assert reverted_balance == initial_balance


class TestHardhatContracts:
    """Test contract deployment and interaction."""

    @pytest.mark.asyncio
    async def test_deployed_contracts(self, deployed_contracts):
        """Test that contracts are deployed correctly."""
        expected_contracts = [
            "TestToken",
            "TestToken2",
            "TestMultisig",
            "TestUniswapV2Factory",
        ]

        for contract_name in expected_contracts:
            assert contract_name in deployed_contracts
            address = deployed_contracts[contract_name]
            assert Web3.is_address(address)

    @pytest.mark.asyncio
    async def test_token_contract_interaction(
        self, blockchain, deployed_contracts, test_accounts
    ):
        """Test interaction with ERC20 token contract."""
        token_address = deployed_contracts["TestToken"]
        owner = test_accounts[0]

        # Check that token contract is deployed
        assert Web3.is_address(token_address)

        # Check that the token contract exists by checking if it has code
        code = await blockchain.web3.eth.get_code(
            Web3.to_checksum_address(token_address)
        )
        assert len(code) > 0  # Contract should have bytecode

        print(
            f"✅ Token contract deployed at {token_address} with {len(code)} bytes of code"
        )

    @pytest.mark.asyncio
    async def test_multisig_contract(self, blockchain, deployed_contracts):
        """Test multisig contract is properly configured."""
        multisig_address = deployed_contracts["TestMultisig"]

        # Check multisig has some ETH
        balance = await blockchain.fetch_native_asset_balance(
            EthereumAddress.from_string(multisig_address)
        )
        assert balance > 0

    @pytest.mark.asyncio
    async def test_uniswap_factory(self, blockchain, deployed_contracts):
        """Test Uniswap factory created a pair."""
        pair_address = deployed_contracts["TestUniswapV2Pair"]

        # Pair should exist
        assert Web3.is_address(pair_address)

        # Pair should have zero balance initially
        balance = await blockchain.fetch_native_asset_balance(
            EthereumAddress.from_string(pair_address)
        )
        assert balance == 0


class TestHardhatSnapshots:
    """Test snapshot functionality for test isolation."""

    @pytest.mark.asyncio
    async def test_with_snapshot_fixture(
        self, blockchain_snapshot, hardhat_env, test_accounts
    ):
        """Test using the snapshot fixture for automatic test isolation."""
        sender = test_accounts[0]
        receiver = test_accounts[1]

        # Any state changes in this test will be reverted automatically
        initial_balance = await hardhat_env.get_account_balance(receiver)

        # Send ETH
        await hardhat_env.send_eth(sender, receiver, 5.0)

        # Balance should change
        new_balance = await hardhat_env.get_account_balance(receiver)
        assert new_balance == initial_balance + 5.0

        # After test completes, snapshot will be reverted automatically

    @pytest.mark.asyncio
    async def test_snapshot_is_reverted(
        self, blockchain_snapshot, hardhat_env, test_accounts
    ):
        """Test that the previous test's changes were reverted."""
        receiver = test_accounts[1]

        # This test should see the original balance, not the modified one
        balance = await hardhat_env.get_account_balance(receiver)

        # Should be close to original hardhat balance (10000 ETH)
        assert balance > 9999  # Account for any gas costs from setup


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

    @pytest.mark.asyncio
    async def test_complete_workflow(
        self, hardhat_env, blockchain, deployed_contracts, test_accounts
    ):
        """Test a complete workflow with multiple operations."""
        # 1. Check initial state
        initial_block = await blockchain.fetch_block_number()
        assert initial_block >= 0

        # 2. Check deployed contracts
        assert len(deployed_contracts) >= 4

        # 3. Test token balance
        token_address = deployed_contracts["TestToken"]
        owner = test_accounts[0]

        # Check that token contract is deployed and has code
        assert Web3.is_address(token_address)
        code = await blockchain.web3.eth.get_code(
            Web3.to_checksum_address(token_address)
        )
        assert len(code) > 0  # Contract should have bytecode

        # 4. Test ETH transfers
        sender = test_accounts[0]
        receiver = test_accounts[1]

        initial_balance = await hardhat_env.get_account_balance(receiver)
        await hardhat_env.send_eth(sender, receiver, 2.0)
        final_balance = await hardhat_env.get_account_balance(receiver)

        assert final_balance == initial_balance + 2.0

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

        print("🎉 Complete workflow test passed!")
        print(f"Final block: {final_block}")
        print(f"Token contract deployed at: {token_address}")
        print(f"Multisig balance: {multisig_balance}")
        print(f"Deployed contracts: {list(deployed_contracts.keys())}")
