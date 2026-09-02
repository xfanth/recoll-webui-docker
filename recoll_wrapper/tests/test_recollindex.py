"""Comprehensive tests for recollindex module."""

from __future__ import annotations

import fcntl
import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure the wrapper package and repo root are on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


# ---------------------------------------------------------------------------
# Module-level smoke tests
# ---------------------------------------------------------------------------


def test_module_imports() -> None:
    """Module imports without error."""
    import recollindex  # noqa: F401

    assert True


def test_datasets_of_interest() -> None:
    """DATASETS_OF_INTEREST contains expected datasets."""
    from recollindex import DATASETS_OF_INTEREST

    assert "lambo/share" in DATASETS_OF_INTEREST
    assert "shuttle/share" in DATASETS_OF_INTEREST


# ---------------------------------------------------------------------------
# pretty_duration
# ---------------------------------------------------------------------------


def test_pretty_duration() -> None:
    """pretty_duration formats seconds correctly."""
    from recollindex import pretty_duration

    assert pretty_duration(0) == "00h 00m 00s"
    assert pretty_duration(60) == "00h 01m 00s"
    assert pretty_duration(3600) == "01h 00m 00s"
    assert pretty_duration(3661) == "01h 01m 01s"
    assert pretty_duration(3723) == "01h 02m 03s"


def test_pretty_duration_large() -> None:
    """pretty_duration handles large values."""
    from recollindex import pretty_duration

    assert pretty_duration(172800) == "48h 00m 00s"
    assert pretty_duration(99999) == "27h 46m 39s"


def test_pretty_duration_clamps_invalid() -> None:
    """Non-finite and negative inputs clamp to zero instead of raising."""
    from recollindex import pretty_duration

    assert pretty_duration(float("inf")) == "00h 00m 00s"
    assert pretty_duration(float("nan")) == "00h 00m 00s"
    assert pretty_duration(-5) == "00h 00m 00s"


# ---------------------------------------------------------------------------
# run_cmd
# ---------------------------------------------------------------------------


def test_run_cmd_success() -> None:
    """run_cmd returns CompletedProcess on success."""
    from recollindex import run_cmd

    result = run_cmd("echo", "hello")
    assert result.returncode == 0
    assert "hello" in result.stdout


def test_run_cmd_failure() -> None:
    """run_cmd returns non-zero exit code without raising."""
    from recollindex import run_cmd

    result = run_cmd("sh", "-c", "exit 42")
    assert result.returncode == 42


def test_run_cmd_timeout() -> None:
    """run_cmd catches TimeoutExpired and returns CompletedProcess."""
    from recollindex import run_cmd

    result = run_cmd("sleep", "10", timeout=1)
    assert result.returncode == -1
    assert "Timed out after 1s" in result.stderr


def test_run_cmd_command_not_found() -> None:
    """run_cmd survives a missing host utility (e.g. no zpool on dev boxes)."""
    from recollindex import run_cmd

    result = run_cmd("definitely-not-a-real-binary-xyz")
    assert result.returncode == 127
    assert "Command not found" in result.stderr


# ---------------------------------------------------------------------------
# Logging setup / console
# ---------------------------------------------------------------------------


def test_module_console_initialised() -> None:
    """Module-level console is a rich Console (terminal display surface)."""
    from rich.console import Console

    import recollindex

    assert isinstance(recollindex.console, Console)
    assert recollindex.log is not None
    # Terminal handler always attached; file handler optional by environment.
    root = __import__("logging").getLogger()
    handlers = [h for h in root.handlers if h.__class__.__name__ == "RichHandler"]
    assert len(handlers) >= 1


def test_set_console_verbosity_toggles_handler_level() -> None:
    """set_console_verbosity raises/lowers the terminal handler level."""
    import logging

    import recollindex

    original = recollindex._term_handler.level
    try:
        recollindex.set_console_verbosity(True)
        assert recollindex._term_handler.level == logging.DEBUG
        recollindex.set_console_verbosity(False)
        assert recollindex._term_handler.level == logging.INFO
    finally:
        recollindex._term_handler.setLevel(original)


# ---------------------------------------------------------------------------
# parse_args
# ---------------------------------------------------------------------------


def test_parse_args_defaults() -> None:
    """parse_args defaults to incremental, non-verbose."""
    from recollindex import parse_args

    args = parse_args([])
    assert args.rebuild is False
    assert args.verbose is False


def test_parse_args_rebuild_verbose() -> None:
    """parse_args picks up --rebuild and -v."""
    from recollindex import parse_args

    args = parse_args(["--rebuild", "-v"])
    assert args.rebuild is True
    assert args.verbose is True


