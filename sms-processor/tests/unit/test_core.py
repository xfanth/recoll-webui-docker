"""Tests for sms_processor.core."""

import hashlib
from pathlib import Path

from sms_processor.core import file_hash, load_state, save_state, scan_and_process


class TestFileHash:
    def test_consistent_hash(self, tmp_path: Path):
        f = tmp_path / "test.txt"
        f.write_text("hello")
        assert file_hash(f) == hashlib.md5(b"hello").hexdigest()

    def test_different_content(self, tmp_path: Path):
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("hello")
        f2.write_text("world")
        assert file_hash(f1) != file_hash(f2)


class TestStateManagement:
    def test_load_empty_state(self, tmp_path: Path):
        # Temporarily override STATE_FILE
        from sms_processor import core

        original = core.STATE_FILE
        core.STATE_FILE = tmp_path / ".processed.json"
        try:
            state = load_state()
            assert state == {}
        finally:
            core.STATE_FILE = original

    def test_save_and_load_state(self, tmp_path: Path):
        from sms_processor import core

        original = core.STATE_FILE
        core.STATE_FILE = tmp_path / ".processed.json"
        try:
            save_state({"a.xml": "abc123"})
            state = load_state()
            assert state == {"a.xml": "abc123"}
        finally:
            core.STATE_FILE = original

    def test_corrupt_state_file(self, tmp_path: Path):
        from sms_processor import core

        original = core.STATE_FILE
        core.STATE_FILE = tmp_path / ".processed.json"
        try:
            core.STATE_FILE.write_text("not valid json")
            state = load_state()
            assert state == {}
        finally:
            core.STATE_FILE = original


class TestScanAndProcess:
    def test_no_input_dir(self, tmp_path: Path):
        from sms_processor import core

        original_input = core.INPUT_DIR
        original_output = core.OUTPUT_DIR
        original_state = core.STATE_FILE
        core.INPUT_DIR = tmp_path / "nonexistent"
        core.OUTPUT_DIR = tmp_path / "output"
        core.STATE_FILE = core.OUTPUT_DIR / ".processed.json"
        core.OUTPUT_DIR.mkdir()
        try:
            count = scan_and_process()
            assert count == 0
        finally:
            core.INPUT_DIR = original_input
            core.OUTPUT_DIR = original_output
            core.STATE_FILE = original_state

    def test_processes_new_files(self, tmp_path: Path):
        from sms_processor import core

        original_input = core.INPUT_DIR
        original_output = core.OUTPUT_DIR
        original_state = core.STATE_FILE
        core.INPUT_DIR = tmp_path / "input"
        core.OUTPUT_DIR = tmp_path / "output"
        core.STATE_FILE = core.OUTPUT_DIR / ".processed.json"
        core.INPUT_DIR.mkdir()
        core.OUTPUT_DIR.mkdir()

        user_dir = core.INPUT_DIR / "user1"
        user_dir.mkdir()
        xml_file = user_dir / "backup.xml"
        xml_file.write_text(
            '<?xml version="1.0"?>'
            "<backup>"
            '<sms address="+123" date="1722051780000" type="1">'
            "<body>test</body><date>2024-01-01 00:00:00</date></sms>"
            "</backup>"
        )

        try:
            count = scan_and_process()
            assert count == 1
        finally:
            core.INPUT_DIR = original_input
            core.OUTPUT_DIR = original_output
            core.STATE_FILE = original_state

    def test_skips_processed_files(self, tmp_path: Path):
        from sms_processor import core

        original_input = core.INPUT_DIR
        original_output = core.OUTPUT_DIR
        original_state = core.STATE_FILE
        core.INPUT_DIR = tmp_path / "input"
        core.OUTPUT_DIR = tmp_path / "output"
        core.STATE_FILE = core.OUTPUT_DIR / ".processed.json"
        core.INPUT_DIR.mkdir()
        core.OUTPUT_DIR.mkdir()

        user_dir = core.INPUT_DIR / "user1"
        user_dir.mkdir()
        xml_file = user_dir / "backup.xml"
        xml_file.write_text(
            '<?xml version="1.0"?>'
            "<backup>"
            '<sms address="+123" date="1722051780000" type="1">'
            "<body>test</body><date>2024-01-01 00:00:00</date></sms>"
            "</backup>"
        )

        try:
            # First run processes
            assert scan_and_process() == 1
            # Second run skips
            assert scan_and_process() == 0
        finally:
            core.INPUT_DIR = original_input
            core.OUTPUT_DIR = original_output
            core.STATE_FILE = original_state
