# -*- coding: utf-8 -*-
"""
三游戏每日一条龙 · 配置页面（本地网页）

用途：在浏览器里一键配置 原神/崩铁/绝区零 的每日任务目标、定时时间，并立即运行/查看日志。
依赖：仅 Python 标准库 + PyYAML（本项目 .venv 已带）。
启动：双击 start_page.bat 或 python web_config.py（浏览器会自动打开 http://127.0.0.1:8765）
"""
import json, os, subprocess, sys, threading, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    import yaml
except Exception:
    print("缺少 PyYAML：pip install pyyaml"); sys.exit(1)

BASE = Path(__file__).resolve().parent
CONFIG = BASE / "config" / "daily_config.yaml"
LOG = BASE / "logs" / "daily.log"
TASK_NAME = "HoYoDaily"


def pick_port(start: int = 8900, end: int = 8999) -> int:
    """自动挑一个空闲端口（避免与其它本地服务冲突）。"""
    import socket
    for p in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return 8999



def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def load_cfg():
    if not CONFIG.exists():
        return {}
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}


def save_cfg(data):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def task_info():
    r = _run(["schtasks", "/Query", "/TN", TASK_NAME, "/V", "/FO", "LIST"])
    info = {"exists": r.returncode == 0, "time": "", "next": "", "state": ""}
    for line in (r.stdout or "").splitlines():
        if "Start Time:" in line: info["time"] = line.split(":", 1)[1].strip()
        if "Next Run Time:" in line: info["next"] = line.split(":", 1)[1].strip()
        if "Status:" in line: info["state"] = line.split(":", 1)[1].strip()
    return info


def set_task_time(hhmm):
    r = _run(["schtasks", "/Change", "/TN", TASK_NAME, "/ST", hhmm])
    return r.returncode == 0, (r.stdout or r.stderr or "").strip()


def log_tail(n=120):
    if not LOG.exists():
        return ""
    try:
        with open(LOG, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 60000))
            text = f.read().decode("utf-8", "ignore")
        return "\n".join(text.splitlines()[-n:])
    except Exception as e:
        return "读取日志失败: %s" % e


