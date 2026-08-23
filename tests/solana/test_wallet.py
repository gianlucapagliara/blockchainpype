"""
Unit tests for SolanaWallet: signing (legacy and versioned), sign-and-send
lifecycle, balance fetching (native and SPL via associated token accounts),
and transaction status polling. The RPC boundary is mocked with the canned
provider harness from test_blockchain.
"""

import asyncio
import time
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from financepype.operations.transactions.models import BlockchainTransactionState
from financepype.simulations.balances.tracking.tracker import BalanceType
from solders.hash import Hash
from solders.keypair import Keypair
from solders.message import Message, MessageAddressTableLookup, MessageV0
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.system_program import TransferParams, transfer
from solders.transaction import Transaction, VersionedTransaction

from blockchainpype.solana.asset import SolanaAsset, SolanaAssetData
from blockchainpype.solana.blockchain.blockchain import SolanaBlockchain
from blockchainpype.solana.blockchain.identifier import (
    SolanaAddress,
    SolanaTransactionSignature,
)
from blockchainpype.solana.dapp.token import SPLToken
from blockchainpype.solana.explorer.solscan import SolscanConfiguration
from blockchainpype.solana.transaction import SolanaTransaction
from blockchainpype.solana.wallet.identifier import SolanaWalletIdentifier
from blockchainpype.solana.wallet.signer import SolanaSigner, SolanaSignerConfiguration
from blockchainpype.solana.wallet.wallet import (
    SolanaWallet,
    SolanaWalletConfiguration,
)
from tests.solana.test_blockchain import (
    BLOCK_TIME,
    BLOCKHASH,
    FEE_LAMPORTS,
    SLOT,
    balance_payload,
    build_blockchain,
    provider_of,
    signature_status_payload,
    transaction_payload,
)


def build_wallet(
    results: dict[str, Any],
    keypair: Keypair | None = None,
    with_signer: bool = True,
    explorer: SolscanConfiguration | None = None,
    default_tx_wait: timedelta = timedelta(seconds=5),
) -> tuple[SolanaWallet, SolanaBlockchain, Keypair]:
    """Build a SolanaWallet bound to a mock-RPC blockchain."""
    keypair = keypair or Keypair()
    # The wallet schedules a native-balance refresh at construction time
    results = {"getBalance": balance_payload(2_500_000_000), **results}
    blockchain = build_blockchain(results, explorer=explorer)
    configuration = SolanaWalletConfiguration(
        identifier=SolanaWalletIdentifier(
            platform=blockchain.configuration.platform,
            name=None,
            address=SolanaAddress.from_raw(keypair.pubkey()),
        ),
        signer=SolanaSignerConfiguration(private_key=str(keypair))
        if with_signer
        else None,
        default_tx_wait=default_tx_wait,
    )
    wallet = SolanaWallet(configuration, blockchain=blockchain)
    return wallet, blockchain, keypair


def make_unsigned_legacy(keypair: Keypair, blockhash: Hash) -> Transaction:
    instruction = transfer(
        TransferParams(
            from_pubkey=keypair.pubkey(),
            to_pubkey=Keypair().pubkey(),
            lamports=1000,
        )
    )
    message = Message.new_with_blockhash([instruction], keypair.pubkey(), blockhash)
    return Transaction.new_unsigned(message)


def make_unsigned_versioned(
    keypair: Keypair, blockhash: Hash, v0: bool
) -> VersionedTransaction:
    instruction = transfer(
        TransferParams(
            from_pubkey=keypair.pubkey(),
            to_pubkey=Keypair().pubkey(),
            lamports=1000,
        )
    )
    if v0:
        message: Message | MessageV0 = MessageV0.try_compile(
            keypair.pubkey(), [instruction], [], blockhash
        )
    else:
        message = Message.new_with_blockhash([instruction], keypair.pubkey(), blockhash)
    num_signers = message.header.num_required_signatures
    return VersionedTransaction.populate(message, [Signature.default()] * num_signers)


