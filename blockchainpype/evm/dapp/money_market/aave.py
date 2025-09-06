"""
Aave V3 protocol implementation for money market operations.
Provides integration with Aave's lending pool and data provider contracts.
"""

from decimal import Decimal
from typing import cast

from financepype.assets.blockchain import BlockchainAsset

from blockchainpype.dapps.money_market import (
    BorrowingPosition,
    CollateralMode,
    InterestRateMode,
    LendingPosition,
    MarketData,
    ProtocolConfiguration,
    ProtocolImplementation,
    UserAccountData,
)
from blockchainpype.evm.asset import EthereumAsset
from blockchainpype.evm.blockchain.identifier import EthereumAddress
from blockchainpype.evm.dapp.abi import EthereumLocalFileABI
from blockchainpype.evm.dapp.contract import (
    EthereumContractConfiguration,
    EthereumSmartContract,
)
from blockchainpype.evm.transaction import EthereumTransaction

from .money_market import EVMMoneyMarket, EVMMoneyMarketConfiguration


class AaveV3Configuration(EVMMoneyMarketConfiguration):
    """Configuration for Aave V3 protocol."""

    def __init__(self, **data):
        super().__init__(**data)
        # Ensure we have Aave-specific protocol configuration
        if not any("aave" in p.protocol_name.lower() for p in self.protocols):
            raise ValueError(
                "AaveV3Configuration requires at least one Aave protocol configuration"
            )


class AaveV3PoolContract(EthereumSmartContract):
    """Aave V3 Pool contract interface."""

    def __init__(self, address: EthereumAddress):
        from financepype.operators.blockchains.models import BlockchainPlatform

        from blockchainpype.initializer import SupportedBlockchainType

        platform = BlockchainPlatform(
            identifier="ethereum",
            type=SupportedBlockchainType.EVM.value,
            chain_id=1,
        )
        config = EthereumContractConfiguration(
            platform=platform,
            address=address,
            abi_configuration=EthereumLocalFileABI(file_name="aave_v3_pool.json"),
        )
        super().__init__(config)


class AaveV3DataProviderContract(EthereumSmartContract):
    """Aave V3 Pool Data Provider contract interface."""

    def __init__(self, address: EthereumAddress):
        from financepype.operators.blockchains.models import BlockchainPlatform

        from blockchainpype.initializer import SupportedBlockchainType

        platform = BlockchainPlatform(
            identifier="ethereum",
            type=SupportedBlockchainType.EVM.value,
            chain_id=1,
        )
        config = EthereumContractConfiguration(
            platform=platform,
            address=address,
            abi_configuration=EthereumLocalFileABI(
                file_name="aave_v3_data_provider.json"
            ),
        )
        super().__init__(config)


