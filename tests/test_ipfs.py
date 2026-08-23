"""
Unit tests for blockchainpype.ipfs.

This module tests:
- Conversion of ipfs:// URIs (including ipfs://ipfs/... and bare CIDs) to
  HTTP gateway URLs, with sub-path/query/fragment preservation
- Gateway configuration via parameter and the IPFS_GATEWAY environment variable
- Clear errors on invalid URIs
- Async data fetching through aiohttp (mocked at the session boundary)
"""

from types import TracebackType

import aiohttp
import pytest

import blockchainpype.ipfs as ipfs_module
from blockchainpype.ipfs import (
    DEFAULT_IPFS_GATEWAY,
    IPFS_GATEWAY_ENV_VAR,
    InvalidIPFSURIError,
    get_http_from_ipfs,
    get_ipfs_data,
)

CID_V0 = "QmYwAPJzv5CZsnA625s3Xf2nemtYgPpHdWEz79ojWnPbdG"
CID_V1 = "bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi"


class TestGetHttpFromIpfs:
    """Tests for the ipfs:// -> HTTP gateway URL conversion matrix."""

    def test_plain_ipfs_uri(self):
        assert (
            get_http_from_ipfs(f"ipfs://{CID_V0}") == f"https://ipfs.io/ipfs/{CID_V0}"
        )

    def test_cid_v1_uri(self):
        assert (
            get_http_from_ipfs(f"ipfs://{CID_V1}") == f"https://ipfs.io/ipfs/{CID_V1}"
        )

    def test_uri_with_path(self):
        assert (
            get_http_from_ipfs(f"ipfs://{CID_V0}/metadata/1.json")
            == f"https://ipfs.io/ipfs/{CID_V0}/metadata/1.json"
        )

    def test_uri_with_path_query_and_fragment(self):
        uri = f"ipfs://{CID_V0}/dir/file.json?filename=a.json&x=1#frag"
        assert (
            get_http_from_ipfs(uri)
            == f"https://ipfs.io/ipfs/{CID_V0}/dir/file.json?filename=a.json&x=1#frag"
        )

    def test_query_without_path(self):
        assert (
            get_http_from_ipfs(f"ipfs://{CID_V0}?x=1")
            == f"https://ipfs.io/ipfs/{CID_V0}?x=1"
        )

    def test_gateway_style_ipfs_prefix(self):
        assert (
            get_http_from_ipfs(f"ipfs://ipfs/{CID_V0}/art.png")
            == f"https://ipfs.io/ipfs/{CID_V0}/art.png"
        )

    def test_bare_cid(self):
        assert get_http_from_ipfs(CID_V0) == f"https://ipfs.io/ipfs/{CID_V0}"

    def test_bare_cid_with_path(self):
        assert (
            get_http_from_ipfs(f"{CID_V0}/1.json")
            == f"https://ipfs.io/ipfs/{CID_V0}/1.json"
        )

    def test_bare_ipfs_prefixed_path(self):
        assert get_http_from_ipfs(f"ipfs/{CID_V0}") == f"https://ipfs.io/ipfs/{CID_V0}"

    def test_uppercase_scheme(self):
        assert (
            get_http_from_ipfs(f"IPFS://{CID_V0}") == f"https://ipfs.io/ipfs/{CID_V0}"
        )

    def test_surrounding_whitespace_is_stripped(self):
        assert (
            get_http_from_ipfs(f"  ipfs://{CID_V0}  ")
            == f"https://ipfs.io/ipfs/{CID_V0}"
        )


