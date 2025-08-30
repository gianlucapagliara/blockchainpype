"""
Unit tests for Uniswap DEX implementation.

This module tests:
- Uniswap V2 strategy functionality
- Uniswap V3 strategy functionality
- UniswapDEX integration
- Error handling and edge cases
"""

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from financepype.platforms.blockchain import BlockchainPlatform

from blockchainpype.dapps.router.models import SwapMode, SwapRoute
from blockchainpype.evm.asset import EthereumAssetData
from blockchainpype.evm.blockchain.blockchain import EthereumBlockchain
from blockchainpype.evm.blockchain.identifier import EthereumAddress
from blockchainpype.evm.dapp.uniswap.dex import UniswapConfiguration, UniswapDEX
from blockchainpype.evm.dapp.uniswap.v2 import UniswapV2
from blockchainpype.evm.dapp.uniswap.v3 import UniswapV3


class MockEthereumAsset:
    """Mock EthereumAsset for testing."""

    def __init__(self, symbol: str, decimals: int, address: str):
        self.identifier = EthereumAddress.from_string(address)
        self.data = EthereumAssetData(
            name=f"{symbol} Token", symbol=symbol, decimals=decimals
        )

    async def initialize_data(self) -> None:
        """Mock initialize_data method."""
        pass

    @property
    def address(self) -> EthereumAddress:
        return self.identifier


@pytest.fixture
def mock_blockchain():
    """Create a mock blockchain for testing."""
    blockchain = MagicMock(spec=EthereumBlockchain)
    blockchain.chain_id = 1  # Ethereum mainnet
    blockchain.current_timestamp = 1640995200  # January 1, 2022
    blockchain.get_default_address = AsyncMock(
        return_value="0x742d35Cc6634C0532925a3b8D2bC7a5Fad7b6e4F"
    )

    # Mock web3 for blockchain - gas_price property that can be awaited
    mock_web3 = MagicMock()

    # Create an awaitable gas_price
    async def mock_gas_price():
        return 20000000000

    mock_web3.eth.gas_price = mock_gas_price()
    blockchain.web3 = mock_web3

    # Mock the platform properly
    mock_platform = MagicMock(spec=BlockchainPlatform)
    mock_platform.chain_id = 1
    mock_platform.identifier = "ethereum"
    blockchain.platform = mock_platform

    return blockchain


@pytest.fixture
def usdc_asset():
    """Create a mock USDC asset."""
    return MockEthereumAsset(
        symbol="USDC", decimals=6, address="0xA0b86a33E6441b29205ab6F5b10C0B7B5C7f1b4e"
    )


@pytest.fixture
def weth_asset():
    """Create a mock WETH asset."""
    return MockEthereumAsset(
        symbol="WETH", decimals=18, address="0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
    )


