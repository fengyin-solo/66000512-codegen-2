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

export interface AnalysisResult {
  spectrum: SpectrumData
  waterfall: WaterfallRow[]
  constellation: ConstellationPoint[]
  modulation: ModulationResult
}

export type ExportSection = 'spectrum' | 'waterfall' | 'constellation' | 'modulation'

export type ExportStatus = 'ok' | 'failed' | 'missing'

export interface ExportRecord {
  id: string
  hash: string
  filename: string
  modulationType: string
  include: ExportSection[]
  includeNames: string[]
  status: ExportStatus
  createdAt: string
  exportedAt?: string
  size?: number
  failedStep?: string
  missingStep?: string
  failStepLabel?: string
}

export const EXPORT_SECTION_OPTIONS: { value: ExportSection; label: string }[] = [
  { value: 'spectrum', label: 'FFT频谱数据' },
  { value: 'waterfall', label: '瀑布图数据' },
  { value: 'constellation', label: '星座图数据' },
  { value: 'modulation', label: '调制识别结论' },
]

export const EXPORT_STEP_LABELS: Record<string, string> = {
  build: '组装报告内容',
  write: '写入报告文件',
  record: '保存导出记录',
}

export const MODULATION_TYPES = ['AM', 'FM', 'BPSK', 'QPSK', '16QAM']