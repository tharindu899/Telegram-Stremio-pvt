
<p align="center">
  <img src="https://iili.io/KhN0ztj.png" alt="Logo" width="400"/>
</p>


<p align="center">
  A powerful, self-hosted <b>Telegram Stremio Media Server</b> built with <b>FastAPI</b>, <b>MongoDB</b>, and <b>PyroFork</b> — seamlessly integrated with <b>Stremio</b> for automated media streaming and discovery.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/UV%20Package%20Manager-2B7A77?logo=uv&logoColor=white" alt="UV Package Manager" />
  <img src="https://img.shields.io/badge/Python-3776AB?logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white" alt="FastAPI" />
  <img src="https://img.shields.io/badge/MongoDB-47A248?logo=mongodb&logoColor=white" alt="MongoDB" />
  <img src="https://img.shields.io/badge/PyroFork-EE3A3A?logo=python&logoColor=white" alt="PyroFork" />
  <img src="https://img.shields.io/badge/Stremio-8D3DAF?logo=stremio&logoColor=white" alt="Stremio" />
  <img src="https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=white" alt="Docker" />
</p>

---

## 🧭 Quick Navigation

- [🚀 Introduction](#-introduction)
  - [✨ Key Features](#-key-features)
  - [🆕 New Features](#-new-features)
  - [� Subscription Management](#-subscription-management)
  - [💳 Subscription Management](#-subscription-management-config)
  - [📋 Plans](#-subscription-plans)
  - [🤖 Bot Payment Flow](#-bot-payment-flow)
  - [🗃️ Access Management](#️-access-management)
  - [🎬 Stremio Addon Integration](#-stremio-addon-integration)
- [⚙️ How It Works](#️-how-it-works)
  - [Overview](#overview)
  - [Upload Guidelines](#upload-guidelines)
  - [Quality Replacement](#-quality-replacement-logic)
  - [Updating CAMRip](#-updating-camrip-or-low-quality-files)
  - [Behind The Scenes](#behind-the-scenes)
- [🤖 Bot Commands](#-bot-commands)
  - [Command List](#command-list)
  - [`/set` Command Usage](#set-command-usage)
- [🔧 Configuration Guide](#-configuration-guide)
  - [🧩 Startup Config](#-startup-config)
  - [🗄️ Storage](#️-storage)
  - [🎬 API](#-api)
  - [🌐 Server](#-server)
  - [🔄 Update Settings](#-update-settings)
  - [🔐 Admin Panel](#-admin-panel)

  - [🧰 Additional CDN Bots (Multi-Token System)](#-additional-cdn-bots-multi-token-system)

- [�🚀 Deployment Guide](#-deployment-guide)
  - [✅ Recommended Prerequisites](#-recommended-prerequisites)
  - [🐙 Heroku Guide](#-heroku-guide)
  - [🐳 VPS Guide (Recommended)](#-vps-guide)
- [📺 Setting up Stremio](#-setting-up-stremio)
  - [🌐 Add the Addon](#-step-3-add-the-addon)
  - [⚙️ Optional: Remove Cinemeta](#️-optional-remove-cinemeta)
- [🏅 Contributor](#-contributor)


# 🚀 Introduction

This project is a **next-generation Telegram Stremio Media Server** that allows you to **stream your Telegram files directly through Stremio**, without any third-party dependencies or file expiration issues. It’s designed for **speed, scalability, and reliability**, making it ideal for both personal and community-based media hosting.


## ✨ Key Features

- ⚙️ **Multiple MongoDB Support** 
- 📡 **Multiple Channel Support** 
- ⚡ **Fast Streaming Experience**
- 🔑 **Multi Token Load Balancer** 
- 🎬 **IMDB and TMDB Metadata Integration** 
- ♾️ **No File Expiration** 
- 🧠 **Admin Panel Support** 
- 💳 **Subscription Management** — Plans, payment approval, auto token generation, and expiry enforcement
- 🔐 **Access Management** — View, extend, reduce, revoke, and reassign subscriptions from the admin UI


## 🆕 New Features

- ⚡ **Speed Test** – Speed testing added for all bots on each file to optimize streaming performance.
- 🔄 **Improved Load Balancer** – Enhanced load balancing algorithm for better traffic distribution across multiple tokens.
- 🚫 **Failed Bot Management** – Max failed bots will be marked as shadow or idle for buffer optimization. This is due to some data center bots having rate-limiting constraints.
- 📊 **Bot-wise Analysis** – Detailed bot performance analytics available in the admin dashboard for monitoring and optimization.
- 🧹 **Deleted File Detection** – Automatic detection of deleted files on every restart, with admin capability to manually remove them from the database.
- 🛠️ **Additional Admin Features** – Various small enhancements and improvements for administrators.
- 🆓 **Free Mode Toggle** – Administrators can turn off the `SUBSCRIPTION` requirement in `config.env` to allow all users immediate access via an automatically generated API token.
- 🔄 **Automatic Stream Cleanup** – Deleting a source message in the Telegram channel instantly deletes all corresponding streams and qualities from the Stremio Addon Database and Admin Panel, preventing dead links.
- 🏷️ **Manual IMDb/TMDb Override** – Users can instantly update or fix incorrect metadata for a file by simply editing the Telegram channel message caption and pasting the correct IMDb/TMDB URL.
- 🛡️ **Stream Stability & Recovery** – Intelligent `b""` empty-chunk fallback that securely pads video streams with zero bytes, and reduced chunk retry lockups (from 6 to 3) to instantaneously recover from stalled parts without breaking the video player.
- 🎯 **DC-Aware Bot Selection** – The underlying streaming load-balancer now ensures that a bot located in the exact same Data Center as the media file is prioritized. This substantially minimizes cross-DC round-trips and timeouts.
- 🌐 **Addon Proxy Integration** – Built-in functionality targeting `config.env` configurations (`Proxy`, `ProxyType`, `HTTP_Proxy_URL`, `SHOW_ProxyAndNonProxyBoth`) allows instant proxying or caching of video streams (such as routing through Cloudflare Workers) natively on Stremio.




## ⚙️ How It Works

This project acts as a **bridge between Telegram storage and Stremio streaming**, connecting **Telegram**, **FastAPI**, and **Stremio** to enable seamless movie and TV show streaming directly from Telegram files.

### Overview

When you **forward Telegram files** (movies or TV episodes) to your **AUTH CHANNEL**, the bot automatically:

1.  🗃️ **Stores** the `message_id` and `chat_id` in the database.
2.  🧠 **Processes** file captions to extract key metadata (title, year, quality, etc.).
3.  🌐 **Generates a streaming URL** through the **PyroFork** module — routed by **FastAPI**.
4.  🎞️ **Provides Stremio Addon APIs**:
    -   `/catalog` → Lists available media
    -   `/meta` → Shows detailed information for each item
    -   `/stream` → Streams the file directly via Telegram

### Upload Guidelines

To ensure proper metadata extraction and seamless integration with **Stremio**, all uploaded Telegram media files **must include specific details** in their captions.

#### 🎥 For Movies

**Example Caption:**

```
Ghosted 2023 720p 10bit WEBRip [Org APTV Hindi AAC 2.0CH + English 6CH] x265 HEVC Msub ~ PSA.mkv
```

**Required Fields:**

-   🎞️ **Name** – Movie title (e.g., _Ghosted_)
-   📅 **Year** – Release year (e.g., _2023_)
-   📺 **Quality** – Resolution or quality (e.g., _720p_, _1080p_, _2160p_)

✅ **Optional:** Include codec, audio format, or source (e.g., `WEBRip`, `x265`, `Dual Audio`).

#### 📺 For TV Shows

**Example Caption:**

```
Harikatha.Sambhavami.Yuge.Yuge.S01E04.Dark.Hours.1080p.WEB-DL.DUAL.DDP5.1.Atmos.H.264-Spidey.mkv
````

**Required Fields:**

-   🎞️ **Name** – TV show title (e.g., _Harikatha Sambhavami Yuge Yuge_)
-   📆 **Season Number** – Use `S` followed by two digits (e.g., `S01`)
-   🎬 **Episode Number** – Use `E` followed by two digits (e.g., `E04`)
-   📺 **Quality** – Resolution or quality (e.g., _1080p_, _720p_)

✅ **Optional:** Include episode title, codec, or audio details (e.g., `WEB-DL`, `DDP5.1`, `Dual Audio`).

### 🔁 Quality Replacement Logic

When you upload multiple files with the **same quality label** (like `720p` or `1080p`),
the **latest file automatically replaces the old one**.

> Example:
> If you already uploaded `Ghosted 2023 720p` and then upload another `720p` version,
> the bot **replaces the old file** to keep your catalog clean and organized.

This helps avoid duplicate entries in Stremio and ensures only the most recent file is used.

---

### 🆙 Updating CAMRip or Low-Quality Files

If you initially uploaded a **CAMRip or low-quality version**, you can easily replace it with a better one:

1. Forward the **new, higher-quality file** (e.g., `1080p`, `WEB-DL`) to your **AUTH CHANNEL**.
2. The bot will **automatically detect and replace** the old CAMRip file in the database.
3. The Stremio addon will then **update automatically**, showing the new stream source.

✅ No manual deletion or command is needed — forwarding the updated file is enough!

---

### 🏷️ Fixing Incorrect Metadata (Manual Override)

If the addon incorrectly identifies a movie or TV show, or if the metadata is entirely missing, you can fix it effortlessly by editing the message in your Telegram channel:

1. Copy the correct **IMDb URL** or **TMDB URL** for the movie/show.
2. Edit the message caption in your Telegram **AUTH CHANNEL** and paste the URL.
3. The bot will automatically wipe the old, incorrect database entry for that file and instantly re-fetch the metadata using your provided link.

✅ The Stremio addon catalog will update dynamically to reflect the correctly identified media.

---


### Behind The Scenes

Here's how each component interacts:

| Component | Role |
| :--- | :--- |
| **Telegram Bot** | Handles uploads, forwards, and file tracking. |
| **MongoDB** | Stores message IDs, chat IDs, and metadata. |
| **PyroFork** | Generates Telegram-based streaming URLs. |
| **FastAPI** | Hosts REST endpoints for streaming, catalog, and metadata. |
| **Stremio Addon** | Consumes FastAPI endpoints for catalog display and playback. |

📦 **Flow Summary:**

```
Telegram ➜ MongoDB ➜ FastAPI ➜ Stremio ➜ User Stream
```



# 🤖 Bot Commands

Below is the list of available bot commands and their usage within the Telegram bot.

### Command List

| Command | Description |
| :--- | :--- |
| **`/start`** | Returns your **Addon URL** for direct installation in **Stremio**. |
| **`/log`** | Sends the latest **log file** for debugging or monitoring. |
| **`/set`** | Used for **manual uploads** by linking IMDB URLs. |
| **`/restart`** | Restarts the bot and pulls any **latest updates** from the upstream repository. |

### `/set` Command Usage

The `/set` command is used to manually upload a specific Movie or TV show to your channel, linking it to its IMDB metadata.

**Command:**

```
/set <imdb-url>
```

**Example:**

```
/set https://m.imdb.com/title/tt665723
```

**Steps:**

1.  Send the `/set` command followed by the **IMDB URL** of the movie or show you want to upload.
2.  **Forward the related movie or TV show files** to your channel.
3.  Once all files are uploaded, **clear the default IMDB link** by simply sending the `/set` command without any URL.

💡 **Tip:** Use `/log` if you encounter any upload or parsing issues.


# 🔧 Configuration Guide

All environment variables for this project are defined in the `config.env` file. A detailed explanation of each parameter is provided below.

### 🧩 Startup Config

| Variable | Description |
| :--- | :--- |
| **`API_ID`** | Your Telegram **API ID** from [my.telegram.org](https://my.telegram.org). Used for authenticating your Telegram session. |
| **`API_HASH`** | Your Telegram **API Hash** from [my.telegram.org](https://my.telegram.org). |
| **`BOT_TOKEN`** | The main bot’s **access token** from [@BotFather](https://t.me/BotFather). Handles user requests and media fetching. |
| **`HELPER_BOT_TOKEN`** | **Secondary bot token** used to assist the main bot with tasks like deleting, editing, or managing. |
| **`OWNER_ID`** | Your **Telegram user ID**. This ID has full administrative access. |
| **`REPLACE_MODE`** | When `true`, new files replace existing files of the same quality. When `false`, multiple files of the same quality are allowed. |
| **`HIDE_CATALOG`** | When `true`, the default Telegram Stremio Catalog is hidden, and streams only show in the Cinemata catalog (i.e., Cinemata addon is mandatory). Default is `false`. |
| **`PARALLEL`** | Controls the queue size for chunks buffered ahead. Keeps the player buffer full without overloading Telegram. Example: `PARALLEL = 4` means 4 chunks are buffered ahead. Default is `1`. |
| **`PRE_FETCH`** | Controls the number of workers downloading chunks simultaneously. Example: `PRE_FETCH = 3` means 3 workers download concurrently. Higher values can improve speed but increase API load. Default is `1`. |

### 🗄️ Storage

| Variable | Description |
| :--- | :--- |
| **`AUTH_CHANNEL`** | One or more **Telegram channel IDs** (comma-separated) where the bot is authorized to fetch or stream content. *Example: `-1001234567890, -1009876543210`*. |
| **`DATABASE`** | MongoDB Atlas connection URI(s). You **must provide at least two databases**, separated by commas (`,`) for load balancing and redundancy. <br>Example: <br>`mongodb+srv://user:pass@cluster0.mongodb.net/db1, mongodb+srv://user:pass@cluster1.mongodb.net/db2` |

> 💡 **Tip:** Create your MongoDB Atlas cluster [here](https://www.mongodb.com/cloud/atlas).

### 🎬 API

| Variable | Description |
| :--- | :--- |
| **`TMDB_API`** | Your **TMDB API key** from [themoviedb.org](https://www.themoviedb.org/settings/api). Used to fetch movie and TV metadata. |

### 🌐 Server

| Variable | Description |
| :--- | :--- |
| **`BASE_URL`** | The Domain or Heroku app URL (e.g. `https://your-domain.com`). Crucial for Stremio addon setup. |
| **`PORT`** | The port number on which your FastAPI server will run. *Default: `8000`*. |

### 🔄 Update Settings

| Variable | Description |
| :--- | :--- |
| **`UPSTREAM_REPO`** | GitHub repository URL for automatic updates. |
| **`UPSTREAM_BRANCH`** | The branch name to track in your upstream repo. *Default: `master`*. |

### 🔐 Admin Panel

| Variable | Description |
| :--- | :--- |
| **`ADMIN_USERNAME`** | Username for logging into the Admin Panel. |
| **`ADMIN_PASSWORD`** | Password for Admin Panel access.|
 **⚠️ Change from default values for security.** 

### 💳 Subscription Management Config

Enable the subscription feature to gate access to streams behind a paid plan. When `SUBSCRIPTION=True`, every user must have an active subscription to stream content.

| Variable | Description |
| :--- | :--- |
| **`SUBSCRIPTION`** | Enable (`True`) or disable (`False`) the subscription gate. When enabled, users without an active subscription see an expired message in Stremio instead of streams. *Default: `False`*. |
| **`SUBSCRIPTION_GROUP_ID`** | Telegram **group/channel ID** where approved subscribers are invited. Users receive an invite link upon payment approval. |
| **`APPROVER_IDS`** | Comma-separated Telegram user IDs of admins who can **approve or reject** subscription payment requests. |
| **`SUBSCRIPTION_URL`** | Telegram bot URL (e.g. `https://t.me/your_bot`) shown to expired users in Stremio so they can renew. |

> 💡 `SUBSCRIPTION_GROUP_ID` and `APPROVER_IDS` must be set **without quotes** in `config.env`.

### 🧰 Additional CDN Bots (Multi-Token System)

| Variable | Description |
| :--- | :--- |
| **`MULTI_TOKEN1`**, **`MULTI_TOKEN2`**, ... | Extra bot tokens used to distribute traffic and prevent Telegram rate-limiting. Add each bot as an **Admin** in your `AUTH_CHANNEL`(s). |

#### About `MULTI_TOKEN`

If your bot handles a high number of downloads/requests at a time, Telegram may limit your main bot.  
To avoid this, you can use **MULTI_TOKEN** system:

- Create multiple bots using [@BotFather](https://t.me/BotFather).
- Add each bot as **Admin** in your `AUTH_CHANNEL`(s).
- Add the tokens in your `config.env` as `MULTI_TOKEN1`, `MULTI_TOKEN2`, `MULTI_TOKEN3`, and so on.
- The system will automatically distribute the load among all these bots!

> ⚠️ **Real Limitation:** 
> Even if you configure 10 bots in your system, **a single stream will typically only use 1 bot**. Extra bots do not make a single stream download 10x faster. Their primary purpose is to help handle **multiple users streaming simultaneously** without hitting rate limits:
> - User 1 → assigned to Bot 1
> - User 2 → assigned to Bot 2
> - User 3 → assigned to Bot 3


---

# 💳 Subscription Management

The Subscription Management system allows you to **monetise access** to your Telegram Stremio server. When enabled, users must have an active subscription to stream content.

## 📋 Subscription Plans

Admins can create and manage subscription plans from the **Admin Panel → Subscription Management** page.

Each plan has:
- **Name** (e.g. `Monthly`, `Quarterly`)
- **Duration** in days
- **Price** (for display)
- **Description**

Plans are stored in MongoDB and can be added, edited, or deleted at any time without restarting.

---

## 🤖 Bot Payment Flow

Users interact with the bot to subscribe:

```
User → /start → selects plan → sends payment screenshot
      → Approver gets notification → Approve / Reject
      → On Approve:
          ✅ Subscription saved to DB
          🔑 Stremio addon token auto-generated
          📨 User receives Stremio install link + group invite
```

**Approver actions** (available to `APPROVER_IDS`):

| Button | Action |
| :--- | :--- |
| ✅ Approve | Activates subscription, generates addon token, invites user to group |
| ❌ Reject | Notifies user with rejection message |

---

## 🗃️ Access Management

The **Admin Panel → Access Management** page gives admins full control over all users and their addon tokens.

### Columns Shown

| Column | Description |
| :--- | :--- |
| Status | 🟢 Active / 🔴 Expired |
| User | Display name or `User {id}` |
| Addon Link | Stremio install URL + copy button |
| Created | Token creation date |
| Expires | Subscription expiry date |
| Actions | Buttons for managing the user |

### Action Buttons

| Button | Description |
| :--- | :--- |
| 📅 **Assign** | Assign or extend a subscription plan (adds days) |
| ➕ **Extend** | Add extra days to an active subscription |
| ➖ **Reduce** | Subtract days from an active subscription |
| 🚫 **Revoke** | Wipe subscription entirely (marks expired) |
| 🗑️ **Del Token** | Delete the addon token only (user still subscribed) |
| 🔗 **Link User ID** | Link an old/orphan token to a Telegram user ID to enable management |

> 💡 Manually created (old) tokens that have no linked user ID show a **🔗 Link User ID** button. Once linked, all action buttons become available.

### Search & Filtering

- 🔍 Search by user name or ID
- Filter by status: All / Active / Expired
- Pagination with configurable page size

---

## 🎬 Stremio Addon Integration

### Per-User Addon Token

Each user gets a **unique addon token** automatically generated on payment approval. Their Stremio addon URL is:

```
https://your-domain.com/stremio/{token}/manifest.json
```

### Dynamic Manifest

The addon manifest updates dynamically per user:

| Scenario | Addon Name | Description |
| :--- | :--- | :--- |
| Active, has expiry | `Telegram — Expires 28 Mar 2026` | 📅 Subscription active until 28 Mar 2026 |
| Active, no expiry | `Telegram — Active` | ✅ Subscription active |
| Default (no subscription mode) | `Telegram` | Standard description |

The manifest `version` encodes the expiry date — when an admin extends or revokes a subscription, the version changes and Stremio detects an update.

### Expired Stream

When a user's subscription expires, instead of streams they see:

```json
{
  "name": "🚫 Subscription Expired",
  "title": "Your subscription has expired.\nRenew via the bot to continue watching.",
  "url": "https://t.me/your_bot"   ← SUBSCRIPTION_URL from config
}
```

Clicking the stream name opens the bot directly for renewal.

### Configure & Reinstall Page

Every addon has a **Configure page** at:

```
https://your-domain.com/stremio/{token}/configure
```

This page shows:
- User name, subscription status, expiry date
- **⚡ Install / Update in Stremio** button (Stremio Web install flow)
- Manual install steps + **📋 Copy URL** button

The ⚙️ gear icon in Stremio opens this page so users can reinstall after an admin updates their subscription.

---

# 🚀 Deployment Guide

This guide will help you deploy your **Telegram Stremio Media Server** using either Heroku or a VPS with Docker.

## ✅ Recommended Prerequisites

**Supported Servers:**

  - 🟣 **Heroku**
  - 🟢 **VPS** 

Before you begin, ensure you have:

1.  ✅ A **VPS** with a public IP (e.g., Ubuntu on DigitalOcean, AWS, Vultr, etc.)
2.  ✅ A **Domain name**


## 🐙 Heroku Guide

Follow the instructions provided in the Google Colab Tool to deploy on Heroku.

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/weebzone/Colab-Tools/blob/main/telegram%20stremio.ipynb)


## 🐳 VPS Guide

This section explains how to deploy your **Telegram Stremio Media Server** on a VPS using **Docker Compose (recommended)** or **Docker**.


### 1️⃣ Step 1: Clone & Configure the Project

```bash
git clone https://github.com/weebzone/Telegram-Stremio
cd Telegram-Stremio
mv sample_config.env config.env
nano config.env
```

* Fill in all required variables in `config.env`.
* Press `Ctrl + O`, then `Enter`, then `Ctrl + X` to save and exit.

## ⚙️ Step 2: Choose Your Deployment Method

You can deploy the server using either **Docker Compose (recommended)** or **plain Docker**.



### 🟢 **Option 1: Deploy with Docker Compose (Recommended)**

Docker Compose provides an easier and more maintainable setup, environment mounting, and restart policies.

#### 🚀 Start the Container

```bash
docker compose up -d
```

Your server will now be running at:
➡️ `http://<your-vps-ip>:8000`

---

#### 🛠️ Update `config.env` While Running

If you need to modify environment values (like `BASE_URL`, `AUTH_CHANNEL`, etc.):

1. **Edit the file:**

   ```bash
   nano config.env
   ```
2. **Save your changes:** (`Ctrl + O`, `Enter`, `Ctrl + X`)
3. **Restart the container to apply updates:**

   ```bash
   docker compose restart
   ```

⚡ Since the config file is mounted, you **don’t need to rebuild** the image — changes apply automatically on restart.



### 🔵 **Option 2: Deploy with Docker (Manual Method)**

If you prefer not to use Docker Compose, you can manually build and run the container.

#### 🧩 Build the Image

```bash
docker build -t telegram-stremio .
```

#### 🚀 Run the Container

```bash
docker run -d -p 8000:8000 telegram-stremio
```

Your server should now be running at:
➡️ `http://<your-vps-ip>:8000`



### 🌐 Step 3: Add Domain (Required)

#### 🅰️ Set Up DNS Records

Go to your domain registrar and add an **A record** pointing to your VPS IP:

| Type | Name | Value             |
| ---- | ---- | ----------------- |
| A    | @    | `195.xxx.xxx.xxx` |


#### 🧱 Install Caddy (for HTTPS + Reverse Proxy)

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg
chmod o+r /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install caddy
```

#### ⚙️ Configure Caddy

1. **Edit the Caddyfile:**

   ```bash
   sudo nano /etc/caddy/Caddyfile
   ```

2. **Replace contents with:**

   ```caddy
   your-domain.com {
       reverse_proxy localhost:8000
   }
   ```

   * Replace `your-domain.com` with your actual domain name.
   * Adjust the port if you changed it in `config.env`.

3. **Save and reload Caddy:**

   ```bash
   sudo systemctl reload caddy
   ```


✅ Your API will now be available securely at:
➡️ `https://your-domain.com`


# 📺 Setting up Stremio

Follow these steps to connect your deployed addon to the **Stremio** app.

### 📥 Step 1: Download Stremio

Download Stremio for your device:
👉 [https://www.stremio.com/downloads](https://www.stremio.com/downloads)

### 👤 Step 2: Sign In

  - Create or log in to your **Stremio account**.

### 🌐 Step 3: Add the Addon

1.  Open the **Stremio App**.
2.  Go to the **Addon Section** (usually represented by a puzzle piece icon 🧩).
3.  In the search bar, paste the appropriate addon URL:

| Deployment Method | Addon URL |
| :--- | :--- |
| **Heroku** | `https://<your-heroku-app>.herokuapp.com/stremio/manifest.json` |
| **Custom Domain** | `https://<your-domain>/stremio/manifest.json` |


## ⚙️ Optional: Remove Cinemeta

If you want to use **only** your **Telegram Stremio Media Server addon** for metadata and streaming, follow this guide to remove the default `Cinemeta` addon.

### 1️⃣ Step 1: Uninstall Other Addons

1.  Go to the **Addon Section** in the Stremio App.
2.  **Uninstall all addons** except your Telegram Stremio Media Server.
3.  Attempt to remove **Cinemeta**. If Stremio prevents it, proceed to Step 2.

### 2️⃣ Step 2: Remove “Cinemeta” Protection

1.  Log in to your **Stremio account** using **Chrome or Chromium-based browser** :
    👉 [https://web.stremio.com/](https://web.stremio.com/)
2.  Once logged in, open your **browser console** (`Ctrl + Shift + J` on Windows/Linux or `Cmd + Option + J` on macOS).
3.  Copy and paste the code below into the console and press **Enter**:

<!-- end list -->

```js
(function() {

	const token = JSON.parse(localStorage.getItem("profile")).auth.key;

    const requestData = {
        type: "AddonCollectionGet",
        authKey: token,
        update: true
    };

    fetch('https://api.strem.io/api/addonCollectionGet', {
        method: 'POST',
        body: JSON.stringify(requestData)
    })
    .then(response => response.json())
    .then(data => {

    if (data && data.result) {

        let result = JSON.stringify(data.result).substring(1).replace(/"protected":true/g, '"protected":false').replace('"idPrefixes":["tmdb:"]', '"idPrefixes":["tmdb:","tt"]');
            
        const index = result.indexOf("}}],");
            
        if (index !== -1) {
            result = result.substring(0, index + 3) + "}";
        }

		let addons = '{"type":"AddonCollectionSet","authKey":"' + token + '",' + result;

		fetch('https://api.strem.io/api/addonCollectionSet', {
    		method: 'POST',
			body: addons 
		})
      	.then(response => response.text())
      	.then(data => {
      		console.log('Success:', data);
      	})
      	.catch((error) => {
      		console.error('Error:', error);
      	});

        } else {
            console.error('Error:', error);
        }
    })
    .catch((error) => {
        console.error('Erro:', error);
    });
})();
```

### 3️⃣ Step 3: Confirm Success

  - Wait until you see this message in the console:
    ```
    Success: {"result":{"success":true}}
    ```
  - Refresh the page (**F5**). You will now be able to **remove Cinemeta** from your addons list.


## 🏅 **Contributor**

|<img width="80" src="https://avatars.githubusercontent.com/u/113664541">|<img width="80" src="https://avatars.githubusercontent.com/u/13152917">|<img width="80" src="https://avatars.githubusercontent.com/u/14957082">|<img width="80" src="https://raw.githubusercontent.com/vflixa1prime/Readme/main/VFlixPRime.png">|
|:---:|:---:|:---:|:---:|
|[`Karan`](https://github.com/Weebzone)|[`Stremio`](https://github.com/Stremio)|[`ChatGPT`](https://github.com/OPENAI)|[`VFlix Prime`](https://t.me/vflixprime2)|
|Author|Stremio SDK|Refactor|Community Support
