# Canvas → Things Mail Bridge

This tool automatically checks your Canvas courses for new assignments and emails them directly to your **Things 3** "Mail to Things" address. It runs entirely in the cloud (GitHub Actions), so you don't need to keep your computer on.

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
Go to the **Actions** tab in your new repository and click the big green button to enable workflows.

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
