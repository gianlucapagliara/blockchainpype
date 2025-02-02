import pytest
from eth_account import Account
from eth_account.signers.local import LocalAccount
from financepype.platforms.blockchain import BlockchainPlatform, BlockchainType
from pydantic import SecretStr
from web3 import AsyncWeb3
from web3.types import Wei

from blockchainpype.evm.blockchain.blockchain import EthereumBlockchain
from blockchainpype.evm.blockchain.configuration import (
    EthereumBlockchainConfiguration,
    EthereumConnectivityConfiguration,
    EthereumNativeAssetConfiguration,
)
from blockchainpype.evm.blockchain.gas import GasConfiguration
from blockchainpype.evm.blockchain.identifier import EthereumAddress
from blockchainpype.evm.wallet.identifier import EthereumWalletIdentifier
from blockchainpype.evm.wallet.signer import EthereumSignerConfiguration
from blockchainpype.evm.wallet.wallet import EthereumWallet, EthereumWalletConfiguration


@pytest.fixture
def ethereum_config() -> EthereumBlockchainConfiguration:
    """Fixture providing a test Ethereum blockchain configuration."""
    return EthereumBlockchainConfiguration(
        platform=BlockchainPlatform(
            identifier="ethereum",
            type=BlockchainType.EVM,
            chain_id=1,
        ),
        native_asset=EthereumNativeAssetConfiguration(),
        connectivity=EthereumConnectivityConfiguration(
            rpc_provider=AsyncWeb3.AsyncHTTPProvider("https://eth.llamarpc.com"),
        ),
        explorer=None,
    )


@pytest.fixture
def ethereum_blockchain(
    ethereum_config: EthereumBlockchainConfiguration,
) -> EthereumBlockchain:
    """Fixture providing a test Ethereum blockchain instance."""
    return EthereumBlockchain(configuration=ethereum_config)


@pytest.fixture
def test_account() -> LocalAccount:
    """Fixture providing a test Ethereum account."""
    # Generate a random account for testing
    return Account.create()


@pytest.fixture
def ethereum_wallet(
    ethereum_config: EthereumBlockchainConfiguration,
    test_account: LocalAccount,
    ethereum_blockchain: EthereumBlockchain,
) -> EthereumWallet:
    """Fixture providing a test Ethereum wallet."""
    identifier = EthereumWalletIdentifier(
        platform=ethereum_config.platform,
        name="test_wallet",
        address=EthereumAddress(
            raw=test_account.address,
            string=test_account.address,
        ),
    )

    signer_config = EthereumSignerConfiguration(
        private_key=SecretStr(test_account.key.hex()),
    )

    wallet_config = EthereumWalletConfiguration(
        identifier=identifier,
        signer=signer_config,
        gas_configuration=GasConfiguration(),
    )

    return EthereumWallet(configuration=wallet_config, blockchain=ethereum_blockchain)


@pytest.mark.asyncio
async def test_wallet_initialization(
    ethereum_wallet: EthereumWallet, test_account: LocalAccount
) -> None:
    """Test wallet initialization and basic properties."""
    assert ethereum_wallet.identifier.name == "test_wallet"
    assert (
        ethereum_wallet.identifier.address.string.lower()
        == test_account.address.lower()
    )
    assert ethereum_wallet.signer is not None


@pytest.mark.asyncio
async def test_wallet_nonce_management(ethereum_wallet: EthereumWallet) -> None:
    """Test wallet nonce management."""
    # Initial nonce should be None
    assert ethereum_wallet.last_nonce is None

    # Sync nonce from blockchain
    await ethereum_wallet.sync_nonce()
    # Check if last_nonce is a non-negative integer
    assert ethereum_wallet.last_nonce is not None
    assert ethereum_wallet.last_nonce >= 0

    # Test nonce allocation
    initial_nonce = ethereum_wallet.last_nonce
    allocated_nonce = ethereum_wallet.allocate_nonce()
    assert allocated_nonce == initial_nonce
    assert ethereum_wallet.last_nonce == initial_nonce + 1


@pytest.mark.asyncio
async def test_wallet_balance(ethereum_wallet: EthereumWallet) -> None:
    """Test fetching wallet balance."""
    balance = await ethereum_wallet.fetch_balance(
        ethereum_wallet.blockchain.native_asset
    )
    # Check if balance is a non-negative decimal
    assert balance is not None
    assert balance >= 0


@pytest.mark.asyncio
async def test_transaction_signing(ethereum_wallet: EthereumWallet) -> None:
    """Test transaction signing."""
    tx_data = {
        "to": "0x" + "1" * 40,
        "value": Wei(1000000000000000000),  # 1 ETH
        "gas": 21000,
        "gasPrice": Wei(20000000000),
        "nonce": 0,
    }

    signed_tx = ethereum_wallet.sign_transaction(tx_data, auto_assign_nonce=False)
    assert signed_tx is not None
    assert signed_tx.hash is not None
