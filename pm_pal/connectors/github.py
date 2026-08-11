"""Authenticated connector for GitHub README, file, issue, and PR ingestion."""

from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from pm_pal.connectors.base import BaseConnector
from pm_pal.connectors.errors import (
    ConnectorAuthError,
    ConnectorNetworkError,
    ConnectorNotFoundError,
    ConnectorPermissionError,
    ConnectorUnsupportedSourceError,
    ConnectorValidationError,
)
from pm_pal.connectors.normalize import extract_mapping, extract_message
from pm_pal.connectors.schemas import SourceDocument, SourceMetadata, SourceType
from pm_pal.integrations.github.config_store import GitHubAuthMode


@dataclass(frozen=True, slots=True)
class GitHubSourceRef:
    raw_source: str
    owner: str
    repo: str
    resource_kind: str
    path: str = ""
    number: int | None = None


@dataclass(frozen=True, slots=True)
class GitHubConfig:
    auth_mode: GitHubAuthMode
    app_id: str
    private_key: str
    installation_id: str
    personal_access_token: str
    base_url: str


@dataclass(frozen=True, slots=True)
class GitHubHTTPResponse:
    status_code: int
    json_body: dict[str, Any] | list[Any]
    headers: dict[str, str]


class GitHubAuthenticationError(ConnectorAuthError):
    """Raised when GitHub credentials are missing or rejected."""


class GitHubPermissionDeniedError(ConnectorPermissionError):
    """Raised when GitHub credentials lack access to a resource."""


class GitHubResourceNotFoundError(ConnectorNotFoundError):
    """Raised when a recognized GitHub resource cannot be found."""


class _GitHubHTTPClient(Protocol):
    def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> GitHubHTTPResponse: ...


class _DefaultGitHubHTTPClient:
    DEFAULT_TIMEOUT_SECONDS = 15.0
    USER_AGENT = "marrdp-requirement-review/1.0"
    API_VERSION = "2022-11-28"

    def __init__(
        self, *, base_url: str, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    ) -> None:
        self._base_url = str(base_url or "").rstrip("/")
        self._timeout_seconds = timeout_seconds

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> GitHubHTTPResponse:
        normalized_path = "/" + str(path or "").lstrip("/")
        url = f"{self._base_url}{normalized_path}"
        request_headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": self.API_VERSION,
            "User-Agent": self.USER_AGENT,
        }
        if headers:
            request_headers.update(headers)

        try:
            with httpx.Client(timeout=self._timeout_seconds) as client:
                response = client.request(
                    method.upper(),
                    url,
                    headers=request_headers,
                    params=params,
                )
        except httpx.RequestError as exc:
            raise ConnectorNetworkError(
                f"Network unavailable while fetching GitHub source from '{url}': {exc}",
                source=url,
                details={"connector": "github_http"},
            ) from exc

        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if not isinstance(payload, (dict, list)):
            payload = {}
        return GitHubHTTPResponse(
            status_code=response.status_code,
            json_body=payload,
            headers=dict(response.headers),
        )


_GITHUB_HOSTS = {"github.com", "www.github.com"}
_GITHUB_SCHEME_RE = re.compile(
    r"^github://(?P<owner>[^/]+)/(?P<repo>[^/]+)(?:/(?P<rest>.+))?$",
    re.IGNORECASE,
)
_GITHUB_URL_RE = re.compile(
    r"github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)(?:/(?P<rest>.+))?$",
    re.IGNORECASE,
)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def build_github_app_jwt(*, app_id: str, private_key_pem: str) -> str:
    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    payload = {"iat": now - 60, "exp": now + 600, "iss": str(app_id).strip()}
    segments = [
        _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8")),
        _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8")),
    ]
    signing_input = ".".join(segments).encode("ascii")
    private_key = serialization.load_pem_private_key(
        private_key_pem.encode("utf-8"), password=None
    )
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return ".".join(segments + [_b64url(signature)])


