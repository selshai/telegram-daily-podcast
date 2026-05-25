"""
Summarize today's Telegram messages using a Nebius-hosted Gemma LLM.

Reads all unread_<today>_*.txt files, sends the content to the LLM,
and writes summary_YYYY-MM-DD_HH-MM-SS.txt with tech and general news
separated into Hebrew bullet-point sections.

Run after main.py (or run.bat) has fetched today's messages.
Requires NEBIUS_API_KEY in .env.
"""

import argparse
import asyncio
import os
import re
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

from openai import AsyncOpenAI

import config

_ENV_LINE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$")
_MSG_LINE_RE = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC)\] ([^:]+): (.+)$"
)

MAX_MSG_CHARS = 500


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


def _parse_files(files: list[Path]) -> dict[str, list[tuple[datetime, str, str]]]:
    """Parse a list of unread_*.txt files into {channel: [(dt, sender, text)]}."""
    seen: set[tuple[str, str, str, str]] = set()
    channels: dict[str, list[tuple[datetime, str, str]]] = {}
    current_channel = None

    for filepath in files:
        for line in filepath.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("=====") and line.endswith("====="):
                current_channel = line.strip("= ").strip()
                channels.setdefault(current_channel, [])
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


def collect_today_messages(folder: Path) -> dict[str, list[tuple[datetime, str, str]]]:
    """Parse all unread_<today>_*.txt files. Returns {channel: [(dt, sender, text)]}."""
    today_str = datetime.now().strftime("%Y-%m-%d")
    files = sorted(folder.glob(f"unread_{today_str}_*.txt"))
    if not files:
        return {}
    print(f"Found {len(files)} file(s) for today ({today_str}).")
    return _parse_files(files)


def build_llm_prompt(channels: dict, date_str: str) -> str:
    """Build the summarization prompt from collected messages."""
    content_parts: list[str] = []
    for channel_name, msgs in channels.items():
        text_msgs = [(s, t) for _, s, t in msgs if t != "[Media / Non-text message]"]
        if not text_msgs:
            continue
        content_parts.append(f"=== {channel_name} ===")
        for sender, text in text_msgs:
            snippet = text[:MAX_MSG_CHARS] + ("..." if len(text) > MAX_MSG_CHARS else "")
            content_parts.append(f"- {snippet}")
        content_parts.append("")

    raw_content = "\n".join(content_parts)

    # Trim to stay within model context limits
    if len(raw_content) > config.MAX_LLM_INPUT_CHARS:
        raw_content = raw_content[: config.MAX_LLM_INPUT_CHARS] + "\n[... truncated]"

    return f"""Below are raw messages collected from Israeli Telegram news channels on {date_str}.

You are a professional Israeli radio news anchor preparing a news bulletin.
Your job is NOT to summarize — your job is to write a broadcast-ready Hebrew news bulletin based on the facts in these messages.

Rules:
1. Report only verified facts. Do not add interpretation, opinion, or filler.
2. Completely ignore advertisements, promotional content, channel self-promotion, and calls to action.
3. Ignore duplicate stories — report each event once, using the most complete version.
4. Classify each item as:
   - חדשות כלליות: politics, security, military, economy, society, world news
   - טכנולוגיה: AI, startups, software, hardware, cybersecurity, science & tech
5. Within each section, order by importance (most significant first).
6. Each bullet is 1-2 sentences, written in clear broadcast Hebrew (not conversational).

Output ONLY the two sections below, with no preamble or closing remarks:

## חדשות כלליות

• [news item]
• [news item]

## חדשות טכנולוגיה

• [news item]
• [news item]

Messages:
---
{raw_content}
---"""


async def call_llm(prompt: str) -> str:
    api_key = os.environ.get("NEBIUS_API_KEY")
    if not api_key:
        raise RuntimeError(
            "NEBIUS_API_KEY not found in .env or environment. "
            "Add NEBIUS_API_KEY=... to your .env file."
        )

    client = AsyncOpenAI(api_key=api_key, base_url=config.NEBIUS_BASE_URL)

    print(f"Sending {len(prompt):,} chars to {config.NEBIUS_MODEL}...")
    response = await client.chat.completions.create(
        model=config.NEBIUS_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a professional Israeli radio news anchor. "
                    "You write broadcast-ready Hebrew news bulletins based strictly on reported facts. "
                    "You never add opinions, filler, or promotional content. "
                    "You respond only in Hebrew and follow the exact output format requested."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        max_tokens=4096,
        temperature=0.3,
    )
    return response.choices[0].message.content.strip()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", help="Path to a specific unread_*.txt to summarize (default: all of today's files)")
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parent
    load_dotenv(base_dir / ".env")

    if args.file:
        target = Path(args.file)
        if not target.is_absolute():
            target = base_dir / target
        if not target.exists():
            print(f"ERROR: file not found: {target}", file=sys.stderr)
            sys.exit(1)
        print(f"Summarizing single file: {target.name}")
        channels = _parse_files([target])
    else:
        channels = collect_today_messages(base_dir)

    if not channels:
        print("No messages found. Run main.py (or run.bat) first, or pass --file <path>.")
        return

    total = sum(len(v) for v in channels.values())
    now_il    = datetime.now(timezone(timedelta(hours=3)))  # Israel time (IDT = UTC+3)
    date_str  = now_il.strftime("%d/%m/%Y")
    time_str  = now_il.strftime("%H:%M")
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    print(f"{len(channels)} channels, {total} messages to summarize.")

    prompt = build_llm_prompt(channels, date_str)
    summary_body = await call_llm(prompt)

    header = (
        f"קול ישראל מירושלים שלום רב, השעה {time_str} "
        f"והרי חדשות הטלגרם מפי מלאכי חזקיה.\n\n"
    )
    out_path = base_dir / f"summary_{timestamp}.txt"
    out_path.write_text(header + summary_body, encoding="utf-8")
    print(f"Summary saved: {out_path.name}")


if __name__ == "__main__":
    log_dir = Path(__file__).resolve().parent / "logs"
    log_dir.mkdir(exist_ok=True)

    try:
        asyncio.run(main())
    except Exception:
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        err_path = log_dir / f"error_summarize_{ts}.log"
        err_path.write_text(traceback.format_exc(), encoding="utf-8")
        print(f"ERROR — see logs/error_summarize_{ts}.log", file=sys.stderr)
        sys.exit(1)
