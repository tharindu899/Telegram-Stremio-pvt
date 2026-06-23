---
title: TG Stremio
emoji: 🎬
colorFrom: blue
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
---

<p align="center">
  <img src="docs/assets/banner.svg" alt="TG Stremio Banner" width="100%" />
</p>

<h1 align="center">🎬 TG Stremio v3.2.0 — Telegram Powered Stremio Addon</h1>

<p align="center">
  <b>⚡ A beautiful Telegram media library + Stremio addon with bots, WebUI config, topic-group scanning, direct/split streaming, subtitles, subscriptions, admin tools, analytics, and Hugging Face Docker deployment.</b>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/FastAPI-WebUI-009688?logo=fastapi&logoColor=white" alt="FastAPI" />
  <img src="https://img.shields.io/badge/MongoDB-Tracking%20%2B%20Storage-47A248?logo=mongodb&logoColor=white" alt="MongoDB" />
  <img src="https://img.shields.io/badge/Telegram-Bot%20System-26A5E4?logo=telegram&logoColor=white" alt="Telegram" />
  <img src="https://img.shields.io/badge/Stremio-Addon-8A2BE2" alt="Stremio" />
  <img src="https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white" alt="Docker" />
  <img src="https://img.shields.io/badge/HuggingFace-Spaces-yellow" alt="Hugging Face" />
  <img src="https://img.shields.io/badge/UI-Mobile%20Compact-blue" alt="Mobile UI" />
  <img src="https://img.shields.io/badge/Config-WebUI%20Live-purple" alt="WebUI Config" />
  <img src="https://img.shields.io/badge/Stream%20Router-Smart%20DC--Aware-green" alt="Smart Stream Router" />
</p>

<p align="center">
  <a href="#-quick-start">⚡ Quick Start</a> •
  <a href="#-required-env">🔐 Required Env</a> •
  <a href="#-webui-config">🧩 WebUI Config</a> •
  <a href="#-admin-tools">🧰 Admin Tools</a> •
  <a href="#-bot-commands">🤖 Bot Commands</a> •
  <a href="#-full-file-structure">📁 File Structure</a>
</p>

---

## 🧠 What is this?

**TG Stremio** turns your Telegram media library into a private **Stremio addon**. It scans Telegram channels or forum/topic groups, detects movies, series, subtitles, split videos and split ZIP archives, stores metadata in MongoDB, then serves Stremio-compatible manifests, catalogs, metadata, streams, and subtitles through FastAPI.

> ⚠️ **Use responsibly:** only stream media you own, created, or have permission to use. Respect copyright, platform rules, and local laws.

---

## 🧩 New WebUI Config

📢 This build supports **configuration management from the Admin WebUI**.

✅ Only startup-critical values are required in Hugging Face Variables / `config.env`.  
✅ All other settings can be changed inside `/admin/config`.  
✅ WebUI changes are saved in MongoDB and apply dynamically without restart.  
⚠️ API credentials, database and port remain startup-only. **Source group IDs and admin login stay editable live**, even when old non-critical env values exist.

### 🔐 Required startup env

```env
# 📱 Telegram required
API_ID=""
API_HASH=""
BOT_TOKEN=""
HELPER_BOT_TOKEN=""
OWNER_ID=""

# 🗄️ Database required — exactly 2 comma-separated URIs: tracking, storage_1
DATABASE=""

# 🌐 Server required
PORT="7860"
```

### ➕ Optional multi-token section — keep it

```env
# MULTI TOKEN CONFIG
# Optional legacy startup method. Remove # to enable.
# MULTI_TOKEN1=""
# MULTI_TOKEN2=""
# MULTI_TOKEN3=""

# Extra stream bots are startup-managed. Add/remove MULTI_TOKEN values in
# Hugging Face Secrets/config.env, then rebuild the Space.
```

---

## 🚀 Quick Start

### 1️⃣ Upload / push to Hugging Face Space

Create a **Docker Space**, then push this repository.

```bash
git remote set-url origin https://huggingface.co/spaces/YOUR_USERNAME/YOUR_SPACE
git add .
git commit -m "Deploy TG Stremio"
git push origin main
```

### 2️⃣ Add required variables

Go to:

```text
Hugging Face Space → Settings → Secrets and variables
```

Add only:

| 🔐 Variable | ✅ Required | 📝 Notes |
|---|---:|---|
| `API_ID` | ✅ | Telegram API ID from my.telegram.org |
| `API_HASH` | ✅ | Telegram API hash |
| `BOT_TOKEN` | ✅ | Main/owner bot token from BotFather |
| `HELPER_BOT_TOKEN` | ✅ | Helper/stream bot token |
| `OWNER_ID` | ✅ | Numeric Telegram user ID |
| `DATABASE` | ✅ | `tracking_uri,storage_1_uri` exactly 2 URIs |
| `PORT` | ✅ | Use `7860` on Hugging Face |
| `MULTI_TOKEN1` | ➕ Optional | Extra stream/helper bot token |
| `MULTI_TOKEN2` | ➕ Optional | Extra stream/helper bot token |
| `MULTI_TOKEN3` | ➕ Optional | Extra stream/helper bot token |

### 3️⃣ Open admin pages

| 🧭 Page | 🔗 Path | 🎯 Purpose |
|---|---|---|
| 🔑 Login | `/login` | Admin login page |
| 🏠 Dashboard | `/admin/dashboard` | Media/system overview |
| 🧰 Tools | `/admin/tools` | Scan, deadcheck, dedupe, cache, analytics |
| ⚙️ Config | `/admin/config` | Live WebUI configuration |
| 🔐 Access | `/admin/access` | Tokens, manifest links, quotas |
| 💬 Subtitles | `/subtitles/manage` | Subtitle management and manual attach |
| 🗂️ Catalogs | `/admin/catalogs` | Custom catalogs and rebuild tools |

---

## 🧰 Admin Tools

The Tools page is the main control center. Telegram bot menu stays clean; maintenance actions are handled here.

| 🧰 Tool | 🔘 Button | 🧠 What it does |
|---|---|---|
| 📡 Scan All | `Scan` | Scan active Telegram channels/groups |
| 🔄 Rescan | `Rescan` | Fresh full rescan without changing original latest order |
| ⛔ Cancel | `Cancel` | Stop current scan job |
| 🩺 Deadcheck | `Dead` | Check streams and repair dead flags |
| 🧹 Dedupe preview | `Check` | Preview duplicates safely |
| ✅ Dedupe confirm | `Clean` | Remove safe exact duplicates |
| 🧊 Cache clear | `Cache` | Clear stream cache from `/tmp` |
| 🗂️ Catalog sync | `Sync` | Refresh catalog entries |
| 🏗️ Rebuild | `Rebuild` | Full catalog rebuild |
| 📊 Analytics | `Load` | Storage chart, token traffic, top watched, topic stats |
| 🔃 Refresh | `Refresh Now` | 30s compact auto-refresh with spin button |

---

## ⚙️ WebUI Config

### 🤖 Extra Stream Bots

Extra stream bots are managed only with `MULTI_TOKEN1`, `MULTI_TOKEN2`, and similar startup environment values. Add or remove a token in Hugging Face Secrets/config.env, then rebuild/restart the app. The Config page intentionally does not accept bot tokens.

- ➕ Each configured `MULTI_TOKEN` starts as a stream client.
- ⚠️ Make every extra bot an admin in each source channel/group it must read.
- 🔒 Keep bot tokens in Hugging Face Secrets; never paste them into the WebUI.

Open:

```text
/admin/config
```

Manage these after startup:

| ⚙️ Setting | 🧠 Meaning | 🔁 Restart needed? |
|---|---|---:|
| `BASE_URL` | Public Space URL used in manifest/stream links | ❌ No |
| `AUTH_CHANNEL` | Source channel/group IDs; parent `-100` group ID for topics | ❌ No |
| `TMDB_API` | TMDB metadata API key | ❌ No |
| `ADMIN_USERNAME` | Admin login username | ❌ No |
| `ADMIN_PASSWORD` | Admin login password; new logins use it immediately | ❌ No |
| `REPLACE_MODE` | Replace behavior for same releases | ❌ No |
| `HIDE_CATALOG` | Hide/show catalog modes | ❌ No |
| `SUBSCRIPTION` | Enable subscription enforcement | ❌ No |
| `APPROVER_IDS` | Subscription approver IDs | ❌ No |
| `UPDATE_REPO` | Update repo URL | ❌ No |
| `UPDATE_BRANCH` | Update branch name | ❌ No |

---

## 🤖 Bot Commands

Command names cannot contain emoji, but descriptions and help text do.