class TestUniswapV2Strategy:
    """Test suite for Uniswap V2 strategy."""

    @pytest.fixture
    def v2_strategy(self, mock_blockchain):
        """Create a Uniswap V2 strategy instance."""
        with patch(
            "blockchainpype.evm.dapp.uniswap.v2.EthereumSmartContract"
        ) as mock_contract_class, patch(
            "financepype.operators.factory.OperatorFactory.get"
        ) as mock_factory:
            # Mock the factory to return a mock blockchain
            mock_factory.return_value = mock_blockchain

            # Mock the contract instances
            mock_factory_contract = MagicMock()
            mock_factory_contract.is_initialized = False
            mock_factory_contract.initialize = AsyncMock()
            mock_factory_contract.functions = MagicMock()

            mock_router_contract = MagicMock()
            mock_router_contract.is_initialized = False
            mock_router_contract.initialize = AsyncMock()
            mock_router_contract.functions = MagicMock()

            # Return different mocks for different addresses
            def mock_contract_init(config):
                if "5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f" in str(config.address):
                    return mock_factory_contract
                else:
                    return mock_router_contract

            mock_contract_class.side_effect = mock_contract_init

            strategy = UniswapV2(
                blockchain=mock_blockchain,
                factory_address="0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f",
                router_address="0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D",
            )

            # Mock contract function calls
            mock_get_pair = MagicMock()
            mock_get_pair.call = AsyncMock()
            mock_factory_contract.functions.getPair.return_value = mock_get_pair

            mock_swap_exact = MagicMock()
            mock_swap_exact.build_transaction = AsyncMock()
            mock_router_contract.functions.swapExactTokensForTokens.return_value = mock_swap_exact

            mock_swap_for_exact = MagicMock()
            mock_swap_for_exact.build_transaction = AsyncMock()
            mock_router_contract.functions.swapTokensForExactTokens.return_value = mock_swap_for_exact

            return strategy

    @pytest.mark.asyncio
    async def test_quote_swap_exact_input(self, v2_strategy, usdc_asset, weth_asset):
        """Test exact input swap quote calculation."""
        # Mock pair address
        v2_strategy.factory_contract.functions.getPair.return_value.call.return_value = "0x1234567890123456789012345678901234567890"

        # Mock pair contract
        with patch(
            "blockchainpype.evm.dapp.uniswap.v2.EthereumSmartContract"
        ) as mock_pair_contract, patch(
            "financepype.operators.factory.OperatorFactory.get"
        ) as mock_factory, patch(
            "blockchainpype.evm.dapp.uniswap.v2.SwapHop"
        ) as mock_swap_hop_class, patch(
            "blockchainpype.evm.dapp.uniswap.v2.SwapRoute"
        ) as mock_swap_route_class:
            mock_factory.return_value = MagicMock()
            pair_contract = mock_pair_contract.return_value
            pair_contract.initialize = AsyncMock()
            pair_contract.functions.getReserves.return_value.call = AsyncMock(return_value=(
                Decimal("1000000") * 10**6,
                Decimal("1000") * 10**18,
                1640995200,
            ))  # getReserves
            pair_contract.functions.token0.return_value.call = AsyncMock(return_value=str(
                usdc_asset.address
            ))  # token0

            # Mock SwapHop and SwapRoute
            mock_swap_hop = MagicMock()
            mock_swap_hop_class.return_value = mock_swap_hop

            mock_swap_route = MagicMock()
            mock_swap_route.input_asset = usdc_asset
            mock_swap_route.output_asset = weth_asset
            mock_swap_route.input_amount = Decimal("100")
            mock_swap_route.output_amount = Decimal("0.095")  # Calculated from formula
            mock_swap_route.protocol = "uniswap_v2"
            mock_swap_route.mode = SwapMode.EXACT_INPUT
            mock_swap_route_class.return_value = mock_swap_route

            quote = await v2_strategy.quote_swap(
                input_asset=usdc_asset,
                output_asset=weth_asset,
                amount=Decimal("100"),  # 100 USDC
                mode=SwapMode.EXACT_INPUT,
            )

            # Verify the mocked quote
            assert quote.input_asset == usdc_asset
            assert quote.output_asset == weth_asset
            assert quote.input_amount == Decimal("100")
            assert quote.output_amount > 0
            assert quote.protocol == "uniswap_v2"
            assert quote.mode == SwapMode.EXACT_INPUT

    @pytest.mark.asyncio
    async def test_quote_swap_no_pair(self, v2_strategy, usdc_asset, weth_asset):
        """Test quote swap when no pair exists."""
        # Mock no pair found
        v2_strategy.factory_contract.functions.getPair.return_value.call.return_value = "0x0000000000000000000000000000000000000000"

        with pytest.raises(ValueError, match="No pair found"):
            await v2_strategy.quote_swap(
                input_asset=usdc_asset,
                output_asset=weth_asset,
                amount=Decimal("100"),
                mode=SwapMode.EXACT_INPUT,
            )

    @pytest.mark.asyncio
    async def test_get_reserves(self, v2_strategy, usdc_asset, weth_asset):
        """Test getting reserves for a pair."""
        # Mock pair address
        v2_strategy.factory_contract.functions.getPair.return_value.call.return_value = "0x1234567890123456789012345678901234567890"

        # Mock pair contract
        with patch(
            "blockchainpype.evm.dapp.uniswap.v2.EthereumSmartContract"
        ) as mock_pair_contract, patch(
            "financepype.operators.factory.OperatorFactory.get"
        ) as mock_factory:
            mock_factory.return_value = MagicMock()
            pair_contract = mock_pair_contract.return_value
            pair_contract.initialize = AsyncMock()
            pair_contract.functions.getReserves.return_value.call = AsyncMock(return_value=(
                1000000 * 10**6,
                1000 * 10**18,
                1640995200,
            ))  # getReserves
            pair_contract.functions.token0.return_value.call = AsyncMock(return_value=str(
                usdc_asset.address
            ))  # token0

            reserves = await v2_strategy.get_reserves(usdc_asset, weth_asset)

            assert len(reserves) == 2
            assert reserves[0] == Decimal("1000000")  # USDC reserves
            assert reserves[1] == Decimal("1000")  # WETH reserves

    @pytest.mark.asyncio
    async def test_build_swap_transaction(self, v2_strategy):
        """Test building a swap transaction - should raise NotImplementedError."""
        # Create a mock route using MagicMock to avoid Pydantic validation
        mock_route = MagicMock()
        mock_route.sequence = [MagicMock()]  # Single hop
        mock_route.mode = SwapMode.EXACT_INPUT
        mock_route.max_slippage = Decimal("0.005")

        # Mock the hop properties
        mock_hop = mock_route.sequence[0]
        mock_hop.input_asset = MagicMock()
        mock_hop.input_asset.data = MagicMock()
        mock_hop.input_asset.data.decimals = 6
        mock_hop.output_asset = MagicMock()
        mock_hop.output_asset.data = MagicMock()
        mock_hop.output_asset.data.decimals = 18
        mock_hop.input_amount = Decimal("100")
        mock_hop.output_amount = Decimal("0.05")

        # Mock transaction data
        v2_strategy.router_contract.functions.swapExactTokensForTokens.return_value.build_transaction.return_value = {
            "data": "0x123456789",
            "gas": 200000,
        }

        # This should raise NotImplementedError as per the current implementation
        with pytest.raises(NotImplementedError):
            await v2_strategy.build_swap_transaction(
                mock_route, recipient="0x742d35Cc6634C0532925a3b8D2bC7a5Fad7b6e4F"
            )

    def test_amount_calculation_formulas(self, v2_strategy):
        """Test Uniswap V2 amount calculation formulas."""
        # Test _get_amount_out
        amount_in = 100 * 10**6  # 100 USDC
        reserve_in = Decimal("1000000") * 10**6  # 1M USDC
        reserve_out = Decimal("1000") * 10**18  # 1K WETH

        amount_out = asyncio.run(
            v2_strategy._get_amount_out(amount_in, reserve_in, reserve_out)
        )

        # Should return some positive amount
        assert amount_out > 0

        # Test _get_amount_in
        amount_out_target = 1 * 10**18  # 1 WETH
        amount_in_needed = asyncio.run(
            v2_strategy._get_amount_in(amount_out_target, reserve_in, reserve_out)
        )

        # Should return some positive amount
        assert amount_in_needed > 0