def parse_github_source(source: str) -> GitHubSourceRef:
    normalized = str(source or "").strip()
    if not normalized:
        raise ConnectorUnsupportedSourceError(
            "GitHub source must be a non-empty URL or github:// reference.",
            source=source,
        )

    scheme_match = _GITHUB_SCHEME_RE.match(normalized)
    if scheme_match:
        owner = scheme_match.group("owner").strip()
        repo = scheme_match.group("repo").strip()
        rest = str(scheme_match.group("rest") or "").strip().strip("/")
        return _parse_github_rest(normalized, owner, repo, rest)

    parsed = urlparse(normalized)
    host = parsed.netloc.lower()
    if host in _GITHUB_HOSTS or host.endswith(".github.com"):
        path = parsed.path.strip("/")
        url_match = _GITHUB_URL_RE.search(normalized)
        if url_match:
            owner = url_match.group("owner").strip()
            repo = url_match.group("repo").strip()
            rest = str(url_match.group("rest") or "").strip().strip("/")
            return _parse_github_rest(normalized, owner, repo, rest)
        if path.count("/") >= 1:
            owner, repo, *rest_parts = path.split("/")
            rest = "/".join(rest_parts)
            return _parse_github_rest(normalized, owner, repo, rest)

    raise ConnectorUnsupportedSourceError(
        f"Unrecognized GitHub source: '{normalized}'",
        source=normalized,
    )


def _parse_github_rest(
    raw_source: str, owner: str, repo: str, rest: str
) -> GitHubSourceRef:
    if not rest or rest.lower() == "readme":
        return GitHubSourceRef(
            raw_source=raw_source,
            owner=owner,
            repo=repo,
            resource_kind="readme",
        )

    lowered = rest.lower()
    if lowered.startswith("file/"):
        file_path = rest[5:].strip().strip("/")
        return GitHubSourceRef(
            raw_source=raw_source,
            owner=owner,
            repo=repo,
            resource_kind="file",
            path=file_path,
        )
    if lowered.startswith("issue/"):
        number_text = rest.split("/", 1)[1].strip()
        return GitHubSourceRef(
            raw_source=raw_source,
            owner=owner,
            repo=repo,
            resource_kind="issue",
            number=int(number_text),
        )
    if lowered.startswith("pull/") or lowered.startswith("pulls/"):
        number_text = rest.split("/", 1)[1].strip()
        return GitHubSourceRef(
            raw_source=raw_source,
            owner=owner,
            repo=repo,
            resource_kind="pull",
            number=int(number_text),
        )
    if lowered.startswith("issues/"):
        number_text = rest.split("/", 1)[1].strip()
        return GitHubSourceRef(
            raw_source=raw_source,
            owner=owner,
            repo=repo,
            resource_kind="issue",
            number=int(number_text),
        )
    if lowered.startswith("pulls/"):
        number_text = rest.split("/", 1)[1].strip()
        return GitHubSourceRef(
            raw_source=raw_source,
            owner=owner,
            repo=repo,
            resource_kind="pull",
            number=int(number_text),
        )
    if lowered.startswith("blob/"):
        parts = rest.split("/", 2)
        if len(parts) >= 3:
            return GitHubSourceRef(
                raw_source=raw_source,
                owner=owner,
                repo=repo,
                resource_kind="file",
                path=parts[2],
            )

    return GitHubSourceRef(
        raw_source=raw_source,
        owner=owner,
        repo=repo,
        resource_kind="file",
        path=rest,
    )


