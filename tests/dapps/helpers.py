"""
Shared test doubles for the abstract dapp-layer tests.

These helpers build REAL financepype objects (platforms, assets, transactions)
so the dapp-layer models and facades are exercised against the actual
contracts they must fulfil. Only the blockchain operator is a minimal local
stand-in, registered at the RPC/network boundary.
"""

from typing import Any

from financepype.assets.blockchain import BlockchainAsset, BlockchainAssetData
from financepype.operations.transactions.models import BlockchainTransactionReceipt
from financepype.operations.transactions.transaction import BlockchainTransaction
from financepype.operators.blockchains.blockchain import Blockchain
from financepype.operators.blockchains.identifier import BlockchainIdentifier
from financepype.operators.blockchains.models import BlockchainConfiguration
from financepype.operators.factory import OperatorFactory
from financepype.owners.owner import OwnerIdentifier
from financepype.platforms.blockchain import BlockchainPlatform, BlockchainType

TEST_PLATFORM_IDENTIFIER = "dapps-layer-test"
FIXED_TIMESTAMP = 1_700_000_000.0


class DappTestBlockchainType(BlockchainType):
    """Blockchain type dedicated to the dapp-layer test-suite."""

    TEST = "dapp_test"


class DappTestAddress(BlockchainIdentifier):
    """Minimal concrete identifier used to build real financepype assets."""

    @classmethod
    def is_valid(cls, value: Any) -> bool:
        return isinstance(value, str) and len(value) > 0

    @classmethod
    def id_from_string(cls, value: str) -> Any:
        return value

    @classmethod
    def id_to_string(cls, value: Any) -> str:
        return str(value)


class DappTestBlockchain(Blockchain):
    """Minimal concrete blockchain operator with a fixed timestamp."""

    @property
    def current_timestamp(self) -> float:
        return FIXED_TIMESTAMP

    async def fetch_transaction(
        self, transaction_id: BlockchainIdentifier
    ) -> BlockchainTransaction | None:
        return None


class StubTransaction(BlockchainTransaction):
    """Concrete BlockchainTransaction used as the strategies' build output."""

    @property
    def can_be_modified(self) -> bool:
        return False

    @property
    def can_be_cancelled(self) -> bool:
        return False

    @property
    def can_be_speeded_up(self) -> bool:
        return False

    def process_operation_update(self, update: Any) -> bool:
        return True

    def process_receipt(self, receipt: BlockchainTransactionReceipt) -> bool:
        return True


def build_platform() -> BlockchainPlatform:
    """Build (or fetch from the platform cache) the dapp-test platform."""
    return BlockchainPlatform(
        identifier=TEST_PLATFORM_IDENTIFIER,
        type=DappTestBlockchainType.TEST,
        chain_id=777,
    )


def ensure_blockchain_registered(platform: BlockchainPlatform) -> None:
    """Idempotently register the test blockchain in the OperatorFactory."""
    try:
        OperatorFactory.register_operator_class(platform, DappTestBlockchain)
    except ValueError:
        # Already registered (the factory rejects duplicate registrations).
        pass
    if OperatorFactory.get_configuration(platform) is None:
        OperatorFactory.register_configuration(
            BlockchainConfiguration(platform=platform)
        )


def make_asset(
    platform: BlockchainPlatform,
    symbol: str,
    decimals: int,
    address: str,
) -> BlockchainAsset:
    """Build a real financepype BlockchainAsset."""
    return BlockchainAsset(
        platform=platform,
        identifier=DappTestAddress.from_string(address),
        data=BlockchainAssetData(
            name=f"{symbol} Token", symbol=symbol, decimals=decimals
        ),
    )


def make_transaction(platform: BlockchainPlatform) -> StubTransaction:
    """Build a real (unsigned, pending-broadcast) transaction object."""
    return StubTransaction(
        client_operation_id="stub-operation",
        owner_identifier=OwnerIdentifier(platform=platform, name="tester"),
        creation_timestamp=FIXED_TIMESTAMP,
    )
