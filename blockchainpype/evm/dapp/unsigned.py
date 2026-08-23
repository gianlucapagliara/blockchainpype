"""
Shared convention for the unsigned transactions built by EVM dapp strategies.

Every EVM protocol strategy in this package exposes ``build_*`` methods that
are **build-only**: they return an :class:`EthereumTransaction` tracking object
in ``PENDING_BROADCAST`` state that is never signed nor broadcast, carrying the
web3 transaction parameters produced by
:meth:`~blockchainpype.evm.wallet.wallet.EthereumWallet.build_transaction`.

Those parameters live under a single agreed key of the transaction's
``other_data`` mapping, :data:`UNSIGNED_TX_DATA_KEY`. Producers should build
their result with :func:`build_unsigned_transaction` and consumers should read
it back with :func:`unsigned_tx_params`, so the convention stays discoverable
and identical across Uniswap, Aave and Polymarket.

Signing and broadcasting such a transaction is the caller's job::

    transaction = await strategy.build_swap_transaction(route)
    params = unsigned_tx_params(transaction)
    wallet.sign_and_send_transaction(
        client_operation_id=transaction.client_operation_id,
        tx_data=dict(params),
    )
"""

from typing import TYPE_CHECKING, Any, Final, cast

from financepype.operations.transactions.models import BlockchainTransactionState
from web3.types import TxParams

from blockchainpype.evm.transaction import EthereumTransaction

if TYPE_CHECKING:
    from blockchainpype.evm.wallet.wallet import EthereumWallet

__all__ = [
    "UNSIGNED_TX_DATA_KEY",
    "build_unsigned_transaction",
    "unsigned_tx_params",
]

#: Key of ``EthereumTransaction.other_data`` carrying the built (but unsigned)
#: web3 transaction parameters of a dapp ``build_*`` method.
UNSIGNED_TX_DATA_KEY: Final[str] = "tx_data"


def build_unsigned_transaction(
    client_operation_id: str,
    wallet: "EthereumWallet",
    tx_params: TxParams,
    extra_data: dict[str, Any] | None = None,
) -> EthereumTransaction:
    """Wrap built web3 transaction parameters into an unsigned transaction.

    Args:
        client_operation_id: Tracking id of the operation
        wallet: The wallet the parameters were built for; supplies the owner
            identifier and the creation timestamp
        tx_params: The parameters returned by ``wallet.build_transaction``
        extra_data: Additional ``other_data`` entries (e.g. a market id);
            they may not override :data:`UNSIGNED_TX_DATA_KEY`

    Returns:
        EthereumTransaction: An unsigned transaction in ``PENDING_BROADCAST``
        state carrying ``other_data[UNSIGNED_TX_DATA_KEY]``

    Raises:
        ValueError: If ``extra_data`` tries to override the reserved key
    """
    other_data: dict[str, Any] = dict(extra_data or {})
    if UNSIGNED_TX_DATA_KEY in other_data:
        raise ValueError(
            f"extra_data must not override the reserved {UNSIGNED_TX_DATA_KEY!r} key"
        )
    other_data[UNSIGNED_TX_DATA_KEY] = dict(tx_params)

    return EthereumTransaction(
        client_operation_id=client_operation_id,
        owner_identifier=wallet.identifier,
        creation_timestamp=wallet.current_timestamp,
        current_state=BlockchainTransactionState.PENDING_BROADCAST,
        signed_transaction=None,
        other_data=other_data,
    )


def unsigned_tx_params(transaction: EthereumTransaction) -> TxParams:
    """Read back the web3 parameters of an unsigned dapp transaction.

    Args:
        transaction: A transaction produced by :func:`build_unsigned_transaction`

    Returns:
        TxParams: The built transaction parameters

    Raises:
        ValueError: If the transaction carries no unsigned parameters
    """
    params = transaction.other_data.get(UNSIGNED_TX_DATA_KEY)
    if params is None:
        raise ValueError(
            f"Transaction {transaction.client_operation_id} carries no "
            f"{UNSIGNED_TX_DATA_KEY!r} entry: it was not built by an EVM dapp "
            "strategy build_* method"
        )
    return cast(TxParams, dict(params))
