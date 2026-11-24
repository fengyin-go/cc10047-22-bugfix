<template>
  <section class="page" data-testid="governance-publish">
    <header class="page-head">
      <div>
        <h2>治理发布确认</h2>
        <p class="page-desc">
          发布详情与审计列表读取同一份确认记录；目录不存在或同步数据为空时批次必须阻断。
        </p>
      </div>
      <div class="page-actions">
        <label class="filter-item">
          <span>治理批次</span>
          <input v-model="batchId" data-testid="governance-batch-input" @keyup.enter="reload" />
        </label>
        <button class="btn" data-testid="governance-refresh" type="button" @click="reload">刷新</button>
      </div>
    </header>

    <p v-if="errorMessage" class="error-text" data-testid="governance-error">{{ errorMessage }}</p>
    <p v-if="notice" class="notice-text" data-testid="governance-notice">{{ notice }}</p>

    <div class="stat-row" v-if="confirmation">
      <article class="stat-card">
        <span class="stat-label">发布状态</span>
        <strong class="stat-value" data-testid="governance-status">{{ confirmation.status }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">采用证据</span>
        <strong class="stat-value" data-testid="governance-evidence">
          {{ confirmation.adopted_evidence }}
        </strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">本次同步表数</span>
        <strong class="stat-value" data-testid="governance-synced">
          {{ confirmation.synced_tables }}
        </strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">发布次数</span>
        <strong class="stat-value" data-testid="governance-count">
          {{ confirmation.publish_count }}
        </strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">确认版本</span>
        <strong class="stat-value" data-testid="governance-revision">
          {{ confirmation.revision }}
        </strong>
      </article>
    </div>

    <template v-if="confirmation">
      <section class="publish-grid">
        <article class="panel">
          <h3>发布内容</h3>
          <p class="panel-hint">
            证据优先级：{{ confirmation.evidence_priority }}
          </p>
          <ul class="tag-list" data-testid="governance-published-tables">
            <li v-for="table in confirmation.published_tables" :key="table" class="tag ok">
              {{ table }}
            </li>
            <li v-if="!confirmation.published_tables.length" class="tag muted">暂无已发布表</li>
          </ul>
          <template v-if="confirmation.evidence_diff.length">
            <h4>证据差异</h4>
            <ul class="plain-list">
              <li v-for="item in confirmation.evidence_diff" :key="item">{{ item }}</li>
            </ul>
          </template>
        </article>

        <article class="panel">
          <h3>下一次仍需处理的表</h3>
          <ul class="tag-list" data-testid="governance-next-tables">
            <li v-for="table in confirmation.next_tables_to_process" :key="table" class="tag warn">
              {{ table }}
            </li>
            <li v-if="!confirmation.next_tables_to_process.length" class="tag muted">无</li>
          </ul>
          <h4>异常项</h4>
          <ul class="plain-list" data-testid="governance-anomalies">
            <li v-for="item in confirmation.anomalies" :key="item">{{ item }}</li>
            <li v-if="!confirmation.anomalies.length" class="muted">无</li>
          </ul>
        </article>

        <article class="panel">
          <h3>审计待查项</h3>
          <ul class="plain-list" data-testid="governance-pending">
            <li v-for="item in confirmation.audit_pending_items" :key="item">{{ item }}</li>
            <li v-if="!confirmation.audit_pending_items.length" class="muted">无</li>
          </ul>
          <h4>目录同步确认</h4>
          <dl v-if="confirmation.catalog_sync" class="kv-list">
            <dt>目标目录</dt>
            <dd>{{ confirmation.catalog_sync.target_catalog }}</dd>
            <dt>同步状态</dt>
            <dd>{{ confirmation.catalog_sync.sync_status }}</dd>
            <dt>同步表</dt>
            <dd>{{ syncedTableText }}</dd>
          </dl>
          <p v-else class="muted">目录读取失败或不存在</p>
        </article>
      </section>

      <div class="page-actions" data-testid="governance-actions">
        <button
          v-if="confirmation.status !== '已发布'"
          class="btn primary"
          data-testid="governance-recover"
          type="button"
          :disabled="busy"
          @click="recover"
        >
          重试发布
        </button>
        <button
          v-else
          class="btn primary"
          data-testid="governance-retry"
          type="button"
          :disabled="busy"
          @click="retryAgain"
        >
          再重试一次（核对幂等）
        </button>
        <span class="page-desc">请求标识：{{ confirmation.request_id }}</span>
      </div>
    </template>

    <section class="panel">
      <h3>血缘更正证据</h3>
      <p class="panel-hint">已确认的更正证据优先于原审计；未确认证据只提示，不改写发布内容。</p>
      <div class="filter-bar">
        <label class="filter-item">
          <span>关联血缘</span>
          <input v-model.number="evidenceForm.lineage_id" data-testid="evidence-lineage" />
        </label>
        <label class="filter-item">
          <span>表</span>
          <input v-model="evidenceForm.table" data-testid="evidence-table" />
        </label>
        <label class="filter-item">
          <span>字段</span>
          <input v-model="evidenceForm.field" data-testid="evidence-field" />
        </label>
        <label class="filter-item">
          <span>原值</span>
          <input v-model="evidenceForm.old_value" data-testid="evidence-old" />
        </label>
        <label class="filter-item">
          <span>新值</span>
          <input v-model="evidenceForm.new_value" data-testid="evidence-new" />
        </label>
        <label class="filter-item">
          <span>已确认</span>
          <input v-model="evidenceForm.confirmed" type="checkbox" data-testid="evidence-confirmed" />
        </label>
        <button class="btn" data-testid="evidence-submit" type="button" :disabled="busy" @click="submitEvidence">
          登记更正证据
        </button>
      </div>
      <table class="data-table">
        <thead>
          <tr><th>证据编号</th><th>表</th><th>字段</th><th>变更</th><th>状态</th><th>登记时间</th></tr>
        </thead>
        <tbody>
          <tr v-for="item in evidenceRows" :key="String(item.correction_id)">
            <td>{{ item.correction_id }}</td>
            <td>{{ item.table }}</td>
            <td>{{ item.field }}</td>
            <td>{{ item.old_value }} → {{ item.new_value }}</td>
            <td>{{ item.status }}</td>
            <td>{{ item.submitted_at }}</td>
          </tr>
          <tr v-if="!evidenceRows.length">
            <td colspan="6" class="empty-state">暂无更正证据</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="panel">
      <h3>审计列表（读取同一份发布确认）</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>报告标识</th><th>审计状态</th><th>发布确认</th><th>采用证据</th>
            <th>同步表数</th><th>发布次数</th><th>待重试表</th><th>审计待查项</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in auditRows" :key="String(row.id)" :data-testid="`governance-audit-${row.id}`">
            <td>{{ row.报告标识 }}</td>
            <td>{{ row.status }}</td>
            <td>{{ row.publish_confirmation?.status ?? '—' }}</td>
            <td>{{ row.publish_confirmation?.adopted_evidence ?? '—' }}</td>
            <td>{{ row.publish_confirmation?.synced_tables ?? 0 }}</td>
            <td>{{ row.publish_confirmation?.publish_count ?? 0 }}</td>
            <td>{{ (row.publish_confirmation?.retry_tables ?? []).join('、') || '—' }}</td>
            <td>{{ (row.publish_confirmation?.audit_pending_items ?? []).join('；') || '—' }}</td>
          </tr>
        </tbody>
      </table>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  listGovernanceAudits,
  loadGovernanceBatch,
  recoverGovernancePublish,
  submitLineageEvidence,
} from '@/api/backend-service'
import type {
  GovernanceAudit,
  PublishConfirmation,
} from '@/api/backend-service'

