"""Parse GitHub webhook events and enqueue connector sync tasks."""
from __future__ import annotations

import fnmatch
import json
from typing import Any, Callable

from prd_pal.connectors.sync import (
    ConnectorSyncStore,
    build_sync_idempotency_key,
    enqueue_sync_task,
    is_event_processed,
    mark_event_processed,
)

from .config_store import GitHubConfigStore, GitHubRepoMapping

ISSUE_ACTIONS = {"opened", "edited", "reopened"}
PULL_REQUEST_ACTIONS = {"opened", "edited", "reopened", "synchronize"}


def decode_request_body(body: bytes) -> dict[str, Any]:
    return json.loads(body.decode("utf-8") or "{}")


def extract_delivery_id(headers: dict[str, str]) -> str:
    lowered = {str(key).lower(): str(value) for key, value in headers.items()}
    return str(lowered.get("x-github-delivery", "") or "").strip()


def extract_event_type(headers: dict[str, str]) -> str:
    lowered = {str(key).lower(): str(value) for key, value in headers.items()}
    return str(lowered.get("x-github-event", "") or "").strip().lower()


def extract_repo_full_name(payload: dict[str, Any]) -> str:
    repository = payload.get("repository")
    if isinstance(repository, dict):
        full_name = str(repository.get("full_name") or "").strip()
        if full_name:
            return full_name
        owner = repository.get("owner")
        name = str(repository.get("name") or "").strip()
        if isinstance(owner, dict):
            login = str(owner.get("login") or "").strip()
            if login and name:
                return f"{login}/{name}"
    return ""


def build_github_source_url(
    owner: str,
    repo: str,
    *,
    kind: str,
    path: str = "",
    number: int | None = None,
) -> str:
    normalized_owner = str(owner or "").strip()
    normalized_repo = str(repo or "").strip()
    base = f"github://{normalized_owner}/{normalized_repo}"
    normalized_kind = str(kind or "").strip().lower()
    if normalized_kind == "readme":
        return f"{base}/readme"
    if normalized_kind == "file" and path.strip():
        return f"{base}/file/{path.strip().lstrip('/')}"
    if normalized_kind == "issue" and number is not None:
        return f"{base}/issue/{int(number)}"
    if normalized_kind == "pull" and number is not None:
        return f"{base}/pull/{int(number)}"
    return base


def path_matches_globs(file_path: str, globs: tuple[str, ...]) -> bool:
    normalized = str(file_path or "").strip().lstrip("/")
    if not normalized:
        return False
    if not globs:
        return True
    for pattern in globs:
        normalized_pattern = str(pattern or "").strip().lstrip("/")
        if not normalized_pattern:
            continue
        if fnmatch.fnmatch(normalized, normalized_pattern):
            return True
        if fnmatch.fnmatch(normalized, normalized_pattern.replace("**", "*")):
            return True
    return False


def collect_push_paths(payload: dict[str, Any]) -> set[str]:
    paths: set[str] = set()
    commits = payload.get("commits")
    if not isinstance(commits, list):
        return paths
    for commit in commits:
        if not isinstance(commit, dict):
            continue
        for key in ("added", "modified", "removed"):
            items = commit.get(key)
            if not isinstance(items, list):
                continue
            for item in items:
                normalized = str(item or "").strip()
                if normalized:
                    paths.add(normalized)
    return paths


def _enqueue_resource_sync(
    *,
    sync_store: ConnectorSyncStore,
    project_id: str,
    mapping: GitHubRepoMapping,
    source_url: str,
    title: str,
    event_id: str,
    resource: str,
    new_id: Callable[[str], str],
    now: Callable[[], str],
) -> dict[str, Any]:
    sync_payload = {
        "trigger": "webhook",
        "event_id": event_id,
        "source_url": source_url,
        "title": title,
        "owner": mapping.owner,
        "repo": mapping.repo,
    }
    return enqueue_sync_task(
        sync_store,
        project_id=project_id,
        provider="github",
        payload=sync_payload,
        idempotency_key=build_sync_idempotency_key(
            project_id,
            "github",
            resource=resource,
            suffix=event_id or "webhook",
        ),
        new_id=new_id,
        now=now,
    )


