import math
import os
import random
import uuid

import numpy as np
from fastapi import FastAPI, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .exports import EXPORT_SECTIONS, ExportError, ExportManager, SECTION_LABELS, SIMULATABLE_STEPS, STEP_LABELS

app = FastAPI(title="RF Signal Analyzer")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

MODULATION_TYPES = ["AM", "FM", "BPSK", "QPSK", "16QAM"]

EXPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "exports")
export_manager = ExportManager(EXPORT_DIR)


@app.exception_handler(ExportError)
def export_error_handler(request: Request, exc: ExportError):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "ok": False,
            "failedStep": exc.step,
            "failedStepLabel": STEP_LABELS.get(exc.step, exc.step),
            "message": exc.message,
        },
    )


class GenerateRequest(BaseModel):
    modulation: str = "QPSK"
    samples: int = 1024
    snr: float = 20.0


class ExportRequest(BaseModel):
    resultId: str
    sections: list[str]
    result: dict
    source: dict | None = None
    force: bool = False
    # 联调/测试用：人为让某一步失败，验证失败提示与重试入口
    forceFailStep: str | None = None


class ExportRetryRequest(BaseModel):
    result: dict | None = None
    sections: list[str] | None = None
    source: dict | None = None
    forceFailStep: str | None = None


def generate_signal(mod: str, samples: int, snr: float) -> np.ndarray:
    """Generate IQ samples for given modulation"""
    t = np.arange(samples) / samples * 10  # time vector
    i, q = np.zeros(samples), np.zeros(samples)
    noise_scale = 10 ** (-snr / 20) * 0.5

    if mod == "AM":
        i = 0.7 * (1 + 0.5 * np.sin(2 * np.pi * 1.5 * t)) * np.cos(2 * np.pi * 5 * t)
        q = np.zeros(samples)
    elif mod == "FM":
        msg = np.sin(2 * np.pi * 1.2 * t)
        phase = np.cumsum(2 * np.pi * (5 + 3 * msg) / samples * 10)
        i = np.cos(phase) * 0.7
        q = np.sin(phase) * 0.7
    elif mod == "BPSK":
        symbols = np.sign(np.random.randn(samples // 16 + 1))
        symbols_upsampled = np.repeat(symbols, 16)[:samples]
        i = symbols_upsampled * np.cos(2 * np.pi * 5 * t) * 0.7
        q = np.zeros(samples)
    elif mod == "QPSK":
        sym_i = np.sign(np.random.randn(samples // 16 + 1))
        sym_q = np.sign(np.random.randn(samples // 16 + 1))
        si = np.repeat(sym_i, 16)[:samples]
        sq = np.repeat(sym_q, 16)[:samples]
        i = si * 0.5
        q = sq * 0.5
    elif mod == "16QAM":
        levels = np.array([-3, -1, 1, 3]) * 0.25
        sym_i = np.random.choice(levels, samples // 16 + 1)
        sym_q = np.random.choice(levels, samples // 16 + 1)
        i = np.repeat(sym_i, 16)[:samples]
        q = np.repeat(sym_q, 16)[:samples]
    else:
        i = np.cos(2 * np.pi * 5 * t) * 0.7
        q = np.sin(2 * np.pi * 5 * t) * 0.7

    # Add noise
    i += np.random.randn(samples) * noise_scale
    q += np.random.randn(samples) * noise_scale

    return i, q


def compute_fft(i: np.ndarray, q: np.ndarray, fs: float = 1000.0):
    """Compute FFT magnitude spectrum in dB"""
    iq = i + 1j * q
    n = len(iq)
    fft = np.fft.fftshift(np.fft.fft(iq))
    mag = np.abs(fft) / n
    mag_db = 20 * np.log10(mag + 1e-10)
    freqs = np.fft.fftshift(np.fft.fftfreq(n, 1/fs))
    return freqs.tolist(), mag_db.tolist()


def compute_waterfall(i: np.ndarray, q: np.ndarray, fs: float = 1000.0, rows: int = 40):
    """Compute spectrogram waterfall"""
    n = len(i)
    # 样本较少时自适应减少行数，保证每段至少 8 个采样点
    rows = min(rows, max(1, n // 8))
    seg = n // rows
    waterfall = []
    for r in range(rows):
        seg_i = i[r * seg:(r + 1) * seg]
        seg_q = q[r * seg:(r + 1) * seg]
        if len(seg_i) < 8:
            break
        fft = np.fft.fftshift(np.fft.fft(seg_i + 1j * seg_q))
        mag_db = 20 * np.log10(np.abs(fft) / len(seg_i) + 1e-10)
        half = len(mag_db) // 2
        waterfall.append({
            "time": r * seg / fs,
            "values": mag_db[half:].tolist()
        })
    return waterfall


def classify_modulation(i: np.ndarray, q: np.ndarray) -> dict:
    """Simple modulation classification based on features"""
    iq = i + 1j * q
    amp = np.abs(iq)
    phase = np.angle(iq)

    scores = {}
    amp_var = np.var(amp) / (np.mean(np.abs(amp)) + 1e-5)
    phase_var = np.var(phase)

    # AM: high amplitude variation, low phase variation
    scores["AM"] = min(1.0, amp_var * 3) * (1 - min(0.5, phase_var / 5))
    # FM: low amplitude variation, high phase variation
    scores["FM"] = (1 - min(0.8, amp_var * 2)) * min(1.0, phase_var / 5 * 3)
    # BPSK: moderate amplitude
    scores["BPSK"] = 0.5 + 0.3 * np.abs(amp_var - 0.5)
    # QPSK
    scores["QPSK"] = 0.6 + 0.2 * (1 - amp_var)
    # 16QAM: higher amplitude variation than QPSK
    scores["16QAM"] = 0.5 + 0.4 * amp_var

    # Normalize
    total = sum(scores.values()) or 1
    scores = {k: v / total for k, v in scores.items()}

    best = max(scores, key=scores.get)
    candidates = sorted([{"type": k, "score": round(v, 3)} for k, v in scores.items()], key=lambda x: x["score"], reverse=True)

    return {
        "type": best,
        "confidence": round(scores[best], 3),
        "candidates": candidates,
        "symbolRate": 1000 / 16 if best in ("BPSK", "QPSK", "16QAM") else None,
        "frequencyOffset": round(random.uniform(-5, 5), 2)
    }


@app.post("/api/generate")
def generate_and_analyze(req: GenerateRequest):
    i, q = generate_signal(req.modulation, req.samples, req.snr)
    freqs, mags = compute_fft(i, q)
    waterfall = compute_waterfall(i, q)
    modulation = classify_modulation(i, q)

    n = len(i)
    step = max(1, n // 200)
    constellation = [{"i": float(i[k]), "q": float(q[k])} for k in range(0, n, step)]

    return {
        "resultId": uuid.uuid4().hex,
        "meta": {"modulation": req.modulation, "samples": req.samples, "snr": req.snr},
        "spectrum": {"frequencies": freqs, "magnitudes": mags},
        "waterfall": waterfall,
        "constellation": constellation,
        "modulation": modulation
    }


@app.get("/api/exports/sections")
def export_sections():
    """可勾选的导出板块定义，供前端渲染复选框。"""
    return {"sections": [{"key": k, "label": SECTION_LABELS[k]} for k in EXPORT_SECTIONS]}


@app.post("/api/exports")
def create_export(req: ExportRequest):
    if req.forceFailStep and req.forceFailStep not in SIMULATABLE_STEPS:
        # 参数非法属于校验阶段问题
        raise ExportError("validate", f"不支持的故障模拟步骤：{req.forceFailStep}", status_code=400)
    try:
        out = export_manager.create_export(
            result_id=req.resultId,
            sections=req.sections,
            result_data=req.result,
            source=req.source,
            force=req.force,
            fail_step=req.forceFailStep,
        )
        return {"ok": True, **out}
    except ExportError as e:
        # 校验类错误（400/409）不落失败记录；真正执行到写文件/记录步骤失败才留痕
        failed_record = None
        if e.status_code == 500:
            failed_record = export_manager.record_failure(
                result_id=req.resultId,
                sections=req.sections,
                step=e.step,
                message=e.message,
                source=req.source,
            )
        return JSONResponse(
            status_code=e.status_code,
            content={
                "ok": False,
                "failedStep": e.step,
                "failedStepLabel": STEP_LABELS.get(e.step, e.step),
                "message": e.message,
                "record": failed_record,
            },
        )


@app.get("/api/exports")
def list_exports():
    return {"records": export_manager.list_records()}


@app.post("/api/exports/{export_id}/retry")
def retry_export(export_id: str, req: ExportRetryRequest):
    if req.forceFailStep and req.forceFailStep not in SIMULATABLE_STEPS:
        raise ExportError("validate", f"不支持的故障模拟步骤：{req.forceFailStep}", status_code=400)
    out = export_manager.retry_export(
        export_id=export_id,
        result_data=req.result,
        sections=req.sections,
        source=req.source,
        fail_step=req.forceFailStep,
    )
    return {"ok": True, **out}


@app.get("/api/exports/{export_id}")
def get_export(export_id: str):
    record = export_manager.get_record(export_id)
    if record is None:
        return JSONResponse(status_code=404, content={"message": "导出记录不存在"})
    return record


@app.get("/api/exports/{export_id}/download")
def download_export(export_id: str):
    record = export_manager.get_record(export_id)
    if record is None:
        return JSONResponse(status_code=404, content={"message": "导出记录不存在"})
    if record["status"] != "success" or not record.get("fileName"):
        return JSONResponse(
            status_code=409,
            content={
                "message": "报告文件缺失，无法下载",
                "status": record["status"],
                "failedStep": record.get("failStep"),
            },
        )
    path = os.path.join(EXPORT_DIR, record["fileName"])
    return FileResponse(path, media_type="application/json", filename=record["fileName"])


# 挂载已生成的报告目录（按文件名访问，下载接口之外的备用入口）
app.mount("/exports-files", StaticFiles(directory=EXPORT_DIR), name="exports-files")
