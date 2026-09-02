"""Tests for sms_processor.archiver."""

from pathlib import Path

from sms_processor.archiver import (
    contact_key,
    process_xml_file,
)

SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<backup>
  <sms address="+15551234567" date="1722051780000" protocol="0" type="1">
    <person>Mom</person>
    <body>Hey, running late tonight!</body>
    <date>2026-08-01 14:23:00</date>
    <type>1</type>
    <protocol>0</protocol>
  </sms>
  <sms address="+15551234567" date="1722051900000" protocol="0" type="2">
    <person>Mom</person>
    <body>No worries</body>
    <date>2026-08-01 14:25:00</date>
    <type>2</type>
    <protocol>0</protocol>
  </sms>
  <sms address="+15559876543" date="1722052800000" protocol="0" type="1">
    <body>Package delivered</body>
    <date>2026-08-01 14:40:00</date>
    <type>1</type>
  </sms>
  <mms address="+15551112222" date="1722053400000" protocol="0" type="1">
    <person>Chloe</person>
    <body>Photo!</body>
    <date>2026-08-01 14:50:00</date>
    <type>1</type>
    <subject>Photo</subject>
    <part ct="image/jpeg" name="photo.jpg" loc="/storage/photo.jpg"/>
  </mms>
</backup>
"""


class TestContactKey:
    def test_with_contact_name(self):
        key, display = contact_key("+15551234567", "Mom")
        assert key == "Mom (+15551234567)"
        assert display == "Mom"

    def test_without_contact_name(self):
        key, display = contact_key("+15559876543", None)
        assert key == "+15559876543"
        assert display == "+15559876543"


class TestProcessXmlFile:
    def test_process_file(self, tmp_path: Path):
        xml_file = tmp_path / "test.xml"
        xml_file.write_text(SAMPLE_XML)
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        updated = process_xml_file(xml_file, output_dir, "alex")
        assert len(updated) == 3

        mom_file = output_dir / "alex" / "Mom (+15551234567).md"
        assert mom_file.exists()
        content = mom_file.read_text()
        assert "running late" in content
        assert "Inbox" in content
        assert "Sent" in content

    def test_unknown_contact(self, tmp_path: Path):
        xml_file = tmp_path / "test.xml"
        xml_file.write_text(SAMPLE_XML)
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        process_xml_file(xml_file, output_dir, "alex")

        unknown_file = output_dir / "alex" / "+15559876543.md"
        assert unknown_file.exists()
        assert "Package delivered" in unknown_file.read_text()

    def test_mms_contact(self, tmp_path: Path):
        xml_file = tmp_path / "test.xml"
        xml_file.write_text(SAMPLE_XML)
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        process_xml_file(xml_file, output_dir, "alex")

        chloe_file = output_dir / "alex" / "Chloe (+15551112222).md"
        assert chloe_file.exists()
        assert "[MMS]" in chloe_file.read_text()
        assert "photo.jpg" in chloe_file.read_text()

    def test_empty_backup(self, tmp_path: Path):
        empty_xml = "<backup><call><number>123</number></call></backup>"
        xml_file = tmp_path / "empty.xml"
        xml_file.write_text(empty_xml)
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        updated = process_xml_file(xml_file, output_dir, "alex")
        assert len(updated) == 0

    def test_invalid_xml(self, tmp_path: Path):
        xml_file = tmp_path / "invalid.xml"
        xml_file.write_text("not valid xml <><>")
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        updated = process_xml_file(xml_file, output_dir, "alex")
        assert len(updated) == 0
