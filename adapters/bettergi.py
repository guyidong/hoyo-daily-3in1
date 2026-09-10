# -*- coding: utf-8 -*-
# BetterGI（原神）引擎适配器
# 原理：
#   1) 按 daily_config 目标生成一条龙配置 -> BetterGI/User/OneDragon/daily.json（固定名，常驻可编辑）
#      （任务开关：领取邮件/合成树脂/自动秘境/领取每日奖励/领取尘歌壶；自动幽境危战=关）
#   2) 启动 BetterGI.exe startOneDragon daily
#      - 若本进程已是管理员（定时任务 /RL HIGHEST 场景）→ 直接启动，【无 UAC 弹窗】
#      - 否则用 runas 提权（手动运行场景，弹一次 UAC）
#   3) 配置 CompletionAction="关闭游戏和软件"，BetterGI 跑完会自动关游戏并退出自身
import ctypes
import json
import re
import subprocess
def _run_hidden(cmd, **kw):
    """静默子进程：无控制台窗口（提权 pythonw 下反复闪终端的问题修复）。"""
    kw.setdefault("capture_output", True)
    if not kw.get("creationflags"):
        # Windows: CREATE_NO_WINDOW
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(cmd, **kw)

import time
import logging
from pathlib import Path

log = logging.getLogger("auto_daily")

BASE = Path(__file__).resolve().parent.parent
# 可用环境变量 HOYO_BETTERGI_DIR 覆盖；默认约定：<项目根>/tools/BetterGI/BetterGI
BETTERGI_DIR = Path(os.environ.get("HOYO_BETTERGI_DIR") or (BASE / "tools" / "BetterGI" / "BetterGI"))
BETTERGI_EXE = BETTERGI_DIR / "BetterGI.exe"
ONEDRAGON_DIR = BETTERGI_DIR / "User" / "OneDragon"
LOG_DIR = BETTERGI_DIR / "User" / "log"
ONE_DRAGON_CONFIG = ONEDRAGON_DIR / "daily.json"   # 固定配置名：可编辑、不删除
COMPLETE_MARK = "一条龙和配置组任务结束"


def _is_admin() -> bool:
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def _launch(exe: Path, args: str, cwd: Path) -> None:
    """提权感知启动：已是管理员直接启动（无 UAC），否则 runas（弹一次 UAC）。"""
    if _is_admin():
        subprocess.Popen([str(exe), args], cwd=str(cwd))
        log.info("[BetterGI] 管理员上下文中直接启动（无 UAC）")
    else:
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", str(exe), args, str(cwd), 1)
        log.info("[BetterGI] 非提权上下文 runas 启动 rc=%s（UAC 一次，>32=成功）", rc)


def kill_bettergi_if_running():
    _run_hidden(["taskkill", "/IM", "BetterGI.exe", "/F"], capture_output=True)
    time.sleep(1)


