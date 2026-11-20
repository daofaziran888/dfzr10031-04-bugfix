"""层级体检接口：两期维度数据诊断、父子边更正重算与结账差异审计。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.schemas import ActionResult, PageResult
from app.services.hierarchy_health import HierarchyHealthService

router = APIRouter(prefix="/api/hierarchy_health", tags=["层级体检"])

service = HierarchyHealthService()


class PeriodPayload(BaseModel):
    nodes: list[Any] = Field(default_factory=list)
    edges: list[Any] = Field(default_factory=list)


class BatchCreatePayload(BaseModel):
    name: str | None = None
    period_current: str | None = None
    period_previous: str | None = None
    operator: str | None = None
    current: PeriodPayload
    previous: PeriodPayload | None = None


class EdgeCorrectionPayload(BaseModel):
    old_parent: str
    child: str
    new_parent: str
    new_nodes: list[str] = Field(default_factory=list)
    operator: str | None = None
    note: str | None = None


@router.post("/batches", response_model=ActionResult)
def create_batch(payload: BatchCreatePayload) -> ActionResult:
    """接收两期维度数据及父子连接，输出最小闭环、断链问题组与受牵连叶子。"""
    batch, message = service.create_batch(payload.model_dump())
    if batch is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=batch)


@router.get("/batches", response_model=PageResult[dict])
def list_batches(page: int = 1, size: int = 20) -> PageResult[dict]:
    """列出全部体检批次（最新在前）。"""
    items = service.list_batches()
    total = len(items)
    start = max(page - 1, 0) * size
    return PageResult(items=items[start:start + size], total=total, page=page, size=size)


@router.get("/batches/{batch_id}")
def get_batch(batch_id: str) -> dict[str, Any]:
    """读取单个批次的完整体检报告与更正记录。"""
    batch = service.get_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"体检批次 {batch_id} 不存在")
    return batch


@router.post("/batches/{batch_id}/corrections", response_model=ActionResult)
def correct_edge(batch_id: str, payload: EdgeCorrectionPayload) -> ActionResult:
    """按父子边更正重新计算关联记录，并更新全图中仍挂起的子树。"""
    batch, message = service.correct_edge(batch_id, payload.model_dump())
    if batch is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=batch)


@router.get("/audits", response_model=PageResult[dict])
def list_audits(
    batch_id: str | None = Query(default=None, description="按体检批次过滤"),
    status: str | None = Query(default=None, description="待核验、已核验"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """查看由维度断链影响表生成的结账差异审计记录。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_audits(batch_id=batch_id, status=status)
    start = max(page - 1, 0) * size
    return PageResult(items=items[start:start + size], total=total, page=page, size=size)
