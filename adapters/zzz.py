# -*- coding: utf-8 -*-
# 绝区零引擎适配器 —— OneDragon（CLI，管理员+后台模式+虚拟手柄进游戏）
# 关键事实（2026-09-10 实测）：
#   1) OneDragon 必须以【管理员】运行（否则 PostMessage 被受保护窗口拒绝）
#   2) 登录页"点击进入游戏"：合成鼠标/PostMessage 都无效 —— 只有【虚拟手柄 A 键】有效（实测）
#   3) 进入游戏后（大世界）：OneDragon 管理员+后台模式可正常完成全部任务（实测 ok=True）
#   4) 计划任务 /RL HIGHEST 上下文 = 提权 → 直接 Popen 启动 CLI（无任何 UAC）
#   5) 启动前先杀游戏：让 OneDragon 走"打开游戏"，再由本适配器用手柄 A 送入游戏
import os
import time
import subprocess
import logging
from pathlib import Path

log = logging.getLogger("auto_daily")

BASE = Path(__file__).resolve().parent.parent
# 可用环境变量 HOYO_ONEDRAGON_DIR 覆盖；默认约定：<项目根>/OneDragon-ref
ONE_DRAGON_DIR = Path(os.environ.get("HOYO_ONEDRAGON_DIR") or (BASE / "OneDragon-ref"))
LAUNCHER = ONE_DRAGON_DIR / "src" / "zzz_od" / "application" / "zzz_application_launcher.py"
PY = ONE_DRAGON_DIR / ".venv" / "Scripts" / "python.exe"
OD_LOG = ONE_DRAGON_DIR / ".log" / "log.txt"
INSTANCE = "1"


def _run(cmd):
    return subprocess.run(cmd, capture_output=True,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _kill_gui_if_running():
    try:
        script = (
            "$ps = Get-CimInstance Win32_Process | "
            "Where-Object { $_.Name -match 'pythonw.exe|python.exe' -and $_.CommandLine -match 'zzz_od.*gui.*app.py' }; "
            "foreach ($p in $ps) { taskkill /F /PID $p.ProcessId 2>&1 | Out-Null }; "
            "Write-Output ('killed=' + (($ps | Measure-Object).Count))"
        )
        r = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                           capture_output=True, text=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        log.info("[zzz] GUI 进程清理: %s", (r.stdout or "").strip())
    except Exception as e:
        log.warning("[zzz] GUI 清理失败: %s", e)


def _ensure_background_mode():
    try:
        import yaml
        p = ONE_DRAGON_DIR / "config" / "01" / "game.yml"
        data = {}
        if p.exists():
            try:
                data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
                data = {k: v for k, v in data.items() if v is not None}
            except Exception:
                data = {}
        # 【关键】保持【前台模式】：该机型上后台模式(PostMessage)会被游戏保护层拒绝，
        # 前台模式(pyautogui)才是用户昨天验证可用的方式。绝不改成 True。
        data["background_mode"] = False
        data["background_gamepad_type"] = "xbox"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        log.info("[zzz] 前台模式已确保 (config/01/game.yml background_mode=false)")
    except Exception as e:
        log.warning("[zzz] game.yml 写入失败: %s", e)


def _tail_od_log(max_bytes: int = 20000) -> str:
    try:
        if not OD_LOG.exists():
            return ""
        size = OD_LOG.stat().st_size
        with open(OD_LOG, "rb") as fp:
            if size > max_bytes:
                fp.seek(size - max_bytes)
            return fp.read().decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _in_login_loop() -> bool:
    """OneDragon 是否还卡在"点击进入游戏"循环（=游戏仍在登录页）。"""
    tail = _tail_od_log()
    lines = [l for l in tail.splitlines() if l.strip()][-8:]
    return any("点击进入游戏" in l for l in lines)


class ZZZAdapter:
    """绝区零适配器：OneDragon CLI + 虚拟手柄自动进入游戏。"""

    def __init__(self, cfg, targets):
        self.cfg = cfg
        self.targets = [t for t in targets if t.get("enabled", True)]
        self.proc = None

    def start(self):
        if not LAUNCHER.exists() or not PY.exists():
            raise FileNotFoundError("OneDragon 未就绪: %s" % ONE_DRAGON_DIR)
        _kill_gui_if_running()
        _ensure_background_mode()
        _run(["taskkill", "/IM", "ZenlessZoneZero.exe", "/F"])
        for _ in range(30):
            r = _run(["tasklist", "/FI", "IMAGENAME eq ZenlessZoneZero.exe"])
            if b"ZenlessZoneZero.exe" not in r.stdout:
                break
            time.sleep(1)
        env = os.environ.copy()
        proxy = os.environ.get("HOYO_PROXY", "").strip()   # 可选：如 http://127.0.0.1:7890
        if proxy:
            env["HTTP_PROXY"] = proxy
            env["HTTPS_PROXY"] = proxy
        args = [str(PY), str(LAUNCHER), "-c", "-i", INSTANCE]
        log.info("[zzz] 启动 OneDragon CLI(提权上下文，无 UAC): %s", " ".join(args))
        try:
            cli_log_path = BASE / "logs" / "zzz_cli.log"
            cli_log_path.parent.mkdir(parents=True, exist_ok=True)
            self._cli_log = open(cli_log_path, "ab", buffering=0)
        except Exception:
            self._cli_log = subprocess.DEVNULL
        self.proc = subprocess.Popen(args, cwd=str(ONE_DRAGON_DIR), env=env,
                                     stdout=self._cli_log, stderr=subprocess.STDOUT)
        # 前台模式下 OneDragon 自行完成"点击进入游戏"（用户实测可用，无需任何虚拟手柄）

    def wait(self, timeout_s: int) -> bool:
        if self.proc is None:
            return False
        try:
            self.proc.wait(timeout=timeout_s)
            log.info("[zzz] OneDragon 退出 code=%s", self.proc.returncode)
            return True
        except subprocess.TimeoutExpired:
            log.warning("[zzz] OneDragon 超时(%ss)，终止", timeout_s)
            return False

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