def make_one_dragon_config(game_key: str, cfg: dict, target: dict, batch: str, idx: int) -> Path:
    """生成/更新持久化一条龙配置（daily.json）。若用户已手动编辑过，仅更新任务相关字段。"""
    domain = target["name"]
    defaults = {
        "Name": "daily",
        "TaskEnabledList": {
            "领取邮件": True,
            "合成树脂": True,
            "自动秘境": True,
            "自动幽境危战": bool(target.get("auto_stygian", False)),   # 默认关（用户手动打）
            "领取每日奖励": True,
            "领取尘歌壶奖励": True,
        },
        "CraftingBenchCountry": "枫丹",
        "AdventurersGuildCountry": "枫丹",
        "PartyName": cfg.get("party_name", ""),
        "DomainName": domain,
        "WeeklyDomainEnabled": False,
        "DailyRewardPartyName": "",
        "MinResinToKeep": 0,
        "SundayEverySelectedValue": "0",
        "SundaySelectedValue": "0",
        "SereniteaPotTpType": "地图传送",
        "SecretTreasureObjects": [],
        "CompletionAction": "关闭游戏和软件",
    }
    # 用户编辑优先：已有文件则保留用户改过的值，仅刷新任务相关字段
    if ONE_DRAGON_CONFIG.exists():
        try:
            data = json.loads(ONE_DRAGON_CONFIG.read_text(encoding="utf-8"))
            defaults.update(data)
        except Exception:
            pass
        defaults["PartyName"] = cfg.get("party_name", defaults.get("PartyName", ""))
        defaults["DomainName"] = domain
        defaults["TaskEnabledList"].update(defaults.get("TaskEnabledList", {}))
    path = ONE_DRAGON_CONFIG
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(defaults, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("一条龙配置(可编辑/常驻): %s", path)
    return path


def _tail_text(f, max_bytes: int = 300_000) -> str:
    """只读文件尾部（避免整读大日志导致主流程卡死）。"""
    try:
        size = f.stat().st_size
        with open(f, "rb") as fp:
            if size > max_bytes:
                fp.seek(size - max_bytes)
            data = fp.read()
        return data.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _log_has_mark() -> bool:
    if not LOG_DIR.exists():
        return False
    for f in sorted(LOG_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)[:3]:
        try:
            text = _tail_text(f)
            if COMPLETE_MARK in text:
                return True
        except Exception:
            pass
    return False


class BetterGIAdapter:
    """原神适配器：多个目标按顺序各自跑一条龙（共享常驻配置 daily.json，逐目标改任务）。"""

    def __init__(self, cfg, targets):
        self.cfg = cfg
        self.targets = [t for t in targets if t.get("enabled", True)]
        self.batch = "daily_" + time.strftime("%Y%m%d_%H%M%S")
        self.proc = None
        self.finished = False

    def start(self):
        kill_bettergi_if_running()   # 保证单实例（startOneDragon 参数走激活流程）
        for idx, t in enumerate(self.targets):
            cfg_path = make_one_dragon_config("genshin", self.cfg, t, self.batch, idx)
            name = cfg_path.stem
            log.info("[BetterGI] 启动一条龙配置 %s（秘境=%s）", name, t["name"])
            _launch(BETTERGI_EXE, f"startOneDragon {name}", BETTERGI_DIR)
            # 等本次 BetterGI 实例跑完并退出（CompletionAction=关闭游戏和软件）
            if not self._wait_exit(timeout_s=int(t.get("timeout_minutes", 60) * 60)):
                log.warning("[BetterGI] 配置 %s 超时，跳过", name)
            # 【不删除】daily.json 常驻，用户可在 BetterGI UI 中直接编辑
        self.finished = True

    def wait(self, timeout_s: int) -> bool:
        """auto_daily 契约接口：等待引擎完成。
        注意：start() 内部已按 target 顺序等待完毕（self.finished），此处不得再等一次，
        否则会第二次看到"进程早已退出"而误报启动失败。"""
        if self.finished:
            return True
        return self._wait_exit(timeout_s)

    def stop(self):
        """auto_daily 契约接口：清理。"""
        kill_bettergi_if_running()

    def _wait_exit(self, timeout_s: int) -> bool:
        """等待引擎完成。修复：runas 是异步的，若进程还未出现就判'完成'会误杀刚拉起的引擎。
        逻辑：启动宽限期内等待进程出现(seen=True)；只有 seen=True 后进程消失(或出现完成标记)才算完成。"""
        def _running() -> bool:
            r = _run_hidden(["tasklist", "/FI", "IMAGENAME eq BetterGI.exe"],
                               capture_output=True, text=True)
            return "BetterGI.exe" in r.stdout

        end = time.time() + timeout_s
        start = time.time()
        seen = False
        last_report = 0.0
        while time.time() < end:
            running = _running()
            if running:
                seen = True
            elif seen:
                return True                       # 出现过又消失 = 完成
            elif time.time() - start > 60:
                log.warning("[BetterGI] 60 秒内未见引擎进程，判定启动失败")
                return False
            if seen and _log_has_mark():
                for _ in range(30):
                    time.sleep(2)
                    if not _running():
                        return True
                return True
            now = time.time()
            if now - last_report > 30:
                last_report = now
                log.info("[BetterGI] 等待引擎完成中… seen=%s", seen)
            time.sleep(3)
        return False