| 🤖 Command | 📝 Menu text | 🎯 Purpose |
|---|---|---|
| `/start` | 🎬 Addon link / membership menu | User start and addon link |
| `/help` | 📖 Show clean help guide | Help guide |
| `/status` | 🩺 Check runtime / subscription | Runtime and user status |
| `/tools` | 🧰 Open Admin Tools panel | Admin Tools link |
| `/set` | 🏷️ Attach IMDb metadata | Manual metadata fix |
| `/channels` | 📡 List source channels | Show active sources |
| `/addchannel` | ➕ Add source channel/group | Add Telegram source |
| `/removechannel` | ➖ Remove source channel/group | Remove Telegram source |
| `/log` | 📜 Send latest log file | Debug logs |
| `/restart` | ♻️ Restart the bot | Restart command |

💡 Advanced maintenance like scan, rescan, dedupe, deadcheck, cache clear, analytics, and catalog rebuild are moved to **Admin Tools** for cleaner mobile use.

---

## 🗂️ Telegram Topic Group Setup

One topic/forum group can replace multiple channels.

```text
📚 TG Stremio Library
├── 🎬 Movies
├── 📺 Series
├── 💬 Subtitles
└── 🧾 Logs
```

### 🔢 Topic group ID format

For a link like:

```text
https://t.me/c/3586810234/377/383
```

Use the parent group ID:

```text
-1003586810234
```

| Link part | Meaning |
|---|---|
| `3586810234` | Parent group ID |
| `377` | Topic ID |
| `383` | Message ID |

### ✅ Telegram setup checklist

| ✅ Step | 🧠 Action |
|---|---|
| 1 | Create a forum/topic group |
| 2 | Add owner bot + helper bot |
| 3 | Make bots admin |
| 4 | Enable delete message permission if you want Telegram duplicate deletion |
| 5 | Disable bot privacy in BotFather |
| 6 | Add group ID in Admin Config or `/addchannel` |
| 7 | Run `Rescan` in Admin Tools |

---

## 🎞️ File Naming Guide

### 🎬 Movies

```text
Drishyam.2.2021.Malayalam.1080p.WEB-DL.x264.AAC.mkv
Afterburn.2025.720p.WEBRip.x264.AAC.mp4
Kantara.Chapter.1.2025.2160p.AMZN.WEB-DL.HEVC.mkv
```

### 📺 Series

```text
Gen.V.S01E01.720p.WEBRip.x265.mkv
Stranger.Things.S04E05.1080p.WEB-DL.mkv
Gachiakuta.S01E18.1080p.HEVC.mkv
```

### 💬 Subtitles

Caption is checked before filename.

```text
Drishyam 2 2021 Sinhala Sub
[SUB:tt12361178 si]
[SUB:tt12361178 S01E02 si]
```

| Language | Detected words | Badge in Stremio |
|---|---|---|
| 🇱🇰 Sinhala | `Sinhala`, `සිංහල`, `sinhala_sub`, `si` | `si` |
| 🇬🇧 English | `English`, `EngSub`, `ESub`, `en` | `en` |
| 🇮🇳 Tamil | `Tamil`, `தமிழ்`, `ta` | `ta` |

---

## ⚡ Streaming Modes

| ⚡ Mode | ✅ Support | 🧠 Speed | 📝 Notes |
|---|---:|---|---|
| 🎬 Normal video | ✅ | Fastest | Best for `.mkv` / `.mp4` |
| 🧩 Direct split video | ✅ | Good | Example: `.mkv.001`, `.mkv.002` |
| 📦 Split ZIP | ✅ | Slower | Works, but random ZIP range reads can buffer more |
| 🧊 Built-in cache | ✅ | Improved | Defaults are built in, no extra env needed |

### Recommended for big files

```text
✅ Best: Movie.mkv.001 + Movie.mkv.002 + Movie.mkv.003
⚠️ OK:   Movie.zip.001 + Movie.zip.002
❌ Slow: compressed ZIP archives
```

---

## 🔐 Access Tokens and Stremio Install

1. Open `/admin/access`.
2. Tap **Add Token**.
3. Link Telegram user ID if subscription mode is enabled.
4. Copy manifest URL.
5. Paste it in Stremio Addons URL field.

```text
https://YOUR_SPACE.hf.space/stremio/YOUR_TOKEN/manifest.json
```

---

## 🧾 Latest Order Rules

| Action | Latest order changes? |
|---|---:|
| New upload | ✅ Goes to latest |
| Rescan old file | ❌ Does not move old file |
| Deadcheck | ❌ No change |
| Dedupe | ❌ No change |
| Subtitle attach | ❌ No change |