def handle_github_event_payload(
    payload: dict[str, Any],
    *,
    headers: dict[str, str],
    sync_store: ConnectorSyncStore,
    config_store: GitHubConfigStore,
    new_id: Callable[[str], str],
    now: Callable[[], str],
) -> dict[str, Any]:
    event_type = extract_event_type(headers)
    event_id = extract_delivery_id(headers)

    if event_type == "ping":
        return {"kind": "ping", "zen": payload.get("zen", "")}

    if event_id and is_event_processed(sync_store, provider="github", event_id=event_id):
        return {"kind": "duplicate", "event_id": event_id}

    full_name = extract_repo_full_name(payload)
    match = config_store.find_project_for_repo(full_name)
    if match is None:
        return {
            "kind": "ignored",
            "reason": "unmapped_repo",
            "repository": full_name,
            "event_type": event_type,
        }

    project_id, mapping = match
    enqueued: list[dict[str, Any]] = []

    if event_type == "push":
        changed_paths = collect_push_paths(payload)
        for file_path in sorted(changed_paths):
            if file_path.upper() == "README.MD" and mapping.include_readme:
                source_url = build_github_source_url(
                    mapping.owner, mapping.repo, kind="readme"
                )
                task = _enqueue_resource_sync(
                    sync_store=sync_store,
                    project_id=project_id,
                    mapping=mapping,
                    source_url=source_url,
                    title=f"{mapping.title} README",
                    event_id=event_id,
                    resource=f"readme:{event_id}",
                    new_id=new_id,
                    now=now,
                )
                enqueued.append({"resource": "readme", "task_id": task["id"]})
                continue
            if path_matches_globs(file_path, mapping.paths):
                source_url = build_github_source_url(
                    mapping.owner,
                    mapping.repo,
                    kind="file",
                    path=file_path,
                )
                task = _enqueue_resource_sync(
                    sync_store=sync_store,
                    project_id=project_id,
                    mapping=mapping,
                    source_url=source_url,
                    title=f"{mapping.title}: {file_path}",
                    event_id=event_id,
                    resource=f"file:{file_path}:{event_id}",
                    new_id=new_id,
                    now=now,
                )
                enqueued.append({"resource": file_path, "task_id": task["id"]})

    elif event_type == "issues" and mapping.sync_issues:
        action = str(payload.get("action") or "").strip().lower()
        if action in ISSUE_ACTIONS:
            issue = payload.get("issue")
            if isinstance(issue, dict):
                number = issue.get("number")
                title = str(issue.get("title") or f"Issue {number}").strip()
                source_url = build_github_source_url(
                    mapping.owner,
                    mapping.repo,
                    kind="issue",
                    number=int(number) if number is not None else None,
                )
                task = _enqueue_resource_sync(
                    sync_store=sync_store,
                    project_id=project_id,
                    mapping=mapping,
                    source_url=source_url,
                    title=f"{mapping.title}: {title}",
                    event_id=event_id,
                    resource=f"issue:{number}:{event_id}",
                    new_id=new_id,
                    now=now,
                )
                enqueued.append({"resource": f"issue:{number}", "task_id": task["id"]})

    elif event_type == "pull_request" and mapping.sync_pull_requests:
        action = str(payload.get("action") or "").strip().lower()
        if action in PULL_REQUEST_ACTIONS:
            pull_request = payload.get("pull_request")
            if isinstance(pull_request, dict):
                number = pull_request.get("number")
                title = str(pull_request.get("title") or f"PR {number}").strip()
                source_url = build_github_source_url(
                    mapping.owner,
                    mapping.repo,
                    kind="pull",
                    number=int(number) if number is not None else None,
                )
                task = _enqueue_resource_sync(
                    sync_store=sync_store,
                    project_id=project_id,
                    mapping=mapping,
                    source_url=source_url,
                    title=f"{mapping.title}: {title}",
                    event_id=event_id,
                    resource=f"pull:{number}:{event_id}",
                    new_id=new_id,
                    now=now,
                )
                enqueued.append({"resource": f"pull:{number}", "task_id": task["id"]})

    else:
        return {
            "kind": "ignored",
            "event_type": event_type,
            "repository": full_name,
        }

    if not enqueued:
        return {
            "kind": "ignored",
            "event_type": event_type,
            "repository": full_name,
            "reason": "no_matching_resources",
        }

    if event_id:
        mark_event_processed(
            sync_store,
            provider="github",
            event_id=event_id,
            project_id=project_id,
            now=now,
        )

    return {
        "kind": "sync_enqueued",
        "project_id": project_id,
        "repository": full_name,
        "event_type": event_type,
        "tasks": enqueued,
    }
