"""运行基线报告生成。"""

from __future__ import annotations

import os
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _ps_snapshot() -> str:
    try:
        out = subprocess.check_output(["ps", "axo", "pid,etime,%cpu,%mem,cmd"], text=True)
        lines = [l for l in out.splitlines() if "datacat-service" in l or "cryptofeed.py" in l or "metrics/http.py" in l]
        return "\n".join(lines) if lines else "无运行进程"
    except Exception as exc:  # noqa: BLE001
        return f"采集失败: {exc}"


def main() -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    report = []
    report.append("# Datacat Service 运行基线报告")
    report.append("")
    report.append(f"- 时间：{now}")
    report.append(f"- 系统：{platform.platform()}")
    report.append(f"- CPU：{platform.processor() or 'unknown'}")
    report.append(f"- 负载：{os.getloadavg() if hasattr(os, 'getloadavg') else 'n/a'}")
    report.append("")
    report.append("## 进程快照")
    report.append("")
    report.append("```")
    report.append(_ps_snapshot())
    report.append("```")
    report.append("")
    (ROOT / "tasks" / "runtime-baseline.md").write_text("\n".join(report), encoding="utf-8")


if __name__ == "__main__":
    main()
