# Callback Configuration

This guide covers webhook URLs, signature verification, and environment variables for Feishu, Notion, and GitHub connector callbacks.

For project-space setup and review APIs, see [project-space.md](./project-space.md) and [v2-api.md](./v2-api.md).

## Environment naming

Runtime code currently reads `MARRDP_*` variables. The preferred public names going forward are `PRD_PAL_*` equivalents documented below. Set either name in `.env` where noted; when both are present, `MARRDP_*` remains the active reader until dual-read helpers land.

## Feishu

### Callback URLs

| Purpose | Method | Path |
|---------|--------|------|
| Event subscription / challenge | POST | `/api/feishu/events` |
| Review submit (plugin/card) | POST | `/api/feishu/submit` |
| Clarification answer | POST | `/api/feishu/clarification` |

Public base URL example: `https://<your-domain>/api/feishu/events`

### Signature modes

Feishu verification supports two modes controlled by whether an encrypt key is configured:

1. **Webhook secret (default)** — HMAC-SHA256 over `timestamp + body`, Base64-encoded signature.
2. **Encrypt key mode** — SHA256 over `timestamp + nonce + encrypt_key + body` when `MARRDP_FEISHU_ENCRYPT_KEY` is set.

Required headers (Lark or Feishu alias):

- `X-Lark-Request-Timestamp` / `X-Feishu-Request-Timestamp`
- `X-Lark-Signature` / `X-Feishu-Signature`
- `X-Lark-Request-Nonce` / `X-Feishu-Request-Nonce` (encrypt key mode only)

### Environment variables

| Active (`MARRDP_*`) | Preferred (`PRD_PAL_*`) | Notes |
|---------------------|-------------------------|-------|
| `MARRDP_FEISHU_SIGNATURE_DISABLED` | `PRD_PAL_FEISHU_SIGNATURE_DISABLED` | `true` for local mock |
| `MARRDP_FEISHU_WEBHOOK_SECRET` | `PRD_PAL_FEISHU_WEBHOOK_SECRET` | Required when signatures enabled (non-encrypt mode) |
| `MARRDP_FEISHU_ENCRYPT_KEY` | `PRD_PAL_FEISHU_ENCRYPT_KEY` | Enables encrypt-key signature path |
| `MARRDP_FEISHU_SIGNATURE_TOLERANCE_SEC` | `PRD_PAL_FEISHU_SIGNATURE_TOLERANCE_SEC` | Default `300` |
| `MARRDP_FEISHU_APP_ID` | `PRD_PAL_FEISHU_APP_ID` | App credentials |
| `MARRDP_FEISHU_APP_SECRET` | `PRD_PAL_FEISHU_APP_SECRET` | App credentials |
| `MARRDP_PUBLIC_BASE_URL` | `PRD_PAL_PUBLIC_BASE_URL` | Used in notification cards |

Local development:

```dotenv
MARRDP_FEISHU_SIGNATURE_DISABLED=true
```

Production:

```dotenv
MARRDP_FEISHU_SIGNATURE_DISABLED=false
MARRDP_FEISHU_WEBHOOK_SECRET=your-webhook-secret
# Optional encrypt-key mode:
# MARRDP_FEISHU_ENCRYPT_KEY=your-encrypt-key
MARRDP_PUBLIC_BASE_URL=https://your-domain
```

If signatures are enabled without a webhook secret (and no encrypt key), callbacks return `detail.code = feishu_signature_not_configured`.

## Notion

### Callback URL

| Purpose | Method | Path |
|---------|--------|------|
| Webhook events / verification | POST | `/api/notion/events` |

Example: `https://<your-domain>/api/notion/events`

### Signature verification

Notion sends `X-Notion-Signature` as `sha256=<hex>` HMAC over the raw request body.

Verification token handshake: initial subscription payloads may include `verification_token`; the handler echoes it without requiring a signature on that first message.

Project-scoped signing secrets can also be stored per project in the project-space database; the router resolves secrets by page ID when available.

### Environment variables

| Active (`MARRDP_*`) | Preferred (`PRD_PAL_*`) | Notes |
|---------------------|-------------------------|-------|
| `MARRDP_NOTION_SIGNATURE_DISABLED` | `PRD_PAL_NOTION_SIGNATURE_DISABLED` | `true` for local mock |
| `MARRDP_NOTION_SIGNING_SECRET` | `PRD_PAL_NOTION_SIGNING_SECRET` | Webhook signing secret |
| `MARRDP_NOTION_VERIFICATION_TOKEN` | `PRD_PAL_NOTION_VERIFICATION_TOKEN` | Legacy fallback for signing secret |
| `MARRDP_NOTION_TOKEN` | `PRD_PAL_NOTION_TOKEN` | Integration token for page fetch |

