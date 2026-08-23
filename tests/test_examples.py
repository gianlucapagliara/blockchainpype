"""Smoke tests for the runnable examples shipped in ``examples/``.

The examples are part of the public surface of this repository: they are the
first code a user runs, so they must at least stay importable and in sync with
the library APIs. Every example module is checked for three things:

1. it byte-compiles (``py_compile``), so no example can rot into a SyntaxError;
2. it imports in this process and exposes a callable ``main`` entry point;
3. it imports in a **fresh interpreter with sockets disabled**, and leaves the
   global factories untouched — i.e. importing an example neither talks to a
   network nor registers blockchains/wallets behind the caller's back.

Check 3 is what keeps ``main()`` the single entry point of every example: any
module-level RPC call, wallet construction or ``configure()`` invocation makes
it fail. It runs one subprocess per example (no network access is needed), so
it stays in the default test selection.
"""

import importlib
import os
import py_compile
import subprocess
import sys
from pathlib import Path

import pytest

import examples

EXAMPLES_ROOT = Path(examples.__file__).parent
REPO_ROOT = EXAMPLES_ROOT.parent

#: Every runnable example, listed explicitly: a renamed or deleted example must
#: be a deliberate change, not a silently shrinking test matrix.
EXAMPLE_MODULES = [
    "examples.basic.configure",
    "examples.basic.configure_wallets",
    "examples.betting_market_example",
    "examples.contract_initialization_example",
    "examples.hardhat_testing_demo",
    "examples.money_market_example",
    "examples.solana_example",
    "examples.uniswap_example",
]

#: Packages of the examples tree; they carry documentation, not entry points.
EXAMPLE_PACKAGES = ["examples", "examples.basic"]

#: Messages the probe below fails with; asserted by the probe's own tests.
NETWORK_ERROR_MESSAGE = "network access attempted at import time"
CONFIGURATIONS_ERROR_MESSAGE = "blockchain configurations registered at import"

# Imports the module named by argv[1] in a clean interpreter with the socket
# connect paths replaced by raising stubs, then asserts that the global
# registries are still empty. Only the connect entry points are patched (not
# the socket class itself, which ssl subclasses at import time).
IMPORT_PROBE = """
import importlib
import socket
import sys


def _blocked(*args, **kwargs):
    raise RuntimeError("network access attempted at import time")


socket.socket.connect = _blocked
socket.socket.connect_ex = _blocked
socket.create_connection = _blocked

importlib.import_module(sys.argv[1])

from blockchainpype.evm.blockchain.blockchain import EthereumBlockchainType
from blockchainpype.factory import BlockchainFactory, WalletFactory, WalletRegistry

configurations = BlockchainFactory.list_configurations()
if configurations:
    raise SystemExit(
        "blockchain configurations registered at import: " + str(configurations)
    )
wallets = WalletRegistry.list()
if wallets:
    raise SystemExit("wallet configurations registered at import: " + str(wallets))
if WalletFactory.get_wallet_class(EthereumBlockchainType) is not None:
    raise SystemExit("wallet classes registered at import")
"""


def module_path(module_name: str) -> Path:
    """Absolute path of an example module's source file."""
    return REPO_ROOT / (module_name.replace(".", "/") + ".py")


def discovered_example_modules() -> list[str]:
    """Every non-package module under ``examples/``, as dotted names."""
    return sorted(
        ".".join(path.relative_to(REPO_ROOT).with_suffix("").parts)
        for path in EXAMPLES_ROOT.rglob("*.py")
        if path.name != "__init__.py"
    )


def test_example_modules_matches_the_examples_tree() -> None:
    """The explicit list above must cover exactly the example scripts on disk."""
    assert discovered_example_modules() == sorted(EXAMPLE_MODULES)


def test_example_packages_exist() -> None:
    """The examples tree is a real package, so ``python -m`` and imports work."""
    for package in EXAMPLE_PACKAGES:
        init_file = REPO_ROOT / package.replace(".", "/") / "__init__.py"
        assert init_file.is_file(), f"{package} is missing an __init__.py"


@pytest.mark.parametrize("module_name", EXAMPLE_MODULES)
def test_example_compiles(module_name: str, tmp_path: Path) -> None:
    """Every example byte-compiles (catches syntax errors without importing)."""
    source = module_path(module_name)
    assert source.is_file(), f"{source} does not exist"

    py_compile.compile(
        str(source),
        cfile=str(tmp_path / f"{module_name}.pyc"),
        doraise=True,
    )


@pytest.mark.parametrize("module_name", EXAMPLE_MODULES)
def test_example_has_callable_entry_point(module_name: str) -> None:
    """Importing works in-process and exposes a callable ``main``."""
    module = importlib.import_module(module_name)

    main = getattr(module, "main", None)
    assert main is not None, f"{module_name} has no main() entry point"
    assert callable(main), f"{module_name}.main is not callable"
    assert module.__doc__, f"{module_name} has no module docstring"


def run_import_probe(
    module_name: str, extra_path: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Import ``module_name`` in a clean, network-less interpreter."""
    env = dict(os.environ)
    if extra_path is not None:
        env["PYTHONPATH"] = str(extra_path)
    return subprocess.run(
        [sys.executable, "-c", IMPORT_PROBE, module_name],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )


@pytest.mark.parametrize("module_name", EXAMPLE_MODULES)
def test_example_import_is_side_effect_free(module_name: str) -> None:
    """A fresh interpreter can import the example with no network, no globals."""
    result = run_import_probe(module_name)

    assert result.returncode == 0, (
        f"importing {module_name} in a clean interpreter failed:\n"
        f"{result.stdout}\n{result.stderr}"
    )


def test_import_probe_rejects_network_access(tmp_path: Path) -> None:
    """The probe is not a no-op: a connection at import time fails it."""
    (tmp_path / "connects_at_import.py").write_text(
        "import socket\n\nsocket.create_connection(('127.0.0.1', 9))\n"
    )

    result = run_import_probe("connects_at_import", extra_path=tmp_path)

    assert result.returncode != 0
    assert NETWORK_ERROR_MESSAGE in result.stderr


def test_import_probe_rejects_global_registration(tmp_path: Path) -> None:
    """The probe is not a no-op: configuring at import time fails it."""
    (tmp_path / "configures_at_import.py").write_text(
        "from blockchainpype.initializer import BlockchainsInitializer\n"
        "\n"
        "BlockchainsInitializer.configure()\n"
    )

    result = run_import_probe("configures_at_import", extra_path=tmp_path)

    assert result.returncode != 0
    assert CONFIGURATIONS_ERROR_MESSAGE in result.stderr
