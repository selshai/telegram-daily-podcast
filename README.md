# Telegram Daily Podcast

Fetches unread messages from your Telegram channels, generates a Hebrew news bulletin using an LLM, converts it to speech, and sends the MP3 to your Telegram Saved Messages — automatically.

## How it works

```
daily_podcast.bat
  1. Fetches unread Telegram messages  → unread_*.txt
  2. LLM generates a Hebrew news bulletin → summary_*.txt
  3. edge-tts converts the bulletin to MP3
  4. MP3 is sent to your Telegram Saved Messages
```

The LLM acts as a radio news anchor: it reports facts, ignores ads, and splits stories into general news and tech news (in that order).

---

## Setup

### 1. Python

Requires **Python 3.11+**. Download from [python.org](https://www.python.org/downloads/).

### 2. Telegram API credentials

You need a Telegram API app (not a bot — a personal API app).

1. Go to [https://my.telegram.org](https://my.telegram.org) and log in with your phone number.
2. Click **API development tools**.
3. Fill in any app name and short name, click **Create application**.
4. Copy your **App api_id** and **App api_hash**.

### 3. Nebius API key (LLM)

The summarizer uses a Gemma model hosted on [Nebius AI Studio](https://studio.nebius.com/).

1. Sign up at [https://studio.nebius.com](https://studio.nebius.com).
2. Go to **API Keys** and create a new key.
3. Copy the key.

> The model used is `google/gemma-3-27b-it` (configurable in `config.py`).

### 4. Create your `.env` file

Create a file named `.env` in the project root:

```env
API_ID=12345678
API_HASH="your_api_hash_here"
PHONE="+972501234567"
SESSION_NAME="telegram_session"
NEBIUS_API_KEY="your_nebius_key_here"
```

- `API_ID` — integer from step 2
- `API_HASH` — string from step 2
- `PHONE` — your Telegram phone number in international format
- `SESSION_NAME` — any name; a `.session` file with this name will be created locally
- `NEBIUS_API_KEY` — from step 3

### 5. Install dependencies

```bat
pip install -r requirements.txt
```

### 6. First run (Telegram login)

The first time you run the script, Telethon will ask for a verification code sent to your Telegram account:

```bat
python main.py
```

Enter the code when prompted. A `telegram_session.session` file is created — you won't need to log in again.

---

## Running the pipeline

### Full daily pipeline

Double-click `daily_podcast.bat` or run from the command line:

```bat
daily_podcast.bat
```

This runs all four steps automatically and sends the MP3 to your Saved Messages.

### Task Scheduler (automatic daily run)

In Windows Task Scheduler:
- **Program/script:** `C:\path\to\Telegram\daily_podcast.bat`
- **Start in:** `C:\path\to\Telegram`
- **Trigger:** Daily at your preferred time

### Other entry points

| Script | What it does |
|---|---|
| `run.bat` | Fetch only — sends a quick raw MP3 without LLM summary |
| `summarize.bat` | Re-run just the LLM step (e.g. to regenerate today's summary) |
| `quick_podcast.bat` | Fetch + podcast for the last 2 hours only (no LLM) |

---

## Configuration

`config.py` exposes three values you can change:

```python
NEBIUS_BASE_URL     = "https://api.studio.nebius.com/v1"
NEBIUS_MODEL        = "google/gemma-3-27b-it"
MAX_LLM_INPUT_CHARS = 80_000
```

To skip specific Telegram channels, add their exact names (one per line) to `ignored_groups.txt`.

---

## File layout

```
.env                    # your secrets (gitignored)
config.py               # LLM endpoint and model
main.py                 # Telegram fetch
summarize.py            # LLM news bulletin
daily_podcast.py        # TTS → MP3 → Telegram
quick_podcast.py        # same, last 2 hours only
ignored_groups.txt      # channels to skip
requirements.txt        # Python dependencies
daily_podcast.bat       # full pipeline entry point
```
