"""
Daily Telegram Podcast
======================
Pipeline: main.py (fetch) → summarize.py (LLM) → daily_podcast.py (TTS → Telegram)

Reads today's summary_*.txt, converts to speech via edge-tts, and sends the MP3
to Telegram Saved Messages. Falls back to raw unread_*.txt if no summary exists.
"""

import asyncio
import os
import re
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from telethon import TelegramClient

try:
    import edge_tts
    _EDGE_TTS_AVAILABLE = True
except ImportError:
    _EDGE_TTS_AVAILABLE = False

# ── Config ────────────────────────────────────────────────────────────────────

FALLBACK_VOICE = "he-IL-AvriNeural"
MAX_MSG_CHARS  = 500

_ENV_LINE_RE   = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$")
_MD_NOISE_RE   = re.compile(r"^#+\s*|^[•\-\*]\s*", re.MULTILINE)
_MSG_LINE_RE   = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC)\] ([^:]+): (.+)$"
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = _ENV_LINE_RE.match(line)
        if not m:
            continue
        key, raw = m.group(1), m.group(2).strip()
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
            raw = raw[1:-1]
        os.environ.setdefault(key, raw)


def collect_today_messages(folder: Path) -> dict[str, list[tuple[datetime, str, str]]]:
    today_str = datetime.now().strftime("%Y-%m-%d")
    files = sorted(folder.glob(f"unread_{today_str}_*.txt"))

    if not files:
        return {}

    print(f"Found {len(files)} file(s) for today ({today_str}):")
    for f in files:
        print(f"  {f.name}")

    seen: set[tuple[str, str, str, str]] = set()
    channels: dict[str, list[tuple[datetime, str, str]]] = {}
    current_channel = None

    for filepath in files:
        lines = filepath.read_text(encoding="utf-8", errors="ignore").splitlines()
        for line in lines:
            if line.startswith("=====") and line.endswith("====="):
                current_channel = line.strip("= ").strip()
                if current_channel not in channels:
                    channels[current_channel] = []
                continue

            m = _MSG_LINE_RE.match(line)
            if m and current_channel:
                ts_str, sender, text = m.group(1), m.group(2).strip(), m.group(3).strip()
                key = (current_channel, ts_str, sender, text)
                if key in seen:
                    continue
                seen.add(key)
                try:
                    dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                channels[current_channel].append((dt, sender, text))

    for ch in channels:
        channels[ch].sort(key=lambda x: x[0])

    return {
        ch: msgs for ch, msgs in channels.items()
        if any(t != "[Media / Non-text message]" for _, _, t in msgs)
    }


def build_fallback_script(channels: dict, date_str: str) -> str:
    total = sum(len(v) for v in channels.values())
    parts = [
        f"Daily Telegram digest for {date_str}. "
        f"{total} messages from {len(channels)} channels.",
    ]
    for channel_name, msgs in channels.items():
        text_msgs = [t for _, _, t in msgs if t != "[Media / Non-text message]"]
        if not text_msgs:
            continue
        parts.append(f"Channel {channel_name}, {len(text_msgs)} messages.")
        combined = " ".join(
            t[:MAX_MSG_CHARS] + ("..." if len(t) > MAX_MSG_CHARS else "")
            for t in text_msgs
        )
        parts.append(combined)
    parts.append("End of daily digest.")
    return "\n\n".join(parts)


def find_today_summary(folder: Path) -> Path | None:
    today_str = datetime.now().strftime("%Y-%m-%d")
    files = sorted(folder.glob(f"summary_{today_str}_*.txt"), reverse=True)
    return files[0] if files else None


def summary_to_tts_script(summary_text: str) -> str:
    return _MD_NOISE_RE.sub("", summary_text).strip()


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    base_dir = Path(__file__).resolve().parent
    load_dotenv(base_dir / ".env")

    api_id       = int(os.environ["API_ID"])
    api_hash     = os.environ["API_HASH"]
    phone        = os.environ["PHONE"]
    session_name = os.environ.get("SESSION_NAME", "telegram_session")

    date_str  = datetime.now().strftime("%d/%m/%Y")
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    if not _EDGE_TTS_AVAILABLE:
        print("ERROR: edge-tts is not installed. Run: pip install edge-tts")
        return

    # Build TTS script — prefer today's LLM summary, fall back to raw messages
    summary_path = find_today_summary(base_dir)
    if summary_path:
        print(f"Using LLM summary: {summary_path.name}")
        script  = summary_to_tts_script(summary_path.read_text(encoding="utf-8"))
        caption = f"Daily Telegram Digest -- {date_str} (LLM summary)"
    else:
        channels = collect_today_messages(base_dir)
        if not channels:
            print("No messages found for today. Nothing to podcast.")
            return
        total = sum(len(v) for v in channels.values())
        print(f"\n{len(channels)} channels, {total} messages collected.")
        script  = build_fallback_script(channels, date_str)
        caption = f"Daily Telegram Digest -- {date_str}"

    # Generate MP3
    mp3_path = base_dir / f"daily_podcast_{timestamp}.mp3"
    print(f"Generating podcast ({FALLBACK_VOICE})...")
    await edge_tts.Communicate(script, FALLBACK_VOICE).save(str(mp3_path))
    print(f"MP3 saved: {mp3_path.name}")

    # Send to Telegram Saved Messages
    tg = TelegramClient(session_name, api_id, api_hash)
    await tg.start(phone=phone)
    try:
        me = await tg.get_me()
        await tg.send_file(me, mp3_path, caption=caption)
        print("OK - Daily podcast MP3 sent to your Telegram Saved Messages!")
    finally:
        await tg.disconnect()


if __name__ == "__main__":
    log_dir = Path(__file__).resolve().parent / "logs"
    log_dir.mkdir(exist_ok=True)

    try:
        asyncio.run(main())
    except Exception:
        ts       = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        err_path = log_dir / f"error_daily_{ts}.log"
        err_path.write_text(traceback.format_exc(), encoding="utf-8")
        print(f"ERROR - see logs/error_daily_{ts}.log", file=sys.stderr)
        sys.exit(1)
