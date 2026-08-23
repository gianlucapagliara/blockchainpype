"""
Unit tests for the Solana identifier types: transaction signatures, public
keys, addresses, and the native-SOL null-address sentinel.
"""

import pytest
from pydantic import ValidationError
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.signature import Signature

from blockchainpype.solana.blockchain.identifier import (
    NATIVE_SOL_SENTINEL_ADDRESS,
    WRAPPED_SOL_MINT_ADDRESS,
    SolanaAddress,
    SolanaNullAddress,
    SolanaPublicKey,
    SolanaTransactionSignature,
)

SYSTEM_PROGRAM = "11111111111111111111111111111111"


class TestSolanaTransactionSignature:
    def test_from_raw_round_trip(self) -> None:
        signature = Signature.new_unique()

        identifier = SolanaTransactionSignature.from_raw(signature)

        assert identifier.raw == signature
        assert identifier.string == str(signature)
        assert str(identifier) == str(signature)

    def test_from_string_round_trip(self) -> None:
        signature = Signature.new_unique()

        identifier = SolanaTransactionSignature.from_string(str(signature))

        assert identifier.raw == signature
        assert identifier.string == str(signature)
        assert identifier == SolanaTransactionSignature.from_raw(signature)

    def test_is_valid(self) -> None:
        assert SolanaTransactionSignature.is_valid(Signature.new_unique()) is True
        assert SolanaTransactionSignature.is_valid(str(Signature.new_unique())) is True
        assert SolanaTransactionSignature.is_valid("not-a-signature") is False
        # A pubkey string is too short to be a signature
        assert SolanaTransactionSignature.is_valid(SYSTEM_PROGRAM) is False

    def test_id_from_string_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid transaction signature"):
            SolanaTransactionSignature.id_from_string("garbage")

    def test_equality_and_hash(self) -> None:
        signature = Signature.new_unique()
        a = SolanaTransactionSignature.from_raw(signature)
        b = SolanaTransactionSignature.from_string(str(signature))

        assert a == b
        assert hash(a) == hash(b)
        assert a != SolanaTransactionSignature.from_raw(Signature.new_unique())

    def test_invalid_raw_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SolanaTransactionSignature(raw="not-a-signature", string="x")


class TestSolanaPublicKey:
    def test_from_raw_round_trip(self) -> None:
        pubkey = Keypair().pubkey()

        identifier = SolanaPublicKey.from_raw(pubkey)

        assert identifier.raw == pubkey
        assert identifier.string == str(pubkey)

    def test_from_string_round_trip(self) -> None:
        pubkey = Keypair().pubkey()

        identifier = SolanaPublicKey.from_string(str(pubkey))

        assert identifier.raw == pubkey
        assert identifier == SolanaPublicKey.from_raw(pubkey)

    def test_is_valid(self) -> None:
        assert SolanaPublicKey.is_valid(Keypair().pubkey()) is True
        assert SolanaPublicKey.is_valid(SYSTEM_PROGRAM) is True
        assert SolanaPublicKey.is_valid("not-a-pubkey") is False
        assert SolanaPublicKey.is_valid("") is False

    def test_id_from_string_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid public key"):
            SolanaPublicKey.id_from_string("garbage")


class TestSolanaAddress:
    def test_address_is_public_key(self) -> None:
        pubkey = Keypair().pubkey()

        address = SolanaAddress.from_raw(pubkey)

        assert isinstance(address, SolanaPublicKey)
        assert address.raw == pubkey

    def test_cross_type_equality_is_string_based(self) -> None:
        pubkey = Keypair().pubkey()

        assert SolanaAddress.from_raw(pubkey) == SolanaPublicKey.from_raw(pubkey)


class TestSolanaNullAddress:
    def test_null_address_uses_native_sentinel(self) -> None:
        null_address = SolanaNullAddress()

        assert null_address.string == NATIVE_SOL_SENTINEL_ADDRESS
        assert null_address.raw == Pubkey.from_string(NATIVE_SOL_SENTINEL_ADDRESS)

    def test_sentinel_is_distinct_from_wrapped_sol_mint(self) -> None:
        # The native-SOL sentinel (...111) must never be conflated with the
        # canonical wrapped-SOL mint (...112)
        assert NATIVE_SOL_SENTINEL_ADDRESS != WRAPPED_SOL_MINT_ADDRESS
        assert NATIVE_SOL_SENTINEL_ADDRESS.endswith("1")
        assert WRAPPED_SOL_MINT_ADDRESS == (
            "So11111111111111111111111111111111111111112"
        )
        assert SolanaNullAddress().string != WRAPPED_SOL_MINT_ADDRESS

    def test_wrapped_sol_mint_is_a_valid_pubkey(self) -> None:
        assert SolanaAddress.is_valid(WRAPPED_SOL_MINT_ADDRESS) is True
        wrapped = SolanaAddress.from_string(WRAPPED_SOL_MINT_ADDRESS)
        assert wrapped != SolanaNullAddress()

    def test_null_addresses_are_equal(self) -> None:
        assert SolanaNullAddress() == SolanaNullAddress()
        assert hash(SolanaNullAddress()) == hash(SolanaNullAddress())
