"""
Unit tests for SolanaProgram instruction building.

Covers:
- Anchor instruction data encoding against the real Jupiter DCA IDL:
  8-byte sha256-based discriminators plus borsh-encoded arguments
- The simplified dict-form IDL path (pre-encoded data passthrough)
- The module-level borsh encoder primitives
- Error handling (uninitialized program, unknown instruction, bad args)
"""

import hashlib
import struct
from decimal import Decimal

import pytest
from financepype.operators.blockchains.models import BlockchainPlatform
from solders.instruction import AccountMeta, Instruction
from solders.pubkey import Pubkey

from blockchainpype.initializer import BlockchainsInitializer, SupportedBlockchainType
from blockchainpype.solana.blockchain.identifier import SolanaAddress
from blockchainpype.solana.dapp.idl import SolanaDictIDL, SolanaLocalFileIDL
from blockchainpype.solana.dapp.program import (
    SolanaProgram,
    SolanaProgramConfiguration,
    anchor_discriminator,
    encode_borsh_value,
    to_snake_case,
)

JUPITER_DCA_PROGRAM = "DCA265Vj8a9CEuX1eb1LWRnDT7uK6q1xMipnNyatn23M"
SOLEND_PROGRAM = "So1endDq2YkqhipRh3WViPa8hdiSpxWy6z3Z6tMCpAo"


@pytest.fixture(scope="session", autouse=True)
def setup_blockchains():
    """Setup blockchain configurations for testing."""
    from blockchainpype.factory import BlockchainFactory

    BlockchainFactory.reset()
    BlockchainsInitializer.configure()


@pytest.fixture
def test_platform():
    """Create a test Solana blockchain platform."""
    return BlockchainPlatform(
        identifier="solana",
        type=SupportedBlockchainType.SOLANA.value,
        chain_id=None,
    )


@pytest.fixture
def jupiter_program(test_platform):
    """A SolanaProgram bound to the real Jupiter DCA Anchor IDL."""
    configuration = SolanaProgramConfiguration(
        platform=test_platform,
        address=SolanaAddress.from_string(JUPITER_DCA_PROGRAM),
        idl_configuration=SolanaLocalFileIDL(file_name="jupiter_dca.json"),
    )
    return SolanaProgram(configuration)


@pytest.fixture
def solend_program(test_platform):
    """A SolanaProgram bound to the simplified dict-form Solend IDL."""
    configuration = SolanaProgramConfiguration(
        platform=test_platform,
        address=SolanaAddress.from_string(SOLEND_PROGRAM),
        idl_configuration=SolanaLocalFileIDL(file_name="solend.json"),
    )
    return SolanaProgram(configuration)


@pytest.fixture
def user_account():
    return AccountMeta(pubkey=Pubkey.new_unique(), is_signer=True, is_writable=True)


class TestSnakeCaseAndDiscriminator:
    """Test discriminator derivation helpers."""

    def test_to_snake_case(self):
        assert to_snake_case("openDcaV2") == "open_dca_v2"
        assert to_snake_case("endAndClose") == "end_and_close"
        assert to_snake_case("deposit") == "deposit"
        assert to_snake_case("enable_collateral") == "enable_collateral"

    def test_anchor_discriminator_matches_sha256(self):
        expected = hashlib.sha256(b"global:open_dca_v2").digest()[:8]
        assert anchor_discriminator("openDcaV2") == expected
        # Already snake_case names hash identically
        assert anchor_discriminator("open_dca_v2") == expected

    def test_anchor_discriminator_namespace(self):
        expected = hashlib.sha256(b"account:deposit").digest()[:8]
        assert anchor_discriminator("deposit", namespace="account") == expected


