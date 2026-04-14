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
Go to **Settings** → **Secrets and variables** → **Actions** → **New repository secret**. You need to add these 5 secrets:

| Secret Name | Value Example | Description |
|---|---|---|
| `CANVAS_BASE_URL` | `https://canvas.instructure.com` | The website address you use to log in to Canvas. |
| `CANVAS_TOKEN` | *<your_token>* | Generate this in Canvas (Account → Settings → New Access Token). |
| `THINGS_EMAIL` | `add-to-things-...@things.email` | Your unique email from Things 3 Settings → Things Cloud. |
| `SMTP_USER` | `me@gmail.com` | The email address you want to send *from*. |
| `SMTP_PASS` | *<app_password>* | Your email password. (For Gmail, use an [App Password](https://myaccount.google.com/apppasswords), not your login password). |

> **Note**: For Gmail, `SMTP_HOST` is usually `smtp.gmail.com` and `SMTP_PORT` is `587`. If you use another provider, check their settings. You can add `SMTP_HOST` and `SMTP_PORT` as secrets too if they differ.

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
```

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

## Local Deadline Sync Companion

The local companion is separate from the email bridge. It runs on your Mac, reads your local Things database in read-only mode, plans mutations for Canvas-managed tasks, and only writes to Things when you explicitly enable apply mode.

### Supported scope
- If `local_sync.project` is omitted, the companion scans the **Inbox** only, including tasks under Inbox headings.
- If `local_sync.project` is omitted and `local_sync.move_to_project` is set, discovery becomes a **dual-scope Inbox run**: it reads both the Inbox scope and the exact destination project scope, then merges those open tasks into one planning batch.
- The destination project may be empty. That still counts as a valid dual-scope Inbox run; it just contributes zero existing tasks.
- If `local_sync.project` is set, it must match **one exact open Things project title**. Discovery includes that project and its headings.
- Only **open, non-trashed tasks** in the chosen scope are considered.
- If more than one open project has the same exact title, the run fails before planning because the scope is ambiguous.
- If `local_sync.move_to_project` is set during an Inbox run, that destination project must also resolve to exactly one open project. Missing or ambiguous destination projects hard-abort the run before planning or apply starts.
- Dual-scope Inbox discovery enforces a duplicate-uuid invariant: every discovered task uuid must appear in at most one scope. If the same uuid is returned from both scopes, the run aborts because scope resolution is inconsistent.

### Required note markers

Only tasks with the canonical v1 managed-note contract are managed:

```text
Course work
Due: 2026-04-15
Canvas:
```

- Freeform note content may appear above the managed lines.
- A managed note must contain exactly one machine-readable line in the form `Due: YYYY-MM-DD`.
- The marker line must be exactly `Canvas:`.
- The marker must be the **last non-empty line** in the note. Trailing blank lines are fine.
- Any line whose trimmed content starts with `Due:` or `Canvas:` is reserved for the managed-note contract. Freeform content must not use those prefixes, even with leading whitespace.
- A writable managed task must contain **exactly one** `Due: YYYY-MM-DD` line and exactly one reserved marker line.
- If the marker is malformed, duplicated, or not last, the task is ignored as unmanaged.
- If the marker is valid but the due line is missing, duplicated, or malformed, the task is kept as a managed diagnostic but will not be edited.

### Future tasks vs existing tasks

There are two different cases:

- **Future Mail-to-Things tasks**: anything created after the notifier fix already arrives in the canonical managed format and does **not** need migration.
- **Existing Mail-to-Things tasks**: older Inbox tasks created before that fix still use the old incompatible note shape and need a one-time backfill before local sync can manage them.

Older incompatible note bodies looked like this:

```text
Course work
Course: Math 101
Due: 2026-04-15 23:59:00 UTC (2026-04-15 16:59:00 PDT)
Submission: online_upload
```

That shape is incompatible because the old `Due:` line is human-readable instead of `YYYY-MM-DD`, and there is no trailing `Canvas:` marker.

New tasks and backfilled old tasks use the canonical managed shape instead:

```text
Course work
Course: Math 101
Due: 2026-04-15
Due At: 2026-04-15 23:59:00 UTC (2026-04-15 16:59:00 PDT)
Submission: online_upload

Canvas:
```

The one-time backfill rewrites note bodies only. It does **not** rename tasks, move tasks between projects, or change any Things deadlines by itself.

### Title grouping and mutation order
- Task families are grouped by title after one exact normalization rule: if a title starts with the literal prefix `[UPDATE] `, that single prefix is removed for grouping. No other case, spacing, or prefix variations are normalized.
- Within a grouped family, non-update tasks stay canonical whenever possible. Update tasks are only kept canonical when no non-update task exists for that normalized title.
- During Inbox runs with `local_sync.move_to_project`, a matching task already in the destination project is preferred as the primary canonical task for that family. Inbox duplicates may still remain canonical if they are non-update tasks, but they are planned to move into the destination project.
- Apply order is strict: all canonical mutations run first in planner order, then redundant-update trash mutations run after them in the same batch. The batch is emitted to AppleScript in that exact order, and the run aborts if AppleScript returns results out of order.

### Backfill Existing Tasks

If you already have older Mail-to-Things Inbox tasks that were created before the canonical managed-note format was added, run the backfill before relying on the local companion.

1. Preview the candidates:

```bash
python scripts/backfill_local_sync_notes.py
```

2. Review the printed task IDs and titles.
3. Apply the note rewrite:

```bash
python scripts/backfill_local_sync_notes.py --apply
```

4. Run local sync in dry-run mode to confirm the migrated tasks are now manageable:

```bash
python -m canvas_things.local_sync_main --config config/config.yml --dry-run
```

### Confirm Success In Logs

Check `~/Library/Logs/canvas_to_things/local_sync.log` after the dry-run.

Successful migration usually looks like this:

- The run starts with `Starting local sync mode=dry-run ...`.
- You do **not** see `Diagnostic-only managed task ... diagnostics=missing_due`, `multiple_due_lines`, or `malformed_due` for the migrated tasks.
- If a migrated task needs a deadline update, you see `Planned mutation task_id=... title='...' actions=due_date=YYYY-MM-DD`.
- The run ends with `Local sync summary exit_code=0 ...`.

If a migrated task already has the correct Things deadline, it may not produce a `Planned mutation ...` line. That is still fine. The important part is that the run completes with `exit_code=0` and the task is no longer showing up as a diagnostic-only managed note.

### Setup
1. Copy `config/config.example.yml` to `config/config.yml` and fill in your normal Canvas/email settings if you have not already.
2. Run `python scripts/setup_local_sync.py` and answer the prompts.

That setup script is intentionally conservative:
- It seeds `config/config.yml` from the example if the file does not exist.
- It walks you through the `local_sync.project` and `local_sync.move_to_project` choices in the terminal.
- It forces `local_sync.mode` back to `dry-run`.
- It installs the LaunchAgent in `dry-run` mode, so scheduled writes stay disabled.

If you want to skip the prompts and preserve the existing `local_sync` scope settings as-is, run:

```bash
python scripts/setup_local_sync.py --no-prompt
```

Run a dry-run manually first if you want to inspect behavior immediately:

```bash
python -m canvas_things.local_sync_main --config config/config.yml --dry-run
```

Automatic writes are **not** enabled by setup alone. After you have verified dry-run output and logs, you must explicitly enable apply mode:

```bash
python scripts/enable_local_sync_apply.py
```

That command switches the LaunchAgent to `apply` mode and runs one immediate apply sync.

### Safety limits and failure behavior
- `local_sync.candidate_cap` is a hard stop on **managed candidates**, not just writable mutations. It counts every discovered `Canvas:` task in the resolved scope, including diagnostic-only managed notes, before any writes are planned. The default is `200`.
- `local_sync.timeout_seconds` is a wall-clock limit for the full run. The default is `120`.
- If the timeout is reached before apply starts, no Things mutations run.
- If apply has already started, the command can still exit with timeout status once control returns and the runtime guard detects that the wall-clock limit has been exceeded.
- The companion never scans all of Things. It only reads the configured Inbox scope or one exact project scope.
- When apply hits a per-task verification failure after some earlier mutations already succeeded, the run exits with partial-failure status. A later rerun is safe: already-settled canonical tasks can drop out of the mutation batch, while any still-redundant update tasks continue to be retried until they are trashed.

### Locking and stale-lock recovery
- The local companion uses a lock file at `~/Library/Application Support/canvas_to_things/local_sync.lock` so two runs do not overlap.
- A live run on the same machine keeps ownership of the lock by PID and process start time. A second run fails instead of racing it.
- If the lock file is stale, malformed, or points at a dead process, the next run automatically replaces it.
- If you suspect a genuinely stuck active run, inspect the PID in the lock file first. Only remove the file manually after confirming that process is no longer running.

### Troubleshooting the local companion
- Check `~/Library/Logs/canvas_to_things/local_sync.log` for the structured run log and summary.
- Check `~/Library/Logs/canvas_to_things/local_sync.launchagent.out.log` and `~/Library/Logs/canvas_to_things/local_sync.launchagent.err.log` for LaunchAgent stdout/stderr.
- A duplicate-project error means `local_sync.project` matched more than one open Things project title. Rename one of the projects or choose a unique title.
- A candidate-cap failure means your scope contains more managed `Canvas:` tasks than expected. Narrow the scope or raise `local_sync.candidate_cap` deliberately.
- A timeout usually means the run spent too long in discovery, planning, or apply verification. Increase `local_sync.timeout_seconds` only if the longer runtime is expected.
- If apply mode is not taking effect, rerun `python scripts/enable_local_sync_apply.py`; `python scripts/setup_local_sync.py` always resets the LaunchAgent back to dry-run for safety.
