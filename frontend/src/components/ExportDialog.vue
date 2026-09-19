<template>
  <el-dialog
    :model-value="modelValue"
    title="📤 导出分析报告"
    width="520px"
    :close-on-click-modal="false"
    @update:model-value="onDialogToggle"
    @open="onOpen"
  >
    <div v-if="!result" class="empty-tip">
      <el-icon><Warning /></el-icon>
      <span>当前没有可导出的分析结果，请先生成信号并完成分析。</span>
    </div>

    <template v-else>
      <div class="result-meta" v-if="result.meta">
        <el-tag size="small" type="info">{{ result.meta.modulation }}</el-tag>
        <span>样本数 {{ result.meta.samples }}</span>
        <span>SNR {{ result.meta.snr }} dB</span>
      </div>

      <div class="section-title">选择报告包含内容（后端返回数据与识别结论）</div>
      <el-checkbox-group v-model="selected" class="section-options">
        <el-checkbox
          v-for="s in exportStore.sections"
          :key="s.key"
          :value="s.key"
          :disabled="!sectionAvailable(s.key)"
        >
          {{ s.label }}
          <span v-if="!sectionAvailable(s.key)" class="unavailable">（本次分析无数据）</span>
        </el-checkbox>
      </el-checkbox-group>
      <div v-if="!selected.length" class="select-hint">请至少勾选一项后再导出</div>

      <!-- 失败提示：指出卡在第几步，并给出重新发起入口 -->
      <el-alert
        v-if="failure"
        class="fail-alert"
        type="error"
        :closable="false"
        show-icon
      >
        <template #title>
          <div>导出失败，卡在「{{ failure.failedStepLabel }}」步骤</div>
        </template>
        <div class="fail-msg">{{ failure.message }}</div>
        <el-button
          size="small" type="danger" plain class="retry-btn"
          :loading="exportStore.exporting" :disabled="!selected.length"
          @click="doExport(true)"
        >
          重新发起导出
        </el-button>
      </el-alert>
    </template>

    <template #footer>
      <el-button @click="emit('update:modelValue', false)">关闭</el-button>
      <el-button
        v-if="result"
        type="primary" :loading="exportStore.exporting"
        :disabled="!selected.length"
        @click="doExport(false)"
      >
        导出报告
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessageBox, ElMessage } from 'element-plus'
import { Warning } from '@element-plus/icons-vue'
import { useExportStore } from '../store/export'
import { STEP_LABELS, type AnalysisResult, type ExportSectionKey } from '../types'

const props = defineProps<{ modelValue: boolean; result: AnalysisResult | null }>()
const emit = defineEmits<{ 'update:modelValue': [boolean] }>()
const exportStore = useExportStore()

const selected = ref<ExportSectionKey[]>(['spectrum', 'waterfall', 'constellation', 'modulation'])
const failure = ref<{ failedStepLabel: string; message: string } | null>(null)

function onDialogToggle(v: boolean) {
  if (!v) emit('update:modelValue', false)
}

function onOpen() {
  failure.value = null
  // 默认全选，但自动剔除本次分析没有数据的板块
  selected.value = (['spectrum', 'waterfall', 'constellation', 'modulation'] as ExportSectionKey[])
    .filter(k => sectionAvailable(k))
  exportStore.loadSections()
}

function sectionAvailable(key: ExportSectionKey): boolean {
  if (!props.result) return false
  const data = props.result[key]
  return Array.isArray(data) ? data.length > 0 : !!data
}

async function doExport(force: boolean) {
  if (!props.result || exportStore.exporting) return
  if (!selected.value.length) {
    ElMessage.warning('请至少勾选一项导出内容')
    return
  }

  // 同一份分析结果重复导出：先提示会覆盖已有文件
  if (!force) {
    const prev = exportStore.findByResult(props.result.resultId)
    if (prev && prev.status === 'success') {
      try {
        await ElMessageBox.confirm(
          `该分析结果已于 ${new Date(prev.exportedAt).toLocaleString()} 导出过，再次导出将覆盖已有文件 ${prev.fileName ?? ''}，是否继续？`,
          '覆盖已有导出文件',
          { confirmButtonText: '覆盖导出', cancelButtonText: '取消', type: 'warning' }
        )
      } catch {
        return
      }
    }
  }

  const res = await exportStore.createExport({
    resultId: props.result.resultId,
    selected: selected.value,
    result: props.result,
    force
  })

  if (res.data.ok) {
    failure.value = null
    ElMessage.success(res.data.overwritten ? '报告已重新导出，原文件已覆盖' : '报告导出成功')
    emit('update:modelValue', false)
  } else {
    const d = res.data
    failure.value = {
      failedStepLabel: d.failedStepLabel || STEP_LABELS[d.failedStep] || d.failedStep,
      message: d.message
    }
    if (res.status === 409) {
      // 覆盖冲突：弹确认后自动带 force 重试
      try {
        await ElMessageBox.confirm(d.message, '覆盖已有导出文件', {
          confirmButtonText: '覆盖导出', cancelButtonText: '取消', type: 'warning'
        })
        doExport(true)
      } catch { /* 用户取消，保留失败提示与重试入口 */ }
    }
  }
}
</script>

<style scoped>
.empty-tip { display:flex; align-items:center; gap:8px; color:#ffa726; font-size:13px; padding:12px 0 }
.result-meta { display:flex; align-items:center; gap:10px; font-size:12px; color:#8899aa; margin-bottom:14px }
.section-title { font-size:13px; color:#90caf9; margin-bottom:10px }
.section-options { display:flex; flex-direction:column; gap:10px }
.unavailable { color:#8899aa; font-size:12px }
.select-hint { color:#ffa726; font-size:12px; margin-top:8px }
.fail-alert { margin-top:14px }
.fail-msg { font-size:12px; margin:6px 0 8px; opacity:.9 }
.retry-btn { margin-top:2px }
</style>
