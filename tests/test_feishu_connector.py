from __future__ import annotations

from dataclasses import dataclass

import pytest

from pm_pal.connectors import ConnectorRegistry, FeishuConnector, SourceType
from pm_pal.connectors.feishu import (
    FeishuAuthenticationError,
    FeishuDocumentNotFoundError,
    FeishuHTTPResponse,
    FeishuPermissionDeniedError,
    FeishuUnsupportedDocumentTypeError,
)


@dataclass(frozen=True, slots=True)
class _ExpectedCall:
    method: str
    path: str
    response: FeishuHTTPResponse


@dataclass(frozen=True, slots=True)
class _RecordedCall:
    method: str
    path: str
    headers: dict[str, str]
    json_body: dict[str, object] | None


class _FakeFeishuClient:
    def __init__(self, responses: list[_ExpectedCall]) -> None:
        self._responses = list(responses)
        self.calls: list[_RecordedCall] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: dict[str, object] | None = None,
    ) -> FeishuHTTPResponse:
        if not self._responses:
            raise AssertionError(f"Unexpected Feishu client call: {method} {path}")
        expected = self._responses.pop(0)
        assert expected.method == method
        assert expected.path == path
        self.calls.append(
            _RecordedCall(
                method=method,
                path=path,
                headers=dict(headers or {}),
                json_body=dict(json_body) if isinstance(json_body, dict) else json_body,
            )
        )
        return expected.response


@pytest.fixture
def feishu_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARRDP_FEISHU_APP_ID", "app-id")
    monkeypatch.setenv("MARRDP_FEISHU_APP_SECRET", "app-secret")
    monkeypatch.setenv(
        "MARRDP_FEISHU_OPEN_BASE_URL", "https://open.feishu.example.test"
    )


def test_feishu_connector_can_handle_custom_scheme_and_lark_urls() -> None:
    connector = FeishuConnector()

    assert connector.can_handle("feishu://wiki/team-space/doc-token") is True
    assert (
        connector.can_handle("https://tenant.feishu.cn/wiki/team-space/doc-token")
        is True
    )
    assert connector.can_handle("https://tenant.larksuite.com/docx/doc-token") is True
    assert connector.can_handle("https://openai.com/spec") is False


def test_connector_registry_routes_feishu_sources() -> None:
    connector = ConnectorRegistry().resolve("feishu://wiki/team-space/doc-token")

    assert isinstance(connector, FeishuConnector)


def test_feishu_connector_fetches_wiki_sources_with_authenticated_client(
    feishu_env: None,
) -> None:
    client = _FakeFeishuClient(
        responses=[
            _ExpectedCall(
                method="POST",
                path="/open-apis/auth/v3/tenant_access_token/internal",
                response=FeishuHTTPResponse(
                    status_code=200,
                    json_body={"code": 0, "tenant_access_token": "tenant-token"},
                ),
            ),
            _ExpectedCall(
                method="GET",
                path="/open-apis/wiki/v2/spaces/team-space/nodes/doc-token",
                response=FeishuHTTPResponse(
                    status_code=200,
                    json_body={
                        "code": 0,
                        "data": {
                            "node": {
                                "obj_type": "docx",
                                "obj_token": "resolved-docx-token",
                                "title": "Team Spec",
                            }
                        },
                    },
                ),
            ),
            _ExpectedCall(
                method="GET",
                path="/open-apis/docx/v1/documents/resolved-docx-token",
                response=FeishuHTTPResponse(
                    status_code=200,
                    json_body={"code": 0, "data": {"document": {"title": "Team Spec"}}},
                ),
            ),
            _ExpectedCall(
                method="GET",
                path="/open-apis/docx/v1/documents/resolved-docx-token/raw_content",
                response=FeishuHTTPResponse(
                    status_code=200,
                    json_body={
                        "code": 0,
                        "data": {"content": "# Team Spec\n\nShip the review flow."},
                    },
                ),
            ),
        ]
    )

    document = FeishuConnector(http_client=client).get_content(
        "https://tenant.feishu.cn/wiki/team-space/doc-token"
    )

    assert document.source_type == SourceType.feishu
    assert document.source == "https://tenant.feishu.cn/wiki/team-space/doc-token"
    assert document.title == "Team Spec"
    assert document.content_markdown == "# Team Spec\n\nShip the review flow."
    assert document.metadata.mime_type == "text/markdown"
    assert document.metadata.extra == {
        "source_kind": "https_url",
        "document_kind": "wiki",
        "resolved_document_kind": "docx",
        "resolved_document_token": "resolved-docx-token",
        "wiki_space": "team-space",
        "host": "tenant.feishu.cn",
        "path": "/wiki/team-space/doc-token",
        "base_url": "https://open.feishu.example.test",
    }
    assert client.calls[0].json_body == {"app_id": "app-id", "app_secret": "app-secret"}
    assert client.calls[1].headers["Authorization"] == "Bearer tenant-token"
    assert client.calls[2].headers["Authorization"] == "Bearer tenant-token"
    assert client.calls[3].headers["Authorization"] == "Bearer tenant-token"


