from decimal import Decimal
from typing import cast

from financepype.assets.blockchain import BlockchainAsset
from financepype.platforms.blockchain import BlockchainPlatform
from web3.types import TxParams

from blockchainpype.dapps.router.dex import ProtocolImplementation
from blockchainpype.dapps.router.models import SwapHop, SwapMode, SwapRoute
from blockchainpype.evm.asset import EthereumAsset
from blockchainpype.evm.blockchain.blockchain import (
    EthereumBlockchain,
    EthereumBlockchainType,
)
from blockchainpype.evm.blockchain.identifier import EthereumAddress
from blockchainpype.evm.dapp.abi import EthereumLocalFileABI
from blockchainpype.evm.dapp.contract import (
    EthereumContractConfiguration,
    EthereumSmartContract,
)
from blockchainpype.evm.transaction import EthereumTransaction
from blockchainpype.evm.wallet.wallet import EthereumWallet


class UniswapV3(ProtocolImplementation):
    """Uniswap V3 protocol implementation for DEX routing."""

    def __init__(
        self,
        blockchain: EthereumBlockchain,
        factory_address: str,
        router_address: str,
        quoter_address: str,
    ):
        self.blockchain = blockchain
        self.factory_address = factory_address
        self.router_address = router_address
        self.quoter_address = quoter_address

        # Load ABIs from common directory
        factory_abi = EthereumLocalFileABI(file_name="uniswap_v3/UniswapV3Factory.json")
        router_abi = EthereumLocalFileABI(file_name="uniswap_v3/ISwapRouter.json")
        quoter_abi = EthereumLocalFileABI(file_name="uniswap_v3/IQuoter.json")

        ethereum_platform = BlockchainPlatform(
            identifier="ethereum",
            type=EthereumBlockchainType,
            chain_id=1,
        )

        self.factory_contract = EthereumSmartContract(
            EthereumContractConfiguration(
                address=EthereumAddress.from_string(factory_address),
                abi_configuration=factory_abi,
                platform=ethereum_platform,
            )
        )

        self.router_contract = EthereumSmartContract(
            EthereumContractConfiguration(
                address=EthereumAddress.from_string(router_address),
                abi_configuration=router_abi,
                platform=ethereum_platform,
            )
        )

        self.quoter_contract = EthereumSmartContract(
            EthereumContractConfiguration(
                address=EthereumAddress.from_string(quoter_address),
                abi_configuration=quoter_abi,
                platform=ethereum_platform,
            )
        )

        # Common fee tiers for Uniswap V3
        self.fee_tiers = [100, 500, 3000, 10000]  # 0.01%, 0.05%, 0.3%, 1%

    async def _ensure_contracts_initialized(self):
        """Ensure factory, router, and quoter contracts are initialized."""
        if not self.factory_contract.is_initialized:
            await self.factory_contract.initialize()
        if not self.router_contract.is_initialized:
            await self.router_contract.initialize()
        if not self.quoter_contract.is_initialized:
            await self.quoter_contract.initialize()

    async def quote_swap(
        self,
        input_asset: BlockchainAsset,
        output_asset: BlockchainAsset,
        amount: Decimal,
        mode: SwapMode = SwapMode.EXACT_INPUT,
    ) -> SwapRoute:
        """Get a quote for swapping between two assets on Uniswap V3."""
        input_asset = cast(EthereumAsset, input_asset)
        output_asset = cast(EthereumAsset, output_asset)

        # Ensure asset data is initialized
        if input_asset.data is None:
            await input_asset.initialize_data()
        if output_asset.data is None:
            await output_asset.initialize_data()
        # Assert data is not None after initialization
        assert input_asset.data is not None, "Input asset data should be initialized"
        assert output_asset.data is not None, "Output asset data should be initialized"

        # Ensure contracts are initialized
        await self._ensure_contracts_initialized()

        # Find the best pool across different fee tiers
        best_quote = None
        best_fee_tier = None

        for fee_tier in self.fee_tiers:
            try:
                # Check if pool exists
                pool_address = await self._get_pool(input_asset, output_asset, fee_tier)
                if (
                    not pool_address
                    or pool_address == "0x0000000000000000000000000000000000000000"
                ):
                    continue

                # Get quote for this fee tier
                if mode == SwapMode.EXACT_INPUT:
                    amount_raw = int(amount * (10**input_asset.data.decimals))
                    quote_raw = (
                        await self.quoter_contract.functions.quoteExactInputSingle(
                            input_asset.address.raw,
                            output_asset.address.raw,
                            fee_tier,
                            amount_raw,
                            0,  # sqrtPriceLimitX96 (0 = no limit)
                        ).call()
                    )
                    quote_amount = Decimal(quote_raw) / (10**output_asset.data.decimals)
                else:
                    amount_raw = int(amount * (10**output_asset.data.decimals))
                    quote_raw = (
                        await self.quoter_contract.functions.quoteExactOutputSingle(
                            input_asset.address.raw,
                            output_asset.address.raw,
                            fee_tier,
                            amount_raw,
                            0,  # sqrtPriceLimitX96 (0 = no limit)
                        ).call()
                    )
                    quote_amount = Decimal(quote_raw) / (10**input_asset.data.decimals)

                # Keep the best quote (highest output for exact input, lowest input for exact output)
                if (
                    best_quote is None
                    or (mode == SwapMode.EXACT_INPUT and quote_amount > best_quote)
                    or (mode == SwapMode.EXACT_OUTPUT and quote_amount < best_quote)
                ):
                    best_quote = quote_amount
                    best_fee_tier = fee_tier

            except Exception:
                continue

        if best_quote is None or best_fee_tier is None:
            raise ValueError(
                f"No valid pool found for {input_asset.data.symbol}/{output_asset.data.symbol}"
            )

        # Create swap route
        if mode == SwapMode.EXACT_INPUT:
            input_amount = amount
            output_amount = best_quote
        else:
            input_amount = best_quote
            output_amount = amount

        swap_hop = SwapHop(
            input_asset=input_asset,
            input_amount=input_amount,
            output_asset=output_asset,
            output_amount=output_amount,
            are_amounts_raw=False,
        )

        return SwapRoute(
            input_asset=input_asset,
            input_amount=input_amount,
            output_asset=output_asset,
            output_amount=output_amount,
            are_amounts_raw=False,
            sequence=[swap_hop],
            mode=mode,
            max_slippage=Decimal("0.005"),  # 0.5% default slippage
            taxes=Decimal(best_fee_tier)
            / Decimal("1000000"),  # Convert fee tier to decimal
            protocol=f"uniswap_v3_{best_fee_tier}",
        )

    async def get_reserves(
        self,
        asset_a: BlockchainAsset,
        asset_b: BlockchainAsset,
    ) -> tuple[Decimal, Decimal]:
        """Get the current liquidity for a pair of assets (approximation using 0.3% pool)."""
        asset_a = cast(EthereumAsset, asset_a)
        asset_b = cast(EthereumAsset, asset_b)

        # Ensure asset data is initialized
        if asset_a.data is None:
            await asset_a.initialize_data()
        if asset_b.data is None:
            await asset_b.initialize_data()
        # Assert data is not None after initialization
        assert asset_a.data is not None, "Asset A data should be initialized"
        assert asset_b.data is not None, "Asset B data should be initialized"

        # Try to find a pool in the most common fee tier (0.3%)
        pool_address = await self._get_pool(asset_a, asset_b, 3000)
        if (
            not pool_address
            or pool_address == "0x0000000000000000000000000000000000000000"
        ):
            # Try other fee tiers
            for fee_tier in [500, 10000, 100]:
                pool_address = await self._get_pool(asset_a, asset_b, fee_tier)
                if (
                    pool_address
                    and pool_address != "0x0000000000000000000000000000000000000000"
                ):
                    break
            else:
                raise ValueError(
                    f"No pool found for {asset_a.data.symbol}/{asset_b.data.symbol}"
                )

        # Create pool contract
        pool_abi = EthereumLocalFileABI(file_name="uniswap_v3/UniswapV3Pool.json")
        ethereum_platform = BlockchainPlatform(
            identifier="ethereum",
            type=EthereumBlockchainType,
            chain_id=1,
        )

        pool_contract = EthereumSmartContract(
            EthereumContractConfiguration(
                address=EthereumAddress.from_string(pool_address),
                abi_configuration=pool_abi,
                platform=ethereum_platform,
            )
        )

        # Initialize pool contract
        await pool_contract.initialize()

        # Get liquidity (this is a simplified approximation)
        liquidity = await pool_contract.functions.liquidity().call()
        slot0 = await pool_contract.functions.slot0().call()
        sqrt_price_x96 = slot0[0]

        # Convert sqrt price to regular price
        price = (sqrt_price_x96 / (2**96)) ** 2

        # Approximate reserves based on liquidity and price
        # This is a simplification - actual V3 reserves are distributed across price ranges
        token0 = await pool_contract.functions.token0().call()

        if asset_a.address.raw.lower() == token0.lower():
            # asset_a is token0, asset_b is token1
            reserve_a = (
                Decimal(liquidity) / Decimal(price) / (10**asset_a.data.decimals)
            )
            reserve_b = (
                Decimal(liquidity) * Decimal(price) / (10**asset_b.data.decimals)
            )
        else:
            # asset_a is token1, asset_b is token0
            reserve_a = (
                Decimal(liquidity) * Decimal(price) / (10**asset_a.data.decimals)
            )
            reserve_b = (
                Decimal(liquidity) / Decimal(price) / (10**asset_b.data.decimals)
            )

        return (reserve_a, reserve_b)

    async def build_swap_transaction(
        self,
        route: SwapRoute,
        wallet: EthereumWallet,
        recipient: str | None = None,
    ) -> TxParams:
        """Build a swap transaction for the given route."""
        if len(route.sequence) != 1:
            raise ValueError(
                "Multi-hop swaps not implemented for Uniswap V3 in this version"
            )

        hop = route.sequence[0]
        input_asset = cast(EthereumAsset, hop.input_asset)
        output_asset = cast(EthereumAsset, hop.output_asset)

        # Ensure asset data is initialized
        if input_asset.data is None:
            await input_asset.initialize_data()
        if output_asset.data is None:
            await output_asset.initialize_data()
        # Assert data is not None after initialization
        assert input_asset.data is not None, "Input asset data should be initialized"
        assert output_asset.data is not None, "Output asset data should be initialized"

        # Extract fee tier from protocol name
        fee_tier = int(route.protocol.split("_")[-1])

        # Calculate deadline (20 minutes from now)
        deadline = int(self.blockchain.current_timestamp) + (20 * 60)

        # Use wallet address as recipient if not specified
        if recipient is None:
            recipient = wallet.address.raw

        if route.mode == SwapMode.EXACT_INPUT:
            # exactInputSingle
            amount_in = int(hop.input_amount * (10**input_asset.data.decimals))
            amount_out_minimum = int(
                hop.output_amount
                * (1 - route.max_slippage)
                * (10**output_asset.data.decimals)
            )

            params = {
                "tokenIn": input_asset.address,
                "tokenOut": output_asset.address,
                "fee": fee_tier,
                "recipient": recipient,
                "deadline": deadline,
                "amountIn": amount_in,
                "amountOutMinimum": amount_out_minimum,
                "sqrtPriceLimitX96": 0,
            }

            function_name = "exactInputSingle"
            args = [params]
        else:
            # exactOutputSingle
            amount_out = int(hop.output_amount * (10**output_asset.data.decimals))
            amount_in_maximum = int(
                hop.input_amount
                * (1 + route.max_slippage)
                * (10**input_asset.data.decimals)
            )

            params = {
                "tokenIn": input_asset.address,
                "tokenOut": output_asset.address,
                "fee": fee_tier,
                "recipient": recipient,
                "deadline": deadline,
                "amountOut": amount_out,
                "amountInMaximum": amount_in_maximum,
                "sqrtPriceLimitX96": 0,
            }

            function_name = "exactOutputSingle"
            args = [params]

        # Ensure contracts are initialized
        await self._ensure_contracts_initialized()

        # Build transaction using Web3 contract function
        if function_name == "exactInputSingle":
            contract_function = self.router_contract.functions.exactInputSingle(*args)
        elif function_name == "exactOutputSingle":
            contract_function = self.router_contract.functions.exactOutputSingle(*args)
        else:
            raise ValueError(f"Unsupported function: {function_name}")

        # Build the transaction using wallet's build_transaction method
        return await wallet.build_transaction(function=contract_function)

    async def create_swap_transaction(
        self,
        route: SwapRoute,
        wallet: EthereumWallet,
        recipient: str | None = None,
        client_operation_id: str | None = None,
    ) -> EthereumTransaction:
        """Create and sign a swap transaction for the given route.

        Args:
            route: The swap route to execute
            wallet: The wallet to use for signing the transaction
            recipient: Optional recipient address (defaults to wallet address)
            client_operation_id: Optional client operation ID for tracking

        Returns:
            EthereumTransaction: The created and signed transaction ready for broadcast
        """
        if client_operation_id is None:
            input_asset_id = cast(EthereumAsset, route.input_asset).address.raw[:8]
            output_asset_id = cast(EthereumAsset, route.output_asset).address.raw[:8]
            fee_tier = route.protocol.split("_")[-1]
            client_operation_id = f"uniswap_v3_swap_{input_asset_id}_{output_asset_id}_{fee_tier}"

        # Build the transaction parameters
        tx_params = await self.build_swap_transaction(route, wallet, recipient)

        # Sign and create the transaction object
        return wallet.sign_and_send_transaction(
            client_operation_id=client_operation_id,
            tx_data=dict(tx_params),
            auto_assign_nonce=True,
        )

    async def execute_swap(
        self,
        input_asset: BlockchainAsset,
        output_asset: BlockchainAsset,
        amount: Decimal,
        wallet: EthereumWallet,
        mode: SwapMode = SwapMode.EXACT_INPUT,
        recipient: str | None = None,
        client_operation_id: str | None = None,
    ) -> EthereumTransaction:
        """Execute a swap between two assets.

        Args:
            input_asset: The asset to swap from
            output_asset: The asset to swap to
            amount: The amount to swap
            wallet: The wallet to use for the transaction
            mode: The swap mode (EXACT_INPUT or EXACT_OUTPUT)
            recipient: Optional recipient address (defaults to wallet address)
            client_operation_id: Optional client operation ID for tracking

        Returns:
            EthereumTransaction: The executed swap transaction
        """
        # Get a quote for the swap
        route = await self.quote_swap(input_asset, output_asset, amount, mode)

        # Create and execute the transaction
        return await self.create_swap_transaction(
            route=route,
            wallet=wallet,
            recipient=recipient,
            client_operation_id=client_operation_id,
        )

    async def _get_pool(
        self, asset_a: EthereumAsset, asset_b: EthereumAsset, fee: int
    ) -> str:
        """Get the pool address for two assets and a specific fee tier."""
        await self._ensure_contracts_initialized()
        return await self.factory_contract.functions.getPool(
            asset_a.address.raw, asset_b.address.raw, fee
        ).call()
