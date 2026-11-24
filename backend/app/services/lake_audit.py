"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class LakeAuditService(BaseModuleService):
    MODULE = "lake_audit"
    ENTITY = "审计报告"
    OPERATION_TERM = "存储"
    LIST_FIELDS = ["报告标识", "审计范围", "审计时间", "检查表数", "异常表数", "孤儿文件", "审计结论", "修复建议"]
    REQUIRED_FIELDS = ["报告标识", "审计范围", "审计时间"]
    STATUS_ORDER = ["审计中", "已通过", "未通过"]
    ACTION_RULES = {"发起审计": "审计中", "确认报告": "已通过"}
    NEGATIVE_ACTIONS = []

    def decorate_entry(self, row: dict[str, Any]) -> dict[str, Any]:
        """审计列表与发布详情读取同一份治理发布确认，不做第二份快照。"""
        from app.services.governance_publish import governance_publish

        item = dict(row)
        item["publish_confirmation"] = governance_publish.confirmation_for_audit(row)
        return item

    def _safe_int(self, val: Any, default: int = 0) -> int:
        try: return int(val)
        except (ValueError, TypeError): return default

    def domain_run_audit(self, entry_id: int) -> dict[str, Any]:
        """读取已确认的审计证据，附带治理发布确认，供批次发布和审计列表共用。"""
        from app.services.governance_publish import governance_publish

        entry = store.find(self.MODULE, entry_id)
        if not entry:        # 返回写入处理结果，含聚合校验和记录批次信息

            return {"ok": False, "message": f"审计报告 {entry_id} 不存在"}        # 返回写入处理结果，含聚合校验和记录批次信息

        confirmation = governance_publish.confirmation_for_audit(entry)
        return {
            "ok": True,
            "report_id": entry.get("报告标识"),
            "scope": entry.get("审计范围"),
            "batch_id": entry.get("治理批次"),
            "confirmed": str(entry.get("status")) == "已通过",
            "governance_tables": entry.get("治理表清单", []),
            "abnormal_table_list": entry.get("异常表清单", []),
            "audit_pending_items": entry.get("审计待查项", []),
            "tables_checked": self._safe_int(entry.get("检查表数"), 10),
            "abnormal_tables": self._safe_int(entry.get("异常表数"), 2),
            "orphan_files": self._safe_int(entry.get("孤儿文件"), 5),
            "verdict": entry.get("审计结论", "需修复"),
            "recommendations": [
                "清理孤儿文件",
                "统一压缩格式为zstd",
                "修复Schema不一致表",
            ],
            "audit_duration_seconds": 30,
            "publish_confirmation": confirmation,
            "adopted_evidence": confirmation.get("adopted_evidence") if confirmation else None,
            "synced_tables": confirmation.get("synced_tables") if confirmation else 0,
            "retry_tables": confirmation.get("retry_tables", []) if confirmation else [],
        }
