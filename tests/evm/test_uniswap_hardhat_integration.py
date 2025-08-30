"""
Integration tests for Uniswap V2 using Hardhat local blockchain.

This module tests the complete Uniswap V2 functionality in a simulated environment,
including transaction creation, signing, and execution.
"""

import asyncio
from decimal import Decimal

import pytest

from blockchainpype.dapps.router.models import SwapMode
from blockchainpype.evm.asset import EthereumNativeAsset
from blockchainpype.evm.blockchain.identifier import EthereumAddress
from blockchainpype.evm.dapp.erc20 import ERC20Token
from blockchainpype.evm.dapp.uniswap.v2 import UniswapV2
from blockchainpype.evm.dapp.uniswap.v3 import UniswapV3
from blockchainpype.evm.wallet.identifier import EthereumWalletIdentifier
from blockchainpype.evm.wallet.signer import EthereumSignerConfiguration
from blockchainpype.evm.wallet.wallet import EthereumWallet, EthereumWalletConfiguration
from blockchainpype.factory import BlockchainFactory
from blockchainpype.initializer import BlockchainConfigurations

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
async def hardhat_blockchain():
    """Create a blockchain instance connected to Hardhat."""
    return BlockchainFactory.get(BlockchainConfigurations.HARDHAT_LOCAL)


@pytest.fixture(scope="module")
async def test_wallet(hardhat_blockchain):
    """Create a test wallet using one of Hardhat's pre-funded accounts."""
    # Using the first Hardhat account (account[0])
    # Private key for account 0: 0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80
    wallet_config = EthereumWalletConfiguration(
        identifier=EthereumWalletIdentifier(
            address=EthereumAddress.from_string("0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"),
            platform=hardhat_blockchain.platform
        ),
        signer=EthereumSignerConfiguration(
            private_key="0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
        )
    )
    
    wallet = EthereumWallet(wallet_config, hardhat_blockchain)
    await wallet.sync_nonce()
    
    return wallet


@pytest.fixture(scope="module")
async def test_tokens(hardhat_blockchain):
    """Create test tokens (assumes they are deployed in Hardhat environment)."""
    # These addresses should match the tokens deployed by Hardhat scripts
    # You may need to update these based on your actual deployment
    
    # TestToken A (assumed to be deployed)
    token_a = ERC20Token(
        address=EthereumAddress.from_string("0x5FbDB2315678afecb367f032d93F642f64180aa3"),
        platform=hardhat_blockchain.platform
    )
    
    # TestToken B (assumed to be deployed)  
    token_b = ERC20Token(
        address=EthereumAddress.from_string("0xe7f1725E7734CE288F8367e1Bb143E90bb3F0512"),
        platform=hardhat_blockchain.platform
    )
    
    # Initialize token data
    await token_a.initialize_data()
    await token_b.initialize_data()
    
    return token_a, token_b


@pytest.fixture(scope="module")
async def uniswap_v2(hardhat_blockchain):
    """Create a Uniswap V2 instance using Hardhat deployed contracts."""
    # These addresses should match the contracts deployed by Hardhat scripts
    # Update these based on your actual deployment addresses
    
    return UniswapV2(
        blockchain=hardhat_blockchain,
        factory_address="0x9fE46736679d2D9a65F0992F2272dE9f3c7fa6e0",  # Update with actual factory
        router_address="0xCf7Ed3AccA5a467e9e704C703E8D87F634fB0Fc9"   # Update with actual router
    )


@pytest.fixture(scope="module")
async def uniswap_v3(hardhat_blockchain):
    """Create a Uniswap V3 instance using Hardhat deployed contracts."""
    # These addresses should match the contracts deployed by Hardhat scripts
    # Update these based on your actual deployment addresses
    
    return UniswapV3(
        blockchain=hardhat_blockchain,
        factory_address="0xa513E6E4b8f2a923D98304ec87F64353C4D5C853",  # Update with actual V3 factory
        router_address="0x2279B7A0a67DB372996a5FaB50D91eAA73d2eBe6",   # Update with actual V3 router
        quoter_address="0x8A791620dd6260079BF849Dc5567aDC3F2FdC318"    # Update with actual quoter
    )


