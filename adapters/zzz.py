# -*- coding: utf-8 -*-
# 绝区零引擎适配器 —— OneDragon（官方 CLI，前台模式）
# 关键事实（实测）：
#   1) OneDragon 必须以【管理员】运行（否则 PostMessage 被受保护的游戏窗口拒绝，错误 5）
#   2) 必须用【前台模式】background_mode: false —— 后台模式下登录页点击会失败（实测）
#   3) 计划任务 /RL HIGHEST 上下文 = 提权 → 直接 Popen 启动 CLI（无任何 UAC）
#   4) 启动前先杀游戏：让 OneDragon 自己走"打开游戏 → 进入游戏"的完整流程
#   5) 不使用任何虚拟手柄/驱动：前台模式 + 正常输入即可
import os
import sys
import json
import shutil
import time
import subprocess
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # 能 import 到项目根的模块
import win_guard      # noqa: E402  游戏窗口兜底（16:9 检查/纠正）
import display_mode   # noqa: E402  注册表显示设置（两套键）

log = logging.getLogger("auto_daily")

BASE = Path(__file__).resolve().parent.parent          # 项目根目录
# 默认 <项目根>/OneDragon-ref；可用环境变量 HOYO_ONEDRAGON_DIR 覆盖
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


# ---- 游戏"自己的"显示设置档切换 ----------------------------------------------
# 实测（2026-09-13）：绝区零把显示模式存在自己的加密设置里（LocalStorage），
# 启动约 8 秒后用它覆盖注册表设置。只改注册表没用：游戏先按注册表以 1920x1080 窗口起来，
# 随即自己切回 2560x1600 全屏，OneDragon 在 16:10 下识别全废（邮件/签到/刷本全失败）。
# SetWindowPos 也拉不动它（实测连拉 10 次全部无效）。
# 所以改为"换档"：跑之前换成窗口档，跑完换回全屏档 —— 你自己玩的时候还是全屏。
PROFILE_DIR = BASE / "config" / "zzz_display_profiles"
LS_SUBPATH = Path("ZenlessZoneZero_Data") / "Persistent" / "LocalStorage"


def _game_localstorage(cfg: dict):
    client = (cfg or {}).get("client_path") or ""
    if not client:
        return None
    p = Path(client).parent / LS_SUBPATH
    return p if p.is_dir() else None


def stash_game_settings(cfg: dict) -> bool:
    """把游戏自己的显示设置文件挪走（备份到 profiles/_stash）。

    游戏没有自己的设置时，会回退用注册表里的设置（=我们写的 1920x1080 窗口），
    这样就能在没有"窗口档"快照的情况下让它跑窗口模式；跑完 after_kill 会把用户的档写回去。"""
    dst = _game_localstorage(cfg)
    if dst is None:
        log.warning("[zzz] 找不到游戏 LocalStorage，跳过设置挪移")
        return False
    stash = PROFILE_DIR / "_stash"
    stash.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in sorted(dst.iterdir()):
        if f.is_file() and f.suffix == ".bin":
            shutil.move(str(f), str(stash / f.name))
            n += 1
    log.info("[zzz] 已把游戏显示设置挪走 %d 个文件（本次将回退用注册表的窗口化设置）", n)
    return True


def swap_display_profile(cfg: dict, name: str) -> bool:
    """把游戏自己的显示设置换成 name 档（windowed / fullscreen）。必须在游戏关闭时执行。"""
    src = PROFILE_DIR / name
    if not src.is_dir():
        log.warning("[zzz] 没有 \"%s\" 显示档（%s），跳过切换", name, src)
        return False
    client = (cfg or {}).get("client_path") or ""
    if not client:
        log.warning("[zzz] 配置里没有 client_path，无法定位游戏目录，跳过显示档切换")
        return False
    dst = Path(client).parent / LS_SUBPATH
    if not dst.is_dir():
        log.warning("[zzz] 找不到游戏 LocalStorage 目录: %s", dst)
        return False
    n = 0
    for f in src.iterdir():
        if f.is_file() and f.suffix != ".json":
            shutil.copy2(f, dst / f.name)
            n += 1
    # 注册表也按这份档写（这份档是"游戏自己写出来的值"，照抄最保险）
    reg = PROFILE_DIR / ("registry_%s.json" % name)
    if reg.exists():
        try:
            display_mode.restore("zzz", json.loads(reg.read_text(encoding="utf-8")))
            log.info("[zzz] 注册表已按 \"%s\" 档写入", name)
        except Exception as e:
            log.warning("[zzz] 注册表档写入失败: %s", e)
    log.info("[zzz] 已切换为 \"%s\" 显示档（%d 个文件）", name, n)
    return True


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
    """绝区零适配器：OneDragon CLI（前台模式）自动进入游戏并跑完每日。"""

    def __init__(self, cfg, targets):
        self.cfg = cfg
        self.targets = [t for t in targets if t.get("enabled", True)]
        self.proc = None
        self._guard = None

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
        # 让游戏这次跑窗口模式：优先换成"窗口档"；没有窗口档就把它的设置挪走（回退用注册表）
        if not swap_display_profile(self.cfg, "windowed"):
            stash_game_settings(self.cfg)
        env = os.environ.copy()
        # 可选：HOYO_PROXY / 配置里的 proxy 才注入代理（默认不设）
        proxy = (os.environ.get("HOYO_PROXY") or self.cfg.get("proxy") or "").strip()
        if proxy:
            env["HTTP_PROXY"] = proxy
            env["HTTPS_PROXY"] = proxy
        args = [str(PY), str(LAUNCHER), "-c", "-i", INSTANCE]
        log.info("[zzz] 启动 OneDragon CLI(提权上下文，无 UAC): %s", " ".join(args))
        try:
            log_dir = BASE / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            self._cli_log = open(log_dir / "zzz_cli.log", "ab", buffering=0)
        except Exception:
            self._cli_log = subprocess.DEVNULL
        self.proc = subprocess.Popen(args, cwd=str(ONE_DRAGON_DIR), env=env,
                                     stdout=self._cli_log, stderr=subprocess.STDOUT)
        # 窗口兜底：整场盯着游戏窗口。实测绝区零会"先窗口化、进游戏后自己切回全屏"，
        # 所以必须一直盯（修一次就收工会漏掉后面这次切换，引擎就全线识别失败）。
        self._guard = win_guard.watch_async("zzz")
        # 前台模式下 OneDragon 自行完成"点击进入游戏"（用户实测可用，无需任何虚拟手柄）

    def wait(self, timeout_s: int) -> bool:
        if self.proc is None:
            return False
        try:
            self.proc.wait(timeout=timeout_s)
            code = self.proc.returncode
            log.info("[zzz] OneDragon 退出 code=%s", code)
            if code != 0:
                # 2026-09-13：CLI 以 0xC000013A 退出（识别失败后自己中止），旧代码一律当成功上报
                log.error("[zzz] OneDragon 非正常退出（code=%s），本次判定为失败", code)
                return False
            return True
        except subprocess.TimeoutExpired:
            log.warning("[zzz] OneDragon 超时(%ss)，终止", timeout_s)
            return False

    def after_kill(self):
        """auto_daily 在"游戏已彻底退出"之后调用：换回用户自己的显示档（全屏）。"""
        swap_display_profile(self.cfg, "fullscreen")

    def stop(self):
        if self._guard is not None:
            self._guard.set()
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
