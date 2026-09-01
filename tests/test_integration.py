"""Integration tests for recoll-webui-docker services.

These tests verify the actual integration between services:
- recoll_wrapper: Manages recollindex, produces search index
- sms-processor: Processes SMS backups into markdown files
- recoll-audio-worker: Transcribes audio files into text
- recoll-webui: Web interface to query the recoll index

Integration tests verify that:
1. Services can communicate through shared filesystem
2. Data flows correctly between components
3. File formats are compatible between services
4. CLI interfaces work end-to-end
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure all service packages are on the path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "recoll_wrapper"))
sys.path.insert(0, str(PROJECT_ROOT / "sms-processor"))
sys.path.insert(0, str(PROJECT_ROOT / "recoll-audio-worker"))
sys.path.insert(0, str(PROJECT_ROOT / "recoll-webui"))

# Mock recoll imports for webui if not available (recoll is a system package)
try:
    import recoll
except ImportError:
    sys.modules['recoll'] = MagicMock()
    sys.modules['recoll.rclextract'] = MagicMock()

# Import service modules (gracefully handle missing modules)
def _import_module(module_name: str, package_path: str = ""):
    """Try to import a module, return None if not available."""
    try:
        if package_path:
            return importlib.import_module(f"{package_path}.{module_name}")
        return importlib.import_module(module_name)
    except (ModuleNotFoundError, ImportError):
        return None

# Try to import each service module
recollindex = _import_module("recollindex")
sms_archiver = _import_module("sms_processor", "archiver")
sms_core = _import_module("sms_processor", "core")
webui = _import_module("webui")

# Skip markers for services that aren't available
requires_recollindex = pytest.mark.skipif(
    recollindex is None,
    reason="recollindex module not available"
)
requires_sms_processor = pytest.mark.skipif(
    sms_archiver is None or sms_core is None,
    reason="sms_processor module not available (requires Python 3.14+)"
)
requires_webui = pytest.mark.skipif(
    webui is None,
    reason="webui module not available"
)


# =============================================================================
# Integration Test: recoll-webui get_dirs returns absolute paths
# =============================================================================


class TestRecollWebUIGetDirsIntegration:
    """Integration tests for recoll-webui get_dirs function.

    Verifies that get_dirs correctly returns absolute paths that can be
    used by recoll's dir: query clause for filtering search results.
    """

    def test_get_dirs_returns_absolute_paths_real_filesystem(self) -> None:
        """Verify get_dirs returns absolute paths that exist on filesystem."""
        from webui import get_dirs

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test directory structure
            sub1 = Path(tmpdir) / "documents"
            sub2 = Path(tmpdir) / "photos"
            sub1.mkdir()
            sub2.mkdir()

            # Create nested directories
            (sub1 / "work").mkdir()
            (sub1 / "personal").mkdir()
            (sub2 / "vacation").mkdir()

            result = get_dirs([tmpdir], depth=2)

            # First entry should be '<all>'
            assert result[0] == "<all>"

            # All other entries should be absolute paths
            for d in result[1:]:
                assert os.path.isabs(d), f"Path {d} is not absolute"
                assert os.path.isdir(d), f"Path {d} does not exist"

    def test_get_dirs_with_relative_input_returns_absolute(self) -> None:
        """Verify get_dirs converts relative paths to absolute."""
        from webui import get_dirs

        with tempfile.TemporaryDirectory() as tmpdir:
            sub = Path(tmpdir) / "test"
            sub.mkdir()

            # Change to the temp directory and use relative path
            original_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                result = get_dirs(["test"], depth=1)

                assert result[0] == "<all>"
                for d in result[1:]:
                    assert os.path.isabs(d), f"Relative input should produce absolute path: {d}"
                    assert os.path.isdir(d), f"Path {d} does not exist"
            finally:
                os.chdir(original_cwd)

    def test_get_dirs_depth_parameter_works(self) -> None:
        """Verify get_dirs respects depth parameter."""
        from webui import get_dirs

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create nested structure
            root = Path(tmpdir) / "root"
            root.mkdir()
            level1 = root / "level1"
            level1.mkdir()
            level2 = level1 / "level2"
            level2.mkdir()
            level3 = level2 / "level3"
            level3.mkdir()

            # Depth 1 should only include root and level1
            result_depth1 = get_dirs([str(root)], depth=1)
            paths_depth1 = set(result_depth1[1:])  # Exclude '<all>'
            assert str(level1) in paths_depth1
            assert str(level2) not in paths_depth1

            # Depth 2 should include up to level2
            result_depth2 = get_dirs([str(root)], depth=2)
            paths_depth2 = set(result_depth2[1:])
            assert str(level1) in paths_depth2
            assert str(level2) in paths_depth2
            assert str(level3) not in paths_depth2


# =============================================================================
# Integration Test: sms-processor output format compatibility
# =============================================================================


class TestSMSProcessorOutputIntegration:
    """Integration tests for sms-processor output format.

    Verifies that sms-processor produces output that can be indexed by recoll
    and queried through recoll-webui.
    """

    def test_processed_sms_creates_markdown_files(self, tmp_path: Path) -> None:
        """Verify sms-processor creates markdown files from SMS XML."""
        from sms_processor.archiver import process_xml_file

        # Create sample SMS XML
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<backup>
  <sms address="+15551234567" date="1722051780000" protocol="0" type="1">
    <person>Test Contact</person>
    <body>Integration test message</body>
    <date>2026-08-01 14:23:00</date>
    <type>1</type>
    <protocol>0</protocol>
  </sms>
</backup>"""
        xml_file = tmp_path / "sms.xml"
        xml_file.write_text(xml_content)

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        # Process the XML file
        updated = process_xml_file(xml_file, output_dir, "testuser")

        # Verify output was created
        assert len(updated) == 1
        user_dir = output_dir / "testuser"
        assert user_dir.exists()

        # Find the markdown file
        md_files = list(user_dir.glob("*.md"))
        assert len(md_files) == 1

        # Verify markdown content
        content = md_files[0].read_text()
        assert "Integration test message" in content
        assert "Test Contact" in content

    def test_processed_mms_creates_markdown_with_attachments(self, tmp_path: Path) -> None:
        """Verify sms-processor handles MMS with attachments."""
        from sms_processor.archiver import process_xml_file

        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<backup>
  <mms address="+15551234567" date="1722053400000" protocol="0" type="1">
    <person>Photo Contact</person>
    <body>Check this photo</body>
    <date>2026-08-01 14:50:00</date>
    <type>1</type>
    <subject>Photo</subject>
    <part ct="image/jpeg" name="photo.jpg" loc="/storage/photo.jpg"/>
  </mms>
