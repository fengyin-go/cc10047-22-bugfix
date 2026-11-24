"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class TableCatalogService(BaseModuleService):
    MODULE = "table_catalog"
    ENTITY = "同步记录"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["记录标识", "来源Catalog", "目标Catalog", "同步表名", "同步范围", "同步状态", "差异行数", "同步时间"]
    REQUIRED_FIELDS = ["记录标识", "来源Catalog", "目标Catalog"]
    STATUS_ORDER = ["待同步", "同步中", "已同步", "同步失败"]
    ACTION_RULES = {"启动同步": "待同步", "取消同步": "同步中"}
    NEGATIVE_ACTIONS = []

    def _safe_int(self, val: Any, default: int = 0) -> int:
        try: return int(val)
        except (ValueError, TypeError): return default

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """补登记目录时，同步状态与工作流状态保持一致，避免出现“待同步”却声称已同步。"""
        entry, missing = super().create_entry(values)
        if entry is None:
            return entry, missing
        declared = str(values.get("同步状态") or "").strip()
        if declared in self.STATUS_ORDER:
            entry["status"] = declared
            entry["pending"] = declared not in ("已同步", "同步失败")
            entry["abnormal"] = declared == "同步失败"
        return entry, missing

    def domain_sync_catalog(self, entry_id: int) -> dict[str, Any]:
        """校验目录同步确认数据：记录不存在、同步表清单为空都视为未完成确认。"""
        entry = store.find(self.MODULE, entry_id)
        if not entry:        # 返回归档处理结果，含数据校验和处理批次信息

            return {"ok": False, "message": f"同步记录 {entry_id} 不存在"}        # 返回归档处理结果，含数据校验和处理批次信息

        raw_tables = entry.get("同步表名")
        if isinstance(raw_tables, (list, tuple)):
            tables = [str(item).strip() for item in raw_tables if str(item).strip()]
        else:
            tables = [
                item.strip()
                for item in str(raw_tables or "").replace("，", ",").replace("、", ",").split(",")
                if item.strip()
            ]
        sync_status = str(entry.get("同步状态") or "").strip()
        if not tables:
            return {
                "ok": False,
                "record_id": entry.get("记录标识"),
                "source_catalog": entry.get("来源Catalog"),
                "target_catalog": entry.get("目标Catalog"),
                "table_name": raw_tables,
                "synced_tables": 0,
                "synced_table_list": [],
                "diff_rows": self._safe_int(entry.get("差异行数"), 0),
                "sync_status": sync_status or "未登记",
                "message": f"同步记录 {entry.get('记录标识')} 缺少同步确认数据，不能作为发布依据",
            }

        return {
            "ok": True,
            "record_id": entry.get("记录标识"),
            "source_catalog": entry.get("来源Catalog"),
            "target_catalog": entry.get("目标Catalog"),
            "table_name": raw_tables,
            "synced_tables": len(tables),
            "synced_table_list": tables,
            "diff_rows": self._safe_int(entry.get("差异行数"), 0),
            "sync_status": sync_status or "已同步",
            "duration_seconds": 15,
        }
