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
import datetime
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


def kill_game_if_running(client_path: str = "") -> None:
    """关掉原神客户端并等它彻底退出（让 BetterGI 自己走"打开游戏 → 进游戏"的完整流程）。"""
    exe = Path(client_path).name if client_path else "YuanShen.exe"
    try:
        _run_hidden(["taskkill", "/IM", exe, "/F"], capture_output=True)
        for _ in range(30):
            r = _run_hidden(["tasklist", "/FI", "IMAGENAME eq " + exe], capture_output=True, text=True)
            if exe.lower() not in (r.stdout or "").lower():
                return
            time.sleep(1)
    except Exception as e:
        log.warning("[BetterGI] 关闭游戏失败: %s", e)


LEYLINE_ASSET = BETTERGI_DIR / "GameTask" / "AutoLeyLineOutcrop" / "Assets" / "config.json"


def _leyline_supported_countries() -> list:
    """当前 BetterGI 的地脉花任务支持哪些国家（读它的资源文件，别猜）。

    键名跨版本变过：0.64 用 mapPositions，0.66 改成 leyLinePositions（并新增了"至冬"）。"""
    try:
        data = json.loads(LEYLINE_ASSET.read_text(encoding="utf-8-sig"))
    except Exception:
        return []
    for key in ("leyLinePositions", "mapPositions"):
        val = data.get(key)
        if isinstance(val, dict) and val:
            return list(val.keys())
    return []


def _check_leyline_countries(data: dict) -> None:
    """地脉花按"星期X + 国家"配置。若配的国家当前版本不支持，任务会直接失败
    （实测报 "冒险之证未找到国家: 至冬"），这里提前把话说清楚。"""
    supported = _leyline_supported_countries()
    if not supported:
        return
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    bad = []
    enabled_day = False
    for d in days:
        if data.get("LeyLineRun" + d):
            enabled_day = True
            c = (data.get("LeyLine%sCountry" % d) or "").strip()
            if c and c not in supported:
                bad.append("%s=%s" % (d, c))
    if bad:
        log.warning("[BetterGI] 地脉花配的国家当前版本不支持，会直接失败: %s（本版本支持: %s）",
                    ", ".join(bad), "/".join(supported))
    elif enabled_day:
        log.info("[BetterGI] 地脉花国家检查通过（%s）", "/".join(supported))


DAY_ALIASES = {
    "monday": 0, "mon": 0, "周一": 0, "星期一": 0, "1": 0,
    "tuesday": 1, "tue": 1, "周二": 1, "星期二": 1, "2": 1,
    "wednesday": 2, "wed": 2, "周三": 2, "星期三": 2, "3": 2,
    "thursday": 3, "thu": 3, "周四": 3, "星期四": 3, "4": 3,
    "friday": 4, "fri": 4, "周五": 4, "星期五": 4, "5": 4,
    "saturday": 5, "sat": 5, "周六": 5, "星期六": 5, "6": 5,
    "sunday": 6, "sun": 6, "周日": 6, "周天": 6, "星期日": 6, "星期天": 6, "0": 6, "7": 6,
}


def _parse_days(days) -> set:
    """把 config 里的 days 解析成 weekday 集合（0=周一 … 6=周日）。"
    支持 ["Friday","周六"] / "周五~周日" / "5-6" 这几种写法。"""
    if not days:
        return set()
    items = days if isinstance(days, (list, tuple)) else [days]
    out = set()
    for it in items:
        s = str(it).strip()
        if not s:
            continue
        for sep in ("~", "～", "到", "-"):
            if sep in s:
                a, _, b = s.partition(sep)
                ka, kb = DAY_ALIASES.get(a.strip().lower()), DAY_ALIASES.get(b.strip().lower())
                if ka is not None and kb is not None:
                    d = ka
                    while True:
                        out.add(d)
                        if d == kb:
                            break
                        d = (d + 1) % 7
                    break
            else:
                k = DAY_ALIASES.get(s.lower())
                if k is not None:
                    out.add(k)
    return out


def _day_ok(t: dict) -> bool:
    """这个 target 配了 days 就只在那些天生效；没配 = 每天生效。"""
    days = _parse_days(t.get("days"))
    if not days:
        return True
    return datetime.date.today().weekday() in days


def _find_target(cfg: dict, *keys):
    for t in (cfg.get("targets") or []):
        name = "%s%s" % (t.get("name") or "", t.get("mission_name") or "")
        if any(k in name for k in keys):
            return t
    return None


