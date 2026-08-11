from __future__ import annotations

import pytest

from pm_pal.connectors import (
    ConnectorAuthConfig,
    ConnectorAuthType,
    get_connector_error_payload,
)
from pm_pal.connectors.errors import (
    ConnectorAuthError,
    ConnectorErrorCode,
    ConnectorNetworkError,
    ConnectorUnsupportedSourceError,
)


@pytest.mark.parametrize(
    ("auth_config", "expected_message"),
    [
        (
            {"auth_type": ConnectorAuthType.none, "token": "secret"},
            "auth_type 'none' cannot include credentials",
        ),
        (
            {"auth_type": ConnectorAuthType.bearer_token},
            "auth_type 'bearer_token' requires a token",
        ),
        (
            {"auth_type": ConnectorAuthType.basic, "username": "demo"},
            "auth_type 'basic' requires both username and password",
        ),
        (
            {
                "auth_type": ConnectorAuthType.oauth_client_credentials,
                "client_id": "client-id",
            },
            "auth_type 'oauth_client_credentials' requires both client_id and client_secret",
        ),
    ],
)
def test_connector_auth_config_validation(
    auth_config: dict[str, object], expected_message: str
) -> None:
    with pytest.raises(ValueError, match=expected_message):
        ConnectorAuthConfig(**auth_config)


def test_connector_auth_config_accepts_valid_oauth_credentials() -> None:
    config = ConnectorAuthConfig(
        auth_type=ConnectorAuthType.oauth_client_credentials,
        client_id="client-id",
        client_secret="client-secret",
        scopes=["docs:read"],
    )

    assert config.auth_type == ConnectorAuthType.oauth_client_credentials
    assert config.client_id == "client-id"
    assert config.client_secret == "client-secret"
    assert config.scopes == ["docs:read"]


@pytest.mark.parametrize(
    ("exc", "expected_code", "expected_retryable"),
    [
        (
            ConnectorUnsupportedSourceError(
                "Unsupported source",
                source="ftp://example.com/spec",
                details={"connector": "url"},
            ),
            ConnectorErrorCode.unsupported_source,
            False,
        ),
        (
            ConnectorAuthError(
                "Missing credentials",
                source="feishu://wiki/team/doc",
                details={"connector": "feishu"},
            ),
            ConnectorErrorCode.authentication_failed,
            False,
        ),
        (
            ConnectorNetworkError(
                "Temporary outage",
                source="https://openai.com/spec",
                details={"connector": "url"},
            ),
            ConnectorErrorCode.network_unavailable,
            True,
        ),
    ],
)
def test_connector_errors_expose_common_payload_shape(
    exc: Exception, expected_code: ConnectorErrorCode, expected_retryable: bool
) -> None:
    payload = get_connector_error_payload(exc)

    assert payload is not None
    assert payload.model_dump()["code"] == expected_code.value
    assert payload.message == str(exc)
    assert payload.source
    assert payload.retryable is expected_retryable
    assert payload.details["connector"] in {"feishu", "url"}