class TestBorshEncoder:
    """Test the module-level borsh value encoder."""

    def test_unsigned_integers(self):
        assert encode_borsh_value(7, "u8") == b"\x07"
        assert encode_borsh_value(258, "u16") == struct.pack("<H", 258)
        assert encode_borsh_value(70000, "u32") == struct.pack("<I", 70000)
        assert encode_borsh_value(2**63, "u64") == struct.pack("<Q", 2**63)
        assert encode_borsh_value(2**100, "u128") == (2**100).to_bytes(16, "little")

    def test_signed_integers(self):
        assert encode_borsh_value(-1, "i8") == b"\xff"
        assert encode_borsh_value(-2, "i16") == struct.pack("<h", -2)
        assert encode_borsh_value(-3, "i32") == struct.pack("<i", -3)
        assert encode_borsh_value(-4, "i64") == struct.pack("<q", -4)
        assert encode_borsh_value(-5, "i128") == (-5).to_bytes(
            16, "little", signed=True
        )

    def test_integer_out_of_range(self):
        with pytest.raises(ValueError, match="out of range"):
            encode_borsh_value(256, "u8")
        with pytest.raises(ValueError, match="out of range"):
            encode_borsh_value(-1, "u64")

    def test_integer_wrong_type(self):
        with pytest.raises(ValueError, match="Expected int"):
            encode_borsh_value("5", "u64")
        with pytest.raises(ValueError, match="Expected int"):
            encode_borsh_value(True, "u8")

    def test_bool(self):
        assert encode_borsh_value(True, "bool") == b"\x01"
        assert encode_borsh_value(False, "bool") == b"\x00"
        with pytest.raises(ValueError, match="Expected bool"):
            encode_borsh_value(1, "bool")

    def test_floats(self):
        assert encode_borsh_value(1.5, "f32") == struct.pack("<f", 1.5)
        assert encode_borsh_value(1.5, "f64") == struct.pack("<d", 1.5)

    def test_string(self):
        assert encode_borsh_value("abc", "string") == struct.pack("<I", 3) + b"abc"
        encoded_unicode = "é".encode()
        assert (
            encode_borsh_value("é", "string")
            == struct.pack("<I", len(encoded_unicode)) + encoded_unicode
        )
        with pytest.raises(ValueError, match="Expected str"):
            encode_borsh_value(5, "string")

    def test_bytes(self):
        assert (
            encode_borsh_value(b"\x01\x02", "bytes")
            == struct.pack("<I", 2) + b"\x01\x02"
        )

    def test_public_key_accepts_all_forms(self):
        pubkey = Pubkey.new_unique()
        address = SolanaAddress.from_raw(pubkey)
        expected = bytes(pubkey)
        assert len(expected) == 32
        assert encode_borsh_value(pubkey, "publicKey") == expected
        assert encode_borsh_value(address, "publicKey") == expected
        assert encode_borsh_value(str(pubkey), "publicKey") == expected
        assert encode_borsh_value(pubkey, "pubkey") == expected
        with pytest.raises(ValueError, match="public key"):
            encode_borsh_value(5, "publicKey")

    def test_option(self):
        assert encode_borsh_value(None, {"option": "u64"}) == b"\x00"
        assert encode_borsh_value(9, {"option": "u64"}) == b"\x01" + struct.pack(
            "<Q", 9
        )

    def test_vec(self):
        assert encode_borsh_value([1, 2], {"vec": "u16"}) == struct.pack(
            "<I", 2
        ) + struct.pack("<H", 1) + struct.pack("<H", 2)
        assert encode_borsh_value([], {"vec": "u8"}) == struct.pack("<I", 0)

    def test_array(self):
        assert encode_borsh_value([1, 2, 3], {"array": ["u8", 3]}) == b"\x01\x02\x03"
        with pytest.raises(ValueError, match="length mismatch"):
            encode_borsh_value([1, 2], {"array": ["u8", 3]})

    def test_defined_struct_and_enum(self):
        defined_types = [
            {
                "name": "Params",
                "type": {
                    "kind": "struct",
                    "fields": [
                        {"name": "amount", "type": "u64"},
                        {"name": "mode", "type": {"defined": "Mode"}},
                    ],
                },
            },
            {
                "name": "Mode",
                "type": {"kind": "enum", "variants": [{"name": "A"}, {"name": "B"}]},
            },
        ]
        encoded = encode_borsh_value(
            {"amount": 5, "mode": "B"}, {"defined": "Params"}, defined_types
        )
        assert encoded == struct.pack("<Q", 5) + b"\x01"

    def test_defined_struct_missing_field(self):
        defined_types = [
            {
                "name": "Params",
                "type": {
                    "kind": "struct",
                    "fields": [{"name": "amount", "type": "u64"}],
                },
            },
        ]
        with pytest.raises(ValueError, match="Missing field 'amount'"):
            encode_borsh_value({}, {"defined": "Params"}, defined_types)

    def test_defined_unknown_type(self):
        with pytest.raises(ValueError, match="Unknown defined type"):
            encode_borsh_value({}, {"defined": "Nope"}, [])

    def test_unsupported_type(self):
        with pytest.raises(ValueError, match="Unsupported borsh type"):
            encode_borsh_value(5, "u42")
        with pytest.raises(ValueError, match="Unsupported borsh type spec"):
            encode_borsh_value(5, {"weird": "u8"})


