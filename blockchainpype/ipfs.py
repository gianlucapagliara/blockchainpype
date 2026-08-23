"""
Utilities for working with IPFS (InterPlanetary File System) resources.

This module provides helpers to translate ``ipfs://`` URIs (or bare content
identifiers) into HTTP gateway URLs and to download the referenced content.
The gateway is configurable per call or globally via the ``IPFS_GATEWAY``
environment variable, defaulting to the public ``https://ipfs.io`` gateway.
"""

import os
import re

import aiohttp

DEFAULT_IPFS_GATEWAY = "https://ipfs.io/ipfs/"
IPFS_GATEWAY_ENV_VAR = "IPFS_GATEWAY"

# CIDs are base58 (v0) or multibase-encoded (typically base32, v1) strings:
# a single alphanumeric token without separators.
_CID_REGEX = re.compile(r"^[A-Za-z0-9]+$")
# Splits an IPFS path into the leading CID and everything that follows it
# (sub-path, query string, and/or fragment), which must be preserved verbatim.
_CID_SPLIT_REGEX = re.compile(r"^(?P<cid>[^/?#]*)(?P<suffix>.*)$", flags=re.DOTALL)


class InvalidIPFSURIError(ValueError):
    """Raised when a string cannot be interpreted as an IPFS URI or CID."""


def _resolve_gateway(gateway: str | None) -> str:
    """Resolve the HTTP gateway prefix used to serve IPFS content.

    Args:
        gateway: Explicit gateway base URL, or None to use the ``IPFS_GATEWAY``
            environment variable, falling back to :data:`DEFAULT_IPFS_GATEWAY`.
            Both ``https://host`` and ``https://host/ipfs`` forms are accepted.

    Returns:
        str: The normalized gateway prefix, always ending in ``/ipfs/``.
    """
    resolved = gateway or os.getenv(IPFS_GATEWAY_ENV_VAR) or DEFAULT_IPFS_GATEWAY
    resolved = resolved.rstrip("/")
    if not resolved.endswith("/ipfs"):
        resolved = f"{resolved}/ipfs"
    return f"{resolved}/"


def get_http_from_ipfs(uri: str, gateway: str | None = None) -> str:
    """Convert an IPFS URI into an HTTP gateway URL.

    Supported input forms:
        - ``ipfs://<cid>[/path][?query][#fragment]``
        - ``ipfs://ipfs/<cid>[/path]`` (gateway-style path emitted by some tools)
        - ``<cid>[/path][?query]`` (bare content identifier)

    Any sub-path, query string, and fragment are preserved verbatim.

    Args:
        uri: The IPFS URI or bare CID to convert.
        gateway: Optional gateway base URL overriding the ``IPFS_GATEWAY``
            environment variable and the default ``https://ipfs.io`` gateway.

    Returns:
        str: The equivalent HTTP gateway URL.

    Raises:
        InvalidIPFSURIError: If the URI is empty, uses a non-IPFS scheme, or
            does not contain a valid CID.
    """
    stripped = uri.strip() if uri else ""
    if not stripped:
        raise InvalidIPFSURIError("IPFS URI cannot be empty")

    if "://" in stripped:
        scheme, _, rest = stripped.partition("://")
        if scheme.lower() != "ipfs":
            raise InvalidIPFSURIError(
                f"Unsupported URI scheme '{scheme}' in '{uri}': expected 'ipfs://'"
            )
    else:
        rest = stripped

    # Normalize the gateway-style 'ipfs/<cid>' prefix (e.g. 'ipfs://ipfs/<cid>').
    if rest.lower().startswith("ipfs/"):
        rest = rest[len("ipfs/") :]

    match = _CID_SPLIT_REGEX.match(rest)
    if match is None:  # pragma: no cover - the pattern matches any string
        raise InvalidIPFSURIError(f"Malformed IPFS URI: '{uri}'")

    cid = match.group("cid")
    suffix = match.group("suffix")

    if not cid:
        raise InvalidIPFSURIError(f"IPFS URI '{uri}' does not contain a CID")
    if not _CID_REGEX.match(cid):
        raise InvalidIPFSURIError(f"IPFS URI '{uri}' contains an invalid CID '{cid}'")

    return f"{_resolve_gateway(gateway)}{cid}{suffix}"


async def get_ipfs_data(
    uri: str,
    gateway: str | None = None,
    timeout_seconds: float = 30.0,
) -> bytes:
    """Download the content referenced by an IPFS URI via an HTTP gateway.

    Args:
        uri: The IPFS URI or bare CID to fetch (see :func:`get_http_from_ipfs`).
        gateway: Optional gateway base URL overriding the ``IPFS_GATEWAY``
            environment variable and the default ``https://ipfs.io`` gateway.
        timeout_seconds: Total request timeout in seconds.

    Returns:
        bytes: The raw content served by the gateway.

    Raises:
        InvalidIPFSURIError: If the URI cannot be converted to a gateway URL.
        aiohttp.ClientResponseError: If the gateway returns an error status.
        aiohttp.ClientError: If the HTTP request fails.
    """
    url = get_http_from_ipfs(uri, gateway=gateway)
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    async with (
        aiohttp.ClientSession(timeout=timeout) as session,
        session.get(url) as response,
    ):
        response.raise_for_status()
        data: bytes = await response.read()
        return data
