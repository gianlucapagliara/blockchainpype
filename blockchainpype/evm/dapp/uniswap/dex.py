from collections.abc import Sequence
from decimal import Decimal
from typing import TYPE_CHECKING

from financepype.assets.blockchain import BlockchainAsset
from financepype.platforms.blockchain import BlockchainPlatform

from blockchainpype.dapps.router.dex import (
    DecentralizedExchange,
    DexConfiguration,
    ProtocolConfiguration,
)
from blockchainpype.dapps.router.models import SwapMode, SwapRoute
from blockchainpype.evm.blockchain.blockchain import (
    EthereumBlockchain,
    EthereumBlockchainType,
)
from blockchainpype.evm.dapp.uniswap.v2 import UniswapV2
from blockchainpype.evm.dapp.uniswap.v3 import UniswapV3


class UniswapConfiguration(DexConfiguration):
    """Configuration for Uniswap DEX with common network addresses."""

    @classmethod
    def ethereum_mainnet(cls) -> "UniswapConfiguration":
        """Create configuration for Ethereum mainnet."""
        return cls(
            protocols=[
                ProtocolConfiguration(
                    protocol_name="uniswap_v2",
                    factory_address="0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f",
                    router_address="0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D",
                    fee_tiers=[Decimal("0.003")],  # 0.3% fixed fee
                ),
                ProtocolConfiguration(
                    protocol_name="uniswap_v3",
                    factory_address="0x1F98431c8aD98523631AE4a59f267346ea31F984",
                    router_address="0xE592427A0AEce92De3Edee1F18E0157C05861564",
                    fee_tiers=[
                        Decimal("0.0001"),
                        Decimal("0.0005"),
                        Decimal("0.003"),
                        Decimal("0.01"),
                    ],  # V3 fee tiers
                ),
            ],
            default_slippage=Decimal("0.005"),  # 0.5%
            default_deadline_minutes=20,
            minimum_received_multiplier=Decimal("0.95"),
            platform=BlockchainPlatform(
                identifier="ethereum",
                type=EthereumBlockchainType,
                chain_id=1,
            ),
        )

    @classmethod
    def polygon_mainnet(cls) -> "UniswapConfiguration":
        """Create configuration for Polygon mainnet."""
        return cls(
            protocols=[
                ProtocolConfiguration(
                    protocol_name="uniswap_v3",
                    factory_address="0x1F98431c8aD98523631AE4a59f267346ea31F984",
                    router_address="0xE592427A0AEce92De3Edee1F18E0157C05861564",
                    fee_tiers=[
                        Decimal("0.0001"),
                        Decimal("0.0005"),
                        Decimal("0.003"),
                        Decimal("0.01"),
                    ],
                )
            ],
            default_slippage=Decimal("0.005"),
            default_deadline_minutes=20,
            minimum_received_multiplier=Decimal("0.95"),
            platform=BlockchainPlatform(
                identifier="polygon",
                type=EthereumBlockchainType,
                chain_id=137,
            ),
        )


class UniswapDEX(DecentralizedExchange):
    """Uniswap decentralized exchange implementation supporting V2 and V3."""

    if TYPE_CHECKING:
        blockchain: EthereumBlockchain

    def __init__(
        self,
        blockchain: EthereumBlockchain,
        configuration: UniswapConfiguration | None = None,
    ):
        if configuration is None:
            # Auto-detect network and use appropriate configuration
            if blockchain.platform.chain_id == 1:  # Ethereum mainnet
                configuration = UniswapConfiguration.ethereum_mainnet()
            elif blockchain.platform.chain_id == 137:  # Polygon mainnet
                configuration = UniswapConfiguration.polygon_mainnet()
            else:
                raise ValueError(
                    f"No default Uniswap configuration for chain ID {blockchain.platform.chain_id}"
                )

        super().__init__(configuration)

    def _initialize_protocols(self) -> None:
        """Initialize Uniswap protocol strategies."""
        for protocol_config in self.configuration.protocols:
            if protocol_config.protocol_name == "uniswap_v2":
                strategy = UniswapV2(
                    blockchain=self.blockchain,
                    factory_address=protocol_config.factory_address,
                    router_address=protocol_config.router_address,
                )
                self._protocol_strategies["uniswap_v2"] = strategy

            elif protocol_config.protocol_name == "uniswap_v3":
                # For V3, we need to determine the quoter address based on the network
                quoter_address = self._get_quoter_address()
                strategy = UniswapV3(
                    blockchain=self.blockchain,
                    factory_address=protocol_config.factory_address,
                    router_address=protocol_config.router_address,
                    quoter_address=quoter_address,
                )
                self._protocol_strategies["uniswap_v3"] = strategy

    def _get_quoter_address(self) -> str:
        """Get the appropriate quoter address for the current network."""
        if self.blockchain.platform.chain_id == 1:  # Ethereum mainnet
            return "0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6"
        elif self.blockchain.platform.chain_id == 137:  # Polygon mainnet
            return "0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6"
        else:
            # Default quoter address (same across most networks)
            return "0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6"

    async def get_supported_pools(
        self,
        protocol: str | None = None,
    ) -> Sequence[tuple[BlockchainAsset, BlockchainAsset]]:
        """Get a list of supported liquidity pools."""
        # This would typically query the factory contracts to get all pairs
        # For now, return an empty list as this requires extensive on-chain queries
        return []

    async def find_best_route(
        self,
        input_asset: BlockchainAsset,
        output_asset: BlockchainAsset,
        amount: Decimal,
        mode: SwapMode = SwapMode.EXACT_INPUT,
        max_hops: int = 3,
        protocol: str | None = None,
    ) -> SwapRoute:
        """Find the optimal trading route between two assets."""
        # For now, use the quote_swap method which finds the best protocol
        # In a full implementation, this would support multi-hop routing
        return await self.quote_swap(input_asset, output_asset, amount, mode, protocol)
