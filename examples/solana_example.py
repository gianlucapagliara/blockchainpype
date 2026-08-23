"""Build a Solana wallet and work with an SPL token.

The Solana side mirrors the EVM one: a
:class:`~blockchainpype.solana.blockchain.blockchain.SolanaBlockchain` created
from the registered configuration, a
:class:`~blockchainpype.solana.wallet.wallet.SolanaWallet` that signs and tracks
transactions, and
:class:`~blockchainpype.solana.dapp.token.SPLTokenProgram` /
:class:`~blockchainpype.solana.dapp.token.SPLToken` for token accounts.

Two things are worth knowing:

* Balances live in *associated token accounts* (ATAs), not on the wallet
  address. ``get_associated_token_account`` derives the ATA locally (a PDA
  derivation, no RPC), and ``get_balance_of`` reads it.
* ``transferChecked`` is the transfer instruction used; it asserts the mint's
  decimals on-chain. ``build_transfer_instruction`` produces it offline, while
  ``place_transfer`` signs and broadcasts a transaction carrying it.

Steps 1 and 2 below are entirely offline (keypair, ATA derivation, instruction
bytes); only step 3 talks to an RPC.

Importing this module is side-effect free. Environment variables:

* ``SOLANA_RPC_URL``: the endpoint used for step 3 (a public mainnet default is
  used when unset).
* ``SOLANA_PRIVATE_KEY``: optional base58 keypair string. A throwaway keypair
  is generated when unset, which is enough for every offline step.

Run it with::

    uv run python -m examples.solana_example
"""

from __future__ import annotations

import asyncio
import os
from decimal import Decimal

from pydantic import SecretStr
from solders.keypair import Keypair

from blockchainpype.factory import BlockchainFactory
from blockchainpype.solana.blockchain.blockchain import SolanaBlockchain
from blockchainpype.solana.blockchain.identifier import SolanaAddress
from blockchainpype.solana.dapp.token import (
    SPLToken,
    SPLTokenProgram,
    SPLTokenProgramConfiguration,
)
from blockchainpype.solana.wallet.identifier import SolanaWalletIdentifier
from blockchainpype.solana.wallet.signer import SolanaSignerConfiguration
from blockchainpype.solana.wallet.wallet import (
    SolanaWallet,
    SolanaWalletConfiguration,
)
from examples.basic.configure import (
    SOLANA_RPC_URL_ENV,
    configure_blockchains,
    load_environment,
)

PRIVATE_KEY_ENV = "SOLANA_PRIVATE_KEY"

#: Circle's USDC mint on Solana mainnet (6 decimals).
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

TRANSFER_AMOUNT = Decimal("1.5")
USDC_DECIMALS = 6


def build_wallet(blockchain: SolanaBlockchain) -> tuple[SolanaWallet, bool]:
    """Build a wallet from ``SOLANA_PRIVATE_KEY`` or from a throwaway keypair.

    Returns:
        tuple[SolanaWallet, bool]: The wallet, and whether it was generated
        (a generated wallet holds no funds).
    """
    private_key = os.getenv(PRIVATE_KEY_ENV)
    generated = not private_key
    if generated:
        private_key = str(Keypair())

    keypair = Keypair.from_base58_string(str(private_key))
    configuration = SolanaWalletConfiguration(
        identifier=SolanaWalletIdentifier(
            name="solana-example",
            platform=blockchain.configuration.platform,
            address=SolanaAddress.from_raw(keypair.pubkey()),
        ),
        signer=SolanaSignerConfiguration(private_key=SecretStr(str(private_key))),
    )
    return SolanaWallet(configuration, blockchain=blockchain), generated


def build_token(blockchain: SolanaBlockchain, mint_address: str) -> SPLToken:
    """Build an SPL token handle bound to the canonical SPL Token program."""
    program = SPLTokenProgram(
        SPLTokenProgramConfiguration(platform=blockchain.configuration.platform)
    )
    mint = SolanaAddress.from_string(mint_address)
    return SPLToken(
        platform=blockchain.configuration.platform,
        identifier=mint,
        mint=mint,
        program=program,
    )


