"""Integration tests for sms_processor - test cross-component data flow."""

import tempfile
from pathlib import Path

from sms_processor.archiver import process_xml_file
from sms_processor.core import load_state, scan_and_process


class TestIntegration:
    """Integration tests for end-to-end processing pipeline."""

    def setup_method(self) -> None:
        """Set up test directories and override module constants."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.temp_dir.name)

        self.input_dir = self.tmp / "input"
        self.output_dir = self.tmp / "output"
        self.input_dir.mkdir()
        self.output_dir.mkdir()

        # Override module constants
        from sms_processor import core

        self.orig_input = core.INPUT_DIR
        self.orig_output = core.OUTPUT_DIR
        self.orig_state = core.STATE_FILE

        core.INPUT_DIR = self.input_dir
        core.OUTPUT_DIR = self.output_dir
        core.STATE_FILE = self.output_dir / ".processed.json"

    def teardown_method(self) -> None:
        """Restore original module constants."""
        from sms_processor import core

        core.INPUT_DIR = self.orig_input
        core.OUTPUT_DIR = self.orig_output
        core.STATE_FILE = self.orig_state
        self.temp_dir.cleanup()

    def _create_test_xml(self, path: Path, content: str) -> None:
        """Create a test XML file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def _get_sample_xml(self) -> str:
        """Return a standard sample XML for testing."""
        return """<?xml version="1.0" encoding="UTF-8"?>
<backup op_id="test" app_version="1.0" backup_time="2024-01-01 00:00:00">
  <sms address="+15551234567" date="1722051780000" type="1" protocol="0">
    <person>Test Contact</person>
    <body>Test message</body>
    <date>2024-01-01 00:00:00</date>
  </sms>
  <sms address="+15559876543" date="1722051900000" type="2" protocol="0">
    <body>Reply message</body>
    <date>2024-01-01 00:01:00</date>
  </sms>
</backup>"""

    def test_full_pipeline_single_file(self) -> None:
        """Test complete pipeline: XML input → markdown output."""
        user_dir = self.input_dir / "user1"
        xml_file = user_dir / "backup.xml"
        self._create_test_xml(xml_file, self._get_sample_xml())

        # Run scan_and_process
        count = scan_and_process()

        assert count == 1

        # Verify output files exist
        md_files = list(self.output_dir.rglob("*.md"))
        assert len(md_files) == 2  # One per contact

        # Verify content
        for md in md_files:
            content = md.read_text()
            assert "Test Contact" in content or "+1555" in content
            assert "Test message" in content or "Reply message" in content

    def test_incremental_processing(self) -> None:
        """Test that re-processing same file doesn't duplicate."""
        user_dir = self.input_dir / "user1"
        xml_file = user_dir / "backup.xml"
        self._create_test_xml(xml_file, self._get_sample_xml())

        # First run
        count1 = scan_and_process()
        assert count1 == 1

        # Second run - should skip
        count2 = scan_and_process()
        assert count2 == 0

    def test_modified_file_reprocessed(self) -> None:
        """Test that modified XML file is reprocessed."""
        user_dir = self.input_dir / "user1"
        xml_file = user_dir / "backup.xml"
        self._create_test_xml(xml_file, self._get_sample_xml())

        # First run
        count1 = scan_and_process()
        assert count1 == 1

        # Modify file
        modified_xml = self._get_sample_xml().replace(
            "Test message", "Modified message"
        )
        self._create_test_xml(xml_file, modified_xml)

        # Second run - should reprocess
        count2 = scan_and_process()
        assert count2 == 1

    def test_multiple_users_separate_output(self) -> None:
        """Test that multiple users get separate output directories."""
        xml_content = self._get_sample_xml()

        # User 1
        user1_dir = self.input_dir / "user1"
        self._create_test_xml(user1_dir / "backup.xml", xml_content)

        # User 2
        user2_dir = self.input_dir / "user2"
        self._create_test_xml(user2_dir / "backup.xml", xml_content)

        count = scan_and_process()
        assert count == 2

        # Check separate output directories
        user1_out = self.output_dir / "user1"
        user2_out = self.output_dir / "user2"
        assert user1_out.exists()
        assert user2_out.exists()

    def test_state_persistence_across_runs(self) -> None:
        """Test that state file persists processed file hashes."""
        user_dir = self.input_dir / "user1"
        xml_file = user_dir / "backup.xml"
        self._create_test_xml(xml_file, self._get_sample_xml())

        # First run
        count1 = scan_and_process()
        assert count1 == 1

        # Verify state file exists and has content
        state = load_state()
        assert len(state) == 1

        # Create new module instance (simulate restart)
        # The state file should still be there
        state2 = load_state()
        assert len(state2) == 1

    def test_empty_input_directory(self) -> None:
        """Test handling of empty input directory."""
        count = scan_and_process()
        assert count == 0

    def test_nonexistent_input_directory(self) -> None:
        """Test handling of nonexistent input directory."""
        from sms_processor import core

        core.INPUT_DIR = self.tmp / "nonexistent"

        count = scan_and_process()
        assert count == 0

    def test_archiver_direct_integration(self) -> None:
        """Test archiver module directly with XML file."""
        xml_file = self.input_dir / "direct_test.xml"
        self._create_test_xml(xml_file, self._get_sample_xml())

        updated = process_xml_file(xml_file, self.output_dir, "direct_user")

        assert len(updated) == 2  # Two contacts

        # Verify markdown files created
        md_files = list(self.output_dir.rglob("*.md"))
        assert len(md_files) == 2

    def test_mms_integration(self) -> None:
        """Test MMS with attachments in full pipeline."""
        mms_xml = """<?xml version="1.0" encoding="UTF-8"?>
<backup>
  <mms address="+15551112222" date="1722053400000" type="1">
    <body>MMS with photo</body>
    <date>2024-01-01 00:00:00</date>
    <subject>Photo</subject>
    <part ct="image/jpeg" name="photo.jpg" loc="/storage/photo.jpg"/>
  </mms>
</backup>"""

        user_dir = self.input_dir / "user1"
        xml_file = user_dir / "mms_backup.xml"
        self._create_test_xml(xml_file, mms_xml)

        count = scan_and_process()
        assert count == 1

        # Verify output
        md_files = list(self.output_dir.rglob("*.md"))
        assert len(md_files) == 1

        content = md_files[0].read_text()
        assert "Photo" in content
        assert "photo.jpg" in content
        assert "Attachments" in content


class TestArchiverIntegration:
    """Integration tests for archiver with various XML inputs."""

    def test_process_xml_file_with_various_types(self) -> None:
        """Test archiver handles all SMS types."""
        import tempfile
        from pathlib import Path

        from sms_processor import archiver

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            out_dir = tmp_path / "output"
            out_dir.mkdir()

            xml = """<?xml version="1.0"?>
<backup>
  <sms address="+1" date="1" type="1"><body>Inbox</body><date>2024-01-01</date></sms>
  <sms address="+2" date="2" type="2"><body>Sent</body><date>2024-01-01</date></sms>
  <sms address="+3" date="3" type="3"><body>Failed</body><date>2024-01-01</date></sms>
  <sms address="+4" date="4" type="4"><body>Draft</body><date>2024-01-01</date></sms>
</backup>"""
            xml_file = tmp_path / "types.xml"
            xml_file.write_text(xml)

            updated = archiver.process_xml_file(xml_file, out_dir, "test")

            assert len(updated) == 4

            for md in out_dir.rglob("*.md"):
                content = md.read_text()
                assert any(t in content for t in ["Inbox", "Sent", "Failed", "Draft"])
