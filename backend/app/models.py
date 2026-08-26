from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    folder: Mapped[str] = mapped_column(String(512), default="")
    backup: Mapped[str] = mapped_column(String(512), default="")
    scan: Mapped[int] = mapped_column(Integer, default=5)
    stable: Mapped[int] = mapped_column(Integer, default=2)
    offline: Mapped[float] = mapped_column(Float, default=0.5)
    batch: Mapped[int] = mapped_column(Integer, default=20)
    resend_max: Mapped[int] = mapped_column(Integer, default=10)
    retry: Mapped[int] = mapped_column(Integer, default=3)
    log_clean: Mapped[int] = mapped_column(Integer, default=30)
    app_id: Mapped[str] = mapped_column(String(128), default="")
    app_secret: Mapped[str] = mapped_column(String(256), default="")
    token_url: Mapped[str] = mapped_column(String(512), default="")
    push_url: Mapped[str] = mapped_column(String(512), default="")
    ingest_token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    tpl_fields: Mapped[list] = mapped_column(JSON, default=list)
    tpl_json: Mapped[str] = mapped_column(Text, default="")
    tpl_custom: Mapped[bool] = mapped_column(Boolean, default=False)
    push_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    monitor_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    test_on: Mapped[bool] = mapped_column(Boolean, default=False)
    test_fast: Mapped[bool] = mapped_column(Boolean, default=True)
    test_tick: Mapped[int] = mapped_column(Integer, default=0)
    test_made: Mapped[int] = mapped_column(Integer, default=0)
    test_next_adv: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    test_next_keep: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    keep_next: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    lines: Mapped[List["Line"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="Line.sort_order"
    )
    trolleys: Mapped[List["Trolley"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="Trolley.id"
    )
    epcs: Mapped[List["Epc"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    locations: Mapped[List["LatestLocation"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class Line(Base):
    __tablename__ = "lines"
    __table_args__ = (UniqueConstraint("project_id", "code", name="uq_line_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    project: Mapped[Project] = relationship(back_populates="lines")
    procedures: Mapped[List["Procedure"]] = relationship(
        back_populates="line", cascade="all, delete-orphan", order_by="Procedure.sort_order"
    )


class Procedure(Base):
    __tablename__ = "procedures"
    __table_args__ = (UniqueConstraint("line_id", "code", name="uq_proc_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    line_id: Mapped[int] = mapped_column(ForeignKey("lines.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    line: Mapped[Line] = relationship(back_populates="procedures")
    areas: Mapped[List["Area"]] = relationship(
        back_populates="procedure", cascade="all, delete-orphan", order_by="Area.sort_order"
    )


class Area(Base):
    __tablename__ = "areas"
    __table_args__ = (UniqueConstraint("procedure_id", "code", name="uq_area_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    procedure_id: Mapped[int] = mapped_column(ForeignKey("procedures.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    procedure: Mapped[Procedure] = relationship(back_populates="areas")
    tag_nos: Mapped[List["TagNo"]] = relationship(
        back_populates="area", cascade="all, delete-orphan", order_by="TagNo.sort_order"
    )


class TagNo(Base):
    __tablename__ = "tag_nos"
    __table_args__ = (UniqueConstraint("area_id", "no", name="uq_area_tag"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    area_id: Mapped[int] = mapped_column(ForeignKey("areas.id", ondelete="CASCADE"), index=True)
    no: Mapped[str] = mapped_column(String(64), index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    area: Mapped[Area] = relationship(back_populates="tag_nos")
    epcs: Mapped[List["Epc"]] = relationship(
        back_populates="tag_no_row", cascade="all, delete-orphan"
    )


class Epc(Base):
    __tablename__ = "epcs"
    __table_args__ = (UniqueConstraint("project_id", "epc_norm", name="uq_epc_norm"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    tag_no_id: Mapped[int] = mapped_column(ForeignKey("tag_nos.id", ondelete="CASCADE"), index=True)
    epc_raw: Mapped[str] = mapped_column(String(128))
    epc_norm: Mapped[str] = mapped_column(String(128), index=True)

    project: Mapped[Project] = relationship(back_populates="epcs")
    tag_no_row: Mapped[TagNo] = relationship(back_populates="epcs")


class Trolley(Base):
    __tablename__ = "trolleys"
    __table_args__ = (
        UniqueConstraint("project_id", "tz", name="uq_trolley_tz"),
        UniqueConstraint("project_id", "reader", name="uq_trolley_reader"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    tz: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    reader: Mapped[str] = mapped_column(String(64), index=True)

    project: Mapped[Project] = relationship(back_populates="trolleys")
    location: Mapped[Optional["LatestLocation"]] = relationship(
        back_populates="trolley", uselist=False, cascade="all, delete-orphan"
    )


class LatestLocation(Base):
    """台车最新位置。主看板后续直接读这张表。"""

    __tablename__ = "latest_locations"
    __table_args__ = (UniqueConstraint("project_id", "trolley_id", name="uq_latest_trolley"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    trolley_id: Mapped[int] = mapped_column(ForeignKey("trolleys.id", ondelete="CASCADE"), index=True)
    line_code: Mapped[str] = mapped_column(String(64), default="")
    line_name: Mapped[str] = mapped_column(String(128), default="")
    proc_code: Mapped[str] = mapped_column(String(64), default="")
    proc_name: Mapped[str] = mapped_column(String(128), default="")
    area_code: Mapped[str] = mapped_column(String(64), default="")
    area_name: Mapped[str] = mapped_column(String(128), default="")
    tag_no: Mapped[str] = mapped_column(String(64), default="")
    epc: Mapped[str] = mapped_column(String(128), default="")
    sport_state: Mapped[str] = mapped_column(String(16), default="still")
    source: Mapped[str] = mapped_column(String(16), default="auto")
    kind: Mapped[str] = mapped_column(String(32), default="实时上报")
    unassigned: Mapped[bool] = mapped_column(Boolean, default=False)
    reissue: Mapped[int] = mapped_column(Integer, default=0)
    test: Mapped[bool] = mapped_column(Boolean, default=False)
    skip_tick: Mapped[int] = mapped_column(Integer, default=-1)
    retry_proc: Mapped[str] = mapped_column(String(64), default="")
    identify_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    project: Mapped[Project] = relationship(back_populates="locations")
    trolley: Mapped[Trolley] = relationship(back_populates="location")


class RawReport(Base):
    """预留：原始 HEX。本版写入但不提供管理页。"""

    __tablename__ = "raw_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    device_id: Mapped[str] = mapped_column(String(64), index=True)
    hex_data: Mapped[str] = mapped_column(Text, default="")
    epcs: Mapped[list] = mapped_column(JSON, default=list)
    report_day: Mapped[str] = mapped_column(String(10), index=True)
    test: Mapped[bool] = mapped_column(Boolean, default=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SysLog(Base):
    """预留：系统日志。映射保存等关键操作会写一条，页面后续再做。"""

    __tablename__ = "sys_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    level: Mapped[str] = mapped_column(String(16), default="信息")
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class PushRecord(Base):
    """预留：推送记录。引擎钩子后续写入。"""

    __tablename__ = "push_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    trolley_tz: Mapped[str] = mapped_column(String(64), default="")
    seat_name: Mapped[str] = mapped_column(String(128), default="")
    line_code: Mapped[str] = mapped_column(String(64), default="")
    proc_code: Mapped[str] = mapped_column(String(64), default="")
    proc_name: Mapped[str] = mapped_column(String(128), default="")
    area_code: Mapped[str] = mapped_column(String(64), default="")
    sport_state: Mapped[str] = mapped_column(String(16), default="still")
    source: Mapped[str] = mapped_column(String(16), default="auto")
    kind: Mapped[str] = mapped_column(String(32), default="实时上报")
    test: Mapped[bool] = mapped_column(Boolean, default=False)
    edited: Mapped[bool] = mapped_column(Boolean, default=False)
    area_name: Mapped[str] = mapped_column(String(128), default="")
    ref_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    identify_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
