from datetime import timedelta

from blockchainpype.evm.blockchain.identifier import EthereumAddress
from blockchainpype.evm.wallet.identifier import EthereumWalletIdentifier
from blockchainpype.evm.wallet.wallet import EthereumWallet, EthereumWalletConfiguration
from blockchainpype.factory import BlockchainFactory, WalletFactory, WalletRegistry
from blockchainpype.initializer import SupportedBlockchainType
from blockchainpype.solana.blockchain.identifier import SolanaAddress
from blockchainpype.solana.wallet.identifier import SolanaWalletIdentifier
from blockchainpype.solana.wallet.wallet import SolanaWallet, SolanaWalletConfiguration


class WalletsInitializer:
    @classmethod
    def register_wallet_classes(cls) -> None:
        """Register wallet classes for different blockchain types."""
        WalletFactory.register_wallet_class(
            SupportedBlockchainType.EVM.value, EthereumWallet
        )
        WalletFactory.register_wallet_class(
            SupportedBlockchainType.SOLANA.value, SolanaWallet
        )

    @classmethod
    def configure_wallets(cls) -> None:
        """Register wallet configurations for different wallets."""

        # Example Ethereum wallet configuration
        ethereum_blockchain = BlockchainFactory.get_by_identifier("ethereum")
        if ethereum_blockchain is None:
            raise ValueError("Ethereum blockchain configuration not found")

        wallet_configuration = EthereumWalletConfiguration(
            identifier=EthereumWalletIdentifier(
                name="Main Wallet",
                platform=ethereum_blockchain.platform,
                address=EthereumAddress.from_string("0xYOUR_ADDRESS_HERE"),
            ),
            tracked_assets=set(),
            default_tx_wait=timedelta(minutes=2),
            # signer=EthereumSignerConfiguration(
            #     private_key=SecretStr("YOUR_PRIVATE_KEY_HERE"),
            # ),
        )

        WalletRegistry.register(wallet_configuration)

        # Example Solana wallet configuration
        solana_blockchain = BlockchainFactory.get_by_identifier("solana")
        if solana_blockchain is None:
            raise ValueError("Solana blockchain configuration not found")

        solana_wallet_configuration = SolanaWalletConfiguration(
            identifier=SolanaWalletIdentifier(
                name=None,
                platform=solana_blockchain.platform,
                address=SolanaAddress.from_string("YOUR_ADDRESS_HERE"),
            ),
            tracked_assets=set(),
            default_tx_wait=timedelta(seconds=10),
            # signer=SolanaSignerConfiguration(
            #     private_key=SecretStr("YOUR_PRIVATE_KEY_HERE"),
            # ),
        )

        WalletRegistry.register(solana_wallet_configuration)


WalletsInitializer.register_wallet_classes()
