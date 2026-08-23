import time
from typing import Any

from financepype.operators.blockchains.blockchain import Blockchain as _Blockchain


class Blockchain(_Blockchain):
    """
    Base class for all blockchain implementations.

    Extends the financepype blockchain operator with library-wide defaults
    shared by every concrete chain implementation (e.g. EVM, Solana).
    """

    @property
    def current_timestamp(self) -> float:
        """
        Get the current timestamp used for blockchain operations.

        Defaults to the local wall-clock time in seconds since the epoch.
        Subclasses may override this with a chain-derived timestamp (e.g. the
        latest block timestamp).

        Returns:
            float: The current timestamp in seconds
        """
        return time.time()

    @property
    def explorer(self) -> Any | None:
        """
        Get the block explorer bound to this blockchain, if any.

        Returns:
            Any | None: The explorer instance, or None when the blockchain was
                configured without one. Concrete subclasses narrow the return
                type to their explorer implementation (e.g. EtherscanExplorer)
        """
        return None
