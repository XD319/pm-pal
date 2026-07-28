# Project Space (self-hosted)

PRD Pal now starts in a project space. Create a project, add PRD text or a Feishu/document link, then start review from the selected source. Each run remains available through its existing URL and is listed under the project.

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
