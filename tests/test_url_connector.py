from __future__ import annotations

import socket
from email.message import Message
from urllib.error import URLError
from urllib.request import Request

import pytest

from pm_pal.connectors import SourceType, URLConnector


class FakeResponse:
    def __init__(
        self, body: bytes, *, url: str, content_type: str, charset: str = "utf-8"
    ) -> None:
        self._body = body
        self._url = url
        self.headers = Message()
        self.headers.add_header("Content-Type", content_type, charset=charset)
        self.headers["Content-Length"] = str(len(body))

    def read(self, amount: int = -1) -> bytes:
        if amount < 0:
            return self._body
        return self._body[:amount]

    def geturl(self) -> str:
        return self._url

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


@pytest.fixture
def allow_public_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_getaddrinfo(host: str, port, *args, **kwargs):
        del host, port, args, kwargs
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", 0))
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


def test_url_connector_fetches_markdown_into_source_document(
    allow_public_hosts,
) -> None:
    captured: dict[str, object] = {}

    def opener(request: Request, timeout: float = 0.0) -> FakeResponse:
        captured["url"] = request.full_url
        captured["accept"] = request.get_header("Accept")
        captured["user_agent"] = request.get_header("User-agent")
        captured["timeout"] = timeout
        return FakeResponse(
            b"# Remote Spec\n\nship it",
            url="https://openai.com/spec.md",
            content_type="text/markdown",
        )

    document = URLConnector(opener=opener, timeout_seconds=3.5).get_content(
        "https://openai.com/spec.md"
    )

    assert captured == {
        "url": "https://openai.com/spec.md",
        "accept": "text/markdown, text/html, text/plain;q=0.9, text/*;q=0.8",
        "user_agent": "marrdp-requirement-review/1.0",
        "timeout": 3.5,
    }
    assert document.source_type == SourceType.url
    assert document.source == "https://openai.com/spec.md"
    assert document.title == "spec"
    assert document.content_markdown == "# Remote Spec\n\nship it"
    assert document.metadata.mime_type == "text/markdown"
    assert document.metadata.encoding == "utf-8"
    assert document.metadata.size_bytes == len(b"# Remote Spec\n\nship it")
    assert document.metadata.extra == {
        "requested_url": "https://openai.com/spec.md",
        "final_url": "https://openai.com/spec.md",
    }


def test_url_connector_normalizes_html_to_text(allow_public_hosts) -> None:
    body = b"<html><head><title>Hosted PRD</title></head><body><main><h1>Hello</h1><p>World</p></main></body></html>"

    document = URLConnector(
        opener=lambda request, timeout=0.0: FakeResponse(
            body, url=request.full_url, content_type="text/html"
        )
    ).get_content("https://openai.com/prd")

    assert document.title == "Hosted PRD"
    assert document.content_markdown == "Hello\n\nWorld"
    assert document.metadata.mime_type == "text/html"


def test_url_connector_rejects_non_text_content_types(allow_public_hosts) -> None:
    connector = URLConnector(
        opener=lambda request, timeout=0.0: FakeResponse(
            b"%PDF", url=request.full_url, content_type="application/pdf"
        )
    )

    with pytest.raises(ValueError, match="Unsupported URL content type"):
        connector.get_content("https://openai.com/spec.pdf")


def test_url_connector_raises_clear_error_when_network_is_unavailable(
    allow_public_hosts,
) -> None:
    def failing_opener(request: Request, timeout: float = 0.0) -> FakeResponse:
        del request, timeout
        raise URLError(socket.gaierror(11001, "getaddrinfo failed"))

    with pytest.raises(
        ConnectionError, match="Network unavailable while fetching URL source"
    ):
        URLConnector(opener=failing_opener).get_content("https://openai.com/spec")


def test_url_connector_rejects_private_hosts_before_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def private_getaddrinfo(host: str, port, *args, **kwargs):
        del host, port, args, kwargs
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("127.0.0.1", 0),
            )
        ]

    called = False

    def opener(request: Request, timeout: float = 0.0) -> FakeResponse:
        del request, timeout
        nonlocal called
        called = True
        return FakeResponse(
            b"hello", url="https://localhost/spec", content_type="text/plain"
        )

    monkeypatch.setattr(socket, "getaddrinfo", private_getaddrinfo)

    with pytest.raises(ValueError, match="publicly reachable"):
        URLConnector(opener=opener).get_content("https://internal.example/spec")

    assert called is False


def test_url_connector_surfaces_dns_validation_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_getaddrinfo(host: str, port, *args, **kwargs):
        del host, port, args, kwargs
        raise socket.gaierror(11001, "getaddrinfo failed")

    monkeypatch.setattr(socket, "getaddrinfo", failing_getaddrinfo)

    with pytest.raises(
        ConnectionError, match="Network unavailable while validating URL source"
    ):
        URLConnector(opener=lambda request, timeout=0.0: None).get_content(
            "https://openai.com/spec"
        )
