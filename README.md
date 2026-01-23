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
   - Copy the template below, replace course IDs/aliases with your own, then paste the entire YAML into a secret named `CANVAS_CONFIG_YAML`. Set `include_description` to true if you want the assignment description in the notes.

```yaml
canvas:
  base_url: ${CANVAS_BASE_URL}
  courses:
    - id: 12345
      alias: "MATH201"
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

## Troubleshooting
- **Workflow fails with “config/config.yml not found”** – ensure the `CANVAS_CONFIG_YAML` secret is set; the workflow writes the file from that secret each run.
- **SMTP errors or 401/403** – double-check your secrets. Gmail users usually need an app password (2FA required).
- **“Artifact not found” on first run** – harmless; the state file doesn’t exist yet. After a successful run, the download step will succeed.
- **Need dry-run mode** – set `dry_run: true` in the YAML secret to test without sending emails.

Contributions and bug reports are welcome. If you run into issues, open a GitHub issue with the workflow logs (redacting any secrets).
