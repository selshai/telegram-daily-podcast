"""
Fetch unread Telegram messages, save to text file, and send
an MP3 podcast (Hebrew narration) to your Telegram Saved Messages.

Requirements: telethon edge-tts

Make sure `.env` contains:
  API_ID=...
  API_HASH="..."
  PHONE="..."
  SESSION_NAME="telegram_session"
"""

import argparse
import asyncio
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telethon import TelegramClient

try:
    import edge_tts
    _EDGE_TTS_AVAILABLE = True
except ImportError:
    _EDGE_TTS_AVAILABLE = False

# Hebrew TTS voice — change to "he-IL-HilaNeural" for female voice
PODCAST_VOICE = "he-IL-AvriNeural"
MAX_MSG_CHARS = 300  # truncate long messages in the narration

_ENV_LINE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$")


def load_dotenv(dotenv_path: Path) -> None:
    """Load `KEY=value` pairs from a `.env` file (ignores non-matching lines)."""

    if not dotenv_path.exists():
        return

    text = dotenv_path.read_text(encoding="utf-8", errors="ignore")
    for line in text.splitlines():
        match = _ENV_LINE_RE.match(line)
        if not match:
            continue

        key = match.group(1)
        raw_value = match.group(2).strip()
        if len(raw_value) >= 2 and raw_value[0] == raw_value[-1] and raw_value[0] in ("'", '"'):
            raw_value = raw_value[1:-1]

        # Don't overwrite real environment variables; use `.env` only as fallback.
        os.environ.setdefault(key, raw_value)


_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F9FF"  # misc symbols, emoticons
    "\U00002702-\U000027B0"  # dingbats
    "\U000024C2-\U0001F251"
    "\U0001F004-\U0001F0CF"
    "\u2600-\u26FF"          # misc symbols (☀☎✈)
    "\u2700-\u27BF"
    "]+",
    flags=re.UNICODE,
)

# Lines that contain only navigation/promo noise — skip them in narration
_NOISE_PATTERNS = re.compile(
    r"לקריאה נוחה|לקריאת נוחה|@\w+|t\.me/|https?://",
    flags=re.IGNORECASE,
)


def clean_for_tts(text: str) -> str:
    """Remove emojis, navigation arrows, links, and promo lines from message text."""
    cleaned_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Drop lines that are pure noise
        if _NOISE_PATTERNS.search(stripped):
            continue
        # Drop lines that become empty after stripping emojis
        no_emoji = _EMOJI_RE.sub("", stripped).strip()
        if not no_emoji:
            continue
        cleaned_lines.append(no_emoji)

    result = " ".join(cleaned_lines)
    result = re.sub(r"\s+", " ", result).strip()
    return result or "[Media / Non-text message]"


def build_podcast_script(messages_by_channel: dict, date_str: str) -> str:
    """Build a Hebrew narration script from collected messages."""
    total = sum(len(v) for v in messages_by_channel.values())
    parts = [
        f"שלום! ברוכים הבאים לעדכון הטלגרם היומי שלך לתאריך {date_str}. "
        f"יש לך {total} הודעות חדשות מ-{len(messages_by_channel)} ערוצים.",
        "נתחיל."
    ]
    for channel_name, msgs in messages_by_channel.items():
        text_msgs = [t for _, t in msgs if t != "[Media / Non-text message]"]
        if not text_msgs:
            continue
        parts.append(f"ערוץ {channel_name}, {len(text_msgs)} הודעות.")
        combined = " ".join(
            t[:MAX_MSG_CHARS] + ("..." if len(t) > MAX_MSG_CHARS else "")
            for t in text_msgs
        )
        parts.append(combined)
    parts.append("זה הכל לעדכון הנוכחי. שיהיה לך יום נפלא!")
    return "\n\n".join(parts)


async def generate_and_send_podcast(
    client: TelegramClient,
    messages_by_channel: dict,
    timestamp: str,
    base_dir: Path,
) -> None:
    """Convert messages to MP3 via edge-tts and send to Saved Messages."""
    if not _EDGE_TTS_AVAILABLE:
        print("\nWARNING: edge-tts not installed -- skipping podcast. Run: pip install edge-tts")
        return

    try:
        date_str = datetime.now().strftime("%d/%m/%Y")
        script = build_podcast_script(messages_by_channel, date_str)

        mp3_path = base_dir / f"podcast_{timestamp}.mp3"
        print(f"\nGenerating podcast MP3 ({PODCAST_VOICE})...")
        communicate = edge_tts.Communicate(script, PODCAST_VOICE)
        await communicate.save(str(mp3_path))
        print(f"MP3 saved: {mp3_path.name}")

        me = await client.get_me()
        total = sum(len(v) for v in messages_by_channel.values())
        await client.send_file(
            me,
            mp3_path,
            caption=(
                f"Telegram Digest -- {date_str}\n"
                f"{len(messages_by_channel)} channels · {total} messages"
            ),
        )
        print("OK - Podcast sent to your Telegram Saved Messages!")
    except Exception as e:
        print(f"FAILED - Podcast error: {e}")


def normalize_name(name: str) -> str:
    """Normalize a channel name for comparison: strip whitespace, collapse spaces around hyphens."""
    name = name.strip()
    name = re.sub(r"\s*-\s*", "-", name)  # "כומתה - צבע אדום" → "כומתה-צבע אדום"
    name = re.sub(r"\s+", " ", name)
    return name


def load_ignored_groups(path: Path) -> set[str]:
    """Load group names to ignore from a text file (one per line, # = comment).
    Names are normalized so minor whitespace differences around hyphens don't matter."""
    if not path.exists():
        return set()
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    return {normalize_name(line) for line in lines if line.strip() and not line.strip().startswith("#")}


