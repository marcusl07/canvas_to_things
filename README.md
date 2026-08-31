# Canvas → Things Mail Bridge

This tool automatically checks your Canvas courses for new assignments and emails them directly to your **Things 3** "Mail to Things" address. It runs entirely in the cloud (GitHub Actions), so you don't need to keep your computer on.

It also includes an optional macOS-only local companion that reads your local Things database and syncs deadlines back into existing Things tasks using note markers.

## What it does
- **Checks Canvas** every 2 hours for updates.
- **Sends tasks** to your Things Inbox with the assignment name, due date, and description.
- **Avoids duplicates**: It remembers what it has already sent.
- **Respects Limits**: It sends a maximum of **95 emails per day** to stay safely under the Things Cloud limit (100/day). If you have more assignments than that, it queues them up for the next day automatically.

---

## Setup Guide

### 1. Get your own copy
Click **Fork** in the top-right corner of this page to create your own copy of this repository.

### 2. Enable updates
Go to the **Actions** tab in your new repository and click the big green button to enable workflows. Then manually run the workflow once (Actions → Canvas Things Poll → Run workflow) to activate the schedule.

### 3. Add your Secrets
Go to **Settings** → **Secrets and variables** → **Actions** → **New repository secret**. You need to add these 7 secrets:

| Secret Name | Value Example | Description |
|---|---|---|
| `CANVAS_BASE_URL` | `https://canvas.instructure.com` | The website address you use to log in to Canvas. |
| `CANVAS_TOKEN` | *<your_token>* | Generate this in Canvas (Account → Settings → New Access Token). |
| `THINGS_EMAIL` | `add-to-things-...@things.email` | Your unique email from Things 3 Settings → Things Cloud. |
| `SMTP_HOST` | `smtp.gmail.com` | Your email provider's SMTP server. |
| `SMTP_PORT` | `587` | Your email provider's SMTP port. |
| `SMTP_USER` | `me@gmail.com` | The email address you want to send *from*. |
| `SMTP_PASS` | *<app_password>* | Your email password. (For Gmail, use an [App Password](https://myaccount.google.com/apppasswords), not your login password). |

> **Note**: For Gmail, `SMTP_HOST` is usually `smtp.gmail.com` and `SMTP_PORT` is `587`. If you use another provider, check their SMTP settings.

### 4. Configure your Courses
Create one last secret named `CANVAS_CONFIG_YAML`. Copy the text below, change the IDs to match your courses, and paste it in:

```yaml
canvas:
  courses:
    # Find the ID in your Canvas course URL: /courses/123456
    - id: 123456
      alias: "Math 101"        # The name you want to see in Things
      include_description: true
    - id: 789012
      alias: "History"
      include_description: false

email:
  subject_template: "{course_alias}: {title}"

run:
  timezone: "America/Los_Angeles" # Your local timezone (e.g. America/New_York, Europe/London)
  skip_undated_assignments: true  # Optional: do not import Canvas items with no due date
```

Set `skip_undated_assignments` to `true` if you only want assignments with Canvas due dates imported into Things. Skipped undated assignments are not marked as notified, so they can still import later if Canvas adds a due date.

---

## How to use it

### Automatic
Once set up, it runs automatically **every 2 hours**. You don't need to do anything.

### Manual / Testing
You can force it to run anytime:
1. Go to the **Actions** tab.
2. Click **Canvas Things Poll** on the left.
3. Click **Run workflow** on the right.

**Dry Run Mode**:
If you check the box **"Run in dry-run mode (no emails sent)"**, it will check for assignments and print what it *would* have done in the logs, but it **won't** actually email your Things account. This is great for testing your configuration safely.

---

## Troubleshooting
- **Workflow failed?** Click on the failed run to see the logs. It will usually tell you if a secret is missing.
- **No emails?** Check the "Run poller" step in the logs. If it says "Mail to Things limit reached", it's waiting until tomorrow to send more.

---

## On-demand single-assignment import

The `scripts/pick_to_things.py` tool lets you manually pick specific Canvas assignments and create them directly in Things 3 with the Canvas due date set as the actual Things **Deadline** field — no email, no polling, no database patching.

### Requirements

- macOS with Things 3 installed
- `CANVAS_TOKEN` environment variable (same token used for the email poller)
- A `config/config.yml` file (see Setup Guide above — only the `canvas` and `run.timezone` sections are read; SMTP and Things-email settings are ignored)

### Usage

```bash
# Preview what would be added (no Things tasks created)
CANVAS_TOKEN=xxx python3 scripts/pick_to_things.py --config config/config.yml --dry-run

# Interactive run — pick assignments from a numbered list
CANVAS_TOKEN=xxx python3 scripts/pick_to_things.py --config config/config.yml

# Show assignments due in the next 14 days (default is 21)
CANVAS_TOKEN=xxx python3 scripts/pick_to_things.py --days 14

# Restrict to one course by alias
CANVAS_TOKEN=xxx python3 scripts/pick_to_things.py --course CS101

# Also show assignments with no due date
CANVAS_TOKEN=xxx python3 scripts/pick_to_things.py --include-undated
```

When you run it without `--dry-run`, the script prints a numbered list of assignments within the window, prompts you to enter comma-separated numbers (or `a` for all), then opens a `things:///add` URL for each pick. Things 3 creates the task with the Deadline field set to the correct local calendar date.

**Note:** Re-running the script on the same assignment creates a second Things task. This is intentional — the tool is manual and deliberate, not the automated poller.

---

## Local Deadline Sync Companion

The local companion runs on your Mac, reads your local Things database, and updates deadlines for Canvas-managed tasks. Setup always starts in `dry-run`; nothing writes to Things until you explicitly enable `apply`.

If a Canvas assignment is due at a local time other than 23:59, the companion treats it as an early warning: the real Canvas due date stays as the Things deadline, the previous day becomes the Things When/Today date, and the title is prefixed with the local due time plus due date, for example `[DUE 0900 5/11]`. If Canvas later changes the assignment back to a normal 23:59 due time, the next sync removes the prefix and clears the When date while keeping the deadline on the actual Canvas due date.

### Setup
1. Copy `config/config.example.yml` to `config/config.yml` if you have not already, then fill in your normal Canvas/email settings.
2. Run the guided installer:

```bash
python scripts/setup_local_sync.py
```

3. Choose whether to scan the Inbox or one exact Things project title. If you want synced tasks moved into a project, set `local_sync.move_to_project` during setup or later in `config/config.yml`.
4. If you already have older Mail-to-Things Inbox tasks from before the managed-note format was added, backfill them once:

```bash
python scripts/backfill_local_sync_notes.py
python scripts/backfill_local_sync_notes.py --apply
```

5. Run a manual dry-run and check the log:

```bash
python -m canvas_things.local_sync_main --config config/config.yml --dry-run
```

Look at `~/Library/Logs/canvas_to_things/local_sync.log` and confirm the run finishes cleanly.

6. When the dry-run looks correct, enable writes:

```bash
python scripts/enable_local_sync_apply.py
```

That switches the LaunchAgent to `apply` mode and runs one immediate sync.

If you want setup to reuse the current `local_sync` settings without prompting, run `python scripts/setup_local_sync.py --no-prompt`.

The installed LaunchAgent runs a lightweight due check every 5 minutes while your Mac is awake. Skipped checks are silent by default. A real sync runs only when the configured interval has elapsed, so opening your laptop after a missed interval catches up within about 5 minutes.

### Troubleshooting
- Check `~/Library/Logs/canvas_to_things/local_sync.log` first for the run summary and task-level errors.
- If scheduled runs are failing, also check `~/Library/Logs/canvas_to_things/local_sync.launchagent.out.log` and `~/Library/Logs/canvas_to_things/local_sync.launchagent.err.log`.
- If you get a duplicate-project error, `local_sync.project` or `local_sync.move_to_project` matches more than one open Things project title. Rename one project or use a unique title.
- If you hit the candidate cap, narrow the sync scope or raise `local_sync.candidate_cap` in `config/config.yml`.
- If the run times out, raise `local_sync.timeout_seconds` in `config/config.yml`.
- If writes are not taking effect, rerun `python scripts/enable_local_sync_apply.py`. Running `python scripts/setup_local_sync.py` always resets the schedule back to `dry-run`.
