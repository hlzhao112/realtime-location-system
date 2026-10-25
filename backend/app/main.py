from __future__ import annotations

import threading
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import auth, ingest, mappings, projects, runtime
from .config import get_settings
from .db import Base, SessionLocal, engine
from .migrate import migrate
from .seed import seed_admin

app = FastAPI(title="预制梁台车追踪系统", version="1.0.0")
settings = get_settings()


@app.middleware("http")
async def _debug_http(request, call_next):
    # #region agent log
    try:
        import json as _json
        import time as _time
        from pathlib import Path as _Path

        _path = str(request.url.path)
        if request.method != "POST" or "/pushes/" not in _path or not _path.endswith("/push"):
            return await call_next(request)
        _p = _Path(__file__).resolve().parents[2] / "debug-7c60c2.log"
        with _p.open("a", encoding="utf-8") as _f:
            _f.write(
                _json.dumps(
                    {
                        "sessionId": "7c60c2",
                        "runId": "post-fix",
                        "hypothesisId": "E",
                        "location": "main.py:middleware",
                        "message": "http-in",
                        "data": {"method": request.method, "path": _path},
                        "timestamp": int(_time.time() * 1000),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except Exception:
        pass
    # #endregion
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list + ["http://localhost:4173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(mappings.router)
app.include_router(ingest.router)
app.include_router(runtime.router)


def _worker_loop():
    from .api.ingest import process_queued
    from .services.queue import consume_one, redis_client

    if not redis_client():
        return
    while True:
        try:
            if not consume_one(process_queued):
                time.sleep(0.2)
        except Exception:
            time.sleep(1)


def _runtime_loop():
    from .engine.runtime import LOCK, enqueue_outbound, take_outbound, tick_all

    while True:
        outbound: list[int] = []
        try:
            db = SessionLocal()
            try:
                with LOCK:
                    dirty = tick_all(db)
                    outbound = take_outbound()
                    if dirty:
                        db.commit()
                    else:
                        db.rollback()
            except Exception:
                db.rollback()
            finally:
                db.close()
            enqueue_outbound(outbound)
        except Exception:
            pass
        time.sleep(0.5)


def _outbound_loop():
    from .engine.runtime import outbound_worker_loop

    outbound_worker_loop()


@app.on_event("startup")
def on_startup():
    from .engine.mqtt_client import start_mqtt

    Base.metadata.create_all(bind=engine)
    migrate(engine)
    db = SessionLocal()
    try:
        seed_admin(db)
        db.commit()
    finally:
        db.close()
    t = threading.Thread(target=_worker_loop, name="ingest-worker", daemon=True)
    t.start()
    r = threading.Thread(target=_runtime_loop, name="runtime-ticker", daemon=True)
    r.start()
    p = threading.Thread(target=_outbound_loop, name="push-worker", daemon=True)
    p.start()
    start_mqtt()


@app.on_event("shutdown")
def on_shutdown():
    from .engine.mqtt_client import stop_mqtt

    stop_mqtt()


@app.get("/api/v1/health")
def health():
    from .engine.mqtt_client import mqtt_status

    return {"ok": True, "name": settings.app_name, "mqtt": mqtt_status()}
