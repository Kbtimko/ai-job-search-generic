# Setup guide

This is an AI-assisted job search you run from Claude Code. It scores job postings against
your résumé, emails you a morning digest of new matches, tailors résumés and cover letters,
and tracks your applications. Nothing is ever submitted or emailed on your behalf except
your own digest.

**Cost:** a Claude Pro or Max subscription for Claude Code, plus about $0.50 to $1.70 per
daily scan on your own Anthropic API key (only if you enable the daily scan; you set a
monthly spend limit). **Time:** about 30 minutes for the core setup, plus about 20 minutes
for each optional tier. You never need to hand-edit JSON: `/setup` writes your files for you.

## 1. Install prerequisites

- Python 3.12 or newer: `python3 --version`
- GitHub CLI: `brew install gh` (macOS) or https://cli.github.com, then `gh auth login`
- Claude Code: https://claude.com/claude-code

You should see: a `Python 3.12.x` (or newer) version line, a `gh version` line, and
`gh auth status` reporting that you are logged in to github.com.

## 2. Create your private copy

On this template's GitHub page click **Use this template → Create a new repository**, and set
the visibility to **Private**.

**Warning: the repository must be Private.** Your résumé data, job store, and applications
are committed to it (the daily scan commits them), so a public copy would publish your
personal information.

Then clone it:

```
gh repo clone <you>/<name> && cd <name>
```

You should see: a new folder containing this repo, and `gh repo view --json visibility -q .visibility` printing `PRIVATE`.

## 3. Install and test

```
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && python -m pytest -q
```

You should see: a final line like `N passed` with no failures.

## 4. Run /setup

Have your résumé file (PDF or DOCX) ready, then start Claude Code in this folder and run the
setup skill:

```
claude
/setup
```

It walks you through four sections: **profile** (your résumé becomes `profile/master.json`),
**search** (titles, locations, keywords, salary floor), **verify** (tests plus a preview of the
scan prompt, which costs nothing), and **accounts** (the optional tiers below). Each section
can be re-run alone, for example "setup search". You enter secrets yourself; never paste one
into the chat.

You should see: `profile/master.json` and an edited `config/search-config.json`, a green test
run, and a prompt preview whose TARGET TITLES and LOCATIONS match what you asked for.

## 5. Optional: daily scan

`/setup` does this for you (section 4a to 4c); here it is by hand. Run the `gh secret` commands in
your own terminal (or inside Claude Code with a leading `!`). They prompt for the value, so
it never appears in a chat.

1. Create an Anthropic API key at console.anthropic.com → API Keys, and under Billing set a
   monthly spend limit (about $50 is plenty).
2. `gh secret set ANTHROPIC_API_KEY` and paste the key at the prompt.
3. Create a Gmail app password: Google Account → Security → 2-Step Verification (must be on)
   → App passwords → name it "job search".
4. `gh secret set GMAIL_APP_PASSWORD` and paste it at the prompt.
5. Set where the digest goes. These are repository variables, not secrets, and
   `JOBSEARCH_EMAIL_FROM` must be your Gmail address:
   ```
   gh variable set JOBSEARCH_EMAIL_TO --body "you@example.com"
   gh variable set JOBSEARCH_EMAIL_FROM --body "you@gmail.com"
   ```
6. Repo → Settings → Actions → General → Workflow permissions → **Read and write
   permissions** → Save.
7. Run a first scan: `gh workflow run daily-scan.yml`, then `gh run watch`.

Until `JOBSEARCH_EMAIL_TO` is set (and, for the daily scan, `ANTHROPIC_API_KEY`), the scheduled
workflows show as skipped rather than failed.

GitHub runs scheduled jobs late (sometimes hours) on private repos. For an on-time scan,
follow `docs/external-cron-trigger.md`.

You should see: `gh secret list` naming `ANTHROPIC_API_KEY` and `GMAIL_APP_PASSWORD`, a green
run in `gh run watch`, and a digest email within about 10 minutes.

## 6. Optional: pipeline scan

This watches your Gmail for application-status emails (rejections, interview invites) and
updates your tracker. It reuses the Gmail app password and the Actions write permission from
step 5, uses no AI, and costs $0. Do not give it an Anthropic key.

1. Finish steps 5.3 to 5.6 (the app password, the email variables, write permission).
2. In Gmail settings, make sure IMAP is enabled (Settings → Forwarding and POP/IMAP).
3. Repo → Actions → **pipeline-scan** → enable the workflow if GitHub shows it as disabled,
   then run it once: `gh workflow run pipeline-scan.yml`.

You should see: a green `pipeline-scan` run, and a `chore: daily pipeline scan` commit whenever
it finds a status change.

## 7. Optional: Hosted tracker

**Local option (no accounts):** `python -m scripts.tracker`, then open http://localhost:8000.
It shows your applications as a kanban board and lets you browse jobs. That is enough for
most people.

**Hosted option (use it from your phone).** The tracker runs on Vercel, signs you in with
Google, and stores data by committing to your private repo. You need a Vercel account and a
Google account.