def test_parse_args_help_exits_zero() -> None:
    """parse_args exits with SystemExit(0) on --help."""
    from recollindex import parse_args

    try:
        parse_args(["--help"])
        raise AssertionError("--help should have exited")
    except SystemExit as exc:
        assert exc.code == 0


def test_parse_args_rejects_unknown() -> None:
    """parse_args rejects unknown flags."""
    from recollindex import parse_args

    try:
        parse_args(["--bogus"])
        raise AssertionError("unknown flag should have exited")
    except SystemExit as exc:
        assert exc.code == 2


# ---------------------------------------------------------------------------
# _print_section, _print_subsection
# ---------------------------------------------------------------------------


def test_print_section() -> None:
    """_print_section logs with info level."""
    import recollindex

    fake_logger = MagicMock()
    orig = recollindex.log
    try:
        recollindex.log = fake_logger
        recollindex._print_section("Test Section")
        fake_logger.info.assert_called_once()
        call_args = fake_logger.info.call_args[0]
        # Check that the title is passed as the second argument (format string, then value)
        assert call_args[1] == "Test Section"
    finally:
        recollindex.log = orig


def test_print_subsection() -> None:
    """_print_subsection logs with info level."""
    import recollindex

    fake_logger = MagicMock()
    orig = recollindex.log
    try:
        recollindex.log = fake_logger
        recollindex._print_subsection("Test Sub")
        fake_logger.info.assert_called_once()
        call_args = fake_logger.info.call_args[0]
        assert call_args[1] == "Test Sub"
    finally:
        recollindex.log = orig


# ---------------------------------------------------------------------------
# _child_level
# ---------------------------------------------------------------------------


def test_child_level_stdout_debug() -> None:
    """Stdout lines are logged at DEBUG (file audit trail only by default)."""
    import logging

    from recollindex import _child_level

    assert _child_level("stdout", "anything") == logging.DEBUG


def test_child_level_error_stderr_warning() -> None:
    """Stderr lines that start with error or fail are WARNING."""
    import logging

    from recollindex import _child_level

    assert _child_level("stderr", "error: boom") == logging.WARNING
    assert _child_level("stderr", "FAILED to open x") == logging.WARNING


def test_child_level_plain_stderr_debug() -> None:
    """Ordinary stderr lines stay DEBUG (not every line is a warning)."""
    import logging

    from recollindex import _child_level

    assert _child_level("stderr", "Found 10 files") == logging.DEBUG


# ---------------------------------------------------------------------------
# run log file naming + pruning
# ---------------------------------------------------------------------------


def test_run_log_file_name_format() -> None:
    """Per-run log files are named <YYYY-MM-DD_HHMMSS>.log under LOG_DIR."""
    import re

    from recollindex import LOG_DIR, _run_log_file

    path = _run_log_file()
    assert path.parent == LOG_DIR
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{6}\.log", path.name)


def test_prune_old_logs_deletes_only_stale(tmp_path: Path) -> None:
    """Pruning removes *.log older than 30 days, keeps the rest."""
    import os
    import time as time_mod

    from recollindex import _prune_old_logs

    old = tmp_path / "2026-01-01_000000.log"
    fresh = tmp_path / "2026-08-15_000000.log"
    other = tmp_path / "notes.txt"
    for p in (old, fresh, other):
        p.write_text("x", encoding="utf-8")
    stale = time_mod.time() - 40 * 86400
    os.utime(old, (stale, stale))

    _prune_old_logs(tmp_path)

    assert not old.exists()
    assert fresh.exists()
    assert other.exists()


def test_prune_old_logs_missing_dir_is_noop(tmp_path: Path) -> None:
    """Pruning a missing directory is silently ignored (best-effort)."""
    from recollindex import _prune_old_logs

    _prune_old_logs(tmp_path / "does-not-exist")  # must not raise


# ---------------------------------------------------------------------------
# container_diagnostics
# ---------------------------------------------------------------------------


def test_container_diagnostics() -> None:
    """container_diagnostics runs through all diagnostics."""
    import recollindex

    fake_console = MagicMock()
    orig = recollindex.console
    try:
        recollindex.console = fake_console

        mock_result = subprocess.CompletedProcess(
            [], 0, "recoll-engine\tUp 5 days\timage\n", ""
        )
        with patch.object(recollindex, "run_cmd", return_value=mock_result):
            recollindex.container_diagnostics("Test")
    finally:
        recollindex.console = orig