def _task_switches(cfg: dict, target: dict) -> dict:
    """返回"我们要写进 daily.json 的任务开关"。**没提到的任务一律不写**。

    优先级规则（用户 2026-10-10 明确要求：界面里配的应该算数）：
      1) 秘境（自动秘境）：按当前 target 的 `days` 规则开/关 —— 这是你要求的按天轮换；
      2) 额外任务（幽境危战 / 地脉花 / 首领讨伐）：只有配置里**显式写了 true/false** 才由我们控制；
         写 `enabled: ui`（默认）或干脆不写这一条 → 完全听 BetterGI 界面的（你在界面里关掉就关掉）；
      3) 日常四项（邮件 / 合成树脂 / 每日奖励 / 尘歌壶）：不由我们写，界面里怎么勾就怎么跑。"""
    sw = {
        # 圣遗物本：配了 days 就按天开（例如只在周五~周日打）
        "自动秘境": bool(target.get("enabled", True)) and _day_ok(target),
    }
    for name, keys in (("自动幽境危战", ("幽境",)),
                       ("自动地脉花", ("地脉",)),
                       ("自动首领讨伐", ("首领",))):
        t = _find_target(cfg, *keys)
        if t is None:
            continue
        val = t.get("enabled", "ui")
        if isinstance(val, str):
            log.info("[BetterGI] %s 的开关听 BetterGI 界面（配置里是 enabled: %s）", name, val)
            continue
        sw[name] = bool(val) and _day_ok(t)
    return sw


def make_one_dragon_config(game_key: str, cfg: dict, target: dict, batch: str, idx: int,
                           switch_override: dict = None) -> Path:
    """生成/更新持久化一条龙配置（daily.json）。用户手动改过的非任务字段会被保留。

    switch_override：临时覆盖某些任务开关（例如"第 1 趟先别打地脉花/秘境"）。"""
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
    # ★ 优先级：BetterGI 界面 > 我们的配置。界面里填了就用界面的，界面空着才用配置里的值。
    if (data.get("PartyName") or "").strip():
        log.info("[BetterGI] 队伍用界面里设的: %s（配置里的 %s 本次不用）",
                 data["PartyName"], cfg.get("party_name", ""))
    else:
        data["PartyName"] = cfg.get("party_name", "")
    today = datetime.date.today().strftime("%A")   # Monday / Tuesday ...
    weekly = (data.get("%sDomainName" % today) or "").strip() if data.get("WeeklyDomainEnabled") else ""
    if weekly:
        log.info("[BetterGI] 每周秘境生效：%s = %s（默认秘境 %s 本次不用）", today, weekly, data.get("DomainName"))
    elif (data.get("DomainName") or "").strip():
        log.info("[BetterGI] 默认秘境用界面里设的: %s（配置里的 %s 本次不用）", data["DomainName"], domain)
    else:
        data["DomainName"] = domain
    # ★ 任务开关必须按 BetterGI 自己的 GUID 键写：TaskDefinitions 是 {GUID: 任务名}。
    #   写成中文名会被 BetterGI 的既有 TaskEnabledList 覆盖，开关等于没生效（实测踩坑）。
    defs = data.get("TaskDefinitions") or {}
    guid_of = {v: k for k, v in defs.items()}
    tel = data.get("TaskEnabledList") or {}
    switches = _task_switches(cfg, target)
    if switch_override:
        switches.update(switch_override)
    for name, want in switches.items():
        g = guid_of.get(name)
        if g:
            tel[g] = want   # 只写 BetterGI 认得的任务，避免留下无对应任务的野键
    data["TaskEnabledList"] = tel
    path = ONE_DRAGON_CONFIG
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("一条龙配置(可编辑/常驻): %s", path)
    _check_leyline_countries(data)
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

# "幽境危战真的打了"的痕迹（只有活动开着并进战斗才会出现）
STYGIAN_RAN_MARKS = ("自动幽境危战：执行战斗策略", "自动幽境危战：挑战成功",
                     "自动幽境危战：选择Boss并开始挑战")
# "活动没开"的痕迹
STYGIAN_CLOSED_MARKS = ("活动已结束", "紊乱爆发期已结束", "未开启")