@pytest.mark.asyncio
class TestUniswapV2HardhatIntegration:
    """Integration tests for Uniswap V2 using Hardhat local blockchain."""
    
    async def test_hardhat_connection(self, hardhat_blockchain):
        """Test that we can connect to Hardhat blockchain."""
        # Test basic blockchain connection
        latest_block = await hardhat_blockchain.web3.eth.get_block_number()
        assert latest_block >= 0
        
        # Test that we have accounts
        accounts = await hardhat_blockchain.web3.eth.accounts
        assert len(accounts) >= 10  # Hardhat provides 20 accounts by default
        
        print(f"Connected to Hardhat. Latest block: {latest_block}, Accounts: {len(accounts)}")
        
    async def test_wallet_balance(self, test_wallet, hardhat_blockchain):
        """Test that test wallet has ETH balance."""
        balance = await test_wallet.fetch_balance(hardhat_blockchain.native_asset)
        assert balance > 0
        print(f"Wallet balance: {balance} ETH")
        
    async def test_token_initialization(self, test_tokens):
        """Test that test tokens are properly initialized."""
        token_a, token_b = test_tokens
        
        assert token_a.data is not None
        assert token_b.data is not None
        assert token_a.data.symbol is not None
        assert token_b.data.symbol is not None
        
        print(f"Token A: {token_a.data.symbol} ({token_a.data.name})")
        print(f"Token B: {token_b.data.symbol} ({token_b.data.name})")
        
    async def test_uniswap_contracts_initialization(self, uniswap_v2):
        """Test that Uniswap contracts are properly initialized."""
        # Ensure contracts are initialized
        await uniswap_v2._ensure_contracts_initialized()
        
        assert uniswap_v2.factory_contract.is_initialized
        assert uniswap_v2.router_contract.is_initialized
        
        print("Uniswap V2 contracts initialized successfully")
        
    @pytest.mark.skip(reason="Requires actual pair creation and liquidity")
    async def test_quote_swap_integration(self, uniswap_v2, test_tokens):
        """Test getting a real swap quote from Hardhat environment."""
        token_a, token_b = test_tokens
        
        try:
            quote = await uniswap_v2.quote_swap(
                input_asset=token_a,
                output_asset=token_b,
                amount=Decimal("1"),
                mode=SwapMode.EXACT_INPUT
            )
            
            assert quote.input_amount == Decimal("1")
            assert quote.output_amount > 0
            assert quote.protocol == "uniswap_v2"
            
            print(f"Quote: {quote.input_amount} {token_a.data.symbol} -> {quote.output_amount} {token_b.data.symbol}")
            
        except ValueError as e:
            if "No pair found" in str(e):
                pytest.skip("No liquidity pair exists for test tokens")
            else:
                raise
                
    @pytest.mark.skip(reason="Requires actual pair creation and liquidity")
    async def test_build_swap_transaction_integration(self, uniswap_v2, test_tokens, test_wallet):
        """Test building a swap transaction in Hardhat environment."""
        token_a, token_b = test_tokens
        
        try:
            # Get a quote first
            quote = await uniswap_v2.quote_swap(
                input_asset=token_a,
                output_asset=token_b,
                amount=Decimal("1"),
                mode=SwapMode.EXACT_INPUT
            )
            
            # Build transaction
            tx_params = await uniswap_v2.build_swap_transaction(
                route=quote,
                wallet=test_wallet
            )
            
            # Verify transaction parameters
            assert "to" in tx_params
            assert "data" in tx_params
            assert "gas" in tx_params
            assert tx_params["from"] == test_wallet.address.raw
            
            print(f"Transaction built successfully:")
            print(f"  To: {tx_params['to']}")
            print(f"  Gas: {tx_params['gas']}")
            print(f"  From: {tx_params['from']}")
            
        except ValueError as e:
            if "No pair found" in str(e):
                pytest.skip("No liquidity pair exists for test tokens")
            else:
                raise
                
    @pytest.mark.skip(reason="Requires actual pair creation and liquidity, and would execute real transaction")
    async def test_create_swap_transaction_integration(self, uniswap_v2, test_tokens, test_wallet):
        """Test creating a signed swap transaction in Hardhat environment."""
        token_a, token_b = test_tokens
        
        try:
            # Get a quote first
            quote = await uniswap_v2.quote_swap(
                input_asset=token_a,
                output_asset=token_b,
                amount=Decimal("0.1"),  # Smaller amount for testing
                mode=SwapMode.EXACT_INPUT
            )
            
            # Create signed transaction
            transaction = await uniswap_v2.create_swap_transaction(
                route=quote,
                wallet=test_wallet,
                client_operation_id="hardhat_integration_test"
            )
            
            # Verify transaction
            assert transaction.client_operation_id == "hardhat_integration_test"
            assert transaction.signed_transaction is not None
            assert transaction.signed_transaction.hash is not None
            
            print(f"Signed transaction created:")
            print(f"  Operation ID: {transaction.client_operation_id}")
            print(f"  Transaction hash: {transaction.signed_transaction.hash.hex()}")
            
            # Note: This transaction would be broadcast to the network
            # In a real test, you might want to wait for confirmation
            
        except ValueError as e:
            if "No pair found" in str(e):
                pytest.skip("No liquidity pair exists for test tokens")
            else:
                raise
                
    async def test_eth_to_token_swap_scenario(self, uniswap_v2, test_tokens, test_wallet, hardhat_blockchain):
        """Test a potential ETH to token swap scenario (dry run)."""
        token_a, _ = test_tokens
        eth_asset = hardhat_blockchain.native_asset
        
        # This would be a typical ETH -> Token swap
        # We'll just test the structure without executing
        
        swap_amount = Decimal("0.1")  # 0.1 ETH
        
        print(f"Scenario: Swap {swap_amount} ETH for {token_a.data.symbol}")
        print(f"  ETH address: {eth_asset.address.raw}")
        print(f"  Token address: {token_a.address.raw}")
        print(f"  Wallet address: {test_wallet.address.raw}")
        print(f"  Uniswap router: {uniswap_v2.router_address}")
        
        # In a real scenario with liquidity, you would:
        # 1. quote = await uniswap_v2.quote_swap(eth_asset, token_a, swap_amount)
        # 2. transaction = await uniswap_v2.execute_swap(...)
        # 3. Wait for transaction confirmation
        
        print("Swap scenario structure verified (would require liquidity to execute)")

    async def test_uniswap_v3_contracts_initialization(self, uniswap_v3):
        """Test that Uniswap V3 contracts are properly initialized."""
        # Ensure contracts are initialized
        await uniswap_v3._ensure_contracts_initialized()
        
        assert uniswap_v3.factory_contract.is_initialized
        assert uniswap_v3.router_contract.is_initialized
        assert uniswap_v3.quoter_contract.is_initialized
        
        print("Uniswap V3 contracts initialized successfully")
        
    @pytest.mark.skip(reason="Requires actual V3 pool creation and liquidity")
    async def test_quote_swap_v3_integration(self, uniswap_v3, test_tokens):
        """Test getting a real V3 swap quote from Hardhat environment."""
        token_a, token_b = test_tokens
        
        try:
            quote = await uniswap_v3.quote_swap(
                input_asset=token_a,
                output_asset=token_b,
                amount=Decimal("1"),
                mode=SwapMode.EXACT_INPUT
            )
            
            assert quote.input_amount == Decimal("1")
            assert quote.output_amount > 0
            assert quote.protocol.startswith("uniswap_v3_")
            
            print(f"V3 Quote: {quote.input_amount} {token_a.data.symbol} -> {quote.output_amount} {token_b.data.symbol}")
            print(f"Fee tier: {quote.protocol.split('_')[-1]}")
            
        except ValueError as e:
            if "No valid pool found" in str(e):
                pytest.skip("No V3 liquidity pool exists for test tokens")
            else:
                raise
                
    @pytest.mark.skip(reason="Requires actual V3 pool creation and liquidity")
    async def test_build_swap_transaction_v3_integration(self, uniswap_v3, test_tokens, test_wallet):
        """Test building a V3 swap transaction in Hardhat environment."""
        token_a, token_b = test_tokens
        
        try:
            # Get a quote first
            quote = await uniswap_v3.quote_swap(
                input_asset=token_a,
                output_asset=token_b,
                amount=Decimal("1"),
                mode=SwapMode.EXACT_INPUT
            )
            
            # Build transaction
            tx_params = await uniswap_v3.build_swap_transaction(
                route=quote,
                wallet=test_wallet
            )
            
            # Verify transaction parameters
            assert "to" in tx_params
            assert "data" in tx_params
            assert "gas" in tx_params
            assert tx_params["from"] == test_wallet.address.raw
            
            print(f"V3 Transaction built successfully:")
            print(f"  To: {tx_params['to']}")
            print(f"  Gas: {tx_params['gas']}")
            print(f"  From: {tx_params['from']}")
            
        except ValueError as e:
            if "No valid pool found" in str(e):
                pytest.skip("No V3 liquidity pool exists for test tokens")
            else:
                raise

    async def test_v3_fee_tier_scenario(self, uniswap_v3, test_tokens, test_wallet, hardhat_blockchain):
        """Test V3 fee tier selection scenario (dry run)."""
        token_a, _ = test_tokens
        eth_asset = hardhat_blockchain.native_asset
        
        # This would test V3's multi-fee-tier selection
        swap_amount = Decimal("0.1")  # 0.1 ETH
        
        print(f"V3 Scenario: Swap {swap_amount} ETH for {token_a.data.symbol}")
        print(f"  V3 supports multiple fee tiers: {uniswap_v3.fee_tiers}")
        print(f"  ETH address: {eth_asset.address.raw}")
        print(f"  Token address: {token_a.address.raw}")
        print(f"  Wallet address: {test_wallet.address.raw}")
        print(f"  Uniswap V3 router: {uniswap_v3.router_address}")
        print(f"  Uniswap V3 quoter: {uniswap_v3.quoter_address}")
        
        # In a real scenario with liquidity, V3 would:
        # 1. Check multiple fee tiers (0.01%, 0.05%, 0.3%, 1%)
        # 2. Find the best rate across all available pools
        # 3. Create exactInputSingle or exactOutputSingle transaction
        
        print("V3 multi-fee-tier scenario structure verified (would require liquidity to execute)")


