"""
Unit tests for SolanaBlockchain against a mocked solana-py RPC provider.

The RPC boundary is mocked at the provider level (the same seam solana-py's
AsyncClient uses), with canned JSON-RPC payloads shaped exactly like real node
responses and parsed by the real solders response parsers.
"""

import json
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pytest
from financepype.operations.transactions.models import BlockchainTransactionState
from financepype.platforms.blockchain import BlockchainPlatform
from solana.rpc.async_api import AsyncClient
from solana.rpc.core import RPCException
from solders.hash import Hash
from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.system_program import TransferParams, transfer
from solders.transaction import VersionedTransaction
from solders.transaction_status import TransactionStatus, UiConfirmedBlock

from blockchainpype.solana.asset import SolanaNativeAsset
from blockchainpype.solana.blockchain.blockchain import (
    SolanaBlockchain,
    SolanaBlockchainType,
)
from blockchainpype.solana.blockchain.configuration import (
    SolanaBlockchainConfiguration,
    SolanaConnectivityConfiguration,
)
from blockchainpype.solana.blockchain.identifier import (
    SolanaAddress,
    SolanaTransactionSignature,
)
from blockchainpype.solana.explorer.solscan import SolscanConfiguration
from blockchainpype.solana.transaction import SolanaTransaction
from blockchainpype.solana.wallet.identifier import SolanaWalletIdentifier

# === Mocked-RPC test harness ===

BLOCKHASH = "4TLzN2RAACFnd5TYpHcUi76pC3V1qkggRF29HWk2VLeT"
SLOT = 331535086
BLOCK_TIME = 1754000000
FEE_LAMPORTS = 5000
SYSTEM_PROGRAM = "11111111111111111111111111111111"


@dataclass
class RPCErrorResult:
    """Marks a canned response as a JSON-RPC error object."""

    code: int
    message: str
    data: Any = None


class CannedRPCProvider:
    """
    In-memory replacement for solana-py's AsyncHTTPProvider.

    Values in ``results`` are returned as the JSON-RPC ``result`` and parsed by
    the real solders response parsers; RPCErrorResult values are parsed as
    JSON-RPC errors and raised as RPCException (matching solana-py's
    error handling); exception instances are raised as-is. Unexpected methods
    fail the test. All performed requests are recorded in ``calls``.
    """

    def __init__(self, results: dict[str, Any]) -> None:
        self.results = dict(results)
        self.calls: list[dict[str, Any]] = []

    def requests_for(self, method: str) -> list[dict[str, Any]]:
        return [call for call in self.calls if call["method"] == method]

    async def make_request(self, body: Any, parser: Any) -> Any:
        request = json.loads(body.to_json())
        self.calls.append(request)
        method = request["method"]
        if method not in self.results:
            raise AssertionError(f"Unexpected RPC method: {method}")

        value = self.results[method]
        if isinstance(value, list):
            # A list is a queue of consecutive responses
            value = value.pop(0) if len(value) > 1 else value[0]
        if isinstance(value, BaseException):
            raise value
        if isinstance(value, RPCErrorResult):
            error: dict[str, Any] = {"code": value.code, "message": value.message}
            if value.data is not None:
                error["data"] = value.data
            payload = {"jsonrpc": "2.0", "id": request["id"], "error": error}
            parsed = parser.from_json(json.dumps(payload))
            raise RPCException(parsed)

        payload = {"jsonrpc": "2.0", "id": request["id"], "result": value}
        return parser.from_json(json.dumps(payload))


def build_blockchain(
    results: dict[str, Any],
    explorer: SolscanConfiguration | None = None,
) -> SolanaBlockchain:
    """Build a SolanaBlockchain wired to a CannedRPCProvider."""
    client = AsyncClient("http://127.0.0.1:8899")
    client._provider = CannedRPCProvider(results)  # type: ignore[assignment]
    configuration = SolanaBlockchainConfiguration(
        platform=BlockchainPlatform(
            identifier="solana",
            type=SolanaBlockchainType,
            chain_id=None,
        ),
        connectivity=SolanaConnectivityConfiguration(rpc_provider=client),
        explorer=explorer,
    )
    return SolanaBlockchain(configuration=configuration)


def provider_of(blockchain: SolanaBlockchain) -> CannedRPCProvider:
    provider = blockchain.rpc_client._provider
    assert isinstance(provider, CannedRPCProvider)
    return provider