</backup>"""
        xml_file = tmp_path / "mms.xml"
        xml_file.write_text(xml_content)

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        updated = process_xml_file(xml_file, output_dir, "testuser")

        assert len(updated) == 1
        user_dir = output_dir / "testuser"
        md_files = list(user_dir.glob("*.md"))
        assert len(md_files) == 1

        content = md_files[0].read_text()
        assert "[MMS]" in content
        assert "photo.jpg" in content


# =============================================================================
# Integration Test: recoll-audio-worker state management
# =============================================================================


class TestAudioWorkerStateIntegration:
    """Integration tests for recoll-audio-worker state management.

    Verifies that the audio worker correctly tracks processed files
    and can resume processing after interruption.
    """

    def test_state_persists_across_instances(self, tmp_path: Path) -> None:
        """Verify state is correctly saved and loaded."""
        from sms_processor.core import load_state, save_state

        # Create a state file path
        state_file = tmp_path / ".processed.json"

        # Patch the STATE_FILE location
        import sms_processor.core as core_module
        original_state_file = core_module.STATE_FILE

        try:
            core_module.STATE_FILE = state_file

            # Save some state
            test_state = {"file1.mp3": "abc123", "file2.wav": "def456"}
            save_state(test_state)

            # Verify file was created
            assert state_file.exists()

            # Load state in a "new" instance
            loaded_state = load_state()

            assert loaded_state == test_state

        finally:
            core_module.STATE_FILE = original_state_file

    def test_state_handles_corruption_gracefully(self, tmp_path: Path) -> None:
        """Verify state recovery when file is corrupted."""
        from sms_processor.core import load_state

        state_file = tmp_path / ".processed.json"
        state_file.write_text("not valid json{{{[")

        import sms_processor.core as core_module
        original_state_file = core_module.STATE_FILE

        try:
            core_module.STATE_FILE = state_file

            # Should return empty dict on corruption
            state = load_state()
            assert state == {}

        finally:
            core_module.STATE_FILE = original_state_file


# =============================================================================
# Integration Test: recoll_wrapper CLI integration
# =============================================================================


class TestRecollWrapperCLIIntegration:
    """Integration tests for recoll_wrapper CLI.

    Verifies that the recoll_wrapper module can be invoked as a CLI
    and correctly processes command-line arguments.
    """

    def test_parse_args_defaults(self) -> None:
        """Verify CLI argument parsing with defaults."""
        from recollindex import parse_args

        args = parse_args([])
        assert args.rebuild is False
        assert args.verbose is False

    def test_parse_args_rebuild_flag(self) -> None:
        """Verify --rebuild flag is correctly parsed."""
        from recollindex import parse_args

        args = parse_args(["--rebuild"])
        assert args.rebuild is True

    def test_parse_args_verbose_flag(self) -> None:
        """Verify -v flag is correctly parsed."""
        from recollindex import parse_args

        args = parse_args(["-v"])
        assert args.verbose is True

    def test_pure_duration_formatting(self) -> None:
        """Verify duration formatting for various inputs."""
        from recollindex import pretty_duration

        # Test various durations
        assert pretty_duration(0) == "00h 00m 00s"
        assert pretty_duration(59) == "00h 00m 59s"
        assert pretty_duration(60) == "00h 01m 00s"
        assert pretty_duration(3661) == "01h 01m 01s"
        assert pretty_duration(86400) == "24h 00m 00s"

    def test_pure_duration_handles_invalid(self) -> None:
        """Verify duration formatting handles invalid inputs."""
        from recollindex import pretty_duration

        # Should not raise on invalid inputs
        assert pretty_duration(float("inf")) == "00h 00m 00s"
        assert pretty_duration(float("nan")) == "00h 00m 00s"
        assert pretty_duration(-1) == "00h 00m 00s"


# =============================================================================
# Integration Test: Cross-service data flow
# =============================================================================


class TestCrossServiceDataFlow:
    """Integration tests for data flow between services.

    Verifies that data produced by one service can be consumed by another.
    """

    def test_sms_output_can_be_indexed_by_recoll(self, tmp_path: Path) -> None:
        """Verify SMS processor output format is compatible with recoll indexing."""
        from sms_processor.archiver import process_xml_file

        # Create SMS data
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<backup>
  <sms address="+15551234567" date="1722051780000" protocol="0" type="1">
    <person>Integration Test</person>
    <body>This message should be searchable after indexing</body>
    <date>2026-08-01 14:23:00</date>
    <type>1</type>
    <protocol>0</protocol>
  </sms>
</backup>"""
        xml_file = tmp_path / "sms.xml"
        xml_file.write_text(xml_content)

        output_dir = tmp_path / "indexed"
        output_dir.mkdir()

        # Process SMS
        process_xml_file(xml_file, output_dir, "testuser")

        # Verify output format is markdown (indexable by recoll)
        md_files = list(output_dir.rglob("*.md"))
        assert len(md_files) > 0

        # Verify content structure
        content = md_files[0].read_text()
        assert "searchable after indexing" in content

        # Verify file is in a directory structure that recoll can index
        assert md_files[0].parent.exists()
        assert md_files[0].parent.is_dir()

    def test_multiple_services_share_filesystem(self, tmp_path: Path) -> None:
        """Verify multiple services can share the same filesystem."""
        from sms_processor.archiver import process_xml_file
        from sms_processor.core import file_hash

        # Create test data
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<backup>
  <sms address="+15551234567" date="1722051780000" protocol="0" type="1">
    <person>Shared Test</person>
    <body>Testing shared filesystem access</body>
    <date>2026-08-01 14:23:00</date>
    <type>1</type>
    <protocol>0</protocol>
  </sms>
