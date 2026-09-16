"""客户平台鉴权与报文：与 smart_beam_yard/push_service.py 对齐。"""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime
from typing import Any, Optional

import httpx

from ..models import Project
from ..schemas import DEFAULT_TPL

PLACEHOLDER = re.compile(r"\$\{([A-Za-z0-9_]+)\}")
MOVEMENT_THRESHOLD = 20 * 60
TOKEN_CACHE: dict[str, tuple[str, float]] = {}
TOKEN_LOCK = threading.RLock()
CONNECT_TIMEOUT = 4.0
READ_TIMEOUT = 8.0


def http_post(url: str, *, json: Any = None, headers: Optional[dict] = None, timeout: float = READ_TIMEOUT):
    """每次独立建连，避免后台线程与点击推送共用 Client 导致卡死。"""
    t = timeout if isinstance(timeout, httpx.Timeout) else httpx.Timeout(timeout, connect=CONNECT_TIMEOUT)
    with httpx.Client(timeout=t) as client:
        return client.post(url, json=json, headers=headers)


def _dlog(hid: str, loc: str, message: str, data: dict) -> None:
    # #region agent log
    from pathlib import Path

    try:
        p = Path(__file__).resolve().parents[3] / "debug-7c60c2.log"
        with p.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "sessionId": "7c60c2",
                        "runId": "pre-fix",
                        "hypothesisId": hid,
                        "location": loc,
                        "message": message,
                        "data": data,
                        "timestamp": int(time.time() * 1000),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except Exception:
        pass
    # #endregion


def _now() -> datetime:
    return datetime.now().astimezone()


def identify_time_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        dt = value.astimezone() if value.tzinfo else value
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    text = str(value).strip().replace("T", " ")
    if "." in text:
        text = text.split(".", 1)[0]
    if text.count(":") >= 3:
        text = text.rsplit(":", 1)[0]
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return text


def replace_identify_time(payload: Any, when: Any) -> dict:
    """复制上一条报文，只改识别时间。"""
    text = identify_time_text(when)
    try:
        out = json.loads(json.dumps(payload or {}))
    except (TypeError, ValueError):
        out = {}
    if not isinstance(out, dict):
        out = {}

    def walk(obj):
        if isinstance(obj, dict):
            if "identifyTime" in obj:
                obj["identifyTime"] = text
            for item in obj.values():
                walk(item)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(out)
    if "identifyTime" not in out:
        out["identifyTime"] = text
    return out


def sport_state_text(value: Any) -> str:
    if value is None or value == "":
        return ""
    text = str(value).strip()
    if text in {"moving", "运动", "1"}:
        return "运动"
    if text in {"still", "静止", "0"}:
        return "静止"
    return text


def replace_sport_state(payload: Any, state: Any) -> dict:
    """在已有报文上改写运动状态，不改其它字段。"""
    text = sport_state_text(state) or "静止"
    try:
        out = json.loads(json.dumps(payload or {}))
    except (TypeError, ValueError):
        out = {}
    if not isinstance(out, dict):
        out = {}

    def walk(obj):
        if isinstance(obj, dict):
            if "sportState" in obj:
                obj["sportState"] = text
            for item in obj.values():
                walk(item)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(out)
    return out


def movement_status(last_time: Any) -> str:
    """旧系统：识别时间在 20 分钟内为「运动」，否则「静止」。"""
    normalized = identify_time_text(last_time)
    if not normalized:
        return "静止"
    try:
        then = datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S")
        diff = (_now().replace(tzinfo=None) - then).total_seconds()
        return "运动" if 0 <= diff <= MOVEMENT_THRESHOLD else "静止"
    except ValueError:
        return "静止"


