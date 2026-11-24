"""治理发布确认测试：目录异常阻断、证据保留、幂等、并发恢复和审计回写。"""
from __future__ import annotations

import sys
import threading
import uuid

sys.path.insert(0, '.')

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.governance_publish import governance_publish
from app.store import store

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_state():
    """每个用例独占发布确认仓库和业务数据，避免证据、目录和版本互相串场。"""
    governance_publish.reset()
    store.reset()
    yield
    governance_publish.reset()
    store.reset()


def _request_id() -> str:
    return f"G71-TEST-{uuid.uuid4().hex[:8]}"


def _register_catalog(identifier: str, tables: str, sync_status: str = "已同步") -> int:
    response = client.post("/api/table_catalog", json={"values": {
        "记录标识": identifier,
        "来源Catalog": "hive_prod",
        "目标Catalog": "unity_prod",
        "同步表名": tables,
        "同步范围": "订单域",
        "同步状态": sync_status,
        "差异行数": "0",
        "同步时间": "2026-11-24",
    }})
    assert response.json()["ok"] is True
    return response.json()["entry"]["id"]


def _publish(request_id: str, catalog_id, batch_id: str = "G71") -> dict:
    response = client.post("/api/chains/metadata-govern", params={
        "lineage_id": 4,
        "audit_id": 2,
        "catalog_id": catalog_id,
        "request_id": request_id,
        "batch_id": batch_id,
    })
    assert response.status_code == 200
    return response.json()


class TestCatalogBlocksPublish:
    def test_missing_catalog_cannot_succeed(self):
        request_id = _request_id()
        data = _publish(request_id, 99999)
        assert data["ok"] is False
        publish = data["publish"]
        assert publish["status"] == "已阻断"
        assert publish["published"] is False
        assert publish["synced_tables"] == 0
        assert publish["publish_count"] == 0
        assert publish["retry_tables"]
        # 已确认的审计证据必须保留下来
        assert publish["original_audit_evidence"]["status"] == "已通过"
        assert publish["original_audit_evidence"]["tables"]

    def test_empty_sync_list_blocks(self):
        identifier = f"CAT-{uuid.uuid4().hex[:6]}"
        entry_id = _register_catalog(identifier, "", sync_status="已同步")
        request_id = _request_id()
        data = _publish(request_id, entry_id)
        assert data["ok"] is False
        assert data["publish"]["status"] == "已阻断"
        assert any("同步确认数据为空" in item for item in data["publish"]["anomalies"])

    def test_retry_does_not_turn_failure_into_success(self):
        request_id = _request_id()
        first = _publish(request_id, 99999)
        second = _publish(request_id, 99999)
        assert first["ok"] is False and second["ok"] is False
        assert second["publish"]["publish_count"] == 0
        assert second["publish"]["attempt_count"] == 2
        assert second["publish"]["original_audit_evidence"] is not None

    def test_retry_records_new_attempt_revision(self):
        request_id = _request_id()
        first = _publish(request_id, 99999)["publish"]
        second = _publish(request_id, 99999)["publish"]
        assert second["revision"] == first["revision"] + 1
        assert second["updated_at"] >= first["updated_at"]


