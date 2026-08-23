"""Runnable examples for blockchainpype.

Every module in this package is safe to import: nothing connects to a network,
reads a private key, or mutates global state at import time. All side effects
live inside an explicit ``main()`` entry point, so the examples can be smoke
tested (see ``tests/test_examples.py``) and reused as library code.

Run one from the repository root, e.g.::

    uv run python -m examples.basic.configure
"""
