export interface SignalData {
  i: number[]
  q: number[]
  sampleRate: number
  centerFreq: number
}

export interface SpectrumData {
  frequencies: number[]
  magnitudes: number[]
}

export interface WaterfallRow {
  time: number
  values: number[]
}

export interface ConstellationPoint {
  i: number
  q: number
}

export interface ModulationResult {
  type: string
  confidence: number
  candidates: { type: string; score: number }[]
  symbolRate: number | null
  frequencyOffset: number | null
}

export interface AnalysisMeta {
  modulation: string
  samples: number
  snr: number
}

export interface AnalysisResult {
  resultId: string
  meta?: AnalysisMeta
  spectrum: SpectrumData
  waterfall: WaterfallRow[]
  constellation: ConstellationPoint[]
  modulation: ModulationResult
}

export const MODULATION_TYPES = ['AM', 'FM', 'BPSK', 'QPSK', '16QAM']

// ---------- 导出功能类型 ----------

export type ExportSectionKey = 'spectrum' | 'waterfall' | 'constellation' | 'modulation'

export interface ExportSectionDef {
  key: ExportSectionKey
  label: string
}

export type ExportStatus = 'success' | 'failed' | 'missing'

export interface ExportRecord {
  id: string
  resultId: string
  sections: ExportSectionKey[]
  sectionLabels?: string[]
  fileName: string | null
  exportedAt: number
  createdAt?: number
  status: ExportStatus
  lastStatus: 'success' | 'failed'
  failStep: 'validate' | 'write' | 'record' | null
  failStepLabel?: string
  failMessage: string | null
  overwritten: boolean
  source?: AnalysisMeta
}

export const STEP_LABELS: Record<string, string> = {
  validate: '校验导出内容',
  write: '写入报告文件',
  record: '保存导出记录'
}
