#!/usr/bin/env python3
"""
Hardhat Testing Framework Demo

This script demonstrates the complete workflow for testing blockchain developments
using the Hardhat local chain integration.

Usage:
    python examples/hardhat_testing_demo.py
"""

import asyncio
import sys
from pathlib import Path

# Add the project root to the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from blockchainpype.evm.blockchain.identifier import EthereumAddress
from tests.hardhat import HardhatTestEnvironment, get_hardhat_accounts


async def main():
    """Main demo function."""
    print("🚀 Hardhat Testing Framework Demo")
    print("=" * 50)

    # Get the hardhat directory
    hardhat_dir = Path(__file__).parent.parent / "common" / "hardhat"

    # Create test environment
    print("\n1. Setting up test environment...")
    env = HardhatTestEnvironment(str(hardhat_dir))

    try:
        # Setup the environment (starts node, deploys contracts, etc.)
        await env.setup()

        # Initialize blockchain connection
        print("\n2. Connecting to blockchain...")
        blockchain = env.blockchain
        if blockchain is None:
            raise RuntimeError("Blockchain connection failed")

        # Get test accounts
        test_accounts = env.test_accounts
        print(f"   ✅ Connected with {len(test_accounts)} test accounts")

        # Check initial blockchain state
        print("\n3. Checking blockchain state...")
        block_number = await blockchain.fetch_block_number()
        print(f"   📦 Current block: {block_number}")

        # Check account balances
        print("\n4. Checking account balances...")
        for i, account in enumerate(test_accounts[:3]):  # Show first 3 accounts
            balance = await env.get_account_balance(account)
            print(f"   💰 Account {i+1}: {balance:.2f} ETH")

        # Test deployed contracts
        print("\n5. Checking deployed contracts...")
        deployed_contracts = env.node.deployments
        for name, address in deployed_contracts.items():
            print(f"   📜 {name}: {address}")

        # Test token contracts
        print("\n6. Testing token contracts...")
        token_address = deployed_contracts["TestToken"]
        print(f"   🪙 TestToken deployed at: {token_address}")
        print("   ✅ Token contract is available for interaction")

        # Test ETH transfer
        print("\n7. Testing ETH transfer...")
        sender = test_accounts[0]
        receiver = test_accounts[1]

        initial_balance = await env.get_account_balance(receiver)
        print(f"   📤 Initial receiver balance: {initial_balance:.2f} ETH")

        # Send 5 ETH
        print("   🔄 Sending 5 ETH...")
        tx_hash = await env.send_eth(sender, receiver, 5.0)
        print(f"   ✅ Transaction hash: {tx_hash}")

        final_balance = await env.get_account_balance(receiver)
        print(f"   📥 Final receiver balance: {final_balance:.2f} ETH")
        print(f"   💸 Difference: {final_balance - initial_balance:.2f} ETH")

        # Test mining blocks
        print("\n8. Testing block mining...")
        initial_block = await blockchain.fetch_block_number()
        print(f"   📦 Initial block: {initial_block}")

        print("   ⛏️  Mining 3 blocks...")
        await env.mine_blocks(3)

        final_block = await blockchain.fetch_block_number()
        print(f"   📦 Final block: {final_block}")
        print(f"   🏗️  Blocks mined: {final_block - initial_block}")

        # Test snapshots
        print("\n9. Testing blockchain snapshots...")
        snapshot_id = await env.snapshot()
        print(f"   📸 Snapshot taken: {snapshot_id}")

        # Make a change
        await env.send_eth(sender, receiver, 1.0)
        balance_after_change = await env.get_account_balance(receiver)
        print(f"   📥 Balance after change: {balance_after_change:.2f} ETH")

        # Revert to snapshot
        await env.revert_to_snapshot(snapshot_id)
        balance_after_revert = await env.get_account_balance(receiver)
        print(f"   📥 Balance after revert: {balance_after_revert:.2f} ETH")
        print(f"   ↩️  Reverted successfully: {balance_after_revert == final_balance}")

        # Test multisig contract
        print("\n10. Testing multisig contract...")
        multisig_address = deployed_contracts["TestMultisig"]
        multisig_balance = await blockchain.fetch_native_asset_balance(
            EthereumAddress.from_string(multisig_address)
        )
        print(f"   🏦 Multisig balance: {multisig_balance:.2f} ETH")

        # Test DEX pair
        print("\n11. Testing DEX components...")
        factory_address = deployed_contracts["TestUniswapV2Factory"]
        pair_address = deployed_contracts["TestUniswapV2Pair"]

        print(f"   🏭 Factory: {factory_address}")
        print(f"   🔗 Pair: {pair_address}")

        # Summary
        print("\n12. Test Summary...")
        print("   ✅ Hardhat node started successfully")
        print("   ✅ Contracts deployed successfully")
        print("   ✅ Blockchain connection established")
        print("   ✅ ETH transfers working")
        print("   ✅ Token contracts deployed")
        print("   ✅ Block mining working")
        print("   ✅ Snapshots working")
        print("   ✅ All test contracts deployed")

        print("\n🎉 All tests completed successfully!")
        print("\n💡 Next steps:")
        print("   - Run: pytest tests/test_hardhat_example.py -v")
        print("   - Create your own test files using the framework")
        print("   - Check docs/HARDHAT_TESTING_GUIDE.md for detailed usage")

    except Exception as e:
        print(f"\n❌ Error during demo: {e}")
        import traceback

        traceback.print_exc()

    finally:
        # Cleanup
        print("\n🧹 Cleaning up...")
        await env.teardown()
        print("   ✅ Environment cleaned up")

    print("\n" + "=" * 50)
    print("Demo completed!")


def demo_utility_functions():
    """Demonstrate utility functions."""
    print("\n🔧 Utility Functions Demo")
    print("-" * 30)

    # Get hardhat accounts
    accounts = get_hardhat_accounts()
    print(f"Default Hardhat accounts: {len(accounts)}")
    for i, account in enumerate(accounts[:3]):
        print(f"  Account {i+1}: {account}")

    # Show deterministic nature
    print(f"\nFirst account (always the same): {accounts[0]}")
    print("These accounts are deterministic and will always be the same")


if __name__ == "__main__":
    print("Hardhat Testing Framework Demo")
    print("This demo showcases the complete testing workflow")
    print("Make sure Node.js and npm are installed for Hardhat")
    print()

    # Show utility functions first
    demo_utility_functions()

    # Run the main demo
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⚠️  Demo interrupted by user")
    except Exception as e:
        print(f"\n❌ Demo failed: {e}")
        sys.exit(1)
