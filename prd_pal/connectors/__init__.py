"""Source connector abstractions and shared schemas."""

from .auth import ConnectorAuthConfig, ConnectorAuthType
from .base import BaseConnector
from .errors import (
    ConnectorErrorCode,
    ConnectorErrorPayload,
    get_connector_error_payload,
)
from .feishu import FeishuConnector
from .github import GitHubConnector
from .local_file import LocalFileConnector
from .notion import NotionConnector
from .registry import ConnectorRegistry
from .schemas import SourceDocument, SourceMetadata, SourceType
from .url import URLConnector

__all__ = [
    "BaseConnector",
    "ConnectorAuthConfig",
    "ConnectorAuthType",
    "ConnectorErrorCode",
    "ConnectorErrorPayload",
    "ConnectorRegistry",
    "FeishuConnector",
    "GitHubConnector",
    "LocalFileConnector",
    "NotionConnector",
    "SourceDocument",
    "SourceMetadata",
    "SourceType",
    "URLConnector",
    "get_connector_error_payload",
]
