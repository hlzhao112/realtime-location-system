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
        self.assertEqual(payload["isAutoImported"], False)
        self.assertEqual(payload["serverUrl"], "http://push.example/data")
        self.assertEqual(payload["projectId"], "HL项目")
        self.assertNotIn("deviceCode", payload)
        self.assertNotIn("projectCode", payload)
        self.assertIn("procedureCode", payload)

    def test_token_is_cached(self):
        response = Mock()
        response.is_success = True
        response.json.return_value = {"code": 200, "msg": "token-value", "expiresIn": 3600}
        with patch.object(customer.HTTP, "post", return_value=response) as post:
            first, err1 = get_customer_token(_project())
            second, err2 = get_customer_token(_project())
        self.assertEqual(err1, "")
        self.assertEqual(err2, "")
        self.assertEqual(first, second)
        self.assertEqual(post.call_count, 1)
        post.assert_called_with(
            "http://token.example/token",
            json={"appId": "app", "appSecret": "secret"},
            timeout=10.0,
        )

    def test_push_sends_bearer_and_requires_code_200(self):
        TOKEN_CACHE["CSCJ"] = ("token-value", time.time() + 3600)
        push_resp = Mock()
        push_resp.is_success = True
        push_resp.status_code = 200
        push_resp.json.return_value = {"code": 200, "msg": "ok"}
        with patch.object(customer.HTTP, "post", return_value=push_resp) as post:
            ok, result = post_customer(_project(), {"beamAssetsCode": "TZ_254"})
        self.assertTrue(ok)
        self.assertEqual(result["code"], 200)
        self.assertEqual(result["msg"], "ok")
        headers = post.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer token-value")
        self.assertEqual(post.call_args.kwargs["json"]["beamAssetsCode"], "TZ_254")

    def test_push_returns_customer_error_body(self):
        TOKEN_CACHE["CSCJ"] = ("token-value", time.time() + 3600)
        push_resp = Mock()
        push_resp.is_success = True
        push_resp.status_code = 200
        push_resp.json.return_value = {"code": 500, "msg": "梁号不存在"}
        with patch.object(customer.HTTP, "post", return_value=push_resp):
            ok, result = post_customer(_project(retry=1), {"beamAssetsCode": "TZ_254"})
        self.assertFalse(ok)
        self.assertEqual(result["code"], 500)
        self.assertEqual(result["msg"], "梁号不存在")


if __name__ == "__main__":
    unittest.main()
