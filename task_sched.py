# -*- coding: utf-8 -*-
"""计划任务 HoYoDaily 的注册/查询（面板和自动调度器共用一份实现）。"""
import re
import subprocess
from pathlib import Path

BASE = Path(__file__).resolve().parent
TASK_NAME = "HoYoDaily"
BS = chr(92)


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def task_start_time() -> str:
    """任务当前的每日触发时间 HH:MM；读不到返回空串。"""
    r = _run(["schtasks", "/Query", "/TN", TASK_NAME, "/XML"])
    m = re.search(r"<StartBoundary>([^<]+)</StartBoundary>", r.stdout or "")
    if not m:
        return ""
    s = m.group(1)
    return s[11:16] if len(s) >= 16 else ""


def task_info() -> dict:
    r = _run(["schtasks", "/Query", "/TN", TASK_NAME, "/V", "/FO", "LIST"])
    info = {"exists": r.returncode == 0, "time": task_start_time(), "next": "", "state": ""}
    for line in (r.stdout or "").splitlines():
        if "Next Run Time:" in line:
            info["next"] = line.split(":", 1)[1].strip()
        if "Status:" in line:
            info["state"] = line.split(":", 1)[1].strip()
    return info


def register_task(hhmm: str):
    """注册/覆盖计划任务（每天 hhmm 触发，最高权限、无 UAC）。需要管理员，返回 (ok, msg)。"""
    exe = str(BASE / ".venv" / "Scripts" / "pythonw.exe").replace("/", BS)
    arg = str(BASE / "auto_daily.py").replace("/", BS)
    wd = str(BASE).replace("/", BS)
    q = chr(39)
    ps = ("$a=New-ScheduledTaskAction -Execute " + q + exe + q + " -Argument " + q + arg + q
          + " -WorkingDirectory " + q + wd + q + "; "
          "$t=New-ScheduledTaskTrigger -Daily -At " + hhmm + "; "
          "$s=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries"
          " -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 6); "
          "$p=New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest; "
          "Register-ScheduledTask -TaskName " + q + TASK_NAME + q
          + " -Action $a -Trigger $t -Settings $s -Principal $p -Force | Out-Null; Write-Output OK")
    r = _run(["powershell", "-NoProfile", "-Command", ps])
    ok = "OK" in (r.stdout or "")
    return ok, ((r.stdout or "") + (r.stderr or "")).strip()[:200]
