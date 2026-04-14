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

The local companion is optional and macOS-only. It runs on your Mac, reads your local Things database, and updates deadlines for Canvas-managed tasks. Setup always starts in `dry-run`; nothing writes to Things until you explicitly enable `apply`.

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

### Troubleshooting
- Check `~/Library/Logs/canvas_to_things/local_sync.log` first for the run summary and task-level errors.
- If scheduled runs are failing, also check `~/Library/Logs/canvas_to_things/local_sync.launchagent.out.log` and `~/Library/Logs/canvas_to_things/local_sync.launchagent.err.log`.
- If you get a duplicate-project error, `local_sync.project` or `local_sync.move_to_project` matches more than one open Things project title. Rename one project or use a unique title.
- If you hit the candidate cap, narrow the sync scope or raise `local_sync.candidate_cap` in `config/config.yml`.
- If the run times out, raise `local_sync.timeout_seconds` in `config/config.yml`.
- If writes are not taking effect, rerun `python scripts/enable_local_sync_apply.py`. Running `python scripts/setup_local_sync.py` always resets the schedule back to `dry-run`.
