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


class UniswapV2(ProtocolImplementation):
    """Uniswap V2 protocol implementation for DEX routing."""

    def __init__(
        self, blockchain: EthereumBlockchain, factory_address: str, router_address: str
    ):
        self.blockchain = blockchain
        self.factory_address = factory_address
        self.router_address = router_address

        # Load ABIs from common directory
        factory_abi = EthereumLocalFileABI(file_name="UniswapV2Factory.json")
        router_abi = EthereumLocalFileABI(file_name="UniswapV2Router02.json")

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

    async def _ensure_contracts_initialized(self):
        """Ensure factory and router contracts are initialized."""
        if not self.factory_contract.is_initialized:
            await self.factory_contract.initialize()
        if not self.router_contract.is_initialized:
            await self.router_contract.initialize()

    async def quote_swap(
        self,
        input_asset: BlockchainAsset,
        output_asset: BlockchainAsset,
        amount: Decimal,
        mode: SwapMode = SwapMode.EXACT_INPUT,
    ) -> SwapRoute:
        """Get a quote for swapping between two assets on Uniswap V2."""
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

        # Get pair address
        pair_address = await self._get_pair(input_asset, output_asset)
        if (
            not pair_address
            or pair_address == "0x0000000000000000000000000000000000000000"
        ):
            raise ValueError(
                f"No pair found for {input_asset.data.symbol}/{output_asset.data.symbol}"
            )

        # Get reserves
        reserve_a, reserve_b = await self.get_reserves(input_asset, output_asset)

        if mode == SwapMode.EXACT_INPUT:
            # Calculate output amount using Uniswap V2 formula: x * y = k
            input_amount_raw = int(amount * (10**input_asset.data.decimals))
            output_amount_raw = await self._get_amount_out(
                input_amount_raw, reserve_a, reserve_b
            )
            output_amount = Decimal(output_amount_raw) / (
                10**output_asset.data.decimals
            )
        else:
            # Calculate input amount for exact output
            output_amount_raw = int(amount * (10**output_asset.data.decimals))
            input_amount_raw = await self._get_amount_in(
                output_amount_raw, reserve_a, reserve_b
            )
            amount = Decimal(input_amount_raw) / (10**input_asset.data.decimals)
            output_amount = amount

        # Create swap hop
        swap_hop = SwapHop(
            input_asset=input_asset,
            input_amount=amount,
            output_asset=output_asset,
            output_amount=output_amount,
            are_amounts_raw=False,
        )

        return SwapRoute(
            input_asset=input_asset,
            input_amount=amount,
            output_asset=output_asset,
            output_amount=output_amount,
            are_amounts_raw=False,
            sequence=[swap_hop],
            mode=mode,
            max_slippage=Decimal("0.005"),  # 0.5% default slippage
            taxes=Decimal("0.003"),  # 0.3% Uniswap V2 fee
            protocol="uniswap_v2",
        )

    async def get_reserves(
        self,
        asset_a: BlockchainAsset,
        asset_b: BlockchainAsset,
    ) -> tuple[Decimal, Decimal]:
        """Get the current reserves for a pair of assets."""
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

        pair_address = await self._get_pair(asset_a, asset_b)
        if (
            not pair_address
            or pair_address == "0x0000000000000000000000000000000000000000"
        ):
            raise ValueError(
                f"No pair found for {asset_a.data.symbol}/{asset_b.data.symbol}"
            )

        # Create pair contract
        pair_abi = EthereumLocalFileABI(file_name="UniswapV2Pair.json")
        ethereum_platform = BlockchainPlatform(
            identifier="ethereum",
            type=EthereumBlockchainType,
            chain_id=1,
        )

        pair_contract = EthereumSmartContract(
            EthereumContractConfiguration(
                address=EthereumAddress.from_string(pair_address),
                abi_configuration=pair_abi,
                platform=ethereum_platform,
            )
        )

        # Initialize pair contract
        await pair_contract.initialize()

        # Get reserves from pair contract
        reserves = await pair_contract.functions.getReserves().call()
        reserve0, reserve1, _ = reserves

        # Determine which asset is token0 and token1
        token0 = await pair_contract.functions.token0().call()

        if asset_a.address.raw.lower() == token0.lower():
            return (
                Decimal(reserve0) / (10**asset_a.data.decimals),
                Decimal(reserve1) / (10**asset_b.data.decimals),
            )
        else:
            return (
                Decimal(reserve1) / (10**asset_a.data.decimals),
                Decimal(reserve0) / (10**asset_b.data.decimals),
            )

    async def build_swap_transaction(
        self,
        route: SwapRoute,
        wallet: EthereumWallet,
        recipient: str | None = None,
    ) -> TxParams:
        """Build a swap transaction for the given route."""
        if len(route.sequence) != 1:
            raise ValueError(
                "Uniswap V2 only supports single-hop swaps in this implementation"
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

        # Calculate minimum amount out with slippage protection
        min_amount_out = int(
            hop.output_amount
            * (1 - route.max_slippage)
            * (10**output_asset.data.decimals)
        )

        # Calculate deadline (20 minutes from now)
        deadline = int(self.blockchain.current_timestamp) + (20 * 60)

        # Use wallet address as recipient if not specified
        if recipient is None:
            recipient = wallet.address.raw

        if route.mode == SwapMode.EXACT_INPUT:
            # swapExactTokensForTokens
            amount_in = int(hop.input_amount * (10**input_asset.data.decimals))
            path = [input_asset.address, output_asset.address]

            function_name = "swapExactTokensForTokens"
            args = [amount_in, min_amount_out, path, recipient, deadline]
        else:
            # swapTokensForExactTokens
            amount_out = int(hop.output_amount * (10**output_asset.data.decimals))
            max_amount_in = int(
                hop.input_amount
                * (1 + route.max_slippage)
                * (10**input_asset.data.decimals)
            )
            path = [input_asset.address, output_asset.address]

            function_name = "swapTokensForExactTokens"
            args = [amount_out, max_amount_in, path, recipient, deadline]

        # Ensure contracts are initialized
        await self._ensure_contracts_initialized()

        # Build transaction using Web3 contract function
        if function_name == "swapExactTokensForTokens":
            contract_function = self.router_contract.functions.swapExactTokensForTokens(
                *args
            )
        elif function_name == "swapTokensForExactTokens":
            contract_function = self.router_contract.functions.swapTokensForExactTokens(
                *args
            )
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
            client_operation_id = f"uniswap_v2_swap_{input_asset_id}_{output_asset_id}"

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

    async def _get_pair(self, asset_a: EthereumAsset, asset_b: EthereumAsset) -> str:
        """Get the pair address for two assets."""
        await self._ensure_contracts_initialized()
        return await self.factory_contract.functions.getPair(
            asset_a.address.raw, asset_b.address.raw
        ).call()

    async def _get_amount_out(
        self, amount_in: int, reserve_in: Decimal, reserve_out: Decimal
    ) -> int:
        """Calculate output amount using Uniswap V2 formula."""
        # Convert reserves to int for calculation
        reserve_in_int = int(reserve_in)
        reserve_out_int = int(reserve_out)

        # Uniswap V2 formula: amount_out = (amount_in * 997 * reserve_out) / (reserve_in * 1000 + amount_in * 997)
        amount_in_with_fee = amount_in * 997
        numerator = amount_in_with_fee * reserve_out_int
        denominator = reserve_in_int * 1000 + amount_in_with_fee

        return numerator // denominator

    async def _get_amount_in(
        self, amount_out: int, reserve_in: Decimal, reserve_out: Decimal
    ) -> int:
        """Calculate input amount required for exact output using Uniswap V2 formula."""
        # Convert reserves to int for calculation
        reserve_in_int = int(reserve_in)
        reserve_out_int = int(reserve_out)

        # Uniswap V2 formula: amount_in = (reserve_in * amount_out * 1000) / ((reserve_out - amount_out) * 997) + 1
        numerator = reserve_in_int * amount_out * 1000
        denominator = (reserve_out_int - amount_out) * 997

        return (numerator // denominator) + 1
