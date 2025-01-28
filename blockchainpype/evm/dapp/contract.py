"""
This module provides base classes for interacting with Ethereum smart contracts.
It implements contract configuration, initialization, and function calling capabilities
through Web3.py's AsyncContract interface.
"""

from typing import cast

from financepype.operators.dapps.dapp import (
    DecentralizedApplication,
    DecentralizedApplicationConfiguration,
)
from pydantic import ConfigDict
from web3.contract import AsyncContract
from web3.contract.async_contract import AsyncContractFunctions

from blockchainpype.evm.blockchain.blockchain import EthereumBlockchain
from blockchainpype.evm.blockchain.identifier import EthereumAddress
from blockchainpype.evm.dapp.abi import EthereumABI


class EthereumContractConfiguration(DecentralizedApplicationConfiguration):
    """
    Configuration class for Ethereum smart contracts.

    This class defines the essential parameters needed to interact with an Ethereum
    smart contract, including its address and ABI configuration.

    Attributes:
        address (EthereumAddress): The deployed contract's address
        abi_configuration (EthereumABI): The contract's ABI configuration
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    address: EthereumAddress
    abi_configuration: EthereumABI


class EthereumSmartContract(DecentralizedApplication):
    """
    Base class for interacting with Ethereum smart contracts.

    This class provides the foundation for contract interaction, including contract
    initialization, function access, and state management. It uses Web3.py's
    AsyncContract for non-blocking contract operations.

    Attributes:
        _contract (AsyncContract | None): The underlying Web3 contract instance
    """

    def __init__(self, configuration: EthereumContractConfiguration):
        """
        Initialize the smart contract interface.

        Args:
            configuration (EthereumContractConfiguration): Contract configuration
                including address and ABI
        """
        super().__init__(configuration)

        self._contract: AsyncContract | None = None

    @property
    def configuration(self) -> EthereumContractConfiguration:
        """
        Get the contract's configuration.

        Returns:
            EthereumContractConfiguration: The contract configuration
        """
        return cast(EthereumContractConfiguration, super().configuration)

    @property
    def blockchain(self) -> EthereumBlockchain:
        """
        Get the blockchain instance.

        Returns:
            EthereumBlockchain: The blockchain interface
        """
        return cast(EthereumBlockchain, super().blockchain)

    @property
    def address(self) -> EthereumAddress:
        """
        Get the contract's address.

        Returns:
            EthereumAddress: The deployed contract's address
        """
        return self.configuration.address

    @property
    def contract(self) -> AsyncContract | None:
        """
        Get the underlying Web3 contract instance.

        Returns:
            AsyncContract | None: The contract instance if initialized, None otherwise
        """
        return self._contract

    @property
    def functions(self) -> AsyncContractFunctions:
        """
        Get access to the contract's functions.

        Returns:
            AsyncContractFunctions: Interface to call contract functions

        Raises:
            ValueError: If the contract is not initialized
        """
        if self.contract is None:
            raise ValueError("Contract is not initialized")

        return self.contract.functions

    @property
    def is_initialized(self) -> bool:
        """
        Check if the contract is initialized.

        Returns:
            bool: True if the contract is initialized, False otherwise
        """
        return self.contract is not None

    async def initialize(self) -> None:
        """
        Initialize the contract by loading its ABI and creating the Web3 contract instance.

        This method fetches the contract's ABI and creates an AsyncContract instance
        for interaction. It only initializes once, subsequent calls have no effect.
        """
        if self.is_initialized:
            return

        abi = await self.configuration.abi_configuration.get_abi()
        self._contract = self.blockchain.web3.eth.contract(
            address=self.address.raw, abi=abi
        )
