import { defineStore } from 'pinia'
import { ref } from 'vue'
import axios from 'axios'
import type { ExportRecord, ExportSectionDef, ExportSectionKey, AnalysisResult } from '@/types'

interface ExportFailureResponse {
  ok: false
  failedStep: 'validate' | 'write' | 'record'
  failedStepLabel: string
  message: string
  record: ExportRecord | null
}

interface ExportSuccessResponse {
  ok: true
  record: ExportRecord
  overwritten: boolean
  failedStep: null
}

export const useExportStore = defineStore('export', () => {
  const records = ref<ExportRecord[]>([])
  const sections = ref<ExportSectionDef[]>([])
  const exporting = ref(false)
  const loadingRecords = ref(false)

  async function loadSections() {
    if (sections.value.length) return
    const { data } = await axios.get<{ sections: ExportSectionDef[] }>('/api/exports/sections')
    sections.value = data.sections
  }

  async function loadRecords() {
    loadingRecords.value = true
    try {
      const { data } = await axios.get<{ records: ExportRecord[] }>('/api/exports')
      records.value = data.records
    } finally {
      loadingRecords.value = false
    }
  }

  /**
   * 发起导出。
   * - 409：同一份分析结果已导出过，需要用户确认覆盖后带 force 重试
   * - 400：校验阶段失败（未勾选/缺数据），不落记录
   * - 500：写报告文件或存记录阶段失败，后端已留痕并返回失败记录
   */
  async function createExport(params: {
    resultId: string
    selected: ExportSectionKey[]
    result: AnalysisResult
    force?: boolean
    forceFailStep?: 'write' | 'record'
  }): Promise<{ status: number; data: ExportSuccessResponse | ExportFailureResponse }> {
    exporting.value = true
    try {
      const { data, status } = await axios.post<ExportSuccessResponse>('/api/exports', {
        resultId: params.resultId,
        sections: params.selected,
        result: {
          spectrum: params.result.spectrum,
          waterfall: params.result.waterfall,
          constellation: params.result.constellation,
          modulation: params.result.modulation
        },
        source: params.result.meta ?? null,
        force: params.force ?? false,
        forceFailStep: params.forceFailStep ?? null
      })
      await loadRecords()
      return { status, data }
    } catch (e: any) {
      const body = e?.response?.data as ExportFailureResponse | undefined
      await loadRecords()
      if (body) return { status: e.response.status, data: body }
      // 网络层错误：视为“发起请求”阶段失败，后端未参与，无法落记录
      throw e
    } finally {
      exporting.value = false
    }
  }

  function downloadUrl(record: ExportRecord) {
    return `/api/exports/${record.id}/download`
  }

  /**
   * 从失败/缺失记录重新发起导出。
   * 若报告文件其实已生成（卡在保存记录步骤），后端只修记录；
   * 若文件缺失，需要把当前页面的分析结果数据带回重新写文件。
   */
  async function retryExport(params: {
    record: ExportRecord
    currentResult: AnalysisResult | null
  }): Promise<{ status: number; data: ExportSuccessResponse | ExportFailureResponse }> {
    exporting.value = true
    const { record, currentResult } = params
    try {
      const sameResult = currentResult?.resultId === record.resultId
      const { data, status } = await axios.post<ExportSuccessResponse>(
        `/api/exports/${record.id}/retry`,
        {
          // 只有同一份分析结果还在当前页面时才能带回数据
          result: sameResult ? {
            spectrum: currentResult.spectrum,
            waterfall: currentResult.waterfall,
            constellation: currentResult.constellation,
            modulation: currentResult.modulation
          } : null,
          sections: record.sections,
          source: sameResult ? (currentResult.meta ?? null) : null
        }
      )
      await loadRecords()
      return { status, data }
    } catch (e: any) {
      const body = e?.response?.data as ExportFailureResponse | undefined
      await loadRecords()
      if (body) return { status: e.response.status, data: body }
      throw e
    } finally {
      exporting.value = false
    }
  }

  /** 判断该结果（按 resultId）是否已有成功导出的文件，用于覆盖提醒 */
  function findByResult(resultId: string): ExportRecord | undefined {
    return records.value.find(r => r.resultId === resultId)
  }

  return {
    records, sections, exporting, loadingRecords,
    loadSections, loadRecords, createExport, retryExport, downloadUrl, findByResult
  }
})