🧠 The project keeps the original Telegram message date as `date_added` so `/rescan confirm` does not reorder old movies/series.

---

## 🩺 Troubleshooting

| 😵 Problem | 🔎 Reason | ✅ Fix |
|---|---|---|
| Push rejected: `colorTo` | Invalid Hugging Face YAML color | Use `purple`, `blue`, `green`, etc. |
| Stremio cannot add addon | Manifest URL not reachable or token invalid | Open `/health` and `/manifest.json` in browser |
| User shows Unknown | Token not linked / subscription inactive | Link Telegram user ID and assign plan |
| Split ZIP buffers | ZIP random ranges are slow | Use direct split video if possible |
| Bot stats show 0 | No recent stream/range request | Play video, refresh Admin Tools |
| Delete duplicate fails | Bot lacks delete permission | Make helper bot admin with delete permission |
| Subtitle wrong language | Old DB entry or unclear name | Resend/rescan subtitle with caption |
| Admin menu not opening | Browser cache / old JS | Hard refresh or clear site cache |
| Config locked in WebUI | Value still exists in env | Remove non-critical env from HF variables |

---

## 🛡️ Security Notes

- 🔒 Do not publish real bot tokens or database URIs.
- 🔒 Keep `DATABASE`, `BOT_TOKEN`, `HELPER_BOT_TOKEN`, and `MULTI_TOKEN` values in Secrets.
- 🔒 Use strong `ADMIN_USERNAME` and `ADMIN_PASSWORD` through WebUI or env.
- 🔒 Rotate any token accidentally shared publicly.
- 🔒 Keep subscription/token access enabled if the addon is not public.

---

## 🧱 Architecture

```text
📤 Telegram Channel / Topic Group
        ↓
🤖 Owner Bot + Helper Bot + Optional Multi Tokens
        ↓
🧠 Parser / Scanner / Metadata / Subtitle Matcher
        ↓
🗄️ MongoDB Tracking DB + Storage DB
        ↓
⚡ FastAPI Web Server
        ↓
🎬 Stremio Manifest / Catalog / Meta / Stream / Subtitle APIs
        ↓
📺 Stremio / VLC / External Player
```

---

## 📁 Full File Structure

