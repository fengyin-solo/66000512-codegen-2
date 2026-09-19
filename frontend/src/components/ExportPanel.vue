<template>
  <div class="panel export-panel" style="margin-top:16px">
    <div class="panel-header">
      <h3>📤 分析结果导出</h3>
      <el-button text size="small" :loading="loadingRecords" @click="refreshRecords">刷新记录</el-button>
    </div>

    <!-- 没有可导出内容时的说明 -->
    <el-empty v-if="!signalStore.result" description="暂无分析结果，请先在上方选择参数并生成信号、完成分析后再导出报告" :image-size="60">
      <template #image>
        <div style="font-size:36px">🗂️</div>
      </template>
    </el-empty>

    <template v-else>
      <div class="export-form">
        <div class="label">选择报告包含的内容：</div>
        <el-checkbox-group v-model="selectedSections">
          <el-checkbox v-for="opt in EXPORT_SECTION_OPTIONS" :key="opt.value" :value="opt.value" :label="opt.label">
            {{ opt.label }}
          </el-checkbox>
        </el-checkbox-group>
        <div class="export-actions">
          <el-button type="primary" :loading="exportStore.exporting" @click="handleExport">
            导出报告文件
          </el-button>
          <span class="hint">报告为 JSON 文件，包含所选图形的后端返回数据及识别结论</span>
        </div>
      </div>
    </template>

    <div class="records-section">
      <div class="label">
        导出记录
        <span class="sub">（记录保存在后端，刷新页面后仍然保留）</span>
      </div>

      <!-- 最近一次失败/缺失的醒目提示，给出重新发起到入口 -->
      <el-alert
        v-for="rec in brokenRecords"
        :key="rec.id"
        class="broken-alert"
        :type="rec.status === 'missing' ? 'warning' : 'error'"
        :closable="false"
        show-icon
      >
        <template #title>
          <div class="broken-title">
            <span>
              {{ rec.status === 'missing'
                ? `报告文件缺失（${brokenStepText(rec)}），该文件未能正常生成或已被删除`
                : `导出失败：卡在「${brokenStepText(rec)}」步骤，报告文件未生成` }}
            </span>
            <el-button
              size="small"
              type="primary"
              plain
              :loading="exportStore.retryingId === rec.id"
              @click="handleRetry(rec)"
            >
              重新发起导出
            </el-button>
          </div>
        </template>
      </el-alert>

      <el-table v-if="exportStore.records.length" :data="exportStore.records" size="small" class="records-table">
        <el-table-column label="导出时间" min-width="170">
          <template #default="{ row }">
            <div>{{ formatTime(row.exportedAt || row.createdAt) }}</div>
            <div v-if="row.exportedAt && row.exportedAt !== row.createdAt" class="sub">
              首次创建 {{ formatTime(row.createdAt) }}
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="modulationType" label="识别结果" width="90" />
        <el-table-column label="包含范围" min-width="240">
          <template #default="{ row }">
            <el-tag v-for="name in row.includeNames" :key="name" size="small" class="scope-tag" type="info">
              {{ name }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="文件大小" width="90">
          <template #default="{ row }">{{ row.size ? formatSize(row.size) : '-' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag v-if="row.status === 'ok'" type="success" size="small">已生成</el-tag>
            <el-tag v-else-if="row.status === 'missing'" type="warning" size="small">文件缺失</el-tag>
            <el-tag v-else type="danger" size="small">导出失败</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="170">
          <template #default="{ row }">
            <el-button
              size="small"
              :disabled="row.status !== 'ok'"
              :loading="exportStore.downloadingId === row.id"
              @click="handleDownload(row)"
            >
              下载
            </el-button>
            <el-button
              size="small"
              type="primary"
              plain
              :loading="exportStore.retryingId === row.id"
              @click="handleRetry(row)"
            >
              {{ row.status === 'ok' ? '重新导出' : '重新发起' }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <div v-else-if="!loadingRecords" class="no-records">暂无导出记录</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useSignalStore } from '../store/signal'
import { useExportStore, ExportConflictError, ExportFailedError } from '../store/export'
import { EXPORT_SECTION_OPTIONS, EXPORT_STEP_LABELS, type ExportRecord, type ExportSection } from '@/types'

const signalStore = useSignalStore()
const exportStore = useExportStore()

const selectedSections = ref<ExportSection[]>([
  'spectrum', 'waterfall', 'constellation', 'modulation',
])

const loadingRecords = ref(false)
const brokenRecords = computed(() => exportStore.records.filter(r => r.status !== 'ok'))

async function refreshRecords() {
  loadingRecords.value = true
  try {
    await exportStore.fetchRecords()
  } catch {
    ElMessage.error('导出记录加载失败，请检查后端服务')
  } finally {
    loadingRecords.value = false
  }
}

async function doExport(overwrite: boolean) {
  if (!signalStore.result) {
    ElMessage.warning('暂无可导出的分析结果，请先完成一次分析')
    return
  }
  if (selectedSections.value.length === 0) {
    ElMessage.warning('请至少勾选一项要包含的内容')
    return
  }
  try {
    const rec = await exportStore.exportReport(signalStore.result, selectedSections.value, overwrite)
    ElMessage.success(`报告已导出：${rec.filename}`)
  } catch (err) {
    if (err instanceof ExportConflictError) {
      // 同一份结果重复导出：提示会覆盖已有文件
      try {
        await ElMessageBox.confirm(
          `${err.message}（导出时间：${formatTime(err.existing.exportedAt || err.existing.createdAt)}），是否继续并覆盖？`,
          '重复导出确认',
          { confirmButtonText: '覆盖并重新导出', cancelButtonText: '取消', type: 'warning' },
        )
        await doExport(true)
      } catch {
        /* 用户取消覆盖 */
      }
      return
    }
    if (err instanceof ExportFailedError) {
      ElMessage.error(err.message)
    }
  }
}

function handleExport() {
  doExport(false)
}

async function handleRetry(rec: ExportRecord) {
  try {
    const updated = await exportStore.retryExport(rec.id)
    ElMessage.success(`已重新导出：${updated.filename}`)
  } catch (err) {
    if (err instanceof ExportFailedError) ElMessage.error(err.message)
  }
}

async function handleDownload(rec: ExportRecord) {
  try {
    await exportStore.downloadRecord(rec.id)
  } catch (err) {
    if (err instanceof ExportFailedError) {
      ElMessageBox.alert(`${err.message}。可点击下方按钮重新发起导出。`, '无法下载报告', {
        confirmButtonText: '重新发起导出',
        type: 'warning',
      }).then(() => handleRetry(rec)).catch(() => {})
    }
  }
}

function brokenStepText(rec: ExportRecord): string {
  const step = rec.failedStep || rec.missingStep || ''
  return EXPORT_STEP_LABELS[step] || (step ? `步骤「${step}」` : '未知步骤')
}

function formatTime(iso?: string): string {
  if (!iso) return '-'
  return iso.replace('T', ' ')
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

onMounted(refreshRecords)
</script>

<style scoped>
.export-panel { background:#1a2332; border-radius:8px; padding:16px; border:1px solid #2a3a4a }
.panel-header { display:flex; justify-content:space-between; align-items:center; margin-bottom:8px }
.panel-header h3 { color:#90caf9; font-size:14px }
.label { font-size:12px; color:#8899aa; margin-bottom:8px }
.sub { color:#667788; font-size:11px; font-weight:normal }
.export-form { padding:12px; background:#0d1520; border-radius:8px; margin-bottom:14px }
.export-actions { display:flex; align-items:center; gap:12px; margin-top:10px }
.hint { font-size:12px; color:#667788 }
.records-section { border-top:1px solid #2a3a4a; padding-top:12px }
.records-table { width:100% }
:deep(.records-table) { background:transparent }
:deep(.records-table .el-table__cell) { background:#0d1520 }
:deep(.records-table th.el-table__cell) { background:#141e2b }
.scope-tag { margin:2px 4px 2px 0 }
.broken-alert { margin-bottom:10px }
.broken-title { display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap }
.no-records { text-align:center; color:#667788; font-size:13px; padding:20px 0 }
</style>
