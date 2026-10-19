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
# 2026-10-14 补：内部任务失败的真实文案是 "自动地脉花执行失败" / "自动秘境执行失败"
#   （异常类型是 System.Exception，文案里是"执行失败"不是"执行异常"），
#   旧清单漏了它，于是 10-14 地脉花只打了 2 次就挂、daily.log 仍报"无失败特征"+ok=True。
FAIL_PATTERNS = ("游戏窗口分辨率不是 16:9", "识别出战角色失败", "任务启动失败",
                 "执行异常", "执行失败", "已达最大重试次数")

# 非致命但值得盯的"退化"特征：命中只 WARNING，不判失败。
# "放弃此路径点" 是 至冬 地脉花寻路出问题的强信号（历史上只在 10-14 出现过 4 次，
# 那天 6 次地脉花只完成 2 次）；偶尔放弃一个点还能自己恢复，所以不直接判失败。
# 2026-10-15 补 "战斗失败，重新寻找地脉花"：当天出现 3 次 —— 识别到队伍角色后
# "未完整匹配到当前队伍" → 退化成"万能战斗策略（萌新推荐）" → 白打一轮再重新找花。
# 2026-10-16 补 "未完整匹配到当前队伍"：同一个根因在【自动秘境】路径上的签名。
#   地脉花那条已在 10-15 加上，但秘境路径不走"重新寻找地脉花"，只留这一句，
#   于是"退化成通用策略"照样打通关（挑战达成）→ 既不判失败、也不出任何 WARNING，
#   汇总日志里完全隐形（10-16 当天 4 次、日志却写"检查通过：无失败特征"）。
#   10-07 起连续 10 天出现（4~9 次/天），加进这里只为让它在 daily.log 里显形，不改判定。
# 2026-10-18 补 "存在角色死亡"：当天第 4 趟秘境战斗中途阵亡，引擎自动传去七天神像
#   复活后重跑一次才领到奖励（多花约 2 分钟，树脂没损失）。进程正常退出、日志里也
#   没有任何 FAIL 特征，于是又一次变成"检查通过"完全隐形 —— 这趟其实重跑过。
#   加进软告警只为让"重跑过"在 daily.log 里留痕，不判失败（引擎自己救回来了）。
SOFT_WARN_PATTERNS = ("放弃此路径点", "距离过远", "战斗失败，重新寻找地脉花",
                      "未完整匹配到当前队伍", "存在角色死亡")

# 树脂不够时"卡在领奖页"的特征（2026-10-15 首次出现，21 天日志里零先例）。
# 固定次数模式(LeyLineResinExhaustionMode=false)不会自己停：树脂花光后照样去打第 N 次，
# 到领奖页找不到能点的树脂选项 → 重试 2 次 → "自动地脉花执行失败:无法领取奖励"。
# 当天 5 次 × 40 = 200（正好是树脂上限），第 6 次没钱付 —— 属于"次数配得比树脂多"，
# 不是寻路/脚本坏了，所以单独把 OCR 原文记下来，方便一眼分辨。
RESIN_BLOCKED_PATTERNS = ("奖励页面树脂识别结果未匹配成功", "无法领取奖励")

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