```text
📦 TG-Stremio/
├── ⚙️ .dockerignore
├── ⚙️ .gitattributes
├── ⚙️ .gitignore
├── ⚙️ .python-version
├── 🟨 admin_tools.js
├── 🐍 bump-version.py
├── ⚙️ docker-compose.yaml
├── 🐳 Dockerfile
├── ⚙️ heroku.yml
├── ⚖️ LICENSE
├── 📦 pyproject.toml
├── 📘 README.md
├── 📄 requirements.txt
├── 🔐 sample_config.env
├── 🖥️ start.sh
├── 🐍 update.py
├── 🔒 uv.lock
├── 🧠 Backend/
│   ├── 🐍 __init__.py
│   ├── 🐍 __main__.py
│   ├── 🐍 config.py
│   ├── 🐍 logger.py
│   ├── ⚡ fastapi/
│   │   ├── 🐍 __init__.py
│   │   ├── 🐍 main.py
│   │   ├── 🐍 themes.py
│   │   ├── 🛣️ routes/
│   │   │   ├── 🐍 api_routes.py
│   │   │   ├── 🐍 stream_routes.py
│   │   │   ├── 🐍 stremio_routes.py
│   │   │   └── 🐍 template_routes.py
│   │   ├── 🔐 security/
│   │   │   ├── 🐍 credentials.py
│   │   │   └── 🐍 tokens.py
│   │   ├── 🎨 static/
│   │   │   ├── 📄 site.webmanifest
│   │   │   └── 🖼️ icons/
│   │   │       ├── 🖼️ apple-touch-icon.png
│   │   │       ├── 🎨 favicon.svg
│   │   │       ├── 🖼️ icon-192.png
│   │   │       └── 🖼️ icon-512.png
│   │   └── 🧩 templates/
│   │       ├── 🧩 access_manage.html
│   │       ├── 🧩 admin_config.html
│   │       ├── 🧩 admin_dashboard.html
│   │       ├── 🧩 admin_tools.html
│   │       ├── 🧩 base.html
│   │       ├── 🧩 custom_catalogs.html
│   │       ├── 🧩 dashboard.html
│   │       ├── 🧩 login.html
│   │       ├── 🧩 media_edit.html
│   │       ├── 🧩 media_management.html
│   │       ├── 🧩 public_status.html
│   │       ├── 🧩 stremio_guide.html
│   │       ├── 🧩 subscriptions_manage.html
│   │       └── 🧩 subtitle_manage.html
│   ├── 🧰 helper/
│   │   ├── 🐍 auto_catalog.py
│   │   ├── 🐍 custom_dl.py
│   │   ├── 🐍 custom_filter.py
│   │   ├── 🐍 database.py
│   │   ├── 🐍 encrypt.py
│   │   ├── 🐍 exceptions.py
│   │   ├── 🐍 imdb.py
│   │   ├── 🐍 link_checker.py
│   │   ├── 🐍 metadata.py
│   │   ├── 🐍 modal.py
│   │   ├── 🐍 pinger.py
│   │   ├── 🐍 pyro.py
│   │   ├── 🐍 runtime_config.py
│   │   ├── 🐍 split_archive.py
│   │   ├── 🐍 subscription_checker.py
│   │   └── 🐍 task_manager.py
│   └── 🤖 pyrofork/
│       ├── 🐍 bot.py
│       ├── 🐍 clients.py
│       ├── 🔌 plugins/
│       │   ├── 🐍 channels.py
│       │   ├── 🐍 deadcheck.py
│       │   ├── 🐍 dedupe.py
│       │   ├── 🐍 feature_commands.py
│       │   ├── 🐍 fix_metadata.py
│       │   ├── 🐍 group_security.py
│       │   ├── 🐍 help.py
│       │   ├── 🐍 log.py
│       │   ├── 🐍 manual.py
│       │   ├── 🐍 reciever.py
│       │   ├── 🐍 restart.py
│       │   ├── 🐍 scanner.py
│       │   ├── 🐍 start.py
│       │   ├── 🐍 subscription.py
│       │   └── 🐍 utilities.py
│       └── 💳 subscription_plugins/
│           ├── 🐍 start.py
│           └── 🐍 subscription.py
├── 📚 docs/
│   └── 🎨 assets/
│       └── 🎨 banner.svg
└── 🛠️ tools/
    └── 🖥️ split-video-parts.sh
```

---

## 🗃️ Full File Guide Table