def test_container_diagnostics_no_processes() -> None:
    """container_diagnostics handles empty process list."""
    import recollindex

    fake_console = MagicMock()
    orig = recollindex.console
    try:
        recollindex.console = fake_console

        def side_effect(*args, **_kwargs):
            return subprocess.CompletedProcess(args, 0, "\n", "")

        with patch.object(recollindex, "run_cmd", side_effect=side_effect):
            recollindex.container_diagnostics("Test")
    finally:
        recollindex.console = orig


def test_container_diagnostics_with_stderr() -> None:
    """container_diagnostics prints stderr when present."""
    import recollindex

    fake_console = MagicMock()
    orig = recollindex.console
    try:
        recollindex.console = fake_console

        def side_effect(*args, **_kwargs):
            if "recollindex -h" in args:
                return subprocess.CompletedProcess(args, 0, "v1\n", "warning\n")
            return subprocess.CompletedProcess(args, 0, "\n", "")

        with patch.object(recollindex, "run_cmd", side_effect=side_effect):
            recollindex.container_diagnostics("Test")
    finally:
        recollindex.console = orig


# ---------------------------------------------------------------------------
# storage_diagnostics
# ---------------------------------------------------------------------------


def test_storage_diagnostics() -> None:
    """storage_diagnostics runs through all checks."""
    import recollindex

    fake_console = MagicMock()
    orig = recollindex.console
    try:
        recollindex.console = fake_console
        mock_result = subprocess.CompletedProcess([], 0, "data\n", "")

        with patch.object(recollindex, "run_cmd", return_value=mock_result):
            with patch.object(Path, "exists", return_value=False):
                recollindex.storage_diagnostics("Test")
    finally:
        recollindex.console = orig


def test_storage_diagnostics_arc_available() -> None:
    """storage_diagnostics parses ARC stats when available."""
    import recollindex

    fake_console = MagicMock()
    orig = recollindex.console
    try:
        recollindex.console = fake_console
        mock_result = subprocess.CompletedProcess([], 0, "data\n", "")
        arc_content = (
            "timestamp    1234567890\n"
            "size         12345678\n"
            "c_min        1111111\n"
            "c_max        2222222\n"
            "hits         333333\n"
            "misses       44444\n"
        )

        with patch.object(recollindex, "run_cmd", return_value=mock_result):
            with patch.object(Path, "exists", return_value=True):
                with patch.object(Path, "read_text", return_value=arc_content):
                    recollindex.storage_diagnostics("Test")
    finally:
        recollindex.console = orig


def test_storage_diagnostics_arc_read_error() -> None:
    """storage_diagnostics handles OSError reading ARC stats."""
    import recollindex

    fake_console = MagicMock()
    orig = recollindex.console
    try:
        recollindex.console = fake_console
        mock_result = subprocess.CompletedProcess([], 0, "data\n", "")

        with patch.object(recollindex, "run_cmd", return_value=mock_result):
            with patch.object(Path, "exists", return_value=True):
                with patch.object(Path, "read_text", side_effect=OSError("perm")):
                    recollindex.storage_diagnostics("Test")
    finally:
        recollindex.console = orig


def test_storage_diagnostics_zfs_failed() -> None:
    """storage_diagnostics handles zfs list failure."""
    import recollindex

    fake_console = MagicMock()
    orig = recollindex.console
    try:
        recollindex.console = fake_console

        def side_effect(*args, **_kwargs):
            if args[0] == "zfs":
                return subprocess.CompletedProcess(args, 1, "", "error\n")
            return subprocess.CompletedProcess(args, 0, "data\n", "")

        with patch.object(recollindex, "run_cmd", side_effect=side_effect):
            with patch.object(Path, "exists", return_value=False):
                recollindex.storage_diagnostics("Test")
    finally:
        recollindex.console = orig


def test_storage_diagnostics_lspci_unavailable() -> None:
    """storage_diagnostics handles missing lspci."""
    import recollindex

    fake_console = MagicMock()
    orig = recollindex.console
    try:
        recollindex.console = fake_console

        def side_effect(*args, **_kwargs):
            if args[0] == "lspci":
                return subprocess.CompletedProcess(args, 1, "", "")
            return subprocess.CompletedProcess(args, 0, "data\n", "")

        with patch.object(recollindex, "run_cmd", side_effect=side_effect):
            with patch.object(Path, "exists", return_value=False):
                recollindex.storage_diagnostics("Test")
    finally:
        recollindex.console = orig


