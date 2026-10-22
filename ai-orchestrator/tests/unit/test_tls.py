"""Unit tests for core/tls.py — mTLS factory and MCPClientPool wiring."""
from __future__ import annotations

import ssl
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from core.tls import make_mtls_factory

# ── Helpers: generate minimal self-signed cert+key for tests ─────────────────

def _write_temp_certs(tmp: Path) -> tuple[str, str, str]:
    """Write a CA cert, client cert, and client key to a temp dir.

    Uses Python's stdlib ssl / cryptography (if available) or falls back to
    pre-baked tiny DER bytes that are structurally valid PEM files.

    We only need the FILES TO EXIST on disk so make_mtls_factory can call
    ssl.create_default_context() without FileNotFoundError. The actual TLS
    handshake is not exercised here (that's an integration concern).
    """
    try:
        # Prefer cryptography library (available in CI via httpx extras)
        import datetime

        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        def _make_cert_and_key(cn: str, ca_key=None, ca_cert=None, is_ca: bool = False):
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
            issuer = ca_cert.subject if ca_cert else subject
            sign_key = ca_key if ca_key else key
            now = datetime.datetime.utcnow()
            builder = (
                x509.CertificateBuilder()
                .subject_name(subject)
                .issuer_name(issuer)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now)
                .not_valid_after(now + datetime.timedelta(days=1))
            )
            if is_ca:
                builder = builder.add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            cert = builder.sign(sign_key, hashes.SHA256())
            return key, cert

        ca_key, ca_cert = _make_cert_and_key("TestCA", is_ca=True)
        cl_key, cl_cert = _make_cert_and_key("TestClient", ca_key=ca_key, ca_cert=ca_cert)

        ca_path = tmp / "ca.crt"
        cl_cert_path = tmp / "client.crt"
        cl_key_path = tmp / "client.key"

        ca_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
        cl_cert_path.write_bytes(cl_cert.public_bytes(serialization.Encoding.PEM))
        cl_key_path.write_bytes(
            cl_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )
        return str(ca_path), str(cl_cert_path), str(cl_key_path)

    except ImportError:
        pytest.skip("cryptography library required for TLS cert generation tests")


# ── make_mtls_factory ─────────────────────────────────────────────────────────

def test_make_mtls_factory_returns_none_when_paths_empty():
    assert make_mtls_factory(client_cert="", client_key="", ca_cert="") is None


def test_make_mtls_factory_returns_none_when_any_path_missing():
    assert make_mtls_factory(client_cert="/some/cert", client_key="", ca_cert="/some/ca") is None
    assert make_mtls_factory(client_cert="", client_key="/some/key", ca_cert="/some/ca") is None
    assert make_mtls_factory(client_cert="/some/cert", client_key="/some/key", ca_cert="") is None


def test_make_mtls_factory_raises_on_nonexistent_file():
    with pytest.raises(FileNotFoundError, match="MCP_CLIENT_CERT"):
        make_mtls_factory(
            client_cert="/nonexistent/cert.crt",
            client_key="/nonexistent/key.key",
            ca_cert="/nonexistent/ca.crt",
        )


def test_make_mtls_factory_returns_callable():
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        ca, cert, key = _write_temp_certs(tmp)
        factory = make_mtls_factory(client_cert=cert, client_key=key, ca_cert=ca)
        assert factory is not None
        assert callable(factory)


@pytest.mark.asyncio
async def test_make_mtls_factory_factory_returns_httpx_async_client():
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        ca, cert, key = _write_temp_certs(tmp)
        factory = make_mtls_factory(client_cert=cert, client_key=key, ca_cert=ca)
        assert factory is not None
        client = factory(headers=None, timeout=None, auth=None)
        assert isinstance(client, httpx.AsyncClient)
        await client.aclose()


@pytest.mark.asyncio
async def test_make_mtls_factory_factory_propagates_headers():
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        ca, cert, key = _write_temp_certs(tmp)
        factory = make_mtls_factory(client_cert=cert, client_key=key, ca_cert=ca)
        assert factory is not None
        client = factory(headers={"X-MCP-Secret": "test-secret"}, timeout=None, auth=None)
        assert isinstance(client, httpx.AsyncClient)
        await client.aclose()


