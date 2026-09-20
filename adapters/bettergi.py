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
import os
import re
import subprocess
import sys
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

BASE = Path(__file__).resolve().parent.parent          # 项目根目录（本文件在 adapters/ 下）
# 默认 <项目根>/tools/BetterGI/BetterGI；可用环境变量 HOYO_BETTERGI_DIR 覆盖
BETTERGI_DIR = Path(os.environ.get("HOYO_BETTERGI_DIR") or (BASE / "tools" / "BetterGI" / "BetterGI"))
sys.path.insert(0, str(BASE))   # 能 import 到项目根的模块
import win_guard   # noqa: E402  游戏窗口兜底（16:9 检查/纠正）
BETTERGI_EXE = BETTERGI_DIR / "BetterGI.exe"
ONEDRAGON_DIR = BETTERGI_DIR / "User" / "OneDragon"
# 日志在包根目录的 log/ 下（User/log 是旧路径，实测不存在 —— 之前完成标记因此一直没生效）
LOG_DIR = BETTERGI_DIR / "log"
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


def _task_switches(target: dict) -> dict:
    """一条龙任务开关（BetterGI 内置任务名 -> 是否启用）。幽境危战默认关（手动打）。"""
    return {
        "领取邮件": True,
        "合成树脂": True,
        "自动秘境": True,
        "自动幽境危战": bool(target.get("auto_stygian", False)),
        "领取每日奖励": True,
        "领取尘歌壶奖励": True,
        "自动首领讨伐": False,
    }


def make_one_dragon_config(game_key: str, cfg: dict, target: dict, batch: str, idx: int) -> Path:
    """生成/更新持久化一条龙配置（daily.json）。用户手动改过的非任务字段会被保留。"""
    domain = target["name"]
    settings = {
        "Name": "daily",
        "CraftingBenchCountry": "枫丹",
        "AdventurersGuildCountry": "枫丹",
        "WeeklyDomainEnabled": False,
        "DailyRewardPartyName": "",
        "MinResinToKeep": 0,
        "SundayEverySelectedValue": "0",
        "SundaySelectedValue": "0",
        "SereniteaPotTpType": "地图传送",
        "SecretTreasureObjects": [],
        "CompletionAction": "关闭游戏和软件",
    }
    data = {}
    if ONE_DRAGON_CONFIG.exists():
        try:
            data = json.loads(ONE_DRAGON_CONFIG.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    # 用户已改过的值优先，本适配器只覆写下面这几个字段
    for k, v in settings.items():
        data.setdefault(k, v)
    data["Name"] = "daily"
    data["PartyName"] = cfg.get("party_name", data.get("PartyName", ""))
    data["DomainName"] = domain
    # ★ 任务开关必须按 BetterGI 自己的 GUID 键写：TaskDefinitions 是 {GUID: 任务名}。
    #   写成中文名会被 BetterGI 的既有 TaskEnabledList 覆盖，开关等于没生效（实测踩坑）。
    defs = data.get("TaskDefinitions") or {}
    guid_of = {v: k for k, v in defs.items()}
    tel = data.get("TaskEnabledList") or {}
    for name, want in _task_switches(target).items():
        g = guid_of.get(name)
        if g:
            tel[g] = want   # 只写 BetterGI 认得的任务，避免留下无对应任务的野键
    data["TaskEnabledList"] = tel
    path = ONE_DRAGON_CONFIG
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
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


# 跑完要扫的"没跑成"特征。BetterGI 内部任务失败时进程照样正常退出，
# 不扫这些就会只看到 ok=True —— 2026-09-19/20 就是这样连挂两天没人发现。
FAIL_PATTERNS = ("游戏窗口分辨率不是 16:9", "识别出战角色失败", "任务启动失败", "执行异常")


_LOG_BASE = {"name": "", "size": 0}


def _log_baseline() -> None:
    """记下"本次启动前"日志写到哪了。只看之后新增的内容，
    否则会把上一次运行留下的完成标记/失败特征误当成这次的。"""
    try:
        f = sorted(LOG_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)[0]
        _LOG_BASE["name"], _LOG_BASE["size"] = f.name, f.stat().st_size
        log.info("[BetterGI] 日志基线: %s @ %d 字节", f.name, _LOG_BASE["size"])
    except Exception:
        _LOG_BASE["name"], _LOG_BASE["size"] = "", 0


def _new_log_text(max_bytes: int = 300_000) -> str:
    """本次运行以来新增的日志文本（跨天换文件时退化为读尾部）。"""
    try:
        files = sorted(LOG_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    except Exception:
        return ""
    if not files:
        return ""
    newest = files[0]
    if _LOG_BASE["name"] and newest.name == _LOG_BASE["name"]:
        try:
            with open(newest, "rb") as fp:
                fp.seek(min(_LOG_BASE["size"], newest.stat().st_size))
                return fp.read().decode("utf-8", errors="ignore")
        except Exception:
            return ""
    return _tail_text(newest, max_bytes)


def _report_log_failures() -> None:
    """扫本次运行日志里的失败特征，命中就在主日志里报 WARNING。"""
    text = _new_log_text()
    if not text:
        return
    hits = {p: text.count(p) for p in FAIL_PATTERNS if p in text}
    if hits:
        log.warning("[BetterGI] 日志有失败特征（本次可能没跑完）: %s", hits)
    else:
        log.info("[BetterGI] 日志检查通过：无失败特征")


def _log_has_mark() -> bool:
    """本次运行以来的日志里有没有完成标记。"""
    return COMPLETE_MARK in _new_log_text()


class BetterGIAdapter:
    """原神适配器：多个目标按顺序各自跑一条龙（共享常驻配置 daily.json，逐目标改任务）。"""

    def __init__(self, cfg, targets):
        self.cfg = cfg
        self.targets = [t for t in targets if t.get("enabled", True)]
        self.batch = "daily_" + time.strftime("%Y%m%d_%H%M%S")
        self.proc = None
        self.finished = False
        self._guard = None

    def start(self):
        kill_bettergi_if_running()   # 保证单实例（startOneDragon 参数走激活流程）
        _log_baseline()              # 记日志基线：只看本次新增的内容
        for idx, t in enumerate(self.targets):
            cfg_path = make_one_dragon_config("genshin", self.cfg, t, self.batch, idx)
            name = cfg_path.stem
            log.info("[BetterGI] 启动一条龙配置 %s（秘境=%s）", name, t["name"])
            _launch(BETTERGI_EXE, f"startOneDragon {name}", BETTERGI_DIR)
            # 窗口兜底：整场盯着游戏窗口（只起一次，多个 target 复用同一个监视线程）
            if self._guard is None:
                self._guard = win_guard.watch_async("genshin")
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
        if self._guard is not None:
            self._guard.set()
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
                _report_log_failures()            # 出现过又消失 = 完成（顺手检查内部有没有失败）
                return True
            elif time.time() - start > 60:
                log.warning("[BetterGI] 60 秒内未见引擎进程，判定启动失败")
                return False
            if seen and _log_has_mark():
                for _ in range(30):
                    time.sleep(2)
                    if not _running():
                        _report_log_failures()
                        return True
                _report_log_failures()
                return True
            now = time.time()
            if now - last_report > 30:
                last_report = now
                log.info("[BetterGI] 等待引擎完成中… seen=%s", seen)
            time.sleep(3)
        return False