class TestCreateInstructionAnchorIDL:
    """Test create_instruction against the real Jupiter DCA Anchor IDL."""

    async def test_discriminator_and_borsh_args(self, jupiter_program, user_account):
        await jupiter_program.initialize()

        instruction = jupiter_program.create_instruction(
            name="openDcaV2",
            accounts=[user_account],
            args={
                "applicationIdx": 42,
                "inAmount": 1_000_000,
                "inAmountPerCycle": 100_000,
                "cycleFrequency": 3600,
                "minPrice": None,
                "maxPrice": 250_000,
                "startAt": 1_700_000_000,
            },
        )

        expected_data = (
            hashlib.sha256(b"global:open_dca_v2").digest()[:8]
            + struct.pack("<Q", 42)
            + struct.pack("<Q", 1_000_000)
            + struct.pack("<Q", 100_000)
            + struct.pack("<q", 3600)
            + b"\x00"  # minPrice: None
            + b"\x01"
            + struct.pack("<Q", 250_000)  # maxPrice: Some(250_000)
            + b"\x01"
            + struct.pack("<q", 1_700_000_000)  # startAt: Some(...)
        )

        assert isinstance(instruction, Instruction)
        assert instruction.data == expected_data
        assert instruction.program_id == Pubkey.from_string(JUPITER_DCA_PROGRAM)
        assert instruction.accounts == [user_account]

    async def test_optional_args_can_be_omitted(self, jupiter_program, user_account):
        await jupiter_program.initialize()

        instruction = jupiter_program.create_instruction(
            name="openDcaV2",
            accounts=[user_account],
            args={
                "applicationIdx": 1,
                "inAmount": 2,
                "inAmountPerCycle": 3,
                "cycleFrequency": 4,
            },
        )

        expected_data = (
            hashlib.sha256(b"global:open_dca_v2").digest()[:8]
            + struct.pack("<Q", 1)
            + struct.pack("<Q", 2)
            + struct.pack("<Q", 3)
            + struct.pack("<q", 4)
            + b"\x00" * 3  # minPrice, maxPrice, startAt all omitted
        )
        assert instruction.data == expected_data

    async def test_single_arg_instruction(self, jupiter_program, user_account):
        await jupiter_program.initialize()

        instruction = jupiter_program.create_instruction(
            name="deposit",
            accounts=[user_account],
            args={"depositIn": 5_000_000},
        )

        assert instruction.data == hashlib.sha256(b"global:deposit").digest()[
            :8
        ] + struct.pack("<Q", 5_000_000)

    async def test_no_arg_instruction(self, jupiter_program, user_account):
        await jupiter_program.initialize()

        instruction = jupiter_program.create_instruction(
            name="endAndClose",
            accounts=[user_account],
        )

        assert instruction.data == hashlib.sha256(b"global:end_and_close").digest()[:8]

    async def test_defined_struct_argument(self, jupiter_program, user_account):
        """The withdraw instruction takes a defined WithdrawParams struct."""
        await jupiter_program.initialize()

        instruction = jupiter_program.create_instruction(
            name="withdraw",
            accounts=[user_account],
            args={
                "withdrawParams": {
                    "withdrawAmount": 77,
                    "withdrawal": "In",  # unit variant of the Withdrawal enum
                }
            },
        )

        assert instruction.data == (
            hashlib.sha256(b"global:withdraw").digest()[:8]
            + struct.pack("<Q", 77)
            + b"\x00"  # Withdrawal::In is variant index 0
        )

    async def test_missing_required_arg_raises(self, jupiter_program, user_account):
        await jupiter_program.initialize()

        with pytest.raises(ValueError, match="Missing argument 'inAmount'"):
            jupiter_program.create_instruction(
                name="openDcaV2",
                accounts=[user_account],
                args={"applicationIdx": 1},
            )

    async def test_unknown_arg_raises(self, jupiter_program, user_account):
        await jupiter_program.initialize()

        with pytest.raises(ValueError, match="Unknown argument"):
            jupiter_program.create_instruction(
                name="deposit",
                accounts=[user_account],
                args={"depositIn": 1, "bogus": 2},
            )

    async def test_unknown_instruction_raises(self, jupiter_program, user_account):
        await jupiter_program.initialize()

        with pytest.raises(ValueError, match="Invalid instruction name"):
            jupiter_program.create_instruction(
                name="nonExistent", accounts=[user_account]
            )

    async def test_data_and_args_mutually_exclusive(
        self, jupiter_program, user_account
    ):
        await jupiter_program.initialize()

        with pytest.raises(ValueError, match="not both"):
            jupiter_program.create_instruction(
                name="deposit",
                accounts=[user_account],
                data=b"\x00",
                args={"depositIn": 1},
            )

    def test_uninitialized_program_raises(self, jupiter_program, user_account):
        with pytest.raises(ValueError, match="not initialized"):
            jupiter_program.create_instruction(name="deposit", accounts=[user_account])

    async def test_explicit_data_passthrough(self, jupiter_program, user_account):
        """Pre-encoded data is used verbatim, even for Anchor IDLs."""
        await jupiter_program.initialize()

        instruction = jupiter_program.create_instruction(
            name="deposit",
            accounts=[user_account],
            data=b"\xde\xad\xbe\xef",
        )
        assert instruction.data == b"\xde\xad\xbe\xef"


