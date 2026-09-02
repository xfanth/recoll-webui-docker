"""TrueNAS Recoll indexing wrapper.

Runs recollindex inside a Docker container with full diagnostics,
concurrency locking, and coloured logging to both console and file.

Logging conventions (enforced by this module):

* Every log record goes through ``logging`` + ``rich.logging.RichHandler``
  — the builtin ``print`` is banned for this codebase (ruff T201).
* The terminal handler renders on ``stderr`` with colours, markup and
  rich tracebacks. A second handler, following the pattern from
  https://github.com/Textualize/rich/discussions/1309
  (``Console(file=open(...))`` + ``RichHandler(console=console)``),
  mirrors every record into a plain-text log file at DEBUG level.
* Each run gets its own log file,
  ``.recoll/recoll_wrapper/logs/<YYYY-MM-DD_HHMMSS>.log`` (under
  ``$RECOLL_BASE_PATH/app-data/recoll/.recoll/``); run logs older than
  30 days are pruned at start-up.
* High-volume child output (per-file indexing lines) is logged at DEBUG,
  so it reaches only the log file by default — pass ``-v`` to stream it
  to the terminal as well.
* Dynamic (user/tool-derived) strings are escaped with
  :func:`rich.markup.escape` before logging, because the handlers parse
  markup — literal ``[...]`` in tool output must not be treated as tags.
* Structured reports are rendered as rich ``Table``/``Panel`` objects on
  the display console; each row is additionally logged at DEBUG so the
  log file carries a full audit trail (visible on the terminal with
  ``-v``).

Usage:
    uv run python recollindex.py            # incremental (file-diff) index
    uv run python recollindex.py --rebuild  # full rebuild (removes existing index)
    uv run python recollindex.py -v         # DEBUG logs on the console too
"""

from __future__ import annotations