class TestRecoveryAndEvidence:
    def _confirmed_catalog(self) -> str:
        # 补登记目录 99999：按业务目录编号匹配，恢复时才能重新找到它。
        _register_catalog(
            "99999",
            "ods_g71_orders,dwd_g71_orders_enriched,dws_g71_order_summary,"
            "dim_g71_channel,ods_g71_returns",
        )
        return "99999"

    def test_confirmed_correction_becomes_published_content(self):
        request_id = _request_id()
        blocked = _publish(request_id, 99999)
        assert blocked["ok"] is False
        self._confirmed_catalog()

        evidence = client.post("/api/governance/evidence", params={
            "batch_id": "G71",
            "lineage_id": 4,
            "table": "ods_g71_returns",
            "field": "上游表",
            "old_value": "ods_g71_orders",
            "new_value": "ods_g71_returns",
        }).json()
        assert evidence["ok"] is True

        revision = blocked["publish"]["revision"]
        recovered = client.post(
            f"/api/governance/publishes/{request_id}/recover",
            params={"expected_revision": revision},
        ).json()
        assert recovered["ok"] is True
        assert recovered["adopted_evidence"] == "新血缘证据"
        assert "ods_g71_returns" in recovered["published_tables"]
        assert recovered["publish_count"] == 1
        assert recovered["revision"] == revision + 1
        assert recovered["next_tables_to_process"]

    def test_partial_sync_keeps_retry_tables(self):
        identifier = f"CAT-{uuid.uuid4().hex[:6]}"
        entry_id = _register_catalog(identifier, "ods_g71_orders,dws_g71_order_summary")
        request_id = _request_id()
        data = _publish(request_id, entry_id)
        publish = data["publish"]
        assert data["ok"] is True
        assert publish["synced_tables"] == 2
        assert set(publish["published_tables"]) == {"ods_g71_orders", "dws_g71_order_summary"}
        assert "dwd_g71_orders_enriched" in publish["retry_tables"]
        assert "dim_g71_channel" in publish["retry_tables"]
        audits = client.get("/api/governance/audits").json()["items"]
        audit = [item for item in audits if item.get("治理批次") == "G71"][0]
        confirmation = audit["publish_confirmation"]
        assert confirmation["retry_tables"] == publish["retry_tables"]
        assert confirmation["next_tables_to_process"] == publish["next_tables_to_process"]
        assert "dwd_g71_orders_enriched" in confirmation["next_tables_to_process"]

    def test_unconfirmed_correction_not_adopted(self):
        catalog_id = self._confirmed_catalog()
        request_id = _request_id()
        client.post("/api/governance/evidence", params={
            "batch_id": "G71",
            "lineage_id": 4,
            "table": "ods_g71_shadow",
            "field": "上游表",
            "old_value": "ods_g71_orders",
            "new_value": "ods_g71_shadow",
            "confirmed": "false",
        })
        data = _publish(request_id, catalog_id)
        assert data["ok"] is True
        assert data["publish"]["adopted_evidence"] == "原审计"
        assert "ods_g71_shadow" not in data["publish"]["published_tables"]
        assert any("待确认的血缘更正证据" in item for item in data["publish"]["anomalies"])

    def test_non_catalog_id_publish_once(self):
        catalog_id = self._confirmed_catalog()
        request_id = _request_id()
        first = _publish(request_id, catalog_id)
        second = _publish(request_id, catalog_id)
        assert first["ok"] is True
        assert second["ok"] is True
        assert second["publish"]["duplicate"] is True
        assert second["publish"]["publish_count"] == 1

    def test_concurrent_recovery_single_winner(self):
        request_id = _request_id()
        _publish(request_id, 99999)
        self._confirmed_catalog()
        record = client.get(f"/api/governance/publishes/{request_id}").json()
        # 两个恢复请求带同一个 expected_revision，只能有一个版本落地。
        results = []

        def recover():
            results.append(client.post(
                f"/api/governance/publishes/{request_id}/recover",
                params={"expected_revision": record["revision"]},
            ).json())

        threads = [threading.Thread(target=recover) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert sum(1 for item in results if item["applied"]) == 1
        assert sum(1 for item in results if item.get("conflict")) == 1
        final = client.get(f"/api/governance/publishes/{request_id}").json()
        assert final["publish_count"] == 1
        assert final["revision"] == record["revision"] + 1


class TestSingleSourceOfTruth:
    def test_audit_list_and_publish_detail_share_confirmation(self):
        request_id = _request_id()
        _publish(request_id, 99999)
        detail = client.get(f"/api/governance/publishes/{request_id}").json()
        audits = client.get("/api/governance/audits").json()["items"]
        audit = [item for item in audits if item.get("治理批次") == "G71"][0]
        confirmation = audit["publish_confirmation"]
        assert confirmation["publish_id"] == detail["publish_id"]
        assert confirmation["revision"] == detail["revision"]
        assert audit["治理发布确认"] == detail["publish_id"]
        # 审计原始确认结论不被发布回写覆盖
        assert audit["status"] == "已通过"

        resource_detail = client.get("/api/lake_audit/2").json()
        assert resource_detail["publish_confirmation"]["publish_id"] == detail["publish_id"]

    def test_batch_detail_contains_evidence_and_publishes(self):
        request_id = _request_id()
        _publish(request_id, 99999)
        detail = client.get("/api/governance/batches/G71").json()
        assert detail["audit"]["治理批次"] == "G71"
        assert any(item["request_id"] == request_id for item in detail["publishes"])
        assert detail["evidence"] is not None
