"""
SMS/RCS Backup Processor
========================
Reads SMS Backup & Restore XML files from Google Drive and organizes
messages by phone number into Recoll-indexable markdown files.

Input:  /input/<user>/*.xml  (SMS Backup & Restore export format)
Output: /output/<user>/<phone-number-or-name>.md

Each .md file contains chronological messages from that contact:
    ## +15551234567 (Mom) — 2026-08-01 14:23:00
    **Inbox** — RCS
    Hey, running late tonight!

    ---

    ## +15551234567 (Mom) — 2026-08-01 14:25:00
    **Sent** — SMS
    No worries, I'll be here a while.

State (processed files) persisted to /output/.processed.json so
re-starts don't re-process everything.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("sms-processor")

# ---------------------------------------------------------------------------
# Configurable via env vars (or CLI args)
# ---------------------------------------------------------------------------
INPUT_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/input")
OUTPUT_DIR = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("/output")
POLL_SECONDS = int(os.environ.get("POLL_INTERVAL", sys.argv[3] if len(sys.argv) > 3 else "300"))
STATE_FILE = OUTPUT_DIR / ".processed.json"


# ---------------------------------------------------------------------------
# State management — track which XML files we've already processed
# ---------------------------------------------------------------------------
def load_state() -> dict[str, str]:
    """Return {relative_path: md5} of already-processed files."""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            log.warning("Corrupt state file, starting fresh")
            return {}
    return {}


def save_state(state: dict[str, str]) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def file_hash(path: Path) -> str:
    h = hashlib.md5()
    h.update(path.read_bytes())
    return h.hexdigest()


# ---------------------------------------------------------------------------
# XML parsing — SMS Backup & Restore format
# ---------------------------------------------------------------------------
SMS_TYPE_MAP = {
    "1": "Inbox",
    "2": "Sent",
    "3": "Failed",
    "4": "Draft",
}

TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}-\d{2}-\d{2}$")


def parse_timestamp(value: str) -> datetime | None:
    """Parse SMS Backup & Restore timestamp like '2026-08-01 14:23:00'."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=UTC
        )
    except ValueError:
        pass
    # Fallback: epoch milliseconds
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC)
    except (ValueError, OSError, OverflowError):
        return None


def extract_contact_name(elem: ET.Element) -> str | None:
    """Try to find the contact name from <person> attribute or sub-element."""
    person = elem.get("person")
    if person and person.strip():
        return person.strip()
    # Some exports put it in a child element
    for child in elem:
        tag = child.tag.split("}")[-1]  # strip namespace
        if tag == "person" and (text := (child.text or "").strip()):
            return text
    return None


def extract_protocol(elem: ET.Element) -> str | None:
    """Extract protocol (SMS/RCS) from <protocol> sub-element."""
    for child in elem:
        tag = child.tag.split("}")[-1]
        if tag == "protocol" and (text := (child.text or "").strip()):
            return text
    # Fallback: check attribute
    proto = elem.get("protocol")
    if proto:
        return proto
    return None


def extract_sms_messages(root: ET.Element) -> list[dict[str, Any]]:
    """Parse <sms> elements from a backup file."""
    messages: list[dict[str, Any]] = []
    for elem in root:
        tag = elem.tag.split("}")[-1]
        if tag not in ("sms", "mms"):
            continue

        address_raw = (elem.get("address") or "").strip()
        if not address_raw:
            continue

        # Normalize: keep as-is (already includes +countrycode)
        address = address_raw

        date_str = elem.get("date") or ""
        ts = parse_timestamp(date_str)

        type_label = SMS_TYPE_MAP.get(elem.get("type", ""), "Unknown")

        body_parts: list[str] = []
        protocol = extract_protocol(elem)
        service_center = None
        subject = None

        for child in elem:
            child_tag = child.tag.split("}")[-1]
            if child_tag == "body" and (text := (child.text or "").strip()):
                body_parts.append(html.unescape(text))
            elif child_tag == "sc":
                service_center = child.get("address")
            elif child_tag == "subject" and (text := (child.text or "").strip()):
                subject = text

        # For MMS, extract parts (attachments reference)
        attachments: list[dict[str, str]] = []
        for child in elem:
            child_tag = child.tag.split("}")[-1]
            if child_tag == "part":
                part_type = child.get("ct") or ""
                part_name = child.get("name") or child.get("tn", "")
                part_loc = child.get("loc") or (child.text or "")
                if part_loc:
                    attachments.append(
                        {"type": part_type, "name": part_name, "location": part_loc}
                    )

        body = "\n".join(body_parts) if body_parts else "(empty message)"
        contact = extract_contact_name(elem)

        messages.append(
            {
                "address": address,
                "timestamp": ts,
                "date_str": date_str,
                "type": type_label,
                "body": body,
                "protocol": protocol,
                "contact": contact,
                "service_center": service_center,
                "subject": subject,
                "attachments": attachments,
                "is_mms": tag == "mms",
            }
        )

    return messages