Local development:

```dotenv
MARRDP_NOTION_SIGNATURE_DISABLED=true
MARRDP_NOTION_TOKEN=secret_...
```

Production:

```dotenv
MARRDP_NOTION_SIGNATURE_DISABLED=false
MARRDP_NOTION_SIGNING_SECRET=your-notion-signing-secret
MARRDP_NOTION_TOKEN=secret_...
```

## GitHub

### Callback URL

| Purpose | Method | Path |
|---------|--------|------|
| Repository webhook events | POST | `/api/github/events` |

Example: `https://<your-domain>/api/github/events`

Configure this URL in your GitHub App or repository webhook settings. Subscribe to content events relevant to your mapped repos (push, issues, pull requests, etc.).

### Signature verification

GitHub sends `X-Hub-Signature-256` as `sha256=<hex>` HMAC-SHA256 over the raw request body.

Per-project webhook secrets stored in project-space config take precedence over the global env secret when resolving verification settings.

### Auth modes (App vs PAT)

GitHub connector auth is configured per project:

- **GitHub App** — `app_id`, installation ID, private key, optional webhook secret
- **PAT** — personal access token for simpler setups

Global env fallbacks:

| Active (`MARRDP_*`) | Preferred (`PRD_PAL_*`) | Notes |
|---------------------|-------------------------|-------|
| `MARRDP_GITHUB_SIGNATURE_DISABLED` | `PRD_PAL_GITHUB_SIGNATURE_DISABLED` | `true` for local mock |
| `MARRDP_GITHUB_WEBHOOK_SECRET` | `PRD_PAL_GITHUB_WEBHOOK_SECRET` | Global webhook secret |
| `MARRDP_GITHUB_APP_ID` | `PRD_PAL_GITHUB_APP_ID` | App mode |
| `MARRDP_GITHUB_PRIVATE_KEY` | `PRD_PAL_GITHUB_PRIVATE_KEY` | PEM private key |
| `MARRDP_GITHUB_INSTALLATION_ID` | `PRD_PAL_GITHUB_INSTALLATION_ID` | App installation |
| `MARRDP_GITHUB_TOKEN` | `PRD_PAL_GITHUB_TOKEN` | PAT fallback (`GITHUB_TOKEN` also accepted) |
| `MARRDP_GITHUB_AUTH_MODE` | `PRD_PAL_GITHUB_AUTH_MODE` | `app` or `pat` |
| `MARRDP_GITHUB_API_BASE_URL` | `PRD_PAL_GITHUB_API_BASE_URL` | Enterprise GitHub base URL |

Local development:

```dotenv
MARRDP_GITHUB_SIGNATURE_DISABLED=true
MARRDP_GITHUB_TOKEN=ghp_...
```

Production (App):

```dotenv
MARRDP_GITHUB_SIGNATURE_DISABLED=false
MARRDP_GITHUB_WEBHOOK_SECRET=your-github-webhook-secret
MARRDP_GITHUB_AUTH_MODE=app
MARRDP_GITHUB_APP_ID=123456
MARRDP_GITHUB_INSTALLATION_ID=789012
MARRDP_GITHUB_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----\n..."
```

## Shared API security

For all callback routes exposed on a shared host, also configure:

```dotenv
MARRDP_API_AUTH_DISABLED=false
MARRDP_API_KEY=your-api-key
# PRD_PAL_API_KEY=your-api-key
```

Note: Feishu run-level H5 requests may omit API keys when valid Feishu context is present on project-scoped review routes. Webhook ingress routes verify connector signatures instead of API keys.

## Smoke-test checklist

1. `GET /health` returns `"service": "prd-pal"`
2. Feishu `url_verification` challenge on `/api/feishu/events`
3. One signed `/api/feishu/submit` or project-scoped review from a Feishu source
4. Notion verification handshake on `/api/notion/events`
5. GitHub `ping` or push event on `/api/github/events` with valid `X-Hub-Signature-256`
6. Confirm connector sync tasks appear in project timeline after webhook delivery
