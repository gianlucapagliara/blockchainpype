"""
This module provides classes for interacting with SPL Token programs on Solana networks.
It implements the standard SPL Token interface, including token transfers and balance
queries, with proper decimal handling and type safety. Instructions are built with the
``spl.token`` helpers shipped with solana-py, so no IDL is required.
"""

import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from pydantic import ConfigDict, Field
from solana.rpc.types import TokenAccountOpts
from solders.instruction import Instruction
from solders.message import Message
from solders.transaction import Transaction
from spl.token.constants import TOKEN_PROGRAM_ID
from spl.token.instructions import (
    TransferCheckedParams,
    get_associated_token_address,
    transfer_checked,
)

from blockchainpype.solana.asset import SolanaAsset, SolanaAssetData
from blockchainpype.solana.blockchain.identifier import SolanaAddress
from blockchainpype.solana.dapp.idl import SolanaDictIDL, SolanaIDL
from blockchainpype.solana.dapp.program import SolanaProgram, SolanaProgramConfiguration
from blockchainpype.solana.transaction import SolanaTransaction

if TYPE_CHECKING:
    from blockchainpype.solana.wallet.wallet import SolanaWallet

# Minimal simplified IDL for the SPL Token program. Instructions are built via
# the spl.token helpers, so this only documents the supported instruction names.
SPL_TOKEN_IDL: dict[str, object] = {
    "version": "3.4.0",
    "name": "spl_token",
    "instructions": {
        "transferChecked": {
            "name": "transferChecked",
            "accounts": [],
            "discriminant": 12,
        },
    },
}


class SPLTokenProgramConfiguration(SolanaProgramConfiguration):
    """
    Configuration for SPL Token programs.

    This class extends the base program configuration with SPL Token specific
    settings. The program address defaults to the canonical SPL Token program
    and the IDL defaults to a bundled in-memory description, since instructions
    are built via the ``spl.token`` helpers rather than IDL-driven encoding.

    Attributes:
        address (SolanaAddress): The token program's address
        idl_configuration (SolanaIDL): IDL configuration, defaults to the
            bundled SPL Token instruction listing
    """

    address: SolanaAddress = Field(
        default_factory=lambda: SolanaAddress.from_raw(TOKEN_PROGRAM_ID)
    )
    idl_configuration: SolanaIDL = Field(
        default_factory=lambda: SolanaDictIDL(idl=dict(SPL_TOKEN_IDL))
    )


