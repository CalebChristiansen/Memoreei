from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from memoreei.connectors.sms_connector import parse_sms_backup


SMS_XML = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8" standalone="yes" ?>
    <smses count="3">
      <sms protocol="0" address="+15551234567" date="1609459200000" type="1"
           body="Hey, are you free tonight?" toa="null" sc_toa="null"
           service_center="null" read="1" status="-1" locked="0" date_sent="0"
           readable_date="Jan 1, 2021 12:00:00 AM" contact_name="Alice" />
      <sms protocol="0" address="+15551234567" date="1609459260000" type="2"
           body="Yes! What did you have in mind?" toa="null" sc_toa="null"
           service_center="null" read="1" status="-1" locked="0" date_sent="0"
           readable_date="Jan 1, 2021 12:01:00 AM" contact_name="Alice" />
      <sms protocol="0" address="+15559876543" date="1609459300000" type="1"
           body="Don't forget the meeting tomorrow" toa="null" sc_toa="null"
           service_center="null" read="1" status="-1" locked="0" date_sent="0"
           readable_date="Jan 1, 2021 12:01:40 AM" contact_name="null" />
    </smses>
""")

MMS_XML = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8" standalone="yes" ?>
    <smses count="1">
      <mms date="1609459400000" rr="null" sub="null" ct_t="application/vnd.wap.multipart.related"
           read_status="null" seen="1" m_id="null" retr_txt_cs="null" retr_txt="null"
           date_sent="0" m_cls="personal" d_tm="null" v="18" exp="null" m_size="1234"
           pri="129" rpt_a="null" resp_txt="null" ct_cls="null" d_rpt="129" locked="0"
           from_address="null" address="+15551234567" m_retries="3" retr_st="null"
           status="32" sub_cs="null" read="1" m_type="132" resp_st="129" ct_l="null"
           tr_id="null" tc="null" msg_box="1" readable_date="Jan 1, 2021 12:03:20 AM"
           contact_name="Alice">
        <parts>
          <part seq="0" ct="image/jpeg" name="null" chset="null" cd="null" fn="null"
                cid="image_0" cl="image_0.jpg" ctt_s="null" ctt_t="null" text="null"
                data="base64datahere" />
          <part seq="1" ct="text/plain" name="null" chset="null" cd="null" fn="null"
                cid="text_0" cl="text_0.txt" ctt_s="null" ctt_t="null"
                text="Check out this photo!" data="null" />
        </parts>
        <addrs>
          <addr address="+15551234567" type="151" charset="106" />
          <addr address="insert-address-token" type="137" charset="106" />
        </addrs>
      </mms>
    </smses>
""")

MIXED_XML = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8" standalone="yes" ?>
    <smses count="2">
      <sms protocol="0" address="+15551234567" date="1609459200000" type="1"
           body="Hello" toa="null" service_center="null" read="1" status="-1"
           locked="0" date_sent="0" readable_date="Jan 1, 2021 12:00:00 AM"
           contact_name="Bob" />
      <mms date="1609459500000" rr="null" sub="null" ct_t="application/vnd.wap.multipart.related"
           read_status="null" seen="1" m_id="null" date_sent="0" m_cls="personal"
           v="18" exp="null" m_size="100" pri="129" locked="0" from_address="null"
           address="+15551234567" status="32" read="1" m_type="132" msg_box="2"
           readable_date="Jan 1, 2021 12:05:00 AM" contact_name="Bob">
        <parts>
          <part seq="0" ct="text/plain" name="null" chset="null" cd="null" fn="null"
                cid="text_0" cl="text_0.txt" ctt_s="null" ctt_t="null"
                text="Got it, thanks!" data="null" />
        </parts>
        <addrs>
          <addr address="+15551234567" type="151" charset="106" />
        </addrs>
      </mms>
    </smses>
