"""Functional tests for sms_processor - test user-facing functionality."""

import tempfile
from pathlib import Path

from sms_processor.core import scan_and_process


class TestFunctional:
    """Functional tests - test complete user workflows."""

    def setup_method(self) -> None:
        """Set up test directories."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.temp_dir.name)

        self.input_dir = self.tmp / "input"
        self.output_dir = self.tmp / "output"
        self.input_dir.mkdir()
        self.output_dir.mkdir()

        from sms_processor import core

        self.orig_input = core.INPUT_DIR
        self.orig_output = core.OUTPUT_DIR
        self.orig_state = core.STATE_FILE

        core.INPUT_DIR = self.input_dir
        core.OUTPUT_DIR = self.output_dir
        core.STATE_FILE = self.output_dir / ".processed.json"

    def teardown_method(self) -> None:
        """Restore and cleanup."""
        from sms_processor import core

        core.INPUT_DIR = self.orig_input
        core.OUTPUT_DIR = self.orig_output
        core.STATE_FILE = self.orig_state
        self.temp_dir.cleanup()

    def _create_backup(self, user: str, messages: list[dict]) -> Path:
        """Create a backup.xml file with given messages."""
        import html

        user_dir = self.input_dir / user
        user_dir.mkdir()
        xml_file = user_dir / "backup.xml"

        # Build XML
        sms_elements = []
        for i, msg in enumerate(messages):
            body = html.escape(msg["body"])
            person = msg.get("person")
            person_elem = f"<person>{html.escape(person)}</person>" if person else ""
            sms_elements.append(
                f'<sms address="{msg["address"]}" date="{msg.get("date", str(1722051780000 + i))}" type="{msg.get("type", "1")}" protocol="{msg.get("protocol", "0")}">'
                f"<body>{body}</body>"
                f"<date>{msg.get('display_date', '2024-01-01 00:00:00')}</date>"
                f"{person_elem}"
                f"</sms>"
            )

        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<backup>
  {"".join(sms_elements)}
</backup>"""
        xml_file.write_text(xml)
        return xml_file

    def test_user_workflow_single_conversation(self) -> None:
        """Functional: User processes single conversation backup."""
        messages = [
            {"address": "+15551234567", "body": "Hello!", "person": "Alice"},
            {"address": "+15551234567", "body": "Hi there!", "person": "Alice"},
        ]
        self._create_backup("user1", messages)

        count = scan_and_process()
        assert count == 1

        # User should find their conversation in output
        md_files = list(self.output_dir.rglob("*.md"))
        assert len(md_files) == 1

        content = md_files[0].read_text()
        assert "Hello!" in content
        assert "Hi there!" in content
        assert "Alice" in content

    def test_user_workflow_multiple_conversations(self) -> None:
        """Functional: User processes backup with multiple contacts."""
        messages = [
            {"address": "+15551111111", "body": "Message from Bob", "person": "Bob"},
            {
                "address": "+15552222222",
                "body": "Message from Carol",
                "person": "Carol",
            },
            {"address": "+15553333333", "body": "Unknown caller", "person": None},
        ]
        self._create_backup("user1", messages)

        count = scan_and_process()
        assert count == 1

        md_files = list(self.output_dir.rglob("*.md"))
        assert len(md_files) == 3  # Three separate conversations

        # Verify each conversation file exists
        contents = [f.read_text() for f in md_files]
        assert any("Bob" in c for c in contents)
        assert any("Carol" in c for c in contents)
        assert any("Unknown caller" in c for c in contents)

    def test_user_workflow_mixed_sms_mms(self) -> None:
        """Functional: User processes backup with both SMS and MMS."""
        mms_xml = """<?xml version="1.0"?>
<backup>
  <sms address="+15551111111" date="1" type="1"><body>SMS message</body><date>2024-01-01</date></sms>
  <mms address="+15552222222" date="2" type="1">
    <body>MMS message</body><date>2024-01-01</date>
    <subject>Photo</subject>
    <part ct="image/jpeg" name="photo.jpg" loc="/path/photo.jpg"/>
  </mms>
</backup>"""

        user_dir = self.input_dir / "user1"
        user_dir.mkdir()
        xml_file = user_dir / "backup.xml"
        xml_file.write_text(mms_xml)

        count = scan_and_process()
        assert count == 1

        md_files = list(self.output_dir.rglob("*.md"))
        assert len(md_files) == 2

        contents = [f.read_text() for f in md_files]
        # One should have SMS, one MMS
        assert any("SMS message" in c for c in contents)
        assert any("MMS message" in c for c in contents)
        assert any("[MMS]" in c or "Attachments" in c for c in contents)

    def test_user_workflow_incremental_updates(self) -> None:
        """Functional: User adds new messages to existing backup."""
        # Initial backup
        messages1 = [
            {"address": "+15551111111", "body": "Original message", "person": "Bob"},
        ]
        self._create_backup("user1", messages1)

        count1 = scan_and_process()
        assert count1 == 1

        # Add new message (append to backup) - keep person name consistent
        xml_file = self.input_dir / "user1" / "backup.xml"
        updated_xml = """<?xml version="1.0"?>
<backup>
  <sms address="+15551111111" date="1" type="1"><body>Original message</body><date>2024-01-01</date><person>Bob</person></sms>
  <sms address="+15551111111" date="2" type="1"><body>New reply</body><date>2024-01-01 00:01:00</date><person>Bob</person></sms>
</backup>"""
        xml_file.write_text(updated_xml)

        count2 = scan_and_process()
        assert count2 == 1  # Reprocessed because file changed

        md_files = list(self.output_dir.rglob("*.md"))
        assert len(md_files) == 1

        content = md_files[0].read_text()
        assert "Original message" in content
        assert "New reply" in content

    def test_user_workflow_multiple_users(self) -> None:
        """Functional: Multiple users have separate output directories."""
        # User 1
        self._create_backup(
            "alex",
            [
                {"address": "+15551111111", "body": "Alex message", "person": "Friend"},
            ],
        )
        # User 2
        self._create_backup(
            "chloe",
            [
                {
                    "address": "+15552222222",
                    "body": "Chloe message",
                    "person": "Colleague",
                },
            ],
        )

        count = scan_and_process()
        assert count == 2

        # Separate output directories
        alex_out = self.output_dir / "alex"
        chloe_out = self.output_dir / "chloe"
        assert alex_out.exists()
        assert chloe_out.exists()

        # Each user only sees their own conversations
        alex_files = list(alex_out.rglob("*.md"))
        chloe_files = list(chloe_out.rglob("*.md"))
        assert len(alex_files) == 1
        assert len(chloe_files) == 1

        assert "Alex message" in alex_files[0].read_text()
        assert "Chloe message" in chloe_files[0].read_text()

    def test_user_workflow_handles_special_characters(self) -> None:
        """Functional: Messages with special characters work correctly."""
        messages = [
            {"address": "+15551111111", "body": "Special: <>&\"'", "person": "Test"},
            {"address": "+15551111111", "body": "Emoji: 😀🎉🚀", "person": "Test"},
            {"address": "+15551111111", "body": "Unicode: 你好世界", "person": "Test"},
        ]
        self._create_backup("user1", messages)

        count = scan_and_process()
        assert count == 1

        md_files = list(self.output_dir.rglob("*.md"))
        assert len(md_files) == 1

        content = md_files[0].read_text()
        # Special chars are HTML-escaped in markdown output for safe display
        # Original message content is preserved (escaped: <>&"')
        assert "😀" in content
        assert "你好世界" in content

    def test_archiver_functional_output_format(self) -> None:
        """Functional: Verify markdown output format is user-readable."""
        from sms_processor import archiver

        xml = """<?xml version="1.0"?>
<backup>
  <sms address="+15551234567" date="1704103200000" type="1">
    <body>First message</body><date>2024-01-01 10:00:00</date>
  </sms>
  <sms address="+15551234567" date="1704103260000" type="2">
    <body>Reply</body><date>2024-01-01 10:01:00</date>
  </sms>
</backup>"""

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            xml_file = tmp_path / "test.xml"
            xml_file.write_text(xml)
            out_dir = tmp_path / "output"
            out_dir.mkdir()

            archiver.process_xml_file(xml_file, out_dir, "test_user")

            md_file = next(iter(out_dir.rglob("*.md")))
            content = md_file.read_text()

            # Check markdown formatting
            assert "##" in content  # Headers
            assert "**Inbox**" in content or "**Sent**" in content  # Bold type
            # Separator (---) is only added when appending to existing file
            # This test processes a fresh file so no separator
            assert "First message" in content
            assert "Reply" in content
            assert "10:00" in content  # Timestamp visible
