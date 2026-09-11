# -*- coding: utf-8 -*-
# March7thAssistant（三月七小助手，崩铁）引擎适配器
# 原理：
#   1) 写 assets/config/config.yaml：清体力 power_plan、每日实训、奖励领取、游戏路径、自动分辨率
#   2) 启动 "March7th Assistant.exe main -e"（完整运行，成功后退出程序）
#   3) 轮询进程退出判定完成
import ctypes
import os
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

BASE = Path(__file__).resolve().parent.parent          # 项目根目录
sys.path.insert(0, str(BASE))                          # 能 import 到项目根的模块
import win_guard   # noqa: E402  游戏窗口兜底（16:9 检查/纠正）
# 部署后路径（下载解压 March7thAssistant_full.zip 到此）；可用 HOYO_MARCH7TH_DIR 覆盖
M7A_DIR = Path(os.environ.get("HOYO_MARCH7TH_DIR") or (BASE / "tools" / "March7thAssistant"))
CONFIG_FILE = M7A_DIR / "config.yaml"   # 运行时配置在包根目录（首次运行自动从 example 生成）
EXE = M7A_DIR / "March7th Assistant.exe"

# 副本类型 -> 默认实例名（游戏内准确名称，可与配置的 mission_name 覆盖）
DEFAULT_INSTANCE_NAMES = {
    "拟造花萼（金）": "回忆之蕾",
    "拟造花萼（赤）": "收容舱段",
    "凝滞虚影": "无",
    "侵蚀隧洞": "睿治之径",
    "饰品提取": "永恒笑剧",
    "历战余响": "毁灭的开端",
}


def write_config(cfg: dict, targets: list, game_path: str) -> None:
    """写 March7th 配置（关键项），不动其他用户设置。"""
    import yaml
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if CONFIG_FILE.exists():
        try:
            data = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}
            data = {k: v for k, v in data.items() if k is not None}
        except Exception:
            data = {}

    # 游戏路径（用户 F 盘桌面客户端）
    data["game_path"] = game_path
    data["game_process_name"] = "StarRail"
    data["game_title_name"] = "崩坏：星穹铁道"
    data["auto_set_game_path_enable"] = True
    data["auto_set_resolution_enable"] = True   # 启动时自动切 1920x1080 + 关 HDR

    # 清体力（用户自由入口 -> power_plan [[类型, 名称, 次数]]）
    plan = []
    for t in targets:
        typ = t["name"]
        name = t.get("mission_name") or DEFAULT_INSTANCE_NAMES.get(typ, "无")
        runs = int(t.get("runs", 0))  # 0 = 自动按开拓力计算直至清空
        plan.append([typ, name, runs])
    data["power_enable"] = True
    data["power_plan"] = plan
    data["power_plan_keep"] = False
    if plan:
        data["instance_type"] = plan[0][0]
        names = data.setdefault("instance_names", {})
        names[plan[0][0]] = plan[0][1]
    data["use_fuel"] = True          # 用燃料清体力（用户要求"清燃料"）
    data["echo_of_war_enable"] = True

    # 每日实训 + 奖励
    data["daily_enable"] = True
    data["daily_material_enable"] = True
    data["reward_enable"] = True
    data["reward_dispatch_enable"] = True   # 委托奖励
    data["reward_mail_enable"] = True
    data["reward_quest_enable"] = True      # 每日实训奖励
    data["reward_srpass_enable"] = True     # 无名勋礼奖励
    data["activity_dailycheckin_enable"] = True

    # 完成后动作：Exit=任务完成后退出程序（配合总控轮询进程退出）
    data["after_finish"] = "Exit"
    # 防回写崩溃：scheduled_time 必须为字符串（"4:00"），否则 timepickersettingcard 启动即崩
    if not isinstance(data.get("scheduled_time"), str):
        data["scheduled_time"] = "4:00"
    # 首次运行检查：CLI 要求先打开 GUI 同意免责声明（写入 auto_update）；由我们代为写入
    data["auto_update"] = True
    data["check_update"] = False
    data["pause_after_success"] = False

    CONFIG_FILE.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                           encoding="utf-8")


class StarRailAdapter:
    """崩铁适配器（三月七）。"""

    def __init__(self, cfg, targets):
        self.cfg = cfg
        self.targets = [t for t in targets if t.get("enabled", True)]
        self.proc = None

    def _is_admin(self) -> bool:
        try:
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            return False

    def start(self):
        if not EXE.exists():
            raise FileNotFoundError(f"未找到三月七主程序: {EXE}（请先下载 March7thAssistant_full.zip 并解压）")
        write_config(self.cfg, self.targets, self.cfg.get("client_path", ""))
        log.info("[March7th] 启动完整运行 main ...")
        # 提权感知启动：定时任务(/RL HIGHEST)已是管理员 -> 直接启动【无 UAC】；
        # 手动运行时非提权 -> runas（弹一次 UAC）。程序自带 pyuac，双保险一致。
        if self._is_admin():
            subprocess.Popen([str(EXE), "main"], cwd=str(M7A_DIR))
            log.info("[March7th] 管理员上下文中直接启动（无 UAC）")
        else:
            ctypes.windll.shell32.ShellExecuteW(None, "runas", str(EXE), "main", str(M7A_DIR), 1)
            log.info("[March7th] 非提权上下文 runas 启动（UAC 一次）")
        # 窗口兜底：注册表万一没生效，游戏以 16:10 全屏起来时把它拉成 1920x1080 窗口
        win_guard.watch_async("starrail")

    def _running(self) -> bool:
        r = _run_hidden(["tasklist", "/FI", "IMAGENAME eq March7th Assistant.exe"],
                           capture_output=True, text=True)
        return "March7th Assistant.exe" in r.stdout

    def wait(self, timeout_s: int = 7200):
        end = time.time() + timeout_s
        while time.time() < end:
            if not self._running():
                return True   # 进程退出 = 完成（after_finish=Exit）
            time.sleep(10)
        return False

    def stop(self):
        _run_hidden(["taskkill", "/IM", "March7th Assistant.exe", "/F"], capture_output=True)
        _run_hidden(["taskkill", "/IM", "March7thAssistant.exe", "/F"], capture_output=True)
