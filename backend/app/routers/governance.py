"""治理发布确认接口：详情、审计列表、批次、证据登记和恢复统一读同一份记录。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.services.governance_publish import governance_publish

router = APIRouter(prefix="/api/governance", tags=["治理发布确认"])


@router.get("/publishes")
def list_governance_publishes(batch_id: str | None = None) -> dict:
    return {"items": governance_publish.list_records(batch_id)}


@router.get("/publishes/{request_id}")
def get_governance_publish(request_id: str) -> dict:
    record = governance_publish.get(request_id)
    if record is None:
        raise HTTPException(404, f"治理发布确认 {request_id} 不存在")
    return record


@router.post("/publishes/{request_id}/recover")
def recover_governance_publish(request_id: str, expected_revision: int | None = None) -> dict:
    """恢复请求：必须重新校验目录；带 expected_revision 时只有一个版本能落地。"""
    return governance_publish.recover(request_id, expected_revision=expected_revision)


@router.get("/audits")
def list_governance_audits() -> dict:
    items = governance_publish.list_audits()
    return {"items": items, "total": len(items)}


@router.get("/batches/{batch_id}")
def get_governance_batch(batch_id: str) -> dict:
    detail = governance_publish.batch_detail(batch_id)
    if not detail["audit"] and not detail["publishes"]:
        raise HTTPException(404, f"治理批次 {batch_id} 不存在")
    return detail


@router.post("/evidence")
def submit_governance_evidence(
    batch_id: str,
    lineage_id: int,
    table: str,
    field: str,
    old_value: str,
    new_value: str,
    source: str = "血缘采集器",
    confirmed: bool = True,
) -> dict:
    """登记血缘更正证据；已确认证据优先于原审计。"""
    return governance_publish.submit_lineage_evidence(
        batch_id=batch_id,
        lineage_id=lineage_id,
        table=table,
        field=field,
        old_value=old_value,
        new_value=new_value,
        source=source,
        confirmed=confirmed,
    )
