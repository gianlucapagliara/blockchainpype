from typing import Any

from financepype.operators.blockchains.blockchain import Blockchain as _Blockchain


class Blockchain(_Blockchain):
    """
    Base class for all blockchain implementations.
    """

    @property
    def explorer(self) -> Any | None:
        return None