def make_signed_transfer(
    keypair: Keypair, blockhash: Hash
) -> tuple[VersionedTransaction, Signature]:
    """Build a real signed SOL transfer; returns (transaction, signature)."""
    instruction = transfer(
        TransferParams(
            from_pubkey=keypair.pubkey(),
            to_pubkey=Keypair().pubkey(),
            lamports=1000,
        )
    )
    message = Message.new_with_blockhash([instruction], keypair.pubkey(), blockhash)
    transaction = VersionedTransaction(message, [keypair])
    return transaction, transaction.signatures[0]


def transaction_payload(
    sender: Pubkey,
    recipient: Pubkey,
    signature: Signature,
    err: dict[str, Any] | None = None,
    block_time: int | None = BLOCK_TIME,
) -> dict[str, Any]:
    """Realistic getTransaction result payload (json encoding)."""
    status: dict[str, Any] = {"Err": err} if err is not None else {"Ok": None}
    return {
        "slot": SLOT,
        "transaction": {
            "signatures": [str(signature)],
            "message": {
                "header": {
                    "numRequiredSignatures": 1,
                    "numReadonlySignedAccounts": 0,
                    "numReadonlyUnsignedAccounts": 1,
                },
                "accountKeys": [str(sender), str(recipient), SYSTEM_PROGRAM],
                "recentBlockhash": BLOCKHASH,
                "instructions": [
                    {
                        "programIdIndex": 2,
                        "accounts": [0, 1],
                        "data": "3Bxs4NN8M2Yn4TLb",
                        "stackHeight": None,
                    }
                ],
            },
        },
        "meta": {
            "err": err,
            "status": status,
            "fee": FEE_LAMPORTS,
            "preBalances": [1000000000, 0, 1],
            "postBalances": [999994000, 1000, 1],
            "innerInstructions": [],
            "logMessages": [
                f"Program {SYSTEM_PROGRAM} invoke [1]",
                f"Program {SYSTEM_PROGRAM} success",
            ],
            "preTokenBalances": [],
            "postTokenBalances": [],
            "rewards": [],
            "loadedAddresses": {"readonly": [], "writable": []},
            "computeUnitsConsumed": 150,
        },
        "blockTime": block_time,
        "version": "legacy",
    }


def signature_status_payload(
    err: dict[str, Any] | None = None,
    confirmation_status: str | None = "finalized",
) -> dict[str, Any]:
    """Realistic getSignatureStatuses result payload."""
    status: dict[str, Any] = {"Err": err} if err is not None else {"Ok": None}
    return {
        "context": {"slot": SLOT + 10},
        "value": [
            {
                "slot": SLOT,
                "confirmations": None,
                "err": err,
                "status": status,
                "confirmationStatus": confirmation_status,
            }
        ],
    }


BLOCK_PAYLOAD: dict[str, Any] = {
    "blockHeight": 300000000,
    "blockTime": BLOCK_TIME,
    "blockhash": BLOCKHASH,
    "parentSlot": SLOT - 1,
    "previousBlockhash": "9aE476sH92Vz7DMPyq5WLPkrKWivxeuTKEFKd2sZZcde",
    "transactions": [],
    "rewards": [],
}

LATEST_BLOCKHASH_PAYLOAD: dict[str, Any] = {
    "context": {"slot": SLOT},
    "value": {"blockhash": BLOCKHASH, "lastValidBlockHeight": 300000000},
}


def balance_payload(lamports: int) -> dict[str, Any]:
    return {"context": {"slot": SLOT}, "value": lamports}


def build_solana_transaction(
    blockchain: SolanaBlockchain,
    keypair: Keypair,
    signed_transaction: VersionedTransaction | None,
) -> SolanaTransaction:
    return SolanaTransaction(
        client_operation_id="op-1",
        owner_identifier=SolanaWalletIdentifier(
            platform=blockchain.configuration.platform,
            name=None,
            address=SolanaAddress.from_raw(keypair.pubkey()),
        ),
        creation_timestamp=time.time(),
        signed_transaction=signed_transaction,
    )


# === Tests ===


class TestInitialization:
    def test_blockchain_initialization(self) -> None:
        blockchain = build_blockchain({})

        assert blockchain.platform.identifier == "solana"
        assert blockchain.platform.type == SolanaBlockchainType
        assert isinstance(blockchain.native_asset, SolanaNativeAsset)
        assert blockchain.native_asset.data.symbol == "SOL"
        assert blockchain.native_asset.data.decimals == 9
        assert blockchain.explorer is None

    def test_blockchain_with_explorer(self) -> None:
        blockchain = build_blockchain({}, explorer=SolscanConfiguration())

        assert blockchain.explorer is not None
        assert blockchain.explorer.base_url == "https://solscan.io"

    def test_current_timestamp_is_wall_clock(self) -> None:
        blockchain = build_blockchain({})

        before = time.time()
        timestamp = blockchain.current_timestamp
        after = time.time()

        assert before <= timestamp <= after