class GitHubConnector(BaseConnector):
    """Fetch GitHub README, repository paths, issues, and pull requests."""

    def __init__(
        self,
        *,
        config: GitHubConfig | None = None,
        http_client: _GitHubHTTPClient | None = None,
    ) -> None:
        self._config = config
        base_url = config.base_url if config else "https://api.github.com"
        self._http_client = http_client or _DefaultGitHubHTTPClient(base_url=base_url)
        self._cached_token = ""
        self._cached_token_expires_at = 0.0

    def can_handle(self, source: str) -> bool:
        normalized = str(source or "").strip()
        if not normalized:
            return False
        if normalized.lower().startswith("github://"):
            return True
        parsed = urlparse(normalized)
        host = parsed.netloc.lower()
        return host in _GITHUB_HOSTS or host.endswith(".github.com")

    def get_content(self, source: str) -> SourceDocument:
        ref = parse_github_source(source)
        token = self._resolve_access_token()
        headers = {"Authorization": f"Bearer {token}"} if token else None
        if ref.resource_kind == "readme":
            return self._fetch_readme(ref, headers=headers)
        if ref.resource_kind == "file":
            return self._fetch_file(ref, headers=headers)
        if ref.resource_kind == "issue":
            return self._fetch_issue(ref, headers=headers)
        if ref.resource_kind == "pull":
            return self._fetch_pull_request(ref, headers=headers)
        raise ConnectorUnsupportedSourceError(
            f"Unsupported GitHub resource kind '{ref.resource_kind}'",
            source=source,
        )

    def _resolve_access_token(self) -> str:
        if self._config is None:
            return ""

        if self._config.auth_mode == GitHubAuthMode.pat:
            token = self._config.personal_access_token.strip()
            if not token:
                raise GitHubAuthenticationError(
                    "GitHub personal access token is not configured.",
                    details={"connector": "github"},
                )
            return token

        if self._cached_token and time.time() < self._cached_token_expires_at:
            return self._cached_token

        app_id = self._config.app_id.strip()
        private_key = self._config.private_key.strip()
        installation_id = self._config.installation_id.strip()
        if not app_id or not private_key or not installation_id:
            raise GitHubAuthenticationError(
                "GitHub App credentials (app_id, private_key, installation_id) are required.",
                details={"connector": "github"},
            )

        jwt_token = build_github_app_jwt(app_id=app_id, private_key_pem=private_key)
        response = self._http_client.request(
            "POST",
            f"/app/installations/{installation_id}/access_tokens",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )
        self._raise_for_status(
            response, source=f"github://app/installations/{installation_id}"
        )
        body = extract_mapping(response.json_body) or {}
        token = str(body.get("token") or "").strip()
        if not token:
            raise GitHubAuthenticationError(
                "GitHub App installation token response did not include a token.",
                details={"connector": "github"},
            )
        expires_at_raw = body.get("expires_at")
        expires_at = time.time() + 300
        if isinstance(expires_at_raw, str) and expires_at_raw.strip():
            try:
                expires_at = time.mktime(
                    time.strptime(expires_at_raw.strip(), "%Y-%m-%dT%H:%M:%SZ")
                )
            except ValueError:
                expires_at = time.time() + 300
        self._cached_token = token
        self._cached_token_expires_at = max(time.time() + 30, expires_at - 60)
        return token

    def _fetch_readme(
        self, ref: GitHubSourceRef, *, headers: dict[str, str] | None
    ) -> SourceDocument:
        response = self._http_client.request(
            "GET",
            f"/repos/{ref.owner}/{ref.repo}/readme",
            headers=headers,
        )
        self._raise_for_status(response, source=ref.raw_source)
        body = extract_mapping(response.json_body) or {}
        content = self._decode_content(body)
        title = str(body.get("name") or "README").strip() or "README"
        return SourceDocument(
            source_type=SourceType.github,
            source=ref.raw_source,
            title=title,
            content_markdown=content,
            metadata=SourceMetadata(
                mime_type="text/markdown",
                extra={
                    "connector": "github",
                    "owner": ref.owner,
                    "repo": ref.repo,
                    "resource_kind": "readme",
                },
            ),
        )

    def _fetch_file(
        self, ref: GitHubSourceRef, *, headers: dict[str, str] | None
    ) -> SourceDocument:
        if not ref.path.strip():
            raise ConnectorValidationError(
                "GitHub file source requires a repository path.",
                source=ref.raw_source,
            )
        response = self._http_client.request(
            "GET",
            f"/repos/{ref.owner}/{ref.repo}/contents/{ref.path.strip().lstrip('/')}",
            headers=headers,
        )
        self._raise_for_status(response, source=ref.raw_source)
        body = extract_mapping(response.json_body) or {}
        content = self._decode_content(body)
        title = str(body.get("name") or ref.path.split("/")[-1]).strip() or ref.path
        return SourceDocument(
            source_type=SourceType.github,
            source=ref.raw_source,
            title=title,
            content_markdown=content,
            metadata=SourceMetadata(
                mime_type="text/markdown",
                extra={
                    "connector": "github",
                    "owner": ref.owner,
                    "repo": ref.repo,
                    "resource_kind": "file",
                    "path": ref.path,
                },
            ),
        )

    def _fetch_issue(
        self, ref: GitHubSourceRef, *, headers: dict[str, str] | None
    ) -> SourceDocument:
        number = ref.number
        if number is None:
            raise ConnectorValidationError(
                "GitHub issue source requires an issue number.",
                source=ref.raw_source,
            )
        response = self._http_client.request(
            "GET",
            f"/repos/{ref.owner}/{ref.repo}/issues/{number}",
            headers=headers,
        )
        self._raise_for_status(response, source=ref.raw_source)
        body = extract_mapping(response.json_body) or {}
        title = str(body.get("title") or f"Issue #{number}").strip()
        issue_body = str(body.get("body") or "").strip()
        markdown = f"# Issue #{number}: {title}\n\n{issue_body}".strip()
        return SourceDocument(
            source_type=SourceType.github,
            source=ref.raw_source,
            title=title,
            content_markdown=markdown,
            metadata=SourceMetadata(
                mime_type="text/markdown",
                extra={
                    "connector": "github",
                    "owner": ref.owner,
                    "repo": ref.repo,
                    "resource_kind": "issue",
                    "number": number,
                },
            ),
        )

    def _fetch_pull_request(
        self, ref: GitHubSourceRef, *, headers: dict[str, str] | None
    ) -> SourceDocument:
        number = ref.number
        if number is None:
            raise ConnectorValidationError(
                "GitHub pull request source requires a PR number.",
                source=ref.raw_source,
            )
        response = self._http_client.request(
            "GET",
            f"/repos/{ref.owner}/{ref.repo}/pulls/{number}",
            headers=headers,
        )
        self._raise_for_status(response, source=ref.raw_source)
        body = extract_mapping(response.json_body) or {}
        title = str(body.get("title") or f"PR #{number}").strip()
        pr_body = str(body.get("body") or "").strip()
        markdown = f"# Pull Request #{number}: {title}\n\n{pr_body}".strip()
        return SourceDocument(
            source_type=SourceType.github,
            source=ref.raw_source,
            title=title,
            content_markdown=markdown,
            metadata=SourceMetadata(
                mime_type="text/markdown",
                extra={
                    "connector": "github",
                    "owner": ref.owner,
                    "repo": ref.repo,
                    "resource_kind": "pull",
                    "number": number,
                },
            ),
        )

    def _decode_content(self, body: dict[str, Any]) -> str:
        encoding = str(body.get("encoding") or "").strip().lower()
        content_raw = body.get("content")
        if not isinstance(content_raw, str):
            return ""
        normalized = content_raw.replace("\n", "")
        if encoding == "base64":
            try:
                decoded = base64.b64decode(normalized)
            except (ValueError, TypeError):
                return ""
            return decoded.decode("utf-8", errors="replace")
        return content_raw

    def _raise_for_status(self, response: GitHubHTTPResponse, *, source: str) -> None:
        status = int(response.status_code)
        if status == 401:
            raise GitHubAuthenticationError(
                extract_message(extract_mapping(response.json_body) or {})
                or "GitHub authentication failed.",
                source=source,
            )
        if status == 403:
            raise GitHubPermissionDeniedError(
                extract_message(extract_mapping(response.json_body) or {})
                or "GitHub permission denied.",
                source=source,
            )
        if status == 404:
            raise GitHubResourceNotFoundError(
                extract_message(extract_mapping(response.json_body) or {})
                or "GitHub resource not found.",
                source=source,
            )
        if status >= 400:
            raise ConnectorValidationError(
                extract_message(extract_mapping(response.json_body) or {})
                or f"GitHub API request failed with status {status}.",
                source=source,
            )


def build_github_connector_config(
    *,
    auth_mode: GitHubAuthMode,
    app_id: str,
    private_key: str,
    installation_id: str,
    personal_access_token: str,
    base_url: str,
) -> GitHubConfig:
    return GitHubConfig(
        auth_mode=auth_mode,
        app_id=app_id,
        private_key=private_key,
        installation_id=installation_id,
        personal_access_token=personal_access_token,
        base_url=base_url,
    )