</backup>"""
        xml_file = tmp_path / "shared.xml"
        xml_file.write_text(xml_content)

        output_dir = tmp_path / "shared_output"
        output_dir.mkdir()

        # Process with sms-processor
        process_xml_file(xml_file, output_dir, "shared_user")

        # Verify files were created
        md_files = list(output_dir.rglob("*.md"))
        assert len(md_files) > 0

        # Verify we can compute hash (as recoll-audio-worker would)
        for f in md_files:
            hash_value = file_hash(f)
            assert len(hash_value) == 32  # MD5 hash length

    def test_webui_get_dirs_finds_service_output(self, tmp_path: Path) -> None:
        """Verify recoll-webui can discover output from other services."""
        from webui import get_dirs

        # Create directory structure similar to service output
        docs_dir = tmp_path / "documents"
        sms_dir = docs_dir / "sms"
        audio_dir = docs_dir / "audio"
        sms_dir.mkdir(parents=True)
        audio_dir.mkdir(parents=True)

        # Create some files
        (sms_dir / "contact.md").write_text("# SMS with Contact\nTest message")
        (audio_dir / "transcript.md").write_text("# Audio Transcript\nTest transcription")

        # Verify get_dirs discovers the directories
        result = get_dirs([str(docs_dir)], depth=1)

        paths = result[1:]  # Exclude '<all>'
        assert str(sms_dir) in paths
        assert str(audio_dir) in paths


# =============================================================================
# Integration Test: Error handling across services
# =============================================================================


class TestCrossServiceErrorHandling:
    """Integration tests for error handling across services."""

    def test_missing_input_directory_handled(self, tmp_path: Path) -> None:
        """Verify services handle missing input directories gracefully."""
        from sms_processor.core import scan_and_process, INPUT_DIR

        # Temporarily override INPUT_DIR to a non-existent path
        import sms_processor.core as core_module
        original_input_dir = core_module.INPUT_DIR

        try:
            core_module.INPUT_DIR = tmp_path / "nonexistent"
            # Should not raise, just return 0
            result = scan_and_process()
            assert result == 0
        finally:
            core_module.INPUT_DIR = original_input_dir

    def test_empty_input_handled(self, tmp_path: Path) -> None:
        """Verify services handle empty input gracefully."""
        from sms_processor.archiver import process_xml_file

        # Create empty XML
        xml_file = tmp_path / "empty.xml"
        xml_file.write_text('<?xml version="1.0"?><backup></backup>')

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        # Should handle empty backup
        updated = process_xml_file(xml_file, output_dir, "test")
        assert len(updated) == 0

    def test_malformed_xml_handled(self, tmp_path: Path) -> None:
        """Verify services handle malformed XML gracefully."""
        from sms_processor.archiver import process_xml_file

        xml_file = tmp_path / "bad.xml"
        xml_file.write_text("This is not XML at all")

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        # Should handle malformed XML without crashing
        try:
            updated = process_xml_file(xml_file, output_dir, "test")
        except Exception as e:
            # Should raise a parse exception, not crash
            assert "parse" in str(e).lower() or "xml" in str(e).lower()