| Emoji | File | Purpose |
|---|---|---|
| ⚙️ | `.dockerignore` | Keeps Docker builds small by excluding cache, git, temp, and local files. |
| ⚙️ | `.gitattributes` | Git file handling rules for consistent repository behavior. |
| ⚙️ | `.gitignore` | Ignores Python cache, virtual envs, local env files, logs, and temp artifacts. |
| ⚙️ | `.python-version` | Pins the Python version expected by uv/pyenv tools. |
| 🐍 | `Backend/__init__.py` | Python package marker. |
| 🐍 | `Backend/__main__.py` | Main application bootstrap: starts web server, bots, clients, and background tasks. |
| 🐍 | `Backend/config.py` | Startup config loader for critical env and defaults. |
| 🐍 | `Backend/fastapi/__init__.py` | Python package marker. |
| 🐍 | `Backend/fastapi/main.py` | FastAPI app factory, middleware, routes, static files, health endpoints. |
| 🐍 | `Backend/fastapi/routes/api_routes.py` | Admin API endpoints: tools, stats, config, tokens, subtitles, catalog actions. |
| 🐍 | `Backend/fastapi/routes/stream_routes.py` | Stream/download routes, range handling, direct/split/ZIP streaming logic. |
| 🐍 | `Backend/fastapi/routes/stremio_routes.py` | Stremio addon routes: manifest, catalogs, meta, streams, subtitles. |
| 🐍 | `Backend/fastapi/routes/template_routes.py` | HTML page routes for admin UI, login, tools, config, dashboard, guides. |
| 🐍 | `Backend/fastapi/security/credentials.py` | Admin login credential handling. |
| 🐍 | `Backend/fastapi/security/tokens.py` | Access token creation, validation, and secure token helpers. |
| 🖼️ | `Backend/fastapi/static/icons/apple-touch-icon.png` | PWA / Chrome shortcut / favicon image asset. |
| 🎨 | `Backend/fastapi/static/icons/favicon.svg` | PWA / Chrome shortcut / favicon image asset. |
| 🖼️ | `Backend/fastapi/static/icons/icon-192.png` | PWA / Chrome shortcut / favicon image asset. |
| 🖼️ | `Backend/fastapi/static/icons/icon-512.png` | PWA / Chrome shortcut / favicon image asset. |
| 📄 | `Backend/fastapi/static/site.webmanifest` | PWA manifest for Chrome Add to Home Screen shortcut. |
| 🧩 | `Backend/fastapi/templates/access_manage.html` | Admin/Public HTML template for access manage page. |
| 🧩 | `Backend/fastapi/templates/admin_config.html` | Admin/Public HTML template for admin config page. |
| 🧩 | `Backend/fastapi/templates/admin_dashboard.html` | Admin/Public HTML template for admin dashboard page. |
| 🧩 | `Backend/fastapi/templates/admin_tools.html` | Admin/Public HTML template for admin tools page. |
| 🧩 | `Backend/fastapi/templates/base.html` | Admin/Public HTML template for base page. |
| 🧩 | `Backend/fastapi/templates/custom_catalogs.html` | Admin/Public HTML template for custom catalogs page. |
| 🧩 | `Backend/fastapi/templates/dashboard.html` | Admin/Public HTML template for dashboard page. |
| 🧩 | `Backend/fastapi/templates/login.html` | Admin/Public HTML template for login page. |
| 🧩 | `Backend/fastapi/templates/media_edit.html` | Admin/Public HTML template for media edit page. |
| 🧩 | `Backend/fastapi/templates/media_management.html` | Admin/Public HTML template for media management page. |
| 🧩 | `Backend/fastapi/templates/public_status.html` | Admin/Public HTML template for public status page. |
| 🧩 | `Backend/fastapi/templates/stremio_guide.html` | Admin/Public HTML template for stremio guide page. |
| 🧩 | `Backend/fastapi/templates/subscriptions_manage.html` | Admin/Public HTML template for subscriptions manage page. |
| 🧩 | `Backend/fastapi/templates/subtitle_manage.html` | Admin/Public HTML template for subtitle manage page. |
| 🐍 | `Backend/fastapi/themes.py` | Shared UI theme helpers/styles for admin templates. |
| 🐍 | `Backend/helper/auto_catalog.py` | Automatic catalog classification, tagging, and interval sync helper. |
| 🐍 | `Backend/helper/custom_dl.py` | Telegram file download helper with stream/range support. |
| 🐍 | `Backend/helper/custom_filter.py` | Pyrogram filter helpers for bot plugin routing. |
| 🐍 | `Backend/helper/database.py` | MongoDB layer for media, subtitles, tokens, config, stats, channels, dedupe. |
| 🐍 | `Backend/helper/encrypt.py` | URL/token encryption helpers for stream links. |
| 🐍 | `Backend/helper/exceptions.py` | Custom exception types used across backend helpers. |
| 🐍 | `Backend/helper/imdb.py` | IMDb/TMDB metadata lookup and title matching helper. |
| 🐍 | `Backend/helper/link_checker.py` | Dead-link scanner and repair logic for normal/split streams. |
| 🐍 | `Backend/helper/metadata.py` | Filename/caption parser for movies, series, subtitles, quality, language, release info. |
| 🐍 | `Backend/helper/modal.py` | HTML/modal utility helpers for admin forms. |
| 🐍 | `Backend/helper/pinger.py` | Keepalive/ping helper for Space or external checks. |
| 🐍 | `Backend/helper/pyro.py` | Pyrogram/Telegram utilities shared by plugins and stream code. |
| 🐍 | `Backend/helper/runtime_config.py` | Live WebUI config manager backed by MongoDB with env lock support. |
| 🐍 | `Backend/helper/split_archive.py` | Split video and split ZIP parser/virtual archive streaming helper. |
| 🐍 | `Backend/helper/subscription_checker.py` | Subscription/token access checker and entitlement helpers. |
| 🐍 | `Backend/helper/task_manager.py` | Background job state manager for scans, deadcheck, dedupe, catalog tasks. |
| 🐍 | `Backend/logger.py` | Project logger with IST-friendly formatting. |
| 🐍 | `Backend/pyrofork/bot.py` | Telegram bot client setup and command registration. |
| 🐍 | `Backend/pyrofork/clients.py` | Main/helper/multi-client Telegram stream client manager. |
| 🐍 | `Backend/pyrofork/plugins/channels.py` | Telegram bot plugin for channels commands/events. |
| 🐍 | `Backend/pyrofork/plugins/deadcheck.py` | Telegram bot plugin for deadcheck commands/events. |
| 🐍 | `Backend/pyrofork/plugins/dedupe.py` | Telegram bot plugin for dedupe commands/events. |
| 🐍 | `Backend/pyrofork/plugins/feature_commands.py` | Telegram bot plugin for feature commands commands/events. |
| 🐍 | `Backend/pyrofork/plugins/fix_metadata.py` | Telegram bot plugin for fix metadata commands/events. |
| 🐍 | `Backend/pyrofork/plugins/group_security.py` | Telegram bot plugin for group security commands/events. |
| 🐍 | `Backend/pyrofork/plugins/help.py` | Telegram bot plugin for help commands/events. |
| 🐍 | `Backend/pyrofork/plugins/log.py` | Telegram bot plugin for log commands/events. |
| 🐍 | `Backend/pyrofork/plugins/manual.py` | Telegram bot plugin for manual commands/events. |
| 🐍 | `Backend/pyrofork/plugins/reciever.py` | Telegram bot plugin for reciever commands/events. |
| 🐍 | `Backend/pyrofork/plugins/restart.py` | Telegram bot plugin for restart commands/events. |
| 🐍 | `Backend/pyrofork/plugins/scanner.py` | Telegram bot plugin for scanner commands/events. |
| 🐍 | `Backend/pyrofork/plugins/start.py` | Telegram bot plugin for start commands/events. |
| 🐍 | `Backend/pyrofork/plugins/subscription.py` | Telegram bot plugin for subscription commands/events. |
| 🐍 | `Backend/pyrofork/plugins/utilities.py` | Telegram bot plugin for utilities commands/events. |
| 🐍 | `Backend/pyrofork/subscription_plugins/start.py` | Subscription bot plugin for start flow. |
| 🐍 | `Backend/pyrofork/subscription_plugins/subscription.py` | Subscription bot plugin for subscription flow. |
| 🐳 | `Dockerfile` | Hugging Face Docker build file using uv + Python 3.11 and port 7860. |
| ⚖️ | `LICENSE` | Project license file. |
| 📘 | `README.md` | Main beautiful documentation and Hugging Face Space card metadata. |
| 🟨 | `admin_tools.js` | Shared helper JavaScript used by Admin Tools buttons and refresh behavior. |
| 🐍 | `bump-version.py` | Small maintenance helper for version bumps. |
| ⚙️ | `docker-compose.yaml` | Optional local Docker Compose runner for VPS/local testing. |
| 🎨 | `docs/assets/banner.svg` | README banner artwork. |
| ⚙️ | `heroku.yml` | Optional Heroku-style container deployment config. |
| 📦 | `pyproject.toml` | Python project metadata and dependency list for uv. |
| 📄 | `requirements.txt` | Compatibility dependency list for traditional pip deployments. |
| 🔐 | `sample_config.env` | Startup-critical config sample plus preserved MULTI_TOKEN examples. |
| 🖥️ | `start.sh` | Container start script for the FastAPI + bot service. |
| 🖥️ | `tools/split-video-parts.sh` | Helper script to split large video files into Telegram-friendly parts. |
| 🐍 | `update.py` | Update helper used by repo update/restart workflows. |
| 🔒 | `uv.lock` | Locked dependency resolution generated by uv. |

