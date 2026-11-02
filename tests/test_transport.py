"""DNS, slow I/O and response limits remain inside a killed worker."""

from email.message import Message
import io
import multiprocessing
import time
from urllib.error import HTTPError
import pytest
import transport
from validation import Failure, connection_values


def config():
    return connection_values(
        {
            "config": {"origin": "https://es.example", "indices": ["chunks"]},
            "secrets": {"api_key": "dummy"},
        }
    )[0]


class Response(io.BytesIO):
    status = 200

    def __init__(self, raw=b"{}"):
        super().__init__(raw)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def test_transport_pinned_headers(monkeypatch):
    monkeypatch.setattr(transport, "resolve_pinned_ips", lambda *a, **k: ["1.1.1.1", "8.8.8.8"])

    def open_url(request, **kw):
        assert request.full_url == "https://es.example/chunks/_search"
        assert request.headers["Authorization"] == "ApiKey dummy"
        assert kw["pinned_ips"] == ("1.1.1.1", "8.8.8.8")
        return Response(b'{"ok":true}')

    monkeypatch.setattr(transport, "open_pinned_url", open_url)
    assert transport.invoke(
        config(), {"api_key": "dummy"}, "POST", "/chunks/_search", {}, timeout_seconds=1
    ) == {"ok": True}


def test_dns_deadline_kills_worker(monkeypatch):
    def resolve(*a, **k):
        time.sleep(10)

    monkeypatch.setattr(transport, "resolve_pinned_ips", resolve)
    before = {p.pid for p in multiprocessing.active_children()}
    start = time.monotonic()
    with pytest.raises(Failure) as error:
        transport.invoke(
            config(), {"api_key": "dummy"}, "GET", "/chunks/_mapping", timeout_seconds=0.15
        )
    assert error.value.code == "timeout" and not error.value.dispatched
    assert time.monotonic() - start < 1
    assert {p.pid for p in multiprocessing.active_children()} == before


def test_slow_body_total_deadline(monkeypatch):
    monkeypatch.setattr(transport, "resolve_pinned_ips", lambda *a, **k: ["1.1.1.1"])

    class Slow(Response):
        def read(self, n=-1, /):
            time.sleep(10)
            return b"{}"

    monkeypatch.setattr(transport, "open_pinned_url", lambda *a, **k: Slow())
    start = time.monotonic()
    with pytest.raises(Failure) as error:
        transport.invoke(
            config(), {"api_key": "dummy"}, "PUT", "/chunks/_doc/one", {}, timeout_seconds=0.15
        )
    assert error.value.code == "timeout" and error.value.dispatched
    assert time.monotonic() - start < 1


@pytest.mark.parametrize("ips", [["1.1.1.1", "127.0.0.1"], ["127.0.0.1"], []])
def test_all_addresses_policy(monkeypatch, ips):
    monkeypatch.delenv("FS_ALLOW_PRIVATE_REMOTE_URLS", raising=False)
    monkeypatch.setattr(transport, "resolve_pinned_ips", lambda *a, **k: ips)
    with pytest.raises(Failure) as error:
        transport.invoke(
            config(), {"api_key": "dummy"}, "GET", "/chunks/_mapping", timeout_seconds=1
        )
    assert error.value.code == "network_blocked"


@pytest.mark.parametrize(
    "status,code",
    [
        (301, "provider_unavailable"),
        (401, "authentication_failed"),
        (403, "authentication_failed"),
        (429, "rate_limited"),
    ],
)
def test_safe_http_errors(monkeypatch, status, code):
    monkeypatch.setattr(transport, "resolve_pinned_ips", lambda *a, **k: ["1.1.1.1"])

    def fail(*a, **k):
        raise HTTPError("secret", status, "private provider body", Message(), None)

    monkeypatch.setattr(transport, "open_pinned_url", fail)
    with pytest.raises(Failure) as error:
        transport.invoke(
            config(), {"api_key": "dummy"}, "GET", "/chunks/_mapping", timeout_seconds=1
        )
    assert error.value.code == code
    assert "secret" not in str(error.value) and "private" not in str(error.value)


def test_response_size_limit(monkeypatch):
    monkeypatch.setattr(transport, "resolve_pinned_ips", lambda *a, **k: ["1.1.1.1"])
    monkeypatch.setattr(
        transport, "open_pinned_url", lambda *a, **k: Response(b" " * (1024 * 1024 + 1))
    )
    with pytest.raises(Failure) as error:
        transport.invoke(
            config(), {"api_key": "dummy"}, "GET", "/chunks/_mapping", timeout_seconds=1
        )
    assert error.value.code == "response_too_large"
