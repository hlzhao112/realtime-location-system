"""重连后的积压回放过滤。

电脑休眠唤醒或原始数据服务重启后，MQTT 会把未确认的历史报文成批补下。
这些报文时间戳相同。窗口内一旦同一时间戳达到大批量，整批丢弃；
窗口结束后恢复实时接收。MQTT 保留消息不进入这套判断。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from ..services.queue import parse_ts

_GUARD = None
_GUARD_LOCK = threading.Lock()
_WATCHER: threading.Thread | None = None


def timestamp_key(ts, arrived: datetime) -> str:
    """有报文时间戳时按它分组；没有则按到达时间的整秒分组。"""
    if ts not in (None, ""):
        parsed = parse_ts(ts)
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return "p:" + parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        return "p:" + str(ts).strip()
    if arrived.tzinfo is None:
        arrived = arrived.replace(tzinfo=timezone.utc)
    return "a:" + arrived.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


class ReplayGuard:
    def __init__(
        self,
        burst: int = 30,
        hold_sec: float = 1.0,
        min_sec: float = 5.0,
        quiet_sec: float = 2.0,
        max_sec: float = 120.0,
    ):
        self.burst = max(2, int(burst))
        self.hold_sec = max(0.2, float(hold_sec))
        self.min_sec = max(0.0, float(min_sec))
        self.quiet_sec = max(0.2, float(quiet_sec))
        self.max_sec = max(self.min_sec, float(max_sec))
        self._lock = threading.Lock()
        self._armed_at: float | None = None
        self._last_drop_at: float | None = None
        self._counts: dict[str, int] = {}
        self._held: dict[str, list[tuple[str, bytes | str | None]]] = {}
        self._last_seen: dict[str, float] = {}
        self._dropped: set[str] = set()
        self._dropped_total = 0
        self._notice: str | None = None

    def arm(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            self._armed_at = now
            self._last_drop_at = None
            self._counts.clear()
            self._held.clear()
            self._last_seen.clear()
            self._dropped.clear()
            self._notice = None

    def active(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            return self._window_open(now)

    def stats(self) -> dict:
        with self._lock:
            return {
                "replay_active": self._armed_at is not None and self._window_open(time.monotonic()),
                "replay_dropped": self._dropped_total,
            }

    def offer(
        self,
        ts,
        topic: str,
        payload: bytes | str | None,
        *,
        retain: bool = False,
        now: float | None = None,
        arrived: datetime | None = None,
    ) -> tuple[str, int]:
        """返回 (accept|hold|drop, 本次新丢弃条数)。保留消息永远 accept。"""
        if retain:
            return "accept", 0
        now = time.monotonic() if now is None else now
        arrived = arrived or datetime.now(timezone.utc)
        with self._lock:
            if not self._window_open(now):
                self._armed_at = None
                return "accept", 0
            key = timestamp_key(ts, arrived)
            if key in self._dropped:
                self._last_drop_at = now
                self._dropped_total += 1
                self._mark_notice()
                return "drop", 1
            self._counts[key] = self._counts.get(key, 0) + 1
            self._held.setdefault(key, []).append((topic, payload))
            self._last_seen[key] = now
            if self._counts[key] >= self.burst:
                n = len(self._held.pop(key, []))
                self._last_seen.pop(key, None)
                self._dropped.add(key)
                self._last_drop_at = now
                self._dropped_total += n
                self._mark_notice()
                return "drop", n
            return "hold", 0

    def poll(self, now: float | None = None) -> tuple[list[tuple[str, bytes | str | None]], str | None]:
        """放出已确认不是积压的报文，并在窗口结束时清空暂存。"""
        now = time.monotonic() if now is None else now
        with self._lock:
            released: list[tuple[str, bytes | str | None]] = []
            for key, seen in list(self._last_seen.items()):
                if key in self._dropped or self._counts.get(key, 0) >= self.burst:
                    continue
                if now - seen >= self.hold_sec:
                    released.extend(self._held.pop(key, []))
                    self._last_seen.pop(key, None)
            notice = None
            if self._armed_at is not None and not self._window_open(now):
                for key, items in list(self._held.items()):
                    if key in self._dropped or self._counts.get(key, 0) >= self.burst:
                        self._dropped.add(key)
                        self._dropped_total += len(items)
                        self._mark_notice()
                    else:
                        released.extend(items)
                self._held.clear()
                self._last_seen.clear()
                self._armed_at = None
                notice = self._notice
                self._notice = None
            return released, notice

    def _window_open(self, now: float) -> bool:
        if self._armed_at is None:
            return False
        if now - self._armed_at >= self.max_sec:
            return False
        if now - self._armed_at < self.min_sec:
            return True
        if self._last_drop_at is not None and now - self._last_drop_at < self.quiet_sec:
            return True
        return False

    def _mark_notice(self) -> None:
        self._notice = f"MQTT 重连后丢弃积压报文 {self._dropped_total} 条（时间戳一致的批量回放），保留消息与实时数据继续接收"


def replay_guard() -> ReplayGuard:
    global _GUARD
    with _GUARD_LOCK:
        if _GUARD is None:
            from ..config import get_settings

            settings = get_settings()
            _GUARD = ReplayGuard(
                burst=settings.mqtt_replay_burst,
                hold_sec=settings.mqtt_replay_hold_sec,
                min_sec=settings.mqtt_replay_min_sec,
                quiet_sec=settings.mqtt_replay_quiet_sec,
                max_sec=settings.mqtt_replay_max_sec,
            )
        return _GUARD


def arm_replay_guard() -> None:
    _ensure_watcher()
    replay_guard().arm()


def _ensure_watcher() -> None:
    global _WATCHER
    with _GUARD_LOCK:
        if _WATCHER is not None and _WATCHER.is_alive():
            return
        _WATCHER = threading.Thread(target=_watch, name="mqtt-replay", daemon=True)
        _WATCHER.start()


def _watch() -> None:
    while True:
        time.sleep(0.2)
        try:
            released, notice = replay_guard().poll()
        except Exception:
            continue
        for topic, payload in released:
            try:
                from .mqtt_ingest import ingest_mqtt_message

                ingest_mqtt_message(topic, payload, replay=False)
            except Exception as exc:
                from .mqtt_client import note_result

                note_result(error=str(exc))
        if notice:
            _write_notice(notice)


def _write_notice(message: str) -> None:
    try:
        from ..db import SessionLocal
        from ..services.project_svc import write_log

        db = SessionLocal()
        try:
            write_log(db, None, "信息", message)
            db.commit()
        finally:
            db.close()
    except Exception:
        pass