PAGE = r"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>三游戏每日一条龙 · 配置</title>
<style>
body{font-family:"Microsoft YaHei",system-ui,sans-serif;background:#0f1115;color:#e8eaed;margin:0;padding:24px}
h1{font-size:20px;margin:0 0 6px}.sub{color:#9aa0a6;font-size:13px;margin-bottom:18px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px}
.card{background:#181b21;border:1px solid #262a33;border-radius:12px;padding:16px}
.card h2{font-size:16px;margin:0 0 10px;display:flex;align-items:center;gap:8px}
label{display:block;font-size:13px;color:#9aa0a6;margin:10px 0 4px}
input[type=text],input[type=time],input[type=number],select{width:100%;box-sizing:border-box;background:#0f1115;border:1px solid #2c313c;color:#e8eaed;border-radius:8px;padding:8px}
.sw{display:flex;align-items:center;gap:8px;margin-top:6px}
.row{display:flex;gap:10px;align-items:center;margin-top:10px;flex-wrap:wrap}
button{background:#2563eb;border:0;color:#fff;padding:9px 14px;border-radius:8px;cursor:pointer;font-size:13px}
button.ghost{background:#232833}button:active{transform:translateY(1px)}
pre{background:#0b0d11;border:1px solid #232833;border-radius:10px;padding:12px;max-height:320px;overflow:auto;font-size:12px;line-height:1.5}
.tag{font-size:12px;padding:2px 8px;border-radius:999px;background:#232833;color:#9aa0a6}
.ok{color:#34d399}.warn{color:#fbbf24}
</style></head><body>
<h1>三游戏每日一条龙 · 配置面板</h1>
<div class="sub">原神(BetterGI) · 崩铁(March7th) · 绝区零(OneDragon) —— 本面板只做「配置 + 调度」，游戏内操作全部由社区工具完成</div>
<div class="card" style="margin-bottom:16px">
  <h2>⏰ 每日定时 <span id="tstate" class="tag">读取中…</span></h2>
  <div class="row">
    <div style="flex:0 0 160px"><label>每天开跑时间</label><input type="time" id="runTime" value="07:30"></div>
    <button onclick="saveTime()">保存定时</button>
    <button class="ghost" onclick="runNow()">立即运行一轮</button>
    <span id="tinfo" class="sub" style="margin:0"></span>
  </div>
</div>
<div class="grid" id="cards"></div>
<div class="card" style="margin-top:16px">
  <h2>📜 运行日志（最近）</h2>
  <div class="row"><button class="ghost" onclick="loadLog()">刷新日志</button></div>
  <pre id="log">点「刷新日志」查看</pre>
</div>
<script>
let CFG=null;
const GAMES=[{k:"genshin",n:"原神",tool:"BetterGI"},{k:"starrail",n:"崩铁",tool:"March7th"},{k:"zzz",n:"绝区零",tool:"OneDragon"}];
async function j(u,o){const r=await fetch(u,o);return await r.json()}
async function load(){CFG=await j("/api/config");const t=await j("/api/task");
  document.getElementById("tstate").textContent=t.exists?("任务状态: "+(t.state||"?")):"未注册任务";
  if(t.time) document.getElementById("runTime").value=t.time.slice(0,5);
  document.getElementById("tinfo").textContent=t.next?("下次运行: "+t.next):"";
  const box=document.getElementById("cards");box.innerHTML="";
  for(const g of GAMES){const c=(CFG.games||{})[g.k]||{};const tg=(c.targets||[])[0]||{};
   box.insertAdjacentHTML("beforeend",`<div class="card"><h2>${g.n} <span class="tag">${g.tool}</span></h2>
    <div class="sw"><input type="checkbox" id="${g.k}_en" ${c.enabled?"checked":""}><label style="margin:0">启用每日自动</label></div>
    <label>游戏路径</label><input type="text" id="${g.k}_path" value="${c.client_path||""}">
    <label>目标任务</label><input type="text" id="${g.k}_task" value="${tg.name||tg.mission_name||""}">
    <label>次数（0=清空体力/电量）</label><input type="number" id="${g.k}_runs" value="${tg.runs!==undefined?tg.runs:0}">
    <div class="row"><button onclick="saveGame('${g.k}')">保存</button><span class="sub" style="margin:0">跑完自动关闭游戏并还原你的全屏</span></div></div>`)}
}
async function saveGame(k){const c=CFG.games[k]||(CFG.games[k]={});
  c.enabled=document.getElementById(k+"_en").checked;
  c.client_path=document.getElementById(k+"_path").value;
  const t=(c.targets&&c.targets[0])||(c.targets=[{}])[0];
  const v=document.getElementById(k+"_task").value;
  if(t.mission_name!==undefined&&!t.name)t.mission_name=v;else t.name=v;
  t.runs=parseInt(document.getElementById(k+"_runs").value||"0");
  await j("/api/config",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(CFG)});
  alert("已保存");load()}
async function saveTime(){const v=document.getElementById("runTime").value;
  const r=await j("/api/task",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({time:v})});
  alert(r.ok?"定时已更新为 "+v:(r.msg||"更新失败（改定时需要管理员权限，请右键页面快捷方式以管理员启动）"));load()}
async function runNow(){if(!confirm("立即运行三个游戏的每日流程？"))return;
  const r=await j("/api/run",{method:"POST"});alert(r.ok?"已开始运行（看下方日志）":(r.msg||"启动失败"));setTimeout(loadLog,3000)}
async function loadLog(){const r=await j("/api/log");document.getElementById("log").textContent=r.text||"(空)"}
load();
</script></body></html>"""


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
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if self.path == "/api/config":
            return self._send(200, json.dumps(load_cfg(), ensure_ascii=False))
        if self.path == "/api/task":
            return self._send(200, json.dumps(task_info(), ensure_ascii=False))
        if self.path == "/api/log":
            return self._send(200, json.dumps({"text": log_tail()}, ensure_ascii=False))
        return self._send(404, json.dumps({"ok": False}))

    def do_POST(self):
        ln = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(ln).decode("utf-8") if ln else "{}"
        try:
            payload = json.loads(raw or "{}")
        except Exception:
            payload = {}
        if self.path == "/api/config":
            save_cfg(payload)
            return self._send(200, json.dumps({"ok": True}))
        if self.path == "/api/task":
            ok, msg = set_task_time(payload.get("time", "07:30"))
            return self._send(200, json.dumps({"ok": ok, "msg": msg}, ensure_ascii=False))
        if self.path == "/api/run":
            r = _run(["schtasks", "/Run", "/TN", TASK_NAME])
            ok = r.returncode == 0
            return self._send(200, json.dumps({"ok": ok, "msg": (r.stdout or r.stderr or "").strip()}, ensure_ascii=False))
        return self._send(404, json.dumps({"ok": False}))

    def log_message(self, *a):
        pass


def main():
    port = pick_port()
    url = "http://127.0.0.1:%d" % port
    print("配置面板已启动: %s （Ctrl+C 退出）" % url)
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()


if __name__ == "__main__":
    main()