1. **GitHub token.** GitHub → Settings → Developer settings → Personal access tokens →
   Fine-grained tokens → Generate new token. Under Repository access choose **Only select
   repositories** and pick this repo. Under Permissions → Repository permissions set
   **Contents** to Read and write. Copy the token.
2. **Vercel project.** vercel.com → Add New → Project → import this repo (Framework Preset:
   Other). Do not deploy yet; open Settings → Environment Variables first.
3. **Environment variables (Production).** Add these:

   | Variable | Value |
   |---|---|
   | `STORE_BACKEND` | `github` |
   | `GITHUB_TOKEN` | the token from step 1 |
   | `STORE_GIT_OWNER` | your GitHub username |
   | `STORE_GIT_REPO` | this repo's name |
   | `STORE_GIT_BRANCH` | `main` |
   | `AUTH_ENABLED` | `1` |
   | `SECRET_KEY` | a long random string (`python -c "import secrets; print(secrets.token_urlsafe(32))"`); it signs your login cookie, so keep it secret |
   | `ALLOWED_EMAILS` | your Google email (comma-separated for more than one) |
   | `GOOGLE_CLIENT_ID` | from step 4 |
   | `GOOGLE_CLIENT_SECRET` | from step 4 |
   | `OAUTH_REDIRECT_URI` | `https://<your-vercel-domain>/auth/callback` |

4. **Google OAuth client.** console.cloud.google.com → APIs & Services → OAuth consent screen
   (External is fine; add yourself as a test user) → Credentials → Create credentials →
   OAuth client ID → **Web application**. Under Authorized redirect URIs add
   `https://<your-vercel-domain>/auth/callback`. Copy the Client ID and Client Secret into the
   Vercel variables above. (You learn your domain after the first deploy; add the redirect URI
   and `OAUTH_REDIRECT_URI` then and redeploy.)
5. **Deploy.** Vercel → Deployments → Deploy (or Redeploy; variables only apply to new
   deployments). Open your domain and sign in with Google.

You should see: your kanban board after Google sign-in, and `https://<your-vercel-domain>/healthz`
returning `{"ok": true}`. A different Google account gets "Not authorized".

## 8. Optional: LinkedIn analysis

Finds warm introductions at companies you are targeting, sorts recruiter messages, and
shows your application funnel.

1. LinkedIn → Settings → Data privacy → Get a copy of your data → choose Connections,
   Messages, Positions, Endorsements received, Invitations, and Job applications → Request
   archive. It arrives by email in about 24 hours; unzip it.
2. `python -m scripts.copy_linkedin_archive <path-to-extracted-export>` copies only the six
   files needed into `data/linkedin_archive/` (git-ignored).
3. `python -m linkedin.cli warm-intros` (also `inbound`, `funnel`, `manifest`).

Outputs go to `output/linkedin/` and contain real names and message text. Never commit or
share them. Everything runs locally; no API calls.

You should see: CSV files in `output/linkedin/`.

## 9. Daily use

Say these to Claude Code in this folder:

| Say | You get |
|---|---|
| "find jobs" | a scan: new matches scored, stored, and emailed as a digest |
| "tailor for `<job id>`" | an editable `.docx` résumé, a cover letter, and fit-notes in `output/<company>-<role>/` (review before applying; nothing is submitted) |
| "I applied to `<job>`" | the application logged in your tracker |
| "check my pipeline" | a review of detected status emails, asking you to confirm ambiguous ones |

`python -m scripts.tracker` opens the board locally.

You should see: results in the chat, and files in `output/` (git-ignored, so private).

## Getting updates

```
git remote add upstream <template URL>
git pull upstream main
```

Your data files are yours. On a conflict in `profile/`, `config/`, or `data/`, keep your
version: `git checkout --ours <file>` (run it while the pull is stopped on the conflict), then
`git add <file>` and finish the merge.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Tests red after setup | Run `python -m pytest -q tests/test_config_invariants.py` and fix the config it names (usually an excluded title that swallows a target title). |
| Scan returns 0 jobs | Check the `Scan cost:` line in the Actions log, then widen your titles and locations. |
| No digest email | Check the `JOBSEARCH_EMAIL_TO` and `JOBSEARCH_EMAIL_FROM` variables (`gh variable list`) and the app password; the scan fails at startup naming whichever one is missing. |
| Actions push rejected | Step 5.6: set workflow permissions to Read and write. |
| A feed shows `url_not_allowed` | Remove it. Only robots-vetted BuiltIn feeds work; LinkedIn feeds never do. |
| Tests red after adding a feed | `tests/test_config_invariants.py` pins the vetted feed hosts (`builtin.com`, `www.builtinnyc.com`). After vetting a new host (see CLAUDE.md), add it to that test's `vetted` set. |
| Scan or pipeline run shows "skipped" | By design: both workflows skip until the `JOBSEARCH_EMAIL_TO` variable is set, and the daily scan also skips without the `ANTHROPIC_API_KEY` secret. |
