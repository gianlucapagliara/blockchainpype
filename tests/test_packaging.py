"""
Unit tests for packaging-sensitive behavior.

This module tests:
- resolve_common_path preferring the in-package common/ directory (wheel
  installs) and falling back to the repository root (source checkouts)
- The resolved ABI/IDL paths pointing at real asset files
- The package version attribute
- The py.typed marker and the hatchling force-include mapping that ships the
  shared assets inside the wheel
"""

import os
import tomllib
from importlib import metadata
from pathlib import Path

import blockchainpype
from blockchainpype import (
    common_abi_path,
    common_idl_path,
    common_path,
    resolve_common_path,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestResolveCommonPath:
    def test_prefers_in_package_common_directory(self, tmp_path: Path):
        package_dir = tmp_path / "site-packages" / "blockchainpype"
        packaged_common = package_dir / "common"
        packaged_common.mkdir(parents=True)

        assert resolve_common_path(str(package_dir)) == str(packaged_common)

    def test_falls_back_to_repository_root(self, tmp_path: Path):
        package_dir = tmp_path / "checkout" / "blockchainpype"
        package_dir.mkdir(parents=True)

        assert resolve_common_path(str(package_dir)) == str(
            tmp_path / "checkout" / "common"
        )

    def test_default_resolution_points_at_existing_assets(self):
        assert common_path == resolve_common_path()
        assert os.path.isdir(common_abi_path)
        assert os.path.isdir(common_idl_path)
        assert os.path.isfile(os.path.join(common_abi_path, "ERC20Mock.json"))
        assert os.path.isfile(os.path.join(common_abi_path, "ERC20.json"))
        assert os.path.isfile(os.path.join(common_idl_path, "jupiter_dca.json"))


class TestVersion:
    def test_version_attribute_exists(self):
        assert isinstance(blockchainpype.__version__, str)
        assert blockchainpype.__version__ != ""

    def test_version_matches_installed_metadata(self):
        assert blockchainpype.__version__ == metadata.version("blockchainpype")


class TestPackagingConfiguration:
    def test_py_typed_marker_ships_with_package(self):
        package_dir = Path(blockchainpype.__file__).resolve().parent
        assert (package_dir / "py.typed").is_file()

    def test_wheel_force_includes_shared_assets(self):
        with open(REPO_ROOT / "pyproject.toml", "rb") as f:
            pyproject = tomllib.load(f)

        wheel_config = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]
        assert wheel_config["packages"] == ["blockchainpype"]

        force_include = wheel_config["force-include"]
        assert force_include["common/abi"] == "blockchainpype/common/abi"
        assert force_include["common/idl"] == "blockchainpype/common/idl"
