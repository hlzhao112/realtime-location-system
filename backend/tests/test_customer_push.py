import json
import time
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.engine import customer
from app.engine.customer import (
    TOKEN_CACHE,
    build_customer_payload,
    get_customer_token,
    identify_time_text,
    movement_status,
    post_customer,
    render_template,
)


def _project(**kwargs):
    defaults = dict(
        pid="CSCJ",
        name="HL项目",
        app_id="app",
        app_secret="secret",
        token_url="http://token.example/token",
        push_url="http://push.example/data",
        retry=3,
        tpl_json='{"areaCode":"${areaCode}","beamAssetsCode":"${beamAssetsCode}","beamLineCode":"${beamLineCode}","identifyTime":"${identifyTime}","procedureCode":"${procedureCode}","sportState":"${sportState}"}',
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class CustomerPushTests(unittest.TestCase):
    def setUp(self):
        TOKEN_CACHE.clear()

    def test_replace_identify_time_only(self):
        payload = {
            "areaCode": "QY_178",
            "beamAssetsCode": "TZ_254",
            "identifyTime": "2026-07-13 00:00:01",
            "nested": {"identifyTime": "2026-07-13 00:00:01", "keep": 1},
        }
        out = customer.replace_identify_time(payload, "2026-09-06 15:00:00")
        self.assertEqual(out["identifyTime"], "2026-09-06 15:00:00")
        self.assertEqual(out["nested"]["identifyTime"], "2026-09-06 15:00:00")
        self.assertEqual(out["beamAssetsCode"], "TZ_254")
        self.assertEqual(payload["identifyTime"], "2026-07-13 00:00:01")
        self.assertEqual(identify_time_text("2026-07-13 00:00:01:798"), "2026-07-13 00:00:01")

    def test_explicit_sport_state_is_used_in_payload(self):
        trolley = SimpleNamespace(tz="TZ_254", reader="HN-2")
        loc = SimpleNamespace(area_code="QY_178", line_code="ZCX_133", proc_code="GX_0113")
        payload = build_customer_payload(_project(), trolley, loc, datetime.now(), sport_state="静止")
        self.assertEqual(payload["sportState"], "静止")
        copied = customer.replace_sport_state(payload, "运动")
        self.assertEqual(copied["sportState"], "运动")
        self.assertEqual(payload["sportState"], "静止")

    def test_movement_status_uses_20_minute_window(self):
        fresh = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        stale = (datetime.now() - timedelta(minutes=21)).strftime("%Y-%m-%d %H:%M:%S")
        self.assertEqual(movement_status(fresh), "运动")
        self.assertEqual(movement_status(stale), "静止")

    def test_template_render_matches_legacy(self):
        template = {"outer": ["${beamAssetsCode}", "prefix-${areaCode}"], "fixed": 1, "sportState": "${sportState}"}
        trolley = SimpleNamespace(tz="TZ_254", reader="HN-2")
        loc = SimpleNamespace(area_code="QY_178", line_code="ZCX_133", proc_code="GX_0113")
        payload = build_customer_payload(
            _project(tpl_json=json.dumps(template)),
            trolley,
            loc,
            "2026-07-13 00:00:01",
        )
        self.assertEqual(payload["outer"], ["TZ_254", "prefix-QY_178"])
        self.assertEqual(payload["fixed"], 1)
        self.assertIn(payload["sportState"], ("运动", "静止"))

    def test_default_legacy_payload(self):
        trolley = SimpleNamespace(tz="TZ_254", reader="HN-2")
        loc = SimpleNamespace(area_code="QY_178", line_code="ZCX_133", proc_code="GX_0113")
        when = datetime.now()
        payload = build_customer_payload(_project(tpl_json=""), trolley, loc, when)
        self.assertEqual(payload["beamAssetsCode"], "TZ_254")
        self.assertEqual(payload["areaCode"], "QY_178")
        self.assertEqual(payload["sportState"], "运动")
        self.assertEqual(payload["dataSource"], 1)
        self.assertEqual(payload["dataIndex"], 0)
        self.assertEqual(payload["isAutoImported"], True)
        self.assertEqual(payload["serverUrl"], "http://push.example/data")
        self.assertEqual(payload["projectId"], "HL项目")
        self.assertNotIn("deviceCode", payload)
        self.assertNotIn("projectCode", payload)
        self.assertIn("procedureCode", payload)

    def test_hardcoded_false_is_forced_true(self):
        trolley = SimpleNamespace(tz="TZ_254", reader="HN-2")
        loc = SimpleNamespace(area_code="QY_178", line_code="ZCX_133", proc_code="GX_0113")
        payload = build_customer_payload(
            _project(tpl_json='{"isAutoImported": false, "beamAssetsCode": "${beamAssetsCode}"}'),
            trolley,
            loc,
            datetime.now(),
        )
        self.assertEqual(payload["beamAssetsCode"], "TZ_254")
        self.assertTrue(payload["isAutoImported"])

    def test_token_is_cached(self):
        response = Mock()
        response.is_success = True
        response.json.return_value = {"code": 200, "msg": "token-value", "expiresIn": 3600}
        with patch.object(customer, "http_post", return_value=response) as post:
            first, err1 = get_customer_token(_project())
            second, err2 = get_customer_token(_project())
        self.assertEqual(err1, "")
        self.assertEqual(err2, "")
        self.assertEqual(first, second)
        self.assertEqual(post.call_count, 1)
        post.assert_called_with(
            "http://token.example/token",
            json={"appId": "app", "appSecret": "secret"},
            timeout=5.0,
        )

    def test_push_sends_bearer_and_requires_code_200(self):
        TOKEN_CACHE["CSCJ"] = ("token-value", time.time() + 3600)
        push_resp = Mock()
        push_resp.is_success = True
        push_resp.status_code = 200
        push_resp.json.return_value = {"code": 200, "msg": "ok"}
        with patch.object(customer, "http_post", return_value=push_resp) as post:
            ok, result = post_customer(_project(), {"beamAssetsCode": "TZ_254"})
        self.assertTrue(ok)
        self.assertEqual(result["code"], 200)
        self.assertEqual(result["msg"], "ok")
        headers = post.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer token-value")
        self.assertEqual(post.call_args.kwargs["json"]["beamAssetsCode"], "TZ_254")
        self.assertTrue(post.call_args.kwargs["json"]["isAutoImported"])

    def test_push_returns_customer_error_body(self):
        TOKEN_CACHE["CSCJ"] = ("token-value", time.time() + 3600)
        push_resp = Mock()
        push_resp.is_success = True
        push_resp.status_code = 200
        push_resp.json.return_value = {"code": 500, "msg": "梁号不存在"}
        with patch.object(customer, "http_post", return_value=push_resp):
            ok, result = post_customer(_project(retry=1), {"beamAssetsCode": "TZ_254"})
        self.assertFalse(ok)
        self.assertEqual(result["code"], 500)
        self.assertEqual(result["msg"], "梁号不存在")


def _record(**kwargs):
    defaults = dict(
        test=True,
        source="auto",
        trolley_tz="TZ_254",
        proc_name="蒸汽养护",
        area_code="QY_178",
        kind="实时上报",
        payload={"beamAssetsCode": "TZ_254"},
        status="pending",
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class DeliverPushTests(unittest.TestCase):
    def test_test_record_is_sent_when_auto_push_enabled(self):
        from app.engine import runtime

        rec = _record(test=True, source="auto")
        project = _project(id=1, push_enabled=True)
        with patch.object(runtime, "post_customer", return_value=(True, {"code": 200, "msg": "ok"})) as post:
            with patch.object(runtime, "write_log"):
                ok, result = runtime.deliver(Mock(), project, rec, force=False)
        self.assertTrue(ok)
        self.assertEqual(rec.status, "success")
        self.assertEqual(result["msg"], "ok")
        post.assert_called_once()

    def test_forced_push_sends_test_record(self):
        from app.engine import runtime

        rec = _record(test=True, source="manual", kind="手动推送")
        project = _project(id=1, push_enabled=False)
        with patch.object(runtime, "post_customer", return_value=(True, {"code": 200, "msg": "ok"})) as post:
            with patch.object(runtime, "write_log"):
                ok, result = runtime.deliver(Mock(), project, rec, force=True)
        self.assertTrue(ok)
        self.assertEqual(result["msg"], "ok")
        post.assert_called_once()

    def test_test_record_stays_local_when_push_stopped(self):
        from app.engine import runtime

        rec = _record(test=True, source="auto")
        project = _project(id=1, push_enabled=False)
        with patch.object(runtime, "post_customer") as post:
            with patch.object(runtime, "write_log"):
                ok, result = runtime.deliver(Mock(), project, rec, force=False)
        self.assertFalse(ok)
        self.assertEqual(rec.status, "pending")
        self.assertIn("自动推送已停止", result["msg"])
        post.assert_not_called()

    def test_prod_auto_stays_pending_when_push_stopped(self):
        from app.engine import runtime

        rec = _record(test=False, source="auto")
        project = _project(id=1, push_enabled=False)
        with patch.object(runtime, "post_customer") as post:
            with patch.object(runtime, "write_log"):
                ok, result = runtime.deliver(Mock(), project, rec, force=False)
        self.assertFalse(ok)
        self.assertEqual(rec.status, "pending")
        self.assertIn("自动推送已停止", result["msg"])
        post.assert_not_called()

    def test_keep_sec_is_10_minutes_when_test_on(self):
        from app.engine import runtime

        fast = SimpleNamespace(test_on=True, test_fast=True, test_adv_mode="fast", keep_sec=3)
        real = SimpleNamespace(test_on=True, test_fast=False, test_adv_mode="real", keep_sec=60)
        hour = SimpleNamespace(test_on=True, test_fast=False, test_adv_mode="hour", keep_sec=60)
        prod = SimpleNamespace(test_on=False, test_fast=False, test_adv_mode="fast", keep_sec=600)
        self.assertEqual(runtime.keep_sec(fast), 600)
        self.assertEqual(runtime.keep_sec(real), 600)
        self.assertEqual(runtime.keep_sec(hour), 600)
        self.assertEqual(runtime.keep_sec(prod), 600)
        self.assertEqual(runtime.keep_label(fast), "10 分钟")
        self.assertEqual(runtime.adv_sec(fast), 6)
        self.assertEqual(runtime.adv_sec(real), 120)
        self.assertEqual(runtime.adv_sec(hour), 3600)
        self.assertEqual(runtime.adv_label(hour), "1 小时")
        self.assertEqual(runtime.normalize_test_adv_mode("hour"), "hour")
        self.assertEqual(runtime.normalize_test_adv_mode(None, True), "fast")
        self.assertEqual(runtime.normalize_test_adv_mode(None, False), "real")

    def test_deferred_deliver_queues_without_http(self):
        from app.engine import runtime

        rec = _record(id=88, test=True, source="auto")
        project = _project(id=1, push_enabled=True)
        runtime.PENDING_OUTBOUND.clear()
        with runtime.defer_outbound():
            with patch.object(runtime, "post_customer") as post:
                with patch.object(runtime, "write_log"):
                    ok, result = runtime.deliver(Mock(), project, rec, force=False)
        self.assertFalse(ok)
        self.assertEqual(rec.status, "pending")
        self.assertEqual(runtime.take_outbound(), [88])
        self.assertIn("排队", result["msg"])
        post.assert_not_called()


class MergeRowsTests(unittest.TestCase):
    def test_merge_only_consecutive_same_position(self):
        from app.engine import runtime

        def row(rid, tz, line, proc, area):
            return SimpleNamespace(
                id=rid,
                trolley_tz=tz,
                line_code=line,
                proc_code=proc,
                area_code=area,
            )

        # 时间倒序：同位置连续三段应压成一条；中间换位置后再回到原位置应保留
        rows = [
            row(5, "TZ_1", "L1", "P1", "A1"),
            row(4, "TZ_1", "L1", "P1", "A1"),
            row(3, "TZ_1", "L1", "P1", "A1"),
            row(2, "TZ_1", "L1", "P2", "A2"),
            row(1, "TZ_1", "L1", "P1", "A1"),
        ]
        merged = runtime.merge_rows(rows)
        self.assertEqual([r.id for r in merged], [5, 2, 1])


class TestProcRotation(unittest.TestCase):
    def test_next_proc_uses_name_when_code_repeats(self):
        from app.engine import runtime

        pour = SimpleNamespace(code="GX_0", name="浇筑", areas=[])
        steam = SimpleNamespace(code="GX_1", name="蒸汽养护", areas=[])
        natural = SimpleNamespace(code="GX_1", name="自然养护", areas=[])
        pull = SimpleNamespace(code="GX_2", name="张拉", areas=[])
        line = SimpleNamespace(procedures=[pour, steam, natural, pull])
        self.assertEqual(runtime.next_proc(line, "GX_1", "蒸汽养护").name, "自然养护")
        self.assertEqual(runtime.next_proc(line, "GX_1", "自然养护").name, "张拉")
        self.assertEqual(runtime.next_proc(line, "GX_2", "张拉").name, "浇筑")

    def test_unassigned_stays_on_target_proc_even_if_later_has_free(self):
        """未分配不得跳到后续空闲工序，必须按序进入目标工序（可挤占）。"""
        from app.engine import runtime

        pour = SimpleNamespace(code="GX_A", name="浇筑", areas=[SimpleNamespace(code="A1")])
        steam = SimpleNamespace(
            code="GX_B",
            name="蒸汽养护",
            areas=[SimpleNamespace(code="B1"), SimpleNamespace(code="B2"), SimpleNamespace(code="B3")],
        )
        line = SimpleNamespace(procedures=[pour, steam])
        victim = (object(), object())

        def fake_pick(_db, _project, _line, proc, _except_tz):
            if proc.name == "浇筑":
                return proc.areas[0], victim
            return proc.areas[0], None

        with patch.object(runtime, "pick_area", side_effect=fake_pick):
            proc, area, evict = runtime.pick_free_on_line(Mock(), SimpleNamespace(), line, "TZ_1", start_proc=pour)
        self.assertEqual(proc.name, "浇筑")
        self.assertEqual(area.code, "A1")
        self.assertIs(evict, victim)

    def test_unassigned_last_proc_does_not_skip_to_earlier_or_later(self):
        """目标为末工序时，即使前序有空位也必须进入末工序。"""
        from app.engine import runtime

        bind = SimpleNamespace(code="GX_1", name="绑扎", areas=[SimpleNamespace(code="A1")])
        pour = SimpleNamespace(
            code="GX_2",
            name="浇筑",
            areas=[SimpleNamespace(code="B1"), SimpleNamespace(code="B2"), SimpleNamespace(code="B3")],
        )
        pull = SimpleNamespace(code="GX_3", name="张拉", areas=[SimpleNamespace(code="C1")])
        grout = SimpleNamespace(code="GX_4", name="压浆", areas=[SimpleNamespace(code="D1")])
        line = SimpleNamespace(procedures=[bind, pour, pull, grout])
        victim = (object(), object())

        def fake_pick(_db, _project, _line, proc, _except_tz):
            if proc.name == "压浆":
                return proc.areas[0], victim
            return proc.areas[0], None

        with patch.object(runtime, "pick_area", side_effect=fake_pick):
            proc, area, evict = runtime.pick_free_on_line(Mock(), SimpleNamespace(), line, "TZ_1", start_proc=grout)
        self.assertEqual(proc.name, "压浆")
        self.assertEqual(area.code, "D1")
        self.assertIs(evict, victim)


if __name__ == "__main__":
    unittest.main()
