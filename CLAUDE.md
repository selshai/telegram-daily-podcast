# CLAUDE.md

Personal Windows automation that turns unread Telegram messages into a Hebrew audio news bulletin and sends the MP3 back to the user's Telegram Saved Messages.

## Runtime

- Windows 11, Python via system Python (no venv — deps in `requirements.txt`).
- Entry points are `.bat` files in the repo root, each `cd /d "%~dp0"` then `chcp 65001` for UTF-8.
- All scripts write a timestamped error file to `logs/error_*.log` on unhandled exception and exit 1.

## Pipeline

```
daily_podcast.bat
  [1/4] pip install -r requirements.txt
  [2/4] main.py --no-podcast   → unread_*.txt
  [3/4] summarize.py           → summary_*.txt
  [4/4] daily_podcast.py       → daily_podcast_*.mp3 → Telegram Saved Messages
```

`daily_podcast.bat` is the single entry point for the full pipeline — safe to put in Task Scheduler (no `pause`).
`summarize.bat` lets you re-run just the LLM step.
`run.bat` runs `main.py` without `--no-podcast` (sends a raw per-run MP3, standalone use only).

## Scripts

### [main.py](main.py) — fetch
- Loads `.env` (API_ID, API_HASH, PHONE, SESSION_NAME) via a tiny in-file parser; does **not** overwrite real env vars.
- Telethon: iterates dialogs with `unread_count > 0`, pulls messages newer than `now - 2 days`, skips groups listed in [ignored_groups.txt](ignored_groups.txt) (matched via `normalize_name` — collapses spaces around hyphens).
- Writes `unread_YYYY-MM-DD_HH-MM-SS.txt` with sections `===== Channel Name =====` and lines `[YYYY-MM-DD HH:MM:SS UTC] sender: text`. `daily_podcast.py` / `quick_podcast.py` rely on this exact format (see `_MSG_LINE_RE`).
- `clean_for_tts` strips emojis, URLs, `@handle`, `t.me/`, and the Hebrew "לקריאה נוחה" promo lines.
- **`--no-podcast` flag** — skips MP3 generation. Always used by `daily_podcast.bat` to avoid a duplicate MP3 alongside the one `daily_podcast.py` creates.
- Without `--no-podcast` (e.g. `run.bat`): generates a Hebrew MP3 via `edge-tts` and sends it to Saved Messages.
- After including messages, calls `send_read_acknowledge(entity, max_id=...)` — marks the included range as read.

### [summarize.py](summarize.py) — LLM news bulletin
- Reads today's `unread_*.txt` files (same parser as `daily_podcast.py`).
- Sends all messages to the Nebius-hosted Gemma model via the OpenAI-compatible API (`config.NEBIUS_BASE_URL`, `config.NEBIUS_MODEL`).
- Model acts as a **professional Israeli radio news anchor** — writes a broadcast-ready Hebrew news bulletin, sticks to facts, ignores ads and promotional content.
- Output is two sections ordered **חדשות כלליות** first, then **חדשות טכנולוגיה**, each ordered by importance.
- Writes `summary_YYYY-MM-DD_HH-MM-SS.txt` with a "קול ישראל מירושלים" header including the current Israel time (IDT = UTC+3).
- Requires `NEBIUS_API_KEY` in `.env`. If missing, exits with a clear error.
- Input is capped at `config.MAX_LLM_INPUT_CHARS` (80k) before sending.

### [daily_podcast.py](daily_podcast.py) — TTS → Telegram
- **NotebookLM removed.** Only path is `edge-tts` (`he-IL-AvriNeural`).
- Checks for `summary_<today>_*.txt` first (written by `summarize.py`). If found, strips markdown via `_MD_NOISE_RE` and reads it as the TTS script.
- Falls back to collecting `unread_<today>_*.txt` directly and building a raw script if no summary exists.
- Sends `daily_podcast_*.mp3` to Saved Messages with caption "(LLM summary)" when using the summary path.

### [quick_podcast.py](quick_podcast.py) — last 2 hours
- Same shape as `daily_podcast.py` but `collect_recent_messages` filters to `now - LOOKBACK_HOURS` (default 2).
- Does **not** use the LLM summary — uses raw unread files directly.
- `quick_podcast.bat` runs `main.py` first so today's files are fresh.

## BAT entry points

- [daily_podcast.bat](daily_podcast.bat) — **full pipeline**: deps → `main.py --no-podcast` → `summarize.py` → `daily_podcast.py`. No `pause` — safe for Task Scheduler.
- [run.bat](run.bat) — `pip install -r requirements.txt --quiet --upgrade`, then `python main.py` (standalone; sends raw per-run MP3).
- [summarize.bat](summarize.bat) — deps + `python summarize.py` (standalone; re-run to refresh today's summary).
- [quick_podcast.bat](quick_podcast.bat) — `main.py` → deps → `quick_podcast.py` (quick digest, no LLM summary).

## Config & state files (gitignored / local-only)

- `.env` — API_ID, API_HASH, PHONE, SESSION_NAME, **NEBIUS_API_KEY**. Required by all scripts.
- `telegram_session.session` (+ `-journal`) — Telethon session; deleting forces re-login.
- [ignored_groups.txt](ignored_groups.txt) — one chat title per line, `#` = comment. Names are normalized (whitespace around hyphens collapsed) before matching.
- `unread_*.txt`, `summary_*.txt`, `daily_podcast_*.mp3`, `quick_podcast_*.mp3` accumulate in the repo root — **no cleanup mechanism exists**; if you add one, beware that the podcast scripts re-read today's `unread_*.txt` and `summary_*.txt` files.

## Conventions to follow when editing

- Keep the `unread_*.txt` line format stable — the regex `^\[(... UTC)\] ([^:]+): (.+)$` in the podcast scripts depends on it.
- Print statements go through `safe_console()` in `main.py` because Windows console encoding can choke on emoji/Hebrew chat titles. Don't bypass it for new prints involving user content.
- All datetimes are normalized to UTC via `to_utc()` before comparison; preserve that — Telethon returns naive datetimes in some paths.
- `load_dotenv` uses `os.environ.setdefault` — real env vars win over `.env`. Don't switch to `os.environ[key] = ...`.
- `edge-tts` is imported under `try/except ImportError`, gated by `_EDGE_TTS_AVAILABLE`. New optional deps should follow the same pattern so the script degrades rather than crashes.
- Israel time for display: `datetime.now(timezone(timedelta(hours=3)))` (IDT = UTC+3). Do not rely on local machine timezone.

## Known gotchas

- `send_read_acknowledge` marks everything up to `max_id` as read, including messages the 2-day window cut off. Intentional for now.
- `logs/run.log` is append-only and contains the early `ModuleNotFoundError: telethon` from before deps were installed — not a live error.
- Running `main.py` without `--no-podcast` AND then `daily_podcast.py` sends two MP3s. Always use `--no-podcast` when the full pipeline runs.