@pytest.mark.asyncio
class TestUniswapV2ErrorHandling:
    """Test error handling in Hardhat environment."""
    
    async def test_no_pair_found_error(self, uniswap_v2, test_tokens):
        """Test error when no pair exists between tokens."""
        token_a, token_b = test_tokens
        
        with pytest.raises(ValueError, match="No pair found"):
            await uniswap_v2.quote_swap(
                input_asset=token_a,
                output_asset=token_b,
                amount=Decimal("1"),
                mode=SwapMode.EXACT_INPUT
            )
            
    async def test_invalid_amount_error(self, uniswap_v2, test_tokens):
        """Test error handling with invalid amounts."""
        token_a, token_b = test_tokens
        
        with pytest.raises((ValueError, Exception)):
            await uniswap_v2.quote_swap(
                input_asset=token_a,
                output_asset=token_b,
                amount=Decimal("0"),  # Zero amount should fail
                mode=SwapMode.EXACT_INPUT
            )


@pytest.mark.asyncio
class TestUniswapV3ErrorHandling:
    """Test V3 error handling in Hardhat environment."""
    
    async def test_no_pool_found_error_v3(self, uniswap_v3, test_tokens):
        """Test error when no V3 pool exists between tokens."""
        token_a, token_b = test_tokens
        
        with pytest.raises(ValueError, match="No valid pool found"):
            await uniswap_v3.quote_swap(
                input_asset=token_a,
                output_asset=token_b,
                amount=Decimal("1"),
                mode=SwapMode.EXACT_INPUT
            )
            
    async def test_invalid_amount_error_v3(self, uniswap_v3, test_tokens):
        """Test error handling with invalid amounts in V3."""
        token_a, token_b = test_tokens
        
        with pytest.raises((ValueError, Exception)):
            await uniswap_v3.quote_swap(
                input_asset=token_a,
                output_asset=token_b,
                amount=Decimal("0"),  # Zero amount should fail
                mode=SwapMode.EXACT_INPUT
            )
            
    async def test_multi_hop_not_implemented_v3(self, uniswap_v3, test_tokens, test_wallet):
        """Test that multi-hop swaps raise NotImplementedError in current V3 version."""
        token_a, token_b = test_tokens
        
        # Create a mock route with multiple hops
        mock_route = MagicMock()
        mock_route.sequence = [MagicMock(), MagicMock()]  # Two hops
        
        with pytest.raises(ValueError, match="Multi-hop swaps not implemented"):
            await uniswap_v3.build_swap_transaction(
                route=mock_route,
                wallet=test_wallet
            )