def test_feishu_connector_converts_legacy_docs_sources(feishu_env: None) -> None:
    client = _FakeFeishuClient(
        responses=[
            _ExpectedCall(
                method="POST",
                path="/open-apis/auth/v3/tenant_access_token/internal",
                response=FeishuHTTPResponse(
                    status_code=200,
                    json_body={"code": 0, "tenant_access_token": "tenant-token"},
                ),
            ),
            _ExpectedCall(
                method="POST",
                path="/open-apis/docx/v1/documents/legacy-doc-token/convert",
                response=FeishuHTTPResponse(
                    status_code=200,
                    json_body={
                        "code": 0,
                        "data": {
                            "document": {
                                "document_id": "docx-token",
                                "title": "Legacy Spec",
                            }
                        },
                    },
                ),
            ),
            _ExpectedCall(
                method="GET",
                path="/open-apis/docx/v1/documents/docx-token",
                response=FeishuHTTPResponse(
                    status_code=200,
                    json_body={
                        "code": 0,
                        "data": {"document": {"title": "Legacy Spec"}},
                    },
                ),
            ),
            _ExpectedCall(
                method="GET",
                path="/open-apis/docx/v1/documents/docx-token/raw_content",
                response=FeishuHTTPResponse(
                    status_code=200,
                    json_body={"code": 0, "data": {"content": "Converted markdown"}},
                ),
            ),
        ]
    )

    document = FeishuConnector(http_client=client).get_content(
        "feishu://docs/legacy-doc-token"
    )

    assert document.title == "Legacy Spec"
    assert document.content_markdown == "Converted markdown"
    assert document.metadata.extra["document_kind"] == "docs"
    assert document.metadata.extra["resolved_document_kind"] == "docx"
    assert document.metadata.extra["resolved_document_token"] == "docx-token"


def test_feishu_connector_maps_authentication_failure(feishu_env: None) -> None:
    client = _FakeFeishuClient(
        responses=[
            _ExpectedCall(
                method="POST",
                path="/open-apis/auth/v3/tenant_access_token/internal",
                response=FeishuHTTPResponse(
                    status_code=401,
                    json_body={"code": 99991661, "msg": "invalid app credentials"},
                ),
            ),
        ]
    )

    with pytest.raises(FeishuAuthenticationError, match="authentication failed"):
        FeishuConnector(http_client=client).get_content("feishu://docx/doc-token")


def test_feishu_connector_maps_permission_denied(feishu_env: None) -> None:
    client = _FakeFeishuClient(
        responses=[
            _ExpectedCall(
                method="POST",
                path="/open-apis/auth/v3/tenant_access_token/internal",
                response=FeishuHTTPResponse(
                    status_code=200,
                    json_body={"code": 0, "tenant_access_token": "tenant-token"},
                ),
            ),
            _ExpectedCall(
                method="GET",
                path="/open-apis/docx/v1/documents/doc-token",
                response=FeishuHTTPResponse(
                    status_code=403,
                    json_body={"code": 99991677, "msg": "permission denied"},
                ),
            ),
        ]
    )

    with pytest.raises(FeishuPermissionDeniedError, match="Permission denied"):
        FeishuConnector(http_client=client).get_content("feishu://docx/doc-token")


def test_feishu_connector_maps_document_not_found(feishu_env: None) -> None:
    client = _FakeFeishuClient(
        responses=[
            _ExpectedCall(
                method="POST",
                path="/open-apis/auth/v3/tenant_access_token/internal",
                response=FeishuHTTPResponse(
                    status_code=200,
                    json_body={"code": 0, "tenant_access_token": "tenant-token"},
                ),
            ),
            _ExpectedCall(
                method="GET",
                path="/open-apis/wiki/v2/spaces/team-space/nodes/missing-token",
                response=FeishuHTTPResponse(
                    status_code=404,
                    json_body={"code": 99991668, "msg": "document not found"},
                ),
            ),
        ]
    )

    with pytest.raises(FeishuDocumentNotFoundError, match="document not found"):
        FeishuConnector(http_client=client).get_content(
            "feishu://wiki/team-space/missing-token"
        )


def test_feishu_connector_maps_unsupported_document_type(feishu_env: None) -> None:
    client = _FakeFeishuClient(
        responses=[
            _ExpectedCall(
                method="POST",
                path="/open-apis/auth/v3/tenant_access_token/internal",
                response=FeishuHTTPResponse(
                    status_code=200,
                    json_body={"code": 0, "tenant_access_token": "tenant-token"},
                ),
            ),
            _ExpectedCall(
                method="GET",
                path="/open-apis/wiki/v2/spaces/team-space/nodes/base-token",
                response=FeishuHTTPResponse(
                    status_code=200,
                    json_body={
                        "code": 0,
                        "data": {
                            "node": {"obj_type": "sheet", "obj_token": "sheet-token"}
                        },
                    },
                ),
            ),
        ]
    )

    with pytest.raises(
        FeishuUnsupportedDocumentTypeError,
        match="Unsupported Feishu document type 'sheet'",
    ):
        FeishuConnector(http_client=client).get_content(
            "feishu://wiki/team-space/base-token"
        )


def test_feishu_connector_rejects_non_feishu_sources() -> None:
    with pytest.raises(ValueError, match="Unsupported Feishu source"):
        FeishuConnector().get_content("https://openai.com/spec")
