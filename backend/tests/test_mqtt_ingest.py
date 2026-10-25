import json
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.engine.mqtt_codec import parse_mqtt_payload
from app.engine.mqtt_history import filter_project_messages
from datetime import datetime, timezone

from app.engine.mqtt_ingest import (
    build_dev_index,
    invalidate_dev_index,
    list_mqtt_events,
    persist_mqtt_event,
    project_dev_keys,
    resolve_project_by_dev,
)
from app.services.pid_rules import match_contained_pid, pid_conflict
from app.engine.parse import extract_epcs
from app.models import Project, Trolley

SAMPLE_HEX = "BB02220011C3300E2806894000040E1ECCC8C53DDFA07E"
SAMPLE_EPC = "E2806894000040E1ECCC8C53"


class MqttCodecTests(unittest.TestCase):
    def test_parse_dev_hex_payload(self):
        raw = json.dumps({"dev": "OF2", "hex": SAMPLE_HEX})
        got = parse_mqtt_payload("dev/coo/OF2", raw)
        self.assertEqual(got["dev"], "OF2")
        self.assertEqual(got["deviceId"], "OF2")
        self.assertEqual(got["hex"], SAMPLE_HEX)

    def test_parse_hn_device(self):
        raw = json.dumps({"dev": "HN-24", "hex": SAMPLE_HEX})
        got = parse_mqtt_payload("dev/coo/HN-24", raw.encode("utf-8"))
        self.assertEqual(got["dev"], "HN-24")

    def test_topic_fills_missing_dev(self):
        raw = json.dumps({"hex": SAMPLE_HEX})
        got = parse_mqtt_payload("dev/coo/YL", raw)
        self.assertEqual(got["dev"], "YL")

    def test_skip_sensor_array(self):
        raw = json.dumps([{"sensor_device_id": 1, "port_id": 1, "sdata": 680}])
        self.assertIsNone(parse_mqtt_payload("dev/coo/1122344", raw))

    def test_skip_missing_hex(self):
        self.assertIsNone(parse_mqtt_payload("dev/coo/OF2", json.dumps({"dev": "OF2"})))

    def test_extract_epc_from_bb02_frame(self):
        self.assertEqual(extract_epcs(SAMPLE_HEX, []), [SAMPLE_EPC])
        self.assertEqual(
            extract_epcs("BB02220011B83000E28068940000402C63E664427C2B7D7E", []),
            ["E28068940000402C63E66442"],
        )


class MqttProjectMapTests(unittest.TestCase):
    def setUp(self):
        invalidate_dev_index()
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False)
        self.db = self.Session()
        of = Project(pid="OF2", name="临沂OF", ingest_token="tok-of2")
        hn = Project(pid="HN", name="淮南", ingest_token="tok-hn")
        self.db.add_all([of, hn])
        self.db.flush()
        self.db.add(Trolley(project_id=hn.id, tz="TZ1", name="台车1", reader="HN-24"))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        invalidate_dev_index()

    def test_index_prefers_pid_then_name_then_reader(self):
        of = self.db.query(Project).filter_by(pid="OF2").one()
        hn = self.db.query(Project).filter_by(pid="HN").one()
        index = build_dev_index([of, hn])
        self.assertEqual(index["of2"], of.id)
        self.assertEqual(index["临沂of"], of.id)
        self.assertEqual(index["hn-24"], hn.id)

    def test_resolve_by_pid(self):
        p = resolve_project_by_dev(self.db, "OF2")
        self.assertEqual(p.pid, "OF2")

    def test_resolve_by_reader(self):
        p = resolve_project_by_dev(self.db, "HN-24")
        self.assertEqual(p.pid, "HN")

    def test_unknown_dev(self):
        self.assertIsNone(resolve_project_by_dev(self.db, "test50"))

    def test_resolve_by_contained_pid(self):
        p = resolve_project_by_dev(self.db, "HN-99")
        self.assertEqual(p.pid, "HN")

    def test_project_dev_keys_include_pid_name_reader(self):
        hn = self.db.query(Project).filter_by(pid="HN").one()
        keys = project_dev_keys(hn)
        self.assertIn("hn", keys)
        self.assertIn("淮南", keys)
        self.assertIn("hn-24", keys)

    def test_contained_pid_prefers_longest(self):
        self.assertEqual(match_contained_pid("OF2", ["OF", "PY"]), "OF")
        self.assertEqual(match_contained_pid("LYG10", ["LYG", "OF"]), "LYG")
        self.assertEqual(match_contained_pid("RFID1", ["HL_Test", "OF"]), "")

    def test_pid_cannot_contain_existing(self):
        self.assertIn("不能包含", pid_conflict("OF2", ["OF"]))
        self.assertIn("已存在", pid_conflict("OF", ["OF"]))
        self.assertEqual(pid_conflict("PY", ["OF", "LYG"]), "")

    def test_list_mqtt_events_matches_contained_pid(self):
        now = datetime.now(timezone.utc)
        persist_mqtt_event(self.db, {"dev": "HN-99", "hex": SAMPLE_HEX, "topic": "dev/coo/HN-99"}, now)
        persist_mqtt_event(self.db, {"dev": "RFID1", "hex": SAMPLE_HEX, "topic": "dev/coo/RFID1"}, now)
        self.db.commit()
        all_rows = list_mqtt_events(self.db)
        self.assertEqual({r["deviceId"] for r in all_rows["items"]}, {"HN-99", "RFID1"})
        hn_rows = list_mqtt_events(self.db, pid="HN")
        self.assertEqual([r["projectPid"] for r in hn_rows["items"]], ["HN"])
        none_rows = list_mqtt_events(self.db, pid="__none__")
        self.assertEqual([r["deviceId"] for r in none_rows["items"]], ["RFID1"])

    def test_filter_keeps_only_this_project(self):
        hn = self.db.query(Project).filter_by(pid="HN").one()
        keys = project_dev_keys(hn)
        messages = [
            ("dev/coo/HN-24", json.dumps({"dev": "HN-24", "hex": SAMPLE_HEX})),
            ("dev/coo/OF2", json.dumps({"dev": "OF2", "hex": SAMPLE_HEX})),
            ("dev/coo/1122344", json.dumps([{"sensor_device_id": 1, "sdata": 1}])),
        ]
        matched = filter_project_messages(messages, keys)
        self.assertEqual([m["dev"] for m in matched], ["HN-24"])


if __name__ == "__main__":
    unittest.main()