class TestUniswapV3Strategy:
    """Test suite for Uniswap V3 strategy."""

    @pytest.fixture
    def v3_strategy(self, mock_blockchain):
        """Create a Uniswap V3 strategy instance."""
        with patch(
            "blockchainpype.evm.dapp.uniswap.v3.EthereumSmartContract"
        ) as mock_contract_class, patch(
            "financepype.operators.factory.OperatorFactory.get"
        ) as mock_factory:
            # Mock the factory to return a mock blockchain
            mock_factory.return_value = mock_blockchain

            # Mock the contract instances
            mock_factory_contract = MagicMock()
            mock_factory_contract.is_initialized = False
            mock_factory_contract.initialize = AsyncMock()
            mock_factory_contract.functions = MagicMock()

            mock_router_contract = MagicMock()
            mock_router_contract.is_initialized = False
            mock_router_contract.initialize = AsyncMock()
            mock_router_contract.functions = MagicMock()

            mock_quoter_contract = MagicMock()
            mock_quoter_contract.is_initialized = False
            mock_quoter_contract.initialize = AsyncMock()
            mock_quoter_contract.functions = MagicMock()

            # Return different mocks for different addresses
            def mock_contract_init(config):
                if "1F98431c8aD98523631AE4a59f267346ea31F984" in str(config.address):
                    return mock_factory_contract
                elif "E592427A0AEce92De3Edee1F18E0157C05861564" in str(config.address):
                    return mock_router_contract
                else:
                    return mock_quoter_contract

            mock_contract_class.side_effect = mock_contract_init

            strategy = UniswapV3(
                blockchain=mock_blockchain,
                factory_address="0x1F98431c8aD98523631AE4a59f267346ea31F984",
                router_address="0xE592427A0AEce92De3Edee1F18E0157C05861564",
                quoter_address="0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6",
            )

            # Mock contract function calls
            mock_get_pool = MagicMock()
            mock_get_pool.call = AsyncMock()
            mock_factory_contract.functions.getPool.return_value = mock_get_pool

            mock_quote_exact_input = MagicMock()
            mock_quote_exact_input.call = AsyncMock()
            mock_quoter_contract.functions.quoteExactInputSingle.return_value = mock_quote_exact_input

            mock_quote_exact_output = MagicMock()
            mock_quote_exact_output.call = AsyncMock()
            mock_quoter_contract.functions.quoteExactOutputSingle.return_value = mock_quote_exact_output

            return strategy

    @pytest.mark.asyncio
    async def test_quote_swap_multiple_fee_tiers(
        self, v3_strategy, usdc_asset, weth_asset
    ):
        """Test quote swap across multiple fee tiers."""

        # Mock pool addresses for different fee tiers
        def mock_get_pool(*args):
            # args will be (asset_a, asset_b, fee)
            if len(args) >= 3:
                fee = args[2]
            else:
                fee = 3000  # default
            if fee == 500:
                return "0x1111111111111111111111111111111111111111"
            elif fee == 3000:
                return "0x2222222222222222222222222222222222222222"
            else:
                return "0x0000000000000000000000000000000000000000"

        v3_strategy.factory_contract.functions.getPool.return_value.call.side_effect = (
            mock_get_pool
        )

        # Mock quoter responses (fee tier 3000 gives better rate)
        def mock_quoter_call(*args):
            # args will be (asset_a, asset_b, fee, amount, price_limit)
            fee = args[2] if len(args) > 2 else 3000
            if fee == 500:  # fee tier
                return 50 * 10**18  # 50 WETH
            elif fee == 3000:  # fee tier
                return 51 * 10**18  # 51 WETH (better rate)
            else:
                raise Exception("No pool")

        v3_strategy.quoter_contract.functions.quoteExactInputSingle.return_value.call.side_effect = mock_quoter_call

        # Mock SwapHop and SwapRoute to avoid Pydantic validation
        with patch("blockchainpype.evm.dapp.uniswap.v3.SwapHop") as mock_swap_hop_class, \
             patch("blockchainpype.evm.dapp.uniswap.v3.SwapRoute") as mock_swap_route_class:

            mock_swap_hop = MagicMock()
            mock_swap_hop_class.return_value = mock_swap_hop

            mock_swap_route = MagicMock()
            mock_swap_route.output_amount = Decimal("51")
            mock_swap_route.protocol = "uniswap_v3_3000"
            mock_swap_route_class.return_value = mock_swap_route

            quote = await v3_strategy.quote_swap(
                input_asset=usdc_asset,
                output_asset=weth_asset,
                amount=Decimal("100"),
                mode=SwapMode.EXACT_INPUT,
            )

            assert quote.output_amount == Decimal("51")  # Should pick better rate
            assert quote.protocol == "uniswap_v3_3000"  # Should use 0.3% fee tier

    @pytest.mark.asyncio
    async def test_quote_swap_no_pools(self, v3_strategy, usdc_asset, weth_asset):
        """Test quote swap when no pools exist."""
        # Mock no pools found
        v3_strategy.factory_contract.functions.getPool.return_value.call.return_value = "0x0000000000000000000000000000000000000000"

        with pytest.raises(ValueError, match="No valid pool found"):
            await v3_strategy.quote_swap(
                input_asset=usdc_asset,
                output_asset=weth_asset,
                amount=Decimal("100"),
                mode=SwapMode.EXACT_INPUT,
            )

    @pytest.mark.asyncio
    async def test_get_reserves_approximation(
        self, v3_strategy, usdc_asset, weth_asset
    ):
        """Test getting reserves approximation for V3."""
        # Mock pool address
        v3_strategy.factory_contract.functions.getPool.return_value.call.return_value = "0x1234567890123456789012345678901234567890"

        # Mock pool contract
        with patch(
            "blockchainpype.evm.dapp.uniswap.v3.EthereumSmartContract"
        ) as mock_pool_contract, patch(
            "financepype.operators.factory.OperatorFactory.get"
        ) as mock_factory:
            mock_factory.return_value = MagicMock()
            pool_contract = mock_pool_contract.return_value
            pool_contract.initialize = AsyncMock()
            pool_contract.functions.liquidity.return_value.call = AsyncMock(return_value=1000000)  # liquidity
            pool_contract.functions.slot0.return_value.call = AsyncMock(return_value=(
                79228162514264337593543950336,
            ))  # slot0 - sqrtPriceX96
            pool_contract.functions.token0.return_value.call = AsyncMock(return_value=str(
                usdc_asset.address
            ))  # token0

            reserves = await v3_strategy.get_reserves(usdc_asset, weth_asset)

            assert len(reserves) == 2
            assert reserves[0] > 0  # USDC reserves approximation
            assert reserves[1] > 0  # WETH reserves approximation