def test_storage_diagnostics_pci_matching() -> None:
    """storage_diagnostics filters PCI storage adapters."""
    import recollindex

    fake_console = MagicMock()
    orig = recollindex.console
    try:
        recollindex.console = fake_console

        def side_effect(*args, **_kwargs):
            if args[0] == "lspci":
                return subprocess.CompletedProcess(
                    args,
                    0,
                    "00:1f.2 SATA controller: Intel\n"
                    "01:00.0 VGA compatible device: NVIDIA\n",
                    "",
                )
            return subprocess.CompletedProcess(args, 0, "data\n", "")

        with patch.object(recollindex, "run_cmd", side_effect=side_effect):
            with patch.object(Path, "exists", return_value=False):
                recollindex.storage_diagnostics("Test")
    finally:
        recollindex.console = orig


def test_storage_diagnostics_zfs_table_rows() -> None:
    """storage_diagnostics renders matching ZFS datasets as a table."""
    import recollindex

    fake_console = MagicMock()
    fake_logger = MagicMock()
    orig_console, orig_log = recollindex.console, recollindex.log
    try:
        recollindex.console = fake_console
        recollindex.log = fake_logger

        def side_effect(*args, **_kwargs):
            if args[0] == "zfs":
                return subprocess.CompletedProcess(
                    args, 0, "shuttle/share\t1G\t2T\t500M\t/mnt/shuttle/share\n", ""
                )
            return subprocess.CompletedProcess(args, 0, "\n", "")

        with patch.object(recollindex, "run_cmd", side_effect=side_effect):
            with patch.object(Path, "exists", return_value=False):
                recollindex.storage_diagnostics("Test")

        calls = [str(c) for c in fake_logger.debug.call_args_list]
        assert any("shuttle/share" in c for c in calls)
    finally:
        recollindex.console, recollindex.log = orig_console, orig_log


# ---------------------------------------------------------------------------
# print_configuration
# ---------------------------------------------------------------------------


def test_print_configuration_missing() -> None:
    """print_configuration handles missing config file."""
    import recollindex

    fake_logger = MagicMock()
    orig = recollindex.log
    try:
        recollindex.log = fake_logger
        with patch.object(Path, "exists", return_value=False):
            recollindex.print_configuration()
            fake_logger.error.assert_called_once()
            call_args = fake_logger.error.call_args[0]
            assert "Missing config" in call_args[0]
    finally:
        recollindex.log = orig


def test_print_configuration_success() -> None:
    """print_configuration parses and logs config values."""
    import recollindex

    fake_logger = MagicMock()
    fake_console = MagicMock()
    orig_log, orig_console = recollindex.log, recollindex.console
    try:
        recollindex.log = fake_logger
        recollindex.console = fake_console
        config_content = "# comment\ntopdirs = /path1\nloglevel = 3\nother = value\n"
        with patch.object(Path, "exists", return_value=True):
            with patch.object(Path, "read_text", return_value=config_content):
                recollindex.print_configuration()
                calls = [str(c) for c in fake_logger.debug.call_args_list]
                assert any("topdirs" in c for c in calls)
                assert any("loglevel" in c for c in calls)
    finally:
        recollindex.log, recollindex.console = orig_log, orig_console


def test_print_configuration_os_error() -> None:
    """print_configuration handles OSError reading config."""
    import recollindex

    fake_logger = MagicMock()
    orig_log = recollindex.log
    try:
        recollindex.log = fake_logger
        with patch.object(Path, "exists", return_value=True):
            with patch.object(Path, "read_text", side_effect=OSError("perm")):
                recollindex.print_configuration()
                fake_logger.error.assert_called_once()
    finally:
        recollindex.log = orig_log


# ---------------------------------------------------------------------------
# check_existing_indexers
# ---------------------------------------------------------------------------


def test_check_existing_indexers_none() -> None:
    """check_existing_indexers returns False when no processes."""
    import recollindex

    fake_console = MagicMock()
    orig = recollindex.console
    try:
        recollindex.console = fake_console
        mock_result = subprocess.CompletedProcess([], 0, "0\n", "")
        with patch.object(recollindex, "run_cmd", return_value=mock_result):
            result = recollindex.check_existing_indexers()
            assert result is False
    finally:
        recollindex.console = orig


def test_check_existing_indexers_running() -> None:
    """check_existing_indexers returns True when processes exist."""
    import recollindex

    fake_console = MagicMock()
    orig = recollindex.console
    try:
        recollindex.console = fake_console
        mock_result = subprocess.CompletedProcess([], 0, "2\n", "")
        with patch.object(recollindex, "run_cmd", return_value=mock_result):
            result = recollindex.check_existing_indexers()
            assert result is True
    finally:
        recollindex.console = orig


