"""
Unit tests for Solana IDL loading and instruction lookup.

Covers:
- Loading a real Anchor IDL (list-form instructions, common/idl/jupiter_dca.json)
- Loading a simplified hand-written IDL (dict-form instructions, common/idl/solend.json)
- Dict-based IDL storage
- Instruction lookup across both IDL layouts
- Error handling for missing/invalid files
"""

import json
import os

import pytest

from blockchainpype import common_idl_path
from blockchainpype.solana.dapp.idl import (
    SolanaDictIDL,
    SolanaIDL,
    SolanaLocalFileIDL,
    find_idl_instruction,
)


class TestSolanaIDLBase:
    """Test the abstract SolanaIDL base class."""

    def test_cannot_instantiate_abstract_base(self):
        with pytest.raises(TypeError):
            SolanaIDL()


class TestSolanaDictIDL:
    """Test dictionary-based IDL storage."""

    async def test_get_idl_returns_stored_dict(self):
        idl_dict = {"version": "0.1.0", "name": "test", "instructions": []}
        idl_config = SolanaDictIDL(idl=idl_dict)
        assert await idl_config.get_idl() == idl_dict


class TestSolanaLocalFileIDL:
    """Test file-based IDL loading."""

    def test_file_path_uses_common_idl_path_by_default(self):
        idl_config = SolanaLocalFileIDL(file_name="jupiter_dca.json")
        assert idl_config.file_path == os.path.join(common_idl_path, "jupiter_dca.json")

    async def test_load_real_anchor_idl(self):
        """A real Anchor IDL has list-form instructions with accounts and args."""
        idl_config = SolanaLocalFileIDL(file_name="jupiter_dca.json")
        idl = await idl_config.get_idl()

        assert idl["name"] == "dca"
        assert idl["version"] == "0.1.0"

        instructions = idl["instructions"]
        assert isinstance(instructions, list)
        assert len(instructions) == 10

        names = [instruction["name"] for instruction in instructions]
        assert names == [
            "openDca",
            "openDcaV2",
            "closeDca",
            "withdraw",
            "deposit",
            "withdrawFees",
            "initiateFlashFill",
            "fulfillFlashFill",
            "transfer",
            "endAndClose",
        ]

        open_dca_v2 = instructions[1]
        assert open_dca_v2["accounts"][0] == {
            "name": "dca",
            "isMut": True,
            "isSigner": False,
        }
        assert open_dca_v2["args"][0] == {"name": "applicationIdx", "type": "u64"}

    async def test_load_simplified_idl(self):
        """The simplified Solend IDL has dict-form instructions keyed by name."""
        idl_config = SolanaLocalFileIDL(file_name="solend.json")
        idl = await idl_config.get_idl()

        assert idl["name"] == "solend"
        instructions = idl["instructions"]
        assert isinstance(instructions, dict)
        assert sorted(instructions.keys()) == [
            "borrow",
            "deposit",
            "disable_collateral",
            "enable_collateral",
            "liquidate",
            "repay",
            "withdraw",
        ]
        assert instructions["deposit"] == {"name": "deposit", "accounts": []}

    async def test_missing_file_raises(self):
        idl_config = SolanaLocalFileIDL(file_name="does_not_exist.json")
        with pytest.raises(FileNotFoundError):
            await idl_config.get_idl()

    async def test_non_object_json_raises(self, tmp_path):
        file_path = tmp_path / "bad.json"
        file_path.write_text(json.dumps([1, 2, 3]))

        idl_config = SolanaLocalFileIDL(file_name="bad.json", folder_path=str(tmp_path))
        with pytest.raises(ValueError, match="Invalid IDL format"):
            await idl_config.get_idl()

    async def test_invalid_json_raises(self, tmp_path):
        file_path = tmp_path / "broken.json"
        file_path.write_text("{not json")

        idl_config = SolanaLocalFileIDL(
            file_name="broken.json", folder_path=str(tmp_path)
        )
        with pytest.raises(json.JSONDecodeError):
            await idl_config.get_idl()


class TestFindIDLInstruction:
    """Test instruction lookup across both IDL layouts."""

    async def test_list_form_lookup(self):
        idl = await SolanaLocalFileIDL(file_name="jupiter_dca.json").get_idl()

        instruction = find_idl_instruction(idl, "openDcaV2")
        assert instruction is not None
        assert instruction["name"] == "openDcaV2"
        assert len(instruction["args"]) == 7

        assert find_idl_instruction(idl, "nonExistent") is None

    async def test_dict_form_lookup(self):
        idl = await SolanaLocalFileIDL(file_name="solend.json").get_idl()

        instruction = find_idl_instruction(idl, "deposit")
        assert instruction == {"name": "deposit", "accounts": []}

        assert find_idl_instruction(idl, "nonExistent") is None

    def test_dict_form_bare_entry(self):
        idl = {"instructions": {"transfer": None}}
        assert find_idl_instruction(idl, "transfer") == {"name": "transfer"}

    def test_missing_instructions_section(self):
        assert find_idl_instruction({}, "anything") is None
        assert find_idl_instruction({"instructions": "bogus"}, "anything") is None