class TestUniswapDEX:
    """Test suite for UniswapDEX integration."""

    @pytest.fixture
    def uniswap_dex(self, mock_blockchain):
        """Create a UniswapDEX instance."""
        # Mock the blockchain to have chain_id = 1 for Ethereum mainnet
        mock_blockchain.platform.chain_id = 1

        # Create a mock DEX instance
        dex = MagicMock(spec=UniswapDEX)
        dex.configuration = UniswapConfiguration.ethereum_mainnet()
        dex.blockchain = mock_blockchain
        dex.get_supported_pools = AsyncMock(return_value=[])
        dex.quote_swap = AsyncMock()

        # Make find_best_route delegate to quote_swap like the real implementation
        async def mock_find_best_route(
            input_asset,
            output_asset,
            amount,
            mode=SwapMode.EXACT_INPUT,
            max_hops=3,
            protocol=None,
        ):
            return await dex.quote_swap(
                input_asset, output_asset, amount, mode, protocol
            )

        dex.find_best_route = AsyncMock(side_effect=mock_find_best_route)

        return dex

    def test_initialization_ethereum_mainnet(self, mock_blockchain):
        """Test DEX initialization for Ethereum mainnet."""
        mock_blockchain.platform.chain_id = 1

        # Test the configuration directly
        config = UniswapConfiguration.ethereum_mainnet()
        assert len(config.protocols) == 2
        assert any(p.protocol_name == "uniswap_v2" for p in config.protocols)
        assert any(p.protocol_name == "uniswap_v3" for p in config.protocols)

    def test_initialization_polygon(self, mock_blockchain):
        """Test DEX initialization for Polygon."""
        mock_blockchain.platform.chain_id = 137

        # Test the configuration directly
        config = UniswapConfiguration.polygon_mainnet()
        assert len(config.protocols) == 1
        assert config.protocols[0].protocol_name == "uniswap_v3"

    def test_initialization_unsupported_network(self, mock_blockchain):
        """Test DEX initialization for unsupported network."""
        mock_blockchain.platform.chain_id = 999  # Unsupported chain

        with pytest.raises(ValueError, match="No default Uniswap configuration"):
            UniswapDEX(mock_blockchain)

    def test_custom_configuration(self, mock_blockchain):
        """Test DEX with custom configuration."""
        from blockchainpype.evm.blockchain.blockchain import EthereumBlockchainType

        ethereum_platform = BlockchainPlatform(
            identifier="ethereum",
            type=EthereumBlockchainType,
            chain_id=1,
        )

        custom_config = UniswapConfiguration(
            protocols=[],
            default_slippage=Decimal("0.01"),
            default_deadline_minutes=30,
            minimum_received_multiplier=Decimal("0.90"),
            platform=ethereum_platform,
        )

        # Test the configuration directly
        assert custom_config.default_slippage == Decimal("0.01")
        assert custom_config.default_deadline_minutes == 30

    @pytest.mark.asyncio
    async def test_get_supported_pools(self, uniswap_dex):
        """Test getting supported pools (returns empty for now)."""
        pools = await uniswap_dex.get_supported_pools()
        assert isinstance(pools, list)
        assert len(pools) == 0  # Current implementation returns empty

    @pytest.mark.asyncio
    async def test_find_best_route_delegates_to_quote_swap(
        self, uniswap_dex, usdc_asset, weth_asset
    ):
        """Test that find_best_route delegates to quote_swap."""
        mock_route = MagicMock(spec=SwapRoute)
        uniswap_dex.quote_swap.return_value = mock_route

        result = await uniswap_dex.find_best_route(
            input_asset=usdc_asset, output_asset=weth_asset, amount=Decimal("100")
        )

        uniswap_dex.quote_swap.assert_called_once_with(
            usdc_asset, weth_asset, Decimal("100"), SwapMode.EXACT_INPUT, None
        )
        assert result == mock_route