def test_check_existing_indexers_non_digit() -> None:
    """check_existing_indexers handles non-digit output."""
    import recollindex

    fake_console = MagicMock()
    orig = recollindex.console
    try:
        recollindex.console = fake_console
        mock_result = subprocess.CompletedProcess([], 0, "error\n", "")
        with patch.object(recollindex, "run_cmd", return_value=mock_result):
            result = recollindex.check_existing_indexers()
            assert result is False
    finally:
        recollindex.console = orig


# ---------------------------------------------------------------------------
# confirm_rebuild
# ---------------------------------------------------------------------------


def test_confirm_rebuild_yes() -> None:
    """confirm_rebuild returns True on 'y'."""
    import recollindex

    fake_console = MagicMock()
    fake_console.input.return_value = "y"
    orig = recollindex.console
    try:
        recollindex.console = fake_console
        result = recollindex.confirm_rebuild()
        assert result is True
    finally:
        recollindex.console = orig


def test_confirm_rebuild_yes_full() -> None:
    """confirm_rebuild returns True on 'yes'."""
    import recollindex

    fake_console = MagicMock()
    fake_console.input.return_value = "yes"
    orig = recollindex.console
    try:
        recollindex.console = fake_console
        result = recollindex.confirm_rebuild()
        assert result is True
    finally:
        recollindex.console = orig


def test_confirm_rebuild_no() -> None:
    """confirm_rebuild returns False on 'n'."""
    import recollindex

    fake_console = MagicMock()
    fake_console.input.return_value = "n"
    orig = recollindex.console
    try:
        recollindex.console = fake_console
        result = recollindex.confirm_rebuild()
        assert result is False
    finally:
        recollindex.console = orig


def test_confirm_rebuild_empty() -> None:
    """confirm_rebuild returns False on empty input."""
    import recollindex

    fake_console = MagicMock()
    fake_console.input.return_value = ""
    orig = recollindex.console
    try:
        recollindex.console = fake_console
        result = recollindex.confirm_rebuild()
        assert result is False
    finally:
        recollindex.console = orig


# ---------------------------------------------------------------------------
# run_indexing (live-streamed child output)
# ---------------------------------------------------------------------------


def _make_mock_proc(returncode, stdout="ok\n", stderr=""):
    """Helper to build a mock Popen process with iterable stream ends."""
    mock_proc = MagicMock()
    mock_proc.returncode = returncode
    mock_proc.wait.return_value = returncode
    mock_proc.stdout = io.StringIO(stdout)
    mock_proc.stderr = io.StringIO(stderr)
    # Enough poll results for the drain loop plus the exit check.
    mock_proc.poll.side_effect = [None, returncode, returncode]
    return mock_proc


def test_run_indexing_success() -> None:
    """run_indexing completes successfully and streams output."""
    import recollindex

    fake_console = MagicMock()
    fake_logger = MagicMock()
    orig_console, orig_log = recollindex.console, recollindex.log
    try:
        recollindex.console = fake_console
        recollindex.log = fake_logger
        mock_proc = _make_mock_proc(0)

        with patch("subprocess.Popen", return_value=mock_proc):
            result = recollindex.run_indexing("INCREMENTAL", ["recollindex"])
            assert result == 0
        # Child stdout line must have been streamed through the logger.
        calls = [str(c) for c in fake_logger.log.call_args_list]
        assert any("ok" in c for c in calls)
    finally:
        recollindex.console, recollindex.log = orig_console, orig_log


def test_run_indexing_failure() -> None:
    """run_indexing returns non-zero and logs the failing tail."""
    import logging

    import recollindex

    fake_console = MagicMock()
    fake_logger = MagicMock()
    orig_console, orig_log = recollindex.console, recollindex.log
    try:
        recollindex.console = fake_console
        recollindex.log = fake_logger
        mock_proc = _make_mock_proc(2, stdout="", stderr="error: bad\n")

        with patch("subprocess.Popen", return_value=mock_proc):
            result = recollindex.run_indexing("FULL REBUILD", ["recollindex", "-z"])
            assert result == 2
        # Error lines are surfaced at WARNING level.
        levels = [c.args[0] for c in fake_logger.log.call_args_list if c.args]
        assert logging.WARNING in levels
    finally:
        recollindex.console, recollindex.log = orig_console, orig_log


def test_run_indexing_many_lines() -> None:
    """run_indexing streams many lines and keeps a bounded tail."""
    import recollindex

    fake_console = MagicMock()
    fake_logger = MagicMock()
    orig_console, orig_log = recollindex.console, recollindex.log
    try:
        recollindex.console = fake_console
        recollindex.log = fake_logger
        mock_proc = _make_mock_proc(3, stdout="\n".join(f"line{i}" for i in range(100)))

        with patch("subprocess.Popen", return_value=mock_proc):
            result = recollindex.run_indexing("INCREMENTAL", ["recollindex"])
            assert result == 3
    finally:
        recollindex.console, recollindex.log = orig_console, orig_log


