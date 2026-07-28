# Project Space (self-hosted)

PRD Pal now starts in a project space. Create a project, add PRD text or a Feishu/document link, then start review from the selected source. Each run is listed under the project and accessed through project-scoped review APIs.

## Review APIs

Legacy global routes such as `POST /api/review`, `GET /api/runs`, and `GET /api/report/{run_id}` were removed in Phase 2. Use project-scoped endpoints instead:

- `POST /api/projects/{project_id}/reviews` — start a review from a project source
- `GET /api/projects/{project_id}/reviews/{run_id}` — poll status
- `GET /api/projects/{project_id}/reviews/{run_id}/result` — fetch structured results
- `GET /api/projects/{project_id}/reviews/{run_id}/report?format=md|json|html|csv` — download reports

The `/run/{run_id}` frontend path still works via redirect to the linked project review page.

## Configure your model provider

1. Generate an encryption key and put it in `.env`:

   ```powershell
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

   Set the result as `MARRDP_SECRETS_MASTER_KEY`.
2. Open **Model connections** in the app.
3. Select a provider, enter its API key and optional base URL, then save and validate it.
4. Create a model preset and choose it for a project.

Secrets are encrypted in `data/project_space.sqlite3`, are never returned by the API, and must not be committed to Git. The current instance owner pays their provider directly. Ollama can be configured without an API key.

Provider entries are shown even when their optional Python package is absent; the UI will state the package to install before validation.

## Connectors and realtime sync

Supported source connectors: Feishu, Notion, GitHub, URL, local file. Webhook callbacks sync external changes into project sources:

- Feishu: `/api/feishu/events`, `/api/feishu/submit`
- Notion: `/api/notion/events`
- GitHub: `/api/github/events`

See [callback-config.md](./callback-config.md) for signature and encrypt settings.
