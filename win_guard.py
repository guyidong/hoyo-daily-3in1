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


def _monitor(game: str, target, wait_s: int, interval_s: int, duration_s: int, stop) -> None:
    """整场盯着游戏窗口：只要客户区不是 16:9 就拉回来。

    为什么不能"修一次就收工"：实测（2026-09-13 绝区零）游戏**先以 1920x1080 窗口起来**，
    进游戏后自己又切回 2560x1600 全屏，OneDragon 紧接着就 "未能识别当前画面" 全线失败。
    所以必须一直盯着，任何时候不对都拉回来。"""
    exe = GAME_EXE.get(game, game)
    t0 = time.time()
    fixed = 0
    seen = False
    while not stop.is_set() and time.time() - t0 < duration_s:
        hwnd, size = find_window(exe)
        if hwnd is None:
            if not seen and time.time() - t0 > wait_s:
                log.info("[%s] 窗口监视结束：%ss 内没看到游戏窗口", game, wait_s)
                return
        elif is_16_9(size):
            if not seen:
                log.info("[%s] 游戏窗口客户区 %dx%d 是 16:9 ✔（继续盯着）", game, size[0], size[1])
            seen = True
        else:
            seen = True
            fixed += 1
            log.warning("[%s] 游戏窗口客户区 %sx%s 不是 16:9，拉成 %dx%d（第 %d 次）",
                        game, size[0], size[1], target[0], target[1], fixed)
            rc = set_client_size(hwnd, target[0], target[1])
            time.sleep(3)
            _h2, size2 = find_window(exe)
            if is_16_9(size2):
                log.info("[%s] 已拉成客户区 %dx%d ✔（SetWindowPos=%s）", game, size2[0], size2[1], rc)
            else:
                log.warning("[%s] 拉窗口未生效（SetWindowPos=%s，当前客户区 %s）", game, rc, size2)
        stop.wait(interval_s)
    if stop.is_set() and seen:
        log.info("[%s] 窗口监视结束（引擎已退出），期间修正 %d 次", game, fixed)


def start_monitor(game: str, target=(1920, 1080), wait_s: int = 900, interval_s: int = 8,
                  duration_s: int = 21600):
    """后台起一个整场盯窗口的线程，返回 stop 事件（引擎结束时 set 它即可）。"""
    import threading
    stop = threading.Event()
    threading.Thread(target=_monitor, args=(game, target, wait_s, interval_s, duration_s, stop),
                     daemon=True).start()
    return stop


def watch_async(game: str, target=(1920, 1080), wait_s: int = 900):
    """兼容旧调用：起监视线程，返回 stop 事件。"""
    return start_monitor(game, target=target, wait_s=wait_s)
