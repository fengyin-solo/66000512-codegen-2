"""分析结果报告导出与导出记录持久化。

导出流程分为三步，任一步骤失败都会通过 ExportError 告知调用方具体卡点，
便于前端在记录中展示失败位置并提供"重新导出"入口：

1. validate  校验导出请求（结果ID/包含范围/数据内容是否齐全）
2. write     生成报告文件并落盘（原子写，避免半截文件）
3. record    更新导出记录索引（原子写 records.json）
"""

import json
import os
import tempfile
import time
import uuid
from typing import Any, Dict, List, Optional

# 全部可导出的内容板块：三块图形数据 + 调制识别结论
EXPORT_SECTIONS = ("spectrum", "waterfall", "constellation", "modulation")
SECTION_LABELS = {
    "spectrum": "FFT频谱数据",
    "waterfall": "瀑布图数据",
    "constellation": "星座图数据",
    "modulation": "调制识别结论",
}
STEP_LABELS = {
    "validate": "校验导出内容",
    "write": "写入报告文件",
    "record": "保存导出记录",
}

# 仅用于联调/测试故障提示：通过 force_fail_step 人为指定失败步骤
SIMULATABLE_STEPS = ("write", "record")


class ExportError(Exception):
    """导出过程中的可预期错误，携带失败步骤与面向用户的提示。"""

    def __init__(self, step: str, message: str, status_code: int = 500):
        super().__init__(message)
        self.step = step
        self.message = message
        self.status_code = status_code


