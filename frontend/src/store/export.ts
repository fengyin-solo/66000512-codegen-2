import { defineStore } from 'pinia'
import { ref } from 'vue'
import axios, { AxiosError } from 'axios'
import type { AnalysisResult, ExportRecord, ExportSection } from '@/types'

export class ExportConflictError extends Error {
  existing: ExportRecord
  constructor(message: string, existing: ExportRecord) {
    super(message)
    this.existing = existing
  }
}

export class ExportFailedError extends Error {
  record?: ExportRecord
  constructor(message: string, record?: ExportRecord) {
    super(message)
    this.record = record
  }
}

function extractErrorData(err: unknown): { message?: string; record?: ExportRecord; existing?: ExportRecord } {
  const axErr = err as AxiosError<any>
  const detail = axErr?.response?.data?.detail
  if (detail && typeof detail === 'object') return detail
  if (typeof detail === 'string') return { message: detail }
  return { message: '导出请求失败，请稍后重试' }
}

export const useExportStore = defineStore('export', () => {
  const records = ref<ExportRecord[]>([])
  const exporting = ref(false)
  const downloadingId = ref<string | null>(null)
  const retryingId = ref<string | null>(null)
  const loaded = ref(false)

  async function fetchRecords() {
    const { data } = await axios.get<ExportRecord[]>('/api/exports')
    records.value = data
    loaded.value = true
  }

  async function exportReport(
    result: AnalysisResult,
    include: ExportSection[],
    overwrite = false,
  ): Promise<ExportRecord> {
    exporting.value = true
    try {
      const { data } = await axios.post<ExportRecord>('/api/exports', { result, include, overwrite })
      await fetchRecords()
      return data
    } catch (err) {
      const info = extractErrorData(err)
      const status = (err as AxiosError)?.response?.status
      if (status === 409 && info.existing) {
        throw new ExportConflictError(info.message || '该结果已导出过，重复导出将覆盖已有文件', info.existing)
      }
      await fetchRecords()
      throw new ExportFailedError(info.message || '导出失败，请重试', info.record)
    } finally {
      exporting.value = false
    }
  }

  async function retryExport(id: string): Promise<ExportRecord> {
    retryingId.value = id
    try {
      const { data } = await axios.post<ExportRecord>(`/api/exports/${id}/retry`)
      await fetchRecords()
      return data
    } catch (err) {
      const info = extractErrorData(err)
      await fetchRecords()
      throw new ExportFailedError(info.message || '重新导出失败，请重试', info.record)
    } finally {
      retryingId.value = null
    }
  }

  async function downloadRecord(id: string) {
    downloadingId.value = id
    try {
      const resp = await axios.get(`/api/exports/${id}/download`, { responseType: 'blob' })
      const disposition = resp.headers['content-disposition'] as string | undefined
      let filename = 'report.json'
      if (disposition) {
        const match = disposition.match(/filename\*?=(?:UTF-8'')?["']?([^;"']+)/i)
        if (match) filename = decodeURIComponent(match[1])
      }
      const url = URL.createObjectURL(resp.data)
      const a = document.createElement('a')
      a.href = url
      a.download = filename
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
    } catch (err) {
      // 410 文件缺失时后端返回 JSON，blob 模式下需解析
      const resp = (err as AxiosError<Blob>)?.response
      if (resp?.data instanceof Blob && resp.data.type.includes('json')) {
        const text = await resp.data.text()
        try {
          const detail = JSON.parse(text)?.detail
          if (detail) throw new ExportFailedError(detail)
        } catch (e) {
          if (e instanceof ExportFailedError) {
            await fetchRecords()
            throw e
          }
        }
      }
      await fetchRecords()
      throw new ExportFailedError('下载失败，报告文件可能已缺失，请重新导出')
    } finally {
      downloadingId.value = null
    }
  }

  return {
    records,
    exporting,
    downloadingId,
    retryingId,
    loaded,
    fetchRecords,
    exportReport,
    retryExport,
    downloadRecord,
  }
})