class TestBlockData:
    async def test_fetch_block_number(self) -> None:
        blockchain = build_blockchain({"getSlot": SLOT})

        assert await blockchain.fetch_block_number() == SLOT

    async def test_fetch_block_timestamp(self) -> None:
        blockchain = build_blockchain({"getBlockTime": BLOCK_TIME})

        assert await blockchain.fetch_block_timestamp(SLOT) == BLOCK_TIME

    async def test_fetch_block_timestamp_missing_raises(self) -> None:
        blockchain = build_blockchain({"getBlockTime": None})

        with pytest.raises(ValueError, match="does not have a timestamp"):
            await blockchain.fetch_block_timestamp(SLOT)

    async def test_fetch_recent_blockhash(self) -> None:
        blockchain = build_blockchain({"getLatestBlockhash": LATEST_BLOCKHASH_PAYLOAD})

        blockhash = await blockchain.fetch_recent_blockhash()

        assert blockhash == Hash.from_string(BLOCKHASH)

    async def test_fetch_block_data_supports_versioned_transactions(self) -> None:
        blockchain = build_blockchain({"getBlock": BLOCK_PAYLOAD})

        block = await blockchain.fetch_block_data(SLOT)

        assert isinstance(block, UiConfirmedBlock)
        assert block.block_time == BLOCK_TIME
        assert block.blockhash == Hash.from_string(BLOCKHASH)

        # Regression: v0-transaction blocks error without this parameter
        request = provider_of(blockchain).requests_for("getBlock")[0]
        assert request["params"][0] == SLOT
        assert request["params"][1]["maxSupportedTransactionVersion"] == 0


class TestBalances:
    async def test_fetch_native_asset_balance_is_decimal_adjusted(self) -> None:
        blockchain = build_blockchain({"getBalance": balance_payload(2_500_000_000)})
        address = SolanaAddress.from_raw(Keypair().pubkey())

        balance = await blockchain.fetch_native_asset_balance(address)

        assert balance == Decimal("2.5")

    def test_derive_associated_token_account_matches_spl_derivation(self) -> None:
        from spl.token.instructions import get_associated_token_address

        owner = SolanaAddress.from_raw(Keypair().pubkey())
        mint = SolanaAddress.from_raw(Keypair().pubkey())

        ata = SolanaBlockchain.derive_associated_token_account(owner, mint)

        assert ata.raw == get_associated_token_address(owner.raw, mint.raw)

    async def test_fetch_spl_token_balance_queries_ata_and_scales(self) -> None:
        blockchain = build_blockchain(
            {
                "getTokenAccountBalance": {
                    "context": {"slot": SLOT},
                    "value": {
                        "amount": "123450000",
                        "decimals": 6,
                        "uiAmount": 123.45,
                        "uiAmountString": "123.45",
                    },
                }
            }
        )
        owner = SolanaAddress.from_raw(Keypair().pubkey())
        mint = SolanaAddress.from_raw(Keypair().pubkey())
        expected_ata = SolanaBlockchain.derive_associated_token_account(owner, mint)

        balance = await blockchain.fetch_spl_token_balance(owner, mint)

        assert balance == Decimal("123.45")
        request = provider_of(blockchain).requests_for("getTokenAccountBalance")[0]
        assert request["params"][0] == expected_ata.string

    async def test_fetch_spl_token_balance_missing_account_is_zero(self) -> None:
        blockchain = build_blockchain(
            {
                "getTokenAccountBalance": RPCErrorResult(
                    -32602, "Invalid param: could not find account"
                )
            }
        )
        owner = SolanaAddress.from_raw(Keypair().pubkey())
        mint = SolanaAddress.from_raw(Keypair().pubkey())

        balance = await blockchain.fetch_spl_token_balance(owner, mint)

        assert balance == Decimal(0)

    async def test_fetch_spl_token_balance_other_errors_propagate(self) -> None:
        blockchain = build_blockchain(
            {
                "getTokenAccountBalance": RPCErrorResult(
                    -32005, "Node is unhealthy", data={}
                )
            }
        )
        owner = SolanaAddress.from_raw(Keypair().pubkey())
        mint = SolanaAddress.from_raw(Keypair().pubkey())

        with pytest.raises(RPCException):
            await blockchain.fetch_spl_token_balance(owner, mint)


