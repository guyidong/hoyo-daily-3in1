# -*- coding: utf-8 -*-
"""三游戏每日一条龙 · 集成面板后端（前端见 panel.html）"""
import json, os, subprocess, sys, threading, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
try:
    import yaml
except Exception:
    print("缺少 PyYAML: pip install pyyaml"); sys.exit(1)
import tools_manager as tm

BASE = Path(__file__).resolve().parent
CONFIG = BASE / "config" / "daily_config.yaml"
LOG = BASE / "logs" / "daily.log"
HTML = BASE / "panel.html"
TASK_NAME = "HoYoDaily"
BS = chr(92)
NL = chr(10)


def _run(cmd, **kw):
    kw.setdefault("capture_output", True)
    kw.setdefault("text", True)
    kw.setdefault("creationflags", getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return subprocess.run(cmd, **kw)


def pick_port(a=8900, b=8999):
    import socket
    for p in range(a, b + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", p)); return p
            except OSError:
                continue
    return b


def load_cfg():
    if not CONFIG.exists():
        ex = BASE / "config" / "daily_config.example.yaml"
        if ex.exists():
            CONFIG.write_text(ex.read_text(encoding="utf-8"), encoding="utf-8")
    return (yaml.safe_load(CONFIG.read_text(encoding="utf-8")) if CONFIG.exists() else {}) or {}


def save_cfg(d):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    # 备份一份（面板保存会重写 YAML，注释可能丢失；备份便于回退）
    if CONFIG.exists():
        try:
            (CONFIG.parent / "daily_config.yaml.bak").write_bytes(CONFIG.read_bytes())
        except Exception:
            pass
    CONFIG.write_text(yaml.safe_dump(d, allow_unicode=True, sort_keys=False), encoding="utf-8")


def task_info():
    r = _run(["schtasks", "/Query", "/TN", TASK_NAME, "/V", "/FO", "LIST"])
    i = {"exists": r.returncode == 0, "time": "", "next": "", "state": ""}
    for line in (r.stdout or "").splitlines():
        if "Start Time:" in line: i["time"] = line.split(":", 1)[1].strip()
        if "Next Run Time:" in line: i["next"] = line.split(":", 1)[1].strip()
        if "Status:" in line: i["state"] = line.split(":", 1)[1].strip()
    return i


def register_task(hhmm):
    exe = str(BASE / ".venv" / "Scripts" / "pythonw.exe").replace("/", BS)
    arg = str(BASE / "auto_daily.py").replace("/", BS)
    wd = str(BASE).replace("/", BS)
    q = chr(39)
    ps = ("$a=New-ScheduledTaskAction -Execute " + q + exe + q + " -Argument " + q + arg + q + " -WorkingDirectory " + q + wd + q + "; "
          "$t=New-ScheduledTaskTrigger -Daily -At " + hhmm + "; "
          "$s=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 6); "
          "$p=New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest; "
          "Register-ScheduledTask -TaskName " + q + TASK_NAME + q + " -Action $a -Trigger $t -Settings $s -Principal $p -Force | Out-Null; Write-Output OK")
    r = _run(["powershell", "-NoProfile", "-Command", ps])
    return ("OK" in (r.stdout or "")), ((r.stdout or "") + (r.stderr or "")).strip()[:200]


def log_tail(n=150):
    if not LOG.exists():
        return "(暂无日志)"
    try:
        with open(LOG, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 80000))
            return NL.join(f.read().decode("utf-8", "ignore").splitlines()[-n:])
    except Exception as e:
        return "读取失败: %s" % e


def _is_admin():
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _start(path, as_admin=False):
    """as_admin=True 时以管理员身份启动；本进程已是管理员则直接启动（不再弹 UAC）。"""
    p = str(path)
    if as_admin and not _is_admin():
        import ctypes
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", p, None, str(Path(p).parent), 1)
        if rc <= 32:
            raise OSError("ShellExecuteW runas 返回 %d" % rc)
        return
    os.startfile(p)


def launch_tool(key):
    m = {"bettergi": (BASE / "tools" / "BetterGI" / "BetterGI" / "BetterGI.exe", False),
         "march7th": (BASE / "tools" / "March7thAssistant" / "March7th Launcher.exe", False),
         "onedragon": (BASE / "one_dragon_launcher.bat", True)}
    if key not in m:
        return False, "未知工具: " + str(key)
    p, need_admin = m[key]
    if not p.exists():
        return False, "未找到 " + p.name + "（请先下载安装）"
    try:
        _start(p, need_admin)
    except Exception as e:
        return False, "启动失败: %s" % e
    tip = "（弹出权限确认请点“是”）" if (need_admin and not _is_admin()) else ""
    return True, "已启动 " + p.name + tip


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            txt = HTML.read_text(encoding="utf-8") if HTML.exists() else "<h3>panel.html 缺失</h3>"
            return self._send(200, txt, "text/html; charset=utf-8")
        if self.path == "/api/config":
            return self._send(200, json.dumps(load_cfg(), ensure_ascii=False))
        if self.path == "/api/task":
            return self._send(200, json.dumps(task_info(), ensure_ascii=False))
        if self.path == "/api/log":
            return self._send(200, json.dumps({"text": log_tail()}, ensure_ascii=False))
        if self.path == "/api/tools":
            return self._send(200, json.dumps(tm.status(), ensure_ascii=False))
        return self._send(404, json.dumps({"ok": False}))

    def do_POST(self):
        ln = int(self.headers.get("Content-Length") or 0)
        try:
            p = json.loads((self.rfile.read(ln).decode("utf-8") if ln else "{}") or "{}")
        except Exception:
            p = {}
        if self.path == "/api/config":
            save_cfg(p)
            return self._send(200, json.dumps({"ok": True}))
        if self.path == "/api/task":
            t = p.get("time", "07:30")
            t = t + ":00" if len(t) == 5 else t
            ok, msg = register_task(t)
            return self._send(200, json.dumps({"ok": ok, "msg": msg}, ensure_ascii=False))
        if self.path == "/api/run":
            r = _run(["schtasks", "/Run", "/TN", TASK_NAME])
            return self._send(200, json.dumps({"ok": r.returncode == 0, "msg": (r.stdout or r.stderr or "").strip()}, ensure_ascii=False))
        if self.path == "/api/tools/install":
            ks = p.get("keys") or []
            if not ks:
                return self._send(200, json.dumps({"ok": False, "msg": "未选择工具"}))
            threading.Thread(target=tm.install, args=(ks, (p.get("proxy") or "").strip()), daemon=True).start()
            return self._send(200, json.dumps({"ok": True}))
        if self.path == "/api/tools/launch":
            ok, msg = launch_tool(p.get("key", ""))
            return self._send(200, json.dumps({"ok": ok, "msg": msg}, ensure_ascii=False))
        return self._send(404, json.dumps({"ok": False}))

    def log_message(self, *a):
        pass


def main():
    port = pick_port()
    url = "http://127.0.0.1:%d" % port
    print("集成面板: %s  (Ctrl+C 退出)" % url)
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()


if __name__ == "__main__":
    main()