class ExportManager:
    def __init__(self, export_dir: str):
        self.export_dir = export_dir
        self.records_path = os.path.join(export_dir, "records.json")
        os.makedirs(export_dir, exist_ok=True)

    # ---------- 记录索引 ----------

    def _load_records(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.records_path):
            return []
        try:
            with open(self.records_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            # 索引损坏不应阻断新的导出，按无记录处理
            return []

    def _save_records(self, records: List[Dict[str, Any]]) -> None:
        try:
            self._atomic_write(self.records_path, json.dumps(records, ensure_ascii=False, indent=2))
        except OSError as e:
            raise ExportError("record", f"导出记录无法写入：{e}", status_code=500)

    @staticmethod
    def _atomic_write(path: str, content: str) -> None:
        directory = os.path.dirname(path) or "."
        fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    def _file_path(self, filename: str) -> str:
        return os.path.join(self.export_dir, filename)

    def _find_record(self, records: List[Dict[str, Any]], result_id: str) -> Optional[Dict[str, Any]]:
        return next((r for r in records if r.get("resultId") == result_id), None)

    @staticmethod
    def _decorate(record: Dict[str, Any], file_exists: bool) -> Dict[str, Any]:
        """补充展示字段：板块中文名、实时状态（成功/文件缺失/失败）。"""
        out = dict(record)
        out["sectionLabels"] = [SECTION_LABELS.get(s, s) for s in record.get("sections", [])]
        if record.get("lastStatus") == "failed":
            out["status"] = "failed"
        elif file_exists:
            out["status"] = "success"
        else:
            out["status"] = "missing"
        return out

    def list_records(self) -> List[Dict[str, Any]]:
        records = self._load_records()
        decorated = [
            self._decorate(r, bool(r.get("fileName")) and os.path.exists(self._file_path(r["fileName"])))
            for r in records
        ]
        decorated.sort(key=lambda r: r.get("exportedAt", 0), reverse=True)
        return decorated

    def get_record(self, export_id: str) -> Optional[Dict[str, Any]]:
        for r in self._load_records():
            if r.get("id") == export_id:
                return self._decorate(
                    r, bool(r.get("fileName")) and os.path.exists(self._file_path(r["fileName"]))
                )
        return None

    def get_record_by_result(self, result_id: str) -> Optional[Dict[str, Any]]:
        record = self._find_record(self._load_records(), result_id)
        if record is None:
            return None
        return self._decorate(
            record, bool(record.get("fileName")) and os.path.exists(self._file_path(record["fileName"]))
        )

    # ---------- 导出主流程 ----------

    def create_export(
        self,
        result_id: str,
        sections: List[str],
        result_data: Dict[str, Any],
        source: Optional[Dict[str, Any]] = None,
        force: bool = False,
        fail_step: Optional[str] = None,
    ) -> Dict[str, Any]:
        # ---- 步骤 1：校验 ----
        if not result_id or not isinstance(result_id, str):
            raise ExportError("validate", "缺少分析结果标识，无法导出。", status_code=400)
        if not sections:
            raise ExportError("validate", "未勾选任何导出内容，请至少选择一项。", status_code=400)
        invalid = [s for s in sections if s not in EXPORT_SECTIONS]
        if invalid:
            raise ExportError("validate", f"包含无法识别的导出内容：{', '.join(invalid)}", status_code=400)
        sections = list(dict.fromkeys(sections))  # 去重并保持顺序
        missing_sections = [s for s in sections if not result_data.get(s)]
        if missing_sections:
            labels = [SECTION_LABELS[s] for s in missing_sections]
            raise ExportError(
                "validate",
                f"所选内容缺少后端返回的数据：{'、'.join(labels)}，请重新分析后再导出。",
                status_code=400,
            )

        records = self._load_records()
        existing = self._find_record(records, result_id)
        existing_file = existing.get("fileName") if existing else None
        will_overwrite = bool(existing_file) and os.path.exists(self._file_path(existing_file))  # type: ignore[arg-type]
        if will_overwrite and not force:
            raise ExportError(
                "validate",
                f"该分析结果已导出过（{existing_file}），再次导出将覆盖已有文件。",
                status_code=409,
            )

        # ---- 步骤 2：写报告文件 ----
        now = time.time()
        filename = f"report-{result_id[:8]}.json"
        if fail_step == "write":
            raise ExportError("write", "报告文件写入失败（模拟）：磁盘不可用，请检查存储空间后重试。")
        report = self._build_report(result_id, sections, result_data, source, now)
        try:
            self._atomic_write(self._file_path(filename), json.dumps(report, ensure_ascii=False, indent=2))
        except OSError as e:
            raise ExportError("write", f"报告文件写入失败：{e}", status_code=500)

        # 导出成功后，若历史文件名不同（旧文件丢失等情况），顺带清理同名旧报告
        if existing and existing.get("fileName") and existing["fileName"] != filename:
            stale = self._file_path(existing["fileName"])
            if os.path.exists(stale):
                try:
                    os.unlink(stale)
                except OSError:
                    pass

        # ---- 步骤 3：保存导出记录 ----
        if fail_step == "record":
            raise ExportError(
                "record",
                "报告文件已生成，但导出记录保存失败（模拟），文件可能无法在记录中追踪。",
            )
        is_overwrite = existing is not None
        if existing:
            record = {
                **existing,
                "sections": sections,
                "fileName": filename,
                "exportedAt": now,
                "lastStatus": "success",
                "failStep": None,
                "failMessage": None,
                "overwritten": True,
            }
            records = [r if r.get("id") != record["id"] else record for r in records]
        else:
            record = {
                "id": uuid.uuid4().hex,
                "resultId": result_id,
                "sections": sections,
                "fileName": filename,
                "exportedAt": now,
                "createdAt": now,
                "lastStatus": "success",
                "failStep": None,
                "failMessage": None,
                "overwritten": False,
                "source": source or {},
            }
            records.append(record)
        self._save_records(records)

        return {
            "record": self._decorate(record, True),
            "overwritten": is_overwrite,
            "failedStep": None,
        }

    def record_failure(
        self,
        result_id: str,
        sections: List[str],
        step: str,
        message: str,
        source: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """导出失败也留痕：记录卡在第几步，供导出记录页提供重试入口。"""
        now = time.time()
        records = self._load_records()
        existing = self._find_record(records, result_id)
        if existing:
            record = {
                **existing,
                "sections": sections or existing.get("sections", []),
                "exportedAt": now,
                "lastStatus": "failed",
                "failStep": step,
                "failMessage": message,
            }
            records = [r if r.get("id") != record["id"] else record for r in records]
        else:
            record = {
                "id": uuid.uuid4().hex,
                "resultId": result_id,
                "sections": sections or [],
                "fileName": None,
                "exportedAt": now,
                "createdAt": now,
                "lastStatus": "failed",
                "failStep": step,
                "failMessage": message,
                "overwritten": False,
                "source": source or {},
            }
            records.append(record)
        self._save_records(records)
        return self._decorate(record, False)

    def retry_export(
        self,
        export_id: str,
        result_data: Optional[Dict[str, Any]] = None,
        sections: Optional[List[str]] = None,
        source: Optional[Dict[str, Any]] = None,
        fail_step: Optional[str] = None,
    ) -> Dict[str, Any]:
        """从失败记录重新发起导出。

        - 文件已在磁盘上（卡在保存记录步骤）：直接修复记录状态，无需重传数据
        - 文件缺失（卡在写文件步骤）：必须带回分析数据，重新执行完整导出流程
        """
        records = self._load_records()
        record = next((r for r in records if r.get("id") == export_id), None)
        if record is None:
            raise ExportError("validate", "导出记录不存在或已被清除。", status_code=404)

        result_id = record["resultId"]
        filename = record.get("fileName") or f"report-{result_id[:8]}.json"
        file_exists = bool(filename) and os.path.exists(self._file_path(filename))

        if file_exists:
            record.update({
                "lastStatus": "success",
                "failStep": None,
                "failMessage": None,
                "exportedAt": time.time(),
                "sections": sections or record.get("sections", []),
                "fileName": filename,
            })
            records = [r if r.get("id") != export_id else record for r in records]
            self._save_records(records)
            return {"record": self._decorate(record, True), "overwritten": False, "repaired": True}

        if not result_data:
            raise ExportError(
                "validate",
                "原报告文件已缺失，需要重新分析获取数据后才能再次导出。",
                status_code=400,
            )
        out = self.create_export(
            result_id=result_id,
            sections=sections or record.get("sections", []),
            result_data=result_data,
            source=source or record.get("source"),
            force=True,
            fail_step=fail_step,
        )
        out["repaired"] = False
        return out

    # ---------- 报告内容 ----------

    @staticmethod
    def _build_report(
        result_id: str,
        sections: List[str],
        result_data: Dict[str, Any],
        source: Optional[Dict[str, Any]],
        now: float,
    ) -> Dict[str, Any]:
        report: Dict[str, Any] = {
            "reportType": "rf-analysis-report",
            "version": 1,
            "resultId": result_id,
            "generatedAt": now,
            "generatedAtText": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
            "source": source or {},
            "includedSections": sections,
            "includedSectionLabels": [SECTION_LABELS[s] for s in sections],
        }
        for s in sections:
            report[s] = result_data.get(s)
        return report