class TestSendTransaction:
    async def test_send_signed_transaction_returns_signature(self) -> None:
        # Regression: the SendTransactionResp must be unwrapped (resp.value)
        # before building the identifier, or every broadcast fails
        keypair = Keypair()
        signed, signature = make_signed_transfer(keypair, Hash.from_string(BLOCKHASH))
        blockchain = build_blockchain({"sendTransaction": str(signature)})

        tx_sig = await blockchain.send_signed_transaction(signed)

        assert isinstance(tx_sig, SolanaTransactionSignature)
        assert tx_sig.raw == signature
        assert tx_sig.string == str(signature)

    async def test_send_transaction_broadcasts_and_reports_signature(self) -> None:
        keypair = Keypair()
        signed, signature = make_signed_transfer(keypair, Hash.from_string(BLOCKHASH))
        blockchain = build_blockchain(
            {"sendTransaction": str(signature)},
            explorer=SolscanConfiguration(),
        )
        transaction = build_solana_transaction(blockchain, keypair, signed)

        update = await blockchain.send_transaction(transaction)

        assert update.new_state == BlockchainTransactionState.BROADCASTED
        assert update.client_transaction_id == "op-1"
        assert update.transaction_id is not None
        assert update.transaction_id.string == str(signature)
        assert update.explorer_link == f"https://solscan.io/tx/{signature}"
        assert update.update_timestamp > 0

    async def test_send_transaction_rpc_error_reports_rejected(self) -> None:
        keypair = Keypair()
        signed, _ = make_signed_transfer(keypair, Hash.from_string(BLOCKHASH))
        blockchain = build_blockchain(
            {
                "sendTransaction": RPCErrorResult(
                    -32002,
                    "Transaction simulation failed: Blockhash not found",
                    data={
                        "accounts": None,
                        "err": "BlockhashNotFound",
                        "logs": [],
                        "unitsConsumed": 0,
                        "returnData": None,
                    },
                )
            }
        )
        transaction = build_solana_transaction(blockchain, keypair, signed)

        update = await blockchain.send_transaction(transaction)

        assert update.new_state == BlockchainTransactionState.REJECTED
        assert update.transaction_id is None
        assert isinstance(update.other_data["exception"], RPCException)

    async def test_send_transaction_unsigned_raises(self) -> None:
        blockchain = build_blockchain({})
        transaction = build_solana_transaction(blockchain, Keypair(), None)

        with pytest.raises(ValueError, match="not signed"):
            await blockchain.send_transaction(transaction)


class TestTransactionStatus:
    async def test_fetch_transaction_status_searches_history(self) -> None:
        keypair = Keypair()
        _, signature = make_signed_transfer(keypair, Hash.from_string(BLOCKHASH))
        blockchain = build_blockchain(
            {"getSignatureStatuses": signature_status_payload()}
        )
        tx_sig = SolanaTransactionSignature.from_raw(signature)

        status = await blockchain.fetch_transaction_status(tx_sig)

        assert isinstance(status, TransactionStatus)
        assert status.err is None
        assert status.slot == SLOT

        # Regression: without searchTransactionHistory older transactions
        # never resolve and stay BROADCASTED forever
        request = provider_of(blockchain).requests_for("getSignatureStatuses")[0]
        assert request["params"][0] == [str(signature)]
        assert request["params"][1] == {"searchTransactionHistory": True}

    async def test_fetch_transaction_status_unknown_signature(self) -> None:
        blockchain = build_blockchain(
            {
                "getSignatureStatuses": {
                    "context": {"slot": SLOT},
                    "value": [None],
                }
            }
        )
        tx_sig = SolanaTransactionSignature.from_raw(Signature.default())

        assert await blockchain.fetch_transaction_status(tx_sig) is None