class TestInitialization:
    async def test_wallet_initialization(self) -> None:
        wallet, blockchain, keypair = build_wallet({})

        assert wallet.address.raw == keypair.pubkey()
        assert wallet.blockchain is blockchain
        assert wallet.signer is not None
        assert wallet.DEFAULT_TRANSACTION_CLASS is SolanaTransaction

    async def test_wallet_without_signer(self) -> None:
        wallet, _, _ = build_wallet({}, with_signer=False)

        assert wallet.signer is None

    async def test_current_timestamp_is_wall_clock(self) -> None:
        wallet, _, _ = build_wallet({})

        before = time.time()
        timestamp = wallet.current_timestamp
        after = time.time()

        assert before <= timestamp <= after


class TestSignTransaction:
    async def test_sign_legacy_transaction(self) -> None:
        wallet, _, keypair = build_wallet({})
        blockhash = Hash.from_string(BLOCKHASH)
        transaction = make_unsigned_legacy(keypair, blockhash)

        signed = wallet.sign_transaction(transaction, blockhash)

        assert isinstance(signed, Transaction)
        assert signed.signatures[0] != Signature.default()
        assert signed.verify_with_results() == [True]

    async def test_sign_versioned_transaction_with_legacy_message(self) -> None:
        # Regression: this branch used to feed CompiledInstruction objects to
        # Message.new_with_blockhash (TypeError) and then attach the new
        # signatures to the OLD message
        wallet, _, keypair = build_wallet({})
        blockhash = Hash.from_string(BLOCKHASH)
        transaction = make_unsigned_versioned(keypair, blockhash, v0=False)

        new_blockhash = Hash.new_unique()
        signed = wallet.sign_transaction(transaction, new_blockhash)

        assert isinstance(signed, VersionedTransaction)
        assert signed.message.recent_blockhash == new_blockhash
        assert signed.verify_with_results() == [True]

    async def test_sign_versioned_transaction_with_v0_message(self) -> None:
        wallet, _, keypair = build_wallet({})
        blockhash = Hash.from_string(BLOCKHASH)
        transaction = make_unsigned_versioned(keypair, blockhash, v0=True)

        new_blockhash = Hash.new_unique()
        signed = wallet.sign_transaction(transaction, new_blockhash)

        assert isinstance(signed, VersionedTransaction)
        assert isinstance(signed.message, MessageV0)
        assert signed.message.recent_blockhash == new_blockhash
        assert signed.verify_with_results() == [True]

    async def test_sign_versioned_transaction_preserves_lookup_tables(self) -> None:
        # Regression: address table lookups used to be dropped when re-signing
        wallet, _, keypair = build_wallet({})
        blockhash = Hash.from_string(BLOCKHASH)
        transaction = make_unsigned_versioned(keypair, blockhash, v0=True)
        assert isinstance(transaction.message, MessageV0)

        lookup = MessageAddressTableLookup(
            account_key=Pubkey.new_unique(),
            writable_indexes=bytes([0, 1]),
            readonly_indexes=bytes([2]),
        )
        message_with_lookups = MessageV0(
            header=transaction.message.header,
            account_keys=transaction.message.account_keys,
            recent_blockhash=blockhash,
            instructions=transaction.message.instructions,
            address_table_lookups=[lookup],
        )
        transaction = VersionedTransaction.populate(
            message_with_lookups, [Signature.default()]
        )

        new_blockhash = Hash.new_unique()
        signed = wallet.sign_transaction(transaction, new_blockhash)

        assert isinstance(signed, VersionedTransaction)
        assert isinstance(signed.message, MessageV0)
        assert signed.message.address_table_lookups == [lookup]
        assert signed.message.account_keys == message_with_lookups.account_keys
        assert signed.message.recent_blockhash == new_blockhash
        assert signed.verify_with_results() == [True]

    async def test_sign_with_additional_signers(self) -> None:
        wallet, _, keypair = build_wallet({})
        second_signer = Keypair()
        blockhash = Hash.from_string(BLOCKHASH)

        # A transfer from the second signer's account, fee paid by the wallet:
        # both accounts must sign
        instruction = transfer(
            TransferParams(
                from_pubkey=second_signer.pubkey(),
                to_pubkey=Keypair().pubkey(),
                lamports=1000,
            )
        )
        message = Message.new_with_blockhash([instruction], keypair.pubkey(), blockhash)
        assert message.header.num_required_signatures == 2
        transaction = VersionedTransaction.populate(message, [Signature.default()] * 2)

        signed = wallet.sign_transaction(
            transaction,
            blockhash,
            additional_signers=[
                SolanaSigner(SolanaSignerConfiguration(private_key=str(second_signer)))
            ],
        )

        assert signed.verify_with_results() == [True, True]

    async def test_sign_without_signer_raises(self) -> None:
        wallet, _, keypair = build_wallet({}, with_signer=False)
        blockhash = Hash.from_string(BLOCKHASH)
        transaction = make_unsigned_legacy(keypair, blockhash)

        with pytest.raises(ValueError, match="Signer is not initialized"):
            wallet.sign_transaction(transaction, blockhash)