---

## 🧪 Health Checks

| Endpoint | Expected result |
|---|---|
| `/health` | Basic app health JSON |
| `/login` | Admin login page |
| `/admin/tools` | Admin tools control center |
| `/admin/config` | WebUI config manager |
| `/stremio/<token>/manifest.json` | Stremio manifest JSON |

---

## 🧹 Clean Repo Rules

These files should **not** be pushed:

```text
__pycache__/
*.pyc
*.pyo
*.pyd
.env
.venv/
logs/
*.log
```

Clean locally:

```bash
find . -type d -name "__pycache__" -exec rm -rf {} +
find . -type f \( -name "*.pyc" -o -name "*.pyo" -o -name "*.pyd" \) -delete
```

---

## 📦 Upload / Push Commands

```bash
cd ~/stremio
unzip -o stremio_upload_ready_full_readme.zip
git add .
git commit -m "Update full beautiful README"
git push origin main
```

---

<p align="center">
  <b>🎬 TG Stremio • Telegram Library → Stremio Addon • Built for mobile admin control ⚡</b>
</p>


### 🌍 Arabic Subtitle Auto-Detection

Arabic subtitles are detected from captions or filenames using keywords such as `Arabic`, `ArabSub`, `AR Sub`, `ara`, `ar`, `عربي`, `عربى`, and `العربية`. Stremio shows the compact language badge as `ar`.



