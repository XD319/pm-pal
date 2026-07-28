# prd-pal v3.1.0 Release Notes

## Highlights

- Formalizes Feishu production submission through signed plugin or message-card callbacks.
- Adds explicit Feishu H5 run context propagation for status, result, report, artifact preview, clarification, revision, roadmap, and SSE progress APIs.
- Adds Feishu OpenAPI notification delivery alongside the existing webhook sender.
- Tightens release governance for CI, package versioning, documentation, and Docker Compose config.

## Feishu Production Hardening

- `MARRDP_FEISHU_SIGNATURE_DISABLED=false` now requires `MARRDP_FEISHU_WEBHOOK_SECRET`; missing configuration returns `feishu_signature_not_configured`.
- Browser H5 no longer acts as a production direct-submit client for `/api/feishu/submit`.
- Feishu-origin runs require matching `open_id` and `tenant_key`; the backend no longer uses `Referer` as an identity source.
- API key auth can be bypassed only for run-level APIs with validated Feishu context. Global management APIs still require API credentials when auth is enabled.

## Notifications

- New configuration:
  - `MARRDP_FEISHU_NOTIFICATION_DRY_RUN`
  - `MARRDP_FEISHU_NOTIFICATION_CHANNELS`
  - `MARRDP_FEISHU_NOTIFICATION_RECEIVE_ID_TYPE`
  - `MARRDP_FEISHU_NOTIFICATION_DEFAULT_RECEIVE_ID`
  - `MARRDP_FEISHU_NOTIFICATION_WEBHOOK_URL`
  - `MARRDP_PUBLIC_BASE_URL`
- OpenAPI delivery uses tenant access token and sends interactive message cards.
- `both` records OpenAPI and webhook attempts separately; one channel does not silently fall back to the other.

## Upgrade Notes

- Set `MARRDP_PUBLIC_BASE_URL` before real Feishu card notifications.
- Keep `MARRDP_FEISHU_NOTIFICATION_DRY_RUN=true` only for local joint debugging.
- Run release validation with `pytest -q`, `npm test -- --run`, and `npm run build` (see `.github/workflows/ci.yml`).
