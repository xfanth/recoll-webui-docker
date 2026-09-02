"""Contract tests for sms_processor - verify API contracts and data schemas."""

import xml.etree.ElementTree as ET

from sms_processor.extractor import (
    extract_contact_name,
    extract_protocol,
    extract_sms_messages,
    parse_timestamp,
)


class TestExtractorContract:
    """Contract tests for extractor module - verify output schema and types."""

    def test_parse_timestamp_returns_datetime_or_none(self) -> None:
        """parse_timestamp must return datetime or None."""
        result = parse_timestamp("2024-01-01 00:00:00")
        assert result is not None
        assert hasattr(result, "year")
        assert hasattr(result, "tzinfo")

        assert parse_timestamp("") is None
        assert parse_timestamp(None) is None
        assert parse_timestamp("invalid") is None

    def test_parse_timestamp_timezone_utc(self) -> None:
        """parse_timestamp must return UTC-aware datetime."""
        result = parse_timestamp("2024-01-01 00:00:00")
        assert result is not None
        assert result.tzinfo is not None

    def test_extract_sms_messages_returns_list_of_dicts(self) -> None:
        """extract_sms_messages must return List[Dict]."""
        xml = """<?xml version="1.0"?>
<backup>
  <sms address="+15551234567" date="1722051780000" type="1">
    <body>Test</body><date>2024-01-01 00:00:00</date>
  </sms>
</backup>"""
        root = ET.fromstring(xml)
        msgs = extract_sms_messages(root)

        assert isinstance(msgs, list)
        assert len(msgs) == 1
        assert isinstance(msgs[0], dict)

    def test_message_dict_required_keys(self) -> None:
        """Each message dict must contain all required keys with correct types."""
        xml = """<?xml version="1.0"?>
<backup>
  <sms address="+15551234567" date="1722051780000" type="1" protocol="0">
    <body>Test</body><date>2024-01-01 00:00:00</date>
  </sms>
</backup>"""
        root = ET.fromstring(xml)
        msgs = extract_sms_messages(root)
        msg = msgs[0]

        # Required keys
        assert "address" in msg
        assert isinstance(msg["address"], str)
        assert msg["address"].startswith("+")

        assert "timestamp" in msg
        assert msg["timestamp"] is None or hasattr(msg["timestamp"], "year")

        assert "date_str" in msg
        assert isinstance(msg["date_str"], str)

        assert "type" in msg
        assert isinstance(msg["type"], str)
        assert msg["type"] in ("Inbox", "Sent", "Failed", "Draft", "Unknown")

        assert "body" in msg
        assert isinstance(msg["body"], str)

        assert "protocol" in msg
        assert msg["protocol"] is None or isinstance(msg["protocol"], str)

        assert "contact" in msg
        assert msg["contact"] is None or isinstance(msg["contact"], str)

        assert "service_center" in msg
        assert msg["service_center"] is None or isinstance(msg["service_center"], str)

        assert "subject" in msg
        assert msg["subject"] is None or isinstance(msg["subject"], str)

        assert "attachments" in msg
        assert isinstance(msg["attachments"], list)

        assert "is_mms" in msg
        assert isinstance(msg["is_mms"], bool)

    def test_mms_message_has_attachment_structure(self) -> None:
        """MMS messages must have properly structured attachments."""
        xml = """<?xml version="1.0"?>
<backup>
  <mms address="+15551112222" date="1722053400000" type="1">
    <body>Check photo</body><date>2024-01-01 00:00:00</date>
    <subject>Photo</subject>
    <part ct="image/jpeg" name="photo.jpg" loc="/path/photo.jpg"/>
  </mms>
</backup>"""
        root = ET.fromstring(xml)
        msgs = extract_sms_messages(root)

        assert msgs[0]["is_mms"] is True
        assert len(msgs[0]["attachments"]) == 1
        att = msgs[0]["attachments"][0]
        assert "type" in att
        assert "name" in att
        assert "location" in att

    def test_extract_contact_name_returns_str_or_none(self) -> None:
        """extract_contact_name must return str or None."""
        elem = ET.fromstring('<sms person="Test"/>')
        result = extract_contact_name(elem)
        assert isinstance(result, str)

        elem2 = ET.fromstring("<sms/>")
        assert extract_contact_name(elem2) is None

    def test_extract_protocol_returns_str_or_none(self) -> None:
        """extract_protocol must return str or None."""
        elem = ET.fromstring('<sms protocol="RCS"/>')
        result = extract_protocol(elem)
        assert isinstance(result, str)
        assert result == "RCS"

        elem2 = ET.fromstring("<sms/>")
        assert extract_protocol(elem2) is None

    def test_type_mapping_contract(self) -> None:
        """SMS type codes must map to expected labels."""
        type_cases = [
            ("1", "Inbox"),
            ("2", "Sent"),
            ("3", "Failed"),
            ("4", "Draft"),
            ("999", "Unknown"),
        ]
        for code, expected in type_cases:
            xml = f"""<?xml version="1.0"?>
<backup>
  <sms address="+15551234567" type="{code}">
    <body>Test</body><date>2024-01-01 00:00:00</date>
  </sms>
</backup>"""
            root = ET.fromstring(xml)
            msgs = extract_sms_messages(root)
            assert msgs[0]["type"] == expected