class AaveV3(ProtocolImplementation):
    """Aave V3 protocol implementation."""

    def __init__(self, protocol_config: ProtocolConfiguration, blockchain):
        self.protocol_config = protocol_config
        self.blockchain = blockchain

        # Initialize contracts
        self.pool_contract = AaveV3PoolContract(
            EthereumAddress.from_string(protocol_config.lending_pool_address)
        )
        self.data_provider_contract = AaveV3DataProviderContract(
            EthereumAddress.from_string(protocol_config.data_provider_address)
        )

    async def get_market_data(self, asset: BlockchainAsset) -> MarketData:
        """Get market data for a specific asset from Aave V3."""
        ethereum_asset = cast(EthereumAsset, asset)

        # Initialize contracts if needed
        if not self.pool_contract.contract:
            await self.pool_contract.initialize()
        if not self.data_provider_contract.contract:
            await self.data_provider_contract.initialize()

        # Get reserve data from data provider
        reserve_data = await self.data_provider_contract.functions.getReserveData(
            ethereum_asset.contract_address.raw
        ).call()

        # Get configuration data
        config_data = (
            await self.data_provider_contract.functions.getReserveConfigurationData(
                ethereum_asset.contract_address.raw
            ).call()
        )

        # Parse the data (this would need to be adapted based on actual Aave V3 contract structure)
        return MarketData(
            asset=asset,
            supply_apy=Decimal(str(reserve_data[1])) / Decimal("1e27"),  # liquidityRate
            variable_borrow_apy=Decimal(str(reserve_data[2]))
            / Decimal("1e27"),  # variableBorrowRate
            stable_borrow_apy=Decimal(str(reserve_data[3]))
            / Decimal("1e27"),  # stableBorrowRate
            total_supply=Decimal(str(reserve_data[4]))
            / Decimal(10**ethereum_asset.decimals),
            total_borrows=Decimal(str(reserve_data[5]))
            / Decimal(10**ethereum_asset.decimals),
            utilization_rate=Decimal(str(reserve_data[6]))
            / Decimal("1e4"),  # utilizationRate
            liquidity_rate=Decimal(str(reserve_data[1])) / Decimal("1e27"),
            liquidation_threshold=Decimal(str(config_data[1]))
            / Decimal("1e4"),  # liquidationThreshold
            loan_to_value=Decimal(str(config_data[0])) / Decimal("1e4"),  # ltv
            reserve_factor=Decimal(str(config_data[2]))
            / Decimal("1e4"),  # reserveFactor
            is_borrowing_enabled=bool(config_data[3]),
            is_stable_rate_enabled=bool(config_data[4]),
            is_frozen=bool(config_data[5]),
            protocol=self.protocol_config.protocol_name,
        )

    async def get_user_account_data(self, user_address: str) -> UserAccountData:
        """Get user's account data from Aave V3."""
        if not self.pool_contract.contract:
            await self.pool_contract.initialize()

        # Get user account data
        account_data = await self.pool_contract.functions.getUserAccountData(
            user_address
        ).call()

        return UserAccountData(
            total_collateral_value=Decimal(str(account_data[0]))
            / Decimal("1e8"),  # totalCollateralETH
            total_debt_value=Decimal(str(account_data[1]))
            / Decimal("1e8"),  # totalDebtETH
            available_borrow_value=Decimal(str(account_data[2]))
            / Decimal("1e8"),  # availableBorrowsETH
            current_liquidation_threshold=Decimal(str(account_data[3]))
            / Decimal("1e4"),  # currentLiquidationThreshold
            loan_to_value=Decimal(str(account_data[4])) / Decimal("1e4"),  # ltv
            health_factor=Decimal(str(account_data[5]))
            / Decimal("1e18"),  # healthFactor
            protocol=self.protocol_config.protocol_name,
        )

    async def get_lending_positions(self, user_address: str) -> list[LendingPosition]:
        """Get user's lending positions from Aave V3."""
        if not self.data_provider_contract.contract:
            await self.data_provider_contract.initialize()

        # Get all user's aToken balances
        # This would require iterating through available reserves and checking balances
        # For now, return empty list as this requires more complex implementation
        return []

    async def get_borrowing_positions(
        self, user_address: str
    ) -> list[BorrowingPosition]:
        """Get user's borrowing positions from Aave V3."""
        if not self.data_provider_contract.contract:
            await self.data_provider_contract.initialize()

        # Get all user's debt token balances
        # This would require iterating through available reserves and checking debt balances
        # For now, return empty list as this requires more complex implementation
        return []

    async def build_supply_transaction(
        self,
        asset: BlockchainAsset,
        amount: Decimal,
        user_address: str,
        enable_as_collateral: bool = True,
    ) -> EthereumTransaction:
        """Build transaction to supply assets to Aave V3."""
        ethereum_asset = cast(EthereumAsset, asset)

        if not self.pool_contract.contract:
            await self.pool_contract.initialize()

        # Convert amount to raw units
        raw_amount = int(amount * Decimal(10**ethereum_asset.decimals))

        # Build supply transaction
        function_call = self.pool_contract.functions.supply(
            ethereum_asset.contract_address.raw,  # asset
            raw_amount,  # amount
            user_address,  # onBehalfOf
            0,  # referralCode
        )

        return EthereumTransaction(
            to=self.pool_contract.address,
            data=function_call.build_transaction()["data"],
            value=0,
        )

    async def build_withdraw_transaction(
        self,
        asset: BlockchainAsset,
        amount: Decimal,
        user_address: str,
    ) -> EthereumTransaction:
        """Build transaction to withdraw assets from Aave V3."""
        ethereum_asset = cast(EthereumAsset, asset)

        if not self.pool_contract.contract:
            await self.pool_contract.initialize()

        # Convert amount to raw units (use max uint256 for full withdrawal)
        raw_amount = int(amount * Decimal(10**ethereum_asset.decimals))

        # Build withdraw transaction
        function_call = self.pool_contract.functions.withdraw(
            ethereum_asset.contract_address.raw,  # asset
            raw_amount,  # amount
            user_address,  # to
        )

        return EthereumTransaction(
            to=self.pool_contract.address,
            data=function_call.build_transaction()["data"],
            value=0,
        )

    async def build_borrow_transaction(
        self,
        asset: BlockchainAsset,
        amount: Decimal,
        interest_rate_mode: InterestRateMode,
        user_address: str,
    ) -> EthereumTransaction:
        """Build transaction to borrow assets from Aave V3."""
        ethereum_asset = cast(EthereumAsset, asset)

        if not self.pool_contract.contract:
            await self.pool_contract.initialize()

        # Convert amount to raw units
        raw_amount = int(amount * Decimal(10**ethereum_asset.decimals))

        # Convert interest rate mode (1 = stable, 2 = variable)
        rate_mode = 1 if interest_rate_mode == InterestRateMode.STABLE else 2

        # Build borrow transaction
        function_call = self.pool_contract.functions.borrow(
            ethereum_asset.contract_address.raw,  # asset
            raw_amount,  # amount
            rate_mode,  # interestRateMode
            0,  # referralCode
            user_address,  # onBehalfOf
        )

        return EthereumTransaction(
            to=self.pool_contract.address,
            data=function_call.build_transaction()["data"],
            value=0,
        )

    async def build_repay_transaction(
        self,
        asset: BlockchainAsset,
        amount: Decimal,
        interest_rate_mode: InterestRateMode,
        user_address: str,
        repay_all: bool = False,
    ) -> EthereumTransaction:
        """Build transaction to repay borrowed assets to Aave V3."""
        ethereum_asset = cast(EthereumAsset, asset)

        if not self.pool_contract.contract:
            await self.pool_contract.initialize()

        # Convert amount to raw units (use max uint256 for full repayment)
        if repay_all:
            raw_amount = 2**256 - 1  # Max uint256
        else:
            raw_amount = int(amount * Decimal(10**ethereum_asset.decimals))

        # Convert interest rate mode (1 = stable, 2 = variable)
        rate_mode = 1 if interest_rate_mode == InterestRateMode.STABLE else 2

        # Build repay transaction
        function_call = self.pool_contract.functions.repay(
            ethereum_asset.contract_address.raw,  # asset
            raw_amount,  # amount
            rate_mode,  # interestRateMode
            user_address,  # onBehalfOf
        )

        return EthereumTransaction(
            to=self.pool_contract.address,
            data=function_call.build_transaction()["data"],
            value=0,
        )

    async def build_collateral_transaction(
        self,
        asset: BlockchainAsset,
        mode: CollateralMode,
        user_address: str,
    ) -> EthereumTransaction:
        """Build transaction to enable/disable asset as collateral in Aave V3."""
        ethereum_asset = cast(EthereumAsset, asset)

        if not self.pool_contract.contract:
            await self.pool_contract.initialize()

        # Build collateral transaction
        function_call = self.pool_contract.functions.setUserUseReserveAsCollateral(
            ethereum_asset.contract_address.raw,  # asset
            mode == CollateralMode.ENABLED,  # useAsCollateral
        )

        return EthereumTransaction(
            to=self.pool_contract.address,
            data=function_call.build_transaction()["data"],
            value=0,
        )

    async def build_liquidation_transaction(
        self,
        collateral_asset: BlockchainAsset,
        debt_asset: BlockchainAsset,
        user_to_liquidate: str,
        debt_to_cover: Decimal,
        receive_collateral: bool = True,
    ) -> EthereumTransaction:
        """Build transaction to liquidate an undercollateralized position in Aave V3."""
        debt_ethereum_asset = cast(EthereumAsset, debt_asset)
        collateral_ethereum_asset = cast(EthereumAsset, collateral_asset)

        if not self.pool_contract.contract:
            await self.pool_contract.initialize()

        # Convert debt amount to raw units
        raw_debt_amount = int(debt_to_cover * Decimal(10**debt_ethereum_asset.decimals))

        # Build liquidation transaction
        function_call = self.pool_contract.functions.liquidationCall(
            collateral_ethereum_asset.contract_address.raw,  # collateralAsset
            debt_ethereum_asset.contract_address.raw,  # debtAsset
            user_to_liquidate,  # user
            raw_debt_amount,  # debtToCover
            receive_collateral,  # receiveAToken
        )

        return EthereumTransaction(
            to=self.pool_contract.address,
            data=function_call.build_transaction()["data"],
            value=0,
        )


class AaveV3MoneyMarket(EVMMoneyMarket):
    """Aave V3 money market implementation."""

    def _initialize_protocols(self) -> None:
        """Initialize Aave V3 protocol strategies."""
        for protocol_config in self.configuration.protocols:
            if "aave" in protocol_config.protocol_name.lower():
                self._protocol_strategies[protocol_config.protocol_name] = AaveV3(
                    protocol_config, self.blockchain
                )