def test_run_indexing_docker_missing() -> None:
    """run_indexing returns 127 when the docker CLI is absent."""
    import recollindex

    fake_console = MagicMock()
    fake_logger = MagicMock()
    orig_console, orig_log = recollindex.console, recollindex.log
    try:
        recollindex.console = fake_console
        recollindex.log = fake_logger
        with patch("subprocess.Popen", side_effect=FileNotFoundError("no docker")):
            result = recollindex.run_indexing("INCREMENTAL", ["recollindex"])
            assert result == 127
    finally:
        recollindex.console, recollindex.log = orig_console, orig_log


def test_run_indexing_with_total() -> None:
    """run_indexing accepts a known line total (progress bar + ETA)."""
    import recollindex

    fake_console = MagicMock()
    fake_logger = MagicMock()
    orig_console, orig_log = recollindex.console, recollindex.log
    try:
        recollindex.console = fake_console
        recollindex.log = fake_logger
        mock_proc = _make_mock_proc(0, stdout="a\nb\nc\n")

        with patch("subprocess.Popen", return_value=mock_proc):
            result = recollindex.run_indexing(
                "INCREMENTAL", ["recollindex"], total_lines=3
            )
            assert result == 0
    finally:
        recollindex.console, recollindex.log = orig_console, orig_log


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def test_main_incremental_success() -> None:
    """Main runs incremental indexing and returns exit code."""
    import recollindex

    fake_console = MagicMock()
    orig = recollindex.console
    try:
        recollindex.console = fake_console
        mock_proc = _make_mock_proc(0)

        with patch.object(
            recollindex,
            "run_cmd",
            return_value=subprocess.CompletedProcess([], 0, "hostname\n", ""),
        ):
            with patch.object(recollindex, "container_diagnostics"):
                with patch.object(recollindex, "storage_diagnostics"):
                    with patch.object(recollindex, "print_configuration"):
                        with patch.object(
                            recollindex, "check_existing_indexers", return_value=False
                        ):
                            with patch("subprocess.Popen", return_value=mock_proc):
                                with patch.object(sys, "argv", ["recollindex.py"]):
                                    result = recollindex.main()
                                    assert result == 0
    finally:
        recollindex.console = orig


def test_main_aborts_on_existing_indexer() -> None:
    """Main returns 2 when indexer already running."""
    import recollindex

    fake_console = MagicMock()
    orig = recollindex.console
    try:
        recollindex.console = fake_console
        with patch.object(
            recollindex,
            "run_cmd",
            return_value=subprocess.CompletedProcess([], 0, "hostname\n", ""),
        ):
            with patch.object(recollindex, "container_diagnostics"):
                with patch.object(recollindex, "storage_diagnostics"):
                    with patch.object(recollindex, "print_configuration"):
                        with patch.object(
                            recollindex, "check_existing_indexers", return_value=True
                        ):
                            with patch.object(sys, "argv", ["recollindex.py"]):
                                result = recollindex.main()
                                assert result == 2
    finally:
        recollindex.console = orig


def test_main_rebuild_cancelled() -> None:
    """Main returns 0 when rebuild is cancelled."""
    import recollindex

    fake_console = MagicMock()
    orig = recollindex.console
    try:
        recollindex.console = fake_console
        with patch.object(
            recollindex,
            "run_cmd",
            return_value=subprocess.CompletedProcess([], 0, "hostname\n", ""),
        ):
            with patch.object(recollindex, "container_diagnostics"):
                with patch.object(recollindex, "storage_diagnostics"):
                    with patch.object(recollindex, "print_configuration"):
                        with patch.object(
                            recollindex, "check_existing_indexers", return_value=False
                        ):
                            with patch.object(
                                recollindex, "confirm_rebuild", return_value=False
                            ):
                                with patch.object(
                                    sys, "argv", ["recollindex.py", "--rebuild"]
                                ):
                                    result = recollindex.main()
                                    assert result == 0
    finally:
        recollindex.console = orig


def test_main_rebuild_success() -> None:
    """Main runs full rebuild and returns exit code."""
    import recollindex

    fake_console = MagicMock()
    orig = recollindex.console
    try:
        recollindex.console = fake_console
        mock_proc = _make_mock_proc(0)

        with patch.object(
            recollindex,
            "run_cmd",
            return_value=subprocess.CompletedProcess([], 0, "hostname\n", ""),
        ):
            with patch.object(recollindex, "container_diagnostics"):
                with patch.object(recollindex, "storage_diagnostics"):
                    with patch.object(recollindex, "print_configuration"):
                        with patch.object(
                            recollindex, "check_existing_indexers", return_value=False
                        ):
                            with patch.object(
                                recollindex, "confirm_rebuild", return_value=True
                            ):
                                with patch("subprocess.Popen", return_value=mock_proc):
                                    with patch.object(
                                        sys, "argv", ["recollindex.py", "--rebuild"]
                                    ):
                                        result = recollindex.main()
                                        assert result == 0
    finally:
        recollindex.console = orig