class TestFetchTransaction:
    async def test_fetch_transaction_receipt_maps_rpc_response(self) -> None:
        keypair = Keypair()
        recipient = Keypair().pubkey()
        _, signature = make_signed_transfer(keypair, Hash.from_string(BLOCKHASH))
        blockchain = build_blockchain(
            {
                "getTransaction": transaction_payload(
                    keypair.pubkey(), recipient, signature
                )
            }
        )
        tx_sig = SolanaTransactionSignature.from_raw(signature)

        receipt = await blockchain.fetch_transaction_receipt(tx_sig)

        assert receipt is not None
        assert receipt.transaction_id == tx_sig
        assert receipt.slot == SLOT
        assert receipt.block_time == BLOCK_TIME
        assert receipt.err is None
        assert receipt.fee == FEE_LAMPORTS
        assert receipt.fee_amount == Decimal(FEE_LAMPORTS)
        assert receipt.pre_balances == [1000000000, 0, 1]
        assert receipt.post_balances == [999994000, 1000, 1]
        assert receipt.logs == [
            f"Program {SYSTEM_PROGRAM} invoke [1]",
            f"Program {SYSTEM_PROGRAM} success",
        ]
        assert receipt.compute_units_consumed == 150

        request = provider_of(blockchain).requests_for("getTransaction")[0]
        assert request["params"][0] == str(signature)
        assert request["params"][1]["maxSupportedTransactionVersion"] == 0

    async def test_fetch_transaction_receipt_not_found(self) -> None:
        blockchain = build_blockchain({"getTransaction": None})
        tx_sig = SolanaTransactionSignature.from_raw(Signature.default())

        assert await blockchain.fetch_transaction_receipt(tx_sig) is None

    async def test_fetch_transaction_not_found(self) -> None:
        blockchain = build_blockchain({"getTransaction": None})
        tx_sig = SolanaTransactionSignature.from_raw(Signature.default())

        assert await blockchain.fetch_transaction(tx_sig) is None

    async def test_fetch_transaction_invalid_id_raises(self) -> None:
        blockchain = build_blockchain({})
        address = SolanaAddress.from_raw(Keypair().pubkey())

        with pytest.raises(ValueError, match="Invalid transaction id"):
            await blockchain.fetch_transaction(address)

    async def test_fetch_confirmed_transaction(self) -> None:
        keypair = Keypair()
        recipient = Keypair().pubkey()
        _, signature = make_signed_transfer(keypair, Hash.from_string(BLOCKHASH))
        blockchain = build_blockchain(
            {
                "getTransaction": transaction_payload(
                    keypair.pubkey(), recipient, signature
                )
            },
            explorer=SolscanConfiguration(),
        )
        tx_sig = SolanaTransactionSignature.from_raw(signature)

        transaction = await blockchain.fetch_transaction(tx_sig)

        assert transaction is not None
        assert transaction.current_state == BlockchainTransactionState.CONFIRMED
        assert transaction.operator_operation_id == tx_sig
        assert transaction.creation_timestamp == BLOCK_TIME

        assert transaction.receipt is not None
        assert transaction.receipt.fee == FEE_LAMPORTS

        # 5000 lamports at 9 decimals
        assert transaction.fee is not None
        assert transaction.fee.amount == Decimal("0.000005")
        assert transaction.fee.asset is blockchain.native_asset

        assert isinstance(transaction.owner_identifier, SolanaWalletIdentifier)
        assert transaction.owner_identifier.address.string == str(keypair.pubkey())

        assert transaction.explorer_link == f"https://solscan.io/tx/{signature}"

    async def test_fetch_failed_transaction(self) -> None:
        keypair = Keypair()
        recipient = Keypair().pubkey()
        _, signature = make_signed_transfer(keypair, Hash.from_string(BLOCKHASH))
        error = {"InstructionError": [0, {"Custom": 1}]}
        blockchain = build_blockchain(
            {
                "getTransaction": transaction_payload(
                    keypair.pubkey(), recipient, signature, err=error
                )
            }
        )
        tx_sig = SolanaTransactionSignature.from_raw(signature)

        transaction = await blockchain.fetch_transaction(tx_sig)

        assert transaction is not None
        assert transaction.current_state == BlockchainTransactionState.FAILED
        assert transaction.receipt is not None
        assert transaction.receipt.err is not None

    async def test_fetch_transaction_without_block_time_uses_wall_clock(self) -> None:
        keypair = Keypair()
        recipient = Keypair().pubkey()
        _, signature = make_signed_transfer(keypair, Hash.from_string(BLOCKHASH))
        blockchain = build_blockchain(
            {
                "getTransaction": transaction_payload(
                    keypair.pubkey(), recipient, signature, block_time=None
                )
            }
        )
        tx_sig = SolanaTransactionSignature.from_raw(signature)

        before = time.time()
        transaction = await blockchain.fetch_transaction(tx_sig)
        after = time.time()

        assert transaction is not None
        assert before <= transaction.creation_timestamp <= after