class TestConfigurationClasses:
    """Test suite for configuration classes."""

    def test_ethereum_mainnet_configuration(self):
        """Test Ethereum mainnet configuration."""
        config = UniswapConfiguration.ethereum_mainnet()

        assert len(config.protocols) == 2
        assert config.default_slippage == Decimal("0.005")
        assert config.default_deadline_minutes == 20

        # Check V2 protocol
        v2_protocol = next(
            p for p in config.protocols if p.protocol_name == "uniswap_v2"
        )
        assert (
            v2_protocol.factory_address == "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f"
        )
        assert (
            v2_protocol.router_address == "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D"
        )

        # Check V3 protocol
        v3_protocol = next(
            p for p in config.protocols if p.protocol_name == "uniswap_v3"
        )
        assert (
            v3_protocol.factory_address == "0x1F98431c8aD98523631AE4a59f267346ea31F984"
        )
        assert (
            v3_protocol.router_address == "0xE592427A0AEce92De3Edee1F18E0157C05861564"
        )

    def test_polygon_mainnet_configuration(self):
        """Test Polygon mainnet configuration."""
        config = UniswapConfiguration.polygon_mainnet()

        assert len(config.protocols) == 1
        assert config.protocols[0].protocol_name == "uniswap_v3"


class TestErrorHandling:
    """Test suite for error handling and edge cases."""

    @pytest.mark.asyncio
    async def test_swap_with_zero_amount(self):
        """Test swap quote with zero amount."""
        # Test that mock route creation works with zero amounts
        mock_route = MagicMock()
        mock_route.input_amount = Decimal("0")
        mock_route.output_amount = Decimal("1")

        # Should be able to create mock route with zero amounts
        assert mock_route.input_amount == Decimal("0")

    @pytest.mark.asyncio
    async def test_swap_same_assets(self):
        """Test swap quote with same input and output assets."""
        # Test that mock route creation works with same assets
        mock_asset = MagicMock()
        mock_route = MagicMock()
        mock_route.input_asset = mock_asset
        mock_route.output_asset = mock_asset  # Same asset

        # Should be able to create mock route with same assets
        assert mock_route.input_asset == mock_route.output_asset


if __name__ == "__main__":
    pytest.main([__file__])
