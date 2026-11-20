<template>
  <section class="page" data-module="hierarchy_health">
    <header class="page-head">
      <div>
        <h2>层级体检</h2>
        <p class="page-desc">月度地区等级结账前，接收两期维度数据及父子连接，诊断父级丢失、代码错位与层级闭环，跟踪受牵连叶子、结账差异与边更正重算。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">新建层级体检</button>
        <button class="btn" type="button" @click="loadDemo">载入演示数据</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <h3 class="section-title">体检批次</h3>
    <table class="data-table">
      <thead>
        <tr>
          <th>批次编号</th>
          <th>批次名称</th>
          <th>账期（当期/上期）</th>
          <th>闭环</th>
          <th>断链</th>
          <th>错位</th>
          <th>受牵连叶子（去重）</th>
          <th>挂起子树</th>
          <th>批次状态</th>
          <th>创建时间</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in batches" :key="row.batch_id" :class="{ active: current?.batch_id === row.batch_id }">
          <td>{{ row.batch_id }}</td>
          <td>{{ row.name }}</td>
          <td>{{ row.period_current }} / {{ row.period_previous }}</td>
          <td>{{ row.report.summary.cycle_count }}</td>
          <td>{{ row.report.summary.missing_parent_count }}</td>
          <td>{{ row.report.summary.misplaced_count }}</td>
          <td>{{ row.report.summary.impacted_leaf_count }}（{{ row.report.summary.impacted_leaf_codes.join('、') || '—' }}）</td>
          <td>{{ row.report.summary.pending_subtree_count }}</td>
          <td><span class="tag" :class="row.status === '体检通过' ? 'tag-ok' : 'tag-bad'">{{ row.status }}</span></td>
          <td>{{ row.created_at }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="selectBatch(row.batch_id)">查看报告</button>
          </td>
        </tr>
        <tr v-if="!batches.length">
          <td colspan="11" class="empty-state">暂无体检批次，点击「新建层级体检」或「载入演示数据」开始</td>
        </tr>
      </tbody>
    </table>

    <template v-if="current">
      <h3 class="section-title">问题组明细（{{ current.batch_id }}）</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>问题组</th>
            <th>类型</th>
            <th>问题根节点</th>
            <th>环上/断链边依据</th>
            <th>受牵连叶子</th>
            <th>叶子原始上报路径</th>
            <th>影响数量</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="group in current.report.groups" :key="group.group_id">
            <td>{{ group.group_id }}</td>
            <td><span class="tag" :class="kindClass(group.kind)">{{ group.kind }}</span></td>
            <td>{{ group.roots.join('、') }}</td>
            <td>
              <span v-for="(edge, idx) in group.evidence" :key="idx" class="edge-chip">
                {{ edge.parent }} → {{ edge.child }}
              </span>
            </td>
            <td>{{ group.leaf_codes.join('、') || '—' }}</td>
            <td>
              <div v-for="leaf in group.affected_leaves" :key="leaf.leaf_code" class="path-line">
                <strong>{{ leaf.leaf_code }}</strong>：{{ leaf.report_path.join(' → ') }}
              </div>
              <span v-if="!group.affected_leaves.length">—</span>
            </td>
            <td>{{ group.impact_count }}</td>
          </tr>
        </tbody>
      </table>

      <div class="two-col">
        <div>
          <h3 class="section-title">待定子树（计入影响结账覆盖）</h3>
          <p class="hint">仍挂起节点：{{ current.report.summary.pending_subtree_nodes.join('、') || '无' }}</p>
        </div>
        <div>
          <h3 class="section-title">两期变动留痕</h3>
          <p class="hint">
            新增节点 {{ current.report.period_change.nodes_added.length }} 个 ·
            移除节点 {{ current.report.period_change.nodes_removed.length }} 个 ·
            新增边 {{ current.report.period_change.edges_added.length }} 条 ·
            移除边 {{ current.report.period_change.edges_removed.length }} 条
          </p>
        </div>
      </div>

      <h3 class="section-title">结账差异审计（按叶子编号去重）</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>记录编号</th>
            <th>叶子编号</th>
            <th>操作类型</th>
            <th>影响类型</th>
            <th>关联问题组</th>
            <th>关联父子边</th>
            <th>上报路径</th>
            <th>状态</th>
            <th>操作时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="audit in audits" :key="audit.记录编号">
            <td>{{ audit.记录编号 }}</td>
            <td>{{ audit.leaf_code || '—' }}</td>
            <td>{{ audit.操作类型 }}</td>
            <td>{{ audit.影响类型 }}</td>
            <td>{{ (audit.关联问题组 || []).join('；') || '—' }}</td>
            <td>
              <span v-for="(edge, idx) in audit.关联边" :key="idx" class="edge-chip">{{ edge.parent }} → {{ edge.child }}</span>
              <span v-if="!audit.关联边?.length">—</span>
            </td>
            <td>
              <div v-for="(path, idx) in audit.上报路径" :key="idx" class="path-line">{{ path.join(' → ') }}</div>
              <span v-if="!audit.上报路径?.length">—</span>
            </td>
            <td><span class="tag" :class="audit.status === '已核验' ? 'tag-ok' : 'tag-wait'">{{ audit.status }}</span></td>
            <td>{{ audit.操作时间 }}</td>
          </tr>
        </tbody>
      </table>

      <h3 class="section-title">父子边更正（替代上级迟到时补登记后重算全图）</h3>
      <form class="filter-bar" @submit.prevent="submitCorrection">
        <label class="filter-item"><span>原父级</span><input v-model="correction.old_parent" placeholder="如 G" required /></label>
        <label class="filter-item"><span>子级</span><input v-model="correction.child" placeholder="如 M" required /></label>
        <label class="filter-item"><span>替代上级</span><input v-model="correction.new_parent" placeholder="如 H" required /></label>
        <label class="filter-item"><span>补登记节点（逗号分隔）</span><input v-model="correction.new_nodes_text" placeholder="如 H" /></label>
        <button class="btn primary" type="submit">更正并重算</button>
      </form>
      <table v-if="current.corrections.length" class="data-table">
        <thead>
          <tr>
            <th>时间</th>
            <th>操作人</th>
            <th>旧边</th>
            <th>新边</th>
            <th>已解挂叶子</th>
            <th>没被修改的叶子</th>
            <th>重算后挂起子树</th>
            <th>备注</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(item, idx) in current.corrections" :key="idx">
            <td>{{ item.at }}</td>
            <td>{{ item.operator }}</td>
            <td><span class="edge-chip">{{ item.old_edge.parent }} → {{ item.old_edge.child }}</span></td>
            <td><span class="edge-chip">{{ item.new_edge.parent }} → {{ item.new_edge.child }}</span></td>
            <td>{{ item.released_leaves.join('、') || '—' }}</td>
            <td>{{ item.unmodified_leaves.join('、') || '—' }}</td>
            <td>{{ item.hanging_subtree.join('、') || '无' }}</td>
            <td>{{ item.note || '—' }}</td>
          </tr>
        </tbody>
      </table>
    </template>

    <footer class="page-foot">
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      <span v-else>共 {{ batches.length }} 个体检批次</span>
    </footer>

    <Teleport to="body">
      <div v-if="showCreateModal" class="modal-overlay" @click.self="showCreateModal = false">
        <div class="modal-card wide">
          <header class="modal-head">
            <h3>新建层级体检</h3>
            <button class="btn ghost" type="button" @click="showCreateModal = false">&times;</button>
          </header>
          <form class="modal-body" @submit.prevent="submitCreate">
            <div class="two-col">
              <label class="modal-field"><span>批次名称</span><input v-model="form.name" placeholder="如 11月地区等级结账前层级体检" required /></label>
              <label class="modal-field"><span>操作人</span><input v-model="form.operator" placeholder="如 结账值班员" required /></label>
              <label class="modal-field"><span>当期账期</span><input v-model="form.period_current" placeholder="如 2026-11" /></label>
              <label class="modal-field"><span>上期账期</span><input v-model="form.period_previous" placeholder="如 2026-10" /></label>
            </div>
            <label class="modal-field">
              <span>当期维度（nodes 一行一个编号；edges 每行 父,子）</span>
              <textarea v-model="form.current_text" rows="7" class="code-input" placeholder="S&#10;A&#10;S,S"></textarea>
            </label>
            <label class="modal-field">
              <span>上期维度（同上，可留空）</span>
              <textarea v-model="form.previous_text" rows="5" class="code-input"></textarea>
            </label>
            <footer class="modal-foot">
              <button class="btn" type="button" @click="showCreateModal = false">取消</button>
              <button class="btn primary" type="submit">开始体检</button>
            </footer>
          </form>
        </div>
      </div>
    </Teleport>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

interface Edge { parent: string; child: string }
interface LeafPath { leaf_code: string; report_path: string[] }
interface IssueGroup {
  group_id: string
  kind: string
  title: string
  roots: string[]
  evidence: Edge[]
  leaf_codes: string[]
  affected_leaves: LeafPath[]
  impact_count: number
}
interface BatchSummary {
  cycle_count: number
  missing_parent_count: number
  misplaced_count: number
  pending_subtree_nodes: string[]
  pending_subtree_count: number
  impacted_leaf_codes: string[]
  impacted_leaf_count: number
}
interface Correction {
  at: string
  operator: string
  note: string
  old_edge: Edge
  new_edge: Edge
  released_leaves: string[]
  unmodified_leaves: string[]
  hanging_subtree: string[]
}
interface Batch {
  batch_id: string
  name: string
  period_current: string
  period_previous: string
  status: string
  created_at: string
  corrections: Correction[]
  report: {
    groups: IssueGroup[]
    summary: BatchSummary
    period_change: {
      nodes_added: string[]
      nodes_removed: string[]
      edges_added: Edge[]
      edges_removed: Edge[]
    }
  }
}
interface AuditRow {
  记录编号: string
  leaf_code: string
  操作类型: string
  影响类型: string
  关联问题组: string[]
  关联边: Edge[]
  上报路径: string[][]
  status: string
  操作时间: string
}

const ENDPOINT = '/api/hierarchy_health'

const batches = ref<Batch[]>([])
const current = ref<Batch | null>(null)
const audits = ref<AuditRow[]>([])
const errorMessage = ref('')
const showCreateModal = ref(false)
const stats = ref([
  { label: '体检批次', value: 0 },
  { label: '层级闭环', value: 0 },
  { label: '父级丢失', value: 0 },
  { label: '代码错位', value: 0 },
  { label: '待核验差异', value: 0 },
])

const emptyForm = () => ({
  name: '',
  operator: '',
  period_current: '2026-11',
  period_previous: '2026-10',
  current_text: '',
  previous_text: '',
})
const form = ref(emptyForm())

const correction = ref({ old_parent: '', child: '', new_parent: '', new_nodes_text: '' })

const DEMO_CURRENT = {
  nodes: ['S', 'A', 'B', 'LA', 'M', 'L1', 'L2', 'N', 'P0', 'R'],
  edges: [
    { parent: 'S', child: 'S' },
    { parent: 'A', child: 'B' },
    { parent: 'B', child: 'A' },
    { parent: 'A', child: 'LA' },
    { parent: 'G', child: 'M' },
    { parent: 'M', child: 'L1' },
    { parent: 'M', child: 'L2' },
    { parent: 'R', child: 'N' },
  ],
}
const DEMO_PREVIOUS = {
  nodes: ['A', 'B', 'LA', 'M', 'L1', 'L2', 'N', 'P0', 'R'],
  edges: [
    { parent: 'P0', child: 'N' },
    { parent: 'A', child: 'LA' },
    { parent: 'M', child: 'L1' },
    { parent: 'M', child: 'L2' },
  ],
}

function parsePeriod(text: string): { nodes: string[]; edges: Edge[] } {
  const nodes = new Set<string>()
  const edges: Edge[] = []
  for (const rawLine of text.split('\n')) {
    const line = rawLine.trim()
    if (!line) continue
    if (line.includes(',')) {
      const [parent, child] = line.split(',').map((part) => part.trim())
      if (parent && child) {
        edges.push({ parent, child })
        nodes.add(parent)
        nodes.add(child)
      }
    } else {
      nodes.add(line)
    }
  }
  return { nodes: [...nodes], edges }
}

function periodToText(period: { nodes: string[]; edges: Edge[] } | null): string {
  if (!period) return ''
  const edgeLines = period.edges.map((edge) => `${edge.parent},${edge.child}`)
  return [...period.nodes, ...edgeLines].join('\n')
}

function kindClass(kind: string): string {
  if (kind === '层级闭环') return 'tag-bad'
  if (kind === '父级丢失') return 'tag-wait'
  return 'tag-info'
}

function openCreate() {
  errorMessage.value = ''
  form.value = emptyForm()
  showCreateModal.value = true
}

function loadDemo() {
  form.value = {
    name: '11月地区等级结账前层级体检（演示）',
    operator: '结账值班员',
    period_current: '2026-11',
    period_previous: '2026-10',
    current_text: periodToText(DEMO_CURRENT),
    previous_text: periodToText(DEMO_PREVIOUS),
  }
  showCreateModal.value = true
}

async function submitCreate() {
  errorMessage.value = ''
  try {
    const payload = {
      name: form.value.name,
      operator: form.value.operator,
      period_current: form.value.period_current,
      period_previous: form.value.period_previous,
      current: parsePeriod(form.value.current_text),
      previous: parsePeriod(form.value.previous_text),
    }
    if (!payload.current.nodes.length) {
      throw new Error('当期维度不能为空')
    }
    const response = await request(`${ENDPOINT}/batches`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
    const data = await response.json()
    if (!response.ok || !data.ok) {
      throw new Error(data.message || '体检失败')
    }
    showCreateModal.value = false
    await reload()
    await selectBatch(data.entry.batch_id)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '体检失败'
  }
}

async function submitCorrection() {
  if (!current.value) return
  errorMessage.value = ''
  try {
    const payload = {
      old_parent: correction.value.old_parent.trim(),
      child: correction.value.child.trim(),
      new_parent: correction.value.new_parent.trim(),
      new_nodes: correction.value.new_nodes_text
        .split(',')
        .map((item) => item.trim())
        .filter(Boolean),
      operator: form.value.operator || '结账值班员',
      note: '前端提交的父子边更正',
    }
    const response = await request(`${ENDPOINT}/batches/${current.value.batch_id}/corrections`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
    const data = await response.json()
    if (!response.ok || !data.ok) {
      throw new Error(data.message || '更正失败')
    }
    correction.value = { old_parent: '', child: '', new_parent: '', new_nodes_text: '' }
    await reload()
    await selectBatch(current.value.batch_id)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '更正失败'
  }
}

async function selectBatch(batchId: string) {
  errorMessage.value = ''
  try {
    const [batchResp, auditResp] = await Promise.all([
      request(`${ENDPOINT}/batches/${batchId}`),
      request(`${ENDPOINT}/audits?batch_id=${batchId}&size=200`),
    ])
    if (!batchResp.ok || !auditResp.ok) {
      throw new Error('体检报告读取失败')
    }
    current.value = await batchResp.json()
    audits.value = (await auditResp.json()).items ?? []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '体检报告读取失败'
  }
}

async function reload() {
  try {
    const response = await request(`${ENDPOINT}/batches?size=100`)
    if (!response.ok) {
      throw new Error('体检批次列表读取失败')
    }
    const payload = await response.json()
    batches.value = payload.items ?? []
    const latest = batches.value[0]
    stats.value[0].value = batches.value.length
    stats.value[1].value = batches.value.reduce((sum, item) => sum + Number(item.report.summary.cycle_count), 0)
    stats.value[2].value = batches.value.reduce((sum, item) => sum + Number(item.report.summary.missing_parent_count), 0)
    stats.value[3].value = batches.value.reduce((sum, item) => sum + Number(item.report.summary.misplaced_count), 0)
    stats.value[4].value = latest ? Number(latest.report.summary.impacted_leaf_count) : 0
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '体检批次列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.section-title {
  margin: 22px 0 10px;
  font-size: 15px;
}
.tag {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 12px;
  white-space: nowrap;
}
.tag-bad { background: #fde8e8; color: #c0392b; }
.tag-wait { background: #fef3e0; color: #b9770e; }
.tag-ok { background: #e6f6ec; color: #1e8449; }
.tag-info { background: #e8f0fe; color: #1a5fb4; }
.edge-chip {
  display: inline-block;
  margin: 1px 4px 1px 0;
  padding: 1px 7px;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 12px;
  background: #fafafa;
  white-space: nowrap;
}
.path-line { font-size: 12px; line-height: 1.7; color: #444; }
.hint { font-size: 13px; color: var(--muted); }
.two-col {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 18px;
}
tr.active { background: #f4f8ff; }
.modal-card.wide { width: 640px; }
.code-input {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
  resize: vertical;
}
</style>
