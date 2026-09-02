"""Smoke tests for sms_processor - quick sanity checks."""


class TestSmoke:
    """Smoke tests - fast tests that verify basic functionality."""

    def test_import_extractor(self) -> None:
        """Verify extractor module imports without error."""
        from sms_processor import extractor

        assert extractor is not None

    def test_import_core(self) -> None:
        """Verify core module imports without error."""
        from sms_processor import core

        assert core is not None

    def test_import_archiver(self) -> None:
        """Verify archiver module imports without error."""
        from sms_processor import archiver

        assert archiver is not None

    def test_parse_timestamp_basic(self) -> None:
        """Basic smoke test for parse_timestamp."""
        from sms_processor.extractor import parse_timestamp

        result = parse_timestamp("2024-01-01 12:00:00")
        assert result is not None

    def test_extract_sms_messages_basic(self) -> None:
        """Basic smoke test for extract_sms_messages."""
        import xml.etree.ElementTree as ET

        from sms_processor.extractor import extract_sms_messages

        xml = """<?xml version="1.0"?>
<backup><sms address="+1" date="1" type="1"><body>Test</body></sms></backup>"""
        root = ET.fromstring(xml)
        msgs = extract_sms_messages(root)
        assert len(msgs) == 1

    def test_contact_key_basic(self) -> None:
        """Basic smoke test for contact_key."""
        from sms_processor.archiver import contact_key

        key, display = contact_key("+15551234567", "Test")
        assert key
        assert display

    def test_file_hash_basic(self) -> None:
        """Basic smoke test for file_hash."""
        import tempfile
        from pathlib import Path

        from sms_processor.core import file_hash

        with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f:
            f.write(b"test")
            tmp = Path(f.name)

        try:
            h = file_hash(tmp)
            assert len(h) == 32
        finally:
            tmp.unlink()

    def test_load_state_basic(self) -> None:
        """Basic smoke test for load_state."""
        import tempfile
        from pathlib import Path

        from sms_processor.core import load_state

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{}")
            tmp = Path(f.name)

        from sms_processor import core

        orig = core.STATE_FILE
        try:
            core.STATE_FILE = tmp
            state = load_state()
            assert isinstance(state, dict)
        finally:
            core.STATE_FILE = orig
            tmp.unlink()
