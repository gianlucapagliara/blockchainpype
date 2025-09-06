"""
Solend protocol implementation for money market operations on Solana.
Provides integration with Solend's lending program.
"""

from decimal import Decimal
from typing import cast

from financepype.assets.blockchain import BlockchainAsset
from solders.instruction import AccountMeta

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
from blockchainpype.solana.asset import SolanaAsset
from blockchainpype.solana.blockchain.identifier import SolanaAddress
from blockchainpype.solana.dapp.idl import SolanaLocalFileIDL
from blockchainpype.solana.dapp.program import SolanaProgram, SolanaProgramConfiguration
from blockchainpype.solana.transaction import SolanaTransaction

from .money_market import SolanaMoneyMarket, SolanaMoneyMarketConfiguration


class SolendConfiguration(SolanaMoneyMarketConfiguration):
    """Configuration for Solend protocol."""

    def __init__(self, **data):
        super().__init__(**data)
        # Ensure we have Solend-specific protocol configuration
        if not any(p.protocol_name.lower() == "solend" for p in self.protocols):
            raise ValueError(
                "SolendConfiguration requires at least one Solend protocol configuration"
            )


class SolendProgram(SolanaProgram):
    """Solend program interface."""

    def __init__(self, address: SolanaAddress, platform=None):
        from financepype.operators.blockchains.models import BlockchainPlatform

        from blockchainpype.initializer import SupportedBlockchainType

        if platform is None:
            platform = BlockchainPlatform(
                identifier="solana",
                type=SupportedBlockchainType.SOLANA.value,
                chain_id=None,
            )

        config = SolanaProgramConfiguration(
            platform=platform,
            address=address,
            idl_configuration=SolanaLocalFileIDL(file_name="solend.json"),
        )
        super().__init__(config)


