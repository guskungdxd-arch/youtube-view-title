# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Two independent deliverables that solve the same problem — keeping a YouTube video's
title in sync with its view count (`"This video has 12,345 views"`):

| | Entry point | Who it serves | Scheduler |
|--|--|--|--|
| **CLI** | `youtube_view_title.py` | one person, one video, runs on the owner's Mac | macOS `launchd` (plist in `~/Library/LaunchAgents/`) |
| **Web** | `web/app.py` | many users, each with their own Google account | `APScheduler` inside the Flask process |

They share no code. The CLI stores state in `config.json` + `token.json`; the web app
stores everything in SQLite.

## Commands

There are no tests, linters, or build steps configured in this repo.

### CLI
```bash
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
./venv/bin/python youtube_view_title.py --dry-run   # show what would change
./venv/bin/python youtube_view_title.py             # update once (what launchd runs)
./venv/bin/python youtube_view_title.py --loop      # update on an interval
./venv/bin/python authorize.py                      # re-do OAuth, prints URL to open
```
Requires `client_secret.json` (Desktop OAuth client) and `config.json` (copy from
`config.example.json`). Both are gitignored.

### Web — local
```bash
cd web && ./start.sh     # http://localhost:5055, backgrounded, logs to web/server.log
cd web && ./stop.sh
```
`start.sh` reads the CLI's `client_secret.json`, generates env vars, and **sets
`RUN_SCHEDULER=0`** so local runs never rename real videos. Port 5055, not 5000 —
macOS AirPlay Receiver occupies 5000.

### Web — production deploy
```bash
ssh -i ~/.ssh/oracle_youtube ubuntu@<server-ip> \
  "cd /opt/viewtitle && git pull && sudo systemctl restart viewtitle"
```
Deploys are pull-based: push to GitHub first, then pull on the server. Live config
lives in `/etc/viewtitle.env` (chmod 600) and is **not** in the repo. The service runs
gunicorn behind nginx; `web/render.yaml` and `web/Procfile` exist for Render but the
app currently runs on a plain VM.

## Architecture

### The core operation (both entry points)
Everything reduces to two YouTube Data API v3 calls:
1. `videos.list(part="snippet,statistics")` → read `statistics.viewCount`
2. `videos.update(part="snippet")` → write the new title

**`videos.update` replaces the whole snippet.** Sending only `title` silently wipes the
description, tags and category. Both `update_title()` (CLI) and `update_one_user()`
(web) re-send the fetched `description`/`tags`/`categoryId` for this reason —
`categoryId` is required by the API and defaults to `"22"` if absent.

Callers skip the update entirely when the computed title equals the current one. This
is deliberate: reads cost 1 quota unit, updates cost ~50 of a 10,000/day budget.

### Web app request flow
`web/app.py` is a single module, sectioned by comment banners (database → OAuth →
update logic → routes). Notable wiring:

- **OAuth + PKCE**: `make_flow()` must be given the same `code_verifier` on the callback
  that `/login` generated, so it is stashed in the Flask session. Rebuilding the flow
  without it fails with `invalid_grant: Missing code verifier`.
- **Per-user credentials** live in the `users.credentials` column as `Credentials.to_json()`.
  `update_one_user()` refreshes and re-persists them when expired.
- **Admin**: `is_admin()` grants access to whoever is in `ADMIN_EMAILS`, falling back to
  the first row in `users` (the deployer) when that env var is unset. `/admin/data` and
  the `/admin/snake/*` actions send back the same `admin_snapshot()` blob, which
  deliberately drops `sub` and `credentials` before it reaches the browser.
- **Snake speed**: adjustable from `/admin`, stored in `settings`, injected into the game
  loop by the template. The anti-cheat floor (`snake_min_ms_per_step()`) is **derived**
  from it and the speed in force is stamped on each issued seed — pinning that floor to a
  constant would reject honest players the moment the game is sped up.
- **Scheduler**: `run_all()` is registered at import time when `RUN_SCHEDULER=1`, so
  **gunicorn must run `--workers 1`** — extra workers would each start their own
  scheduler and multiply API calls.

### Database
Tables: `users` (keyed by the Google `sub`), `snake_scores` / `snake_runs` (the game
leaderboard and its outstanding seeds), and `settings` (key/value, for the things admin
can change at runtime). All created on import by `init_db()`; `DATABASE_PATH` overrides
the location.

There is no migration framework. `CREATE TABLE IF NOT EXISTS` skips an existing table
whole, so a **new column on a deployed table must go through `add_column()`** — it
checks `PRAGMA table_info` first and is safe to re-run. Adding one to the `CREATE TABLE`
alone changes nothing on the server.

## Constraints that shaped this code

- **Google OAuth publishing status must be Production, not Testing.** In Testing,
  refresh tokens expire after 7 days and the background scheduler silently dies.
- **`youtube.force-ssl` is a sensitive scope.** Removing the "unverified app" warning
  requires Google verification, which is why `/privacy`, `/terms`, the account-deletion
  route, and the Limited Use disclosure exist — they are verification requirements, not
  decoration.
- **Public pages (`index.html`, `privacy.html`, `terms.html`) are intentionally English.**
  Google's reviewers read them; a Thai-only home page was rejected. `dashboard.html` and
  `admin.html` are still Thai.
- Updates are deliberately **not** real time: YouTube's own counts lag, and frequent
  title edits burn quota.

## Working on this repo

- The owner's real YouTube videos are the test fixtures. Do not change a live video's
  title without asking, and leave `RUN_SCHEDULER=0` locally.
- `web/DEPLOY_CHECKLIST.md` tracks what is done vs. outstanding for going public;
  `web/DEMO_VIDEO_SCRIPT.md` is the script for the OAuth verification demo video.
