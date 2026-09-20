# -*- coding: utf-8 -*-
"""
社区工具下载器：从各工具官方 GitHub Releases 一键下载并解压到标准位置。
  - bettergi   -> tools/BetterGI/BetterGI/BetterGI.exe
  - march7th   -> tools/March7thAssistant/March 7th Assistant.exe
  - onedragon  -> OneDragon-ref/（自带 runtime 的官方打包版）
支持代理、断点重下（临时文件）、进度回调。仅下载官方发布物，不修改其内容。
"""
import json, os, shutil, subprocess, time, urllib.request, zipfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
DL_DIR = BASE / "downloads"

# key -> (GitHub 仓库, 资产名匹配规则, 解压目标, 说明)
TOOLS = {
    "bettergi": {
        "repo": "babalae/better-genshin-impact",
        "match": "BetterGI_v", "ext": ".7z",
        # ★ 固定版本，不跟最新：0.65.0 是上游重构版（自己都标注"版本BUG较多"），
        #   实测自动秘境里"识别出战角色失败 → 卡墙角 → 后续任务全被挡"，2026-09-19/20 连挂两天。
        #   0.64.0 稳定可用。要升版本请先手动验证一条龙能跑完再改这里。
        "tag": "0.64.0",
        "target": "tools/BetterGI",
        "exe": "tools/BetterGI/BetterGI/BetterGI.exe",
        "desc": "原神 · BetterGI（免安装 7z 包，固定 0.64.0）",
    },
    "march7th": {
        "repo": "moesnow/March7thAssistant",
        "match": "March7thAssistant_full", "ext": ".zip",
        "target": "tools",
        "exe": "tools/March7thAssistant/March7th Assistant.exe",
        "desc": "崩铁 · March7thAssistant（完整包）",
    },
    "onedragon": {
        "repo": "OneDragon-Anything/ZenlessZoneZero-OneDragon",
        "match": "WithRuntime-Full", "ext": ".zip",
        "target": "OneDragon-ref",
        "exe": "",
        "desc": "绝区零 · OneDragon（自带运行时的官方打包版）",
    },
}

STATE = {"running": False, "tool": "", "phase": "", "percent": 0, "msg": "", "done": [], "log": []}


def _log(msg):
    STATE["log"].append("%s %s" % (time.strftime("%H:%M:%S"), msg))
    STATE["log"] = STATE["log"][-200:]
    STATE["msg"] = msg


def _opener(proxy: str):
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener(*handlers)


def latest_asset(repo: str, match: str, ext: str, proxy: str, token: str = "", tag: str = ""):
    """查 release（给了 tag 就查指定版本，否则查最新），返回 (名称, 下载地址, 大小)。"""
    headers = {"User-Agent": "hoyo-daily-3in1"}
    if token:
        headers["Authorization"] = "token " + token
    api = ("https://api.github.com/repos/%s/releases/tags/%s" % (repo, tag)) if tag \
        else ("https://api.github.com/repos/%s/releases/latest" % repo)
    req = urllib.request.Request(api, headers=headers)
    with _opener(proxy).open(req, timeout=60) as r:
        data = json.loads(r.read().decode("utf-8"))
    for a in data.get("assets", []):
        n = a.get("name", "")
        if match.lower() in n.lower() and n.lower().endswith(ext):
            return n, a["browser_download_url"], a.get("size", 0)
    raise RuntimeError("未找到匹配资产 %s*%s（仓库 %s）" % (match, ext, repo))


def download(url: str, dest: Path, proxy: str, token: str = ""):
    """分块下载 + 进度（支持续传：存在 .part 时从断点继续）。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    headers = {"User-Agent": "hoyo-daily-3in1"}
    if token:
        headers["Authorization"] = "token " + token
    done = part.stat().st_size if part.exists() else 0
    if done:
        headers["Range"] = "bytes=%d-" % done
    req = urllib.request.Request(url, headers=headers)
    with _opener(proxy).open(req, timeout=120) as r:
        total = int(r.headers.get("Content-Length") or 0) + done
        mode = "ab" if done and r.status == 206 else "wb"
        if mode == "wb":
            done = 0
        with open(part, mode) as f:
            while True:
                chunk = r.read(1024 * 512)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total:
                    STATE["percent"] = int(done * 100 / total)
                    STATE["phase"] = "下载中 %d%%（%.0f/%.0f MB）" % (STATE["percent"], done/1048576, total/1048576)
    part.replace(dest)
    return dest


def extract(archive: Path, target: Path):
    """解压（zip 用内置、7z 用 py7zr），并把单一顶层目录内容上提。"""
    target.mkdir(parents=True, exist_ok=True)
    tmp = target / "_extract_tmp"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    if archive.suffix.lower() == ".zip":
        with zipfile.ZipFile(archive) as z:
            z.extractall(tmp)
    else:
        import py7zr
        with py7zr.SevenZipFile(archive) as z:
            z.extractall(tmp)
    entries = [p for p in tmp.iterdir()]
    src = entries[0] if len(entries) == 1 and entries[0].is_dir() else tmp
    for item in src.iterdir():
        dst = target / item.name
        if dst.exists():
            if dst.is_dir():
                shutil.rmtree(dst, ignore_errors=True)
            else:
                dst.unlink()
        shutil.move(str(item), str(dst))
    shutil.rmtree(tmp, ignore_errors=True)


def install_one(key: str, proxy: str = "", token: str = ""):
    info = TOOLS[key]
    name, url, size = latest_asset(info["repo"], info["match"], info["ext"], proxy, token,
                                  tag=info.get("tag", ""))
    _log("找到 %s（%.0f MB）" % (name, size / 1048576))
    archive = DL_DIR / name
    if archive.exists() and archive.stat().st_size == size:
        _log("已存在同名完整安装包，跳过下载")
    else:
        download(url, archive, proxy, token)
    _log("解压中…")
    STATE["phase"] = "解压中…"
    extract(archive, BASE / info["target"])
    _log("完成：%s" % info["desc"])


def install(keys, proxy: str = "", token: str = ""):
    STATE.update({"running": True, "done": [], "percent": 0, "phase": "准备中", "log": []})
    try:
        for k in keys:
            if k not in TOOLS:
                continue
            STATE["tool"] = k
            _log("=== 开始安装 %s ===" % TOOLS[k]["desc"])
            install_one(k, proxy, token)
            STATE["done"].append(k)
    except Exception as e:
        _log("失败：%s" % e)
        STATE["phase"] = "失败：%s" % e
    finally:
        STATE["running"] = False
        STATE["phase"] = STATE["phase"] if STATE["phase"].startswith("失败") else "全部完成"


def status():
    return {"running": STATE["running"], "tool": STATE["tool"], "phase": STATE["phase"],
            "percent": STATE["percent"], "done": STATE["done"], "log": STATE["log"][-40:],
            "installed": {k: (BASE / v["exe"]).exists() if v["exe"] else (BASE / v["target"]).exists() for k, v in TOOLS.items()}}