class Solend(ProtocolImplementation):
    """Solend protocol implementation."""

    def __init__(
        self, protocol_config: ProtocolConfiguration, blockchain, platform=None
    ):
        self.protocol_config = protocol_config
        self.blockchain = blockchain

        # Initialize program
        self.program = SolendProgram(
            SolanaAddress.from_string(protocol_config.lending_pool_address), platform
        )

    async def get_market_data(self, asset: BlockchainAsset) -> MarketData:
        """Get market data for a specific asset from Solend."""
        solana_asset = cast(SolanaAsset, asset)

        # Initialize program if needed
        if not self.program.is_initialized:
            await self.program.initialize()

        # This would require reading market state accounts
        # For now, return dummy data as this requires more complex implementation
        return MarketData(
            asset=asset,
            supply_apy=Decimal("0.05"),  # 5% APY
            variable_borrow_apy=Decimal("0.08"),  # 8% APY
            stable_borrow_apy=Decimal("0.07"),  # 7% APY
            total_supply=Decimal("1000000"),
            total_borrows=Decimal("500000"),
            utilization_rate=Decimal("0.5"),  # 50%
            liquidity_rate=Decimal("0.05"),
            liquidation_threshold=Decimal("0.8"),  # 80%
            loan_to_value=Decimal("0.75"),  # 75%
            reserve_factor=Decimal("0.1"),  # 10%
            is_borrowing_enabled=True,
            is_stable_rate_enabled=True,
            is_frozen=False,
            protocol=self.protocol_config.protocol_name,
        )

    async def get_user_account_data(self, user_address: str) -> UserAccountData:
        """Get user's account data from Solend."""
        if not self.program.is_initialized:
            await self.program.initialize()

        # This would require reading user obligation accounts
        # For now, return dummy data as this requires more complex implementation
        return UserAccountData(
            total_collateral_value=Decimal("10000"),
            total_debt_value=Decimal("5000"),
            available_borrow_value=Decimal("2500"),
            current_liquidation_threshold=Decimal("0.8"),
            loan_to_value=Decimal("0.75"),
            health_factor=Decimal("1.6"),
            protocol=self.protocol_config.protocol_name,
        )

    async def get_lending_positions(self, user_address: str) -> list[LendingPosition]:
        """Get user's lending positions from Solend."""
        if not self.program.is_initialized:
            await self.program.initialize()

        # This would require reading user's deposit accounts
        # For now, return empty list as this requires more complex implementation
        return []

    async def get_borrowing_positions(
        self, user_address: str
    ) -> list[BorrowingPosition]:
        """Get user's borrowing positions from Solend."""
        if not self.program.is_initialized:
            await self.program.initialize()

        # This would require reading user's borrow accounts
        # For now, return empty list as this requires more complex implementation
        return []

    async def build_supply_transaction(
        self,
        asset: BlockchainAsset,
        amount: Decimal,
        user_address: str,
        enable_as_collateral: bool = True,
    ) -> SolanaTransaction:
        """Build transaction to supply assets to Solend."""
        solana_asset = cast(SolanaAsset, asset)

        if not self.program.is_initialized:
            await self.program.initialize()

        # Convert amount to raw units
        raw_amount = int(amount * Decimal(10**solana_asset.decimals))

        # Build accounts for supply instruction
        accounts = [
            AccountMeta(
                pubkey=SolanaAddress.from_string(user_address).raw,
                is_signer=True,
                is_writable=True,
            ),
            # Additional accounts would be needed based on Solend's instruction format
        ]

        # Create supply instruction
        instruction = self.program.create_instruction(
            name="deposit", accounts=accounts, data=raw_amount.to_bytes(8, "little")
        )

        from datetime import datetime

        from financepype.operators.blockchains.models import BlockchainPlatform
        from financepype.owners.owner import OwnerIdentifier

        from blockchainpype.initializer import SupportedBlockchainType

        test_platform = BlockchainPlatform(
            identifier="solana",
            type=SupportedBlockchainType.SOLANA.value,
            chain_id=None,
        )

        return SolanaTransaction(
            client_operation_id="test-operation",
            owner_identifier=OwnerIdentifier(platform=test_platform, name=user_address),
            creation_timestamp=datetime.now().timestamp(),
            instructions=[instruction],
            recent_blockhash="",  # Would be filled by the blockchain
        )

    async def build_withdraw_transaction(
        self,
        asset: BlockchainAsset,
        amount: Decimal,
        user_address: str,
    ) -> SolanaTransaction:
        """Build transaction to withdraw assets from Solend."""
        solana_asset = cast(SolanaAsset, asset)

        if not self.program.is_initialized:
            await self.program.initialize()

        # Convert amount to raw units
        raw_amount = int(amount * Decimal(10**solana_asset.decimals))

        # Build accounts for withdraw instruction
        accounts = [
            AccountMeta(
                pubkey=SolanaAddress.from_string(user_address).raw,
                is_signer=True,
                is_writable=True,
            ),
            # Additional accounts would be needed based on Solend's instruction format
        ]

        # Create withdraw instruction
        instruction = self.program.create_instruction(
            name="withdraw", accounts=accounts, data=raw_amount.to_bytes(8, "little")
        )

        from datetime import datetime

        from financepype.operators.blockchains.models import BlockchainPlatform
        from financepype.owners.owner import OwnerIdentifier

        from blockchainpype.initializer import SupportedBlockchainType

        test_platform = BlockchainPlatform(
            identifier="solana",
            type=SupportedBlockchainType.SOLANA.value,
            chain_id=None,
        )

        return SolanaTransaction(
            client_operation_id="test-operation",
            owner_identifier=OwnerIdentifier(platform=test_platform, name=user_address),
            creation_timestamp=datetime.now().timestamp(),
            instructions=[instruction],
            recent_blockhash="",  # Would be filled by the blockchain
        )

    async def build_borrow_transaction(
        self,
        asset: BlockchainAsset,
        amount: Decimal,
        interest_rate_mode: InterestRateMode,
        user_address: str,
    ) -> SolanaTransaction:
        """Build transaction to borrow assets from Solend."""
        solana_asset = cast(SolanaAsset, asset)

        if not self.program.is_initialized:
            await self.program.initialize()

        # Convert amount to raw units
        raw_amount = int(amount * Decimal(10**solana_asset.decimals))

        # Build accounts for borrow instruction
        accounts = [
            AccountMeta(
                pubkey=SolanaAddress.from_string(user_address).raw,
                is_signer=True,
                is_writable=True,
            ),
            # Additional accounts would be needed based on Solend's instruction format
        ]

        # Create borrow instruction
        instruction = self.program.create_instruction(
            name="borrow", accounts=accounts, data=raw_amount.to_bytes(8, "little")
        )

        from datetime import datetime

        from financepype.operators.blockchains.models import BlockchainPlatform
        from financepype.owners.owner import OwnerIdentifier

        from blockchainpype.initializer import SupportedBlockchainType

        test_platform = BlockchainPlatform(
            identifier="solana",
            type=SupportedBlockchainType.SOLANA.value,
            chain_id=None,
        )

        return SolanaTransaction(
            client_operation_id="test-operation",
            owner_identifier=OwnerIdentifier(platform=test_platform, name=user_address),
            creation_timestamp=datetime.now().timestamp(),
            instructions=[instruction],
            recent_blockhash="",  # Would be filled by the blockchain
        )

    async def build_repay_transaction(
        self,
        asset: BlockchainAsset,
        amount: Decimal,
        interest_rate_mode: InterestRateMode,
        user_address: str,
        repay_all: bool = False,
    ) -> SolanaTransaction:
        """Build transaction to repay borrowed assets to Solend."""
        solana_asset = cast(SolanaAsset, asset)

        if not self.program.is_initialized:
            await self.program.initialize()

        # Convert amount to raw units
        if repay_all:
            raw_amount = 2**64 - 1  # Max u64
        else:
            raw_amount = int(amount * Decimal(10**solana_asset.decimals))

        # Build accounts for repay instruction
        accounts = [
            AccountMeta(
                pubkey=SolanaAddress.from_string(user_address).raw,
                is_signer=True,
                is_writable=True,
            ),
            # Additional accounts would be needed based on Solend's instruction format
        ]

        # Create repay instruction
        instruction = self.program.create_instruction(
            name="repay", accounts=accounts, data=raw_amount.to_bytes(8, "little")
        )

        from datetime import datetime

        from financepype.operators.blockchains.models import BlockchainPlatform
        from financepype.owners.owner import OwnerIdentifier

        from blockchainpype.initializer import SupportedBlockchainType

        test_platform = BlockchainPlatform(
            identifier="solana",
            type=SupportedBlockchainType.SOLANA.value,
            chain_id=None,
        )

        return SolanaTransaction(
            client_operation_id="test-operation",
            owner_identifier=OwnerIdentifier(platform=test_platform, name=user_address),
            creation_timestamp=datetime.now().timestamp(),
            instructions=[instruction],
            recent_blockhash="",  # Would be filled by the blockchain
        )

    async def build_collateral_transaction(
        self,
        asset: BlockchainAsset,
        mode: CollateralMode,
        user_address: str,
    ) -> SolanaTransaction:
        """Build transaction to enable/disable asset as collateral in Solend."""
        if not self.program.is_initialized:
            await self.program.initialize()

        # Build accounts for collateral instruction
        accounts = [
            AccountMeta(
                pubkey=SolanaAddress.from_string(user_address).raw,
                is_signer=True,
                is_writable=True,
            ),
            # Additional accounts would be needed based on Solend's instruction format
        ]

        # Create collateral instruction
        instruction_name = (
            "enable_collateral"
            if mode == CollateralMode.ENABLED
            else "disable_collateral"
        )
        instruction = self.program.create_instruction(
            name=instruction_name, accounts=accounts, data=b""
        )

        from datetime import datetime

        from financepype.operators.blockchains.models import BlockchainPlatform
        from financepype.owners.owner import OwnerIdentifier

        from blockchainpype.initializer import SupportedBlockchainType

        test_platform = BlockchainPlatform(
            identifier="solana",
            type=SupportedBlockchainType.SOLANA.value,
            chain_id=None,
        )

        return SolanaTransaction(
            client_operation_id="test-operation",
            owner_identifier=OwnerIdentifier(platform=test_platform, name=user_address),
            creation_timestamp=datetime.now().timestamp(),
            instructions=[instruction],
            recent_blockhash="",  # Would be filled by the blockchain
        )

    async def build_liquidation_transaction(
        self,
        collateral_asset: BlockchainAsset,
        debt_asset: BlockchainAsset,
        user_to_liquidate: str,
        debt_to_cover: Decimal,
        receive_collateral: bool = True,
    ) -> SolanaTransaction:
        """Build transaction to liquidate an undercollateralized position in Solend."""
        debt_solana_asset = cast(SolanaAsset, debt_asset)

        if not self.program.is_initialized:
            await self.program.initialize()

        # Convert debt amount to raw units
        raw_debt_amount = int(debt_to_cover * Decimal(10**debt_solana_asset.decimals))

        # Build accounts for liquidation instruction
        accounts = [
            AccountMeta(
                pubkey=SolanaAddress.from_string(user_to_liquidate).raw,
                is_signer=False,
                is_writable=True,
            ),
            # Additional accounts would be needed based on Solend's instruction format
        ]

        # Create liquidation instruction
        instruction = self.program.create_instruction(
            name="liquidate",
            accounts=accounts,
            data=raw_debt_amount.to_bytes(8, "little"),
        )

        from datetime import datetime

        from financepype.operators.blockchains.models import BlockchainPlatform
        from financepype.owners.owner import OwnerIdentifier

        from blockchainpype.initializer import SupportedBlockchainType

        test_platform = BlockchainPlatform(
            identifier="solana",
            type=SupportedBlockchainType.SOLANA.value,
            chain_id=None,
        )

        return SolanaTransaction(
            client_operation_id="test-operation",
            owner_identifier=OwnerIdentifier(
                platform=test_platform, name=user_to_liquidate
            ),
            creation_timestamp=datetime.now().timestamp(),
            instructions=[instruction],
            recent_blockhash="",  # Would be filled by the blockchain
        )


class SolendMoneyMarket(SolanaMoneyMarket):
    """Solend money market implementation."""

    def _initialize_protocols(self) -> None:
        """Initialize Solend protocol strategies."""
        for protocol_config in self.configuration.protocols:
            if protocol_config.protocol_name.lower() == "solend":
                self._protocol_strategies[protocol_config.protocol_name] = Solend(
                    protocol_config, self.blockchain, self.configuration.platform
                )
