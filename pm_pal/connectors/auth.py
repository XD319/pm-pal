"""Shared authentication config models for authenticated source connectors."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import Field, model_validator

from pm_pal.schemas.base import AgentSchemaModel


class ConnectorAuthType(str, Enum):
    """Supported connector authentication strategies."""

    none = "none"
    bearer_token = "bearer_token"
    basic = "basic"
    api_key = "api_key"
    oauth_client_credentials = "oauth_client_credentials"


class ConnectorAuthConfig(AgentSchemaModel):
    """Minimal shared auth config for private or authenticated connectors."""

    auth_type: ConnectorAuthType = ConnectorAuthType.none
    header_name: str = "Authorization"
    token: str = ""
    username: str = ""
    password: str = ""
    client_id: str = ""
    client_secret: str = ""
    scopes: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_auth_config(self) -> ConnectorAuthConfig:
        if self.auth_type == ConnectorAuthType.none:
            if any(
                (
                    self.token,
                    self.username,
                    self.password,
                    self.client_id,
                    self.client_secret,
                    self.scopes,
                )
            ):
                raise ValueError("auth_type 'none' cannot include credentials")
            return self

        if (
            self.auth_type
            in {ConnectorAuthType.bearer_token, ConnectorAuthType.api_key}
            and not self.token
        ):
            raise ValueError(f"auth_type '{self.auth_type}' requires a token")

        if self.auth_type == ConnectorAuthType.basic and (
            not self.username or not self.password
        ):
            raise ValueError("auth_type 'basic' requires both username and password")

        if self.auth_type == ConnectorAuthType.oauth_client_credentials and (
            not self.client_id or not self.client_secret
        ):
            raise ValueError(
                "auth_type 'oauth_client_credentials' requires both client_id and client_secret"
            )

        if (
            self.auth_type == ConnectorAuthType.api_key
            and not str(self.header_name or "").strip()
        ):
            raise ValueError("auth_type 'api_key' requires a non-empty header_name")

        return self
