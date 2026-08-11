# Callback Configuration

This guide covers webhook URLs, signature verification, and environment variables for Feishu, Notion, and GitHub connector callbacks.

For project-space setup and review APIs, see [project-space.md](./project-space.md) and [v2-api.md](./v2-api.md).

## Environment naming

| Area | Runtime reader today | Notes |
|------|----------------------|-------|
| API auth / rate limit | **Dual-read**: `PM_PAL_*` preferred, `MARRDP_*` fallback | See `.env.example` |
| Host / port | **Dual-read**: `PM_PAL_HOST` / `PM_PAL_PORT` | Used by `main.py` |
| Secrets master key | **Dual-read**: `PM_PAL_SECRETS_MASTER_KEY` | Provider key encryption |
| Data directory | `PM_PAL_DATA_DIR` only | Default `data` |
| Feishu / Notion / GitHub connector & webhooks | **`MARRDP_*` only** | Set `MARRDP_*` in `.env` today (no `PM_PAL_*` dual-read yet) |

## Feishu

### Callback URLs

| Purpose | Method | Path | Status |
|---------|--------|------|--------|
| Event subscription / challenge | POST | `/api/feishu/events` | Active |
| Clarification answer | POST | `/api/feishu/clarification` | Active |
| Workspace helpers | GET/POST | `/api/feishu/workspaces/...` | Active |
| Legacy review submit | POST | `/api/feishu/submit` | **410 Gone** — use `POST /api/projects/{project_id}/reviews` |

Public base URL example: `https://<your-domain>/api/feishu/events`

### Signature modes

Feishu verification supports two modes controlled by whether an encrypt key is configured:

1. **Webhook secret (default)** — HMAC-SHA256 over `timestamp + body`, Base64-encoded signature.
2. **Encrypt key mode** — SHA256 over `timestamp + nonce + encrypt_key + body` when `MARRDP_FEISHU_ENCRYPT_KEY` is set.

Required headers (Lark or Feishu alias):

- `X-Lark-Signature` / `X-Feishu-Signature`
- `X-Lark-Request-Timestamp` / `X-Feishu-Request-Timestamp`
- Optional nonce headers for encrypt-key mode

### Environment variables

| Active (`MARRDP_*`) | Notes |
|---------------------|-------|
| `MARRDP_FEISHU_SIGNATURE_DISABLED` | `true` for local mock |
| `MARRDP_FEISHU_WEBHOOK_SECRET` | Required when signatures enabled (non-encrypt mode) |
| `MARRDP_FEISHU_ENCRYPT_KEY` | Enables encrypt-key signature path |
| `MARRDP_FEISHU_SIGNATURE_TOLERANCE_SEC` | Default `300` |
| `MARRDP_FEISHU_APP_ID` | App credentials |
| `MARRDP_FEISHU_APP_SECRET` | App credentials |
| `MARRDP_PUBLIC_BASE_URL` | Used in notification cards |

Local mock:

```dotenv
MARRDP_FEISHU_SIGNATURE_DISABLED=true
```

Production-style:

```dotenv
MARRDP_FEISHU_SIGNATURE_DISABLED=false
MARRDP_FEISHU_WEBHOOK_SECRET=your-webhook-secret
# MARRDP_FEISHU_ENCRYPT_KEY=your-encrypt-key
MARRDP_PUBLIC_BASE_URL=https://your-domain
```

When signatures are enabled and `MARRDP_FEISHU_WEBHOOK_SECRET` is empty (and encrypt key is not used), callbacks return a controlled configuration error (`feishu_signature_not_configured`).

## Notion

### Callback URL

| Purpose | Method | Path |
|---------|--------|------|
| Webhook / verification | POST | `/api/notion/events` |

### Environment variables

| Active (`MARRDP_*`) | Notes |
|---------------------|-------|
| `MARRDP_NOTION_SIGNATURE_DISABLED` | `true` for local mock |
| `MARRDP_NOTION_SIGNING_SECRET` | Webhook signing secret |
| `MARRDP_NOTION_VERIFICATION_TOKEN` | Legacy fallback for signing secret |
| `MARRDP_NOTION_TOKEN` | Integration token for page fetch |

Local mock:

```dotenv
MARRDP_NOTION_SIGNATURE_DISABLED=true
MARRDP_NOTION_TOKEN=secret_...
```

Production-style:

```dotenv
MARRDP_NOTION_SIGNATURE_DISABLED=false
MARRDP_NOTION_SIGNING_SECRET=your-notion-signing-secret
MARRDP_NOTION_TOKEN=secret_...
```

## GitHub

### Callback URL

| Purpose | Method | Path |
|---------|--------|------|
| Webhook events | POST | `/api/github/events` |

### Environment variables

| Active (`MARRDP_*`) | Notes |
|---------------------|-------|
| `MARRDP_GITHUB_SIGNATURE_DISABLED` | `true` for local mock |
| `MARRDP_GITHUB_WEBHOOK_SECRET` | Global webhook secret |
| `MARRDP_GITHUB_APP_ID` | App mode |
| `MARRDP_GITHUB_PRIVATE_KEY` | PEM private key |
| `MARRDP_GITHUB_INSTALLATION_ID` | App installation |
| `MARRDP_GITHUB_TOKEN` | PAT fallback (`GITHUB_TOKEN` also accepted) |
| `MARRDP_GITHUB_AUTH_MODE` | `app` or `pat` |
| `MARRDP_GITHUB_API_BASE_URL` | Enterprise GitHub base URL |

Local mock:

```dotenv
MARRDP_GITHUB_SIGNATURE_DISABLED=true
MARRDP_GITHUB_TOKEN=ghp_...
```

Production-style:

```dotenv
MARRDP_GITHUB_SIGNATURE_DISABLED=false
MARRDP_GITHUB_WEBHOOK_SECRET=your-github-webhook-secret
MARRDP_GITHUB_AUTH_MODE=app
MARRDP_GITHUB_APP_ID=123456
MARRDP_GITHUB_INSTALLATION_ID=789012
MARRDP_GITHUB_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----\n..."
```

## Shared API security

For all non-webhook routes on a shared host, configure:

```dotenv
PM_PAL_API_AUTH_DISABLED=false
PM_PAL_API_KEY=your-api-key
# MARRDP_API_AUTH_DISABLED / MARRDP_API_KEY also accepted
```

Note: Feishu run-level H5 requests may omit API keys when valid Feishu context is present on project-scoped review routes. Webhook ingress routes (`/api/{feishu,github,notion}/events`) verify connector signatures instead of API keys.

## Smoke-test checklist

1. `GET /health` returns `"service": "pm-pal"` (or equivalent ok payload)
2. Feishu `url_verification` challenge on `/api/feishu/events`
3. One project-scoped review from a Feishu-backed source (`POST /api/projects/{project_id}/reviews`)
4. Notion verification handshake on `/api/notion/events`
5. GitHub `ping` or push event on `/api/github/events` with valid `X-Hub-Signature-256`
6. Confirm connector sync tasks appear in project timeline after webhook delivery