class SPLTokenProgram(SolanaProgram):
    """
    Implementation of the SPL Token program interface.

    This class provides methods for interacting with SPL Token programs,
    including querying balances and performing transfers.
    All numeric values are handled as Decimal for precision.
    """

    def get_associated_token_account(
        self, owner: SolanaAddress, mint: SolanaAddress
    ) -> SolanaAddress:
        """
        Derive the associated token account (ATA) for an owner and mint.

        Args:
            owner (SolanaAddress): The owner's wallet address
            mint (SolanaAddress): The token mint address

        Returns:
            SolanaAddress: The derived associated token account address
        """
        ata = get_associated_token_address(
            owner.raw, mint.raw, token_program_id=self.address.raw
        )
        ata_address: SolanaAddress = SolanaAddress.from_raw(ata)
        return ata_address

    async def get_token_account_balance(self, token_account: SolanaAddress) -> Decimal:
        """
        Get the decimal-adjusted token balance of a token account.

        Args:
            token_account (SolanaAddress): The token account to check

        Returns:
            Decimal: The token balance, scaled by the mint's decimals
        """
        token_amount = (
            await self.blockchain.rpc_client.get_token_account_balance(
                token_account.raw
            )
        ).value
        return Decimal(token_amount.amount) / Decimal(10) ** token_amount.decimals

    async def get_balance_of(
        self, owner: SolanaAddress, mint: SolanaAddress
    ) -> Decimal:
        """
        Get an owner's decimal-adjusted balance for a mint via its associated
        token account.

        Note that the RPC call fails if the associated token account has not
        been created on-chain yet.

        Args:
            owner (SolanaAddress): The owner's wallet address
            mint (SolanaAddress): The token mint address

        Returns:
            Decimal: The token balance, scaled by the mint's decimals
        """
        token_account = self.get_associated_token_account(owner, mint)
        return await self.get_token_account_balance(token_account)

    async def get_token_accounts_by_owner(
        self, owner: SolanaAddress, mint: SolanaAddress
    ) -> list[SolanaAddress]:
        """
        Get all token accounts owned by an address for a specific mint.

        Args:
            owner (SolanaAddress): The owner's address
            mint (SolanaAddress): The token mint address

        Returns:
            list[SolanaAddress]: List of token account addresses
        """
        accounts = (
            await self.blockchain.rpc_client.get_token_accounts_by_owner(
                owner.raw,
                TokenAccountOpts(mint=mint.raw),
            )
        ).value
        return [SolanaAddress.from_raw(account.pubkey) for account in accounts]

    def build_transfer_instruction(
        self,
        source_owner: SolanaAddress,
        destination_owner: SolanaAddress,
        mint: SolanaAddress,
        raw_amount: int,
        decimals: int,
    ) -> Instruction:
        """
        Build a ``transferChecked`` instruction between two owners' associated
        token accounts.

        Args:
            source_owner (SolanaAddress): The sending wallet's address
            destination_owner (SolanaAddress): The receiving wallet's address
            mint (SolanaAddress): The token mint address
            raw_amount (int): The amount in raw (smallest) token units
            decimals (int): The mint's decimals, asserted on-chain

        Returns:
            Instruction: The transfer instruction
        """
        source_account = self.get_associated_token_account(source_owner, mint)
        destination_account = self.get_associated_token_account(destination_owner, mint)
        return transfer_checked(
            TransferCheckedParams(
                program_id=self.address.raw,
                source=source_account.raw,
                mint=mint.raw,
                dest=destination_account.raw,
                owner=source_owner.raw,
                amount=raw_amount,
                decimals=decimals,
            )
        )

    async def place_transfer(
        self,
        wallet: "SolanaWallet",
        token: "SPLToken",
        destination: SolanaAddress,
        amount: Decimal,
        client_operation_id: str | None = None,
    ) -> SolanaTransaction:
        """
        Transfer tokens from the wallet's associated token account to the
        destination owner's associated token account.

        The transfer is built as a ``transferChecked`` instruction, wrapped in
        a legacy transaction with the wallet as fee payer, and handed to the
        wallet's sign-and-send API for signing, broadcasting, and tracking.

        Args:
            wallet (SolanaWallet): The sending wallet (source owner and fee payer)
            token (SPLToken): The token to transfer
            destination (SolanaAddress): The receiving owner's wallet address
            amount (Decimal): The amount of tokens to transfer, in decimal units
            client_operation_id (str | None): Optional identifier for tracking;
                generated when omitted

        Returns:
            SolanaTransaction: The tracked transfer transaction

        Raises:
            ValueError: If the token's data (decimals) is not initialized
        """
        if token.data is None:
            raise ValueError(
                "Token data is not initialized; call initialize_data() first"
            )

        raw_amount = token.convert_to_raw(amount)
        instruction = self.build_transfer_instruction(
            source_owner=wallet.address,
            destination_owner=destination,
            mint=token.mint,
            raw_amount=raw_amount,
            decimals=token.data.decimals,
        )

        recent_blockhash = await self.blockchain.fetch_recent_blockhash()
        message = Message.new_with_blockhash(
            [instruction],
            wallet.address.raw,
            recent_blockhash,
        )
        transaction = Transaction.new_unsigned(message)

        if client_operation_id is None:
            client_operation_id = (
                f"spl-transfer-{token.mint.string}-"
                f"{destination.string}-{uuid.uuid4().hex}"
            )

        return wallet.sign_and_send_transaction(
            client_operation_id=client_operation_id,
            transaction=transaction,
            recent_blockhash=recent_blockhash,
        )


class SPLToken(SolanaAsset):
    """
    Representation of an SPL Token as a Solana asset.

    This class combines the SPL Token program interface with asset management
    capabilities, allowing the token to be treated as a standard asset
    while providing access to its program functionality.

    Attributes:
        program (SPLTokenProgram | None): The token's program interface
        mint (SolanaAddress): The token's mint address
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=False)

    program: SPLTokenProgram | None = None
    mint: SolanaAddress

    async def initialize_data(self) -> None:
        """
        Initialize the asset data from the chain when not supplied upfront.

        The mint's decimals are fetched on-chain (the only field affecting
        amount scaling). Solana mints carry no name/symbol on the mint account
        itself (that requires Metaplex metadata, which is not queried here), so
        both default to the mint address string.

        Raises:
            ValueError: If the token program is not set
        """
        if self.data is not None:
            return

        if self.program is None:
            raise ValueError("Token program is not initialized")

        token_amount = (
            await self.program.blockchain.rpc_client.get_token_supply(self.mint.raw)
        ).value
        self.data = SolanaAssetData(
            name=self.mint.string,
            symbol=self.mint.string,
            decimals=token_amount.decimals,
        )
