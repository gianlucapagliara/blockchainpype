"""
Unit tests for Ethereum identifier classes: addresses, transaction hashes,
and block hashes. Covers checksum normalization, 0x-prefixed hash strings,
round-trip equality, and rejection of invalid inputs.
"""

from typing import Any

import pytest
from hexbytes import HexBytes
from web3 import AsyncWeb3

from blockchainpype.evm.blockchain.identifier import (
    EthereumAddress,
    EthereumBlockHash,
    EthereumNullAddress,
    EthereumTransactionHash,
)

CHECKSUMMED_ADDRESS = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"
LOWERCASE_ADDRESS = CHECKSUMMED_ADDRESS.lower()
UPPERCASE_ADDRESS = "0x" + CHECKSUMMED_ADDRESS[2:].upper()

# First transaction ever mined on Ethereum mainnet (block 46147).
TX_HASH = "0x5c504ed432cb51138bcf09aa5e8a410dd4a1e204ef84bfed1be16dfba1b22060"
TX_HASH_UPPER = "0x" + TX_HASH[2:].upper()


class TestEthereumAddress:
    def test_from_string_checksummed_round_trip(self) -> None:
        address = EthereumAddress.from_string(CHECKSUMMED_ADDRESS)

        assert address.string == CHECKSUMMED_ADDRESS
        assert address.raw == CHECKSUMMED_ADDRESS
        assert str(address) == CHECKSUMMED_ADDRESS

    def test_from_string_normalizes_lowercase_to_checksum(self) -> None:
        address = EthereumAddress.from_string(LOWERCASE_ADDRESS)

        assert address.string == CHECKSUMMED_ADDRESS
        assert address.raw == CHECKSUMMED_ADDRESS

    def test_from_string_normalizes_uppercase_to_checksum(self) -> None:
        address = EthereumAddress.from_string(UPPERCASE_ADDRESS)

        assert address.string == CHECKSUMMED_ADDRESS

    def test_from_string_and_from_raw_are_equal(self) -> None:
        from_string = EthereumAddress.from_string(LOWERCASE_ADDRESS)
        from_raw = EthereumAddress.from_raw(
            AsyncWeb3.to_checksum_address(LOWERCASE_ADDRESS)
        )

        assert from_string == from_raw
        assert hash(from_string) == hash(from_raw)

    def test_different_casings_dedupe_in_set(self) -> None:
        addresses = {
            EthereumAddress.from_string(CHECKSUMMED_ADDRESS),
            EthereumAddress.from_string(LOWERCASE_ADDRESS),
            EthereumAddress.from_string(UPPERCASE_ADDRESS),
        }

        assert len(addresses) == 1

    def test_different_addresses_are_not_equal(self) -> None:
        first = EthereumAddress.from_string(CHECKSUMMED_ADDRESS)
        second = EthereumAddress.from_string("0x" + "12" * 20)

        assert first != second

    @pytest.mark.parametrize(
        "value",
        [
            CHECKSUMMED_ADDRESS,
            LOWERCASE_ADDRESS,
            UPPERCASE_ADDRESS,
            "0x0000000000000000000000000000000000000000",
        ],
    )
    def test_is_valid_accepts_valid_addresses(self, value: str) -> None:
        assert EthereumAddress.is_valid(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            None,
            123,
            "",
            "0x123",  # too short
            "0x" + "12" * 19,  # 19 bytes
            "0x" + "12" * 21,  # 21 bytes
            "0x" + "gg" * 20,  # non-hex
            "not-an-address",
        ],
    )
    def test_is_valid_rejects_invalid_inputs(self, value: Any) -> None:
        assert EthereumAddress.is_valid(value) is False

    def test_from_string_invalid_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Invalid wallet id"):
            EthereumAddress.from_string("0x123")

    def test_id_from_string_invalid_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Invalid wallet id"):
            EthereumAddress.id_from_string("not-an-address")


class TestEthereumNullAddress:
    def test_defaults_to_null_address(self) -> None:
        null_address = EthereumNullAddress()

        assert null_address.string == "0x0000000000000000000000000000000000000000"
        assert null_address.raw == "0x0000000000000000000000000000000000000000"

    def test_equals_address_built_from_string(self) -> None:
        null_address = EthereumNullAddress()
        from_string = EthereumAddress.from_string(
            "0x0000000000000000000000000000000000000000"
        )

        assert null_address == from_string


class TestEthereumTransactionHash:
    def test_from_string_round_trip_identical(self) -> None:
        tx_hash = EthereumTransactionHash.from_string(TX_HASH)

        assert tx_hash.string == TX_HASH
        assert tx_hash.raw == HexBytes(TX_HASH)
        assert str(tx_hash) == TX_HASH

    def test_from_raw_produces_0x_prefixed_string(self) -> None:
        """Regression: hexbytes>=1.0 ``.hex()`` drops the 0x prefix."""
        tx_hash = EthereumTransactionHash.from_raw(HexBytes(TX_HASH))

        assert tx_hash.string == TX_HASH
        assert tx_hash.string.startswith("0x")

    def test_from_raw_and_from_string_are_equal(self) -> None:
        from_raw = EthereumTransactionHash.from_raw(HexBytes(TX_HASH))
        from_string = EthereumTransactionHash.from_string(TX_HASH)

        assert from_raw == from_string
        assert hash(from_raw) == hash(from_string)
        assert len({from_raw, from_string}) == 1

    def test_from_string_normalizes_uppercase_hex(self) -> None:
        tx_hash = EthereumTransactionHash.from_string(TX_HASH_UPPER)

        assert tx_hash.string == TX_HASH

    @pytest.mark.parametrize(
        "value",
        [
            TX_HASH,
            TX_HASH_UPPER,
            HexBytes(TX_HASH),
            b"\x11" * 32,
            "11" * 32,  # non-prefixed hex is convertible
        ],
    )
    def test_is_valid_accepts_32_byte_values(self, value: Any) -> None:
        assert EthereumTransactionHash.is_valid(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            None,
            5,  # converts to a single byte
            "0x1234",  # too short
            "0x" + "11" * 31,  # 31 bytes
            "0x" + "11" * 33,  # 33 bytes
            HexBytes("0x1234"),  # HexBytes of the wrong length
            b"\x11" * 31,
            "not-a-hash",
            "0x" + "zz" * 32,  # non-hex
            "",
        ],
    )
    def test_is_valid_rejects_invalid_inputs(self, value: Any) -> None:
        assert EthereumTransactionHash.is_valid(value) is False

    def test_from_string_invalid_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Invalid transaction id"):
            EthereumTransactionHash.from_string("0x1234")

    def test_id_from_string_invalid_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Invalid transaction id"):
            EthereumTransactionHash.id_from_string("not-a-hash")


class TestEthereumBlockHash:
    def test_round_trip_matches_transaction_hash_semantics(self) -> None:
        block_hash_hex = "0x" + "ab" * 32

        from_raw = EthereumBlockHash.from_raw(HexBytes(block_hash_hex))
        from_string = EthereumBlockHash.from_string(block_hash_hex)

        assert from_raw.string == block_hash_hex
        assert from_raw == from_string

    def test_is_valid_enforces_length(self) -> None:
        assert EthereumBlockHash.is_valid("0x" + "ab" * 32) is True
        assert EthereumBlockHash.is_valid("0x" + "ab" * 16) is False
