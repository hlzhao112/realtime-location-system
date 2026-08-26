from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_current_user

router = APIRouter(prefix="/api/v1/projects", tags=["reserved"], dependencies=[Depends(get_current_user)])


@router.get("/{pid}/board")
def reserved_board(pid: str):
    raise HTTPException(501, "主看板将在后续迭代提供，位置数据已由解析引擎写入 latest_locations")


@router.get("/{pid}/raw")
def reserved_raw(pid: str):
    raise HTTPException(501, "原始数据监控将在后续迭代提供，上报 HEX 已写入 raw_reports")


@router.get("/{pid}/logs")
def reserved_logs(pid: str):
    raise HTTPException(501, "系统日志页将在后续迭代提供，关键操作已写入 sys_logs")
