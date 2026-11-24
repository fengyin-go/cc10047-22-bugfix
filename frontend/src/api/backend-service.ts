import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, EntryRow, OverviewResult, PageResult } from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

export type PublishConfirmation = {
  publish_id: string
  request_id: string
  batch_id: string | null
  status: string
  published: boolean
  publish_count: number
  attempt_count: number
  revision: number
  adopted_evidence: string
  evidence_priority: string
  audit_tables: string[]
  effective_tables: string[]
  evidence_diff: string[]
  published_tables: string[]
  synced_tables: number
  retry_tables: string[]
  next_tables_to_process: string[]
  anomalies: string[]
  audit_pending_items: string[]
  original_audit_evidence: Record<string, unknown> | null
  correction_evidence: Record<string, unknown> | null
  applied_correction: Record<string, unknown> | null
  catalog_sync: Record<string, unknown> | null
  updated_at: string
}

export type GovernanceAudit = EntryRow & {
  publish_confirmation: PublishConfirmation | null
}

export type GovernanceBatch = {
  batch_id: string
  audit: GovernanceAudit | null
  lineage: EntryRow | null
  evidence: Record<string, unknown>[]
  publishes: PublishConfirmation[]
}

export type RecoveryResult = {
  ok: boolean
  applied: boolean
  duplicate?: boolean
  conflict?: boolean
  message: string
} & Partial<PublishConfirmation>

export async function loadGovernanceBatch(batchId: string): Promise<GovernanceBatch> {
  const response = await request(`/api/governance/batches/${encodeURIComponent(batchId)}`)
  if (!response.ok) throw new Error(`治理批次 ${batchId} 读取失败`)
  return await response.json()
}

export async function listGovernanceAudits(): Promise<GovernanceAudit[]> {
  const response = await request('/api/governance/audits')
  if (!response.ok) throw new Error('审计列表读取失败')
  return (await response.json()).items
}

export async function listGovernancePublishes(
  batchId: string,
): Promise<PublishConfirmation[]> {
  const response = await request(
    `/api/governance/publishes?batch_id=${encodeURIComponent(batchId)}`,
  )
  if (!response.ok) throw new Error('治理发布确认读取失败')
  return (await response.json()).items
}

export async function loadGovernancePublish(
  requestId: string,
): Promise<PublishConfirmation> {
  const response = await request(`/api/governance/publishes/${encodeURIComponent(requestId)}`)
  if (!response.ok) throw new Error(`治理发布确认 ${requestId} 不存在`)
  return await response.json()
}

export async function submitLineageEvidence(params: {
  batch_id: string
  lineage_id: number
  table: string
  field: string
  old_value: string
  new_value: string
  confirmed: boolean
}): Promise<{ ok: boolean; message: string }> {
  const query = new URLSearchParams({
    batch_id: params.batch_id,
    lineage_id: String(params.lineage_id),
    table: params.table,
    field: params.field,
    old_value: params.old_value,
    new_value: params.new_value,
    confirmed: String(params.confirmed),
  }).toString()
  const response = await request(`/api/governance/evidence?${query}`, { method: 'POST' })
  return await response.json()
}

export async function recoverGovernancePublish(
  requestId: string,
  expectedRevision: number,
): Promise<RecoveryResult> {
  const query = new URLSearchParams({ expected_revision: String(expectedRevision) }).toString()
  const response = await request(
    `/api/governance/publishes/${encodeURIComponent(requestId)}/recover?${query}`,
    { method: 'POST' },
  )
  return await response.json()
}
