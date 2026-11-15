import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.engine.parse import apply_report, should_write_raw
from app.engine.runtime import add_calendar_months, apply_place, asset_details, batch_repush_ids, cleanup_retention, loc_of
from app.models import Area, Epc, Line, Procedure, Project, PushRecord, RawReport, TagNo, Trolley


def _now():
    return datetime(2026, 11, 15, 8, 0, tzinfo=timezone.utc)


class OccupyHexTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)
        self.db = self.Session()
        self.project, self.trolleys = self._seed(area_single=True)

    def tearDown(self):
        self.db.close()

    def _seed(self, area_single=True, hex_agg_min=5, inactive_days=30):
        db = self.db
        p = Project(
            pid="OF",
            name="制梁",
            ingest_token="tok-occupy",
            area_single=area_single,
            hex_agg_min=hex_agg_min,
            inactive_days=inactive_days,
            raw_keep_month=1,
            push_keep_month=1,
        )
        db.add(p)
        db.flush()
        line = Line(code="L1", name="1#线", sort_order=1, project_id=p.id)
        db.add(line)
        db.flush()
        trolleys = []
        epc_n = 1
        for i in range(3):
            proc = Procedure(code=f"G{i+1}", name=f"工序{i+1}", sort_order=i + 1, line_id=line.id)
            db.add(proc)
            db.flush()
            for j in range(2):
                area = Area(code=f"A{i+1}{j+1}", name=f"区域{i+1}-{j+1}", sort_order=j + 1, procedure_id=proc.id)
                db.add(area)
                db.flush()
                tag = TagNo(no=f"NO{i+1}{j+1}", sort_order=1, area_id=area.id)
                db.add(tag)
                db.flush()
                epc = f"E2806894000040E1ECCC8C{epc_n:02d}"
                epc_n += 1
                db.add(Epc(project_id=p.id, tag_no_id=tag.id, epc_raw=epc, epc_norm=epc))
        for i in range(4):
            t = Trolley(project_id=p.id, tz=f"TZ{i+1}", name=f"台座{i+1}", reader=f"R{i+1}")
            db.add(t)
            trolleys.append(t)
        db.commit()
        p = db.get(Project, p.id)
        db.refresh(p)
        line = p.lines[0]
        for proc in line.procedures:
            _ = list(proc.areas)
        return p, trolleys

    def _reload(self):
        self.project = self.db.get(Project, self.project.id)
        self.trolleys = list(self.project.trolleys)
        return self.project.lines[0]

    def _place(self, trolley, proc_i, area_i, make_push=False):
        line = self._reload()
        proc = line.procedures[proc_i]
        area = proc.areas[area_i]
        apply_place(
            self.db,
            self.project,
            trolley,
            line,
            proc,
            area,
            source="auto",
            kind="实时上报",
            test=False,
            sport_state="moving",
            identify_time=_now(),
            make_push=make_push,
        )
        self.db.flush()
        return loc_of(self.db, self.project, trolley)

    def test_same_proc_next_area(self):
        t1, t2 = self.trolleys[0], self.trolleys[1]
        self._place(t1, 0, 0)
        self._place(t2, 0, 0)
        loc1 = loc_of(self.db, self.project, t1)
        loc2 = loc_of(self.db, self.project, t2)
        self.assertFalse(loc2.unassigned)
        self.assertEqual(loc2.area_code, "A11")
        self.assertFalse(loc1.unassigned)
        self.assertEqual(loc1.proc_code, "G1")
        self.assertEqual(loc1.area_code, "A12")
        line = self._reload()
        vacated = line.procedures[0].areas[0]
        self.assertEqual(vacated.reissue_count, 1)

    def test_first_proc_last_area_goes_idle(self):
        t1, t2 = self.trolleys[0], self.trolleys[1]
        self._place(t1, 0, 1)
        self._place(t2, 0, 1)
        loc1 = loc_of(self.db, self.project, t1)
        self.assertTrue(loc1.unassigned)
        self.assertEqual(loc1.area_code, "")

    def test_middle_proc_last_area_to_next_proc(self):
        t1, t2 = self.trolleys[0], self.trolleys[1]
        self._place(t1, 1, 1)
        self._place(t2, 1, 1)
        loc1 = loc_of(self.db, self.project, t1)
        self.assertFalse(loc1.unassigned)
        self.assertEqual(loc1.proc_code, "G3")
        self.assertEqual(loc1.area_code, "A31")

    def test_last_proc_last_area_goes_idle(self):
        t1, t2 = self.trolleys[0], self.trolleys[1]
        self._place(t1, 2, 1)
        self._place(t2, 2, 1)
        loc1 = loc_of(self.db, self.project, t1)
        self.assertTrue(loc1.unassigned)

    def test_cascade_middle_slots(self):
        t1, t2, t3 = self.trolleys[0], self.trolleys[1], self.trolleys[2]
        self._place(t1, 1, 0)
        self._place(t2, 1, 1)
        self._place(t3, 1, 0)
        loc1 = loc_of(self.db, self.project, t1)
        loc2 = loc_of(self.db, self.project, t2)
        loc3 = loc_of(self.db, self.project, t3)
        self.assertEqual(loc3.area_code, "A21")
        self.assertEqual(loc1.area_code, "A22")
        self.assertEqual(loc2.proc_code, "G3")
        self.assertEqual(loc2.area_code, "A31")

    def test_multi_mode_no_squeeze(self):
        self.project.area_single = False
        self.db.flush()
        t1, t2 = self.trolleys[0], self.trolleys[1]
        self._place(t1, 0, 0)
        self._place(t2, 0, 0)
        loc1 = loc_of(self.db, self.project, t1)
        loc2 = loc_of(self.db, self.project, t2)
        self.assertEqual(loc1.area_code, "A11")
        self.assertEqual(loc2.area_code, "A11")
        self.assertFalse(loc1.unassigned)
        self.assertFalse(loc2.unassigned)

    def test_hex_agg_same_skips_and_diff_resets(self):
        p = self.project
        t0 = _now()
        hex_a = "BB02220011C3300E2806894000040E1ECCC8C53DDFA07E"
        hex_b = "BB02220011C3300E2806894000040E1ECCC8C99DDFA07E"
        self.assertTrue(should_write_raw(self.db, p, "R1", hex_a, t0))
        self.db.add(RawReport(project_id=p.id, device_id="R1", hex_data=hex_a, epcs=[], report_day="2026-11-15", ts=t0))
        self.db.flush()
        self.assertFalse(should_write_raw(self.db, p, "R1", hex_a, t0 + timedelta(minutes=4)))
        self.assertTrue(should_write_raw(self.db, p, "R1", hex_b, t0 + timedelta(minutes=1)))
        self.assertTrue(should_write_raw(self.db, p, "R2", hex_a, t0 + timedelta(minutes=1)))
        self.assertTrue(should_write_raw(self.db, p, "R1", hex_a, t0 + timedelta(minutes=6)))

    def test_apply_report_hex_agg_keeps_one_row(self):
        p = self.project
        hex_a = "BB02220011C3300E2806894000040E1ECCC8C53DDFA07E"
        t0 = _now()
        apply_report(self.db, p, "R1", hex_a, [], t0, None)
        apply_report(self.db, p, "R1", hex_a, [], t0 + timedelta(minutes=2), None)
        n = self.db.scalar(select(RawReport).where(RawReport.project_id == p.id).order_by(RawReport.id))
        count = len(self.db.scalars(select(RawReport).where(RawReport.project_id == p.id)).all())
        self.assertEqual(count, 1)
        self.assertIsNotNone(n)
        t1 = self.trolleys[0]
        self.db.refresh(t1)
        last = t1.last_report_time
        self.assertIsNotNone(last)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        self.assertEqual(last, t0 + timedelta(minutes=2))

    def test_calendar_month_cleanup(self):
        p = self.project
        old = _now() - timedelta(days=40)
        fresh = _now()
        self.db.add(RawReport(project_id=p.id, device_id="R1", hex_data="AA", epcs=[], report_day="2026-10-01", ts=old))
        self.db.add(RawReport(project_id=p.id, device_id="R1", hex_data="BB", epcs=[], report_day="2026-11-15", ts=fresh))
        self.db.add(
            PushRecord(
                project_id=p.id,
                trolley_tz="TZ1",
                seat_name="台座1",
                identify_time=old,
                payload={},
                status="success",
            )
        )
        self.db.add(
            PushRecord(
                project_id=p.id,
                trolley_tz="TZ1",
                seat_name="台座1",
                identify_time=fresh,
                payload={},
                status="success",
            )
        )
        self.db.flush()
        cleanup_retention(self.db, p)
        self.db.flush()
        raws = self.db.scalars(select(RawReport).where(RawReport.project_id == p.id)).all()
        pushes = self.db.scalars(select(PushRecord).where(PushRecord.project_id == p.id)).all()
        self.assertEqual([r.hex_data for r in raws], ["BB"])
        self.assertEqual(len(pushes), 1)
        when = pushes[0].identify_time
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        self.assertEqual(when, fresh)

    def test_add_calendar_months_jan_to_feb(self):
        dt = datetime(2026, 1, 31, 12, 0, tzinfo=timezone.utc)
        out = add_calendar_months(dt, 1)
        self.assertEqual(out.month, 2)
        self.assertEqual(out.day, 28)

    def test_batch_repush_ids_time_range(self):
        p = self.project
        t0 = _now()
        ids = []
        for i, when in enumerate([t0 - timedelta(hours=3), t0 - timedelta(hours=1), t0 + timedelta(hours=1)]):
            rec = PushRecord(
                project_id=p.id,
                trolley_tz="TZ1",
                seat_name="台座1",
                identify_time=when,
                payload={"i": i},
                status="success",
            )
            self.db.add(rec)
            self.db.flush()
            ids.append(rec.id)
        got = batch_repush_ids(self.db, p, t0 - timedelta(hours=2), t0)
        self.assertEqual(got, [ids[1]])

    def test_asset_details_never_and_abnormal(self):
        p = self.project
        t1 = self.trolleys[0]
        t1.last_report_time = _now() - timedelta(days=40)
        epc = self.db.scalars(select(Epc).where(Epc.project_id == p.id)).first()
        epc.last_read_time = None
        epc.last_hex = ""
        self.db.flush()
        data = asset_details(self.db, p)
        tz_row = next(r for r in data["trolleys"] if r["tz"] == "TZ1")
        self.assertTrue(tz_row["abnormal"])
        self.assertEqual(tz_row["status"], "可能存在异常")
        never = next(r for r in data["trolleys"] if r["tz"] == "TZ2")
        self.assertEqual(never["last"], "— / 从未上报")
        tag = next(r for r in data["tags"] if r["epc"] == (epc.epc_raw or epc.epc_norm))
        self.assertEqual(tag["last"], "— / 从未读取")
        self.assertTrue(tag["never"])


if __name__ == "__main__":
    unittest.main()