# ---------------------------------------------------------------------------
# Output — organize by phone number into markdown files
# ---------------------------------------------------------------------------
def contact_key(address: str, contact_name: str | None) -> tuple[str, str]:
    """Return (filename-safe key, display name)."""
    if contact_name:
        # Use "name (+15551234567)" format
        safe = re.sub(r"[^a-zA-Z0-9_\-\+]", "", address)
        return (f"{contact_name} ({safe})", contact_name)
    safe = re.sub(r"[^a-zA-Z0-9_\-\+]", "", address)
    return (safe, address)


def process_xml_file(
    xml_path: Path, output_base: Path, user_label: str
) -> list[str]:
    """
    Process a single XML backup file.
    Returns list of contact keys that were updated.
    """
    log.info("Processing %s", xml_path.name)

    try:
        tree = ET.parse(xml_path)
    except ET.ParseError as e:
        log.error("Failed to parse %s: %s", xml_path.name, e)
        return []

    root = tree.getroot()
    messages = extract_sms_messages(root)
    if not messages:
        log.info("  No SMS/MMS messages found in %s", xml_path.name)
        return []

    # Group by address
    groups: dict[str, list[dict[str, Any]]] = {}
    for msg in messages:
        key, _ = contact_key(msg["address"], msg["contact"])
        groups.setdefault(key, []).append(msg)

    user_dir = output_base / user_label
    user_dir.mkdir(parents=True, exist_ok=True)
    updated: list[str] = []

    for key, msgs in groups.items():
        display = key
        if " (" in key and key.endswith(")"):
            display = key.split(" (", 1)[0]

        # Sort by timestamp
        msgs.sort(
            key=lambda m: m["timestamp"]
            or datetime.min.replace(tzinfo=UTC)
        )

        md_path = user_dir / f"{key}.md"

        # Build markdown content for this batch
        new_content = _build_md_content(msgs, display)

        # Append to file (dedup handled by XML-level state tracking)
        with open(md_path, "a") as f:
            if md_path.exists() and md_path.stat().st_size > 0:
                f.write("\n---\n\n")
            f.write(new_content)

        updated.append(key)
        log.info("  Updated %s (+%d messages)", md_path.name, len(msgs))

    return updated


def _build_md_content(
    msgs: list[dict[str, Any]], display_name: str
) -> str:
    """Build markdown text for a batch of messages."""
    lines: list[str] = []
    for msg in msgs:
        ts_str = (
            msg["timestamp"].strftime("%Y-%m-%d %H:%M:%S UTC")
            if msg["timestamp"]
            else msg.get("date_str", "Unknown date")
        )
        protocol = f" ({msg['protocol']})" if msg.get("protocol") else ""
        mms_badge = " [MMS]" if msg.get("is_mms") else ""

        lines.append(f"## {display_name} — {ts_str}")
        lines.append(f"**{msg['type']}**{protocol}{mms_badge}")

        if msg.get("subject"):
            lines.append(f"*Subject: {html.escape(msg['subject'])}*")

        # Body
        body = html.escape(msg["body"])
        body = body.replace("\n", "\n\n")
        lines.append("")
        lines.append(body)

        # Attachments
        if msg.get("attachments"):
            lines.append("")
            lines.append("**Attachments:**")
            for att in msg["attachments"]:
                name = att.get("name") or "unknown"
                loc = att.get("location") or ""
                atype = att.get("type") or ""
                lines.append(f"- `{name}` ({atype}) — {html.escape(loc)}")

        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main loop — scan input dirs, process new XML files
# ---------------------------------------------------------------------------
def scan_and_process() -> int:
    """Scan INPUT_DIR for user subdirs and process any new XML files."""
    state = load_state()
    processed_count = 0

    if not INPUT_DIR.exists():
        log.warning("Input directory %s does not exist", INPUT_DIR)
        return 0

    for user_dir in sorted(INPUT_DIR.iterdir()):
        if not user_dir.is_dir():
            continue

        user_label = user_dir.name
        for xml_file in sorted(user_dir.glob("*.xml")):
            rel = str(xml_file.relative_to(INPUT_DIR))
            current_hash = file_hash(xml_file)
            saved_hash = state.get(rel)

            if saved_hash == current_hash:
                continue

            process_xml_file(xml_file, OUTPUT_DIR, user_label)
            state[rel] = current_hash
            processed_count += 1

    if processed_count:
        save_state(state)

    return processed_count


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    log.info("=" * 60)
    log.info("SMS/RCS Backup Processor")
    log.info("Input:  %s", INPUT_DIR)
    log.info("Output: %s", OUTPUT_DIR)
    log.info("Poll:   every %ds", POLL_SECONDS)
    log.info("=" * 60)

    # Initial run
    count = scan_and_process()
    if count:
        log.info("Initial run: processed %d file(s)", count)
    else:
        log.info("Initial run: no new files to process")

    # Poll loop
    iteration = 0
    while True:
        time.sleep(POLL_SECONDS)
        iteration += 1
        try:
            count = scan_and_process()
            if count:
                log.info("Poll #%d: processed %d new file(s)", iteration, count)
            else:
                log.debug("Poll #%d: no changes", iteration)
        except Exception:
            log.exception("Poll #%d encountered an error", iteration)


if __name__ == "__main__":
    main()
