"""Tests for sms_processor.extractor."""

import xml.etree.ElementTree as ET

from sms_processor.extractor import (
    extract_contact_name,
    extract_protocol,
    extract_sms_messages,
    parse_timestamp,
)

SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<backup op_id="6376594f2a9576d9" app_version="9.6" backup_time="Fri Aug 01 14:30:00 2026">
  <sms address="+15551234567" date="1722051780000" protocol="0" type="1" sub_id="0">
    <person>Mom</person>
    <body>Hey, running late tonight!</body>
    <date>2026-08-01 14:23:00</date>
    <type>1</type>
    <protocol>0</protocol>
    <status>-1</status>
    <service_center>+15559998888</service_center>
  </sms>
  <sms address="+15551234567" date="1722051900000" protocol="0" type="2" sub_id="0">
    <person>Mom</person>
    <body>No worries, &apos;ll be here a while.</body>
    <date>2026-08-01 14:25:00</date>
    <type>2</type>
    <protocol>0</protocol>
    <status>-1</status>
  </sms>
  <sms address="+15559876543" date="1722052800000" protocol="0" type="1" sub_id="0">
    <body>Package delivered to front door</body>
    <date>2026-08-01 14:40:00</date>
    <type>1</type>
    <protocol>0</protocol>
    <status>-1</status>
  </sms>
  <mms address="+15551112222" date="1722053400000" protocol="0" type="1" sub_id="0">
    <person>Chloe</person>
    <body>Check out this photo!</body>
    <date>2026-08-01 14:50:00</date>
    <type>1</type>
    <protocol>0</protocol>
    <status>-1</status>
    <subject>Photo</subject>
    <part ct="image/jpeg" name="photo.jpg" loc="/storage/emulated/0/Pictures/photo.jpg"/>
  </mms>
</backup>
"""


class TestParseTimestamp:
    def test_datetime_format(self):
        ts = parse_timestamp("2026-08-01 14:23:00")
        assert ts is not None
        assert ts.year == 2026
        assert ts.month == 8
        assert ts.day == 1
        assert ts.hour == 14
        assert ts.minute == 23

    def test_epoch_milliseconds(self):
        ts = parse_timestamp("1722051780000")
        assert ts is not None
        assert ts.year == 2024

    def test_empty_string(self):
        assert parse_timestamp("") is None

    def test_none(self):
        assert parse_timestamp(None) is None


class TestExtractMessages:
    def test_message_count(self):
        root = ET.fromstring(SAMPLE_XML)
        msgs = extract_sms_messages(root)
        assert len(msgs) == 4

    def test_first_message(self):
        root = ET.fromstring(SAMPLE_XML)
        msgs = extract_sms_messages(root)
        assert msgs[0]["address"] == "+15551234567"
        assert msgs[0]["type"] == "Inbox"
        assert "running late" in msgs[0]["body"]
        assert msgs[0]["contact"] == "Mom"

    def test_html_entities_decoded(self):
        root = ET.fromstring(SAMPLE_XML)
        msgs = extract_sms_messages(root)
        assert "&apos;" not in msgs[1]["body"]
        assert "'" in msgs[1]["body"]

    def test_no_contact_name(self):
        root = ET.fromstring(SAMPLE_XML)
        msgs = extract_sms_messages(root)
        assert msgs[2]["address"] == "+15559876543"
        assert msgs[2]["contact"] is None

    def test_mms_with_attachment(self):
        root = ET.fromstring(SAMPLE_XML)
        msgs = extract_sms_messages(root)
        assert msgs[3]["is_mms"] is True
        assert len(msgs[3]["attachments"]) == 1
        assert msgs[3]["attachments"][0]["name"] == "photo.jpg"
        assert msgs[3]["subject"] == "Photo"

    def test_empty_backup(self):
        empty_xml = "<backup><call><number>123</number></call></backup>"
        root = ET.fromstring(empty_xml)
        msgs = extract_sms_messages(root)
        assert len(msgs) == 0


class TestExtractContactName:
    def test_person_attribute(self):
        elem = ET.fromstring('<sms person="Mom"/>')
        assert extract_contact_name(elem) == "Mom"

    def test_person_child(self):
        elem = ET.fromstring("<sms><person>Dad</person></sms>")
        assert extract_contact_name(elem) == "Dad"

    def test_no_person(self):
        elem = ET.fromstring("<sms/>")
        assert extract_contact_name(elem) is None


class TestExtractProtocol:
    def test_protocol_attribute(self):
        elem = ET.fromstring('<sms protocol="RCS"/>')
        assert extract_protocol(elem) == "RCS"
