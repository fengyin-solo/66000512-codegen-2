<template>
  <div class="app-container">
    <header class="app-header">
      <h1>📡 射频信号频谱分析与调制识别仪</h1>
      <p class="subtitle">FFT频谱 · 瀑布图 · 星座图 | AM/FM/BPSK/QPSK/16QAM</p>
    </header>
    <main class="app-main">
      <div class="control-card">
        <el-form :model="form" inline>
          <el-form-item label="调制方式">
            <el-select v-model="form.modulation">
              <el-option v-for="m in ['AM','FM','BPSK','QPSK','16QAM']" :key="m" :label="m" :value="m" />
            </el-select>
          </el-form-item>
          <el-form-item label="样本数">
            <el-input-number v-model="form.samples" :min="256" :max="8192" :step="256" />
          </el-form-item>
          <el-form-item label="SNR(dB)">
            <el-input-number v-model="form.snr" :min="0" :max="40" :step="5" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="generate" :loading="store.loading">🔍 生成信号并分析</el-button>
          </el-form-item>
        </el-form>
      </div>

      <div v-if="store.result" class="results-grid">
        <SpectrumPlot />
        <ConstellationPlot />
      </div>
      <WaterfallPlot v-if="store.result" />
      <ModulationResult v-if="store.result" />
      <ExportPanel />
    </main>
  </div>
</template>

<script setup lang="ts">
import { reactive } from 'vue'
import SpectrumPlot from './components/SpectrumPlot.vue'
import ConstellationPlot from './components/ConstellationPlot.vue'
import WaterfallPlot from './components/WaterfallPlot.vue'
import ModulationResult from './components/ModulationResult.vue'
import ExportPanel from './components/ExportPanel.vue'
import { useSignalStore } from './store/signal'
const store = useSignalStore()
const form = reactive({ modulation: 'QPSK', samples: 1024, snr: 20 })
function generate() { store.analyze({ ...form }) }
</script>

<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:system-ui,sans-serif;background:#0f1923;color:#e0e0e0}
.app-container{min-height:100vh}
.app-header{background:linear-gradient(135deg,#1a2332,#2d3e50);padding:20px 40px;border-bottom:1px solid #2a3a4a}
.app-header h1{font-size:1.5rem;color:#64b5f6}
.subtitle{opacity:.7;margin-top:4px;font-size:.85rem}
.app-main{padding:16px 40px}
.control-card{background:#1a2332;border-radius:8px;padding:16px 20px;margin-bottom:16px;border:1px solid #2a3a4a}
.results-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
</style>