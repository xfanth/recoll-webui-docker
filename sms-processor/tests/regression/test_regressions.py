"""Regression tests for sms_processor - verify bug fixes don't regress."""

import xml.etree.ElementTree as ET

from sms_processor.extractor import extract_sms_messages


class TestRegression:
    """Regression tests for previously fixed bugs."""

    def test_duplicate_message_ids_not_double_counted(self) -> None:
        """Regression: messages with same address+timestamp should not be deduplicated incorrectly."""
        xml = """<?xml version="1.0"?>
<backup>
  <sms address="+15551234567" date="1722051780000" type="1">
    <body>First</body><date>2024-01-01 00:00:00</date>
  </sms>
  <sms address="+15551234567" date="1722051780000" type="1">
    <body>Second</body><date>2024-01-01 00:00:00</date>
  </sms>
</backup>"""
        root = ET.fromstring(xml)
        msgs = extract_sms_messages(root)
        assert len(msgs) == 2  # Both should be counted

    def test_empty_body_preserved(self) -> None:
        """Regression: empty message body should not cause index error."""
        xml = """<?xml version="1.0"?>
<backup>
  <sms address="+15551234567" date="1722051780000" type="1">
    <body></body><date>2024-01-01 00:00:00</date>
  </sms>
</backup>"""
        root = ET.fromstring(xml)
        msgs = extract_sms_messages(root)
        assert len(msgs) == 1
        assert msgs[0]["body"] == "(empty message)"

    def test_special_characters_in_body(self) -> None:
        """Regression: special XML/HTML chars should be handled correctly."""
        # Test with HTML-escaped content (realistic XML)
        import html

        escaped_body = html.escape("<script>alert(1)</script>")
        xml = f"""<?xml version="1.0"?>
<backup>
  <sms address="+15551234567" date="1722051780000" type="1">
    <body>{escaped_body}</body>
    <date>2024-01-01 00:00:00</date>
  </sms>
</backup>"""
        root = ET.fromstring(xml)
        msgs = extract_sms_messages(root)
        # HTML entities should be decoded back
        assert "<script>" in msgs[0]["body"]

    def test_multilingual_contact_names(self) -> None:
        """Regression: Unicode contact names should be preserved."""
        xml = """<?xml version="1.0"?>
<backup>
  <sms address="+15551234567" date="1722051780000" type="1">
    <person>张三</person>
    <body>Hello</body><date>2024-01-01 00:00:00</date>
  </sms>
</backup>"""
        root = ET.fromstring(xml)
        msgs = extract_sms_messages(root)
        assert msgs[0]["contact"] == "张三"

    def test_very_long_message_body(self) -> None:
        """Regression: messages with very long bodies should not crash."""
        long_body = "x" * 10000
        xml = f"""<?xml version="1.0"?>
<backup>
  <sms address="+15551234567" date="1722051780000" type="1">
    <body>{long_body}</body><date>2024-01-01 00:00:00</date>
  </sms>
</backup>"""
        root = ET.fromstring(xml)
        msgs = extract_sms_messages(root)
        assert len(msgs[0]["body"]) == 10000

    def test_missing_date_attribute(self) -> None:
        """Regression: missing date attribute should not crash."""
        xml = """<?xml version="1.0"?>
<backup>
  <sms address="+15551234567" type="1">
    <body>No date</body>
  </sms>
</backup>"""
        root = ET.fromstring(xml)
        msgs = extract_sms_messages(root)
        assert len(msgs) == 1
        assert msgs[0]["timestamp"] is None
