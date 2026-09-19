<template>
  <div class="panel records-panel" style="margin-top:16px">
    <div class="panel-head">
      <h3>🗂️ 导出记录</h3>
      <el-button size="small" text :loading="exportStore.loadingRecords" @click="exportStore.loadRecords()">
        刷新记录
      </el-button>
    </div>

    <el-table
      :data="exportStore.records"
      v-loading="exportStore.loadingRecords"
      empty-text="暂无导出记录，完成一次分析后可在上方“导出分析报告”"
      size="small"
      class="records-table"
    >
      <el-table-column label="导出时间" width="170">
        <template #default="{ row }">{{ formatTime(row.exportedAt) }}</template>
      </el-table-column>
      <el-table-column label="包含范围" min-width="220">
        <template #default="{ row }">
          <el-tag v-for="(l, i) in (row.sectionLabels || row.sections)" :key="i" size="small" class="sec-tag">
            {{ l }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="报告文件" min-width="170">
        <template #default="{ row }">
          <span v-if="row.fileName">{{ row.fileName }}</span>
          <span v-else class="muted">—</span>
          <el-tag v-if="row.overwritten" size="small" type="warning" class="ow-tag">已覆盖</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="220">
        <template #default="{ row }">
          <el-tag v-if="row.status === 'success'" type="success" size="small">成功</el-tag>
          <el-tag v-else-if="row.status === 'missing'" type="info" size="small">文件缺失</el-tag>
          <el-tag v-else type="danger" size="small">失败</el-tag>
          <div v-if="row.status === 'missing'" class="status-detail">
            文件不在服务器上（可能被手动删除），请重新导出
          </div>
          <div v-else-if="row.status === 'failed'" class="status-detail">
            卡在「{{ failStepLabel(row) }}」：{{ row.failMessage }}
          </div>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="200" fixed="right">
        <template #default="{ row }">
          <el-button
            v-if="row.status === 'success'" size="small" type="primary" link tag="a"
            :href="exportStore.downloadUrl(row)"
          >下载</el-button>
          <el-button size="small" type="warning" link @click="retry(row)">
            {{ row.status === 'success' ? '重新导出' : '重新发起导出' }}
          </el-button>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { useExportStore } from '../store/export'
import { useSignalStore } from '../store/signal'
import { STEP_LABELS, type ExportRecord } from '../types'

const exportStore = useExportStore()
const signalStore = useSignalStore()

const emit = defineEmits<{ openDialog: [] }>()

function formatTime(ts: number) {
  return new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false })
}

function failStepLabel(row: ExportRecord) {
  return (row as any).failStepLabel || STEP_LABELS[row.failStep ?? ''] || row.failStep || '未知步骤'
}

/**
 * 重新发起导出：
 * - 成功记录：属于"再次导出"，走对话框确认覆盖
 * - 失败/文件缺失记录：直接调重试接口；文件缺失且当前页面已不是原结果时，
 *   提示需要重新分析
 */
async function retry(row: ExportRecord) {
  const status: string = row.status
  if (status === 'success') {
    emit('openDialog')
    return
  }

  if (signalStore.result?.resultId !== row.resultId) {
    const reason = status === 'missing' ? '该报告文件已缺失' : '该次导出在生成报告文件前失败'
    try {
      await ElMessageBox.confirm(
        `${reason}，且当前页面的分析结果不是当时那一份，无法补回文件。请重新生成信号分析后再导出。`,
        status === 'missing' ? '文件缺失' : '导出失败',
        { confirmButtonText: '去重新分析', cancelButtonText: '关闭', type: 'warning' }
      )
      window.scrollTo({ top: 0, behavior: 'smooth' })
    } catch { /* 用户取消 */ }
    return
  }

  try {
    const res = await exportStore.retryExport({ record: row, currentResult: signalStore.result })
    if (res.data.ok) {
      ElMessage.success((res.data as any).repaired ? '已补全导出记录，可正常下载' : '报告已重新导出成功')
    } else {
      ElMessage.error(`导出仍失败（${res.data.failedStepLabel}）：${res.data.message}`)
    }
  } catch {
    ElMessage.error('无法连接导出服务，请稍后重试')
  }
}
</script>

<style scoped>
.panel { background:#1a2332; border-radius:8px; padding:16px; border:1px solid #2a3a4a }
.panel-head { display:flex; justify-content:space-between; align-items:center; margin-bottom:10px }
.panel h3 { color:#90caf9; font-size:14px }
.sec-tag { margin:2px 4px 2px 0 }
.ow-tag { margin-left:6px }
.muted { color:#8899aa }
.status-detail { font-size:11px; color:#ef9a9a; margin-top:4px; line-height:1.4 }
.records-table { background:transparent }
:deep(.el-table), :deep(.el-table tr), :deep(.el-table th.el-table__cell) {
  background:transparent; color:#e0e0e0; --el-table-border-color:#2a3a4a;
}
:deep(.el-table td.el-table__cell), :deep(.el-table th.is-leaf) { border-bottom:1px solid #2a3a4a }
:deep(.el-table__body tr:hover > td.el-table__cell) { background:#1f2c3d !important }
:deep(.el-table__empty-block) { background:#0d1520 }
:deep(.el-table__empty-text) { color:#8899aa; line-height:1.6 }
:deep(.el-table__body-wrapper) { scrollbar-color:#2a3a4a transparent }
</style>
