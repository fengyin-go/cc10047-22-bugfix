"""治理发布确认：把血缘、审计、目录同步收敛成一份可追溯、幂等的发布记录。

设计要点：
- 治理批次的发布必须先校验目录同步确认数据；目录不存在或同步表清单为空时阻断发布，
  绝不能在缺少目录的情况下返回成功，也不能留下空的同步表数。
- 审计证据一旦确认就保留在发布确认记录里，失败重试只追加异常和待重试表，不改写审计结论。
- 已确认的新血缘更正证据优先于原审计证据；发布详情会明确写出本次采用哪一份证据。
- 同一 request_id 只会真正发布一次；失败后的重试必须重新校验，不能把失败直接写成成功。
- 恢复请求携带 expected_revision，配合进程内锁做 compare-and-set，两个并发恢复只能落地一个版本。
"""
from __future__ import annotations

import copy
import threading
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _as_table_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        raw = list(value)
    else:
        raw = str(value or "").replace("，", ",").replace("、", ",").split(",")
    tables: list[str] = []
    for item in raw:
        table = str(item).strip()
        if table and table not in tables:
            tables.append(table)
    return tables


def _same_identifier(left: Any, right: Any) -> bool:
    return str(left).strip() == str(right).strip()


class GovernancePublishService:
    """进程内治理发布确认仓库，行为与 Store 一样只依赖标准库。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._records: dict[str, dict[str, Any]] = {}
        self._records_by_publish_id: dict[str, dict[str, Any]] = {}
        self._evidence: list[dict[str, Any]] = []
        self._publish_seq = 0
        self._evidence_seq = 0

    # ------------------------------------------------------------------
    # 查询辅助
    # ------------------------------------------------------------------
    @staticmethod
    def _rows(module: str) -> list[dict[str, Any]]:
        from app.store import store

        return store.rows(module)

    def _find_entry(self, module: str, identifier: Any, identifier_field: str) -> dict[str, Any] | None:
        rows = self._rows(module)
        for row in rows:
            if _same_identifier(row.get(identifier_field), identifier):
                return row
        for row in rows:
            try:
                if int(row.get("id", 0)) == int(identifier):
                    return row
            except (TypeError, ValueError):
                continue
        return None

    def find_audit(self, audit_id: Any) -> dict[str, Any] | None:
        return self._find_entry("lake_audit", audit_id, "报告标识")

    def find_lineage(self, lineage_id: Any) -> dict[str, Any] | None:
        return self._find_entry("lake_lineage", lineage_id, "链路标识")

    def find_catalog(self, catalog_id: Any) -> dict[str, Any] | None:
        """目录既支持数值 id，也支持业务上的目录编号（例如 99999）。"""
        return self._find_entry("table_catalog", catalog_id, "记录标识")

    # ------------------------------------------------------------------
    # 证据
    # ------------------------------------------------------------------
    def submit_lineage_evidence(
        self,
        *,
        batch_id: str,
        lineage_id: Any,
        table: str,
        field: str,
        old_value: str,
        new_value: str,
        source: str = "血缘采集器",
        confirmed: bool = True,
    ) -> dict[str, Any]:
        with self._lock:
            for item in self._evidence:
                if (
                    item["batch_id"] == batch_id
                    and _same_identifier(item["lineage_id"], lineage_id)
                    and item["table"] == table
                    and item["field"] == field
                    and item["old_value"] == old_value
                    and item["new_value"] == new_value
                ):
                    return {
                        "ok": True,
                        "applied": False,
                        "duplicate": True,
                        "message": "同一条血缘更正证据已登记，未重复写入",
                        "evidence": copy.deepcopy(item),
                    }

            self._evidence_seq += 1
            correction = {
                "correction_id": f"LE-{self._evidence_seq:04d}",
                "batch_id": batch_id,
                "lineage_id": lineage_id,
                "table": table,
                "field": field,
                "old_value": old_value,
                "new_value": new_value,
                "source": source,
                "confirmed": bool(confirmed),
                "status": "已确认" if confirmed else "待验证",
                "revision": self._evidence_seq,
                "submitted_at": _now(),
            }
            self._evidence.append(correction)

            # 已确认的更正会直接作用到有效血缘；原审计证据仍保留在发布确认里。
            if confirmed:
                lineage = self.find_lineage(lineage_id)
                if lineage is not None:
                    if field in ("上游表", "下游表", "转换算子"):
                        lineage[field] = new_value
                    elif field in ("表", "table"):
                        for key in ("上游表", "下游表"):
                            if lineage.get(key) == old_value:
                                lineage[key] = new_value
                    lineage["链路版本"] = "v2"
                    lineage["证据来源"] = correction["correction_id"]
                    lineage["更正时间"] = correction["submitted_at"]

            return {
                "ok": True,
                "applied": True,
                "duplicate": False,
                "message": "血缘更正证据已登记并生效",
                "evidence": copy.deepcopy(correction),
            }

    def list_evidence(self, batch_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            items = [
                item for item in self._evidence
                if batch_id is None or item["batch_id"] == batch_id
            ]
            return copy.deepcopy(sorted(items, key=lambda item: item["revision"]))

    def _latest_correction(self, batch_id: str, lineage_id: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """返回（已确认的最新更正, 任意最新更正），前者优先用于发布内容。"""
        with self._lock:
            candidates = [
                item for item in self._evidence
                if item["batch_id"] == batch_id
                and _same_identifier(item["lineage_id"], lineage_id)
            ]
            latest_any = max(candidates, key=lambda item: item["revision"]) if candidates else None
            confirmed = [item for item in candidates if item["confirmed"]]
            latest_confirmed = max(confirmed, key=lambda item: item["revision"]) if confirmed else None
            return copy.deepcopy(latest_confirmed), copy.deepcopy(latest_any)

    # ------------------------------------------------------------------
    # 发布
    # ------------------------------------------------------------------
    def publish(
        self,
        *,
        request_id: str | None = None,
        batch_id: str | None = None,
        lineage_id: Any,
        audit_id: Any,
        catalog_id: Any,
    ) -> dict[str, Any]:
        audit = self.find_audit(audit_id)
        effective_batch = batch_id or self._infer_batch(audit, lineage_id)
        effective_request = request_id or f"{effective_batch or 'GOV'}-{lineage_id}-{audit_id}-{catalog_id}"

        with self._lock:
            existing = self._records.get(effective_request)
            if existing is not None:
                # 已发布的请求只生效一次；阻断/失败过的请求允许显式重试，但必须重新校验。
                if existing["status"] == "已发布":
                    return self._result(
                        existing, ok=True, applied=False, duplicate=True,
                        message="同一发布请求已生效，本次未重复发布",
                    )
                return self._advance(existing, audit_id=audit_id, lineage_id=lineage_id,
                                     catalog_id=catalog_id)

            self._publish_seq += 1
            record: dict[str, Any] = {
                "publish_id": f"GP-{self._publish_seq:04d}",
                "sequence": self._publish_seq,
                "request_id": effective_request,
                "batch_id": effective_batch,
                "lineage_id": lineage_id,
                "audit_id": audit_id,
                "catalog_id": catalog_id,
                "status": "待发布",
                "published": False,
                "publish_count": 0,
                "attempt_count": 0,
                "revision": 0,
                "adopted_evidence": "原审计",
                "evidence_priority": "新血缘证据优先于原审计",
                "audit_tables": [],
                "effective_tables": [],
                "evidence_diff": [],
                "published_tables": [],
                "synced_tables": 0,
                "retry_tables": [],
                "next_tables_to_process": [],
                "expected_tables": [],
                "anomalies": [],
                "audit_pending_items": [],
                "original_audit_evidence": None,
                "correction_evidence": None,
                "applied_correction": None,
                "catalog_sync": None,
                "requested_at": _now(),
                "updated_at": _now(),
                "published_at": None,
            }
            self._records[effective_request] = record
            self._records_by_publish_id[record["publish_id"]] = record
            return self._advance(record, audit_id=audit_id, lineage_id=lineage_id,
                                 catalog_id=catalog_id)

    def _advance(
        self,
        record: dict[str, Any],
        *,
        audit_id: Any,
        lineage_id: Any,
        catalog_id: Any,
    ) -> dict[str, Any]:
        """在锁内推进一次尝试并递增确认版本，保证详情读到的是完整一致的一版。"""
        # 先递增版本再执行，回写审计的版本号才会和发布详情一致。
        record["revision"] += 1
        ok, message = self._attempt(record, audit_id=audit_id, lineage_id=lineage_id,
                                    catalog_id=catalog_id)
        return self._result(record, ok=ok, applied=True, duplicate=False, message=message)

    def recover(
        self,
        request_id: str,
        *,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            record = self._records.get(request_id)
            if record is None:
                return {"ok": False, "applied": False, "message": f"发布确认 {request_id} 不存在"}
            if expected_revision is not None and int(expected_revision) != int(record["revision"]):
                return self._result(
                    record, ok=False, applied=False, conflict=True,
                    message=(
                        f"发布确认版本已从 {expected_revision} 变为 {record['revision']}，"
                        "本恢复请求未落地"
                    ),
                )
            if record["status"] == "已发布":
                return self._result(
                    record, ok=True, applied=False, duplicate=True,
                    message="该发布请求已生效，恢复请求未重复发布",
                )
            return self._advance(
                record,
                audit_id=record["audit_id"],
                lineage_id=record["lineage_id"],
                catalog_id=record["catalog_id"],
            )

    def _infer_batch(self, audit: dict[str, Any] | None, lineage_id: Any) -> str | None:
        if audit and audit.get("治理批次"):
            return str(audit["治理批次"])
        if audit and audit.get("治理表清单"):
            return str(audit.get("报告标识") or audit.get("id"))
        lineage = self.find_lineage(lineage_id)
        if lineage and lineage.get("治理批次"):
            return str(lineage["治理批次"])
        return None

    def _attempt(
        self,
        record: dict[str, Any],
        *,
        audit_id: Any,
        lineage_id: Any,
        catalog_id: Any,
    ) -> tuple[bool, str]:
        """执行一次发布校验。任何一次尝试都必须从当前审计/血缘/目录重新取数。"""
        record["audit_id"] = audit_id
        record["lineage_id"] = lineage_id
        record["catalog_id"] = catalog_id
        record["attempt_count"] = int(record.get("attempt_count", 0)) + 1
        record["updated_at"] = _now()

        anomalies: list[str] = []
        blocking = False
        audit = self.find_audit(audit_id)
        lineage = self.find_lineage(lineage_id)
        governance_mode = bool(record.get("batch_id")) or bool(audit and audit.get("治理表清单"))

        # 1) 审计证据：已确认证据要保留，失败重试不能把它冲掉。
        if audit is not None:
            record["original_audit_evidence"] = self._audit_evidence(audit)
        elif record.get("original_audit_evidence"):
            anomalies.append("审计证据本次读取失败，已保留上次确认的审计证据")
        else:
            anomalies.append(f"审计报告 {audit_id} 不存在，无法形成治理发布")
            blocking = True

        if governance_mode and audit is not None and str(audit.get("status")) != "已通过":
            anomalies.append(f"审计报告 {audit.get('报告标识', audit_id)} 尚未确认，治理批次不允许发布")
            blocking = True

        # 2) 有效血缘：已确认的新证据优先，否则沿用原审计。
        batch_id = str(record.get("batch_id") or "")
        latest_confirmed, latest_any = self._latest_correction(batch_id, lineage_id)
        original_tables = self._expected_tables(audit, lineage)
        expected_tables = list(original_tables)
        applied_correction: dict[str, Any] | None = None
        if latest_confirmed is not None:
            record["adopted_evidence"] = "新血缘证据"
            record["correction_evidence"] = latest_confirmed
            applied_correction = latest_confirmed
            expected_tables = self._apply_correction(expected_tables, latest_confirmed)
        else:
            record["adopted_evidence"] = "原审计"
            record["correction_evidence"] = latest_any
            if latest_any is not None:
                anomalies.append("存在待确认的血缘更正证据，本次仍采用原审计")
        record["applied_correction"] = applied_correction
        record["audit_tables"] = original_tables
        record["expected_tables"] = expected_tables
        record["effective_tables"] = expected_tables
        record["evidence_diff"] = self._evidence_diff(original_tables, expected_tables)

        # 3) 目录同步确认：目录不存在或同步数据为空，直接阻断。
        catalog = self.find_catalog(catalog_id)
        catalog_sync: dict[str, Any] | None = None
        synced_list: list[str] = []
        if catalog is None:
            anomalies.append(f"目标目录 {catalog_id} 不存在，治理批次已阻断，不能返回成功")
            blocking = True
        else:
            synced_list = _as_table_list(catalog.get("同步表名"))
            sync_status = str(catalog.get("同步状态") or catalog.get("status") or "").strip()
            catalog_sync = {
                "catalog_identifier": catalog.get("记录标识"),
                "source_catalog": catalog.get("来源Catalog"),
                "target_catalog": catalog.get("目标Catalog"),
                "sync_status": sync_status,
                "synced_table_list": synced_list,
                "diff_rows": catalog.get("差异行数"),
                "sync_time": catalog.get("同步时间"),
            }
            if not synced_list:
                anomalies.append(f"目录 {catalog_id} 的同步确认数据为空，缺少可发布的同步表清单")
                blocking = True
            if governance_mode and sync_status != "已同步":
                anomalies.append(f"目录 {catalog_id} 同步状态为「{sync_status or '未登记'}」，尚未完成同步确认")
                blocking = True
        record["catalog_sync"] = catalog_sync

        # 4) 计算发布内容。
        published_tables: list[str] = []
        retry_tables: list[str] = []
        if not blocking:
            if governance_mode:
                published_tables = [table for table in expected_tables if table in synced_list]
                retry_tables = [table for table in expected_tables if table not in published_tables]
                if not published_tables:
                    anomalies.append("目录同步表与治理表清单没有交集，发布内容为空，已阻断")
                    blocking = True
            else:
                # 兼容普通元数据治理记录：目录同步表即本次发布内容。
                published_tables = synced_list
                retry_tables = []
                expected_tables = synced_list
                record["expected_tables"] = expected_tables
        else:
            retry_tables = expected_tables

        if blocking:
            record["status"] = "已阻断" if catalog is None or not synced_list else "发布失败"
            record["published"] = False
            record["published_tables"] = []
            record["synced_tables"] = 0
            record["retry_tables"] = retry_tables
            record["next_tables_to_process"] = self._next_tables(audit, retry_tables)
            record["anomalies"] = anomalies
            record["audit_pending_items"] = self._build_pending(audit, retry_tables, applied_correction)
            self._write_back_audit(record, audit)
            return False, anomalies[0] if anomalies else "治理批次未能发布"

        record["status"] = "已发布"
        record["published"] = True
        record["published_tables"] = published_tables
        record["synced_tables"] = len(published_tables)
        record["retry_tables"] = retry_tables
        record["next_tables_to_process"] = self._next_tables(audit, retry_tables)
        record["anomalies"] = anomalies
        record["audit_pending_items"] = self._build_pending(audit, retry_tables, applied_correction)
        record["publish_count"] = 1
        record["published_at"] = _now()
        self._write_back_audit(record, audit)
        message = f"治理发布成功，同步 {record['synced_tables']} 张表"
        if retry_tables:
            message += f"，仍需重试：{'、'.join(retry_tables)}"
        return True, message

    def _audit_evidence(self, audit: dict[str, Any]) -> dict[str, Any]:
        tables = _as_table_list(audit.get("治理表清单"))
        abnormal = _as_table_list(audit.get("异常表清单"))
        return {
            "source": "原审计",
            "audit_id": audit.get("id"),
            "audit_report": audit.get("报告标识"),
            "scope": audit.get("审计范围"),
            "audit_time": audit.get("审计时间"),
            "confirmed_at": audit.get("审计确认时间"),
            "status": audit.get("status"),
            "tables": tables,
            "abnormal_tables": abnormal,
            "tables_checked": audit.get("检查表数"),
            "abnormal_table_count": audit.get("异常表数"),
            "orphan_files": audit.get("孤儿文件"),
            "verdict": audit.get("审计结论"),
            "recommendation": audit.get("修复建议"),
        }

    def _expected_tables(
        self,
        audit: dict[str, Any] | None,
        lineage: dict[str, Any] | None,
    ) -> list[str]:
        tables: list[str] = []
        if audit:
            tables.extend(_as_table_list(audit.get("治理表清单")))
        if not tables and lineage:
            tables.extend(_as_table_list(lineage.get("上游表")))
            tables.extend(_as_table_list(lineage.get("下游表")))
        if not tables:
            tables.extend(_as_table_list(audit.get("审计范围") if audit else None))
        seen: list[str] = []
        for table in tables:
            if table and table not in seen:
                seen.append(table)
        return seen

    def _apply_correction(self, tables: list[str], correction: dict[str, Any]) -> list[str]:
        old_value = str(correction.get("old_value") or "")
        new_value = str(correction.get("new_value") or "")
        if not old_value or not new_value:
            return list(tables)
        corrected = [new_value if table == old_value else table for table in tables]
        if new_value not in corrected:
            corrected.append(new_value)
        seen: list[str] = []
        for table in corrected:
            if table not in seen:
                seen.append(table)
        return seen

    def _next_tables(self, audit: dict[str, Any] | None, retry_tables: list[str]) -> list[str]:
        """下一次仍需处理的表：本次未发布的表 > 审计异常表 > 审计待查项里提到的表。"""
        next_tables = list(retry_tables)
        if audit:
            for table in _as_table_list(audit.get("异常表清单")):
                if table not in next_tables:
                    next_tables.append(table)
            for item in _as_table_list(audit.get("审计待查项")):
                for table in _as_table_list(audit.get("治理表清单")):
                    if table in item and table not in next_tables:
                        next_tables.append(table)
        return next_tables

    def _evidence_diff(self, original: list[str], effective: list[str]) -> list[str]:
        diff: list[str] = []
        for table in original:
            if table not in effective:
                diff.append(f"{table} 已由更正证据替换")
        for table in effective:
            if table not in original:
                diff.append(f"{table} 来自新血缘证据")
        return diff

    def _build_pending(
        self,
        audit: dict[str, Any] | None,
        retry_tables: list[str],
        correction: dict[str, Any] | None,
    ) -> list[str]:
        pending: list[str] = []
        if audit:
            # 只读审计确认时的原始待查项，绝不把自己上一次回写的派生待办再读回来。
            pending.extend(_as_table_list(audit.get("审计待查项")))
            if not pending:
                for table in _as_table_list(audit.get("异常表清单")):
                    pending.append(f"{table} 异常待复核")
                orphan_files = audit.get("孤儿文件")
                if orphan_files not in (None, "", 0, "0"):
                    pending.append(f"孤儿文件 {orphan_files} 个待清理")
        if retry_tables:
            pending.append(f"待重试表：{'、'.join(retry_tables)}")
        if correction is not None:
            pending.append(
                f"血缘更正待复核：{correction.get('table')} {correction.get('field')} "
                f"{correction.get('old_value')}→{correction.get('new_value')}"
            )
        seen: list[str] = []
        for item in pending:
            if item and item not in seen:
                seen.append(item)
        return seen

    def _write_back_audit(self, record: dict[str, Any], audit: dict[str, Any] | None) -> None:
        """把发布结果回写审计待办；原始审计证据和确认结论保持不变。"""
        if audit is None:
            return
        audit["治理发布确认"] = record["publish_id"]
        audit["治理发布请求"] = record["request_id"]
        audit["治理发布状态"] = record["status"]
        audit["采用证据"] = record["adopted_evidence"]
        audit["发布同步表数"] = record["synced_tables"]
        audit["已发布表"] = list(record["published_tables"])
        audit["待重试表"] = list(record["retry_tables"])
        audit["发布次数"] = record["publish_count"]
        audit["发布确认版本"] = record["revision"]
        audit["异常项"] = list(record["anomalies"])
        audit["治理发布待办"] = list(record["audit_pending_items"])
        audit["发布确认时间"] = record["updated_at"]

    def _result(
        self,
        record: dict[str, Any],
        *,
        ok: bool,
        applied: bool,
        message: str,
        duplicate: bool = False,
        conflict: bool = False,
    ) -> dict[str, Any]:
        snapshot = copy.deepcopy(record)
        payload = {
            "ok": bool(ok and record.get("published")),
            "applied": applied,
            "duplicate": duplicate,
            "conflict": conflict,
            "message": message,
            "publish": snapshot,
        }
        # 同时铺开确认记录字段，详情入口和列表入口读到的都是同一份记录。
        payload.update(snapshot)
        return payload

    # ------------------------------------------------------------------
    # 读取入口
    # ------------------------------------------------------------------
    def get(self, request_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._records.get(request_id)
            return copy.deepcopy(record) if record is not None else None

    def get_by_publish_id(self, publish_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._records_by_publish_id.get(publish_id)
            return copy.deepcopy(record) if record is not None else None

    def list_records(self, batch_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            records = [
                record for record in self._records.values()
                if batch_id is None or str(record.get("batch_id")) == str(batch_id)
            ]
            return copy.deepcopy(sorted(records, key=lambda item: item["publish_id"]))

    def confirmation_for_audit(self, audit: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            # 优先按审计回写的发布确认指针取数，保证详情与列表永远指向同一条记录。
            pointer = audit.get("治理发布确认")
            if pointer and pointer in self._records_by_publish_id:
                return copy.deepcopy(self._records_by_publish_id[pointer])
            matches = [
                record for record in self._records.values()
                if _same_identifier(record.get("audit_id"), audit.get("id"))
                or _same_identifier(record.get("audit_id"), audit.get("报告标识"))
            ]
            if not matches:
                return None
            latest = max(matches, key=lambda item: (item["revision"], item["sequence"]))
            return copy.deepcopy(latest)

    def reset(self) -> None:
        """只供测试隔离：清空发布确认和证据，不影响业务数据。"""
        with self._lock:
            self._records.clear()
            self._records_by_publish_id.clear()
            self._evidence.clear()
            self._publish_seq = 0
            self._evidence_seq = 0

    def list_audits(self) -> list[dict[str, Any]]:
        """审计列表入口：每行都从同一份发布确认仓库取数，不做二次快照。"""
        items: list[dict[str, Any]] = []
        for audit in self._rows("lake_audit"):
            item = copy.deepcopy(audit)
            item["publish_confirmation"] = self.confirmation_for_audit(audit)
            items.append(item)
        return items

    def batch_detail(self, batch_id: str) -> dict[str, Any]:
        audits = [item for item in self.list_audits() if str(item.get("治理批次")) == str(batch_id)]
        if not audits:
            audits = [
                item for item in self.list_audits()
                if str(item.get("报告标识")) == str(batch_id)
            ]
        audit = audits[0] if audits else None
        lineage = None
        if audit:
            scope_audit_id = audit.get("id")
            for record in self.list_records(batch_id):
                if _same_identifier(record.get("audit_id"), scope_audit_id):
                    lineage = self.find_lineage(record.get("lineage_id"))
                    break
        if lineage is None:
            for row in self._rows("lake_lineage"):
                if str(row.get("链路标识")) == str(batch_id):
                    lineage = copy.deepcopy(row)
                    break
        return {
            "batch_id": batch_id,
            "audit": audit,
            "lineage": lineage,
            "evidence": self.list_evidence(batch_id),
            "publishes": self.list_records(batch_id),
        }


governance_publish = GovernancePublishService()