class TestSignAndSendTransaction:
    async def test_sign_and_send_tracks_broadcasts_and_finalizes(self) -> None:
        keypair = Keypair()
        blockhash = Hash.from_string(BLOCKHASH)
        recipient = Keypair().pubkey()

        # Determine the expected signature by signing the same message locally
        instruction = transfer(
            TransferParams(
                from_pubkey=keypair.pubkey(), to_pubkey=recipient, lamports=1000
            )
        )
        message = Message.new_with_blockhash([instruction], keypair.pubkey(), blockhash)
        expected_signature = VersionedTransaction(message, [keypair]).signatures[0]

        wallet, blockchain, _ = build_wallet(
            {
                "sendTransaction": str(expected_signature),
                "getSignatureStatuses": signature_status_payload(
                    confirmation_status="finalized"
                ),
                "getTransaction": transaction_payload(
                    keypair.pubkey(), recipient, expected_signature
                ),
            },
            keypair=keypair,
            explorer=SolscanConfiguration(),
        )

        transaction = wallet.sign_and_send_transaction(
            client_operation_id="op-send-1",
            transaction=Transaction.new_unsigned(message),
            recent_blockhash=blockhash,
        )

        assert isinstance(transaction, SolanaTransaction)
        assert transaction.is_signed

        # Re-invoking with the same id is an idempotent retry
        assert (
            wallet.sign_and_send_transaction(
                client_operation_id="op-send-1",
                transaction=Transaction.new_unsigned(message),
                recent_blockhash=blockhash,
            )
            is transaction
        )

        # The broadcast and the tracking wait-loop run in the background
        deadline = time.monotonic() + 10
        while (
            transaction.current_state != BlockchainTransactionState.FINALIZED
            and time.monotonic() < deadline
        ):
            await asyncio.sleep(0.05)

        assert transaction.current_state == BlockchainTransactionState.FINALIZED
        assert transaction.operator_operation_id is not None
        assert transaction.operator_operation_id.string == str(expected_signature)
        assert transaction.receipt is not None
        assert transaction.receipt.fee == FEE_LAMPORTS
        assert (
            transaction.explorer_link == f"https://solscan.io/tx/{expected_signature}"
        )

    async def test_broadcast_transaction_processes_rejection(self) -> None:
        wallet, blockchain, keypair = build_wallet({})
        transaction = SolanaTransaction(
            client_operation_id="op-reject-1",
            owner_identifier=wallet.configuration.identifier,
            creation_timestamp=time.time(),
            signed_transaction=None,
        )

        with pytest.raises(ValueError, match="not signed"):
            await wallet.broadcast_transaction(transaction)


