"""
Unit tests for the Solscan explorer link builders and configuration, and for
the package export fix (the Solana explorer package must export Solscan, not
the copy-pasted Etherscan classes).
"""

from solders.keypair import Keypair
from solders.signature import Signature

import blockchainpype.solana.explorer as explorer_package
from blockchainpype.solana.blockchain.identifier import (
    SolanaAddress,
    SolanaTransactionSignature,
)
from blockchainpype.solana.explorer.solscan import (
    SolscanConfiguration,
    SolscanExplorer,
)


class TestPackageExports:
    def test_package_exports_solscan_classes(self) -> None:
        assert explorer_package.SolscanExplorer is SolscanExplorer
        assert explorer_package.SolscanConfiguration is SolscanConfiguration
        assert sorted(explorer_package.__all__) == [
            "SolscanConfiguration",
            "SolscanExplorer",
        ]

    def test_package_does_not_export_etherscan(self) -> None:
        assert not hasattr(explorer_package, "EtherscanExplorer")


class TestSolscanConfiguration:
    def test_defaults_to_mainnet(self) -> None:
        configuration = SolscanConfiguration()

        assert configuration.base_url == "https://solscan.io"

    def test_custom_base_url(self) -> None:
        configuration = SolscanConfiguration(base_url="https://solscan.io/custom")

        assert configuration.base_url == "https://solscan.io/custom"


class TestSolscanExplorer:
    def setup_method(self) -> None:
        self.explorer = SolscanExplorer(configuration=SolscanConfiguration())

    def test_base_url(self) -> None:
        assert self.explorer.base_url == "https://solscan.io"

    def test_get_transaction_link(self) -> None:
        signature = Signature.new_unique()
        tx_sig = SolanaTransactionSignature.from_raw(signature)

        link = self.explorer.get_transaction_link(tx_sig)

        assert link == f"https://solscan.io/tx/{signature}"

    def test_get_address_link(self) -> None:
        pubkey = Keypair().pubkey()
        address = SolanaAddress.from_raw(pubkey)

        link = self.explorer.get_address_link(address)

        assert link == f"https://solscan.io/account/{pubkey}"

    def test_get_token_link(self) -> None:
        mint = Keypair().pubkey()
        address = SolanaAddress.from_raw(mint)

        link = self.explorer.get_token_link(address)

        assert link == f"https://solscan.io/token/{mint}"

    def test_get_block_link(self) -> None:
        link = self.explorer.get_block_link(331535086)

        assert link == "https://solscan.io/block/331535086"

    def test_custom_base_url_used_in_links(self) -> None:
        explorer = SolscanExplorer(
            configuration=SolscanConfiguration(base_url="https://custom.example")
        )
        signature = Signature.new_unique()

        link = explorer.get_transaction_link(
            SolanaTransactionSignature.from_raw(signature)
        )

        assert link == f"https://custom.example/tx/{signature}"