""")


def test_parse_sms_elements(tmp_path: Path) -> None:
    xml_file = tmp_path / "backup.xml"
    xml_file.write_text(SMS_XML, encoding="utf-8")

    items = parse_sms_backup(xml_file)

    assert len(items) == 3
    assert all(item.content for item in items)
    assert all(item.ts > 0 for item in items)
    assert all(item.source.startswith("sms:") for item in items)


def test_sms_received_vs_sent(tmp_path: Path) -> None:
    xml_file = tmp_path / "backup.xml"
    xml_file.write_text(SMS_XML, encoding="utf-8")

    items = parse_sms_backup(xml_file)

    # First message: type=1 (received) from Alice
    received = items[0]
    assert received.metadata["type"] == "received"
    assert received.content.startswith("Alice: ")

    # Second message: type=2 (sent) → sender is "me"
    sent = items[1]
    assert sent.metadata["type"] == "sent"
    assert sent.content.startswith("me: ")


def test_sms_contact_name_used_as_source(tmp_path: Path) -> None:
    xml_file = tmp_path / "backup.xml"
    xml_file.write_text(SMS_XML, encoding="utf-8")

    items = parse_sms_backup(xml_file)

    alice_items = [i for i in items if i.source == "sms:Alice"]
    assert len(alice_items) == 2

    # Third message has contact_name="null", should fall back to phone number
    no_name = items[2]
    assert no_name.source == "sms:+15559876543"


def test_parse_mms_elements(tmp_path: Path) -> None:
    xml_file = tmp_path / "backup.xml"
    xml_file.write_text(MMS_XML, encoding="utf-8")

    items = parse_sms_backup(xml_file)

    assert len(items) == 1
    item = items[0]
    assert "Check out this photo!" in item.content
    assert item.metadata["message_type"] == "mms"
    assert item.source == "sms:Alice"


def test_mms_skips_non_text_parts(tmp_path: Path) -> None:
    xml_file = tmp_path / "backup.xml"
    xml_file.write_text(MMS_XML, encoding="utf-8")

    items = parse_sms_backup(xml_file)

    # Should only include the text/plain part content, not base64 image data
    assert "base64datahere" not in items[0].content


def test_mixed_sms_and_mms(tmp_path: Path) -> None:
    xml_file = tmp_path / "backup.xml"
    xml_file.write_text(MIXED_XML, encoding="utf-8")

    items = parse_sms_backup(xml_file)

    assert len(items) == 2
    sms_item = items[0]
    mms_item = items[1]

    assert sms_item.metadata["message_type"] == "sms"
    assert mms_item.metadata["message_type"] == "mms"
    assert mms_item.metadata["type"] == "sent"
    assert mms_item.content.startswith("me: ")


def test_source_id_uniqueness(tmp_path: Path) -> None:
    xml_file = tmp_path / "backup.xml"
    xml_file.write_text(SMS_XML, encoding="utf-8")

    items = parse_sms_backup(xml_file)
    source_ids = [item.source_id for item in items]
    assert len(source_ids) == len(set(source_ids))


def test_participants_recorded(tmp_path: Path) -> None:
    xml_file = tmp_path / "backup.xml"
    xml_file.write_text(SMS_XML, encoding="utf-8")

    items = parse_sms_backup(xml_file)

    alice_item = items[0]
    assert "Alice" in alice_item.participants
    assert "me" in alice_item.participants


def test_empty_body_skipped(tmp_path: Path) -> None:
    xml = textwrap.dedent("""\
        <?xml version="1.0" encoding="UTF-8" standalone="yes" ?>
        <smses count="2">
          <sms address="+15551234567" date="1609459200000" type="1"
               body="" contact_name="Alice" />
          <sms address="+15551234567" date="1609459260000" type="1"
               body="Real message" contact_name="Alice" />
        </smses>
    """)
    xml_file = tmp_path / "backup.xml"
    xml_file.write_text(xml, encoding="utf-8")

    items = parse_sms_backup(xml_file)
    assert len(items) == 1
    assert "Real message" in items[0].content