def _ok_process(stdout: str) -> subprocess.CompletedProcess[str]:
    """Build a successful CompletedProcess."""
    return subprocess.CompletedProcess([], 0, stdout, "")


def test_main_verbose_flag() -> None:
    """Main applies the verbose flag to the terminal handler level."""
    import logging

    import recollindex

    fake_console = MagicMock()
    orig = recollindex.console
    original_level = recollindex._term_handler.level
    try:
        recollindex.console = fake_console
        with patch.object(
            recollindex, "run_cmd", return_value=_ok_process("hostname\n")
        ):
            with patch.object(recollindex, "container_diagnostics"):
                with patch.object(recollindex, "storage_diagnostics"):
                    with patch.object(recollindex, "print_configuration"):
                        with patch.object(
                            recollindex, "check_existing_indexers", return_value=True
                        ):
                            with patch.object(sys, "argv", ["recollindex.py", "-v"]):
                                recollindex.main()
        assert recollindex._term_handler.level == logging.DEBUG
    finally:
        recollindex.console = orig
        recollindex._term_handler.setLevel(original_level)


# ---------------------------------------------------------------------------
# _locked_main
# ---------------------------------------------------------------------------


def test_locked_main_success() -> None:
    """_locked_main runs main with file lock."""
    import recollindex

    fake_console = MagicMock()
    orig = recollindex.console
    try:
        recollindex.console = fake_console

        with tempfile.NamedTemporaryFile(delete=False) as f:
            tmp_path = f.name

        try:
            with patch.object(recollindex, "LOCK_FILE", Path(tmp_path)):
                with patch.object(recollindex, "main", return_value=0):
                    result = recollindex._locked_main()
                    assert result == 0
        finally:
            os.unlink(tmp_path)
    finally:
        recollindex.console = orig


def test_locked_main_lock_file_os_error() -> None:
    """_locked_main handles lock file creation failure."""
    import recollindex

    fake_console = MagicMock()
    orig = recollindex.console
    try:
        recollindex.console = fake_console
        with patch.object(Path, "open", side_effect=OSError("no perm")):
            result = recollindex._locked_main()
            assert result == 1
    finally:
        recollindex.console = orig


def test_locked_main_lock_contention() -> None:
    """_locked_main exits 3 when another process holds the lock."""
    import recollindex

    fake_console = MagicMock()
    orig = recollindex.console
    try:
        recollindex.console = fake_console

        with tempfile.NamedTemporaryFile(delete=False) as f:
            tmp_path = f.name

        try:
            holder = open(tmp_path, "w")  # noqa: SIM115
            fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                with patch.object(recollindex, "LOCK_FILE", Path(tmp_path)):
                    result = recollindex._locked_main()
                    assert result == 3
            finally:
                holder.close()
        finally:
            os.unlink(tmp_path)
    finally:
        recollindex.console = orig


def test_locked_main_exit_code_passthrough() -> None:
    """_locked_main propagates exit codes from main() (argparse help exits 0)."""
    import recollindex

    fake_console = MagicMock()
    orig = recollindex.console
    try:
        recollindex.console = fake_console

        with tempfile.NamedTemporaryFile(delete=False) as f:
            tmp_path = f.name

        try:
            with patch.object(recollindex, "LOCK_FILE", Path(tmp_path)):
                with patch.object(recollindex, "main", side_effect=SystemExit(0)):
                    assert recollindex._locked_main() == 0
                with patch.object(recollindex, "main", side_effect=SystemExit(2)):
                    assert recollindex._locked_main() == 2
        finally:
            os.unlink(tmp_path)
    finally:
        recollindex.console = orig


def test_locked_main_exception_handling() -> None:
    """_locked_main catches and reports exceptions."""
    import recollindex

    fake_console = MagicMock()
    orig = recollindex.console
    try:
        recollindex.console = fake_console

        with tempfile.NamedTemporaryFile(delete=False) as f:
            tmp_path = f.name

        try:
            with patch.object(recollindex, "LOCK_FILE", Path(tmp_path)):
                with patch.object(
                    recollindex, "main", side_effect=RuntimeError("boom")
                ):
                    result = recollindex._locked_main()
                    assert result == 1
        finally:
            os.unlink(tmp_path)
    finally:
        recollindex.console = orig


