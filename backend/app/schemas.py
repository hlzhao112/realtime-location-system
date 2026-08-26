from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


DEFAULT_FIELDS = [
    "areaCode",
    "beamAssetsCode",
    "beamLineCode",
    "identifyTime",
    "procedureCode",
    "sportState",
]
ALL_FIELDS = DEFAULT_FIELDS + ["deviceCode", "projectCode"]
DEFAULT_TPL = """{
  "areaCode": "${areaCode}",
  "beamAssetsCode": "${beamAssetsCode}",
  "beamLineCode": "${beamLineCode}",
  "identifyTime": "${identifyTime}",
  "procedureCode": "${procedureCode}",
  "sportState": "${sportState}"
}"""


class LoginIn(BaseModel):
    username: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str


class PasswordIn(BaseModel):
    old_password: str
    new_password: str


class EpcItem(BaseModel):
    raw: str
    norm: str


class TagNoOut(BaseModel):
    no: str
    epcs: list[EpcItem] = []


class AreaOut(BaseModel):
    id: str
    name: str
    nos: list[str] = []
    epcs: list[str] = []


class ProcOut(BaseModel):
    code: str
    name: str
    order: int
    areas: list[AreaOut] = []


class LineOut(BaseModel):
    id: str
    name: str
    procs: list[ProcOut] = []


class TrolleyOut(BaseModel):
    tz: str
    name: str
    reader: str


class TemplateOut(BaseModel):
    fields: list[str]
    json: str
    custom: bool


class CfgOut(BaseModel):
    pid: str
    name: str
    folder: str = ""
    backup: str = ""
    scan: int = 5
    stable: int = 2
    offline: float = 0.5
    batch: int = 20
    resendMax: int = 10
    retry: int = 3
    logClean: int = 30
    appId: str = ""
    appSecret: str = ""
    tokenUrl: str = ""
    pushUrl: str = ""
    ingestTokenMasked: str = ""


class ProjectOut(BaseModel):
    pid: str
    name: str
    cfg: CfgOut
    lines: list[LineOut] = []
    trolleys: list[TrolleyOut] = []
    tagNos: dict[str, list[str]] = {}
    tpl: TemplateOut
    push: bool = False
    monitor: bool = False
    lastPush: str = "—"
    online: int = 0
    ready: bool = False
    ingestTokenOnce: Optional[str] = None


class ProjectListItem(BaseModel):
    pid: str
    name: str
    lines: int
    areas: int
    trolleys: int
    online: int
    lastPush: str
    push: bool
    ready: bool


class ProjectSaveIn(BaseModel):
    pid: str
    name: str
    folder: str = ""
    backup: str = ""
    scan: int = 5
    stable: int = 2
    offline: float = 0.5
    batch: int = 20
    resendMax: int = 10
    retry: int = 3
    logClean: int = 30
    appId: str = ""
    appSecret: str = ""
    tokenUrl: str = ""
    pushUrl: str = ""
    lines: list[LineOut] = []
    trolleys: list[TrolleyOut] = []
    tagNos: dict[str, list[str]] = {}
    tpl: Optional[TemplateOut] = None
    push: Optional[bool] = None


class CfgPatchIn(BaseModel):
    group: str
    values: dict[str, Any]


class LinePatchIn(BaseModel):
    name: str
    id: str


class ProcPatchIn(BaseModel):
    name: str
    code: str


class AreaPatchIn(BaseModel):
    id: str
    name: str
    nos: Optional[list[str]] = None
    epcs: Optional[list[str]] = None


class TrolleyPatchIn(BaseModel):
    tz: str
    name: str
    reader: str


class ProcOrderIn(BaseModel):
    codes: list[str]


class TemplatePatchIn(BaseModel):
    fields: list[str]
    json: str


class CopyIn(BaseModel):
    pid: str
    name: str


class CheckItem(BaseModel):
    level: str
    title: str
    detail: str
    file: Optional[str] = None
    row: Optional[int] = None
    column: Optional[str] = None


class DiffRow(BaseModel):
    object: str
    current: int
    incoming: int
    added: int
    changed: int
    removed: int


class PreviewRow(BaseModel):
    lineName: str
    lineId: str
    order: int
    procName: str
    procCode: str
    areaName: str
    areaId: str
    no: str
    epcs: list[str]


class TagImportPreview(BaseModel):
    ok: bool
    stats: dict[str, int]
    preview: list[PreviewRow]
    checks: list[CheckItem]
    diff: list[DiffRow]
    unusedNos: list[str] = []
    payload: Optional[dict[str, Any]] = None


class TrolleyImportPreview(BaseModel):
    ok: bool
    stats: dict[str, int]
    preview: list[TrolleyOut]
    checks: list[CheckItem]
    diff: list[DiffRow]
    payload: Optional[list[TrolleyOut]] = None


class IngestIn(BaseModel):
    deviceId: str = Field(..., min_length=1, max_length=64)
    ts: Optional[datetime] = None
    hex: str = ""
    epcs: list[str] = []
    sportState: Optional[str] = None


class IngestOut(BaseModel):
    accepted: bool
    queued: int
    id: str


class LocationOut(BaseModel):
    tz: str
    name: str
    reader: str
    lineCode: str
    lineName: str
    procCode: str
    procName: str
    areaCode: str
    areaName: str
    tagNo: str
    epc: str
    sportState: str
    source: str
    kind: str = "实时上报"
    unassigned: bool = False
    test: bool = False
    reissue: int = 0
    identifyTime: Optional[datetime]
    updatedAt: Optional[datetime]


class RuntimePatchIn(BaseModel):
    push: Optional[bool] = None
    monitor: Optional[bool] = None
    testFast: Optional[bool] = None


class PlaceIn(BaseModel):
    lineCode: str
    procCode: str
    areaCode: str = ""


class PushEditIn(BaseModel):
    procCode: str
    lineCode: str
    areaCode: str = ""
    sportState: str = "静止"


class TestSpeedIn(BaseModel):
    fast: bool = True