class TestCreateInstructionSimplifiedIDL:
    """Test create_instruction against the simplified dict-form Solend IDL."""

    async def test_data_passthrough(self, solend_program, user_account):
        await solend_program.initialize()

        raw_amount = Decimal("1000000")
        data = bytes([13]) + int(raw_amount).to_bytes(8, "little")
        instruction = solend_program.create_instruction(
            name="deposit",
            accounts=[user_account],
            data=data,
        )

        assert instruction.data == data
        assert instruction.program_id == Pubkey.from_string(SOLEND_PROGRAM)
        assert instruction.accounts == [user_account]

    async def test_no_data_defaults_to_empty(self, solend_program, user_account):
        await solend_program.initialize()

        instruction = solend_program.create_instruction(
            name="enable_collateral", accounts=[user_account]
        )
        assert instruction.data == b""

    async def test_args_rejected_for_simplified_idl(self, solend_program, user_account):
        await solend_program.initialize()

        with pytest.raises(ValueError, match="pre-encoded 'data'"):
            solend_program.create_instruction(
                name="deposit", accounts=[user_account], args={"amount": 1}
            )

    async def test_unknown_instruction_raises(self, solend_program, user_account):
        await solend_program.initialize()

        with pytest.raises(ValueError, match="Invalid instruction name"):
            solend_program.create_instruction(name="flashLoan", accounts=[user_account])


class TestCreateInstructionExplicitDiscriminator:
    """Test that an IDL-provided discriminator (Anchor >=0.30) is honored."""

    async def test_explicit_discriminator(self, test_platform, user_account):
        idl = {
            "name": "modern",
            "instructions": [
                {
                    "name": "doThing",
                    "discriminator": [1, 2, 3, 4, 5, 6, 7, 8],
                    "accounts": [],
                    "args": [{"name": "x", "type": "u8"}],
                }
            ],
        }
        program = SolanaProgram(
            SolanaProgramConfiguration(
                platform=test_platform,
                address=SolanaAddress.from_raw(Pubkey.new_unique()),
                idl_configuration=SolanaDictIDL(idl=idl),
            )
        )
        await program.initialize()

        instruction = program.create_instruction(
            name="doThing", accounts=[user_account], args={"x": 9}
        )
        assert instruction.data == bytes([1, 2, 3, 4, 5, 6, 7, 8, 9])
