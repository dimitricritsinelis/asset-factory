from __future__ import annotations

from pathlib import Path

from .json_io import read_json_dict, write_json


def set_report_status(path: Path, status: str, reasons: list[str]) -> None:
    report = read_json_dict(path)
    report["status"] = status
    if reasons:
        report["reason"] = reasons
        report["reasons"] = reasons
    else:
        report.pop("reason", None)
        report.pop("reasons", None)
    write_json(path, report)
