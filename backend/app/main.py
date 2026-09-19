import hashlib
import json
import math
import os
import random
import threading
from datetime import datetime

import numpy as np
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi import Body
from pydantic import BaseModel

app = FastAPI(title="RF Signal Analyzer")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

MODULATION_TYPES = ["AM", "FM", "BPSK", "QPSK", "16QAM"]

# ---- 报告导出相关 ----
EXPORT_SECTIONS = ["spectrum", "waterfall", "constellation", "modulation"]
SECTION_LABELS = {
    "spectrum": "FFT频谱数据",
    "waterfall": "瀑布图数据",
    "constellation": "星座图数据",
    "modulation": "调制识别结论",
}
STEP_LABELS = {
    "build": "组装报告内容",
    "write": "写入报告文件",
    "record": "保存导出记录",
}
# 导出数据保存在后端，保证刷新/重启后导出记录仍然保留
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "export_data")
REPORT_DIR = os.path.join(DATA_DIR, "reports")
INDEX_PATH = os.path.join(DATA_DIR, "records.json")
_index_lock = threading.Lock()

os.makedirs(REPORT_DIR, exist_ok=True)
if not os.path.exists(INDEX_PATH):
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump([], f)


class GenerateRequest(BaseModel):
    modulation: str = "QPSK"
    samples: int = 1024
    snr: float = 20.0


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
    seg = n // rows
    waterfall = []
    for r in range(rows):
        seg_i = i[r * seg:(r + 1) * seg]
        seg_q = q[r * seg:(r + 1) * seg]
        if len(seg_i) < 32:
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
        "spectrum": {"frequencies": freqs, "magnitudes": mags},
        "waterfall": waterfall,
        "constellation": constellation,
        "modulation": modulation
    }


# =========================== 分析结果导出 ===========================

class ExportRequest(BaseModel):
    include: list[str]
    result: dict
    overwrite: bool = False
    # 测试用：在指定步骤模拟失败，可选 build/write/record
    failStep: str | None = None


def _load_records() -> list[dict]:
    try:
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return []


def _save_records(records: list[dict]) -> None:
    tmp_path = INDEX_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False)
    os.replace(tmp_path, INDEX_PATH)