def require_program(token: SPLToken) -> SPLTokenProgram:
    """Return the token's program, or fail with a clear message."""
    if token.program is None:
        raise ValueError("The SPL token was built without a program")
    return token.program


def show_accounts(wallet: SolanaWallet, token: SPLToken) -> None:
    """Derive the wallet's associated token account. No RPC involved."""
    program = require_program(token)
    ata = program.get_associated_token_account(wallet.address, token.mint)

    print("=== 1. Wallet and token accounts (offline) ===")
    print(f"wallet address: {wallet.address.string}")
    print(f"can sign:       {wallet.signer is not None}")
    print(f"token program:  {program.address.string}")
    print(f"mint:           {token.mint.string}")
    print(f"wallet ATA:     {ata.string}")


def show_transfer_instruction(wallet: SolanaWallet, token: SPLToken) -> None:
    """Build a transferChecked instruction locally and print its bytes."""
    program = require_program(token)
    destination = SolanaAddress.from_raw(Keypair().pubkey())
    raw_amount = int(TRANSFER_AMOUNT * 10**USDC_DECIMALS)

    instruction = program.build_transfer_instruction(
        source_owner=wallet.address,
        destination_owner=destination,
        mint=token.mint,
        raw_amount=raw_amount,
        decimals=USDC_DECIMALS,
    )

    print()
    print("=== 2. transferChecked instruction (offline) ===")
    print(f"destination owner: {destination.string}")
    print(f"amount:            {TRANSFER_AMOUNT} ({raw_amount} raw units)")
    print(f"program id:        {instruction.program_id}")
    print(f"data:              0x{bytes(instruction.data).hex()}")
    print("accounts:")
    for account in instruction.accounts:
        role = (
            "signer"
            if account.is_signer
            else "writable"
            if account.is_writable
            else "readonly"
        )
        print(f"  - {account.pubkey} ({role})")
    print()
    print("wallet.sign_and_send_transaction(...) broadcasts it, or use")
    print("SPLTokenProgram.place_transfer(wallet, token, destination, amount).")


async def show_balances(wallet: SolanaWallet, token: SPLToken) -> None:
    """Read the on-chain balances. This is the only step needing an RPC."""
    program = require_program(token)

    print()
    print("=== 3. On-chain balances (needs a Solana RPC) ===")
    sol_balance = await wallet.fetch_balance(wallet.blockchain.native_asset)
    print(f"SOL balance: {sol_balance}")

    await token.initialize_data()
    if token.data is not None:
        print(f"mint decimals: {token.data.decimals}")

    try:
        usdc_balance = await program.get_balance_of(wallet.address, token.mint)
    except Exception as error:
        # The RPC rejects the lookup when the ATA does not exist yet.
        print(f"USDC balance: no associated token account ({type(error).__name__})")
        return
    print(f"USDC balance: {usdc_balance}")


async def main() -> None:
    """Show the offline wallet/token plumbing, then read on-chain balances."""
    load_environment()
    configure_blockchains()
    blockchain = BlockchainFactory.get_solana_blockchain_by_identifier("solana")

    wallet, generated = build_wallet(blockchain)
    token = build_token(blockchain, USDC_MINT)

    show_accounts(wallet, token)
    show_transfer_instruction(wallet, token)
    if generated:
        print()
        print(
            f"Using a throwaway keypair; set {PRIVATE_KEY_ENV} (base58) to "
            "inspect your own wallet."
        )

    try:
        await show_balances(wallet, token)
    except Exception as error:
        print(f"Network step failed: {type(error).__name__}: {error}")
        print(f"Point {SOLANA_RPC_URL_ENV} at a working Solana RPC endpoint.")
    finally:
        await blockchain.rpc_client.close()


if __name__ == "__main__":
    asyncio.run(main())