def render_template(value: Any, context: dict) -> Any:
    if isinstance(value, dict):
        return {key: render_template(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [render_template(item, context) for item in value]
    if not isinstance(value, str):
        return value
    exact = PLACEHOLDER.fullmatch(value)
    if exact:
        return context.get(exact.group(1), value)
    return PLACEHOLDER.sub(lambda match: str(context.get(match.group(1), match.group(0))), value)


def payload_context(project: Project, trolley, loc_like, identify_time: Any, sport_state: Any = None) -> dict:
    state = sport_state_text(sport_state) or movement_status(identify_time)
    return {
        "areaCode": getattr(loc_like, "area_code", "") or "",
        "beamAssetsCode": getattr(trolley, "tz", "") or "",
        "beamLineCode": getattr(loc_like, "line_code", "") or "",
        "identifyTime": identify_time_text(identify_time),
        "procedureCode": getattr(loc_like, "proc_code", "") or "",
        "sportState": state,
        "serverUrl": project.push_url or "",
        "projectId": project.name or "",
        "dataSource": 1,
        "dataIndex": 0,
        "isAutoImported": True,
    }


def build_customer_payload(project: Project, trolley, loc_like, identify_time: Any, template_override=None, sport_state: Any = None) -> dict:
    raw = template_override if template_override is not None else (project.tpl_json or DEFAULT_TPL)
    if isinstance(raw, str):
        try:
            template = json.loads(raw or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError(f"报文模板不是有效JSON: {exc}") from exc
    else:
        template = raw
    if not isinstance(template, dict):
        raise ValueError("报文模板必须是JSON对象")
    payload = render_template(template, payload_context(project, trolley, loc_like, identify_time, sport_state=sport_state))
    if isinstance(payload, dict):
        payload["isAutoImported"] = True
    return payload


def invalidate_token(pid: str) -> None:
    with TOKEN_LOCK:
        TOKEN_CACHE.pop(pid, None)


def get_customer_token(project: Project, force_refresh: bool = False) -> tuple[Optional[str], str]:
    pid = project.pid
    if not project.app_id or not project.app_secret or not project.token_url:
        return None, "Token配置不完整（需 AppID、AppSecret、Token URL）"
    now = time.time()
    with TOKEN_LOCK:
        cached = TOKEN_CACHE.get(pid)
        if cached and not force_refresh and cached[1] > now:
            return cached[0], ""
    attempts = min(2, max(1, int(project.retry or 3)))
    last_error = ""
    for attempt in range(attempts):
        try:
            response = http_post(
                project.token_url,
                json={"appId": project.app_id, "appSecret": project.app_secret},
                timeout=5.0,
            )
            try:
                data = response.json()
            except Exception:
                data = {"body": (response.text or "")[:500]}
            if response.is_success and data.get("code") == 200 and data.get("msg"):
                token = str(data["msg"])
                expires_in = int(data.get("expiresIn") or data.get("expires_in") or 3600)
                with TOKEN_LOCK:
                    TOKEN_CACHE[pid] = (token, now + max(60, expires_in - 300))
                return token, ""
            last_error = f"HTTP {response.status_code}: {data}"
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
        if attempt + 1 < attempts:
            time.sleep(min(2 ** attempt, 4))
    return None, last_error


def _reply_from_body(data: Any, http_status: Optional[int] = None) -> dict:
    body = data if isinstance(data, dict) else {"body": data}
    msg = body.get("msg")
    if msg in (None, ""):
        msg = body.get("message")
    if msg in (None, ""):
        msg = body.get("detail")
    if msg in (None, ""):
        extra = body.get("body")
        msg = extra if extra not in (None, "") else ""
    return {
        "code": body.get("code"),
        "msg": "" if msg is None else str(msg),
        "httpStatus": http_status,
    }


def post_customer(project: Project, payload: dict, force_refresh: bool = False, attempts: Optional[int] = None) -> tuple[bool, dict]:
    """POST 客户推送 URL。成功条件与旧系统一致：HTTP 2xx 且 body.code == 200。"""
    if not project.push_url:
        # #region agent log
        _dlog("A", "customer.py:post_customer", "skip-no-push-url", {"pid": getattr(project, "pid", "")})
        # #endregion
        return False, {"code": None, "msg": "未配置客户推送 URL"}
    attempts = min(2, max(1, int(project.retry or 3) if attempts is None else attempts))
    last: dict = {"code": None, "msg": ""}
    need_refresh = force_refresh
    for attempt in range(attempts):
        token, token_error = get_customer_token(project, force_refresh=need_refresh or attempt > 0 and "401" in str(last.get("msg") or ""))
        if not token:
            last = {"code": None, "msg": token_error or "Token获取失败"}
            # #region agent log
            _dlog("A", "customer.py:post_customer", "token-fail", {"pid": getattr(project, "pid", ""), "err": (token_error or "")[:200]})
            # #endregion
            break
        body = dict(payload or {})
        body["isAutoImported"] = True
        # #region agent log
        _dlog(
            "B,C",
            "customer.py:post_customer",
            "before-http",
            {
                "pid": getattr(project, "pid", ""),
                "pushHost": (project.push_url or "").split("?")[0][:180],
                "isAutoImported": body.get("isAutoImported"),
                "isAutoType": type(body.get("isAutoImported")).__name__,
                "dataSource": body.get("dataSource"),
                "dataIndex": body.get("dataIndex"),
                "keys": list(body.keys())[:20],
            },
        )
        # #endregion
        try:
            response = http_post(
                project.push_url,
                json=body,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                timeout=READ_TIMEOUT,
            )
            try:
                result = response.json()
            except Exception:
                result = {"body": (response.text or "")[:500]}
            last = _reply_from_body(result, response.status_code)
            if not last["msg"]:
                last["msg"] = f"HTTP {response.status_code}"
            # #region agent log
            _dlog(
                "C,D",
                "customer.py:post_customer",
                "after-http",
                {
                    "httpStatus": response.status_code,
                    "ok2xx": bool(response.is_success),
                    "code": result.get("code") if isinstance(result, dict) else None,
                    "msg": (last.get("msg") or "")[:200],
                    "isAutoImportedSent": body.get("isAutoImported"),
                    "dataSourceSent": body.get("dataSource"),
                },
            )
            # #endregion
            if response.is_success and (result.get("code") == 200 if isinstance(result, dict) else False):
                return True, last
            # #region agent log
            _dlog(
                "E,G",
                "customer.py:post_customer",
                "customer-reject",
                {
                    "procedureCode": body.get("procedureCode"),
                    "areaCode": body.get("areaCode"),
                    "beamAssetsCode": body.get("beamAssetsCode"),
                    "code": last.get("code"),
                    "msg": (last.get("msg") or "")[:200],
                },
            )
            # #endregion
            if response.status_code == 401 or (isinstance(result, dict) and result.get("code") == 401):
                invalidate_token(project.pid)
                need_refresh = True
        except Exception as exc:  # noqa: BLE001
            last = {"code": None, "msg": str(exc)}
        if attempt + 1 < attempts:
            time.sleep(min(2 ** attempt, 4))
    return False, last