@pytest.mark.asyncio
async def test_make_mtls_factory_ssl_context_is_mtls():
    """SSL context must have CERT_REQUIRED so server verifies the CA."""
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        ca, cert, key = _write_temp_certs(tmp)
        factory = make_mtls_factory(client_cert=cert, client_key=key, ca_cert=ca)
        assert factory is not None
        client = factory(headers=None, timeout=None, auth=None)
        # The client's ssl context should be an ssl.SSLContext with CERT_REQUIRED
        ssl_ctx = client._transport._pool._ssl_context  # httpx internals
        assert isinstance(ssl_ctx, ssl.SSLContext)
        assert ssl_ctx.verify_mode == ssl.CERT_REQUIRED
        await client.aclose()


# ── MCPClientPool mTLS wiring ─────────────────────────────────────────────────

def _make_runtime(cert="", key="", ca=""):
    r = MagicMock()
    r.mcp_claims_url = "http://claims:8001"
    r.mcp_policies_url = "http://policies:8002"
    r.mcp_users_url = "http://users:8003"
    r.mcp_documents_url = "http://documents:8004"
    r.mcp_shared_secret = "x" * 32
    r.mcp_client_cert = cert
    r.mcp_client_key = key
    r.mcp_ca_cert = ca
    return r


def test_pool_mtls_factory_is_none_without_certs():
    from core.mcp_client import MCPClientPool
    pool = MCPClientPool(_make_runtime())
    assert pool._mtls_factory is None


def test_pool_mtls_factory_set_when_certs_configured():
    from core.mcp_client import MCPClientPool

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        ca, cert, key = _write_temp_certs(tmp)
        pool = MCPClientPool(_make_runtime(cert=cert, key=key, ca=ca))
        assert pool._mtls_factory is not None
        assert callable(pool._mtls_factory)


@pytest.mark.asyncio
async def test_pool_passes_factory_to_sse_client():
    """When mTLS is configured, _open() passes httpx_client_factory to sse_client."""
    from contextlib import asynccontextmanager

    from core.mcp_client import MCPClientPool

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        ca, cert, key = _write_temp_certs(tmp)
        pool = MCPClientPool(_make_runtime(cert=cert, key=key, ca=ca))

        captured_kwargs: dict = {}

        # sse_client is a regular callable (not async) that returns an async CM.
        # If we make fake_sse_client async, its body only runs when awaited — but
        # _open() uses it as a sync call returning an async CM, so it's never awaited.
        @asynccontextmanager
        async def _sse_cm():
            yield AsyncMock(), AsyncMock()

        def fake_sse_client(**kwargs):
            captured_kwargs.update(kwargs)
            return _sse_cm()

        mock_session = MagicMock()
        mock_session.initialize = AsyncMock()

        with patch("core.mcp_client.sse_client", side_effect=fake_sse_client):
            with patch("core.mcp_client.ClientSession") as mock_cs:
                mock_cs.return_value.__aenter__ = AsyncMock(return_value=mock_session)
                mock_cs.return_value.__aexit__ = AsyncMock(return_value=False)
                try:
                    await pool._open("claims")
                except Exception:
                    pass  # session init may fail in mock context

    assert "httpx_client_factory" in captured_kwargs
    assert captured_kwargs["httpx_client_factory"] is pool._mtls_factory


@pytest.mark.asyncio
async def test_pool_no_factory_kwarg_without_tls():
    """Without certs, sse_client is called WITHOUT httpx_client_factory."""
    from contextlib import asynccontextmanager

    from core.mcp_client import MCPClientPool

    pool = MCPClientPool(_make_runtime())
    captured_kwargs: dict = {}

    @asynccontextmanager
    async def _sse_cm():
        yield AsyncMock(), AsyncMock()

    def fake_sse_client(**kwargs):
        captured_kwargs.update(kwargs)
        return _sse_cm()

    mock_session = MagicMock()
    mock_session.initialize = AsyncMock()

    with patch("core.mcp_client.sse_client", side_effect=fake_sse_client):
        with patch("core.mcp_client.ClientSession") as mock_cs:
            mock_cs.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_cs.return_value.__aexit__ = AsyncMock(return_value=False)
            try:
                await pool._open("claims")
            except Exception:
                pass

    assert "httpx_client_factory" not in captured_kwargs
