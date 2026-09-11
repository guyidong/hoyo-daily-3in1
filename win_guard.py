# -*- coding: utf-8 -*-
# win_guard.py —— 游戏窗口兜底：保证引擎跑的时候游戏是 16:9 窗口（1080p 基准）
#
# 为什么需要它：三个引擎（BetterGI / March7th / OneDragon）都以 1920x1080 为基准做识别，
# 正常路径是脚本改注册表把游戏切成 1920x1080 窗口化。但实测米哈游客户端有时不认注册表
# （尤其绝区零有两套显示键），结果游戏以 2560x1600 全屏(16:10)起来，识别会不稳。
# 这里在引擎启动后盯着游戏窗口：客户区不是 16:9 就按客户区拉成 1920x1080。
# 只做这一件事，失败只记日志，绝不打断主流程。
import ctypes
import ctypes.wintypes as wt
import logging
import subprocess
import time

log = logging.getLogger("auto_daily")

GAME_EXE = {"genshin": "YuanShen.exe", "starrail": "StarRail.exe", "zzz": "ZenlessZoneZero.exe"}


def _dpi_aware() -> None:
    """必须的：不开 DPI 感知的话，坐标会被系统按缩放虚拟化（150% 缩放下 1920 会变成 2880）。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


_dpi_aware()
u = ctypes.windll.user32


def _run(cmd):
    return subprocess.run(cmd, capture_output=True,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _client_size(hwnd):
    rc = wt.RECT()
    if not u.GetClientRect(hwnd, ctypes.byref(rc)):
        return None
    return (rc.right - rc.left, rc.bottom - rc.top)


def find_window(exe_name: str):
    """按进程名找客户区最大的可见窗口，返回 (hwnd, 客户区宽高)。"""
    r = _run(["tasklist", "/FI", "IMAGENAME eq " + exe_name, "/FO", "CSV", "/NH"])
    txt = (r.stdout or b"").decode("utf-8", "ignore")
    pids = set()
    for line in txt.splitlines():
        parts = [p.strip().strip(chr(34)) for p in line.split(",")]
        if len(parts) >= 2 and parts[1].isdigit():
            pids.add(int(parts[1]))
    if not pids:
        return None, None
    found = []

    def cb(hwnd, _lp):
        pid = wt.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and u.IsWindowVisible(hwnd):
            size = _client_size(hwnd)
            if size and size[0] > 640 and size[1] > 480:
                found.append((hwnd, size[0], size[1]))
        return True

    u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)(cb), 0)
    if not found:
        return None, None
    found.sort(key=lambda t: t[1] * t[2], reverse=True)
    return found[0][0], (found[0][1], found[0][2])


def is_16_9(size, tol: float = 0.02) -> bool:
    """客户区宽高比是不是 16:9（允许 2% 误差）。"""
    if not size:
        return False
    w, h = size
    return abs(w / float(h) - 16 / 9.0) < tol


def set_client_size(hwnd, w: int, h: int):
    """把窗口的【客户区】设成 w×h（自动算上边框/标题栏）。"""
    style = u.GetWindowLongW(hwnd, -16)
    exstyle = u.GetWindowLongW(hwnd, -20)
    rc = wt.RECT(0, 0, w, h)
    u.AdjustWindowRectEx(ctypes.byref(rc), style, False, exstyle)
    ow, oh = rc.right - rc.left, rc.bottom - rc.top
    SWP_NOZORDER, SWP_SHOWWINDOW = 0x0004, 0x0040
    return u.SetWindowPos(hwnd, 0, 0, 0, ow, oh, SWP_NOZORDER | SWP_SHOWWINDOW)


def ensure_16_9(game: str, target=(1920, 1080), wait_s: int = 300, watch_s: int = 180) -> bool:
    """等游戏窗口出现；客户区不是 16:9 就拉成 target（默认客户区 1920x1080）。返回是否已是 16:9。"""
    exe = GAME_EXE.get(game, game)
    t0 = time.time()
    hwnd = None
    while time.time() - t0 < wait_s:
        hwnd, _size = find_window(exe)
        if hwnd:
            break
        time.sleep(3)
    if not hwnd:
        log.warning("[%s] 等了 %ss 没看到游戏窗口，跳过窗口兜底", game, wait_s)
        return False
    end = time.time() + watch_s
    complained = False
    while True:
        hwnd, size = find_window(exe)
        if hwnd and is_16_9(size):
            if not complained:
                log.info("[%s] 游戏窗口客户区 %dx%d 是 16:9 ✔", game, size[0], size[1])
            return True
        if hwnd:
            if not complained:
                log.warning("[%s] 游戏窗口客户区 %sx%s 不是 16:9（注册表没生效），拉成 %dx%d",
                            game, size[0], size[1], target[0], target[1])
            rc = set_client_size(hwnd, target[0], target[1])
            time.sleep(4)
            _h2, size2 = find_window(exe)
            if is_16_9(size2):
                log.info("[%s] 已拉成客户区 %dx%d ✔（SetWindowPos=%s）", game, size2[0], size2[1], rc)
                return True
            if not complained:
                log.warning("[%s] 拉窗口未生效（SetWindowPos=%s，当前客户区 %s），继续交给引擎尝试", game, rc, size2)
            complained = True
        if time.time() > end:
            return False
        time.sleep(15)


def watch_async(game: str, target=(1920, 1080), wait_s: int = 300, watch_s: int = 180) -> None:
    """后台线程跑 ensure_16_9，不阻塞主流程。"""
    import threading
    threading.Thread(target=ensure_16_9, args=(game, target, wait_s, watch_s), daemon=True).start()
