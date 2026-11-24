# 实时流数据湖构建与治理平台

面向实时流数据接入、Delta湖表构建、流批一体转换、分区合并和数据治理的流式数据湖平台

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8011/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5175/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8011`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 流数据源 | `stream_source` | 流数据源 | 来源标识、接入协议、主题名称 |
| 湖表定义 | `lake_table` | 湖表定义 | 表标识、表名称、主键列 |
| 流式摄入 | `stream_ingest` | 摄入管道 | 管道标识、来源流、目标湖表 |
| 提交版本 | `delta_commit` | 版本提交 | 提交标识、目标湖表、提交类型 |
| 分区合并 | `partition_merge` | 合并任务 | 任务标识、目标湖表、源分区 |
| 流Schema演进 | `stream_schema` | Schema变更 | 变更标识、目标湖表、变更类型 |
| 快照管理 | `snapshot_mgr` | 表快照 | 快照标识、目标湖表、快照时间 |
| 流质量校验 | `stream_quality` | 质量检查 | 检查标识、目标管道、校验字段 |
| 历史清理 | `vacuum_job` | 清理作业 | 作业标识、目标湖表、保留版本 |
| 批流合并 | `batch_merge` | 合并管道 | 管道标识、流来源、批来源 |
| 湖表血缘 | `lake_lineage` | 血缘链路 | 链路标识、上游表、下游表 |
| 保留策略 | `retention_policy` | 保留规则 | 规则标识、目标湖表、保留天数 |
| 压缩任务 | `compaction_task` | 压缩任务 | 任务标识、目标湖表、压缩算法 |
| 数据回填 | `backfill_pipeline` | 回填任务 | 任务标识、目标湖表、回填范围 |
| 流处理指标 | `stream_metric` | 处理指标 | 指标标识、关联管道、指标类型 |
| 格式迁移 | `iceberg_migrate` | 迁移计划 | 计划标识、源格式、目标格式 |
| 表目录同步 | `table_catalog` | 同步记录 | 记录标识、来源Catalog、目标Catalog |
| 湖仓审计 | `lake_audit` | 审计报告 | 报告标识、审计范围、审计时间 |

## 治理发布确认

元数据治理链路（`lake_lineage → lake_audit → table_catalog`）的发布结果统一落在
`backend/app/services/governance_publish.py` 的发布确认仓库里，页面入口是 `/governance`。

- **目录异常阻断发布**：目标目录不存在、同步表清单为空、目录未完成同步确认，批次状态为
  `已阻断`，`synced_tables` 保持 0，不会出现“已成功但同步表数为空”。
- **审计证据保留**：已确认的审计证据（表清单、异常表、待查项、确认时间）会写入发布确认；
  失败重试只追加异常和待重试表，不会改写或清空审计结论。
- **证据优先级**：已确认的新血缘更正证据优先于原审计，发布详情通过 `adopted_evidence`、
  `evidence_diff` 明确展示本次采用哪一份证据、哪些表被替换；未确认证据只提示不生效。
- **失败重试不得写成成功**：同一 `request_id` 已发布则直接返回幂等结果（`duplicate=true`，
  `publish_count` 保持 1）；被阻断过的请求每次重试都要重新校验目录和同步数据。
- **并发恢复只有一个版本落地**：恢复请求携带 `expected_revision`，在进程内锁里做
  compare-and-set，两个同时到达的恢复请求只有一个能推进确认版本。
- **同一份记录多入口读取**：`/api/governance/publishes/{request_id}`、`/api/governance/audits`、
  `/api/lake_audit` 列表与详情都读取同一条发布确认；发布结果回写审计的
  `治理发布状态 / 采用证据 / 发布同步表数 / 待重试表 / 发布次数 / 治理发布待办`。

相关接口：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/chains/metadata-govern` | 治理批次发布（`request_id`、`batch_id` 可选） |
| GET | `/api/governance/publishes?batch_id=G71` | 批次发布确认列表 |
| GET | `/api/governance/publishes/{request_id}` | 发布详情 |
| POST | `/api/governance/publishes/{request_id}/recover?expected_revision=N` | 恢复/重试 |
| GET | `/api/governance/audits` | 审计列表（含同一份发布确认） |
| GET | `/api/governance/batches/{batch_id}` | 批次详情：审计、血缘、证据、发布记录 |
| POST | `/api/governance/evidence` | 登记血缘更正证据（`confirmed` 控制是否生效） |

G71 演示数据在种子数据里：审计报告 `id=2`（已确认）、血缘 `id=4`，目录 `99999` 初始不存在。
页面 `/governance` 可以复现“目录缺失 → 补登记 → 登记更正证据 → 恢复发布 → 再重试”的全过程，
并看到已发布内容、异常项、下次仍需处理的表和发布次数。

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

领域：数据工程
子方向：数仓与数据湖