class TestCoreContract:
    """Contract tests for core processing module."""

    def test_file_hash_returns_32_char_hex(self) -> None:
        """file_hash must return 32-char hex string."""
        from pathlib import Path

        from sms_processor.core import file_hash

        test_file = Path("/tmp/test_hash.bin")
        test_file.write_bytes(b"test")
        try:
            h = file_hash(test_file)
            assert isinstance(h, str)
            assert len(h) == 32
            assert all(c in "0123456789abcdef" for c in h)
        finally:
            test_file.unlink(missing_ok=True)

    def test_load_state_returns_dict(self) -> None:
        """load_state must return dict."""
        # Use temp file
        import tempfile
        from pathlib import Path

        from sms_processor.core import load_state

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{}")
            temp_path = Path(f.name)

        original_state = __import__(
            "sms_processor.core", fromlist=["STATE_FILE"]
        ).STATE_FILE
        try:
            __import__(
                "sms_processor.core", fromlist=["STATE_FILE"]
            ).STATE_FILE = temp_path
            state = load_state()
            assert isinstance(state, dict)
        finally:
            __import__(
                "sms_processor.core", fromlist=["STATE_FILE"]
            ).STATE_FILE = original_state
            temp_path.unlink(missing_ok=True)

    def test_save_state_accepts_dict(self) -> None:
        """save_state must accept dict[str, str]."""
        import tempfile
        from pathlib import Path

        from sms_processor.core import load_state, save_state

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            temp_path = Path(f.name)

        original_state = __import__(
            "sms_processor.core", fromlist=["STATE_FILE"]
        ).STATE_FILE
        try:
            __import__(
                "sms_processor.core", fromlist=["STATE_FILE"]
            ).STATE_FILE = temp_path
            save_state({"file.xml": "abc123"})
            loaded = load_state()
            assert loaded == {"file.xml": "abc123"}
        finally:
            __import__(
                "sms_processor.core", fromlist=["STATE_FILE"]
            ).STATE_FILE = original_state
            temp_path.unlink(missing_ok=True)


class TestArchiverContract:
    """Contract tests for archiver output format."""

    def test_contact_key_returns_tuple(self) -> None:
        """contact_key must return tuple of (key, display_name)."""
        from sms_processor.archiver import contact_key

        key, display = contact_key("+15551234567", "Mom")
        assert isinstance(key, str)
        assert isinstance(display, str)

    def test_contact_key_with_name(self) -> None:
        """contact_key with name must include name in key."""
        from sms_processor.archiver import contact_key

        key, display = contact_key("+15551234567", "Mom")
        assert "Mom" in key
        assert display == "Mom"

    def test_contact_key_without_name(self) -> None:
        """contact_key without name must use address only."""
        from sms_processor.archiver import contact_key

        key, display = contact_key("+15551234567", None)
        assert key == "+15551234567"
        assert display == "+15551234567"