class TestBalances:
    async def test_fetch_balance_native(self) -> None:
        wallet, _, _ = build_wallet({})

        balance = await wallet.fetch_balance(wallet.blockchain.native_asset)

        assert balance == Decimal("2.5")

    async def test_update_balance_sets_total_and_available(self) -> None:
        wallet, _, _ = build_wallet({})
        native = wallet.blockchain.native_asset

        await wallet.update_balance(native)

        assert wallet.balance_tracker.get_balance(native, BalanceType.TOTAL) == Decimal(
            "2.5"
        )
        assert wallet.balance_tracker.get_balance(
            native, BalanceType.AVAILABLE
        ) == Decimal("2.5")

    async def test_fetch_balance_spl_token_uses_derived_ata(self) -> None:
        # Regression: the wallet OWNER address used to be passed to
        # getTokenAccountBalance, which requires a token account
        wallet, blockchain, keypair = build_wallet(
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
        mint = SolanaAddress.from_raw(Keypair().pubkey())
        token = SPLToken(
            platform=blockchain.configuration.platform,
            identifier=mint,
            data=SolanaAssetData(name="USD Coin", symbol="USDC", decimals=6),
            mint=mint,
        )
        expected_ata = SolanaBlockchain.derive_associated_token_account(
            wallet.address, mint
        )

        balance = await wallet.fetch_balance(token)

        assert balance == Decimal("123.45")
        request = provider_of(blockchain).requests_for("getTokenAccountBalance")[0]
        assert request["params"][0] == expected_ata.string

    async def test_fetch_balance_platform_mismatch_raises(self) -> None:
        wallet, _, _ = build_wallet({})
        other_blockchain = build_blockchain({})
        foreign_asset = SolanaAsset(
            platform=other_blockchain.configuration.platform.model_copy(
                update={"identifier": "solana-devnet"}
            ),
            identifier=SolanaAddress.from_raw(Keypair().pubkey()),
            data=SolanaAssetData(name="Foreign", symbol="FRN", decimals=6),
        )

        with pytest.raises(ValueError, match="platform does not match"):
            await wallet.fetch_balance(foreign_asset)

    async def test_fetch_balance_unsupported_asset_raises(self) -> None:
        wallet, blockchain, _ = build_wallet({})
        plain_asset = SolanaAsset(
            platform=blockchain.configuration.platform,
            identifier=SolanaAddress.from_raw(Keypair().pubkey()),
            data=SolanaAssetData(name="Plain", symbol="PLN", decimals=6),
        )

        with pytest.raises(ValueError, match="Unsupported asset type"):
            await wallet.fetch_balance(plain_asset)


def make_broadcasted_transaction(
    wallet: SolanaWallet, signature: Signature
) -> SolanaTransaction:
    return SolanaTransaction(
        client_operation_id="op-status-1",
        owner_identifier=wallet.configuration.identifier,
        creation_timestamp=time.time(),
        operator_operation_id=SolanaTransactionSignature.from_raw(signature),
        current_state=BlockchainTransactionState.BROADCASTED,
    )


class TestGetTransactionUpdate:
    async def test_confirmed_transaction_reports_confirmed_with_receipt(self) -> None:
        keypair = Keypair()
        recipient = Keypair().pubkey()
        signature = Signature.new_unique()
        wallet, _, _ = build_wallet(
            {
                "getSignatureStatuses": signature_status_payload(
                    confirmation_status="confirmed"
                ),
                "getTransaction": transaction_payload(
                    keypair.pubkey(), recipient, signature
                ),
            },
            keypair=keypair,
            explorer=SolscanConfiguration(),
        )
        transaction = make_broadcasted_transaction(wallet, signature)

        update = await wallet.get_transaction_update(
            transaction, timeout=timedelta(seconds=1), raise_timeout=True
        )

        assert update.new_state == BlockchainTransactionState.CONFIRMED
        assert update.transaction_id is not None
        assert update.transaction_id.string == str(signature)
        assert update.receipt is not None
        assert update.receipt.fee == FEE_LAMPORTS
        assert update.receipt.block_time == BLOCK_TIME
        assert update.explorer_link == f"https://solscan.io/tx/{signature}"

    async def test_finalized_transaction_reports_finalized(self) -> None:
        keypair = Keypair()
        signature = Signature.new_unique()
        wallet, _, _ = build_wallet(
            {
                "getSignatureStatuses": signature_status_payload(
                    confirmation_status="finalized"
                ),
                "getTransaction": transaction_payload(
                    keypair.pubkey(), Keypair().pubkey(), signature
                ),
            },
            keypair=keypair,
        )
        transaction = make_broadcasted_transaction(wallet, signature)

        update = await wallet.get_transaction_update(
            transaction, timeout=timedelta(seconds=1), raise_timeout=True
        )

        assert update.new_state == BlockchainTransactionState.FINALIZED

    async def test_failed_transaction_reports_failed(self) -> None:
        keypair = Keypair()
        signature = Signature.new_unique()
        error = {"InstructionError": [0, {"Custom": 1}]}
        wallet, _, _ = build_wallet(
            {
                "getSignatureStatuses": signature_status_payload(
                    err=error, confirmation_status="confirmed"
                ),
                "getTransaction": transaction_payload(
                    keypair.pubkey(), Keypair().pubkey(), signature, err=error
                ),
            },
            keypair=keypair,
        )
        transaction = make_broadcasted_transaction(wallet, signature)

        update = await wallet.get_transaction_update(
            transaction, timeout=timedelta(seconds=1), raise_timeout=True
        )

        assert update.new_state == BlockchainTransactionState.FAILED
        assert update.other_data["error"] is not None
        assert update.receipt is not None
        assert update.receipt.err is not None

    async def test_processed_transaction_keeps_polling_until_timeout(self) -> None:
        # A merely processed transaction must NOT be reported as confirmed
        signature = Signature.new_unique()
        wallet, blockchain, _ = build_wallet(
            {
                "getSignatureStatuses": signature_status_payload(
                    confirmation_status="processed"
                ),
            }
        )
        transaction = make_broadcasted_transaction(wallet, signature)

        with pytest.raises(TimeoutError):
            await wallet.get_transaction_update(
                transaction,
                timeout=timedelta(milliseconds=100),
                raise_timeout=True,
                poll_interval=0.01,
            )

        # The status endpoint was actually polled multiple times
        assert len(provider_of(blockchain).requests_for("getSignatureStatuses")) > 1

    async def test_timeout_without_raise_returns_current_state(self) -> None:
        signature = Signature.new_unique()
        wallet, _, _ = build_wallet(
            {
                "getSignatureStatuses": {
                    "context": {"slot": SLOT},
                    "value": [None],
                }
            }
        )
        transaction = make_broadcasted_transaction(wallet, signature)

        update = await wallet.get_transaction_update(
            transaction,
            timeout=timedelta(milliseconds=50),
            raise_timeout=False,
            poll_interval=0.01,
        )

        assert update.new_state == BlockchainTransactionState.BROADCASTED
        assert update.receipt is None

    async def test_confirmation_becomes_available_while_polling(self) -> None:
        keypair = Keypair()
        signature = Signature.new_unique()
        wallet, _, _ = build_wallet(
            {
                "getSignatureStatuses": [
                    signature_status_payload(confirmation_status="processed"),
                    signature_status_payload(confirmation_status="confirmed"),
                ],
                "getTransaction": transaction_payload(
                    keypair.pubkey(), Keypair().pubkey(), signature
                ),
            },
            keypair=keypair,
        )
        transaction = make_broadcasted_transaction(wallet, signature)

        update = await wallet.get_transaction_update(
            transaction,
            timeout=timedelta(seconds=5),
            raise_timeout=True,
            poll_interval=0.01,
        )

        assert update.new_state == BlockchainTransactionState.CONFIRMED