import argparse
import fcntl
import logging
import math
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from rich.console import Console
from rich.logging import RichHandler
from rich.markup import escape as _escape_markup
from rich.panel import Panel
from rich.progress import (
    Progress,
    ProgressColumn,
    SpinnerColumn,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from rich.text import Text

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CONTAINER = "recoll-engine"
BASE_PATH = Path(os.environ.get("RECOLL_BASE_PATH", "/mnt/shuttle/share"))
CONFIG_FILE = BASE_PATH / "app-data/recoll/.recoll/recoll.conf"
# One log file per run: .recoll/recoll_wrapper/logs/<YYYY-MM-DD_HHMMSS>.log
LOG_DIR = CONFIG_FILE.parent / "recoll_wrapper" / "logs"
INDEX_PATH = "/root/.recoll/xapiandb"
# /tmp via gettempdir() so bandit S108 (hardcoded insecure temp path) stays green
LOCK_FILE = Path(tempfile.gettempdir()) / "recollindex-wrapper.lock"
DATASETS_OF_INTEREST = ("lambo/share", "shuttle/share")

# ---------------------------------------------------------------------------
# Logging setup — Rich handlers to BOTH terminal and log file.
# Terminal: colours + tracebacks on stderr (keeps stdout clean for tools).
# File: the two-line pattern from rich discussion #1309, one file per run
# at DEBUG level so the file is a complete audit trail while the console
# stays readable.
# ---------------------------------------------------------------------------

RUN_LOG_RETENTION_DAYS = 30


def _run_log_file() -> Path:
    """Return this run's log path: ``<LOG_DIR>/<YYYY-MM-DD_HHMMSS>.log``."""
    return LOG_DIR / f"{time.strftime('%Y-%m-%d_%H%M%S')}.log"


def _prune_old_logs(
    directory: Path, max_age_days: int = RUN_LOG_RETENTION_DAYS
) -> None:
    """Delete ``*.log`` files in *directory* older than *max_age_days*.

    Best-effort housekeeping: any individual failure is ignored so a
    wedged log directory never blocks an indexing run.
    """
    cutoff = time.time() - max_age_days * 86400
    try:
        entries = list(directory.glob("*.log"))
    except OSError:
        return
    for entry in entries:
        try:
            if entry.stat().st_mtime < cutoff:
                entry.unlink()
        except OSError:
            pass


def _open_log_file() -> tuple[TextIO | None, str | None]:
    """Open (creating if needed) this run's log file for append-mode logging.

    Older run logs are pruned first. A missing or unwritable base path is
    not fatal — the wrapper then logs to the terminal only and reports
    the problem at warning level.

    Returns:
        Tuple of ``(file_or_none, error_message_or_none)``.
    """
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        _prune_old_logs(LOG_DIR)
        return _run_log_file().open("a", encoding="utf-8"), None
    except OSError as exc:
        return None, f"{exc}"


_log_file, _log_file_error = _open_log_file()

# Display surface for the terminal: tables, panels, progress bars, prompts.
console = Console(stderr=True)

_term_handler = RichHandler(
    console=console,
    markup=True,
    rich_tracebacks=True,
    tracebacks_show_locals=True,
    log_time_format="[%X]",
    show_path=False,
)
_term_handler.setLevel(logging.INFO)

_file_handler: RichHandler | None = None
if _log_file is not None:
    # rich discussion #1309: Console(file=...) + RichHandler(console=console)
    file_console = Console(file=_log_file, soft_wrap=True)
    _file_handler = RichHandler(
        console=file_console,
        markup=True,
        rich_tracebacks=True,
        show_path=False,
        log_time_format="[%X]",
    )
    _file_handler.setLevel(logging.DEBUG)

logging.getLogger().setLevel(logging.DEBUG)
logging.getLogger().addHandler(_term_handler)
if _file_handler is not None:
    logging.getLogger().addHandler(_file_handler)

log = logging.getLogger(__name__)

if _log_file_error is not None:
    log.warning(
        "Run logs in %s unavailable (%s) — logging to terminal only.",
        LOG_DIR,
        _escape_markup(_log_file_error),
    )


def set_console_verbosity(debug: bool) -> None:
    """Raise or lower the *terminal* handler level (file stays at DEBUG)."""
    if debug:
        _term_handler.setLevel(logging.DEBUG)
    else:
        _term_handler.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run_cmd(*args: str, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    """Run a command. Diagnostics never abort the script."""
    try:
        # S603 is accepted here: callers pass fixed internal tool names
        # (docker/zpool/lsblk...), never untrusted user input.
        return subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        timeout_message = f"Timed out after {timeout}s"
        return subprocess.CompletedProcess[str](args, -1, "", timeout_message)
    except FileNotFoundError:
        missing_message = f"Command not found: {args[0]}"
        return subprocess.CompletedProcess[str](args, 127, "", missing_message)


def pretty_duration(seconds: float) -> str:
    """Format seconds as ``HHh MMm SSs`` (non-finite or negative clamp to zero)."""
    if not math.isfinite(seconds) or seconds < 0:
        return "00h 00m 00s"
    total = int(seconds)
    h, m, s = total // 3600, (total % 3600) // 60, total % 60
    return f"{h:02d}h {m:02d}m {s:02d}s"


def _log_rows(title: str, rows: Sequence[tuple[str, ...]]) -> None:
    """Log every row of a report at DEBUG (file audit trail / -v console)."""
    for row in rows:
        log.debug("%s | %s", title, " | ".join(_escape_markup(cell) for cell in row))


def _report_table(
    title: str, header: tuple[str, ...], rows: Sequence[tuple[str, ...]]
) -> None:
    """Render a rich ``Table`` on the display console and log its rows.

    Args:
        title: Panel/table title (e.g. "Container status").
        header: Column headers.
        rows: Cell contents per row; dynamic text is markup-escaped.
    """
    table = Table(title=title, header_style="bold cyan")
    for col in header:
        table.add_column(col)
    for row in rows:
        table.add_row(*(_escape_markup(cell) for cell in row))
    console.print(table)
    _log_rows(title, rows)


def _report_panel(title: str, body: str) -> None:
    """Render preformatted tool output inside a rich ``Panel`` and log it."""
    console.print(Panel(Text(_escape_markup(body), style="dim"), title=title))
    for line in body.strip().splitlines():
        log.debug("%s | %s", title, _escape_markup(line))


def _log_child_line(name: str, line: str) -> None:
    """Log one streamed child-output line at its proper level."""
    log.log(_child_level(name, line), "recollindex %s: %s", name, _escape_markup(line))


def _drain_remaining(line_queue: queue.Queue[_LineItem], tail: deque[str]) -> int:
    """Drain child lines queued just before the process ended.

    Args:
        line_queue: Queue filled by the reader threads.
        tail: Bounded ring buffer of recent child output (for failures).

    Returns:
        Number of additional lines logged during the drain.
    """
    seen = 0
    while True:
        try:
            name, line = line_queue.get(timeout=0.2)
        except queue.Empty:
            break
        if line is None:
            continue
        seen += 1
        tail.append(f"{name}: {line}")
        _log_child_line(name, line)
    return seen


def _child_level(name: str, line: str) -> int:
    """Map a streamed child-output line to its log level.

    stderr lines that look like errors are surfaced at WARNING; all other
    streamed output is logged at DEBUG so the hundreds of thousands of
    per-file indexing lines stay out of the console (they still reach
    the log file; ``-v`` streams them to the terminal as well).
    """
    if name == "stderr" and line.lower().startswith(("error", "fail")):
        return logging.WARNING
    return logging.DEBUG


def _print_cmd_output(
    label: str, result: subprocess.CompletedProcess[str], logger: logging.Logger
) -> None:
    """Print command output, handling failures gracefully.

    On TrueNAS, some host utilities are missing or return
    ``Function not implemented``. This helper logs stdout on success
    and a generic (unavailable) placeholder on failure instead of
    dumping stderr as data.
    """
    if result.returncode == 0 and result.stdout.strip():
        for line in result.stdout.strip().splitlines():
            logger.info("  %s", _escape_markup(line))
    elif result.returncode != 0:
        logger.debug("(unavailable)")


def _update_progress(
    progress: Progress,
    task_id: TaskID,
    seen: int,
    total_lines: int | None,
    start: float,
) -> None:
    """Refresh the progress bar's description, iteration rate and ETA."""
    elapsed = time.monotonic() - start
    rate = seen / elapsed if elapsed > 0 else 0.0
    eta = (
        pretty_duration((total_lines - seen) / rate)
        if total_lines is not None and rate > 0
        else "n/a"
    )
    progress.update(
        task_id,
        advance=1 if total_lines is not None else 0,
        description=f"Indexing... ({pretty_duration(elapsed)})",
        rate=f"{rate:.1f} it/s",
        eta=f"ETA {eta}",
    )


def _print_section(title: str) -> None:
    log.info("═══ %s ═══", title)


def _print_subsection(title: str) -> None:
    log.info("─── %s ───", title)


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


def container_diagnostics(label: str) -> None:
    """Print Docker / container health information.

    Args:
        label: Prefix for the diagnostic section (e.g., "Initial", "Post-index").
    """
    _print_subsection(f"{label}: Container diagnostics")

    # Container status table
    result = run_cmd(
        "docker",
        "ps",
        "--filter",
        f"name={CONTAINER}",
        "--format",
        "table {{.Names}}\t{{.Status}}\t{{.Image}}",
    )
    rows: list[tuple[str, ...]] = []
    for line in result.stdout.strip().splitlines():
        parts = line.split(None, 2)
        if len(parts) >= 3:
            rows.append(tuple(parts))
    _report_table("Container status", ("Name", "Status", "Image"), rows)

    # Container image
    result = run_cmd(
        "docker",
        "inspect",
        CONTAINER,
        "--format",
        "{{.Config.Image}}",
    )
    log.info("Container image: %s", _escape_markup(result.stdout.strip()))

    # Recoll version
    result = run_cmd(
        "docker", "exec", CONTAINER, "sh", "-c", "recollindex -h 2>&1 | head -3"
    )
    log.info("Recoll version:")
    for line in result.stdout.strip().splitlines():
        log.info("  %s", _escape_markup(line))
    if result.stderr.strip():
        log.debug("%s", _escape_markup(result.stderr.strip()))

    # Index size
    result = run_cmd(
        "docker", "exec", CONTAINER, "sh", "-c", f"du -sh {INDEX_PATH} 2>/dev/null"
    )
    for line in result.stdout.strip().splitlines():
        log.info("Index size: %s", _escape_markup(line.strip()))

    # Container resources (CPU, memory, network, I/O)
    result = run_cmd("docker", "stats", CONTAINER, "--no-stream")
    if result.returncode == 0 and result.stdout.strip():
        _report_panel(f"{label}: docker stats", result.stdout.strip())
    else:
        log.debug("Docker stats unavailable")

    # Recoll processes inside container
    log.info("Existing Recoll processes:")
    result = run_cmd(
        "docker",
        "exec",
        CONTAINER,
        "sh",
        "-c",
        "ps -eo pid,comm,args | grep -E 'recoll(index)?|rcl' | grep -v grep",
    )
    if result.stdout.strip():
        for line in result.stdout.strip().splitlines():
            log.info("  %s", _escape_markup(line))
    else:
        log.debug("(none)")


def storage_diagnostics(label: str) -> None:
    """Print ZFS, disk, and kernel-level storage information.

    Runs on the TrueNAS host (not inside the container).

    Args:
        label: Prefix for the diagnostic section.
    """
    _print_subsection(f"{label}: Storage diagnostics")

    log.info(
        "[bold yellow]NOTE:[/] zpool/zfs diagnostics run on the TrueNAS "
        "host, not inside the Recoll container."
    )

    # ZFS pools ----------------------------------------------------------
    _print_cmd_output("zpool status", run_cmd("zpool", "status"), log)

    # Selected ZFS datasets (only the ones we care about) ----------------
    result = run_cmd(
        "zfs",
        "list",
        "-H",
        "-o",
        "name,used,available,referenced,mountpoint",
    )
    dataset_rows: list[tuple[str, ...]] = []
    if result.returncode == 0:
        for line in result.stdout.strip().splitlines():
            parts = line.split("\t")
            if len(parts) >= 5 and parts[0] in DATASETS_OF_INTEREST:
                dataset_rows.append(tuple(parts[:5]))
        _report_table(
            f"{label}: Selected ZFS datasets",
            ("NAME", "USED", "AVAIL", "REFER", "MOUNTPOINT"),
            dataset_rows,
        )
    else:
        log.warning("ZFS datasets unavailable")

    # ZFS ARC cache stats ------------------------------------------------
    arc_path = Path("/proc/spl/kstat/zfs/arcstats")
    if arc_path.exists():
        try:
            text = arc_path.read_text()
            for line in text.splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[1] in (
                    "size",
                    "c_min",
                    "c_max",
                    "hits",
                    "misses",
                ):
                    log.debug("ARC | %s %s", _escape_markup(parts[1]), parts[2])
        except OSError:
            log.error("Could not read ARC stats")
    else:
        log.warning("ARC stats unavailable")

    # Filesystem usage ---------------------------------------------------
    dataset_mounts = {f"/mnt/{ds.split('/')[0]}/share" for ds in DATASETS_OF_INTEREST}
    _print_cmd_output("df", run_cmd("df", "-h", *dataset_mounts), log)

    # Block devices ------------------------------------------------------
    result = run_cmd(
        "lsblk",
        "-o",
        "NAME,SIZE,MODEL,SERIAL,FSTYPE,MOUNTPOINT",
    )
    if result.returncode == 0 and result.stdout.strip():
        _report_panel(f"{label}: Block devices", result.stdout.strip())
    else:
        log.debug("Block devices unavailable")

    # PCI storage adapters -----------------------------------------------
    result = run_cmd("lspci")
    if result.returncode == 0:
        pci_rows = [
            (line,)
            for line in result.stdout.strip().splitlines()
            if re.search(
                r"sata|ahci|raid|sas|lsi|marvell|asm|asmedia|usb",
                line,
                re.IGNORECASE,
            )
        ]
        _report_table(f"{label}: PCI storage adapters", ("Adapter",), pci_rows)
    else:
        log.debug("lspci not available")

    # SMART devices ------------------------------------------------------
    _print_cmd_output("smartctl", run_cmd("smartctl", "--scan-open"), log)

    # Recent kernel storage messages -------------------------------------
    result = run_cmd(
        "sh",
        "-c",
        'dmesg | grep -Ei "ata|ahci|sas|scsi|usb|reset|timeout|error|failed|link|crc" '
        "| tail -100",
    )
    if result.returncode == 0 and result.stdout.strip():
        for line in result.stdout.strip().splitlines():
            log.debug("%s", _escape_markup(line))
    else:
        log.debug("Kernel storage messages unavailable")


def print_configuration() -> None:
    """Print relevant Recoll configuration values."""
    _print_subsection("Configuration")

    config_path = CONFIG_FILE
    if not config_path.exists():
        log.error("Missing config file: %s", CONFIG_FILE)
        return

    keys_of_interest: set[str] = {
        "topdirs",
        "dbdir",
        "indexstemminglanguages",
        "indexallfilenames",
        "loglevel",
        "maxfsmbexp",
        "storeAllExtraDbFields",
        "usesystemhacks",
    }
    config_rows: list[tuple[str, ...]] = []
    try:
        for line in config_path.read_text().splitlines():
            stripped = line.strip()
            if "=" in stripped and not stripped.startswith("#"):
                key = stripped.split("=")[0].strip()
                if key in keys_of_interest:
                    value = stripped.split("=", 1)[1].strip()
                    config_rows.append((key, value))
    except OSError as exc:
        log.error("Could not read config: %s", exc)
        return

    _report_table(f"{CONFIG_FILE}", ("Key", "Value"), config_rows)


def check_existing_indexers() -> bool:
    """Check whether recollindex is already running inside the container.

    Returns:
        True if an indexer is already running (i.e., we should abort).
    """
    result = run_cmd(
        "docker",
        "exec",
        CONTAINER,
        "sh",
        "-c",
        "pgrep -x recollindex | wc -l",
    )
    count_text = result.stdout.strip()
    count = int(count_text) if count_text.isascii() and count_text.isdigit() else 0

    log.info("Checking existing Recoll indexers...")
    log.info("Existing recollindex processes: %d", count)

    if count > 0:
        log.error("recollindex is already running.")
        log.error("Refusing to start another indexer.")
        return True
    return False


# ---------------------------------------------------------------------------
# Full rebuild confirmation
# ---------------------------------------------------------------------------


def confirm_rebuild() -> bool:
    """Ask the user for y/N confirmation before a full rebuild."""
    log.warning("[bold red]WARNING:[/] This will completely rebuild the Recoll index.")
    log.warning("This may take a long time.")

    # console.input() renders on the terminal display surface — the prompt
    # must never be swallowed into the log file.
    answer = console.input("Continue? [y/N] ").strip().lower()
    if answer in ("y", "yes"):
        log.info("Starting full rebuild...")
        return True
    log.warning("Cancelled.")
    return False


# ---------------------------------------------------------------------------
# Run recollindex with live progress
# ---------------------------------------------------------------------------

# A child-output line queued between a reader thread and the main loop:
# (stream name, line text or None as end-of-stream sentinel).
_LineItem = tuple[str, str | None]


def _stream_lines(stream: TextIO, name: str, out_queue: queue.Queue[_LineItem]) -> None:
    """Copy a subprocess stream into ``out_queue`` line by line.

    Args:
        stream: The pipe end to read (stdout or stderr).
        name: Stream label stored with each queued line.
        out_queue: Queue drained by the caller's progress loop.
    """
    for line in stream:
        out_queue.put((name, line.rstrip("\n")))
    out_queue.put((name, None))


def run_indexing(mode: str, command: list[str], total_lines: int | None = None) -> int:
    """Run recollindex inside the container with a live progress bar.

    Child output is streamed (not captured until exit) so indexing
    activity lands in the log file while a live progress bar keeps the
    run visible on the terminal (``-v`` also streams every line).
    The progress display shows elapsed time, an iteration rate
    (child-output lines per second) and — when ``total_lines`` is known —
    a percentage bar plus estimated time until completion.

    Args:
        mode: Human-readable run label ("INCREMENTAL" / "FULL REBUILD").
        command: recollindex command words executed inside the container.
        total_lines: Optional upper bound of expected child output lines;
            enables the percentage bar and a real ETA.

    Returns:
        The recollindex exit code.
    """
    _print_subsection("Indexing")
    log.info("[bold]Mode:[/] %s", mode)
    log.info("[bold]Command:[/] %s", " ".join(command))

    full_cmd = [
        "docker",
        "exec",
        CONTAINER,
        "sh",
        "-c",
        f"ionice -c 3 nice -n 19 {' '.join(command)}",
    ]

    log.info("Starting: %s", _escape_markup(" ".join(full_cmd)))
    start = time.monotonic()
    try:
        proc = subprocess.Popen(
            full_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
    except FileNotFoundError:
        log.error("Docker CLI not found on this host — cannot run %s", CONTAINER)
        return 127
    if proc.stdout is None or proc.stderr is None:  # defensive: PIPE set above
        log.error("Child pipes are unexpectedly unavailable")
        proc.kill()
        return -1

    line_queue: queue.Queue[_LineItem] = queue.Queue()
    readers = [
        threading.Thread(
            target=_stream_lines, args=(proc.stdout, "stdout", line_queue), daemon=True
        ),
        threading.Thread(
            target=_stream_lines, args=(proc.stderr, "stderr", line_queue), daemon=True
        ),
    ]

    columns: list[ProgressColumn] = [
        SpinnerColumn(spinner_name="arrow"),
        TextColumn("[bold cyan]{task.description}[/]"),
        TextColumn("{task.fields[rate]} [dim]({task.fields[eta]})[/]"),
        TimeElapsedColumn(),
    ]
    if total_lines is not None:
        columns.append(TaskProgressColumn())
        columns.append(TimeRemainingColumn())

    seen = 0
    tail: deque[str] = deque(maxlen=50)
    with Progress(*columns, console=console) as progress:
        # extra kwargs to add_task/update become task.fields entries
        task_id = progress.add_task(
            "Indexing in progress...",
            total=total_lines,
            start=True,
            rate="--",
            eta="ETA n/a",
        )
        for reader in readers:
            reader.start()

        done_streams = 0
        while done_streams < 2 and proc.poll() is None:
            try:
                name, line = line_queue.get(timeout=0.5)
            except queue.Empty:
                pass
            else:
                if line is None:
                    done_streams += 1
                    continue
                seen += 1
                tail.append(f"{name}: {line}")
                _log_child_line(name, line)

            _update_progress(progress, task_id, seen, total_lines, start)

        # Drain whatever the readers queued before their sentinels landed.
        seen += _drain_remaining(line_queue, tail)

        exit_code = proc.wait()

    duration = time.monotonic() - start

    if exit_code != 0 and tail:
        log.error("Last child output lines before failure:")
        for line in tail:
            log.error("  %s", _escape_markup(line))

    status = "SUCCESS" if exit_code == 0 else f"FAILED (exit {exit_code})"
    elapsed = pretty_duration(duration)
    color = "green" if exit_code == 0 else "red"
    log.log(
        logging.INFO if exit_code == 0 else logging.ERROR,
        "[bold %s]Indexing complete:[/] %s in %s (%d lines)",
        color,
        status,
        elapsed,
        seen,
    )

    return exit_code


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Argument words (defaults to ``sys.argv[1:]``).

    Returns:
        Parsed namespace with ``rebuild`` and ``verbose`` flags.
    """
    parser = argparse.ArgumentParser(
        description="Run recollindex in the recoll-engine container "
        "with diagnostics, locking and live progress.",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help=(
            "full rebuild: remove existing index before indexing "
            "(asks for confirmation)"
        ),
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="debug-level logging on the console (file always logs at DEBUG)",
    )
    return parser.parse_args(argv)


def _print_header(
    args: argparse.Namespace, argv_words: list[str], pid: int, hostname: str
) -> None:
    """Render the START banner as a rich table."""
    rows: list[tuple[str, str]] = [
        ("PID", str(pid)),
        ("Hostname", _escape_markup(hostname)),
        ("User", os.environ.get("USER", "unknown")),
        ("Arguments", " ".join(argv_words) or "(none)"),
        ("Time", time.strftime("%a %b %d %X %Z %Y")),
        ("Container", CONTAINER),
        ("Mode", "FULL REBUILD" if args.rebuild else "INCREMENTAL"),
    ]
    _report_table("START", ("Field", "Value"), rows)


def _print_summary(exit_code: int, duration: float, pid: int) -> None:
    """Render the END summary as a rich table."""
    status = "SUCCESS" if exit_code == 0 else f"FAILED (exit {exit_code})"
    style = "bold green" if exit_code == 0 else "bold red"
    _print_section("END")
    log.info("[%s]Exit code:[/] %s", style, status)
    rows: list[tuple[str, str]] = [
        ("PID", str(pid)),
        ("Exit code", status),
        ("Duration", pretty_duration(duration)),
        ("Finished", time.strftime("%a %b %d %X %Z %Y")),
    ]
    _report_table("END", ("Field", "Value"), rows)


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = parse_args(argv)
    set_console_verbosity(args.verbose)
    effective_argv = sys.argv[1:] if argv is None else list(argv)

    start_wall = time.monotonic()
    my_pid = os.getpid()

    # Header
    hostname_result = run_cmd("hostname")
    hostname = (
        hostname_result.stdout.strip() if hostname_result.returncode == 0 else "unknown"
    )
    _print_header(args, effective_argv, my_pid, hostname)

    # Pre-index diagnostics
    container_diagnostics("Initial")
    storage_diagnostics("Initial")
    print_configuration()

    # Guard: don't start a second indexer
    if check_existing_indexers():
        log.error("Aborting because Recoll is already indexing.")
        duration = time.monotonic() - start_wall
        _print_summary(2, duration, my_pid)
        return 2

    # Full rebuild confirmation
    if args.rebuild and not confirm_rebuild():
        log.warning("Cancelled.")
        return 0

    # Run indexing --------------------------------------------------------
    if args.rebuild:
        exit_code = run_indexing("FULL REBUILD", ["recollindex", "-z"])
    else:
        exit_code = run_indexing("INCREMENTAL", ["recollindex"])

    # Post-index diagnostics
    container_diagnostics("Post-index")
    storage_diagnostics("Post-index")

    _print_summary(exit_code, time.monotonic() - start_wall, my_pid)

    return exit_code


# ---------------------------------------------------------------------------
# Entry point with file lock
# ---------------------------------------------------------------------------


def _locked_main() -> int:
    """Run main() inside a non-blocking exclusive file lock.

    Returns:
        0 for success paths from ``main``; 1 when the lock cannot be
        created or an unhandled exception occurs; 3 when another wrapper
        instance already holds the lock.
    """
    try:
        lock_fd = LOCK_FILE.open("w", encoding="utf-8")
    except OSError as exc:
        log.error("Cannot create lock file %s: %s", LOCK_FILE, exc)
        return 1

    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log.error(
            "Another recollindex wrapper is already running "
            "(lock %s is held). Exiting.",
            LOCK_FILE,
        )
        lock_fd.close()
        return 3

    try:
        return main()
    except SystemExit as exc:  # argparse --help and friends exit via SystemExit
        code = exc.code
        return code if isinstance(code, int) else 0
    except KeyboardInterrupt:  # ^C must always be able to abort cleanly
        raise
    except BaseException:
        log.exception("Unhandled exception:")
        return 1
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


if __name__ == "__main__":
    sys.exit(_locked_main())