def _content_hash(result: dict, include: list[str]) -> str:
    """同一份结果 + 同一包含范围 => 同一哈希，用于判定重复导出"""
    payload = json.dumps({"include": sorted(include), "result": result}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _build_report(result: dict, include: list[str]) -> dict:
    data = {}
    if "spectrum" in include:
        data["spectrum"] = result.get("spectrum")
    if "waterfall" in include:
        data["waterfall"] = result.get("waterfall")
    if "constellation" in include:
        data["constellation"] = result.get("constellation")

    modulation = result.get("modulation") or {}
    report = {
        "title": "射频信号分析结果报告",
        "exportedAt": _now_iso(),
        "conclusion": {
            "modulationType": modulation.get("type"),
            "confidence": modulation.get("confidence"),
            "symbolRate": modulation.get("symbolRate"),
            "frequencyOffset": modulation.get("frequencyOffset"),
            "candidates": modulation.get("candidates", []),
        } if "modulation" in include else None,
        "includedSections": list(include),
        "includedSectionNames": [SECTION_LABELS[s] for s in include],
        "data": data,
    }
    return report


def _strip_payload(record: dict) -> dict:
    """列表/详情返回时不携带用于重试的原始结果快照"""
    return {k: v for k, v in record.items() if k != "payload"}


def _refresh_health(record: dict) -> dict:
    """检查报告文件是否实际存在，导出失败导致文件缺失时标记 missing"""
    if record.get("status") == "ok":
        filename = record.get("filename")
        if not filename or not os.path.exists(os.path.join(REPORT_DIR, filename)):
            record["status"] = "missing"
            record["missingStep"] = "write"
    return record


def _run_export(record: dict) -> dict:
    """按 build -> write -> record 的步骤执行导出，返回更新后的记录"""
    payload = record["payload"]
    include = payload["include"]
    result = payload["result"]

    # 步骤 1：组装报告内容
    try:
        report = _build_report(result, include)
    except Exception:
        record["status"] = "failed"
        record["failedStep"] = "build"
        return record
    if record.get("failStep") == "build":
        record["status"] = "failed"
        record["failedStep"] = "build"
        return record

    # 步骤 2：写入报告文件
    if record.get("failStep") != "write":
        try:
            path = os.path.join(REPORT_DIR, record["filename"])
            tmp_path = path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
        except Exception:
            record["status"] = "failed"
            record["failedStep"] = "write"
            return record
    else:
        record["status"] = "failed"
        record["failedStep"] = "write"
        return record

    # 步骤 3：持久化导出记录
    if record.get("failStep") == "record":
        record["status"] = "failed"
        record["failedStep"] = "record"
        return record

    record["status"] = "ok"
    record.pop("failedStep", None)
    record.pop("failStep", None)
    record["size"] = os.path.getsize(os.path.join(REPORT_DIR, record["filename"]))
    record["exportedAt"] = _now_iso()
    return record


def _persist_record(record: dict) -> None:
    """把记录写入索引（含 payload 快照，用于文件缺失后的重新导出）"""
    with _index_lock:
        records = _load_records()
        for idx, existing in enumerate(records):
            if existing["id"] == record["id"]:
                records[idx] = record
                break
        else:
            records.insert(0, record)
        _save_records(records)


@app.post("/api/exports")
def create_export(req: ExportRequest):
    include = req.include or []
    invalid = [s for s in include if s not in EXPORT_SECTIONS]
    if invalid:
        raise HTTPException(400, f"不支持的报告内容: {', '.join(invalid)}")
    if not include:
        raise HTTPException(400, "请至少勾选一项要包含的内容")
    if not isinstance(req.result, dict) or not req.result:
        raise HTTPException(400, "没有可导出的分析结果")

    h = _content_hash(req.result, include)
    with _index_lock:
        records = _load_records()
        duplicate = next((r for r in records if r.get("hash") == h), None)
        duplicate = dict(duplicate) if duplicate else None

    # 同一份结果重复导出：已有健康文件时需显式确认覆盖
    if duplicate and duplicate.get("status") == "ok":
        path = os.path.join(REPORT_DIR, duplicate["filename"])
        if os.path.exists(path):
            if not req.overwrite:
                raise HTTPException(
                    409,
                    detail={
                        "message": "该分析结果已导出过，重新导出将覆盖已有报告文件",
                        "existing": _strip_payload(_refresh_health(duplicate)),
                    },
                )

    if duplicate:
        record = duplicate
        record["payload"] = {"result": req.result, "include": include}
        record.pop("missingStep", None)
        record.pop("failedStep", None)
    else:
        mod_type = (req.result.get("modulation") or {}).get("type", "UNKNOWN")
        record = {
            "id": h,
            "hash": h,
            "filename": f"report_{mod_type}_{h}.json",
            "modulationType": mod_type,
            "include": list(include),
            "includeNames": [SECTION_LABELS[s] for s in include],
            "createdAt": _now_iso(),
            "payload": {"result": req.result, "include": include},
        }

    if req.failStep:
        record["failStep"] = req.failStep

    record = _run_export(record)
    # 无论成功失败都落盘记录（失败时也保留“卡在哪一步”与重试入口）
    persist_ok = True
    try:
        _persist_record(record)
    except Exception:
        persist_ok = False
    # 文件已生成但记录持久化失败：按“保存导出记录”步骤失败报告
    if not persist_ok and record["status"] == "ok":
        record["status"] = "failed"
        record["failedStep"] = "record"
        try:
            _persist_record(record)
        except Exception:
            pass

    if record["status"] != "ok":
        step = STEP_LABELS.get(record["failedStep"], record["failedStep"])
        raise HTTPException(500, detail={"message": f"导出失败：卡在「{step}」步骤，报告文件未生成", "record": _strip_payload(record)})

    return _strip_payload(record)


@app.get("/api/exports")
def list_exports():
    with _index_lock:
        records = _load_records()
    return [_strip_payload(_refresh_health(dict(r))) for r in records]


@app.get("/api/exports/{record_id}")
def get_export(record_id: str):
    with _index_lock:
        records = _load_records()
    record = next((r for r in records if r["id"] == record_id), None)
    if not record:
        raise HTTPException(404, "导出记录不存在")
    return _strip_payload(_refresh_health(dict(record)))


@app.post("/api/exports/{record_id}/retry")
def retry_export(record_id: str):
    """文件缺失/导出失败后，用记录中保存的原始结果重新发起导出（覆盖同名文件）"""
    with _index_lock:
        records = _load_records()
        record = next((dict(r) for r in records if r["id"] == record_id), None)

    if record is None:
        raise HTTPException(404, "导出记录不存在")
    payload = record.get("payload")

    if not payload:
        raise HTTPException(409, "该记录缺少原始分析数据，无法重新导出，请重新执行分析后再导出")

    # 重新发起导出时忽略历史上的故障注入标记
    record.pop("failStep", None)
    record.pop("missingStep", None)
    record.pop("failedStep", None)
    record = _run_export(record)
    _persist_record(record)

    if record["status"] != "ok":
        step = STEP_LABELS.get(record["failedStep"], record["failedStep"])
        raise HTTPException(500, detail={"message": f"重新导出失败：仍卡在「{step}」步骤", "record": _strip_payload(record)})

    return _strip_payload(record)


@app.get("/api/exports/{record_id}/download")
def download_export(record_id: str):
    with _index_lock:
        records = _load_records()
    record = next((r for r in records if r["id"] == record_id), None)
    if record is None:
        raise HTTPException(404, "导出记录不存在")

    path = os.path.join(REPORT_DIR, record["filename"])
    if not os.path.exists(path):
        record["status"] = "missing"
        record["missingStep"] = "write"
        with _index_lock:
            records = _load_records()
            for idx, r in enumerate(records):
                if r["id"] == record_id:
                    records[idx] = record
                    break
            _save_records(records)
        raise HTTPException(410, "报告文件缺失（可能写入失败或被手动删除），请在导出记录中重新导出")

    return FileResponse(
        path,
        media_type="application/json",
        filename=record["filename"],
    )