### 🛠️ Rescan + Split/Sub Fix Notes

✅ `/scan` and `/rescan confirm` now reprocess subtitle documents more reliably, including subtitles with generic Telegram filenames when the real `.srt/.vtt/.ass` name is in the caption.  
✅ Split ZIP (`.zip.001/.002`) and direct split video (`.mkv.001/.002`) stream tokens now use JSON-safe part metadata, so datetime values will not break indexing.  
✅ Dashboard bot username lookup is safe during early startup, before Pyrogram finishes setting bot metadata.


## ⚡ Streaming v3.2.0 — Fast Seek + Multi-User Bot Pool

| 🎛️ Feature | ✅ Behavior | 📝 Details |
|---|---|---|
| ⚡ Low-buffer start | ✅ Enabled | Cold playback replies with a modest `206` window and streams the first 1 MB block immediately. |
| 🎯 Seek-aware ranges | ✅ Enabled | A new non-zero seek uses a smaller response window, so the player reaches the new position without waiting for an old large range. |
| 🤖 One stream, one bot | ✅ Enabled | A viewer’s play/resume/seek requests stay on one healthy bot for the configured stickiness period. |
| ⚖️ Multi-user balancing | ✅ Enabled | New viewers are assigned to the least-busy healthy bot. Extra bots increase concurrent-viewer capacity, not one viewer’s bandwidth. |
| 🩺 Safe failover | ✅ Enabled | On repeated bot errors, the next range can select another healthy bot. |
| 🧩 Raw split video | ✅ Best split format | Use `Movie.mkv.001`, `Movie.mkv.002`… for direct virtual range mapping. |
| 📦 Stored ZIP split | ✅ Playable | `.zip.001/.002` works best only when the video was stored with no ZIP compression. |
| 🐢 Compressed ZIP | ⚠️ Inherently slow seek | ZIP compression requires decompression from earlier bytes. Use raw split video instead. |

### 🧭 Stream router policy

Each playback is sticky to **one bot**. Other bots remain free for other users:

```text
User 1 → Bot 1
User 2 → Bot 2
User 3 → Bot 3
User 1 seeks → Bot 1 again
```

The response header `X-Stream-Router` shows the selected client and the `one-stream-one-bot` policy for troubleshooting.

### 🛠️ WebUI streaming tuning

Open `Admin → Config` → **Streaming**. Defaults are safe for Hugging Face and small-RAM hosts:

| ⚙️ Setting | Default | Purpose |
|---|---:|---|
| `STREAM_INITIAL_RANGE_MB` | `32` | Normal file cold-play window. |
| `SPLIT_STREAM_WINDOW_MB` | `32` | Direct split / stored ZIP cold-play window. |
| `STREAM_SEEK_WINDOW_MB` | `16` | Normal file seek response window. |
| `SPLIT_SEEK_WINDOW_MB` | `16` | Split file/stored ZIP seek response window. |
| `STREAM_AFFINITY_SECONDS` | `900` | How long play, resume, and seek stay on the same bot. |
| `STREAM_PREFETCH_WORKERS` | `2` | Same-bot concurrent Telegram reads; never cross-bot striping. |
| `STREAM_PREFETCH_BLOCKS` | `3` | Low-memory 1 MB read-ahead queue. |


## 🧹 Quiet Production Logs

- ✅ Successful health pings are silent.
- ✅ Auto-catalog waits quietly until a catalog selection is saved in WebUI.
- ✅ Scanner details stay in the scan progress card and Admin Tools instead of flooding container logs.
- ✅ Telegram delete-permission failures are throttled per chat for 15 minutes.
- ✅ Legacy split names such as `Movie.part001.mkv` are grouped as one stream.

## Nuvio stream card names

Stream cards use clean headings instead of generated bracket blocks:

```text
Telegram 2160p WEB-DL
Telegram 1080p WEBRip
Telegram 720p HDRip
```

The release details stay in normal readable lines (`Hindi`, `HEVC`, `DD+`, `5.1`, and file size), so the imported Nuvio profile can create badges without showing extra `[4K] [WebDL] [HIN]` text in the card heading.
