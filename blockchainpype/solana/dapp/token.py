"""
This module provides classes for interacting with SPL Token programs on Solana networks.
It implements the standard SPL Token interface, including token transfers, minting,
and balance queries, with proper decimal handling and type safety.
"""

from decimal import Decimal

from pydantic import ConfigDict, Field
from solana.rpc.types import TokenAccountOpts
from solders.instruction import AccountMeta
from solders.message import Message
from solders.transaction import VersionedTransaction

from blockchainpype.solana.asset import SolanaAsset
from blockchainpype.solana.blockchain.identifier import SolanaAddress
from blockchainpype.solana.dapp.idl import SolanaIDL, SolanaLocalFileIDL
from blockchainpype.solana.dapp.program import SolanaProgram, SolanaProgramConfiguration
from blockchainpype.solana.transaction import SolanaRawTransaction, SolanaTransaction
from blockchainpype.solana.wallet.identifier import SolanaWalletIdentifier


class SPLTokenProgramConfiguration(SolanaProgramConfiguration):
    """
    Configuration for SPL Token programs.

    This class extends the base program configuration with SPL Token specific settings,
    including automatic loading of the standard SPL Token IDL.

    Attributes:
        address (SolanaAddress): The token program's address
        idl_configuration (SolanaIDL): IDL configuration, defaults to standard SPL Token IDL
    """

    address: SolanaAddress = Field(
        default_factory=lambda: SolanaAddress.from_string(
            "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
        )
    )
    idl_configuration: SolanaIDL = Field(
        default_factory=lambda: SolanaLocalFileIDL(file_name="SPLToken.json")
    )


class SPLTokenProgram(SolanaProgram):
    """
    Implementation of the SPL Token program interface.

    This class provides methods for interacting with SPL Token programs,
    including querying balances and performing transfers.
    All numeric values are handled as Decimal for precision.
    """

    async def get_token_account_balance(self, token_account: SolanaAddress) -> Decimal:
        """
        Get the token balance of a token account.

        Args:
            token_account (SolanaAddress): The token account to check

        Returns:
            Decimal: The token balance
        """
        raw_balance = (
            await self.blockchain.rpc_client.get_token_account_balance(
                token_account.raw
            )
        ).value.amount
        return Decimal(raw_balance)

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

    async def place_transfer(
        self,
        source: SolanaAddress,
        destination: SolanaAddress,
        authority: SolanaAddress,
        amount: Decimal,
    ) -> SolanaTransaction:
        """
        Create a transaction to transfer tokens between accounts.

        Args:
            source (SolanaAddress): The source token account
            destination (SolanaAddress): The destination token account
            authority (SolanaAddress): The source account's owner/authority
            amount (Decimal): The amount of tokens to transfer

        Returns:
            SolanaTransaction: The prepared transfer transaction
        """
        accounts = [
            AccountMeta(pubkey=source.raw, is_signer=False, is_writable=True),
            AccountMeta(pubkey=destination.raw, is_signer=False, is_writable=True),
            AccountMeta(pubkey=authority.raw, is_signer=True, is_writable=False),
        ]

        instruction = self.create_instruction(
            name="transfer",
            accounts=accounts,
            data=int(amount).to_bytes(8, byteorder="little"),
        )

        recent_blockhash = await self.blockchain.fetch_recent_blockhash()
        message = Message.new_with_blockhash(
            [instruction],
            authority.raw,
            recent_blockhash,
        )
        transaction = VersionedTransaction(message, [])

        raw_transaction = SolanaRawTransaction(
            message=message,
            signatures=[],
            recent_blockhash=recent_blockhash,
            fee_payer=authority.raw,
            is_versioned=True,
        )

        return SolanaTransaction(
            client_operation_id=f"transfer_{source.string}_{destination.string}_{amount}",
            owner_identifier=SolanaWalletIdentifier(
                platform=self.platform,
                address=authority,
            ),
            creation_timestamp=self.blockchain.current_timestamp,
            raw_transaction=raw_transaction,
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

    model_config = ConfigDict(arbitrary_types_allowed=True)

    program: SPLTokenProgram | None = None
    mint: SolanaAddress