# ---------------------------------------------------------------------------
# Constants and configuration
# ---------------------------------------------------------------------------


def test_constants() -> None:
    """Module constants are set correctly."""
    import recollindex

    assert recollindex.CONTAINER == "recoll-engine"
    assert ".recoll/recoll_wrapper/logs" in str(recollindex.LOG_DIR)
    assert "recoll.conf" in str(recollindex.CONFIG_FILE)
    assert recollindex.INDEX_PATH == "/root/.recoll/xapiandb"
    assert Path("/tmp/recollindex-wrapper.lock") == recollindex.LOCK_FILE


def test_config_file_constant_uses_base_path() -> None:
    """CONFIG_FILE equals BASE_PATH + app-data/recoll/.recoll/recoll.conf."""
    import recollindex

    expected = Path(recollindex.BASE_PATH) / "app-data/recoll/.recoll/recoll.conf"
    assert expected == recollindex.CONFIG_FILE


def test_log_dir_constant_uses_base_path() -> None:
    """LOG_DIR equals BASE_PATH + app-data/recoll/.recoll/recoll_wrapper/logs."""
    import recollindex

    expected = (
        Path(recollindex.BASE_PATH) / "app-data/recoll/.recoll/recoll_wrapper/logs"
    )
    assert expected == recollindex.LOG_DIR


def test_container_diagnostics_recoll_version_stderr() -> None:
    """container_diagnostics logs stderr when recoll version command has stderr."""
    import subprocess

    import recollindex

    fake_logger = MagicMock()
    orig = recollindex.log
    try:
        recollindex.log = fake_logger
        container = recollindex.CONTAINER

        def side_effect(*args, **_kwargs):
            if (
                len(args) >= 4
                and args[0] == "docker"
                and args[1] == "exec"
                and args[2] == container
                and args[3] == "sh"
            ):
                return subprocess.CompletedProcess(args, 0, "v1\n", "warning\n")
            return subprocess.CompletedProcess(args, 0, "data\n", "")

        with patch.object(recollindex, "run_cmd", side_effect=side_effect):
            recollindex.container_diagnostics("Test")
            calls = [str(c) for c in fake_logger.debug.call_args_list]
            assert any("warning" in c for c in calls)
    finally:
        recollindex.log = orig


# ---------------------------------------------------------------------------
# _print_cmd_output — graceful failure handling
# ---------------------------------------------------------------------------


def test_print_cmd_output_success() -> None:
    """_print_cmd_output logs stdout on success."""
    import recollindex

    fake_logger = MagicMock()
    result = subprocess.CompletedProcess([], 0, "line1\nline2\n", "")
    recollindex._print_cmd_output("test", result, fake_logger)
    assert fake_logger.info.call_count == 2


def test_print_cmd_output_failure() -> None:
    """_print_cmd_output logs debug unavailable on failure."""
    import recollindex

    fake_logger = MagicMock()
    result = subprocess.CompletedProcess([], 1, "", "Function not implemented")
    recollindex._print_cmd_output("test", result, fake_logger)
    fake_logger.debug.assert_called_once()
    call = fake_logger.debug.call_args[0][0]
    assert "unavailable" in call
    # Should NOT leak the raw stderr message
    assert "Function not implemented" not in call


def test_print_cmd_output_empty_success() -> None:
    """_print_cmd_output prints nothing on success with empty output."""
    import recollindex

    fake_console = MagicMock()
    result = subprocess.CompletedProcess([], 0, "", "")
    recollindex._print_cmd_output("test", result, fake_console)
    fake_console.print.assert_not_called()


# ---------------------------------------------------------------------------
# storage_diagnostics — failing host utilities (TrueNAS)
# ---------------------------------------------------------------------------


def test_storage_diagnostics_all_commands_fail() -> None:
    """storage_diagnostics handles every command failing (TrueNAS BusyBox env)."""
    import recollindex

    fake_console = MagicMock()
    orig = recollindex.console
    try:
        recollindex.console = fake_console

        def side_effect(*args, **_kwargs):
            return subprocess.CompletedProcess(args, 1, "", "Function not implemented")

        with patch.object(recollindex, "run_cmd", side_effect=side_effect):
            with patch.object(Path, "exists", return_value=False):
                recollindex.storage_diagnostics("Test")
        # Should NOT have printed any "Function not implemented" lines
        calls = [str(c) for c in fake_console.print.call_args_list]
        assert not any("Function not implemented" in c for c in calls)
    finally:
        recollindex.console = orig
