"""
This module provides classes for interacting with ERC-20 tokens on Ethereum networks.
It implements the standard ERC-20 interface, including token transfers, allowances,
and balance queries, with proper decimal handling and type safety.
"""

from decimal import Decimal

from pydantic import ConfigDict, Field

from blockchainpype.evm.asset import EthereumAsset
from blockchainpype.evm.blockchain.identifier import EthereumAddress
from blockchainpype.evm.dapp.abi import EthereumABI, EthereumLocalFileABI
from blockchainpype.evm.dapp.contract import (
    EthereumContractConfiguration,
    EthereumSmartContract,
)
from blockchainpype.evm.transaction import EthereumTransaction


class ERC20ContractConfiguration(EthereumContractConfiguration):
    """
    Configuration for ERC-20 token contracts.

    This class extends the base contract configuration with ERC-20 specific settings,
    including automatic loading of the standard ERC-20 ABI.

    Attributes:
        address (EthereumAddress): The token contract's address
        abi_configuration (EthereumABI): ABI configuration, defaults to standard ERC-20 ABI
    """

    address: EthereumAddress
    abi_configuration: EthereumABI = Field(
        default_factory=lambda: EthereumLocalFileABI(file_name="ERC20.json")
    )


class ERC20Contract(EthereumSmartContract):
    """
    Implementation of the ERC-20 token standard interface.

    This class provides methods for interacting with ERC-20 token contracts,
    including querying balances and allowances, and performing transfers.
    All numeric values are handled as Decimal for precision.
    """

    async def get_total_supply(self) -> Decimal:
        """
        Get the total supply of the token.

        Returns:
            Decimal: The total token supply
        """
        raw_supply = await self.functions.totalSupply().call()
        return Decimal(str(raw_supply))

    async def get_balance_of(self, address: EthereumAddress) -> Decimal:
        """
        Get the token balance of an address.

        Args:
            address (EthereumAddress): The address to check

        Returns:
            Decimal: The token balance
        """
        raw_balance = await self.functions.balanceOf(address.raw).call()
        return Decimal(str(raw_balance))

    async def get_allowance(
        self, owner: EthereumAddress, spender: EthereumAddress
    ) -> Decimal:
        """
        Get the amount of tokens that a spender is allowed to spend on behalf of the owner.

        Args:
            owner (EthereumAddress): The token owner's address
            spender (EthereumAddress): The spender's address

        Returns:
            Decimal: The approved amount
        """
        raw_allowance = await self.functions.allowance(owner.raw, spender.raw).call()
        return Decimal(str(raw_allowance))

    async def place_transfer(
        self, recipient: EthereumAddress, amount: Decimal
    ) -> EthereumTransaction:
        """
        Create a transaction to transfer tokens to a recipient.

        Args:
            recipient (EthereumAddress): The recipient's address
            amount (Decimal): The amount of tokens to transfer

        Returns:
            EthereumTransaction: The prepared transfer transaction
        """
        raise NotImplementedError

    async def place_transfer_from(
        self, sender: EthereumAddress, recipient: EthereumAddress, amount: Decimal
    ) -> EthereumTransaction:
        """
        Create a transaction to transfer tokens from one address to another.

        This method is used for transferring tokens on behalf of another address
        that has approved the spending.

        Args:
            sender (EthereumAddress): The token owner's address
            recipient (EthereumAddress): The recipient's address
            amount (Decimal): The amount of tokens to transfer

        Returns:
            EthereumTransaction: The prepared transfer transaction
        """
        raise NotImplementedError

    async def place_approve(
        self, spender: EthereumAddress, amount: Decimal
    ) -> EthereumTransaction:
        """
        Create a transaction to approve a spender to spend tokens.

        Args:
            spender (EthereumAddress): The address to approve
            amount (Decimal): The amount of tokens to approve

        Returns:
            EthereumTransaction: The prepared approval transaction
        """
        raise NotImplementedError


class ERC20Token(EthereumAsset):
    """
    Representation of an ERC-20 token as an Ethereum asset.

    This class combines the ERC-20 contract interface with asset management
    capabilities, allowing the token to be treated as a standard asset
    while providing access to its contract functionality.

    Attributes:
        contract (ERC20Contract | None): The token's contract interface
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    contract: ERC20Contract | None = None
