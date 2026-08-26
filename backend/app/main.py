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
    from .engine.runtime import LOCK, tick_all

    while True:
        try:
            db = SessionLocal()
            try:
                with LOCK:
                    tick_all(db)
                    db.commit()
            except Exception:
                db.rollback()
            finally:
                db.close()
        except Exception:
            pass
        time.sleep(0.5)


@app.on_event("startup")
def on_startup():
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


@app.get("/api/v1/health")
def health():
    return {"ok": True, "name": settings.app_name}
