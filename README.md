# Canvas → Things Mail Bridge

Poll Canvas LMS for assignment updates and email them into Things 3 via Mail to Things. Everything runs inside GitHub Actions.

## Quick Start
1. **Fork this repository** so you have your own copy.
2. **Enable GitHub Actions** (Actions tab → “I understand my workflows, go ahead and enable them”).
3. **Add repository secrets** (Settings → Secrets and variables → Actions → “New repository secret”):
   - `CANVAS_BASE_URL` – e.g. `https://school.instructure.com`
   - `CANVAS_TOKEN` – Canvas API token with assignment access
   - `THINGS_EMAIL` – your Mail to Things address (e.g. `name@things.email`)
   - `SMTP_HOST` – outbound SMTP server (e.g. `smtp.gmail.com`)
   - `SMTP_PORT` – usually `587`
   - `SMTP_USER` – email/username for SMTP auth
   - `SMTP_PASS` – SMTP password or app-specific password
4. **Add your Canvas config as a single secret**:
   - Copy the template below, replace course IDs/aliases with your own, then paste the entire YAML into a secret named `CANVAS_CONFIG_YAML`.

```yaml
canvas:
  base_url: ${CANVAS_BASE_URL}
  courses:
    - id: 12345        # REQUIRED: replace with your Canvas course ID
      alias: "MATH201" # REQUIRED: short label shown in Things
      include_description: true
    - id: 67890
      alias: "HIST101"
      include_description: false

email:
  from_name: "Canvas Bot"
  subject_template: "{course_alias} – {title}"
  include_description: true
  max_description_chars: 500

run:
  timezone: "America/New_York"
  dry_run: false
  state_file: "data/state.json"
```

5. **Trigger the workflow** (Actions tab → “Canvas Things Poll” → “Run workflow”).
6. After the first successful run, the workflow will re-run automatically every 2 hours.

## How It Works
1. **Config + secrets** – GitHub Actions writes `config/config.yml` from `CANVAS_CONFIG_YAML` and injects your secrets as environment variables.
2. **Canvas client** – Fetches assignments via `/api/v1/courses/{course_id}/assignments`, handling pagination and rate-limit headers.
3. **State store** – Keeps a JSON file of assignment fingerprints (`course_id:assignment_id:updated_at`). Stored as an artifact between runs.
4. **Notifier** – Formats each assignment into a plain-text email and delivers it via SMTP. Dry-run mode logs instead of sending.
5. **CLI** – Orchestrates the whole cycle: load config, fetch assignments, dedupe via state, send notifications, update state.
6. **Workflow** – Runs on `ubuntu-latest`, installs dependencies, downloads the previous `data/state.json`, executes the CLI, uploads the updated state.

## Local Testing (Optional)
If you want to run everything locally before trusting GitHub Actions:
1. Install Python 3.11+ and create a virtualenv.
2. `pip install -e .[dev]`
3. Copy `config/config.example.yml` → `config/config.yml`, edit courses, and export the same env vars you added as secrets.
4. `python -m canvas_things.main --per-page 50`
5. Run tests via `pytest -q` (currently 13 tests covering config, state, Canvas client, notifier, and CLI).

## Troubleshooting
- **Workflow fails with “config/config.yml not found”** – ensure the `CANVAS_CONFIG_YAML` secret is set; the workflow writes the file from that secret each run.
- **SMTP errors or 401/403** – double-check your secrets. Gmail users usually need an app password (2FA required).
- **“Artifact not found” on first run** – harmless; the state file doesn’t exist yet. After a successful run, the download step will succeed.
- **Need dry-run mode** – set `dry_run: true` in the YAML secret to test without sending emails.

## Roadmap
- [x] Config/state loader
- [x] Canvas client + tests
- [x] Notifier + SMTP transport
- [x] CLI & GitHub Actions workflow
- [ ] README refinements & friendly docs (this page!)
- [ ] Optional: support Canvas To-Do items / grade updates

Contributions and bug reports are welcome. If you run into issues, open a GitHub issue with the workflow logs (redacting any secrets). Happy automation! ✨
