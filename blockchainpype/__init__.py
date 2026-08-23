"""
blockchainpype - A Python library for interacting with multiple blockchain networks.

This package exposes the resolved locations of the shared ABI/IDL asset
directories (``common_abi_path`` / ``common_idl_path``) used by the EVM and
Solana dapp layers, alongside the installed package version.
"""

import os
from importlib import metadata

try:
    __version__ = metadata.version("blockchainpype")
except metadata.PackageNotFoundError:  # pragma: no cover - unbuilt source tree
    __version__ = "0.0.0"


def resolve_common_path(package_dir: str | None = None) -> str:
    """Resolve the directory containing the shared ABI/IDL assets.

    Wheel installs ship the assets inside the package (``blockchainpype/common``,
    mapped there at build time via hatchling force-include), while source
    checkouts keep them at the repository root (``common/`` next to the package
    directory). The in-package location is preferred when it exists.

    Args:
        package_dir: Directory of the ``blockchainpype`` package. Defaults to
            the directory containing this module; overridable for testing.

    Returns:
        str: Absolute path of the resolved ``common`` directory.
    """
    if package_dir is None:
        package_dir = os.path.dirname(__file__)
    packaged = os.path.join(package_dir, "common")
    if os.path.isdir(packaged):
        return packaged
    return os.path.join(os.path.dirname(package_dir), "common")


common_path = resolve_common_path()
common_abi_path = os.path.join(common_path, "abi")
common_idl_path = os.path.join(common_path, "idl")
