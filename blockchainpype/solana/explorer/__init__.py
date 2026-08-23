"""
This package provides integrations with Solana blockchain explorers.
Currently supports Solscan for transaction, account, token, and block links.
"""

from blockchainpype.solana.explorer.solscan import (
    SolscanConfiguration,
    SolscanExplorer,
)

__all__ = ["SolscanConfiguration", "SolscanExplorer"]
