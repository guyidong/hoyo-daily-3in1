# -*- coding: utf-8 -*-
# auto_daily.py —— 米哈游三游每日自动化总控（配置驱动）
# 流程（每款游戏）：
#   1) 读取 config/daily_config.yaml 中该游戏的开关与目标列表
#   2) display_mode 快照当前屏幕模式（用户的全屏设置）
#   3) 自动切成 1920x1080 窗口化（若 auto_switch_window=true）
#   4) 启动游戏客户端 + 对应引擎（BetterGI / StarRailCopilot / OneDragon）
#   5) 引擎执行每日任务（委托、领月卡、凯瑟琳、清体力/燃料/电量…按 targets 配置）
#   6) 结束后退出游戏，并【总是】恢复用户原来的屏幕模式（异常也恢复）
# 用法：
#   python auto_daily.py                 # 按配置跑（全部启用的游戏）
#   python auto_daily.py genshin         # 只跑指定游戏
#   python auto_daily.py --dry-run       # 只打印计划，不真正执行
import sys, os, time, json, subprocess, logging
from pathlib import Path


def _run_hidden(cmd, **kw):
    """静默子进程：无控制台窗口（提权 pythonw 下反复闪终端的问题修复）。"""
    kw.setdefault("capture_output", True)
    if not kw.get("creationflags"):
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(cmd, **kw)

# 控制台/管道统一 UTF-8，避免中文在 cp1252 环境打印崩溃
if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr is not None and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parent
CONFIG = BASE / "config" / "daily_config.yaml"
LOG_DIR = BASE / "logs"
LOCK_FILE = BASE / "_auto_daily.lock"


def _acquire_single_instance_lock() -> bool:
    """单实例锁：已有实例在运行时直接退出（防止同时启动多个游戏/工具打架）。"""
    try:
        if LOCK_FILE.exists():
            try:
                old = int(LOCK_FILE.read_text(encoding="utf-8").strip() or "0")
            except Exception:
                old = 0
            if old:
                import ctypes
                h = ctypes.windll.kernel32.OpenProcess(0x1000, False, old)
                if h:
                    ctypes.windll.kernel32.CloseHandle(h)
                    return False   # 旧实例仍活着
        LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")
        return True
    except Exception:
        return True

import yaml
import display_mode

log = logging.getLogger("auto_daily")


def setup_log():
    LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_DIR / "daily.log", encoding="utf-8"),
                  logging.StreamHandler()])


def load_config():
    with open(CONFIG, "r", encoding="utf-8") as fp:
        return yaml.safe_load(fp)


# ---------------- 每款游戏的"引擎适配器" ----------------
# 职责：启动引擎并让它按目标执行。以子进程方式启动引擎，并监控其存活/日志，
# 直到完成或超时。具体驱动方式待源码分析后填充（不同引擎不同）。
class EngineAdapter:
    game = "?"
    engine_desc = "?"

    def __init__(self, cfg, targets):
        self.cfg = cfg
        self.targets = targets          # 已启用的目标列表 [{name, runs}]

    def start(self):
        raise NotImplementedError

    def wait(self, timeout_s):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError


# ---------------- 通用运行逻辑 ----------------
def run_game(game_key, games_cfg, dry_run=False):
    g = games_cfg.get(game_key)
    if not g or not g.get("enabled", True):
        print(f"[skip] {game_key}: disabled")
        return "skip"
    targets = [t for t in g.get("targets", []) if t.get("enabled", True)]
    def _label(t):
        n = t.get("name") or t.get("mission_name") or t.get("mission_type") or "?"
        return f"{n}x{t.get('runs', 0)}"
    print(f"[plan] {game_key} ({g.get('name')}): targets={[_label(t) for t in targets]}")
    if dry_run:
        return "dry-run"

    disp = g.get("display", {})
    snap = None
    adapter = build_adapter(game_key, g, targets)
    try:
        if disp.get("auto_switch_window", True):
            snap = display_mode.snapshot(game_key)
            display_mode.set_windowed(game_key,
                                      int(disp.get("auto_width", 1920)),
                                      int(disp.get("auto_height", 1080)))
            log.info("[%s] 已切换为窗口化", game_key)
        adapter.start()
        log.info("[%s] 引擎已启动，等待完成…", game_key)
        ok = adapter.wait(timeout_s=int(g.get("timeout_minutes", 90) * 60))
        log.info("[%s] 执行结束 ok=%s", game_key, ok)
        return "ok" if ok else "timeout"
    except Exception as e:
        log.exception("[%s] 执行异常: %s", game_key, e)
        return "error"
    finally:
        try:
            adapter.stop()
        except Exception:
            pass
        if snap is not None:
            display_mode.restore(game_key, snap)
            log.info("[%s] 已恢复用户原屏幕模式", game_key)
        # 结束游戏进程（如果还在）
        kill_game(g.get("client_path"))


def kill_game(client_path):
    """"强制结束游戏并等待进程彻底退出（游戏退出瞬间会回写注册表，必须等它死透再恢复显示）"""
    try:
        exe = Path(client_path).name
        _run_hidden(["taskkill", "/IM", exe, "/F"])
        for _ in range(30):   # 最多等 30s
            r = _run_hidden(["tasklist", "/FI", f"IMAGENAME eq {exe}"], text=True)
            if exe not in r.stdout:
                break
            time.sleep(1)
    except Exception:
        pass


def build_adapter(game_key, g, targets):
    # 按引擎选择适配器（引擎具体驱动方式在源码分析后实现）
    if game_key == "genshin":
        from adapters.bettergi import BetterGIAdapter
        return BetterGIAdapter(g, targets)
    if game_key == "starrail":
        from adapters.starrail import StarRailAdapter
        return StarRailAdapter(g, targets)
    if game_key == "zzz":
        from adapters.zzz import ZZZAdapter
        return ZZZAdapter(g, targets)
    raise ValueError("未知游戏: " + game_key)


def main():
    setup_log()
    if not _acquire_single_instance_lock():
        log.warning("已有 auto_daily 实例在运行，本次退出（防止重复启动游戏）")
        return 0
    try:
        return _main_inner()
    finally:
        try:
            LOCK_FILE.unlink(missing_ok=True)
        except Exception:
            pass


def _main_inner():
    cfg = load_config()
    sched = cfg.get("scheduler", {})
    order = sched.get("run_order", ["genshin", "starrail", "zzz"])
    if len(sys.argv) >= 2 and sys.argv[1] in ("--dry-run", "-n"):
        dry = True
        games = order
    elif len(sys.argv) >= 2:
        dry = False
        games = [sys.argv[1]]
    else:
        dry = False
        games = order
    results = {}
    for game_key in games:
        results[game_key] = run_game(game_key, cfg.get("games", {}), dry_run=dry)
    print("==== 结果 ====")
    for k, v in results.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
