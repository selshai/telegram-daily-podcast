"""
Quick Telegram Podcast (last 2 hours)
======================================
Reads today's unread_*.txt files, keeps only messages from the last 2 hours,
generates a short podcast via NotebookLM (or edge-tts fallback),
and sends the MP3 + notebook link to Telegram Saved Messages.

Designed to be called after run.bat has already fetched the latest messages.

Requirements: telethon notebooklm-py[browser] edge-tts
"""

import asyncio
import os
import re
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telethon import TelegramClient

try:
    from notebooklm import NotebookLMClient
    try:
        from notebooklm.models import AudioFormat, AudioLength
        _AUDIO_FORMAT = AudioFormat.BRIEF
        _AUDIO_LENGTH = AudioLength.SHORT
    except Exception:
        _AUDIO_FORMAT = None
        _AUDIO_LENGTH = None
    _NOTEBOOKLM_AVAILABLE = True
except ImportError:
    _NOTEBOOKLM_AVAILABLE = False
    _AUDIO_FORMAT = None
    _AUDIO_LENGTH = None

try:
    import edge_tts
    _EDGE_TTS_AVAILABLE = True
except ImportError:
    _EDGE_TTS_AVAILABLE = False

# ── Config ────────────────────────────────────────────────────────────────────

LOOKBACK_HOURS = 2
FALLBACK_VOICE = "he-IL-AvriNeural"
MAX_MSG_CHARS  = 500

_ENV_LINE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$")
_MSG_LINE_RE = re.compile(
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


def collect_recent_messages(folder: Path, hours: int = LOOKBACK_HOURS) -> dict[str, list[tuple[datetime, str, str]]]:
    """
    Parse all of today's unread_*.txt files, return only messages
    from the last `hours` hours. Deduplicates across files.
    """
    today_str = datetime.now().strftime("%Y-%m-%d")
    files     = sorted(folder.glob(f"unread_{today_str}_*.txt"))
    cutoff    = datetime.now(timezone.utc) - timedelta(hours=hours)

    if not files:
        return {}

    print(f"Scanning {len(files)} file(s), keeping messages from last {hours}h "
          f"(since {cutoff.strftime('%H:%M UTC')})...")

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
                try:
                    dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=timezone.utc)
                except ValueError:
                    continue

                # Only keep messages within the lookback window
                if dt < cutoff:
                    continue

                key = (current_channel, ts_str, sender, text)
                if key in seen:
                    continue
                seen.add(key)
                channels[current_channel].append((dt, sender, text))

    # Sort chronologically, drop media-only channels
    for ch in channels:
        channels[ch].sort(key=lambda x: x[0])

    channels = {
        ch: msgs for ch, msgs in channels.items()
        if msgs and any(t != "[Media / Non-text message]" for _, _, t in msgs)
    }

    return channels


def build_notebooklm_source(channels: dict, label: str) -> str:
    """Clean structured text for NotebookLM to turn into a podcast."""
    total = sum(len(v) for v in channels.values())
    lines = [
        f"Telegram Quick Digest - {label}",
        f"{total} messages from {len(channels)} channels in the last {LOOKBACK_HOURS} hours.",
        "",
    ]
    for channel_name, msgs in channels.items():
        text_msgs = [(s, t) for _, s, t in msgs if t != "[Media / Non-text message]"]
        if not text_msgs:
            continue
        lines.append(f"## {channel_name}")
        for sender, text in text_msgs:
            snippet = text[:MAX_MSG_CHARS] + ("..." if len(text) > MAX_MSG_CHARS else "")
            lines.append(f"- {sender}: {snippet}")
        lines.append("")
    return "\n".join(lines)


def build_fallback_script(channels: dict, label: str) -> str:
    """Fallback TTS script."""
    total = sum(len(v) for v in channels.values())
    parts = [f"Quick Telegram digest, {label}. {total} messages from the last {LOOKBACK_HOURS} hours."]
    for channel_name, msgs in channels.items():
        text_msgs = [t for _, _, t in msgs if t != "[Media / Non-text message]"]
        if not text_msgs:
            continue
        parts.append(f"Channel {channel_name}, {len(text_msgs)} messages.")
        parts.append(" ".join(
            t[:MAX_MSG_CHARS] + ("..." if len(t) > MAX_MSG_CHARS else "")
            for t in text_msgs
        ))
    parts.append("End of quick digest.")
    return "\n\n".join(parts)


# ── NotebookLM ────────────────────────────────────────────────────────────────