const props = defineProps<{ batchId?: string }>()
const batchId = ref(props.batchId || 'G71')
const batch = ref<Awaited<ReturnType<typeof loadGovernanceBatch>> | null>(null)
const auditRows = ref<GovernanceAudit[]>([])
const errorMessage = ref('')
const notice = ref('')
const busy = ref(false)
const evidenceForm = ref({
  lineage_id: 4,
  table: 'ods_g71_returns',
  field: '上游表',
  old_value: 'ods_g71_orders',
  new_value: 'ods_g71_returns',
  confirmed: true,
})

const confirmation = computed<PublishConfirmation | null>(
  () => batch.value?.publishes.at(-1) ?? null,
)
const evidenceRows = computed(() => batch.value?.evidence ?? [])
const syncedTableText = computed(() => {
  const sync = confirmation.value?.catalog_sync as
    | { synced_table_list?: string[] }
    | null
    | undefined
  const tables = sync?.synced_table_list ?? []
  return tables.length ? tables.join('、') : '—'
})

async function reload() {
  errorMessage.value = ''
  try {
    batch.value = await loadGovernanceBatch(batchId.value)
    auditRows.value = await listGovernanceAudits()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '治理发布确认读取失败'
  }
}

async function submitEvidence() {
  busy.value = true
  errorMessage.value = ''
  notice.value = ''
  try {
    const result = await submitLineageEvidence({
      batch_id: batchId.value,
      lineage_id: evidenceForm.value.lineage_id,
      table: evidenceForm.value.table,
      field: evidenceForm.value.field,
      old_value: evidenceForm.value.old_value,
      new_value: evidenceForm.value.new_value,
      confirmed: evidenceForm.value.confirmed,
    })
    if (!result.ok) throw new Error(result.message)
    notice.value = result.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '证据登记失败'
  } finally {
    busy.value = false
  }
}

async function recover() {
  if (!confirmation.value) return
  busy.value = true
  errorMessage.value = ''
  notice.value = ''
  try {
    const result = await recoverGovernancePublish(
      confirmation.value.request_id,
      confirmation.value.revision,
    )
    if (result.conflict) {
      errorMessage.value = result.message
    } else if (!result.ok) {
      errorMessage.value = result.message
    } else {
      notice.value = result.message
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '恢复发布失败'
  } finally {
    busy.value = false
  }
}

async function retryAgain() {
  if (!confirmation.value) return
  busy.value = true
  errorMessage.value = ''
  notice.value = ''
  try {
    const result = await recoverGovernancePublish(
      confirmation.value.request_id,
      confirmation.value.revision,
    )
    notice.value = result.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '重试失败'
  } finally {
    busy.value = false
  }
}

onMounted(reload)
</script>
