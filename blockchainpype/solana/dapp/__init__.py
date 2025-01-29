"""
This module provides interfaces for interacting with Solana programs and tokens.
"""

from blockchainpype.solana.dapp.idl import SolanaDictIDL, SolanaIDL, SolanaLocalFileIDL
from blockchainpype.solana.dapp.program import SolanaProgram, SolanaProgramConfiguration
from blockchainpype.solana.dapp.token import (
    SPLToken,
    SPLTokenProgram,
    SPLTokenProgramConfiguration,
)

__all__ = [
    "SolanaIDL",
    "SolanaDictIDL",
    "SolanaLocalFileIDL",
    "SolanaProgram",
    "SolanaProgramConfiguration",
    "SPLToken",
    "SPLTokenProgram",
    "SPLTokenProgramConfiguration",
]
