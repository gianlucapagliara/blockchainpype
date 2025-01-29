"""
This module provides base classes for interacting with Solana programs.
It implements program configuration, initialization, and instruction building capabilities
through Solders' interfaces.
"""

from typing import cast

from financepype.operators.dapps.dapp import (
    DecentralizedApplication,
    DecentralizedApplicationConfiguration,
)
from pydantic import ConfigDict
from solders.instruction import AccountMeta, Instruction

from blockchainpype.solana.blockchain.blockchain import SolanaBlockchain
from blockchainpype.solana.blockchain.identifier import SolanaAddress
from blockchainpype.solana.dapp.idl import SolanaIDL


class SolanaProgramConfiguration(DecentralizedApplicationConfiguration):
    """
    Configuration class for Solana programs.

    This class defines the essential parameters needed to interact with a Solana
    program, including its address and IDL configuration.

    Attributes:
        address (SolanaAddress): The deployed program's address
        idl_configuration (SolanaIDL): The program's IDL configuration
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    address: SolanaAddress
    idl_configuration: SolanaIDL


class SolanaProgram(DecentralizedApplication):
    """
    Base class for interacting with Solana programs.

    This class provides the foundation for program interaction, including program
    initialization, instruction building, and state management. It uses Solders'
    interfaces for program operations.

    Attributes:
        _idl (dict | None): The program's IDL data
    """

    def __init__(self, configuration: SolanaProgramConfiguration):
        """
        Initialize the program interface.

        Args:
            configuration (SolanaProgramConfiguration): Program configuration
                including address and IDL
        """
        super().__init__(configuration)

        self._idl: dict | None = None

    @property
    def configuration(self) -> SolanaProgramConfiguration:
        """
        Get the program's configuration.

        Returns:
            SolanaProgramConfiguration: The program configuration
        """
        return cast(SolanaProgramConfiguration, super().configuration)

    @property
    def blockchain(self) -> SolanaBlockchain:
        """
        Get the blockchain instance.

        Returns:
            SolanaBlockchain: The blockchain interface
        """
        return cast(SolanaBlockchain, super().blockchain)

    @property
    def address(self) -> SolanaAddress:
        """
        Get the program's address.

        Returns:
            SolanaAddress: The deployed program's address
        """
        return self.configuration.address

    @property
    def idl(self) -> dict | None:
        """
        Get the program's IDL data.

        Returns:
            dict | None: The IDL data if initialized, None otherwise
        """
        return self._idl

    @property
    def is_initialized(self) -> bool:
        """
        Check if the program is initialized.

        Returns:
            bool: True if the program is initialized, False otherwise
        """
        return self.idl is not None

    async def initialize(self) -> None:
        """
        Initialize the program by loading its IDL.

        This method fetches the program's IDL and stores it for use in instruction
        building. It only initializes once, subsequent calls have no effect.
        """
        if self.is_initialized:
            return

        self._idl = await self.configuration.idl_configuration.get_idl()

    def create_instruction(
        self,
        name: str,
        accounts: list[AccountMeta],
        data: bytes | None = None,
    ) -> Instruction:
        """
        Create a program instruction.

        Args:
            name (str): The instruction name from the IDL
            accounts (list[AccountMeta]): The accounts required for the instruction
            data (bytes | None): Optional instruction data

        Returns:
            Instruction: The created program instruction

        Raises:
            ValueError: If the program is not initialized or the instruction name is invalid
        """
        if not self.is_initialized:
            raise ValueError("Program is not initialized")

        idl = self.idl or {}
        instructions = idl.get("instructions", {})
        if name not in instructions:
            raise ValueError(f"Invalid instruction name: {name}")

        return Instruction(
            program_id=self.address.raw,
            accounts=accounts,
            data=data or b"",
        )