def _report_log_failures() -> bool:
    """扫本次运行日志里的失败特征，命中就在主日志里报 WARNING，并返回 True（=本次失败）。

    返回 True 时调用方会把本次运行判为失败，这样 daily.log 里不会再出现
    "引擎已退出 + ok=True" 的假成功。"""
    text = _new_log_text()
    if not text:
        return False
    failed = False
    hits = {p: text.count(p) for p in FAIL_PATTERNS if p in text}
    amounts = [int(m) for m in re.findall(
        r"使用\s*[“\"”]?(?:原粹|浓缩|脆弱|须臾)树脂[”\"’]?\s*[,，]?\s*数量[：:]\s*(\d+)", text)]
    # 2026-10-19：树脂花光导致的“执行失败”不是故障 —— 固定次数模式(LeyLineResinExhaustionMode=false)
    #   不会自己停：当天至冬地脉花领了 4 次（40+40+40+20=140 树脂），第 5 次到领奖页已经一粒树脂都没有
    #   （OCR 原文：“使用20个原粹树脂补充…今日还可使用6次”），引擎重试 2 次后报
    #   “自动地脉花执行失败:无法领取奖励”，被 FAIL_PATTERNS 里的“执行失败”整轮判成失败。
    #   10-18 报告早已预警：至冬 6 次 × 40 = 240 > 树脂上限(200)，周一~周四几乎必然触发。
    #   降级条件刻意收窄：必须“领奖页明确说树脂不够 + 本次确实花过树脂 + 失败特征只有执行失败”，
    #   这样“领奖页认不出新弹窗”“寻路卡死”之类的真故障仍然照旧判失败。
    resin_short = ("无法领取奖励" in text) and bool(amounts) and \
                  ("原粹树脂补充" in text or "今日还可使用" in text)
    expected = resin_short and set(hits) <= {"执行失败"}
    if hits and not expected:
        log.error("[BetterGI] 日志有失败特征（本次没跑完）: %s", hits)
        failed = True
    elif hits:
        log.warning("[BetterGI] 树脂已花光，固定次数打不满（预期，不判失败）: %s；"
                    "本次已花 %d 树脂（%s）—— 想打满请到 BetterGI 界面把地脉花次数调低",
                    hits, sum(amounts), "+".join(str(a) for a in amounts))
    else:
        log.info("[BetterGI] 日志检查通过：无失败特征")
    soft = {p: text.count(p) for p in SOFT_WARN_PATTERNS if p in text}
    if soft:
        log.warning("[BetterGI] 日志有寻路/重试退化特征（可能少刷了）: %s", soft)
    # 树脂不够导致"卡在领奖页"的诊断：把领奖页 OCR 原文也写进 daily.log，
    # 这样"无法领取奖励"到底是树脂花光了、还是引擎认不出新弹窗，一眼能分辨。
    blocked = {p: text.count(p) for p in RESIN_BLOCKED_PATTERNS if p in text}
    if blocked:
        _lvl = log.warning if expected else log.error
        _lvl("[BetterGI] ⚠ 领奖页选不出树脂（%s）—— 固定次数模式下树脂花光就会这样："
             "任务不会自己停，会一直重试到失败。对照下面『本次树脂消耗』，"
             "若已把树脂花光，就是树脂不够（不是寻路/脚本坏了）", blocked)
        m = re.search(r'奖励页面树脂识别结果未匹配成功，ocr="([^"]*)"', text)
        if m:
            _lvl("[BetterGI] 领奖页 OCR 原文: %s", m.group(1))
    # 树脂账：把"这次花掉多少树脂"直接写进 daily.log，省得每天翻引擎日志。
    # （10-13 是 0 树脂静静地失败，10-14 是只花了 80/120）
    spent = bool(amounts) or re.search(r"使用\s*[“\"”]?(原粹|浓缩|脆弱|须臾)树脂", text) is not None
    if amounts:
        log.info("[BetterGI] 本次树脂消耗: %d（%d 次领取: %s）", sum(amounts), len(amounts),
                 "+".join(str(a) for a in amounts))
    # 兜底：启用了"要花树脂"的任务，日志里却一点树脂都没花 → 大声报警
    #（10/10 就是这么静悄悄失败的：秘境进了、F 按了、然后没了，树脂 0 消耗）
    wanted = ("自动秘境任务：执行" in text) or ("自动地脉花" in text) or ("自动幽境危战" in text)
    if wanted and not spent:
        log.error("[BetterGI] ⚠ 本次启用了要花树脂的任务，但日志里没有任何树脂消耗 —— "
                  "大概率没跑成（去看 BetterGI 日志的最后一屏）")
        failed = True
    return failed


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

    def _finish_check(self) -> None:
        """引擎退出后的收尾判定：日志里有失败特征就把本次判为失败（不再是 ok=True）。"""
        if _report_log_failures():
            self.failed = True

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
                self._finish_check()              # 出现过又消失 = 完成（顺手检查内部有没有失败）
                return True
            elif time.time() - start > 60:
                log.warning("[BetterGI] 60 秒内未见引擎进程，判定启动失败")
                return False
            if seen and _log_has_mark():
                for _ in range(30):
                    time.sleep(2)
                    if not _running():
                        self._finish_check()
                        return True
                self._finish_check()
                return True
            now = time.time()
            if now - last_report > 30:
                last_report = now
                log.info("[BetterGI] 等待引擎完成中… seen=%s", seen)
            time.sleep(3)
        return False