async def generate_notebooklm_podcast(
    source_text: str,
    label: str,
    base_dir: Path,
    timestamp: str,
) -> tuple[str | None, Path | None]:
    mp3_path    = base_dir / f"quick_podcast_{timestamp}.mp3"
    source_file = base_dir / f"source_{timestamp}.txt"
    source_file.write_text(source_text, encoding="utf-8")

    try:
        async with await NotebookLMClient.from_storage() as client:
            print("Connected to NotebookLM.")

            nb = await client.notebooks.create(f"Telegram Quick Digest - {label}")
            notebook_url = f"https://notebooklm.google.com/notebook/{nb.id}"
            print(f"Notebook created: {notebook_url}")

            print("Uploading source...")
            try:
                await client.sources.add_file(nb.id, str(source_file), wait=True)
                print("Source uploaded as file.")
            except Exception as file_err:
                print(f"File upload failed ({file_err}), trying add_text...")
                await client.sources.add_text(nb.id, source_text, wait=True)
                print("Source uploaded as text.")

            print("Generating Audio Overview (1-2 minutes)...")
            audio_kwargs = dict(
                instructions=(
                    "You are a professional Hebrew radio news anchor. "
                    "Read these news items as a single-voice, 2-minute radio news bulletin. "
                    "No discussion between two hosts - one anchor only. "
                    "Group related stories together. Use a concise, professional tone. "
                    "Start with the most important headline."
                ),
                wait=True,
            )
            if _AUDIO_FORMAT is not None:
                audio_kwargs["audio_format"] = _AUDIO_FORMAT
            if _AUDIO_LENGTH is not None:
                audio_kwargs["audio_length"] = _AUDIO_LENGTH

            result = await client.artifacts.generate_audio(nb.id, **audio_kwargs)

            # If wait=True is not supported, fall back to manual polling
            if hasattr(result, "task_id"):
                print("Waiting for audio generation...")
                await client.artifacts.wait_for_completion(nb.id, result.task_id)

            print("Downloading MP3...")
            await client.artifacts.download_audio(nb.id, str(mp3_path))
            print(f"MP3 saved: {mp3_path.name}")

            return notebook_url, mp3_path
    finally:
        if source_file.exists():
            source_file.unlink()


# ── Fallback: edge-tts ────────────────────────────────────────────────────────

async def generate_fallback_podcast(script: str, base_dir: Path, timestamp: str) -> Path:
    mp3_path = base_dir / f"quick_podcast_{timestamp}.mp3"
    print(f"Generating fallback TTS podcast ({FALLBACK_VOICE})...")
    await edge_tts.Communicate(script, FALLBACK_VOICE).save(str(mp3_path))
    print(f"MP3 saved: {mp3_path.name}")
    return mp3_path


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    base_dir = Path(__file__).resolve().parent
    load_dotenv(base_dir / ".env")

    api_id       = int(os.environ["API_ID"])
    api_hash     = os.environ["API_HASH"]
    phone        = os.environ["PHONE"]
    session_name = os.environ.get("SESSION_NAME", "telegram_session")

    channels = collect_recent_messages(base_dir, hours=LOOKBACK_HOURS)
    if not channels:
        print(f"No messages found in the last {LOOKBACK_HOURS} hours. Nothing to podcast.")
        return

    now       = datetime.now()
    label     = now.strftime("%d/%m/%Y %H:%M")
    timestamp = now.strftime("%Y-%m-%d_%H-%M-%S")
    total     = sum(len(v) for v in channels.values())
    print(f"\n{len(channels)} channels, {total} messages in the last {LOOKBACK_HOURS}h.")

    notebook_url: str | None = None
    mp3_path: Path | None    = None

    # Try NotebookLM first
    if _NOTEBOOKLM_AVAILABLE:
        try:
            source_text = build_notebooklm_source(channels, label)
            notebook_url, mp3_path = await generate_notebooklm_podcast(
                source_text, label, base_dir, timestamp
            )
        except Exception as e:
            err = str(e).lower()
            if "timeout" in err or "timed out" in err:
                print("NotebookLM timed out -- your session likely expired.")
                print("Fix: run setup_notebooklm.bat and choose 'login' to re-authenticate.")
            elif "auth" in err or "login" in err or "credential" in err:
                print("NotebookLM auth error -- run setup_notebooklm.bat to re-login.")
            else:
                print(f"NotebookLM failed: {e}")
            print("Falling back to edge-tts...")

    # Fallback to edge-tts
    if mp3_path is None:
        if not _EDGE_TTS_AVAILABLE:
            print("ERROR: Neither notebooklm-py nor edge-tts available.")
            return
        script   = build_fallback_script(channels, label)
        mp3_path = await generate_fallback_podcast(script, base_dir, timestamp)

    # Send to Telegram
    tg = TelegramClient(session_name, api_id, api_hash)
    await tg.start(phone=phone)
    try:
        me = await tg.get_me()

        if notebook_url:
            await tg.send_message(
                me,
                f"Quick Digest -- {label}\n"
                f"{len(channels)} channels, {total} messages (last {LOOKBACK_HOURS}h)\n\n"
                f"Listen on NotebookLM: {notebook_url}"
            )
            print(f"Notebook link sent.")

        if mp3_path and mp3_path.exists():
            await tg.send_file(
                me,
                mp3_path,
                caption=f"Quick Digest -- {label}",
            )
            print("OK - Quick podcast sent to your Telegram Saved Messages!")

    finally:
        await tg.disconnect()


if __name__ == "__main__":
    log_dir = Path(__file__).resolve().parent / "logs"
    log_dir.mkdir(exist_ok=True)

    try:
        asyncio.run(main())
    except Exception:
        ts       = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        err_path = log_dir / f"error_quick_{ts}.log"
        err_path.write_text(traceback.format_exc(), encoding="utf-8")
        print(f"ERROR - see logs/error_quick_{ts}.log", file=sys.stderr)
        sys.exit(1)