async def main(no_podcast: bool = False) -> None:
    dotenv_path = Path(__file__).resolve().parent / ".env"
    load_dotenv(dotenv_path)

    api_id_raw = os.getenv("API_ID")
    api_hash = os.getenv("API_HASH")
    phone = os.getenv("PHONE")
    session_name = os.getenv("SESSION_NAME")

    missing = [
        name
        for name, val in {
            "API_ID": api_id_raw,
            "API_HASH": api_hash,
            "PHONE": phone,
            "SESSION_NAME": session_name,
        }.items()
        if not val
    ]

    if missing:
        raise RuntimeError(
            "Missing required values in .env: "
            + ", ".join(missing)
            + '. Expected lines like API_ID=..., API_HASH="...", PHONE="...", SESSION_NAME="...".'
        )

    try:
        api_id = int(api_id_raw)
    except ValueError as e:
        raise RuntimeError(f"API_ID must be an integer, got: {api_id_raw!r}") from e

    client = TelegramClient(session_name, api_id, api_hash)
    await client.start(phone=phone)

    try:
        print("\nConnected to Telegram!\n")
        print("=" * 60)
        print("UNREAD MESSAGES (last 2 days)")
        print("=" * 60)

        since = datetime.now(timezone.utc) - timedelta(days=2)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        output_path = Path(__file__).resolve().parent / f"unread_{timestamp}.txt"
        ignored_groups = load_ignored_groups(Path(__file__).resolve().parent / "ignored_groups.txt")
        all_lines: list[str] = []
        total_saved = 0
        messages_by_channel: dict[str, list[tuple[str, str]]] = {}  # for podcast

        # Helper: make Telethon datetimes consistently timezone-aware (UTC).
        def to_utc(dt: datetime) -> datetime:
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)

        # Windows console may use a non-UTF8 encoding (e.g. cp1252). This prevents crashes
        # when chat titles contain emojis or other characters.
        def safe_console(s: object) -> str:
            try:
                text = str(s)
                return text.encode("ascii", "backslashreplace").decode("ascii")
            except Exception:
                return "[unprintable]"

        async for dialog in client.iter_dialogs():
            if dialog.unread_count and dialog.unread_count > 0:
                entity = dialog.entity
                name = dialog.name or "Unknown"
                if normalize_name(name) in ignored_groups:
                    print(f"\n[{safe_console(name)}] — skipped (in ignored_groups.txt)")
                    continue
                print(f"\n[{safe_console(name)}] ({dialog.unread_count} unread, checking last 2 days)")

                included_messages = []

                # Telethon iterates newest->oldest by default; once we go older than `since`,
                # we can stop fetching further messages for this dialog.
                async for msg in client.iter_messages(entity, limit=dialog.unread_count):
                    msg_dt = to_utc(msg.date)
                    if msg_dt < since:
                        break

                    included_messages.append(msg)

                if not included_messages:
                    continue

                all_lines.append(f"===== {name} =====")
                channel_podcast_msgs: list[tuple[str, str]] = []

                # Write in chronological order (oldest -> newest)
                for msg in reversed(included_messages):
                    sender = "Unknown"
                    try:
                        if msg.sender:
                            sender = (
                                getattr(msg.sender, "first_name", "") or getattr(msg.sender, "title", "Unknown")
                            )
                    except Exception:
                        pass

                    text = getattr(msg, "message", None) or getattr(msg, "text", None) or ""
                    text = text.strip() if isinstance(text, str) else ""
                    if not text:
                        text = "[Media / Non-text message]"

                    clean_text = clean_for_tts(text)
                    date_str = to_utc(msg.date).strftime("%Y-%m-%d %H:%M:%S UTC")
                    all_lines.append(f"[{date_str}] {sender}: {clean_text}")
                    channel_podcast_msgs.append((sender, clean_text))
                    total_saved += 1

                if channel_podcast_msgs:
                    messages_by_channel[name] = channel_podcast_msgs

                all_lines.append("")  # blank line between chats

                # Mark ONLY the messages we included as read.
                try:
                    # Telethon marks reads using `max_id`, which effectively marks a range.
                    # We use the newest message ID we included, so the chat's unread tail is cleared.
                    max_id = max(msg.id for msg in included_messages)
                    await client.send_read_acknowledge(entity, max_id=max_id)
                except Exception as e:
                    print(f"Warning: could not mark messages as read for '{safe_console(name)}': {e}")

        if total_saved == 0:
            print("\nNo unread messages from the last 2 days.")
            all_lines.append("No unread messages from the last 2 days.")
        else:
            print(f"\nDone. Saved {total_saved} message(s) to: {output_path.name}")

        output_path.write_text("\n".join(all_lines), encoding="utf-8")

        # Generate podcast and send to phone (skipped when --no-podcast is passed)
        if messages_by_channel and not no_podcast:
            base_dir = Path(__file__).resolve().parent
            await generate_and_send_podcast(client, messages_by_channel, timestamp, base_dir)

    finally:
        await client.disconnect()


if __name__ == "__main__":
    import sys
    import traceback

    parser = argparse.ArgumentParser()
    parser.add_argument("--no-podcast", action="store_true", help="Fetch only; skip MP3 generation")
    args = parser.parse_args()

    log_dir = Path(__file__).resolve().parent / "logs"
    log_dir.mkdir(exist_ok=True)

    try:
        asyncio.run(main(no_podcast=args.no_podcast))
    except Exception:
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        err_path = log_dir / f"error_{ts}.log"
        err_path.write_text(traceback.format_exc(), encoding="utf-8")
        print(f"ERROR — see {err_path}", file=sys.stderr)
        sys.exit(1)