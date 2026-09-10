# -*- coding: utf-8 -*-
# display_mode.py —— 三款米哈游游戏 窗口化(1920x1080) ↔ 用户全屏 自动切换
# 原理：HoYoverse(Unity) 把分辨率/窗口模式存在注册表 HKCU\Software\miHoYo\<游戏> 的
#       Screenmanager 键里（Unity PlayerPrefs）。脚本在启动游戏前改注册表→游戏以窗口化 1920x1080 启动，
#       跑完后再把注册表恢复成用户原来的全屏/分辨率，不影响用户自己玩。
# 用法：
#   python display_mode.py <game> snapshot                打印当前屏幕设置（不修改）
#   python display_mode.py <game> set-windowed           设置成 1920x1080 窗口化
#   python display_mode.py <game> restore <snapshot.json> 恢复快照
import winreg, json, os, sys

# 每款游戏的：注册表路径 + 需要快照/修改的键（值名 -> 修改时的取值逻辑）
GAME_KEYS = {
    "genshin": {
        "reg": r"Software\miHoYo\原神",
        "width": "Screenmanager Resolution Width_h182942802",
        "height": "Screenmanager Resolution Height_h2627697771",
        "fullscreen": "Screenmanager Is Fullscreen mode_h3981298716",   # 0=窗口化 1=全屏
        "windowed_value": 0,
        "use_native": None,
        "extra": [],
    },
    "starrail": {
        "reg": r"Software\miHoYo\崩坏：星穹铁道",
        "width": "Screenmanager Resolution Width_h182942802",
        "height": "Screenmanager Resolution Height_h2627697771",
        "fullscreen": "Screenmanager Fullscreen mode_h3630240806",
        "windowed_value": 3,   # 此键语义：1=全屏 3=窗口化
        "use_native": "Screenmanager Resolution Use Native_h1405027254",
        # REG_BINARY UTF-8 JSON，形如 {"width":2560,"height":1600,"isFullScreen":true}\x00
        "extra": ["GraphicsSettings_PCResolution_h431323223"],
    },
    "zzz": {
        "reg": r"Software\miHoYo\绝区零",
        "width": "Screenmanager Resolution Width_h182942802",
        "height": "Screenmanager Resolution Height_h2627697771",
        "fullscreen": "Screenmanager Fullscreen mode_h3630240806",
        "windowed_value": 3,   # 此键语义：1=全屏 3=窗口化
        "use_native": "Screenmanager Resolution Use Native_h1405027254",
        "extra": [],
    },
}
AUTO_WIDTH, AUTO_HEIGHT = 1920, 1080   # 自动化用的窗口化分辨率（16:9，BetterGI 要求）


def _open(reg_path, access):
    return winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path, 0, access)


def read_value(reg_path, name, default=(None, None)):
    try:
        with _open(reg_path, winreg.KEY_READ) as k:
            value, typ = winreg.QueryValueEx(k, name)
            return value, typ
    except OSError:
        return default


def write_value(reg_path, name, value, typ):
    with _open(reg_path, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, name, 0, typ, value)


def _all_keys(game):
    g = GAME_KEYS[game]
    names = [g["width"], g["height"], g["fullscreen"]]
    if g["use_native"]:
        names.append(g["use_native"])
    names += g["extra"]
    return names


def snapshot(game):
    """读取当前屏幕设置（只读）。返回 {key_name: {"value":..,"type":..}}；
    二进制值以 hex 存储（json 安全）。"""
    g = GAME_KEYS[game]
    state = {}
    for name in _all_keys(game):
        value, typ = read_value(g["reg"], name)
        if isinstance(value, bytes):
            state[name] = {"value_hex": value.hex(), "type": typ}
        else:
            state[name] = {"value": value, "type": typ}
    return state


def _restore_value(name, info):
    if "value_hex" in info:
        return bytes.fromhex(info["value_hex"]), info["type"]
    return info.get("value"), info.get("type")


def _bytes_json(width, height, fullscreen):
    return (json.dumps({"width": width, "height": height, "isFullScreen": fullscreen},
                       separators=(",", ":")).encode("utf-8") + b"\x00")


def apply(game, width, height, fullscreen, use_native=None):
    """直接写入给定参数；fullscreen: True/False；use_native: True/False/None。"""
    g = GAME_KEYS[game]
    write_value(g["reg"], g["width"], width, winreg.REG_DWORD)
    write_value(g["reg"], g["height"], height, winreg.REG_DWORD)
    win_val = g.get("windowed_value", 0)
    write_value(g["reg"], g["fullscreen"], 1 if fullscreen else win_val, winreg.REG_DWORD)
    if g["use_native"] and use_native is not None:
        write_value(g["reg"], g["use_native"], 1 if use_native else 0, winreg.REG_DWORD)
    for extra in g["extra"]:
        if extra == "GraphicsSettings_PCResolution_h431323223":
            write_value(g["reg"], extra, _bytes_json(width, height, fullscreen), winreg.REG_BINARY)
    return f"{game}: {width}x{height} fullscreen={fullscreen} native={use_native}"


def set_windowed(game, width=AUTO_WIDTH, height=AUTO_HEIGHT):
    return apply(game, width, height, fullscreen=False, use_native=False)


def restore(game, state):
    g = GAME_KEYS[game]
    for name, info in state.items():
        value, typ = _restore_value(name, info)
        if typ is not None and value is not None:
            write_value(g["reg"], name, value, typ)
    return f"restored {game} ({len(state)} keys)"


def current(game):
    s = snapshot(game)
    g = GAME_KEYS[game]
    w = s[g["width"]]["value"]; h = s[g["height"]]["value"]
    f = s[g["fullscreen"]]["value"]
    n = s[g["use_native"]]["value"] if g["use_native"] else None
    return f"{game}: {w}x{h} fullscreen={f} native={n}"


def main():
    if len(sys.argv) < 3:
        print("用法: python display_mode.py <genshin|starrail|zzz> snapshot|set-windowed|restore [snapshot.json]")
        return 1
    game = sys.argv[1]; cmd = sys.argv[2]
    if game not in GAME_KEYS:
        print("未知游戏:", game); return 1
    if cmd == "snapshot":
        print(current(game))
        return 0
    if cmd == "set-windowed":
        r = set_windowed(game)
        print(r); print("[after]", current(game))
        return 0
    if cmd == "restore" and len(sys.argv) >= 4:
        with open(sys.argv[3], "r", encoding="utf-8") as fp:
            st = dict(json.load(fp))
        print(restore(game, st)); print("[after]", current(game))
        return 0
    print("未知子命令:", cmd); return 1


if __name__ == "__main__":
    sys.exit(main())
