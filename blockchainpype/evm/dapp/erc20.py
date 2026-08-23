"""
This module provides classes for interacting with ERC-20 tokens on Ethereum networks.
It implements the standard ERC-20 interface, including token transfers, allowances,
and balance queries, with proper decimal handling and type safety.
"""

import uuid
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast

from pydantic import ConfigDict, Field

from blockchainpype.evm.asset import EthereumAsset, EthereumAssetData
from blockchainpype.evm.blockchain.gas import GasConfiguration
from blockchainpype.evm.blockchain.identifier import EthereumAddress
from blockchainpype.evm.dapp.abi import EthereumABI, EthereumLocalFileABI
from blockchainpype.evm.dapp.contract import (
    EthereumContractConfiguration,
    EthereumSmartContract,
)
from blockchainpype.evm.transaction import EthereumTransaction

if TYPE_CHECKING:
    from blockchainpype.evm.wallet.wallet import EthereumWallet


class ERC20ContractConfiguration(EthereumContractConfiguration):
    """
    Configuration for ERC-20 token contracts.

    This class extends the base contract configuration with ERC-20 specific settings,
    including automatic loading of the standard ERC-20 ABI.

    Attributes:
        address (EthereumAddress): The token contract's address
        abi_configuration (EthereumABI): ABI configuration, defaults to the
            canonical ERC-20 interface ABI (ERC20.json)
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

    Amount conventions: get_total_supply/get_balance_of/get_allowance return
    decimal-adjusted (human-readable) amounts, dividing the on-chain value by
    10**decimals; the get_raw_* accessors expose the untouched on-chain integer
    amounts. The token's decimals are fetched once and cached.
    """

    def __init__(self, configuration: EthereumContractConfiguration):
        """
        Initialize the ERC-20 contract interface.

        Args:
            configuration (EthereumContractConfiguration): Contract configuration
                including address and ABI
        """
        super().__init__(configuration)

        self._decimals: int | None = None

    async def get_name(self) -> str:
        """
        Get the name of the token.
        """
        return cast(str, await self.functions.name().call())

    async def get_symbol(self) -> str:
        """
        Get the symbol of the token.
        """
        return cast(str, await self.functions.symbol().call())

    async def get_decimals(self) -> int:
        """
        Get the number of decimals of the token.

        The contract is lazily initialized when needed, and the value is
        fetched from the chain once and cached afterwards.
        """
        if self._decimals is None:
            if not self.is_initialized:
                await self.initialize()
            self._decimals = cast(int, await self.functions.decimals().call())
        return self._decimals

    async def _to_decimal_amount(self, raw_amount: int) -> Decimal:
        """Convert a raw on-chain amount to its decimal-adjusted representation."""
        decimals = await self.get_decimals()
        return Decimal(raw_amount) / Decimal(10**decimals)

    async def _to_raw_amount(self, amount: Decimal) -> int:
        """Convert a decimal-adjusted amount to its raw on-chain representation."""
        decimals = await self.get_decimals()
        return int(amount * (Decimal(10) ** decimals))

    async def get_raw_total_supply(self) -> int:
        """
        Get the total supply of the token in raw (smallest) units.

        Returns:
            int: The total token supply in raw units
        """
        return cast(int, await self.functions.totalSupply().call())

    async def get_total_supply(self) -> Decimal:
        """
        Get the total supply of the token, adjusted by the token's decimals.

        Returns:
            Decimal: The decimal-adjusted total token supply
        """
        return await self._to_decimal_amount(await self.get_raw_total_supply())

    async def get_raw_balance_of(self, address: EthereumAddress) -> int:
        """
        Get the token balance of an address in raw (smallest) units.

        Args:
            address (EthereumAddress): The address to check

        Returns:
            int: The token balance in raw units
        """
        return cast(int, await self.functions.balanceOf(address.raw).call())

    async def get_balance_of(self, address: EthereumAddress) -> Decimal:
        """
        Get the token balance of an address, adjusted by the token's decimals.

        Args:
            address (EthereumAddress): The address to check

        Returns:
            Decimal: The decimal-adjusted token balance
        """
        return await self._to_decimal_amount(await self.get_raw_balance_of(address))

    async def get_raw_allowance(
        self, owner: EthereumAddress, spender: EthereumAddress
    ) -> int:
        """
        Get the approved spending amount in raw (smallest) units.

        Args:
            owner (EthereumAddress): The token owner's address
            spender (EthereumAddress): The spender's address

        Returns:
            int: The approved amount in raw units
        """
        return cast(int, await self.functions.allowance(owner.raw, spender.raw).call())

    async def get_allowance(
        self, owner: EthereumAddress, spender: EthereumAddress
    ) -> Decimal:
        """
        Get the amount of tokens that a spender is allowed to spend on behalf of
        the owner, adjusted by the token's decimals.

        Args:
            owner (EthereumAddress): The token owner's address
            spender (EthereumAddress): The spender's address

        Returns:
            Decimal: The decimal-adjusted approved amount
        """
        return await self._to_decimal_amount(
            await self.get_raw_allowance(owner, spender)
        )

    async def _place_function_transaction(
        self,
        wallet: "EthereumWallet",
        function_name: str,
        args: list[Any],
        client_operation_id: str | None,
        gas_configuration: GasConfiguration | None = None,
    ) -> EthereumTransaction:
        """
        Build, sign and broadcast a state-changing contract call.

        The contract is lazily initialized, the wallet's nonce is synced when
        needed, and the transaction is built via wallet.build_transaction so the
        wallet's gas configuration applies unless one is passed explicitly.

        Args:
            wallet (EthereumWallet): The wallet signing and sending the transaction
            function_name (str): Name of the contract function to call
            args (list[Any]): Already-encoded (raw) function arguments
            client_operation_id (str | None): Optional operation ID; generated
                when not provided
            gas_configuration (GasConfiguration | None): Gas settings to
                estimate the fees with; the wallet's own configuration is used
                when omitted

        Returns:
            EthereumTransaction: The tracked, broadcast transaction
        """
        if not self.is_initialized:
            await self.initialize()

        function = self.functions[function_name](*args)
        tx_data = await wallet.build_transaction(
            function=function, gas_configuration=gas_configuration
        )

        if wallet.last_nonce is None:
            await wallet.sync_nonce()

        if client_operation_id is None:
            client_operation_id = (
                f"erc20-{function_name}-{self.address.string}-{uuid.uuid4().hex}"
            )

        return wallet.sign_and_send_transaction(
            client_operation_id=client_operation_id,
            tx_data=cast(dict[str, Any], dict(tx_data)),
        )

    async def place_transfer(
        self,
        wallet: "EthereumWallet",
        recipient: EthereumAddress,
        amount: Decimal,
        client_operation_id: str | None = None,
        gas_configuration: GasConfiguration | None = None,
    ) -> EthereumTransaction:
        """
        Sign and broadcast a transaction transferring tokens to a recipient.

        Args:
            wallet (EthereumWallet): The wallet holding the tokens; the contract
                configuration carries no wallet reference, so the signing wallet
                must be passed explicitly
            recipient (EthereumAddress): The recipient's address
            amount (Decimal): The decimal-adjusted amount of tokens to transfer
            client_operation_id (str | None): Optional operation ID for tracking
            gas_configuration (GasConfiguration | None): Optional gas settings
                overriding the wallet's own (e.g. a
                :class:`~blockchainpype.evm.dapp.gas.GasPriceCappedConfiguration`
                enforcing a caller's maximum gas price)

        Returns:
            EthereumTransaction: The tracked transfer transaction
        """
        raw_amount = await self._to_raw_amount(amount)
        return await self._place_function_transaction(
            wallet,
            "transfer",
            [recipient.raw, raw_amount],
            client_operation_id,
            gas_configuration=gas_configuration,
        )

    async def place_transfer_from(
        self,
        wallet: "EthereumWallet",
        sender: EthereumAddress,
        recipient: EthereumAddress,
        amount: Decimal,
        client_operation_id: str | None = None,
        gas_configuration: GasConfiguration | None = None,
    ) -> EthereumTransaction:
        """
        Sign and broadcast a transaction transferring tokens between addresses.

        This method is used for transferring tokens on behalf of another address
        that has approved the spending.

        Args:
            wallet (EthereumWallet): The wallet spending the approved allowance;
                passed explicitly since the contract holds no wallet reference
            sender (EthereumAddress): The token owner's address
            recipient (EthereumAddress): The recipient's address
            amount (Decimal): The decimal-adjusted amount of tokens to transfer
            client_operation_id (str | None): Optional operation ID for tracking
            gas_configuration (GasConfiguration | None): Optional gas settings
                overriding the wallet's own (e.g. a
                :class:`~blockchainpype.evm.dapp.gas.GasPriceCappedConfiguration`
                enforcing a caller's maximum gas price)

        Returns:
            EthereumTransaction: The tracked transfer transaction
        """
        raw_amount = await self._to_raw_amount(amount)
        return await self._place_function_transaction(
            wallet,
            "transferFrom",
            [sender.raw, recipient.raw, raw_amount],
            client_operation_id,
            gas_configuration=gas_configuration,
        )

    async def place_approve(
        self,
        wallet: "EthereumWallet",
        spender: EthereumAddress,
        amount: Decimal,
        client_operation_id: str | None = None,
        gas_configuration: GasConfiguration | None = None,
    ) -> EthereumTransaction:
        """
        Sign and broadcast a transaction approving a spender to spend tokens.

        Args:
            wallet (EthereumWallet): The wallet granting the approval; passed
                explicitly since the contract holds no wallet reference
            spender (EthereumAddress): The address to approve
            amount (Decimal): The decimal-adjusted amount of tokens to approve
            client_operation_id (str | None): Optional operation ID for tracking
            gas_configuration (GasConfiguration | None): Optional gas settings
                overriding the wallet's own (e.g. a
                :class:`~blockchainpype.evm.dapp.gas.GasPriceCappedConfiguration`
                enforcing a caller's maximum gas price)

        Returns:
            EthereumTransaction: The tracked approval transaction
        """
        raw_amount = await self._to_raw_amount(amount)
        return await self._place_function_transaction(
            wallet,
            "approve",
            [spender.raw, raw_amount],
            client_operation_id,
            gas_configuration=gas_configuration,
        )


class ERC20Token(EthereumAsset):
    """
    Representation of an ERC-20 token as an Ethereum asset.

    This class combines the ERC-20 contract interface with asset management
    capabilities, allowing the token to be treated as a standard asset
    while providing access to its contract functionality.

    Attributes:
        contract (ERC20Contract): The token's contract interface
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=False)

    contract: ERC20Contract

    async def initialize_data(self, auto_initialize_contract: bool = True) -> None:
        if self.data is not None:
            return

        if not self.contract.is_initialized:
            if auto_initialize_contract:
                await self.contract.initialize()
            else:
                raise ValueError("Contract is not initialized")

        self.data = EthereumAssetData(
            name=await self.contract.get_name(),
            symbol=await self.contract.get_symbol(),
            decimals=await self.contract.get_decimals(),
        )