class TestGatewayConfiguration:
    """Tests for gateway override via parameter and environment variable."""

    def test_gateway_parameter_bare_host(self):
        assert (
            get_http_from_ipfs(
                f"ipfs://{CID_V0}", gateway="https://cloudflare-ipfs.com"
            )
            == f"https://cloudflare-ipfs.com/ipfs/{CID_V0}"
        )

    def test_gateway_parameter_with_ipfs_suffix(self):
        assert (
            get_http_from_ipfs(f"ipfs://{CID_V0}", gateway="https://dweb.link/ipfs/")
            == f"https://dweb.link/ipfs/{CID_V0}"
        )

    def test_gateway_env_variable(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(IPFS_GATEWAY_ENV_VAR, "https://gateway.pinata.cloud")
        assert (
            get_http_from_ipfs(f"ipfs://{CID_V0}")
            == f"https://gateway.pinata.cloud/ipfs/{CID_V0}"
        )

    def test_gateway_parameter_overrides_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(IPFS_GATEWAY_ENV_VAR, "https://gateway.pinata.cloud")
        assert (
            get_http_from_ipfs(f"ipfs://{CID_V0}", gateway="https://dweb.link")
            == f"https://dweb.link/ipfs/{CID_V0}"
        )

    def test_default_gateway_when_env_unset(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv(IPFS_GATEWAY_ENV_VAR, raising=False)
        assert DEFAULT_IPFS_GATEWAY == "https://ipfs.io/ipfs/"
        assert get_http_from_ipfs(CID_V0) == f"{DEFAULT_IPFS_GATEWAY}{CID_V0}"


class TestInvalidUris:
    """Tests for clear errors on invalid URIs."""

    def test_empty_string(self):
        with pytest.raises(InvalidIPFSURIError, match="cannot be empty"):
            get_http_from_ipfs("")

    def test_whitespace_only(self):
        with pytest.raises(InvalidIPFSURIError, match="cannot be empty"):
            get_http_from_ipfs("   ")

    def test_http_scheme_rejected(self):
        with pytest.raises(InvalidIPFSURIError, match="Unsupported URI scheme 'https'"):
            get_http_from_ipfs(f"https://ipfs.io/ipfs/{CID_V0}")

    def test_missing_cid(self):
        with pytest.raises(InvalidIPFSURIError, match="does not contain a CID"):
            get_http_from_ipfs("ipfs://")

    def test_missing_cid_after_ipfs_prefix(self):
        with pytest.raises(InvalidIPFSURIError, match="does not contain a CID"):
            get_http_from_ipfs("ipfs://ipfs/")

    def test_slash_only_path(self):
        with pytest.raises(InvalidIPFSURIError, match="does not contain a CID"):
            get_http_from_ipfs("ipfs:///path")

    def test_invalid_cid_characters(self):
        with pytest.raises(InvalidIPFSURIError, match="invalid CID"):
            get_http_from_ipfs("ipfs://not-a-cid!")

    def test_bare_string_with_spaces(self):
        with pytest.raises(InvalidIPFSURIError, match="invalid CID"):
            get_http_from_ipfs("not a cid")

    def test_invalid_error_is_value_error(self):
        with pytest.raises(ValueError):
            get_http_from_ipfs("ipfs://")


class FakeResponse:
    """Minimal stand-in for aiohttp.ClientResponse."""

    def __init__(self, data: bytes, status: int = 200):
        self._data = data
        self.status = status

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise aiohttp.ClientResponseError(
                request_info=None,  # type: ignore[arg-type]
                history=(),
                status=self.status,
                message="Gateway error",
            )

    async def read(self) -> bytes:
        return self._data

    async def __aenter__(self) -> "FakeResponse":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None


class FakeSession:
    """Minimal stand-in for aiohttp.ClientSession recording requested URLs."""

    requested_urls: list[str] = []
    response: FakeResponse = FakeResponse(b"")
    init_kwargs: dict = {}

    def __init__(self, **kwargs):
        type(self).init_kwargs = kwargs

    def get(self, url: str) -> FakeResponse:
        type(self).requested_urls.append(url)
        return type(self).response

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None


@pytest.fixture
def fake_session(monkeypatch: pytest.MonkeyPatch) -> type[FakeSession]:
    FakeSession.requested_urls = []
    FakeSession.response = FakeResponse(b"")
    FakeSession.init_kwargs = {}
    monkeypatch.setattr(ipfs_module.aiohttp, "ClientSession", FakeSession)
    return FakeSession


class TestGetIpfsData:
    """Tests for the async gateway fetch, mocked at the aiohttp boundary."""

    async def test_fetches_bytes_from_gateway(self, fake_session: type[FakeSession]):
        payload = b'{"name": "token"}'
        fake_session.response = FakeResponse(payload)

        data = await get_ipfs_data(f"ipfs://{CID_V0}/metadata.json")

        assert data == payload
        assert fake_session.requested_urls == [
            f"https://ipfs.io/ipfs/{CID_V0}/metadata.json"
        ]

    async def test_gateway_override_is_used(self, fake_session: type[FakeSession]):
        fake_session.response = FakeResponse(b"binary")

        data = await get_ipfs_data(CID_V1, gateway="https://dweb.link")

        assert data == b"binary"
        assert fake_session.requested_urls == [f"https://dweb.link/ipfs/{CID_V1}"]

    async def test_timeout_is_configured(self, fake_session: type[FakeSession]):
        await get_ipfs_data(f"ipfs://{CID_V0}", timeout_seconds=5.0)

        timeout = fake_session.init_kwargs["timeout"]
        assert isinstance(timeout, aiohttp.ClientTimeout)
        assert timeout.total == 5.0

    async def test_error_status_raises(self, fake_session: type[FakeSession]):
        fake_session.response = FakeResponse(b"", status=404)

        with pytest.raises(aiohttp.ClientResponseError) as exc_info:
            await get_ipfs_data(f"ipfs://{CID_V0}")

        assert exc_info.value.status == 404

    async def test_invalid_uri_raises_without_network(
        self, fake_session: type[FakeSession]
    ):
        with pytest.raises(InvalidIPFSURIError):
            await get_ipfs_data("ipfs://")

        assert fake_session.requested_urls == []