def _stygian_ran(text: str) -> bool:
    """本次日志里幽境危战是否真的开打了（= 活动在奖励周期内）。"""
    return any(m in text for m in STYGIAN_RAN_MARKS)


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
    # 兜底：启用了"要花树脂"的任务，日志里却一点树脂都没花 → 大声报警
    #（10/10 就是这么静悄悄失败的：秘境进了、F 按了、然后没了，树脂 0 消耗）
    wanted = ("自动秘境任务：执行" in text) or ("自动地脉花" in text) or ("自动幽境危战" in text)
    # 注意引号可能是全角/弯引号，用正则匹配更稳
    spent = re.search(r"使用\s*[“\"”]?(原粹|浓缩|脆弱|须臾)树脂", text) is not None
    if wanted and not spent:
        log.warning("[BetterGI] ⚠ 本次启用了要花树脂的任务，但日志里没有任何树脂消耗 —— "
                    "大概率没跑成（去看 BetterGI 日志的最后一屏）")


def _log_has_mark() -> bool:
    """本次运行以来的日志里有没有完成标记。"""
    return COMPLETE_MARK in _new_log_text()


class BetterGIAdapter:
    """原神适配器：多个目标按顺序各自跑一条龙（共享常驻配置 daily.json，逐目标改任务）。"""

    def __init__(self, cfg, targets):
        self.cfg = cfg
        # switch_only 的条目（幽境危战 / 地脉花 / 首领讨伐）只是"开哪个任务"的开关，
        # 不是秘境：不能拿它们去起一条龙（否则会多跑几趟、还把 DomainName 写成"地脉花"）。
        self.targets = [t for t in targets if t.get("enabled", True) and not t.get("switch_only")]
        if not self.targets:
            log.warning("[BetterGI] 配置里没有可跑的秘境 target（只剩 switch_only 条目）；"
                        "一条龙至少要有一个秘境才能触发整轮任务")
        self.batch = "daily_" + time.strftime("%Y%m%d_%H%M%S")
        self.proc = None
        self.finished = False
        self.failed = False
        self._guard = None

    def _run_one_dragon(self, target: dict, timeout_s: int, idx: int, override: dict = None) -> None:
        """起一趟一条龙并等它跑完。"""
        cfg_path = make_one_dragon_config("genshin", self.cfg, target, self.batch, idx,
                                          switch_override=override)
        name = cfg_path.stem
        log.info("[BetterGI] 启动一条龙配置 %s（秘境=%s）", name, target["name"])
        _launch(BETTERGI_EXE, f"startOneDragon {name}", BETTERGI_DIR)
        # 窗口兜底：整场盯着游戏窗口（只起一次，多趟复用同一个监视线程）
        if self._guard is None:
            self._guard = win_guard.watch_async("genshin")
        if not self._wait_exit(timeout_s=timeout_s):
            log.error("[BetterGI] 配置 %s 超时（本次视为失败，别再报 ok=True）", name)
            self.failed = True

    def start(self):
        kill_bettergi_if_running()   # 保证单实例（startOneDragon 参数走激活流程）
        kill_game_if_running(self.cfg.get("client_path", ""))   # 先关游戏：让它自己从登录界面进游戏，
                                     # 否则用户把游戏停在某个界面时 BetterGI 会一直等（实测 10/3-10/5 空等一小时）
        _log_baseline()              # 记日志基线：只看本次新增的内容
        if not self.targets:
            self.finished = True
            return
        t = self.targets[0]                      # 秘境 target（switch_only 的条目已在 __init__ 过滤掉）
        timeout_s = int(t.get("timeout_minutes", 60) * 60)

        # 一趟跑完就好：一条龙里"幽境危战"排在 地脉花/秘境 之前（BetterGI 自带 TaskOrder），
        # 危战开着时它先吃树脂；没开时 BetterGI 自己跳过，接着按当天规则打地脉花/秘境。
        # ※ 曾为了"树脂全给危战"拆成两趟（第二趟重开游戏），10/10 实测第二趟进至冬秘境按 F 之后
        #   BetterGI 直接没了、树脂一点没花 —— 换新游戏会话风险太大，回退单趟。
        self._run_one_dragon(t, timeout_s, 0, None)
        # 【不删除】daily.json 常驻，用户可在 BetterGI UI 中直接编辑
        self.finished = True

    def wait(self, timeout_s: int) -> bool:
        """auto_daily 契约接口：等待引擎完成。
        注意：start() 内部已按 target 顺序等待完毕（self.finished），此处不得再等一次，
        否则会第二次看到"进程早已退出"而误报启动失败。"""
        if self.finished:
            return not self.failed      # 超时过就报失败（以前一律 True，把空跑一小时报成成功）
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
