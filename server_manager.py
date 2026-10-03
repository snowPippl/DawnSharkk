# -*- coding: utf-8 -*-
"""
Unturned 服务器开服器 (本地网页版 · 萌新友好版)
- 首次启动在网页里选择游戏目录与存档(实例), 之后自动记住
- 功能: 开服/安全关服/重启、控制台(实时日志+Server Code 抓取+发送命令+报错高亮)、
        傻瓜式一键设置(死亡不掉落/建筑无敌/车辆无敌/免道具看地图等)、Commands.dat / Config.txt 汉化编辑、
        服务器图标与大厅链接(图床)、Rocket 指令反馈汉化、
        创意工坊模组、建筑与玩家存档备份/删除(删前强制备份)、全部配置文件(含 Rocket 插件)读取与修改、明/暗主题切换
- 纯 Python 标准库, 自带免安装 Python, 双击 DawnSharkk.exe 即用 (备用: 启动开服器.bat)
"""
import json
import os
import random
import re
import shutil
import signal
import socket
import string
import subprocess
import sys
import threading
import time
import zipfile
import http.client
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote, urlencode
import urllib.error
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(BASE_DIR, "settings.json")
OPLOG_PATH = os.path.join(BASE_DIR, "操作日志.txt")
HOST, PORT = "127.0.0.1", 8787
VERSION = "0.1.17"           # 发布版本号: 改这里 + 新增 更新内容-版本号.md + 跑 发布打包.py
# 发行版本: full = 完整版 (自带 SteamCMD 下载开服端)。从 v0.1.12 起只发完整版 ——
# 自带的工具才 1.6 MB, 再单独出一份"没有下载功能"的简装版没意义。
# lite 分支的代码保留, 只为兼容手里还拿着旧简装包的人。
EDITION = "full"
LITE = EDITION != "full"
VER_LABEL = "v" + VERSION + (" · 简装版" if LITE else "")

# 项目仓库: 侧边栏只显示 GitHub 图标 + 名称, 不把网址写在页面上
REPO_URL = "https://github.com/snowPippl/DawnSharkk"
# GitHub 图标路径来自 CC0 的 simple-icons (内联使用, 保持离线可用)
GH_ICON = ('<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 .297c-6.63 0-12 5.373-12 12 '
           '0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724'
           '-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729'
           ' 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305'
           '.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523'
           '.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28'
           '-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61'
           '-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69'
           '.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12"/></svg>')

ALLOWED_EXT = {".dat", ".txt", ".json", ".xml", ".cfg", ".ini"}

# ================================================================ 设置 (游戏目录 / 存档)
_settings = {"game_dir": None, "instance": None}


def load_settings():
    global _settings
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            _settings.update(data)
    except (OSError, ValueError):
        pass


def save_settings():
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(_settings, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def game_dir():
    return _settings.get("game_dir")


def instance():
    return _settings.get("instance")


def server_dir():
    g, i = game_dir(), instance()
    if g and i:
        return os.path.join(g, "Servers", i)
    return None


def setup_done():
    sd = server_dir()
    return bool(sd and game_exe_ok(game_dir()) and os.path.isdir(sd))


# 游戏本体 (客户端) 和专用开服端里都有 Unturned.exe, 光看 exe 分不出来。
# 能区分的三条"残留"信号: 客户端带 BattlEye 启动器、steam_appid.txt 写本体的 304930、
# 还有 Worlds + Preferences.json (单人存档和游戏设置)。
# ⚠ 这三条全是运行残留而不是安装标识 —— 真机取证: steam_appid.txt 的 mtime 等于
# 那次运行写出的 Logs mtime, 而不是 Steam 的 LastUpdated。所以一份真开服端只要被本体的
# 文件污染过 (整合包 / 手拷 / 网上教程让建的 304930 文件), 单看残留就会一直误判。
# 因此判据分三层: Steam 的 appmanifest 身份证 > 目录名叫 U3DS > 其他目录要命中两条。
CLIENT_APPID = "304930"
SIGNAL_DESC = {
    "be": "目录里有 Unturned_BE.exe (BattlEye 客户端启动器, 开服端没有)",
    "appid": "steam_appid.txt 写着 " + CLIENT_APPID + " (游戏本体的 AppID)",
    "worlds": "目录里同时有 Worlds 和 Preferences.json (本体的单人存档与游戏设置)",
}


def _steam_appid(gdir):
    """读 steam_appid.txt 第一行。Valve 写这个文件时结尾带 \\x00, str.strip() 去不掉"""
    try:
        with open(os.path.join(gdir, "steam_appid.txt"),
                  encoding="utf-8", errors="replace") as f:
            return f.readline().strip("\x00 \t\r\n")
    except OSError:
        return ""


def client_signals(gdir):
    """这个目录里出现了哪几条「只有客户端才有」的残留 (只报事实, 不下结论)"""
    if not gdir or not os.path.isdir(gdir):
        return []
    hits = []
    if os.path.isfile(os.path.join(gdir, "Unturned_BE.exe")):
        hits.append("be")
    if _steam_appid(gdir) == CLIENT_APPID:
        hits.append("appid")
    if os.path.isdir(os.path.join(gdir, "Worlds")) \
            and os.path.isfile(os.path.join(gdir, "Preferences.json")):
        hits.append("worlds")
    return hits


_manifest_cache = {}


def steam_install_dirs(appid, maxage=120):
    """Steam 客户端自己记在 appmanifest 里的安装目录 (用户改不动, 唯一权威的身份证)"""
    now = time.time()
    got = _manifest_cache.get(appid)
    if got and now - got[0] < maxage:
        return got[1]
    out = set()
    for root in steam_library_roots():
        acf = os.path.join(root, "steamapps", "appmanifest_%s.acf" % appid)
        try:
            with open(acf, encoding="utf-8", errors="replace") as f:
                m = re.search(r'"installdir"\s+"([^"]+)"', f.read())
            if m:
                out.add(os.path.normcase(os.path.normpath(
                    os.path.join(root, "steamapps", "common", m.group(1)))))
        except OSError:
            continue
    _manifest_cache[appid] = (now, out)
    return out


def is_steam_dir(gdir, appid):
    """这个文件夹是不是 Steam 装的某个 app (304930=本体 / 1110390=开服端)"""
    return bool(gdir) and os.path.normcase(os.path.normpath(gdir)) \
        in steam_install_dirs(str(appid))


def client_dir_reason(gdir):
    """空串 = 可以当开服端用; 否则返回一句萌新看得懂的「这是本体」的理由"""
    if not gdir or not os.path.isdir(gdir):
        return ""
    if is_steam_dir(gdir, DEDI_APPID):
        return ""                       # Steam 亲装的开服端, 残留一律不定罪
    if is_steam_dir(gdir, CLIENT_APPID):
        return "Steam 的记录 (appmanifest_" + CLIENT_APPID + ") 说这个文件夹就是游戏本体"
    hits = client_signals(gdir)
    if "be" in hits:
        return SIGNAL_DESC["be"]        # 这条开服端绝不会有, 单独定罪
    if os.path.basename(os.path.normpath(gdir)).lower() == "u3ds":
        return ""                       # 名字对得上 Steam 默认安装名: 降级成黄字提醒
    if len(hits) >= 2:
        return "、".join(SIGNAL_DESC[h] for h in hits)
    return ""


def client_soft_note(gdir):
    """放行但目录里有客户端残留: 一句黄字提醒 (不拦路)"""
    hits = [h for h in client_signals(gdir) if h != "be"]
    if not hits or client_dir_reason(gdir):
        return ""
    return ("这个文件夹里还留着本体的文件 (" + "、".join(SIGNAL_DESC[h] for h in hits) +
            ") —— 不影响开服, 但说明它可能被本体文件污染过。万一开服时抛 "
            "NullReferenceException at SDG.Unturned.Assets.Update(), 就是这份开服端里混了客户端文件")


def dir_evidence(gdir):
    """三条判据各自的实测结果 —— 报错里带上它, 用户截一张图就能定位是哪条判据命中"""
    hits = client_signals(gdir)
    who = ("Steam 装的开服端" if is_steam_dir(gdir, DEDI_APPID) else
           "Steam 装的游戏本体" if is_steam_dir(gdir, CLIENT_APPID) else
           "不在任何 Steam 库里 (自己下载或拷贝的)")
    return " | ".join([
        "Unturned_BE.exe: " + ("有" if "be" in hits else "没有"),
        "steam_appid.txt: " + (_steam_appid(gdir) or "没有这个文件"),
        "Worlds+Preferences.json: " + ("都有" if "worlds" in hits else "没有"),
        "Steam 记录: " + who,
    ])


def game_exe_ok(gdir):
    """能不能当游戏目录用: 有 Unturned.exe, 而且不是游戏本体"""
    return (bool(gdir) and os.path.isfile(os.path.join(gdir, "Unturned.exe"))
            and not client_dir_reason(gdir))


def check_game_dir(gdir):
    """返回空串表示可以用, 否则返回一句萌新看得懂的报错"""
    if not gdir or not os.path.isdir(gdir):
        return "这个文件夹不存在, 请检查路径有没有写全"
    why = client_dir_reason(gdir)
    if why:
        return ("这是 Unturned 游戏本体的目录, 不能拿来开服 ({}). ".format(why)
                + "开服要用 Steam 免费的「Unturned - Dedicated Server」(U3DS), 它和本体是两个东西。"
                + ("本机没有的话, 回到第 1 步点「🛒 在 Steam 商店打开开服端」装一个 (免费), "
                   "商店那条路走不动再点备用的「🔻 用 SteamCMD 下载」"
                   if not LITE else
                   "本机没有的话, 先在 Steam 商店装一个免费的 Unturned - Dedicated Server (U3DS)")
                + " (本目录实测: " + dir_evidence(gdir) + ")")
    if not os.path.isfile(os.path.join(gdir, "Unturned.exe")):
        return ("该目录下没有找到 Unturned.exe, 请确认是 U3DS 服务器目录"
                " (本目录实测: " + dir_evidence(gdir) + ")")
    return ""


def bound_dir_error():
    """当前 settings.json 里绑的目录有什么问题 (空串 = 正常)"""
    g = game_dir()
    return "请先设置有效的游戏目录" if not g else check_game_dir(g)


def steam_library_roots():
    """这台电脑上所有 Steam 库的根目录 (注册表 + 各盘常见位置 + libraryfolders.vdf)"""
    roots, seen = [], set()

    def add(r):
        r = os.path.normpath(str(r or ""))
        k = os.path.normcase(r)
        if r and k not in seen and os.path.isdir(r):
            seen.add(k)
            roots.append(r)

    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            add(winreg.QueryValueEx(k, "SteamPath")[0])
    except (OSError, ImportError):
        pass
    for drv in "CDEFGH":
        for tail in (r"Program Files (x86)\Steam", "Steam", "SteamLibrary",
                     r"Epic Games\Steam"):
            add(os.path.join(f"{drv}:\\", tail))
    for root in list(roots):
        vdf = os.path.join(root, "steamapps", "libraryfolders.vdf")
        if os.path.isfile(vdf):
            try:
                with open(vdf, encoding="utf-8", errors="replace") as f:
                    for m in re.finditer(r'"path"\s+"([^"]+)"', f.read()):
                        add(m.group(1).replace("\\\\", "\\"))
            except OSError:
                pass
    return roots


def detect_game_dirs():
    """探测各 Steam 库里的开服端目录

    除了默认文件夹名 U3DS, 还认 appmanifest_1110390.acf 里记的 installdir ——
    用户把开服端文件夹改过名、或者库不在常见盘符上, 光按名字找就永远探测不到。
    """
    found = []

    def add(p):
        p = os.path.normpath(p)
        if game_exe_ok(p) and os.path.normcase(p) not in map(os.path.normcase, found):
            found.append(p)

    for root in steam_library_roots():
        common = os.path.join(root, "steamapps", "common")
        add(os.path.join(common, "U3DS"))
        add(os.path.join(root, "SteamLibrary", "steamapps", "common", "U3DS"))
        acf = os.path.join(root, "steamapps", "appmanifest_%s.acf" % DEDI_APPID)
        try:
            with open(acf, encoding="utf-8", errors="replace") as f:
                m = re.search(r'"installdir"\s+"([^"]+)"', f.read())
            if m:
                add(os.path.join(common, m.group(1)))
        except OSError:
            pass
    return found


def detect_client_dirs():
    """本机的 Unturned 游戏本体目录 —— 只用来比对版本, 拿它开服会报错

    这里要的是「像不像本体」, 不是「能不能开服」, 所以命中任意一条残留就算, 不走三层判据。
    """
    found = []
    for root in steam_library_roots():
        p = os.path.normpath(os.path.join(root, "steamapps", "common", "Unturned"))
        if (client_signals(p) or is_steam_dir(p, CLIENT_APPID)) \
                and os.path.normcase(p) not in map(os.path.normcase, found):
            found.append(p)
    return found


def list_instances():
    """列出 Servers 下的所有存档(实例)"""
    g = game_dir()
    result = []
    base = os.path.join(g, "Servers") if g else None
    if base and os.path.isdir(base):
        for name in sorted(os.listdir(base)):
            p = os.path.join(base, name)
            if os.path.isdir(p):
                try:
                    mt = os.path.getmtime(p)
                except OSError:
                    mt = 0
                result.append({"name": name,
                               "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(mt))})
    return result


INSTANCE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
# 存档名会原样进开服命令行 (+Secureserver/存档名), 新建时仍要英文; 但 Servers 下
# 已经存在的文件夹就是一个存档 (别人给的、游戏里建的都可能叫中文), 选中它不该被拦。
NAME_BAD_CHARS = set('/\\:*?"<>|\r\n\t')


def existing_instance_ok(name):
    """已存在的存档目录名能不能用: 允许中文, 只挡路径符号和越界写法"""
    n = (name or "").strip()
    if not n or len(n) > 32 or n in (".", ".."):
        return False
    if any(c in NAME_BAD_CHARS for c in n):
        return False
    return n == os.path.basename(n)


def non_ascii_name_hint(name):
    """非英文存档名的风险提醒 (英文 Windows 上比对启动命令行会糊, 表现为关不掉服)"""
    if ascii_path(name):
        return ""
    return ("这个存档的名字里有中文或其他非英文字符。开服器读写它的配置没问题; "
            "但服务器「在不在运行 / 能不能关掉」靠比对游戏启动命令行判断, "
            "少数非中文系统的 Windows 上这行字会被显示成一串问号而认不出来 —— "
            "真碰上关不掉服务器, 把 Servers\\" + str(name) + " 改名成英文就好, 里面的数据不用动。")


def create_instance(name):
    if not INSTANCE_NAME_RE.match(name or ""):
        return False, "新建存档的名字只能用英文字母、数字、- 和 _ (1~32 字符)"
    base = os.path.join(game_dir(), "Servers", name)
    if os.path.isdir(base):
        return False, "这个存档名已经存在"
    os.makedirs(base, exist_ok=True)
    ensure_instance_files(name)
    port_m = re.search(r"(?mi)^Port\s+(\d+)",
                       open(os.path.join(base, "Server", "Commands.dat"),
                            encoding="utf-8").read())
    port = port_m.group(1) if port_m else "27015"
    return True, f"存档「{name}」已创建 (端口 {port})"


# ================================================================ 开服端下载 (SteamCMD)
# Steam 商店搜 "Unturned - Dedicated Server" 即可核对这个 appid (它是免费的专用服务器端)。
# 走 Valve 官方 steamcmd + 匿名登录, 不是第三方镜像站, 也不碰用户的 Steam 账号。
DEDI_APPID = "1110390"
# 商店页: 优先推荐朋友走这条路 (Steam 客户端里点安装最省事), steamcmd 只当备用
DEDI_STORE_URL = "https://store.steampowered.com/app/1110390/Unturned__Dedicated_Server/"
STEAMCMD_ZIP_URL = "https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip"
DL_PROGRESS_RE = re.compile(r"progress:\s*([\d.]+)\s*\((\d+)\s*/\s*(\d+)\)", re.I)
DL_STATE_RE = re.compile(r"Update state \((0x[0-9a-f]+)\)\s*([^,]*)", re.I)
# steamcmd 的两类致命失败长得完全不一样, 必须分开翻译, 不然用户会被「网络不通」带进死胡同
DL_PATH_ERR_RE = re.compile(r"non-english|incompatible path|cannot run from a folder", re.I)
DL_LICENSE_ERR_RE = re.compile(r"no app license|not owning instance|owned app is not available", re.I)
# Steam 那头一次没把应用信息递过来 (真机实测: 同一个目录原地重跑一次就装完了)。
# 这类绝不能报成「你的网络不行」, 也不能让用户手点第五遍。
DL_RETRY_ERR_RE = re.compile(r"missing configuration|unable to update prior app state|"
                             r"failed to install app .*?\(unavailable\)", re.I)
DL_UP_TO_DATE_RE = re.compile(r"already up to date", re.I)


class _DlAbort(Exception):
    pass


DL = {"state": "idle", "stage": "", "detail": "", "pct": 0.0, "got": 0, "total": 0,
      "speed": 0.0, "dest": "", "result": "", "msg": "", "log": deque(maxlen=60)}
DL_LOCK = threading.Lock()
DL_PROC = None
_dl_stop = threading.Event()


def tool_dir():
    """下载来的小工具放哪 (默认开服器文件夹下的 工具\\)"""
    return _settings.get("tool_dir") or os.path.join(BASE_DIR, "工具")


def ascii_path(p):
    """路径里能不能只有英文字符 —— steamcmd 见到中文路径会当场 Fatal Error 退出"""
    try:
        str(p).encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def _drive_ready(p):
    """路径所在盘在不在 (软盘/光驱/没插好的移动硬盘都会 False)"""
    root = os.path.splitdrive(str(p))[0]
    if not root:
        return False
    return os.path.isdir(root + "\\")


def steamcmd_dir():
    """steamcmd 自己的窝, 必须是纯英文路径。

    Valve 的 steamcmd 启动时检查自身所在文件夹: 路径带中文就直接
    「Fatal Error: cannot run from a folder path that includes non-English characters」,
    连网络都不碰。所以开服器装在中文文件夹里 (桌面/中文用户名/工具\\) 时要另找地方。
    """
    cand = [os.path.join(tool_dir(), "steamcmd"),
            os.path.join(BASE_DIR, "tools", "steamcmd")]
    cand += [f"{drv}:\\DawnSharkTools\\steamcmd" for drv in "DEFGHIJKC"]
    for d in cand:
        if ascii_path(d) and _drive_ready(d):
            return d
    for d in cand:
        if ascii_path(d):
            return d
    return "D:\\DawnSharkTools\\steamcmd"


def dedi_default_dest():
    """开服端默认下载位置: 同样必须纯英文, 而且要留够空间。

    查空间只能查盘符根目录 —— 建议的位置这会儿还不存在, 对它调 disk_usage 会直接抛错。
    """
    cand = [os.path.join(BASE_DIR, "U3DS")] + [f"{drv}:\\U3DS" for drv in "DEFGHIJC"]
    for p in cand:
        root = os.path.splitdrive(p)[0] + "\\"
        try:
            if ascii_path(p) and os.path.isdir(root) and shutil.disk_usage(root).free > 3 << 30:
                return p
        except OSError:
            continue
    for p in cand:
        if ascii_path(p):
            return p
    return "D:\\U3DS"


def dl_set(**kw):
    with DL_LOCK:
        DL.update(kw)


def dl_log(line):
    line = str(line).strip()
    if line:
        with DL_LOCK:
            DL["log"].append(line[:220])


def dl_snapshot():
    with DL_LOCK:
        out = {k: v for k, v in DL.items() if k != "log"}
        out["log"] = list(DL["log"])[-30:]
        out["running"] = out["state"] == "running"
        return out


def find_steamcmd():
    """找本机已有的 steamcmd: 手动指定 → 开服器下的 工具\\ → Steam 目录 → 常见安装位置 → PATH

    只认纯英文路径的: 放在中文文件夹里的 steamcmd 根本跑不起来, 宁可不复用。
    """
    cand = []
    s = _settings.get("steamcmd")
    if s:
        cand.append(s)
    cand.append(os.path.join(steamcmd_dir(), "steamcmd.exe"))
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            sp = winreg.QueryValueEx(k, "SteamPath")[0]
        cand += [os.path.join(sp, "steamcmd.exe"),
                 os.path.join(sp, "steamapps", "common", "SteamCMD", "steamcmd.exe")]
    except (OSError, ImportError):
        pass
    for drv in "CDEFGH":
        for tail in ("SteamCMD", r"steamcmd", r"tools\steamcmd", r"Program Files (x86)\SteamCMD"):
            cand.append(os.path.join(f"{drv}:\\", tail, "steamcmd.exe"))
    g = game_dir()
    if g:
        cand.append(os.path.join(os.path.dirname(os.path.abspath(g)), "steamcmd", "steamcmd.exe"))
    for p in cand:
        if p and ascii_path(p) and os.path.isfile(p):
            return os.path.abspath(p)
    w = shutil.which("steamcmd") or shutil.which("steamcmd.exe")
    if w and ascii_path(w):
        return os.path.abspath(w)
    return None


def scan_dedi_installs():
    """再查一遍本机有没有现成的开服端 —— 点下载时必须先跑这个, 别白下 1.5 GB"""
    found, seen = [], set()

    def add(p):
        p = os.path.normpath(str(p))
        if not game_exe_ok(p) or os.path.normcase(p) in seen:
            return
        seen.add(os.path.normcase(p))
        base = os.path.join(p, "Servers")
        try:
            n_inst = len([d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))]) \
                if os.path.isdir(base) else 0
            mt = os.path.getmtime(os.path.join(p, "Unturned.exe"))
        except OSError:
            n_inst, mt = 0, 0
        found.append({"path": p, "instances": n_inst,
                      "rocket": p and rocket_check(p)["installed"],
                      "time": time.strftime("%Y-%m-%d", time.localtime(mt)) if mt else ""})

    for p in detect_game_dirs():
        add(p)
    for p in (_settings.get("game_dir"), _settings.get("dedi_dest"),
              dedi_default_dest(), os.path.join(BASE_DIR, "U3DS"),
              os.path.join(BASE_DIR, "Unturned服务器")):
        if p:
            add(p)
    found.sort(key=lambda d: os.path.normcase(d["path"]) != os.path.normcase(game_dir() or ""))
    return found


def check_dest(p):
    """下载目标路径校验: 必须是盘符下的完整路径, 必须纯英文, 且不能是盘符根/系统目录"""
    p = str(p or "").strip().strip('"').replace("/", "\\").rstrip("\\")
    if not re.match(r"^[A-Za-z]:\\", p + "\\"):
        return False, "请填写完整路径, 例如 G:\\U3DS"
    if ".." in re.split(r"[\\/]", p):
        return False, "路径里不能用 .."
    root = p[:2] + "\\"
    if not os.path.isdir(root):
        return False, f"{root} 这个盘不存在, 或者硬盘还没插好"
    if os.path.normcase(p) == os.path.normcase(root.rstrip("\\")):
        return False, ("不能直接下载到盘符根目录 (例如 " + root +
                       ")。请在盘里新建一个文件夹, 例如 " + root + "U3DS")
    if not ascii_path(p):
        return False, ("这个路径里有中文或其他非英文字符, 而 steamcmd 只认英文 —— "
                       "Valve 的程序一看到中文文件夹就直接报错退出 (跟网络无关)。"
                       "请把下载位置改成纯英文, 例如 " + root + "U3DS 或 " + root + "games\\U3DS")
    risky = {os.path.normcase(os.environ.get("SystemRoot", r"C:\Windows")),
             os.path.normcase(os.path.expanduser("~")),
             os.path.normcase(os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"))),
             os.path.normcase(os.path.dirname(sys.executable))}
    if os.path.normcase(p) in risky:
        return False, "这个位置太靠近系统目录了, 换一个新建的子文件夹 (例如 G:\\U3DS)"
    if len(p) < 5:
        return False, "路径太短了, 请写到一个具体的子文件夹里"
    return True, p


def _dl_http(url, dst, label, tries=5):
    """下一个小文件: 断在半路就带 Range 续传, 不从 0 重来。

    国内连 Akamai 经常读到九成多就 read timeout, 一次都不重试等于逼用户手点好几遍。
    读超时故意设得短: 卡住时早点断线重连, 比死等 60 秒更快续上。
    """
    got = total = 0
    t0 = last_ui = time.time()
    last_err = None
    for n in range(tries):
        if _dl_stop.is_set():
            raise _DlAbort("已取消")
        if n:
            time.sleep(min(1.5 * n, 6))
            dl_log("第 %d 次重试: 从已下的 %d KB 处接着下" % (n + 1, got // 1024))
        hdr = {"User-Agent": "DawnSharkk/" + VERSION}
        if got:
            hdr["Range"] = "bytes=%d-" % got
        try:
            r = urllib.request.urlopen(
                urllib.request.Request(url, headers=hdr), timeout=25)
        except (urllib.error.URLError, OSError, ValueError) as e:
            last_err = e
            continue
        done = False
        with r:
            cr = (r.headers.get("Content-Range") or "").strip()
            if got and cr.lower().startswith("bytes "):
                try:
                    total = int(cr.rsplit("/", 1)[-1])
                except ValueError:
                    total = 0
            else:                                   # 服务器不认续传: 只能从头下
                got = 0
                try:
                    total = int(r.headers.get("Content-Length") or 0)
                except ValueError:
                    total = 0
            with open(dst, "ab" if got else "wb") as f:
                while True:
                    if _dl_stop.is_set():
                        raise _DlAbort("已取消")
                    try:
                        chunk = r.read(65536)
                    except (OSError, http.client.HTTPException,
                            urllib.error.URLError) as e:
                        last_err = e
                        break
                    if not chunk:
                        done = True
                        break
                    f.write(chunk)
                    got += len(chunk)
                    now = time.time()
                    if now - last_ui >= 0.4:
                        last_ui = now
                        dl_set(stage=label, got=got, total=total,
                               speed=got / max(now - t0, 0.1),
                               pct=(got * 100.0 / total) if total else 0.0)
        if done and (not total or got >= total):
            return got
    raise _DlAbort(
        "下不动 SteamCMD: %s (已重试 %d 次, 每次都从断点接着下)。这一步只是拿不到 1 MB 的"
        "官方 steamcmd, 和 1.5 GB 开服端无关。v0.1.11 起的完整版已经把 steamcmd 自带在"
        " tools\\steamcmd 里了, 走不到这一步 —— 如果你看到这句, 说明那份被删掉了 (或所在文件夹"
        "没能复制出去), 重新解压一份完整包到纯英文路径即可; 急着开服的话也可以先开个 Steam 加速器再点一次"
        % (last_err, tries))


def bundled_steamcmd():
    """发布包里自带的那份 steamcmd —— 有它就完全不用去下那 757 KB 的 zip。

    国内连 Valve 的 Akamai 经常读到九成多就断, 而这一步只是拿工具本身, 断在哪都很没道理。
    """
    p = bundled_exe()
    return os.path.abspath(p) if (ascii_path(p) and os.path.isfile(p)) else None


def bundled_exe():
    return os.path.join(BASE_DIR, "tools", "steamcmd", "steamcmd.exe")


def ensure_steamcmd():
    """返回 (可用路径 or None, 是否是我们自己下的)"""
    b = bundled_steamcmd()
    if b:
        dl_set(stage="① 准备 SteamCMD", detail="用开服器自带的一份, 不用联网下载")
        dl_log("使用开服器自带的 SteamCMD (跳过下载): " + b)
        return b, False
    exe = find_steamcmd()
    if exe:
        dl_set(stage="① 准备 SteamCMD", detail="复用本机已有的 steamcmd")
        dl_log("复用已有 SteamCMD: " + exe)
        return exe, False
    d = steamcmd_dir()
    exe = os.path.join(d, "steamcmd.exe")
    if os.path.isfile(exe):
        dl_set(stage="① 准备 SteamCMD", detail="复用上次下载的 steamcmd")
        dl_log("复用上次下载的 SteamCMD: " + exe)
        return exe, False
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        raise _DlAbort(
            "这台电脑上没能建出放 steamcmd 的英文文件夹 (盘符根目录通常不让直接写)。"
            "手动建一个纯英文文件夹, 例如 D:\\DawnSharkTools, 再点一次试试") from None
    src = bundled_exe()
    if os.path.isfile(src):
        # 走到这里说明自带那份没被直接用掉, 只因开服器所在的文件夹带中文 —— 抄一份到英文文件夹, 照样不用联网
        try:
            shutil.copy2(src, exe)
        except OSError:
            dl_log("自带的 SteamCMD 没能复制到 " + d + ", 改为联网下载")
        else:
            dl_set(stage="① 准备 SteamCMD", detail="用开服器自带的一份, 不用联网下载")
            dl_log("自带 SteamCMD 的路径含中文跑不动, 已复制一份到英文文件夹: " + exe)
            return exe, False
    zp = os.path.join(d, "steamcmd.zip")
    dl_set(stage="① 下载官方 SteamCMD", detail="正在从 Valve 官方服务器下载 (不到 1 MB)")
    if os.path.splitdrive(d)[0].lower() != os.path.splitdrive(BASE_DIR)[0].lower():
        dl_log("开服器所在的文件夹路径含中文, SteamCMD 改放到 " + d)
    try:
        _dl_http(STEAMCMD_ZIP_URL, zp, "① 下载官方 SteamCMD")
        dl_set(stage="① 解压 SteamCMD", pct=100.0)
        with zipfile.ZipFile(zp) as zf:
            zf.extractall(d)
    finally:
        try:
            os.remove(zp)
        except OSError:
            pass
    if not os.path.isfile(exe):
        return None, False
    dl_log("SteamCMD 已解压到 " + d)
    return exe, True


def _kill_tree(proc):
    try:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        try:
            proc.kill()
        except OSError:
            pass


def _steamcmd_err(err_line, dest):
    """把 steamcmd 的英文报错翻译成人话: 路径 / Steam 抽风 / 许可证 / 网络 四类绝不能混着说"""
    root = (dest or "D:")[:2] + "\\"
    if DL_PATH_ERR_RE.search(err_line or ""):
        return ("开服端所在的文件夹路径里有中文, 而 Valve 的 steamcmd 只认英文, 一看到中文就直接退出 "
                "(这一条和网络无关, 加速器帮不上)。把这一项改成纯英文的位置, 例如 " + root + "U3DS; "
                "开服器自己那份 steamcmd 会挪到英文文件夹里, 不用你搬整个开服器")
    if DL_RETRY_ERR_RE.search(err_line or ""):
        return ("Steam 那一头刚才没把开服端的应用信息递过来, 这一条重跑一次基本就好, "
                "和路径、设置都没关系 (开服器已经自动重试过几轮)。等一两分钟再点一次"
                "「开始下载」; 反复都这样再开个 Steam 加速器 —— 卡的是 Steam 的元数据服务器, "
                "不是那 1.5 GB 的下载")
    if DL_LICENSE_ERR_RE.search(err_line or ""):
        return ("Steam 说这个应用不归这个账号 (匿名下载偶尔抽风)。"
                "先开个 Steam 加速器再重试一次; 还是不行就在 Steam「库 → 工具」里"
                "安装一次 Unturned - Dedicated Server, 装完回到这一页点「重新检测本机」")
    if err_line:
        return ("多半是网络不通: 国内直连 Valve 下载服务器经常卡住, "
                "开个 Steam 加速器再重试一次。实在不行的兜底路径: Steam「库 → 工具」里"
                "安装 Unturned - Dedicated Server (免费), 装完点「重新检测本机」")
    return ("下载跑完了, 但目标文件夹里没有 Unturned.exe —— 可能是磁盘满了或中途断网。"
            "看一下下面最后几行 steamcmd 的输出")


def _steamcmd_once(exe, dest, update=False):
    """跑一趟 steamcmd 装/更新开服端。update=True 时目标是已经存在的开服端目录"""
    global DL_PROC
    if not ascii_path(exe) or not ascii_path(dest):
        return False, _steamcmd_err("non-english", dest), ""
    args = [exe, "+force_install_dir", dest, "+login", "anonymous",
            "+app_update", DEDI_APPID, "validate", "+quit"]
    what = "更新开服端" if update else "下载开服端"
    dl_set(stage="② " + what, detail=f"Steam 应用 {DEDI_APPID} → {dest}")
    dl_log("正在启动 steamcmd (窗口不会显示, 进度看这里)…")
    proc = subprocess.Popen(args, cwd=os.path.dirname(exe), stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", bufsize=1,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    with DL_LOCK:
        DL_PROC = proc
    err_line = ""
    fatal_line = ""
    uptodate = False
    try:
        while True:
            if _dl_stop.is_set():
                _kill_tree(proc)
                return False, "下载已取消", ""
            line = proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            dl_log(line)
            m = DL_PROGRESS_RE.search(line)
            if m:
                dl_set(pct=min(100.0, float(m.group(1))), got=int(m.group(2)),
                       total=int(m.group(3)))
            m = DL_STATE_RE.search(line)
            if m and m.group(2).strip():
                dl_set(detail=f"{m.group(2).strip()} · {dest}")
            low = line.lower()
            if low.startswith("error!") or low.startswith("fatal error") \
                    or "assertion failed" in low or "no app license" in low:
                fatal_line = line
            elif "failed" in low or "unable to" in low:
                err_line = err_line or line
            if DL_UP_TO_DATE_RE.search(line):
                uptodate = True
        proc.wait(timeout=60)
    finally:
        with DL_LOCK:
            DL_PROC = None
    rc = proc.returncode
    got = dedi_root(dest)
    if _dl_stop.is_set():
        return False, "下载已取消", ""
    # steamcmd 明确说了已是最新 = 成功; 更新一个已经能跑的开服端时, 目录里本来就有
    # Unturned.exe, 不能拿「目录存在」当成功依据, 否则 Fatal Error 会被当成更新完成
    if uptodate and got:
        dl_set(pct=100.0)
        return True, ("已经是最新版本, 不用更新 ✔ 重启服务器就能继续玩" if update
                      else "开服端下载完成 🎉 点「用这个目录」就能接着建存档"), got
    if fatal_line:
        return False, (_steamcmd_err(fatal_line, dest) +
                       " (steamcmd 原话: " + fatal_line[:150] + ")"), ""
    if err_line and not got:
        return False, (_steamcmd_err(err_line, dest) +
                       " (steamcmd 原话: " + err_line[:150] + ")"), ""
    if not got:
        return False, _steamcmd_err("", dest), ""
    if rc not in (0, None):
        return False, (_steamcmd_err(err_line, dest) +
                       f" (steamcmd 以错误码 {rc} 退出, 看一下下面最后几行的输出)"), ""
    dl_set(pct=100.0)
    if update:
        return True, "开服端已更新到最新版 🎉 重启服务器后玩家就能进来了", got
    return True, "开服端下载完成 🎉 点「用这个目录」就能接着建存档", got


def _run_steamcmd(exe, dest, update=False, tries=3):
    """跑 steamcmd 装开服端, 遇到「Steam 没递上应用信息」这类原地重跑

    真机复现过: 同一份 steamcmd、同一个目录, 第一趟报 Missing configuration,
    第二趟就把 1.86 GB 全装完了。这种事让用户手点三遍不如我们替他点。
    """
    for n in range(1, tries + 1):
        if _dl_stop.is_set():
            return False, "下载已取消", ""
        ok, msg, got = _steamcmd_once(exe, dest, update)
        if ok or not DL_RETRY_ERR_RE.search(msg or ""):
            return ok, msg, got
        if n < tries:
            dl_log("Steam 没递上开服端的应用信息, 稍等几秒自动重跑第 %d 趟 (不用你管)…" % (n + 1))
            for _ in range(3):
                if _dl_stop.is_set():
                    return False, "下载已取消", ""
                time.sleep(2)
    return ok, msg, got


def dedi_root(dest):
    """steamcmd 有时直接把文件铺在目标目录, 有时会套一层 U3DS"""
    for p in (dest, os.path.join(dest, "U3DS")):
        if game_exe_ok(p):
            return os.path.normpath(p)
    return ""


def _dl_worker(dest, update=False):
    what = "更新" if update else "下载"
    try:
        dl_set(state="running", stage="准备中", detail="", pct=0.0, got=0, total=0,
               speed=0.0, result="", msg="")
        exe, fresh = ensure_steamcmd()
        if not exe:
            raise _DlAbort("拿不到 steamcmd: 官方包下载失败, 或解压后没找到 steamcmd.exe")
        if fresh:
            dl_log(f"SteamCMD 已就位, 开始{what}开服端")
        ok, msg, path = _run_steamcmd(exe, dest, update=update)
        if _dl_stop.is_set():
            dl_set(state="cancelled", stage=f"{what}已取消", msg="已经取消了。随时可以再点「开始下载」")
            oplog("操作", f"开服端{what}已取消: {dest}")
            return
        if ok:
            _settings["dedi_dest"] = path
            save_settings()
            dl_set(state="done", stage="完成", result=path, msg=msg)
            oplog("操作", f"开服端{what}完成: {path}")
        else:
            dl_set(state="error", stage=f"{what}失败", msg=msg)
            oplog("错误", f"开服端{what}失败: {msg[:160]}")
    except _DlAbort as e:
        dl_set(state="cancelled" if _dl_stop.is_set() else "error",
               stage=f"{what}已取消" if _dl_stop.is_set() else f"{what}失败", msg=str(e))
        oplog("操作", f"开服端{what}中断: {e}")
    except Exception as e:
        dl_set(state="error", stage=f"{what}失败", msg=f"开服器{what}出错: {e}")
        oplog("错误", f"开服端{what}异常: {e}")


def start_download(dest, update=False):
    if DL.get("state") == "running":
        return False, "已经有一个下载/更新在进行了, 等它结束再试"
    what = "更新" if update else "下载"
    ok, info = check_dest(dest)
    if not ok:
        return False, info
    dest = info
    if update and not game_exe_ok(dest):
        return False, "要更新的这个文件夹里没有 Unturned.exe, 不能当开服端更新"
    _dl_stop.clear()
    with DL_LOCK:
        DL["log"].clear()
    dl_set(state="running", dest=dest)
    threading.Thread(target=_dl_worker, args=(dest,), kwargs={"update": update},
                     daemon=True).start()
    oplog("操作", f"开始{what} Unturned 开服端 (app {DEDI_APPID}) → {dest}")
    return True, f"开始{what}了, 进度会显示在下面"


def start_update():
    """把当前绑定的开服端用 steamcmd 更新到最新版 (AppID 不变, validate 会补齐缺的文件)"""
    if LITE:
        return False, ("简装版不带 steamcmd: 请在 Steam「库 → 工具」里右键 "
                       "Unturned - Dedicated Server → 更新")
    dest = game_dir() or _settings.get("dedi_dest") or ""
    if not game_exe_ok(dest):
        return False, "还没有可用的开服端目录, 先去「初始设置」选好或下载一个"
    if not ascii_path(dest):
        return False, ("这个开服端所在的文件夹路径里有中文, steamcmd 更新不了它 (Valve 只认英文)。"
                       "两个办法: ① 用 Steam 客户端更新 —— 库 → 按「工具」筛选 → 右键 "
                       "Unturned - Dedicated Server → 更新; ② 把整个文件夹挪到纯英文路径 "
                       "(例如 E:\\U3DS), 再回「初始设置」重新选一次目录")
    return start_download(dest, update=True)


def cancel_download():
    if DL.get("state") != "running":
        return False, "现在没有正在下载的开服端"
    _dl_stop.set()
    p = DL_PROC
    if p:
        _kill_tree(p)
    dl_set(stage="正在取消…")
    return True, "正在取消, 已下载的部分 steamcmd 会自动续上"


def adopt_dedi(path):
    """把刚下载好的开服端直接认作游戏目录"""
    p = os.path.normpath(str(path or ""))
    if not game_exe_ok(p):
        p = dedi_root(p) or p
    if not game_exe_ok(p):
        return False, "这个目录里没有 Unturned.exe, 还不能当游戏目录用", None
    _settings["game_dir"] = p
    _settings["dedi_dest"] = p
    if instance() and not os.path.isdir(os.path.join(p, "Servers", instance())):
        _settings["instance"] = None
    save_settings()
    oplog("操作", f"选用下载好的开服端: {p}")
    return True, "已经把开服端设为游戏目录", p


# ================================================================ 开服端版本
# exe 的文件版本读出来是 Unity 引擎版本 (本体和开服端一模一样), 判断不了游戏版本。
# 真正写清楚版本的是游戏自己的日志: "Game version: 3.26.3.11 Engine version: 2022.3.62f3"
GAME_VER_RE = re.compile(r"Game version:\s*([0-9][0-9A-Za-z.\-]*)")


def version_tuple(s):
    return tuple(int(x) for x in re.findall(r"\d+", str(s or ""))[:4])


def _last_game_version(path):
    """从日志末尾抓最后一次 Game version (每次启动都会写一遍, 取最后一次才是当前版本)"""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 262144))
            data = f.read()
    except OSError:
        return ""
    found = GAME_VER_RE.findall(data.decode("utf-8", errors="replace"))
    return found[-1] if found else ""


def _logs_under(root, rel, limit=6):
    d = os.path.join(root or "", rel)

    def mt(p):
        try:
            return os.path.getmtime(p)
        except OSError:
            return 0.0

    try:
        names = os.listdir(d)
    except OSError:
        return []
    paths = [os.path.join(d, n) for n in names if n.lower().endswith(".log")]
    paths.sort(key=mt, reverse=True)
    return paths[:limit]


def _newest_game_version(paths):
    v, best_mt = "", 0.0
    for p in paths:
        try:
            mt = os.path.getmtime(p)
        except OSError:
            continue
        if mt < best_mt:
            continue
        got = _last_game_version(p)
        if got:
            v, best_mt = got, mt
    return v


def dedi_version():
    """开服端上次启动时跑的游戏版本 —— 纯本地读日志, 不联网"""
    g = game_dir()
    if not g:
        return ""
    paths = _logs_under(g, "Logs")
    base = os.path.join(g, "Servers")
    try:
        for name in os.listdir(base):
            paths.append(os.path.join(base, name, "console.log"))
    except OSError:
        pass
    return _newest_game_version(paths)


def client_version():
    """本机游戏本体的版本 —— 本体跟着 Steam 自动更新, 开服端不会, 差版本玩家就进不来"""
    for d in detect_client_dirs():
        v = _newest_game_version(_logs_under(d, "Logs"))
        if v:
            return v, d
    return "", ""


def version_report():
    """开服端 / 本体版本对照, 给「服务器设置」和「初始设置」两处用"""
    dv = dedi_version()
    cv, cdir = client_version()
    dt, ct = version_tuple(dv), version_tuple(cv)
    return {"dedi": dv, "client": cv, "client_dir": cdir, "game_dir": game_dir() or "",
            "stale": bool(dt and ct and ct > dt), "same": bool(dt and ct and ct == dt)}


def dedi_version_card():
    """「服务器设置」页: 开服端版本对照 + 一键更新 (进度条和初始设置的下载面板共用一套组件)"""
    r = version_report()
    head = (f'开服端上次运行的版本: <b style="color:var(--ok)">{esc(r["dedi"])}</b>' if r["dedi"]
            else '开服端版本: 暂时读不到 (这个开服端完整跑过一次之后, 日志里才会有版本号)')
    cl = (f'本机游戏本体的版本: <b>{esc(r["client"])}</b>' if r["client"]
          else '本机游戏本体: 没检测到 (这台机器可能只装了开服端)')
    if r["stale"]:
        state = ('<div class="tip">⚠ <b>本体比开服端新了一版</b>。Steam 会自动把玩家的本体更新, '
                 '但开服端不会自己动 —— 版本差开的常见结果是朋友点进服务器卡在加载那一格, '
                 '或者提示版本不一致。点下面的按钮让开服器用 Steam 官方的 steamcmd 补到最新。</div>')
    elif r["same"]:
        state = '<div class="hint">两边版本对得上 ✔ 朋友能正常进服。</div>'
    else:
        state = '<div class="hint">读不全两边版本时, 这个按钮也会顺手向 Steam 核对一次最新构建。</div>'
    if LITE:
        btn = ('<div class="hint" style="margin-top:10px">简装版不带 steamcmd: 更新请在 Steam 里 '
               '「库 → 按工具筛选 → 右键 Unturned - Dedicated Server → 更新」, '
               '更新完重启开服器再看这一行。</div>')
    else:
        btn = """<div style="margin-top:12px;display:flex;gap:10px;flex-wrap:wrap;align-items:center">
<button class="btn" id="dlgo" onclick="updateDedi()">🔄 检查并更新开服端 (SteamCMD)</button>
<button class="btn gray" id="dlcancel" onclick="cancelDl()" style="display:none">✖ 取消</button>
<span class="hint" id="dlmsg" style="margin:0"></span></div>
<div class="bar" id="dlbar" style="display:none"><i id="dlfill"></i></div>
<div class="hint" id="dlstage" style="white-space:pre-line"></div>
<pre class="dllog" id="dllog" style="display:none"></pre>"""
    return f"""<div class="card"><h2>开服端版本与更新</h2>
<div class="desc">开服端就是这个文件夹: <code class="k">{esc(r["game_dir"] or "未设置")}</code><br>
{head} · {cl}</div>
{state}
{btn}
<div class="hint">更新走 Valve 官方服务器 (匿名登录, 不碰你的 Steam 账号), 只会补游戏文件,
<b>不会动 Servers 文件夹里的存档</b>; 本来就最新的话几十秒就完。更新完要重启服务器才生效。
开服端所在的文件夹路径必须是纯英文, 有中文 steamcmd 会拒绝工作 (Valve 的限制)。
<span style="color:var(--acc2)">· 不想用这条路</span>: 开服端当初是在 Steam 商店装的,
直接在「库 → 按工具筛选」里右键它 → 更新也一样。</div></div>"""


# ================================================================ 编码
def detect_encoding(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    try:
        data.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "gbk"


def read_file(path):
    with open(path, "rb") as f:
        data = f.read()
    return data.decode(detect_encoding(data)), detect_encoding(data)


def write_file(path, text):
    try:
        with open(path, "rb") as f:
            enc = detect_encoding(f.read())
    except FileNotFoundError:
        enc = "utf-8"          # 新存档还没生成该文件时, 由开服器创建
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if text and not text.endswith("\n"):
        text += "\n"
    with open(path, "w", encoding=enc, newline="") as f:
        f.write(text)


def safe_path(rel):
    sd = server_dir()
    if not sd:
        return None
    full = os.path.abspath(os.path.join(sd, rel))
    if not full.startswith(os.path.abspath(sd)):
        return None
    return full if os.path.isfile(full) else None


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def config_txt():
    return os.path.join(server_dir(), "Config.txt")


# ================================================================ 配置文件解析 (Config.txt 嵌套格式)
def parse_cfg(text):
    lines = text.splitlines()
    items, stack, pending = [], [], None
    i, n = 0, len(lines)

    def next_nonempty(j):
        while j < n:
            s = lines[j].strip()
            if s:
                return s
            j += 1
        return ""

    while i < n:
        raw = lines[i]
        st = raw.strip()
        if not st or st.startswith("//"):
            items.append({"type": "other", "raw": raw})
            i += 1
            continue
        if st == "{":
            stack.append(pending if pending else "")
            pending = None
            items.append({"type": "brace", "raw": raw})
            i += 1
            continue
        if st == "}":
            if stack:
                stack.pop()
            items.append({"type": "brace", "raw": raw})
            i += 1
            continue
        m = re.match(r"^([^\s{}]+)(?:\s+(.*?))?\s*$", st)
        key, val = (m.group(1), m.group(2)) if m else (None, None)
        nxt = next_nonempty(i + 1)
        if key and not val and nxt == "{":            # 段头
            pending = key
            items.append({"type": "sectopen", "raw": raw, "key": key})
            i += 1
            continue
        if key and not val and nxt == "[":            # 数组(原样保留, 不修改)
            block = [raw]
            i += 1
            while i < n:
                block.append(lines[i])
                if lines[i].strip() == "]":
                    i += 1
                    break
                i += 1
            items.append({"type": "block", "raws": block, "key": key,
                          "section": tuple(stack)})
            continue
        if key:
            items.append({"type": "setting", "key": key, "value": val,
                          "indent": raw[:len(raw) - len(raw.lstrip())],
                          "section": tuple(stack)})
            i += 1
            continue
        items.append({"type": "other", "raw": raw})
        i += 1
    return items


def build_cfg(items):
    out = []
    for it in items:
        if it["type"] == "setting":
            v = it.get("value")
            out.append((it["indent"] or "") + it["key"] + (("" if v is None else " " + v)))
        elif it["type"] == "block":
            out.extend(it["raws"])
        else:
            out.append(it["raw"])
    return "\n".join(out)


def set_cfg(path, section, key, value):
    """value=None 恢复默认(删除值); section 可为 'Players' 或 ('A','B'); 返回是否找到"""
    _ensure_cfg_file(path)
    text, _ = read_file(path)
    items = parse_cfg(text)
    sec = (section,) if isinstance(section, str) else tuple(section or ())
    for it in items:
        if it["type"] == "setting" and it["key"].lower() == key.lower() \
                and it["section"] == sec:
            it["value"] = value
            write_file(path, build_cfg(items))
            return True
    return False


def get_cfg(path, section, key):
    _ensure_cfg_file(path)
    try:
        text, _ = read_file(path)
    except OSError:
        return None
    sec = (section,) if isinstance(section, str) else tuple(section or ())
    for it in parse_cfg(text):
        if it["type"] == "setting" and it["key"].lower() == key.lower() \
                and it["section"] == sec:
            return it["value"]
    return None


# ================================================= 服务器图标 / 大厅链接 (Config.txt 的 Browser 段)
BROWSER_DEFAULTS = {
    "icon": "https://s41.ax1x.com/2026-07-16/pmyJTMR.jpg",
    "thumb": "https://s41.ax1x.com/2026-07-16/pmyJIz9.jpg",
    "links": [("欢迎使用DawnSharkk", "https://github.com/snowPippl/DawnSharkk")],
}
BROWSER_SEC = ("Browser",)


def _links_raws(links, indent="\t"):
    out = [indent + "Links", indent + "["]
    for msg, url in links:
        out += [indent + "\t{", indent + "\t\tMessage " + msg,
                indent + "\t\tURL " + url, indent + "\t}"]
    out.append(indent + "]")
    return out


def _parse_links(raws):
    links, msg, url = [], None, None
    for r in raws:
        s = r.strip()
        if s.startswith("Message"):
            msg = s[len("Message"):].strip()
        elif s.startswith("URL"):
            url = s[len("URL"):].strip()
        elif s == "}":
            if url:
                links.append((msg or "", url))
            msg = url = None
    return links


def _browser_setting(key, value):
    return {"type": "setting", "key": key, "value": value,
            "indent": "\t", "section": BROWSER_SEC}


def parse_link_lines(text):
    """每行一条链接: 「文字 | 网址」; 没有 | 时按最后一个空格切分"""
    out = []
    for ln in (text or "").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        if "|" in ln:
            msg, url = ln.split("|", 1)
        else:
            parts = ln.rsplit(" ", 1)
            msg, url = (parts if len(parts) == 2 else ("", parts[0]))
        msg, url = msg.strip()[:60], url.strip()[:300]
        if url.startswith("http://") or url.startswith("https://"):
            out.append((msg or url, url))
    return out[:8]


def browser_cfg(path=None):
    """读当前存档 Config.txt 的 Browser 段 (未设置的返回空)"""
    p = path or config_txt()
    info = {"icon": "", "thumb": "", "links": []}
    try:
        _ensure_cfg_file(p)
        text, _ = read_file(p)
    except OSError:
        return info
    for it in parse_cfg(text):
        if it.get("section") != BROWSER_SEC:
            continue
        if it["type"] == "setting":
            k = it["key"].lower()
            if k == "icon":
                info["icon"] = it["value"] or ""
            elif k == "thumbnail":
                info["thumb"] = it["value"] or ""
        elif it["type"] == "block" and it["key"].lower() == "links":
            info["links"] = _parse_links(it["raws"])
    return info


def set_browser_cfg(icon, thumb, links, path=None):
    """写入 Browser 段 Icon / Thumbnail / Links; 传空表示恢复游戏默认(不设置)"""
    p = path or config_txt()
    _ensure_cfg_file(p)
    text, _ = read_file(p)
    items = parse_cfg(text)
    found = {"icon": False, "thumb": False, "links": False}
    for i, it in enumerate(items):
        if it.get("section") != BROWSER_SEC:
            continue
        if it["type"] == "setting":
            k = it["key"].lower()
            if k == "icon":
                it["value"] = icon or None
                found["icon"] = True
            elif k == "thumbnail":
                it["value"] = thumb or None
                found["thumb"] = True
            elif k == "links":
                items[i] = (_browser_setting("Links", None) if not links
                            else {"type": "block", "key": "Links",
                                  "section": BROWSER_SEC, "raws": _links_raws(links)})
                found["links"] = True
        elif it["type"] == "block" and it["key"].lower() == "links":
            if links:
                it["raws"] = _links_raws(links)
            else:
                items[i] = _browser_setting("Links", None)
            found["links"] = True
    todo = []
    if not found["icon"]:
        todo.append(_browser_setting("Icon", icon or None))
    if not found["thumb"]:
        todo.append(_browser_setting("Thumbnail", thumb or None))
    if not found["links"]:
        todo.append(_browser_setting("Links", None) if not links else
                    {"type": "block", "key": "Links", "section": BROWSER_SEC,
                     "raws": _links_raws(links)})
    if todo:
        head = next((i for i, it in enumerate(items)
                     if it["type"] == "sectopen" and it["key"] == "Browser"), None)
        if head is None:
            items.append({"type": "sectopen", "raw": "Browser", "key": "Browser"})
            items.append({"type": "brace", "raw": "{"})
            items.extend(todo)
            items.append({"type": "brace", "raw": "}"})
            items.append({"type": "other", "raw": ""})
        else:
            at = head + 1
            if at < len(items) and items[at]["type"] == "brace":
                at += 1
            items[at:at] = todo
    write_file(p, build_cfg(items))
    return True


def apply_browser_defaults(name=None, path=None):
    """每个存档第一次被打开时写入 Dawn Sharkk 默认服务器图标与大厅链接;
    之后用户自己清空/改动不再自动补, 以免覆盖他的选择"""
    inst = name or instance()
    done = _settings.get("browser_defaults_done") or []
    if not inst or inst in done:
        return False
    p = path or os.path.join(game_dir(), "Servers", inst, "Config.txt")
    cur = browser_cfg(p)
    icon = cur["icon"] or BROWSER_DEFAULTS["icon"]
    thumb = cur["thumb"] or BROWSER_DEFAULTS["thumb"]
    links = cur["links"] or list(BROWSER_DEFAULTS["links"])
    if (icon, thumb, links) == (cur["icon"], cur["thumb"], cur["links"]):
        return False
    set_browser_cfg(icon, thumb, links, p)
    done.append(inst)
    _settings["browser_defaults_done"] = done
    save_settings()
    return True


# ================================================================ 傻瓜式开关定义
TOGGLES = [
    {"id": "keep_inventory", "icon": "🎒", "name": "死亡不掉落",
     "desc": "死亡后保留背包里的物品、身上穿的装备、手里的武器, 经验和技能也不掉",
     "keys": [("Players", "Lose_Items_PvP", "0"), ("Players", "Lose_Items_PvE", "0"),
              ("Players", "Lose_Clothes_PvP", "False"), ("Players", "Lose_Clothes_PvE", "False"),
              ("Players", "Lose_Weapons_PvP", "False"), ("Players", "Lose_Weapons_PvE", "False"),
              ("Players", "Lose_Experience_PvP", "1"), ("Players", "Lose_Experience_PvE", "1"),
              ("Players", "Lose_Skills_PvP", "1"), ("Players", "Lose_Skills_PvE", "1"),
              ("Players", "Lose_Skill_Levels_PvP", "0"), ("Players", "Lose_Skill_Levels_PvE", "0")]},
    {"id": "struct_inv", "icon": "🏠", "name": "建筑无敌",
     "desc": "基地的墙壁、地板、门、家具不会被任何武器和工具打坏",
     "keys": [("Barricades", "Armor_Lowtier_Multiplier", "0"), ("Barricades", "Armor_Hightier_Multiplier", "0"),
              ("Barricades", "Gun_Lowcal_Damage_Multiplier", "0"), ("Barricades", "Gun_Highcal_Damage_Multiplier", "0"),
              ("Barricades", "Melee_Damage_Multiplier", "0"),
              ("Structures", "Armor_Lowtier_Multiplier", "0"), ("Structures", "Armor_Hightier_Multiplier", "0"),
              ("Structures", "Gun_Lowcal_Damage_Multiplier", "0"), ("Structures", "Gun_Highcal_Damage_Multiplier", "0"),
              ("Structures", "Melee_Damage_Multiplier", "0")]},
    {"id": "veh_inv", "icon": "🚗", "name": "车辆无敌",
     "desc": "汽车、飞机等载具不会被武器打坏, 也不会自然损坏",
     "keys": [("Vehicles", "Armor_Multiplier", "0"), ("Vehicles", "Gun_Lowcal_Damage_Multiplier", "0"),
              ("Vehicles", "Gun_Highcal_Damage_Multiplier", "0"), ("Vehicles", "Melee_Damage_Multiplier", "0"),
              ("Vehicles", "Child_Explosion_Armor_Multiplier", "0"),
              ("Vehicles", "Decay_Damage_Per_Second", "0")]},
    {"id": "zombie_safe", "icon": "🧟", "name": "僵尸不拆家",
     "desc": "僵尸不再攻击玩家的墙壁、家具和载具(仍会攻击玩家)",
     "keys": [("Zombies", "Can_Target_Barricades", "False"), ("Zombies", "Can_Target_Structures", "False"),
              ("Zombies", "Can_Target_Vehicles", "False")]},
    {"id": "no_fall", "icon": "🦵", "name": "关闭摔落伤害",
     "desc": "从高处掉落不掉血、不会摔断腿",
     "keys": [("Players", "Can_Hurt_Legs", "False"), ("Players", "Can_Break_Legs", "False")]},
    {"id": "friendly_fire", "icon": "🤝", "name": "组队友伤",
     "desc": "允许同一个队伍的成员互相造成伤害(默认关闭)",
     "keys": [("Gameplay", "Friendly_Fire", "True")]},
    {"id": "airdrops", "icon": "✈️", "name": "空投补给",
     "desc": "定期有飞机飞过地图空投高级物资箱",
     "keys": [("Events", "Use_Airdrops", "True")]},
    {"id": "max_skills", "icon": "⭐", "name": "出生满技能",
     "desc": "新玩家进入服务器时所有技能直接满级",
     "keys": [("Players", "Spawn_With_Max_Skills", "True")]},
    {"id": "free_compass", "icon": "🧭", "name": "无需指南针即可显示方向",
     "desc": "玩家不用捡指南针, 屏幕上一直显示自己的朝向(北/东/南/西), 萌新不再迷路",
     "keys": [("Gameplay", "Compass", "True")]},
    {"id": "free_satellite", "icon": "🛰️", "name": "无需GPS即可开启卫星地图",
     "desc": "玩家不用捡 GPS 物品, 打开地图就能看卫星地图(全图)",
     "keys": [("Gameplay", "Satellite", "True")]},
    {"id": "free_chart", "icon": "🗺️", "name": "无需手绘地图即可开启手绘地图",
     "desc": "玩家不用捡纸质/手绘地图物品, 打开地图就能看手绘地图(建筑轮廓那张)",
     "keys": [("Gameplay", "Chart", "True")]},
]

SELECTS = [
    {"id": "zombie_power", "icon": "🧟", "name": "僵尸强度",
     "section": "Zombies", "key": "Damage_Multiplier",
     "desc": "僵尸攻击玩家的伤害倍率",
     "opts": [("0.5", "轻松"), ("1", "普通"), ("1.5", "困难"), ("3", "地狱")]},
    {"id": "exp_rate", "icon": "🔥", "name": "经验倍率",
     "section": "Players", "key": "Experience_Multiplier",
     "desc": "做任务、杀僵尸获得经验的速度",
     "opts": [("1", "1倍"), ("2", "2倍"), ("5", "5倍"), ("10", "10倍")]},
    {"id": "loot", "icon": "📦", "name": "物资丰富度",
     "section": "Items", "key": "Spawn_Chance",
     "desc": "地图上刷出的物品数量",
     "opts": [("0.2", "稀缺"), ("0.35", "默认"), ("0.6", "充足"), ("1", "海量")]},
    {"id": "weather", "icon": "🌧️", "name": "天气系统",
     "section": "Events", "key": "Weather_Duration_Multiplier",
     "desc": "下雨、下雪等天气变化",
     "opts": [("0", "关闭"), ("1", "默认"), ("0.5", "更频繁")]},
]

# ================================================================ 玩法设置表单 (Config.txt 汉化)
# 每项: (键, 中文名, 类型, 短提示, 规格(lo, hi, 游戏默认), 详细说明)
#   规格里的 None = 这一头没有界限; 默认写字符串 "mode" = 由「游戏难度」决定, 不是一个固定数。
#   范围取的是游戏真正认的数(概率类一律 0~1, 倍率类一律 >=0), 填超了保存会被拦下来。
GAMEPLAY_FIELDS = [
    ("Players", "玩家", [
        ("Health_Default", "出生生命值", "number", "", (1, 1000, 100),
         "刚进服有多少血。100 是原版; 填 500 就是砍不死的爽服, 填 1 就是被僵尸蹭一下就没。"),
        ("Food_Default", "出生饱食度", "number", "", (0, 100, "mode"),
         "刚进服肚子有多饱, 满值 100。0 = 一出生就在饿死边缘, 得马上找吃的。"),
        ("Water_Default", "出生含水量", "number", "", (0, 100, "mode"),
         "刚进服有多渴, 满值 100。和饱食度一样, 掉到 0 会开始掉血。"),
        ("Armor_Multiplier", "玩家受伤倍率", "number", "0=无敌", (0, None, 1),
         "所有打在你身上的伤害乘多少倍。0 = 完全不掉血(想无敌就填 0), 0.5 = 只吃一半伤害, 2 = 双倍疼。"),
        ("Experience_Multiplier", "经验获取倍率", "number", "", (0, None, "mode"),
         "杀僵尸、做动作拿到的经验乘多少倍。填 3~5 半小时技能毕业, 填 0 就永远 0 级。"),
        ("Skill_Cost_Multiplier", "技能升级消耗倍率", "number", "", (0.1, None, 1),
         "点一级技能要花多少经验。0.5 = 半价练级; 这一项别填 0, 游戏会算不出等级。"),
        ("Can_Hurt_Legs", "摔落伤害", "bool", "", (None, None, True),
         "开 = 从高处跳下来会摔断腿(得等一会儿或者让人扶); 关 = 随便跳楼。"),
        ("Can_Start_Bleeding", "会流血", "bool", "", (None, None, True),
         "开 = 被枪、近战、僵尸抓伤之后会持续流血, 必须用绷带止血; 关 = 只掉血不会流血, 新手服建议关。"),
        ("Allow_Instakill_Headshots", "狙击爆头一击必杀", "bool", "", (None, None, False),
         "开 = 打中头直接秒人, 不管还剩多少血; 关 = 爆头只是伤害高一些。PVP 服开了会非常劝退。"),
        ("Spawn_With_Max_Skills", "出生满技能", "bool", "", (None, None, False),
         "开 = 一出生所有技能直接满级, 完全不用练。和「经验倍率」同时开的话这个更省事。"),
        ("Lose_Items_PvP", "PVP死亡掉落物品", "number", "0~1", (0, 1, 0),
         "被人打死时背包掉多少。0 = 一件不掉, 1 = 全掉地上, 0.5 = 随机掉一半。只在 PVP 打架时生效。"),
        ("Lose_Items_PvE", "PVE死亡掉落物品", "number", "0~1", (0, 1, 0),
         "被僵尸打死时背包掉多少, 同上。想让服轻松一点就填 0。"),
        ("Lose_Clothes_PvP", "PVP死亡掉落穿戴", "bool", "", (None, None, False),
         "开 = 被打死时身上穿的衣服、头盔、背包也会掉出来。"),
        ("Lose_Clothes_PvE", "PVE死亡掉落穿戴", "bool", "", (None, None, False),
         "开 = 被僵尸打死时身上穿的装备会掉出来。"),
        ("Lose_Weapons_PvP", "PVP死亡掉落武器", "bool", "", (None, None, False),
         "开 = 被打死时手上拿着的那把枪会掉在地上(单独算, 不含背包)。"),
        ("Lose_Weapons_PvE", "PVE死亡掉落武器", "bool", "", (None, None, False),
         "开 = 被僵尸打死时手上拿着的枪会掉出来。"),
    ]),
    ("Zombies", "僵尸", [
        ("Spawn_Chance", "僵尸刷新率", "number", "0~1", (0, 1, "mode"),
         "刷新点长出僵尸的概率。0 = 整个世界一只僵尸都没有(纯养老服), 1 = 到处挤满。"),
        ("Damage_Multiplier", "僵尸伤害倍率", "number", "", (0, None, "mode"),
         "僵尸打你有多疼。0 = 站着让僵尸咬也不掉血, 2 = 两三口没命。"),
        ("Armor_Multiplier", "僵尸承伤倍率", "number", "越低越耐打", (0, None, "mode"),
         "你打僵尸的伤害乘多少倍 —— 这个数字越小僵尸越硬。想两枪一个就填 3, 填 0 僵尸就彻底打不死。"),
        ("Sprinter_Chance", "疾跑僵尸概率", "number", "0~1", (0, 1, "mode"),
         "刷出来的僵尸里有多少比例是会全速冲脸的疾跑者。填 0 就没有追着你跑的僵尸了。"),
        ("Crawler_Chance", "爬行僵尸概率", "number", "0~1", (0, 1, "mode"),
         "趴在地上、靠近才爬起来的那批僵尸的比例。填 0 就没有这种突然惊吓。"),
        ("Loot_Chance", "僵尸掉落物品概率", "number", "0~1", (0, 1, "mode"),
         "僵尸死后身上有东西可捡的概率。1 = 每只都掉, 想要刷物资可以调高。"),
        ("Respawn_Day_Time", "僵尸复活时间(秒)", "number", "", (1, None, None),
         "白天的尸体过多少秒重新刷成一只新的。数字越小刷得越快, 基地门口会被刷个不停。"),
        ("Can_Target_Barricades", "僵尸攻击家具", "bool", "", (None, None, True),
         "开 = 僵尸会锤你家的门、路障和栏杆; 关 = 僵尸完全不碰家具, 围起来的基地绝对安全。"),
        ("Can_Target_Structures", "僵尸攻击建筑", "bool", "", (None, None, True),
         "开 = 僵尸会拆墙拆地板; 关 = 建筑打不动, 不怕僵尸潮。"),
        ("Can_Target_Vehicles", "僵尸攻击载具", "bool", "", (None, None, True),
         "开 = 僵尸会砸车, 车停在野外容易被挠坏挠炸。"),
    ]),
    ("Items", "物品", [
        ("Spawn_Chance", "物品刷新率", "number", "0~1", (0, 1, "mode"),
         "房子里的物资刷出来的概率, 这就是「抢不抢得到东西」的关键。1 = 每个刷新点都出东西。"),
        ("Respawn_Time", "物品刷新间隔(秒)", "number", "", (1, None, "mode"),
         "被你捡空的房子过多少秒重新长出物资。想「跑一圈回来又满了」就填小一点, 50 左右很舒服。"),
        ("Despawn_Dropped_Time", "丢在地上的物品消失时间(秒)", "number", "", (10, None, None),
         "扔在地上的东西多少秒之后消失。填太小会出现「刚扔的枪转头就没了」, 建议 600 以上。"),
        ("Has_Durability", "物品耐久损耗", "bool", "", (None, None, True),
         "开 = 枪械和武器用久了会磨损坏掉, 得用维修包; 关 = 武器永久不坏, 养老服推荐关。"),
    ]),
    ("Vehicles", "载具", [
        ("Armor_Multiplier", "载具受伤倍率", "number", "0=无敌", (0, None, 1),
         "打在车上的伤害乘多少倍。0 = 车怎么打都不会炸, 3 以上就成了纸糊的, 一枪就爆。"),
        ("Decay_Time", "载具自然损坏时间(秒)", "number", "", (60, None, None),
         "车放着不管多久开始自己老化(掉油、掉轮胎)。想要车永远不旧, 填一个很大的数比如 999999999。"),
        ("Respawn_Time", "载具爆炸后重刷时间(秒)", "number", "", (10, None, None),
         "车被炸毁之后过多少秒在原刷车点重新刷一辆出来。"),
        ("Has_Battery_Chance", "刷车自带电瓶概率", "number", "0~1", (0, 1, "mode"),
         "刷出来的车自带电瓶的概率。填 1 就不用满地图翻电瓶了, 新手会很感谢你。"),
        ("Has_Tire_Chance", "刷车自带轮胎概率", "number", "0~1", (0, 1, "mode"),
         "刷出来的车自带轮胎的概率。填 1 = 每辆车下来就能开走。"),
        ("Max_Instances_Medium", "刷车上限(中型地图)", "number", "", (1, 100, 16),
         "中型地图(PEI 这类)同一时间路上最多存在多少辆车。越多越热闹, 也更吃服务器性能。"),
    ]),
    ("Barricades", "家具/路障", [
        ("Decay_Time", "家具自然损坏时间(秒)", "number", "", (60, None, None),
         "家具路障放着不管多久开始老化损坏。填很大的数基本就等于永远不会坏。"),
        ("Armor_Lowtier_Multiplier", "低级家具承伤倍率", "number", "0=无敌", (0, None, 1),
         "木门、铁丝网这类低级家具的承伤倍率。0 = 怎么砸都不坏。"),
        ("Armor_Hightier_Multiplier", "高级家具承伤倍率", "number", "", (0, None, 0.5),
         "铁柜、水泥这一类的承伤倍率, 原版默认 0.5(比低级家具更耐打)。数字越小越硬。"),
        ("Melee_Damage_Multiplier", "近战对家具伤害倍率", "number", "", (0, None, 1),
         "拿冷兵器砸家具的伤害乘多少倍。0 = 砍不坏别人家, 想防砍家可以调这个而不是把家具设成无敌。"),
    ]),
    ("Structures", "建筑", [
        ("Decay_Time", "建筑自然损坏时间(秒)", "number", "", (60, None, None),
         "玩家盖的建筑多久开始老化。留太长会出现一堆没人玩的破房子占着地图。"),
        ("Armor_Lowtier_Multiplier", "低级建筑承伤倍率", "number", "0=无敌", (0, None, 1),
         "木墙木地板这类低级墙的承伤倍率。0 = 拆不掉。"),
        ("Armor_Hightier_Multiplier", "高级建筑承伤倍率", "number", "", (0, None, 0.5),
         "水泥、金属墙这类高级墙的承伤倍率, 原版默认 0.5。想防抄家就调小, 别调成 0。"),
        ("Melee_Damage_Multiplier", "近战对建筑伤害倍率", "number", "", (0, None, 1),
         "冷兵器拆墙的伤害乘多少倍。0 = 只能靠枪和爆炸物拆家。"),
    ]),
    ("Gameplay", "玩法规则", [
        ("Hitmarkers", "命中标记", "bool", "打中人出现白叉", (None, None, True),
         "开 = 打中人的瞬间屏幕中间出现白叉, 告诉你「这枪打到了」。新手很有用, 硬核服会关。"),
        ("Crosshair", "显示准星", "bool", "", (None, None, True),
         "开 = 屏幕上一直有个准星; 关 = 只能靠机瞄或者拉栓感觉, 老玩家喜欢。"),
        ("Chart", "常驻纸质地图", "bool", "不用捡地图物品", (None, None, True),
         "开 = 玩家一直带着手绘地图, 不用去捡地图物品。和下面的卫星地图是两张不同的图。"),
        ("Satellite", "常驻卫星地图", "bool", "", (None, None, True),
         "开 = 一直有卫星地图(看得清地形和建筑)。想省事就 Chart 和这个一起开。"),
        ("Compass", "常驻指南针", "bool", "", (None, None, True),
         "开 = 右上角一直有指南针。想关掉请直接用「一键设置」里的免指南针开关。"),
        ("Group_Map", "队友显示在地图上", "bool", "", (None, None, True),
         "开 = 组队时队友的小圆点会显示在地图上, 走散了能找到人。"),
        ("Group_HUD", "队友名字透视", "bool", "", (None, None, True),
         "开 = 队友名字隔着墙也看得见。PVE 合作很有用; PVP 服别开, 这等于给队友开透视。"),
        ("Friendly_Fire", "组队友伤", "bool", "", (None, None, False),
         "开 = 打队友会真的扣队友的血; 关 = 误伤自己人没伤害, 人多的小队建议关。"),
        ("Can_Suicide", "允许自杀按钮", "bool", "", (None, None, True),
         "开 = 玩家菜单里有自杀选项。卡进地形、掉下悬崖出不来时全靠它, 关掉容易收到投诉。"),
        ("Timer_Respawn", "死亡后重生等待(秒)", "number", "", (0, 300, 0),
         "死了之后要站多少秒才能点重生。0 = 立刻重生; 想硬核一点填 20~30。"),
        ("Timer_Home", "回床等待(秒)", "number", "", (0, 600, 60),
         "按 /home 回家之前要站住不动多少秒。中途被打就重新计时, 防止打架时秒回家。"),
        ("Timer_Exit", "退出服务器等待(秒)", "number", "", (0, 600, 20),
         "玩家点退出时要停留多少秒, 避免手滑退出去。"),
        ("Bypass_Building_In_Safezones", "允许安全区内建造", "bool", "", (None, None, False),
         "开 = 在加油站、城市中心这些安全区也能放家具盖建筑。原版不允许, 开了会出现堵门房。"),
        ("Allow_Shoulder_Camera", "第三人称越肩视角", "bool", "", (None, None, True),
         "开 = 第三人称可以切到越肩视角(手感接近第一人称); 关 = 只能是身后拉远视角。"),
    ]),
    ("Events", "事件", [
        ("Use_Airdrops", "开启空投", "bool", "", (None, None, True),
         "开 = 空投会按时掉下来。养老服、不想让玩家抢空投打架可以关。"),
        ("Airdrop_Frequency_Min", "空投间隔倍率(最小)", "number", "是倍率不是天数", (0.1, None, 1),
         "空投间隔的倍率, 不是天数 —— 1 = 原版节奏, 0.5 = 空投来得更勤一倍, 3 = 很久才来一次。"),
        ("Airdrop_Frequency_Max", "空投间隔倍率(最大)", "number", "要 ≥ 最小值", (0.1, None, 1),
         "间隔在最小和最大之间随机取。这一项必须大于等于上面的最小值, 不然游戏会取不到数。"),
        ("Weather_Duration_Multiplier", "天气时长倍率", "number", "0=关闭天气", (0, None, 1),
         "一场雷雨、浓雾持续多久。0 = 天气系统不再来, 1 = 原版, 想天天起雾就填大。"),
        ("Arena_Min_Players", "竞技场最少队伍数", "number", "", (1, 32, 2),
         "竞技场活动要凑够几队才会开始。不开竞技场就不用管这一项。"),
    ]),
    ("Server", "网络/安全", [
        ("Max_Ping_Milliseconds", "高延迟踢出(毫秒)", "number", "默认750", (100, 5000, 750),
         "玩家延迟超过多少毫秒就请他出去。原版 750 对国内联机太狠了, 建议 1500~2000, 不然朋友进不来。"),
        ("Timeout_Game_Seconds", "无响应踢出(秒)", "number", "", (1, 600, 20),
         "客户端多久不回话就判定掉线踢出。网络不稳可以把这一项调大一点, 太小会频繁被踢。"),
        # VAC_Secure / BattlEye_Secure 从 v0.1.17 起挪到「服务器设置」页那张反作弊卡片单独管:
        # 同一个键在两个页面各有一份表单值, 后保存的会把先改的覆盖回去
    ]),
]

# ================================================================ 高级配置 (玩法设置没列的其余 Config.txt 参数)
# 每项: (键, 中文名, 类型, 一行说明)。类型 num=非负数字 / pct=0~1 的概率 / count=整数 / bool=开关 / text=一行文字。
# 这批是原版 Config.txt 里剩下没被表单收录的键: 官方没公布安全范围, 所以 num 只挡负数和乱码, 不设上下限,
# 说明里只写"往哪边调会发生什么", 拿不准的一律在说明里点明"别动"。
# 值留空 = 写回原版默认 (这一项交还给游戏自己决定)。
ADVANCED_SECTIONS = [
    ("Players", "玩家 (进阶)", [
        ("Health_Regen_Min_Food", "回血所需最低饱食度", "num",
         "饱食度低于这个数就不会自动回血 (原版普通难度 90)。"),
        ("Health_Regen_Min_Water", "回血所需最低含水量", "num",
         "和上面那条一样, 看的是水分。低于这个数不回血。"),
        ("Health_Regen_Ticks", "自动回血间隔", "num",
         "隔多久回一次血, 数字越小回得越勤 (原版 60)。"),
        ("Food_Use_Ticks", "饱食度下降间隔", "num",
         "隔多久掉一格饱食度, 数字越小饿得越快 (简单 350 / 普通 300 / 困难 250)。"),
        ("Food_Damage_Ticks", "饿到见底掉血间隔", "num",
         "饱食度掉到 0 之后, 隔多久扣一次血 (原版 15)。"),
        ("Water_Use_Ticks", "含水量下降间隔", "num",
         "数字越小渴得越快 (简单 320 / 普通 270 / 困难 220)。"),
        ("Water_Damage_Ticks", "渴到见底掉血间隔", "num",
         "水分掉到 0 之后, 隔多久扣一次血。"),
        ("Virus_Default", "出生免疫力", "num",
         "一出生有多少免疫值 (原版 100)。免疫掉破底线会开始生病掉血。"),
        ("Virus_Infect", "感染触发线", "num",
         "免疫值掉到这个数以下就一路往下掉 (原版 50)。"),
        ("Virus_Use_Ticks", "免疫力下降间隔", "num", "数字越小免疫掉得越快。"),
        ("Virus_Damage_Ticks", "免疫见底掉血间隔", "num",
         "免疫掉到 0 之后, 隔多久扣一次血。"),
        ("Leg_Regen_Ticks", "断腿恢复时长", "num",
         "腿断了要多久自己长好 (原版 750)。数字越大断腿越难受。"),
        ("Bleed_Regen_Ticks", "流血自愈时长", "num",
         "不用绷带时, 流血状态多久自己停 (原版 750)。"),
        ("Bleed_Damage_Ticks", "流血掉血间隔", "num", "流血期间隔多久扣一次血。"),
        ("Can_Fix_Legs", "可以自行接骨", "bool",
         "开 = 能用夹板一类物品把断腿治好; 关 = 只能等它自己长。"),
        ("Can_Stop_Bleeding", "可以止血", "bool",
         "开 = 绷带能止血; 关 = 止血不了, 只能等流血自己停。"),
        ("Detect_Radius_Multiplier", "僵尸与动物的察觉距离倍率", "num",
         "离多近就会被发现 (简单 0.5 / 普通 1 / 困难 1.25)。想潜服玩就调小。"),
        ("Ray_Aggressor_Distance", "判定凶手的射线距离(米)", "num",
         "系统靠这条射线算「是谁打的」, 关系到伤害归因和战报 (原版 8)。没弄清就别动。"),
        ("Spawn_With_Stamina_Skills", "出生满体力类技能", "bool",
         "开 = 一出生心肺、健身、潜水、跑酷这几项直接满级, 不用练。"),
        ("Skillset_Reduces_Skill_Cost", "职业套装减少技能消耗", "bool",
         "开 = 穿上对应职业套装时, 升级技能的经验打折。"),
        ("Skillset_Prevents_Skill_Loss", "职业套装防止掉技能", "bool",
         "开 = 穿着对应套装时死亡不会掉技能等级。"),
        ("Prevent_Level_Skill_Overrides", "禁止等级覆盖技能", "bool",
         "开 = 不让游戏的等级/难度规则回头改写玩家已经点好的技能。"),
        ("Allow_Per_Character_Saves", "允许每个角色独立存档", "bool",
         "开 = 同一个账号可以各练各的角色; 联机服默认是关的。"),
        ("Enable_Terrain_Color_Kick", "受伤时画面色彩抖动", "bool",
         "纯粹是画面效果: 开 = 挨打时地面颜色会闪一下。想减少晕动感就关。"),
    ]),
    ("Zombies", "僵尸 (进阶)", [
        ("Flanker_Chance", "包抄僵尸出现概率", "pct",
         "刷出来的僵尸里有多少是会绕路包抄你的。"),
        ("Burner_Chance", "自燃僵尸出现概率", "pct",
         "身上带火、靠近会点着你的那种僵尸的比例。"),
        ("Acid_Chance", "吐酸僵尸出现概率", "pct",
         "会远程吐酸水的那种僵尸的比例。"),
        ("Spirit_Chance", "幽灵僵尸出现概率", "pct",
         "半透明、不容易看见的那批僵尸的比例。"),
        ("Boss_Electric_Chance", "电系精英僵尸出现概率", "pct",
         "带放电效果的精英怪出现概率。"),
        ("Boss_Wind_Chance", "风系精英僵尸出现概率", "pct", "带风效果的精英怪出现概率。"),
        ("Boss_Fire_Chance", "火系精英僵尸出现概率", "pct", "带火效果的精英怪出现概率。"),
        ("Boss_Elver_Stomper_Chance", "踩踏型精英出现概率", "pct",
         "体型大、靠踩踏攻击的那只精英怪出现概率。"),
        ("Boss_Kuwait_Chance", "特定精英僵尸出现概率", "pct",
         "官方维基没有这一项的说明, 是某张地图专属的精英怪。不知道就别动。"),
        ("DL_Red_Volatile_Chance", "红色易爆僵尸概率", "pct",
         "打死会爆炸的红色变异僵尸比例。"),
        ("DL_Blue_Volatile_Chance", "蓝色易爆僵尸概率", "pct",
         "打死会爆炸的蓝色变异僵尸比例。"),
        ("Respawn_Night_Time", "夜里尸体复活时间(秒)", "num",
         "晚上的尸体过多少秒刷回一只新僵尸; 数字越小夜里越凶。"),
        ("Respawn_Beacon_Time", "僵尸信标重生时间(秒)", "num",
         "信标被拆掉之后, 过多少秒重新刷出来。"),
        ("Quest_Boss_Respawn_Interval", "任务首领重生间隔", "num",
         "任务用的那只精英怪多久重新刷一只。"),
        ("Backstab_Multiplier", "背刺伤害倍率", "num",
         "从背后偷袭僵尸时伤害乘多少。"),
        ("NonHeadshot_Armor_Multiplier", "非爆头部位承伤倍率", "num",
         "打身体(不打头)时僵尸挨多少伤害; 数字越大越好打。"),
        ("Beacon_Experience_Multiplier", "拆信标经验倍率", "num",
         "清掉一个僵尸信标给的经验乘多少。"),
        ("Full_Moon_Experience_Multiplier", "满月夜经验倍率", "num",
         "月圆那晚打僵尸的经验乘多少。"),
        ("Min_Drops", "僵尸最少掉几件", "count", "每只僵尸死亡保底掉几样东西。"),
        ("Max_Drops", "僵尸最多掉几件", "count", "每只僵尸最多掉几样, 和上面的最小值一起决定随机区间。"),
        ("Min_Mega_Drops", "巨型僵尸最少掉几件", "count", "大块头僵尸死亡保底掉落数。"),
        ("Max_Mega_Drops", "巨型僵尸最多掉几件", "count", "大块头僵尸死亡掉落上限。"),
        ("Min_Boss_Drops", "精英最少掉几件", "count", "精英僵尸死亡保底掉落数。"),
        ("Max_Boss_Drops", "精英最多掉几件", "count", "精英僵尸死亡掉落上限。"),
        ("Slow_Movement", "僵尸整体减速", "bool",
         "开 = 僵尸移动速度整体降一档, 跑得没玩家快。"),
        ("Can_Stun", "僵尸可被击晕", "bool",
         "开 = 挨重击时僵尸会短时间僵住不动。"),
        ("Only_Critical_Stuns", "只有暴击才能击晕", "bool",
         "开 = 只有打出暴击那一下才把僵尸打晕。"),
        ("Can_Target_Objects", "僵尸会破坏场景物件", "bool",
         "开 = 僵尸会推倒树、集装箱一类场景物件; 关 = 只追人不动东西。"),
        ("Weapons_Use_Player_Damage", "僵尸用玩家伤害倍率", "bool",
         "开 = 僵尸捡起武器打人时, 按「玩家受伤倍率」那一套算伤害, 不再单独乘僵尸自己的系数。"),
        ("Beacon_Max_Rewards", "信标最多发几个奖励", "count",
         "一个信标被拆掉时最多掉几份奖励。"),
        ("Beacon_Max_Participants", "信标最多算几个人", "count",
         "拆信标时最多给前几名参与的人记功、发奖励。"),
        ("Beacon_Rewards_Multiplier", "信标奖励倍率", "num",
         "信标奖励的数量乘多少。"),
    ]),
    ("Animals", "动物 (进阶)", [
        ("Respawn_Time", "动物复活时间(秒)", "num",
         "鹿、熊这类动物被打死之后, 过多久在同一带刷回来。"),
        ("Damage_Multiplier", "动物伤害倍率", "num",
         "动物咬你有多疼。0 = 咬不动。"),
        ("Armor_Multiplier", "动物承伤倍率", "num",
         "你打动物时伤害乘多少; 数字越小动物越耐打。"),
        ("Max_Instances_Tiny", "小地图上动物数量上限", "count", "地图尺寸很小时全图最多同时存在多少只动物。"),
        ("Max_Instances_Small", "较小地图动物上限", "count", "地图尺寸「小」时的同屏总量上限。"),
        ("Max_Instances_Medium", "中等地图动物上限", "count", "地图尺寸「中」时的总量上限。"),
        ("Max_Instances_Large", "大地图动物上限", "count", "地图尺寸「大」时的总量上限。"),
        ("Max_Instances_Insane", "超大地图动物上限", "count", "地图尺寸「疯狂」时的总量上限。"),
        ("Weapons_Use_Player_Damage", "动物用玩家伤害倍率", "bool",
         "开 = 动物拿武器时按玩家伤害那一套算, 不再单独乘动物的系数。"),
    ]),
    ("Vehicles", "载具 (进阶)", [
        ("Min_Battery_Charge", "刷出来的车最低电量", "pct",
         "车上电瓶至少有几分电 (0~1)。填 1 = 每辆车都满电。"),
        ("Max_Battery_Charge", "刷出来的车最高电量", "pct",
         "车上电瓶最多带几分电 (0~1)。和最低电量一起决定随机区间。"),
        ("Min_Natural_Vehicles", "地图上至少有多少台车", "count",
         "自然刷新的车辆数量底线, 免得整张图找不着一台车。"),
        ("Max_Instances_Tiny", "小地图车辆数量上限", "count", "地图尺寸很小时全图最多同时存在多少台车。"),
        ("Max_Instances_Small", "较小地图车辆上限", "count", "地图尺寸「小」时的车辆总量上限。"),
        ("Max_Instances_Large", "大地图车辆上限", "count", "地图尺寸「大」时的车辆总量上限。"),
        ("Max_Instances_Insane", "超大地图车辆上限", "count", "地图尺寸「疯狂」时的车辆总量上限。"),
        ("Melee_Repair_Multiplier", "近战修理车辆倍率", "num",
         "拿锤子敲车能修回多少; 数字越大一锤顶得越多。"),
        ("Unlocked_After_Seconds_In_Safezone", "停进安全区多久自动解锁", "num",
         "车在安全区里停够这么多秒就自动上锁/解锁到位, 免得在区域内抢车。"),
    ]),
    ("Barricades", "家具与路障 (进阶)", [
        ("Melee_Repair_Multiplier", "近战修理家具倍率", "num",
         "拿锤子修补路障、栏杆这类家具能修回多少, 数字越大越省材料。"),
        ("Allow_Item_Placement_On_Vehicle", "允许在车上放家具", "bool",
         "开 = 能把箱子、架子固定到车体上; 关 = 车上放不了。"),
        ("Allow_Trap_Placement_On_Vehicle", "允许在车上放陷阱", "bool",
         "开 = 能把地刺、电线一类的陷阱装到车上。"),
        ("Max_Item_Distance_From_Hull", "车上物品离车身最远距离", "num",
         "装车上的家具允许超出车身多少; 太大就会出现悬在车外的东西。"),
        ("Max_Trap_Distance_From_Hull", "车上陷阱离车身最远距离", "num",
         "装车的陷阱允许超出车身多少, 和上面那条同理。"),
    ]),
    ("Structures", "建筑 (进阶)", [
        ("Melee_Repair_Multiplier", "近战修理建筑倍率", "num",
         "拿锤子修墙、地板能修回多少, 数字越大越省材料。"),
    ]),
    ("Items", "物品 (进阶)", [
        ("Despawn_Natural_Time", "地上物品多久消失(秒)", "num",
         "掉在地上的东西过多久自然消失。调大能保住朋友掉在地上的背包。"),
        ("Quality_Full_Chance", "物资直接满品质的概率", "pct",
         "捡起来就是全新的东西占多少比例。"),
        ("Quality_Multiplier", "物资品质倍率", "num",
         "刷出来的东西耐用品质的整体倍率; 调大 = 满地都是好东西。"),
        ("Gun_Bullets_Full_Chance", "枪械自带满弹概率", "pct",
         "刷出来的枪一拿到手就是满弹匣的比例。"),
        ("Gun_Bullets_Multiplier", "枪械子弹倍率", "num",
         "枪里带多少发子弹的整体倍率。"),
        ("Magazine_Bullets_Full_Chance", "弹匣满弹概率", "pct",
         "捡到的弹匣本来就是满的比例。"),
        ("Magazine_Bullets_Multiplier", "弹匣子弹倍率", "num",
         "刷出来的弹匣里装多少发的整体倍率。"),
        ("Crate_Bullets_Full_Chance", "弹药箱满弹概率", "pct",
         "弹药箱一刷新就是满的比例。"),
        ("Crate_Bullets_Multiplier", "弹药箱子弹倍率", "num",
         "弹药箱里的子弹数量倍率。"),
        ("Food_Spawns_At_Full_Quality", "食物总是满品质刷新", "bool",
         "开 = 找到的罐头、零食一律是全新的, 不会放着放着坏掉。"),
        ("Water_Spawns_At_Full_Quality", "饮水总是满品质刷新", "bool",
         "开 = 瓶装水、水桶一类刷新出来就是满状态。"),
        ("Clothing_Spawns_At_Full_Quality", "衣服总是满品质刷新", "bool",
         "开 = 捡到的护甲、衣服一上手就是全新的。"),
        ("Weapons_Spawn_At_Full_Quality", "武器总是满品质刷新", "bool",
         "开 = 捡到的枪和近战武器一律全新, 不用先修一遍。"),
        ("Default_Spawns_At_Full_Quality", "其余物品总是满品质刷新", "bool",
         "开 = 上面没单列的所有物品都按全新刷新。"),
        ("Clothing_Has_Durability", "衣服会穿坏", "bool",
         "开 = 护甲和衣服会掉耐久、最终穿破; 关 = 永远不坏。"),
        ("Weapons_Have_Durability", "武器会用坏", "bool",
         "开 = 枪和近战武器会掉耐久、需要修理; 关 = 打不坏。"),
    ]),
    ("Objects", "场景物件 (进阶)", [
        ("Resource_Reset_Multiplier", "采集点恢复倍率", "num",
         "树、矿石这类采集点刷回可采状态的速度, 数字越小刷得越快。"),
        ("Resource_Drops_Multiplier", "采集点产出倍率", "num",
         "砍树、挖矿一次给多少材料。"),
        ("Water_Reset_Multiplier", "水源恢复倍率", "num",
         "水桶、水洼被舀空之后多久恢复, 数字越小越快。"),
        ("Fuel_Reset_Multiplier", "油料点恢复倍率", "num",
         "加油机这类油料点被抽干后恢复的速度。"),
        ("Binary_State_Reset_Multiplier", "开关类物件复位倍率", "num",
         "门、集装箱、抽屉这类「开/关」状态物件恢复的速度, 数字越小越快。"),
        ("Rubble_Reset_Multiplier", "废墟复位倍率", "num",
         "被打坏的墙体、家具变回的瓦砾堆多久恢复。"),
        ("Allow_Holiday_Drops", "允许节日掉落物", "bool",
         "开 = 万圣节、圣诞这类节日期间场景会掉对应道具; 关 = 不掉落。"),
        ("Items_Obstruct_Tree_Respawns", "有物品挡着就不刷树", "bool",
         "开 = 树的位置上还有掉落物时, 这棵树不刷新 (原版行为); 关 = 照样刷。"),
    ]),
    ("Events", "空投与天气 (进阶)", [
        ("Weather_Frequency_Multiplier", "天气变化频率倍率", "num",
         "所有天气切换的整体速度。数字越大天气换得越勤。"),
        ("Rain_Frequency_Min", "下雨最短间隔", "num", "两场雨之间最少等多久。"),
        ("Rain_Frequency_Max", "下雨最长间隔", "num", "两场雨之间最多等多久。"),
        ("Rain_Duration_Min", "下雨最短持续", "num", "一场雨最少下多久。"),
        ("Rain_Duration_Max", "下雨最长持续", "num", "一场雨最多下多久。"),
        ("Snow_Frequency_Min", "下雪最短间隔", "num", "两场雪之间最少等多久。"),
        ("Snow_Frequency_Max", "下雪最长间隔", "num", "两场雪之间最多等多久。"),
        ("Snow_Duration_Min", "下雪最短持续", "num", "一场雪最少下多久。"),
        ("Snow_Duration_Max", "下雪最长持续", "num", "一场雪最多下多久。"),
        ("Airdrop_Speed", "空投下落速度", "num",
         "空投降落伞往下飘的速度, 调慢大家来得及抢。"),
        ("Airdrop_Force", "空投落地冲击", "num",
         "空投砸地上的力度; 调太大落点附近的人和建筑会被砸坏。"),
        ("Arena_Clear_Timer", "竞技场清场计时(秒)", "num",
         "竞技场事件判定「这一波清完」要等多久。"),
        ("Arena_Finale_Timer", "竞技场决赛计时(秒)", "num", "最后一波给多少秒。"),
        ("Arena_Restart_Timer", "竞技场重开计时(秒)", "num", "一波打完到下一波开始之间等多久。"),
        ("Arena_Use_Compactor_Pause", "竞技场压缩墙启用暂停", "bool",
         "开 = 压缩墙在波次之间会停下来等玩家喘口气。"),
        ("Arena_Compactor_Pause_Timer", "压缩墙暂停时长(秒)", "num",
         "压缩墙中间歇多久再继续收。"),
        ("Arena_Compactor_Delay_Timer", "压缩墙启动延迟(秒)", "num",
         "竞技场开场多久之后压缩墙才开始往里收。"),
        ("Arena_Compactor_Shrink_Factor", "压缩墙收缩系数", "num",
         "压缩墙往里收的幅度; 数字越大圈缩得越狠。"),
        ("Arena_Compactor_Damage", "压缩墙伤害", "num",
         "被压缩墙压到一次扣多少血。"),
        ("Arena_Compactor_Extra_Damage_Per_Second", "压缩墙每秒附加伤害", "num",
         "一直待在墙里时每秒额外扣多少血。"),
        ("Arena_Compactor_Speed_Tiny", "最小地图压缩墙速度", "num",
         "竞技场所在地图尺寸「极小」时墙收拢的速度。"),
        ("Arena_Compactor_Speed_Small", "小地图压缩墙速度", "num", "地图尺寸「小」时墙收拢的速度。"),
        ("Arena_Compactor_Speed_Medium", "中等地图压缩墙速度", "num", "地图尺寸「中」时墙收拢的速度。"),
        ("Arena_Compactor_Speed_Large", "大地图压缩墙速度", "num", "地图尺寸「大」时墙收拢的速度。"),
        ("Arena_Compactor_Speed_Insane", "超大地图压缩墙速度", "num", "地图尺寸「疯狂」时墙收拢的速度。"),
    ]),
    ("Gameplay", "玩法与手感 (进阶)", [
        ("Ballistics", "启用真实弹道", "bool",
         "开 = 子弹走弹道 (受下坠等影响); 关 = 按传统射线判定命中。改这一项对枪感影响很大。"),
        ("Repair_Level_Max", "修理可用技能上限", "count",
         "修东西最高能吃到几级修理技能。"),
        ("Allow_Static_Groups", "允许固定组队", "bool", "开 = 玩家可以建长期固定的队伍。"),
        ("Allow_Dynamic_Groups", "允许临时组队", "bool", "开 = 允许临时凑的动态队伍。"),
        ("Allow_Lobby_Groups", "允许大厅组队", "bool", "开 = 进服之前就能在列表上先组队。"),
        ("Group_Player_List", "界面显示队友列表", "bool",
         "开 = 屏幕上列出当前队友; 关 = 不显示这一栏。"),
        ("Max_Group_Members", "组队人数上限", "count", "一支队伍最多几个人。"),
        ("Timer_Leave_Group", "离线多久自动退队(秒)", "num",
         "队友离线超过这么多秒就自动踢出队伍。"),
        ("Bypass_Buildable_Mobility", "无视家具可移动限制", "bool",
         "开 = 摆放家具不再检查它属不属于「可移动」那一类, 想放就放。"),
        ("Bypass_No_Building_Zones", "无视禁建区", "bool",
         "开 = 地图上标了不能建的区域也照样能建。会让部分地图的规则失去意义, 想清楚再开。"),
        ("Allow_Freeform_Buildables", "允许任意角度摆放", "bool",
         "开 = 家具可以任意角度对着放, 不用只能贴格子。"),
        ("Allow_Freeform_Buildables_On_Vehicles", "车上允许任意角度", "bool",
         "开 = 车上的家具也能任意角度摆放。"),
        ("Allow_Holidays", "启用节日内容", "bool",
         "开 = 万圣节、圣诞这类节日装饰与内容会出现在服里。"),
        ("Enable_Workstation_Requirements", "工作台讲究条件", "bool",
         "开 = 在工作台上做东西要满足技能、工具等条件; 关 = 直接就能做。"),
        ("Enable_Fishing_Catch_Challenge", "钓鱼要搏斗", "bool",
         "开 = 上钩之后要玩那一段搏斗小游戏才能收杆; 关 = 上了钩直接给鱼。"),
        ("Min_Fishing_Bite_Interval", "鱼上钩最短间隔", "num", "甩竿之后最快多久可能有口。"),
        ("Max_Fishing_Bite_Interval", "鱼上钩最长间隔", "num", "最慢多久一定有口, 和上面那条一起决定随机区间。"),
        ("Fishing_MaxStrength_Bite_Interval_Multiplier", "体力满时咬钩间隔倍率", "num",
         "玩家体力满的时候咬钩间隔乘多少。"),
        ("Explosion_Launch_Speed_Multiplier", "爆炸抛飞速度倍率", "num",
         "被炸飞时人和物飞出去的速度乘多少。"),
        ("AirStrafing_Acceleration_Multiplier", "空中平移加速倍率", "num",
         "在半空中左右移动的加速手感乘多少。"),
        ("AirStrafing_Deceleration_Multiplier", "空中平移减速倍率", "num",
         "在半空中刹车的减速乘多少, 调大了会有点飘。"),
        ("FirstPerson_RecoilMultiplier", "第一人称后坐力倍率", "num",
         "第一人称开枪时的镜头后坐力乘多少。"),
        ("FirstPerson_AimingRecoilMultiplier", "第一人称机瞄后坐力倍率", "num",
         "第一人称开着机瞄射击时的后坐力乘多少。"),
        ("FirstPerson_AimingZoomRecoilReduction", "第一人称开镜后坐力削减", "num",
         "开镜倍率越高削减多少后坐力; 数字大 = 瞄着打更稳。"),
        ("ThirdPerson_RecoilMultiplier", "第三人称后坐力倍率", "num", "第三人称视角下的后坐力乘多少。"),
        ("ThirdPerson_SpreadMultiplier", "第三人称散布倍率", "num", "第三人称视角下的弹散布乘多少。"),
        ("Viewmodel_AimingJumpLandMultiplier", "落地时武器晃动倍率", "num",
         "跳下落地那一下手里那把枪在画面里晃多少。"),
        ("Viewmodel_AimingMisalignmentMultiplier", "瞄准时武器错位倍率", "num",
         "举枪瞄准时画面里的枪和实际准星错开多少。"),
        ("Enable_Damage_Flinch", "挨打时镜头一顿", "bool",
         "开 = 受到伤害时角色和镜头会抽一下; 关 = 不受影响, 想打 PVP 更顺可以关。"),
        ("Enable_Explosion_Camera_Shake", "爆炸时镜头震动", "bool",
         "开 = 炸点附近镜头会抖; 关 = 不抖。"),
        ("Disable_Motion_Sickness_Options", "屏蔽防晕选项", "bool",
         "开 = 玩家客户端里那些「防晕镜头」选项不再由服务器决定。一般不用动。"),
        ("Disable_Foliage_Off", "不许关闭植被", "bool",
         "开 = 不让玩家把草和树关掉, 视野统一、但配置差的玩家会更卡。"),
        ("Use_2D_Scope_Overlay", "瞄准镜用 2D 画面", "bool",
         "开 = 开镜是 2D 遮罩画面 (更省资源); 关 = 用 3D 镜片效果。"),
    ]),
    ("Server", "服务器与防刷屏 (进阶)", [
        ("Timeout_Queue_Seconds", "排队等待超时(秒)", "num",
         "服务器满了以后朋友在队列里最多等多久, 超了就断开。"),
        ("Max_Packets_Per_Second", "每秒数据包上限", "num",
         "单个连接每秒最多发多少个包。调太低正常玩都会掉线, 只在你清楚做什么时改。"),
        ("Join_Rate_Limit_Window_Seconds", "进服频率限制窗口(秒)", "num",
         "在这个时间窗内统计进服次数, 配合下面的阈值挡连点/开挂秒进。"),
        ("Bad_Packet_Rate_Limit_Window_Seconds", "坏包统计窗口(秒)", "num",
         "多久之内统计一次「发坏包」的次数。"),
        ("Bad_Packet_Rate_Limit_Threshold", "坏包次数上限", "count",
         "窗口内坏包到了这个数就进入限制。乱调低会把有 mod 的玩家当外挂踢。"),
        ("Rate_Limit_Kick_Threshold", "超限制直接踢人阈值", "count",
         "超过多少次限制之后直接踢出服务器。"),
        ("Max_Clients_With_Same_IP_Address", "同一 IP 最多几个连接", "count",
         "同一个 IP 地址允许同时连几个人。网吧/学校出口共用 IP 时别设成 1。"),
        ("Max_Clients_With_Same_IP_Address_Log_Warnings", "同 IP 超限写警告日志", "bool",
         "开 = 有人超过上面那个数量时往日志写一条警告。"),
        ("Fake_Lag_Threshold_Seconds", "假延迟判定阈值(秒)", "num",
         "一个包晚到超过这么多秒就算作「假延迟/作弊」, 配合下面两条罚。"),
        ("Fake_Lag_Log_Warnings", "假延迟写警告日志", "bool",
         "开 = 判定到假延迟时往日志写警告。"),
        ("Fake_Lag_Damage_Penalty_Multiplier", "假延迟伤害惩罚倍率", "num",
         "被判假延迟的人打出的伤害乘多少 (调小就是罚他打不动人)。"),
        ("Enable_Kick_Input_Spam", "狂发输入就踢", "bool",
         "开 = 短时间内疯狂上报输入的玩家会被踢; 手残误点宏的玩家可以被冤枉, 想清楚再开。"),
        ("Enable_Kick_Input_Timeout", "输入超时就踢", "bool",
         "开 = 长时间不上报输入(卡住/挂连接)的玩家会被踢下线。"),
        ("Chat_Always_Use_Rich_Text", "聊天一律走富文本", "bool",
         "开 = 聊天框强制用富文本渲染 (颜色、图标那些更完整)。"),
        ("Enable_Scheduled_Shutdown", "启用定时关服", "bool",
         "开 = 服务器到点自己关, 配合下面两条填几点关、提前警告几次。"),
        ("Scheduled_Shutdown_Time", "定时关服时刻", "text",
         "填游戏要求的时刻格式 (例如 04:00)。这一项格式不对不会生效, 建议先在原始编辑器里对照写法。"),
        ("Scheduled_Shutdown_Warnings", "关服前警告次数", "count",
         "定时关服之前分几次向在线玩家广播警告。"),
        ("Enable_Update_Shutdown", "开服端更新后自动关服", "bool",
         "开 = 检测到需要更新时自己关服去更新。挂着朋友在玩时别开。"),
        ("Update_Shutdown_Warnings", "更新关服前警告次数", "count",
         "为了更新而关服之前分几次广播警告。"),
        ("Reset_Vehicles_Outside_Horizontal_Distance", "车离多远才重置(米)", "num",
         "玩家离开车辆多远之后车重置状态 (防止车被丢在半路永远占着)。"),
        ("Validate_EconInfo_Hash", "校验 Steam 经济信息哈希", "bool",
         "和 Steam 物品/经济数据校验有关。P2P 朋友服一般不用动, 不清楚就别关。"),
        ("Use_FakeIP", "使用 Fake IP", "bool",
         "官方服务器列表那边的网络相关开关, 本工具的代码联机用不上, 别动。"),
    ]),
    ("UnityEvents", "地图事件脚本 (进阶)", [
        ("Allow_Server_Messages", "允许服务端消息", "bool",
         "给模组/地图里的事件脚本用: 开 = 允许往服务端发事件消息。不确定就别开。"),
        ("Allow_Server_Commands", "允许服务端指令", "bool",
         "给模组/地图里的事件脚本用: 开 = 允许事件脚本执行服务端指令, 等于给它发指令的权力。"),
        ("Allow_Client_Messages", "允许客户端消息", "bool",
         "开 = 允许事件脚本走客户端那头的消息通道。"),
        ("Allow_Client_Commands", "允许客户端指令", "bool",
         "开 = 允许事件脚本执行客户端指令。来路不明的地图/模组开这一项有安全风险。"),
    ]),
    ("Browser", "服务器列表简介 (进阶)", [
        ("Desc_Hint", "列表里的一行短简介", "text",
         "朋友在服务器列表看到的那一行短介绍。"),
        ("Desc_Full", "完整简介", "text",
         "点开你这一栏时显示的整段介绍。"),
        ("Desc_Server_List", "服务器列表说明", "text",
         "列表页里再补的一句说明文字。"),
    ]),
]

# 这几项和账号凭据 / 官方服务器登录 / 商业化有关, 交给用户填只会泄露信息或搞出误会, 高级配置不提供
ADVANCED_DENY = {("Browser", "Login_Token"), ("Browser", "BookmarkHost"),
                 ("Browser", "Is_Using_Anycast_Proxy"), ("Browser", "Monetization"),
                 ("Server", "Update_Steam_Beta_Name")}
ADV_TYPE_CN = {"num": "填数字 (不能是负数)", "pct": "只能填 0 ~ 1",
               "count": "填整数", "bool": "只能选 开 / 关 / 默认",
               "text": "填一行文字"}

# ================================================================ 三档难度的原版默认值
# 换 mode 时游戏就是整套替换这批默认值, 数值来自游戏自己的难度配置表 (Easy / Normal / Hard)。
# 只列「玩法设置」页面里能单独改的项, 这样每一项都能标出「你已经手动填过 -> 换难度不再动它」。
DIFF_ROWS = [
    ("Items", "Spawn_Chance", "物资刷新率 (屋子里东西的多少)", "pct", .35, .35, .15),
    ("Items", "Respawn_Time", "物资刷新间隔 (捡空了多久刷回来)", "sec", 50, 100, 150),
    ("Items", "Has_Durability", "物品会用坏 (耐久损耗)", "bool", False, True, True),
    ("Zombies", "Spawn_Chance", "僵尸刷新率", "pct", .2, .25, .3),
    ("Zombies", "Damage_Multiplier", "僵尸打你多疼", "x", .75, 1, 1.5),
    ("Zombies", "Armor_Multiplier", "僵尸有多耐打 (越低越难打死)", "x", 1.25, 1, .75),
    ("Zombies", "Loot_Chance", "僵尸身上掉东西的概率", "pct", .55, .5, .3),
    ("Zombies", "Sprinter_Chance", "疾跑僵尸出现的概率", "pct", 0, .15, .175),
    ("Zombies", "Crawler_Chance", "爬行僵尸出现的概率", "pct", 0, .15, .125),
    ("Vehicles", "Has_Battery_Chance", "刷出来的车自带电瓶的概率", "pct", 1, .8, .25),
    ("Vehicles", "Has_Tire_Chance", "刷出来的车自带轮胎的概率", "pct", 1, .85, .7),
    ("Players", "Food_Default", "出生时饱食度", "num", 100, 100, 85),
    ("Players", "Water_Default", "出生时含水量", "num", 100, 100, 85),
    ("Players", "Experience_Multiplier", "经验获取倍率", "x", 1.5, 1, 1.5),
    ("Players", "Can_Hurt_Legs", "摔落会摔断腿", "bool", False, True, True),
    ("Players", "Can_Start_Bleeding", "会流血", "bool", False, True, True),
    ("Players", "Allow_Instakill_Headshots", "爆头一击必杀", "bool", False, False, True),
    ("Gameplay", "Hitmarkers", "打中人出现命中标记", "bool", True, True, False),
    ("Gameplay", "Crosshair", "屏幕准星", "bool", True, True, False),
    ("Gameplay", "Chart", "常驻地图 (不用捡地图)", "bool", True, False, False),
]

MODE_LABELS = {"easy": "🟢 简单", "normal": "🟡 普通", "hard": "🔴 困难"}
MODE_COLS = ("easy", "normal", "hard")


def diff_fmt(kind, v):
    if kind == "bool":
        return "✔ 开" if v else "✘ 关"
    v = float(v)
    if kind == "pct":
        return "%g%%" % round(v * 100, 1)
    if kind == "x":
        return "×%g" % v
    if kind == "sec":
        return "%g 秒" % v
    return "%g" % v


def difficulty_overrides():
    """当前存档 Config.txt 里已经被手动改过的难度项 -> 'Section|Key': 值"""
    out = {}
    try:
        path = config_txt()
    except OSError:
        return out
    if not os.path.isfile(path):
        return out
    for sec, key, *_rest in DIFF_ROWS:
        try:
            val = get_cfg(path, sec, key)
        except OSError:
            val = None
        if val is not None and str(val).strip() != "":
            out[f"{sec}|{key}"] = val
    return out


def difficulty_rows_html(pinned=None, only=None):
    """难度对照表; only=(a,b) 时只列这两档之间有差异的行"""
    pinned = pinned or {}
    ai = MODE_COLS.index(only[0]) if only and only[0] in MODE_COLS else -1
    bi = MODE_COLS.index(only[1]) if only and only[1] in MODE_COLS else -1
    cols = MODE_COLS if only is None else tuple(only)
    rows = ""
    for sec, key, cn, kind, *vals in DIFF_ROWS:
        if only is not None and vals[ai] == vals[bi]:
            continue
        cells = "".join(f"<td>{esc(diff_fmt(kind, vals[MODE_COLS.index(c)]))}</td>" for c in cols)
        tag = ""
        if f"{sec}|{key}" in pinned:
            tag = (f'<span class="pin">已手动设为 {esc(str(pinned[f"{sec}|{key}"]))}</span>')
        rows += (f'<tr><td>{esc(cn)}{tag}</td>{cells}</tr>')
    if not rows:
        return '<div class="hint">这两档之间没有数值差异。</div>'
    head = "".join(f"<th>{esc(MODE_LABELS.get(c, c))}</th>" for c in cols)
    return (f'<table class="difft"><thead><tr><th class="l">会变的数值</th>{head}</tr></thead>'
            f'<tbody>{rows}</tbody></table>')


# ================================================================ 玩法参数规格 (表单提示 + 保存校验)
DIFF_BY_KEY = {f"{sec}|{key}": (kind, e, n, h) for sec, key, _cn, kind, e, n, h in DIFF_ROWS}
CFG_SPECS = {}

for _sec, _sec_cn, _fields in GAMEPLAY_FIELDS:
    for _f in _fields:
        CFG_SPECS[f"{_sec}|{_f[0]}"] = {"dk": f"{_sec}|{_f[0]}", "label": _f[1],
                                        "sec_cn": _sec_cn, "type": _f[2],
                                        "lo": _f[4][0], "hi": _f[4][1], "def": _f[4][2]}


def num_out(v):
    v = float(v)
    return str(int(v)) if v == int(v) else ("%g" % v)


def default_text(spec):
    """游戏默认值怎么说: 定值 / 随难度 / 没有依据就不说"""
    d = spec["def"]
    if d == "mode":
        got = DIFF_BY_KEY.get(spec["dk"])
        if not got:
            return "默认值随「游戏难度」变化"
        kind, e, n, h = got
        return (f"默认随难度: 简单 {diff_fmt(kind, e)} · 普通 {diff_fmt(kind, n)} · "
                f"困难 {diff_fmt(kind, h)}")
    if d is None:
        return ""
    if spec["type"] == "bool":
        return f"游戏默认: {'开' if d else '关'}"
    return f"游戏默认 {num_out(d)}"


def spec_text(spec):
    if spec["type"] == "bool":
        return default_text(spec)
    lo, hi = spec["lo"], spec["hi"]
    if lo is not None and hi is not None:
        rng = f"可以填 {num_out(lo)} ~ {num_out(hi)}"
    elif lo is not None:
        rng = f"最小 {num_out(lo)}"
    elif hi is not None:
        rng = f"最大 {num_out(hi)}"
    else:
        rng = ""
    return " · ".join(x for x in (rng, default_text(spec)) if x)


def gameplay_check(dk, raw):
    """保存前逐项体检: 返回 (要写入的值或 None, 报错文字)。报错文字非空 = 整次保存作废。"""
    sp = CFG_SPECS.get(dk)
    if not sp:
        return None, "不认识的配置项"
    name = f"{sp['sec_cn']} · {sp['label']}"
    s = str(raw).strip()
    if s == "":
        return None, ""                       # 留空 = 恢复游戏默认
    if sp["type"] == "bool":
        low = s.lower()
        if low not in ("true", "false"):
            return None, f"{name} 只能选 开 / 关 / 默认"
        return low.capitalize(), ""
    try:
        v = float(s.replace(",", "").replace("，", ""))
    except ValueError:
        return None, f"{name} 要填数字, 现在填的是「{s[:16]}」"
    lo, hi = sp["lo"], sp["hi"]
    if lo is not None and v < lo:
        return None, f"{name} 最小是 {num_out(lo)} (你填了 {num_out(v)})"
    if hi is not None and v > hi:
        return None, f"{name} 最大是 {num_out(hi)} (你填了 {num_out(v)})"
    return num_out(v), ""


# ================================================================ 高级配置 (玩法设置里默认收起那一批)
ADV_SPECS = {}
for _sec, _sec_cn, _items in ADVANCED_SECTIONS:
    for _k, _cn, _t, _hint in _items:
        ADV_SPECS[f"{_sec}|{_k}"] = {"dk": f"{_sec}|{_k}", "sec": _sec, "sec_cn": _sec_cn,
                                     "key": _k, "label": _cn, "type": _t, "hint": _hint}


def cfg_section_keys(path):
    """这份 Config.txt 里出现过的 段|键 (小写) —— 用来判断某项是「改已有」还是「新插入」"""
    try:
        text, _ = read_file(path)
    except OSError:
        return {}
    out = {}
    for it in parse_cfg(text):
        if it["type"] == "setting" and len(it["section"]) == 1:
            out.setdefault(f"{it['section'][0]}|{it['key']}".lower(), it["value"])
    return out


def cfg_insert_setting(items, section, key, value):
    """把文件里还没有的键插进对应段落; 连段落都没有就在末尾补一段 (只认最外层段落, 段落名按文件里的大小写)"""
    depth, hit = 0, None
    for i, it in enumerate(items):
        if it["type"] == "sectopen" and depth == 0 and it["key"].lower() == section.lower():
            hit = (i, it["key"])
            break
        if it["type"] == "brace":
            depth += 1 if it["raw"].strip() == "{" else -1
    if hit:
        i, sec_name = hit
        nxt = items[i + 1] if i + 1 < len(items) else None
        if nxt and nxt["type"] == "brace" and nxt["raw"].strip() == "{":
            items.insert(i + 2, {"type": "setting", "key": key, "value": value,
                                 "indent": "\t", "section": (sec_name,)})
            return True
        return False
    for raw in ("", section, "{", None, "}"):
        if raw is None:
            items.append({"type": "setting", "key": key, "value": value,
                          "indent": "\t", "section": (section,)})
        elif raw == "{":
            items.append({"type": "brace", "raw": "{"})
        elif raw == "}":
            items.append({"type": "brace", "raw": "}"})
        elif raw == section:
            items.append({"type": "sectopen", "raw": section, "key": section})
        else:
            items.append({"type": "other", "raw": raw})
    return True


def set_cfg_value(path, section, key, value):
    """写一个「Config.txt 里可能还没有」的键; 已有就改值 (键名大小写照文件里的原样), 没有就插一笔。
    返回 set=改已有 / new=新插一笔 / skip=想留默认而文件里本来也没有 / fail=没写进去"""
    _ensure_cfg_file(path)
    text, _ = read_file(path)
    items = parse_cfg(text)
    for it in items:
        if it["type"] == "setting" and len(it["section"]) == 1 \
                and it["section"][0].lower() == section.lower() \
                and it["key"].lower() == key.lower():
            it["value"] = value
            write_file(path, build_cfg(items))
            return "set"
    if value is None:
        return "skip"                       # 文件里没有这一笔, 而用户要的是"交回游戏" —— 本来就是不写
    if not cfg_insert_setting(items, section, key, value):
        return "fail"
    write_file(path, build_cfg(items))
    return "new"


def adv_check(dk, raw):
    """高级配置逐项体检: 官方没公布范围, 所以只挡「一定会写坏文件或一定被游戏无视」的填法"""
    sp = ADV_SPECS.get(dk)
    if not sp:
        return None, "这一项开服器没有收录, 不能写"
    name = f"{sp['sec_cn']} · {sp['label']}"
    s = str("" if raw is None else raw).strip()
    if s == "":
        return None, ""
    t = sp["type"]
    if t == "bool":
        low = s.lower()
        if low not in ("true", "false"):
            return None, f"{name} 只能选 开 / 关 / 默认"
        return low.capitalize(), ""
    if t == "text":
        if len(s) > 200:
            return None, f"{name} 太长了 (最多 200 字, 现在 {len(s)} 字)"
        if "{" in s or "}" in s or "[" in s or "]" in s or "//" in s \
                or "\n" in s or "\r" in s:
            return None, (f"{name} 里不能有换行、大括号或注释符号 —— "
                          "这几样会把 Config.txt 的段落结构写坏")
        return s, ""
    try:
        v = float(s.replace(",", "").replace("，", ""))
    except ValueError:
        return None, f"{name} 要填数字, 现在填的是「{s[:16]}」"
    if v != v or v in (float("inf"), float("-inf")):
        return None, f"{name} 这个数游戏算不出来, 换一个正常的"
    if v < 0:
        return None, f"{name} 不能是负数 (你填了 {num_out(v)})"
    if t == "count" and v != int(v):
        return None, f"{name} 只能填整数 (你填了 {num_out(v)})"
    if t == "pct" and v > 1:
        return None, f"{name} 是概率, 只能填 0 ~ 1 (你填了 {num_out(v)})"
    return num_out(v), ""


def cfg_adv_rows():
    """高级配置表单要渲染的行: 按段分组, 每项带上当前值和「这个存档的 Config.txt 里有没有这一笔」
    只是打开页面看一眼, 所以这里绝不建文件 —— 文件还没有就整页显示成"没写过" """
    path = config_txt()
    cur = cfg_section_keys(path) if os.path.isfile(path) else {}
    groups, total = [], 0
    for sec, sec_cn, items in ADVANCED_SECTIONS:
        rows = []
        for key, label, typ, hint in items:
            if (sec, key) in ADVANCED_DENY:
                continue
            dk = f"{sec}|{key}"
            got = cur.get(dk.lower())
            rows.append({"dk": dk, "sec": sec, "sec_cn": sec_cn, "key": key,
                         "label": label, "type": typ, "hint": hint,
                         "cur": got if got is not None else "",
                         "untouched": dk.lower() not in cur})
        if rows:
            total += len(rows)
            groups.append((sec, sec_cn, rows))
    return groups, total


# ---- 新建存档时生成的配置文件 ----
ALL_CFG_KEYS = {}

def _collect_cfg_key(sec, key):
    ALL_CFG_KEYS.setdefault(sec, [])
    if key not in ALL_CFG_KEYS[sec]:
        ALL_CFG_KEYS[sec].append(key)


for _t in TOGGLES:
    for _s, _k, _v in _t["keys"]:
        _collect_cfg_key(_s, _k)
for _s in SELECTS:
    _collect_cfg_key(_s["section"], _s["key"])
for _sec, _cn, _fields in GAMEPLAY_FIELDS:
    for _f in _fields:
        _collect_cfg_key(_sec, _f[0])
for _k in ("Icon", "Thumbnail", "Links"):
    _collect_cfg_key("Browser", _k)


DEFAULT_WELCOME = ("欢迎使用DawnSharkk开服器 github仓库地址"
                   "https://github.com/snowPippl/DawnSharkk "
                   "如果好用请帮忙点击一个star哦~！游戏愉快~~")


def default_commands_text(name, port, cheat=True):
    txt = (f"name {name}\nMap PEI\nMaxplayers 24\nPort {port}\nMode normal\n"
           f"perspective both\nWelcome {DEFAULT_WELCOME}\n//PVE 或 PVP\nPVE\n")
    if cheat:
        txt += "cheats on //开启作弊 (check/give/teleport 等管理指令可用)\n"
    return txt


# ---- 仪表盘「默认开服选项」: 应用到当前服务器, 并成为以后新建服务器的默认 ----
DASH_TOGGLE_IDS = ["keep_inventory", "struct_inv", "veh_inv", "zombie_safe",
                   "friendly_fire", "airdrops", "max_skills"]


def get_server_defaults():
    d = _settings.get("server_defaults") or {}
    togs = d.get("toggles")
    if togs is None:
        togs = ["keep_inventory"]
    return {"cheat": bool(d.get("cheat", True)),
            "toggles": [t for t in togs if t in DASH_TOGGLE_IDS]}


def save_server_defaults(d):
    _settings["server_defaults"] = {"cheat": bool(d.get("cheat")),
                                    "toggles": [t for t in (d.get("toggles") or [])
                                                if t in DASH_TOGGLE_IDS]}
    save_settings()


def apply_defaults_to_instance(name):
    """新建存档时把默认一键开关写入其 Config.txt (作弊在 Commands.dat 生成时已带上)"""
    cfg_path = os.path.join(game_dir(), "Servers", name, "Config.txt")
    for t in TOGGLES:
        if t["id"] in get_server_defaults()["toggles"]:
            for sec, key, val in t["keys"]:
                set_cfg(cfg_path, sec, key, val)


def _ensure_cfg_file(cfg_path):
    """Config.txt 不存在时生成一份含全部受管理键的基础配置 (未写的=游戏默认值)"""
    if os.path.isfile(cfg_path):
        return
    os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
    lines = ["// Dawn Sharkk 生成的基础配置 (没有写的设置 = 游戏默认值)", "Version 1", ""]
    for sec, keys in ALL_CFG_KEYS.items():
        lines.append(sec)
        lines.append("{")
        lines.extend("\t" + k for k in keys)
        lines.append("}")
        lines.append("")
    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def ensure_instance_files(name):
    """确保实例的 Commands.dat / Config.txt 存在 (新建或游戏未生成时自动补齐)"""
    base = os.path.join(game_dir(), "Servers", name)
    os.makedirs(os.path.join(base, "Server"), exist_ok=True)
    cmd_path = os.path.join(base, "Server", "Commands.dat")
    created_cmd = not os.path.isfile(cmd_path)
    if created_cmd:
        port = 26010
        try:
            used = set()
            for d in os.listdir(os.path.join(game_dir(), "Servers")):
                cf = os.path.join(game_dir(), "Servers", d, "Server", "Commands.dat")
                if os.path.isfile(cf):
                    m = re.search(r"(?mi)^Port\s+(\d+)",
                                  open(cf, encoding="utf-8", errors="replace").read())
                    if m:
                        used.add(int(m.group(1)))
            while port in used:
                port += 1
        except OSError:
            pass
        with open(cmd_path, "w", encoding="utf-8") as f:
            f.write(default_commands_text(name, port,
                                          get_server_defaults()["cheat"]))
    _ensure_cfg_file(os.path.join(base, "Config.txt"))
    if apply_browser_defaults(name, os.path.join(base, "Config.txt")):
        oplog("操作", f"存档「{name}」写入默认服务器图标与大厅链接 (Config.txt 的 Browser 段)")
    if created_cmd:
        apply_defaults_to_instance(name)   # 应用仪表盘「默认开服选项」
    return True

# ================================================================ Commands.dat
CMD_FIELDS = [
    ("name", "服务器名称", "text", "在服务器列表里显示的名字", ""),
    ("map", "地图", "map", "服务器玩的地图", "PEI"),
    ("maxplayers", "最大玩家数", "number", "同时在线人数上限", "24"),
    ("port", "端口", "number", "连接服务器的端口", "27015"),
    ("mode", "游戏难度", "select", "简单|普通|困难", "normal"),
    ("perspective", "允许的视角", "select", "第一|第三|都可以|仅载具内", "both"),
    ("welcome", "进服欢迎语", "text", "玩家进入服务器时看到的提示", ""),
    ("loadout", "出生装备", "text", "物品ID用/分隔, 255/4=默认衣服", ""),
    ("password", "进入密码", "text", "留空=不需要密码", ""),
    ("owner", "服主 SteamID", "text", "", ""),
]


def parse_commands(text):
    entries = []
    for line in text.splitlines():
        s = line.rstrip()
        if not s.strip() or s.strip().startswith("//"):
            entries.append({"key": None, "value": "", "comment": s.strip()})
            continue
        cm = re.search(r"\s+//", s)          # 行内注释只在 // 前有空白时才切开
        comment = ""
        if cm:
            comment = s[cm.start():].strip()
            s = s[:cm.start()].rstrip()
        m = re.match(r"^([A-Za-z_][\w.]*)\s*(.*)$", s.strip())
        if m:
            entries.append({"key": m.group(1), "value": m.group(2).strip(),
                            "comment": comment})
        else:
            entries.append({"key": None, "value": "", "comment": ""})
    return entries


def build_commands(entries):
    lines = []
    for e in entries:
        if e["key"] is None:
            lines.append(e["comment"])
        else:
            line = e["key"] + ("" if e["value"] == "" else " " + e["value"])
            if e["comment"]:
                line += " " + e["comment"]
            lines.append(line)
    return "\n".join(lines)


def commands_path():
    return os.path.join(server_dir(), "Server", "Commands.dat")


def cmd_info():
    d = {}
    if not setup_done():
        d.update(name="(未设置)", map="—", port="27015", maxplayers="—", mode="normal")
        return d
    try:
        text, _ = read_file(commands_path())
        for e in parse_commands(text):
            if e["key"]:
                k = e["key"].lower()
                if k in ("name", "map", "mode", "port", "maxplayers", "perspective", "password"):
                    d[k] = e["value"] or ""
    except OSError:
        pass
    d.setdefault("name", "(未命名服务器)")
    d.setdefault("map", "PEI")
    d.setdefault("port", "27015")
    d.setdefault("maxplayers", "—")
    d.setdefault("mode", "normal")
    return d


def set_commands_kv(pairs):
    """直接改 Commands.dat 的若干键(供进服密码等使用); value=None 表示整行删除该键"""
    full = commands_path()
    if not os.path.isfile(full):
        return False
    text, _ = read_file(full)
    entries = parse_commands(text)
    for k, v in pairs.items():
        matched = [e for e in entries if e["key"] and e["key"].lower() == k.lower()]
        if v is None:
            entries = [e for e in entries if e not in matched]
            continue
        if matched:
            matched[0]["value"] = v
        else:
            entries.append({"key": k, "value": v, "comment": ""})
    write_file(full, build_commands(entries))
    return True


def cheat_enabled():
    """Commands.dat 里是否开启作弊 (cheats on; 兼容旧版写法的裸 cheat 行)"""
    if not setup_done() or not os.path.isfile(commands_path()):
        return False
    try:
        text, _ = read_file(commands_path())
    except OSError:
        return False
    return any(e["key"] and e["key"].lower() in ("cheat", "cheats")
               for e in parse_commands(text))


def set_cheat(on):
    """给当前存档开启/关闭作弊 (Commands.dat 增删 cheats on 行, 顺带清掉旧版 cheat 行)"""
    if not os.path.isfile(commands_path()):
        return False, "找不到 Commands.dat, 请先完成初始设置"
    try:
        if on:
            set_commands_kv({"cheat": None, "cheats": "on"})
        else:
            set_commands_kv({"cheat": None, "cheats": None})
    except OSError as e:
        return False, str(e)
    return True, ("已开启作弊选项 (写入 cheats on), 重启服务器后生效" if on
                  else "已关闭作弊选项, 重启后 check/give/teleport 等管理指令将被禁用")


def list_maps():
    """探测可用地图: 返回游戏里真实存在的文件夹名 (大小写原样, 游戏按名字找地图)"""
    maps, seen = [], set()
    g = game_dir()
    bases = []
    if g:
        bases.append(os.path.join(g, "Maps"))
    sd = server_dir()
    if sd:
        bases.append(os.path.join(sd, "Maps"))
        bases.append(os.path.join(sd, "Level"))
    for base in bases:
        if os.path.isdir(base):
            for name in sorted(os.listdir(base)):
                low = name.lower()
                if os.path.isdir(os.path.join(base, name)) and low not in seen:
                    seen.add(low)
                    maps.append(name)
    for e in workshop_maps():          # 创意工坊地图也算"真实存在的地图"
        low = e["map"].lower()
        if low not in seen:
            seen.add(low)
            maps.append(e["map"])
    return maps


def norm_map_name(name):
    """把用户填的地图名对齐到游戏里真实存在的文件夹名 (大小写照 Maps 里的原样; 名字错了游戏不报错, 只会退回 PEI)"""
    name = (name or "").strip()
    if not name:
        return "", False
    low = name.lower()
    for m in list_maps():
        if m.lower() == low:
            return m, True
    return name, False


def map_exists(name):
    """Commands.dat 里写的地图名是否真的存在 (Windows 下大小写不敏感)"""
    return norm_map_name(name)[1]


# ================================================================ 创意工坊地图
# 创意工坊内容下载后落在: 存档\Workshop\Steam\content\304930\<模组ID>\
# 判定"这是一个地图"的标准 (按用户给的样本目录定的):
#   模组ID 文件夹里有 Map.meta  →  与 Map.meta 同级的文件夹名 = 游戏里的地图名
#   (样本: content\304930\3707778928\Map.meta + California2\ → 地图名 California2)
# 没有 Map.meta 的那些 ID 文件夹是别的模组或地图的"资产包", 不当地图。
WS_APPID = CLIENT_APPID         # 创意工坊内容目录用游戏本体的 AppID 命名
WS_FILE_URL = "https://steamcommunity.com/sharedfiles/filedetails/?id=%d"


def workshop_maps():
    """本存档里已经下载好的创意工坊地图: [{map, id, url}]"""
    out = []
    sd = server_dir()
    if not sd:
        return out
    base = os.path.join(sd, "Workshop", "Steam", "content", WS_APPID)
    try:
        pids = sorted(os.listdir(base))
    except OSError:
        return out
    for pid in pids:
        pdir = os.path.join(base, pid)
        if not pid.isdigit() or not os.path.isfile(os.path.join(pdir, "Map.meta")):
            continue
        try:
            subs = sorted(os.listdir(pdir))
        except OSError:
            continue
        for nm in subs:
            if os.path.isdir(os.path.join(pdir, nm)):
                out.append({"map": nm, "id": int(pid), "url": WS_FILE_URL % int(pid)})
    return out


def workshop_map_of(name):
    """当前 Commands.dat 写的地图是不是创意工坊地图 (是 → 返回那条记录)"""
    name = (name or "").strip()
    if not name:
        return None
    low = name.lower()
    for e in workshop_maps():
        if e["map"].lower() == low:
            return e
    return None


def ws_start_notice(ok):
    """开服成功后要不要弹资产包提醒: 当前地图是创意工坊地图 → 返回那条记录 + 存档目录"""
    if not ok:
        return None
    e = workshop_map_of(cmd_info().get("map"))
    if not e:
        return None
    e = dict(e)
    e["inst"] = server_dir() or ""
    return e


def ws_subscribed_ids():
    """本存档 WorkshopDownloadConfig.json 里订阅了多少个创意工坊内容"""
    sd = server_dir()
    if not sd:
        return []
    full = os.path.join(sd, "WorkshopDownloadConfig.json")
    try:
        return [int(i) for i in json.loads(read_file(full)[0]).get("File_IDs", [])]
    except (OSError, ValueError, TypeError, AttributeError):
        return []


# ================================================================ 登录密钥 (每次启动随机生成)
_login_key = [None]
_login_key_at = [0.0]
KEY_AUTO_WINDOW = 1800          # 带 ?key= 的网址自动登录有效期 (秒)


def login_key():
    """本次运行的网页登录密钥: 每次启动开服器都会重新随机生成, 不再固定"""
    if not _login_key[0]:
        _login_key[0] = "".join(random.choices(string.ascii_letters + string.digits, k=24))
        _login_key_at[0] = time.time()
    return _login_key[0]


def key_auto_ok():
    """网址里带密钥的自动登录是否还在时间窗口内 (超时须手动输入)"""
    login_key()
    return time.time() - _login_key_at[0] < KEY_AUTO_WINDOW


# ================================================================ 文件列表
FILE_DESC = {
    "Config.txt": "游戏核心配置 (伤害/掉落/僵尸/载具等全部玩法)",
    "WorkshopDownloadConfig.json": "创意工坊模组下载列表",
    "Server/Commands.dat": "服务器基本设置 (名称/地图/人数/端口)",
    "Server/Adminlist.dat": "管理员列表 (每行一个 SteamID)",
    "Server/Blacklist.dat": "黑名单",
    "Server/Whitelist.dat": "白名单",
    "Rocket/Rocket.config.xml": "Rocket 主配置 (RCON/语言/帧率)",
    "Rocket/Rocket.Unturned.config.xml": "Rocket Unturned 组件配置",
    "Rocket/Commands.config.xml": "Rocket 命令启用/别名配置",
    "Rocket/Permissions.config.xml": "Rocket 权限组配置",
}


def list_files():
    files, seen = [], set()
    sd = server_dir()

    def walk(d, rel):
        for name in sorted(os.listdir(d)):
            p = os.path.join(d, name)
            r = os.path.normpath(os.path.join(rel, name)) if rel else name
            if os.path.isdir(p):
                if name.lower() in {"level", "players", "logs", "libraries", "cache",
                                    "workshop", "userdata", "bundlemetadata"}:
                    continue
                walk(p, r)
            else:
                ext = os.path.splitext(name)[1].lower()
                if ext in ALLOWED_EXT and r not in seen:
                    seen.add(r)
                    try:
                        files.append({"path": r.replace("\\", "/"), "size": os.path.getsize(p)})
                    except OSError:
                        pass
    if sd and os.path.isdir(sd):
        walk(sd, "")
    return files


# ================================================================ 操作日志
_op_lock = threading.Lock()


def exc_summary(e):
    """错误摘要: 只含异常类型/信息/行号, 不含本机文件路径"""
    line = "?"
    tb = e.__traceback__
    while tb is not None:
        line = tb.tb_lineno
        tb = tb.tb_next
    return f"{type(e).__name__}: {e} (server_manager.py 第{line}行)"


def oplog(cat, detail):
    """操作日志: cat = 操作/安全/错误; 同时写入文件与内存缓冲"""
    line = {"t": time.strftime("%Y-%m-%d %H:%M:%S"), "cat": cat, "msg": detail}
    with _op_lock:
        try:
            if os.path.isfile(OPLOG_PATH) and os.path.getsize(OPLOG_PATH) > 2_000_000:
                os.replace(OPLOG_PATH, OPLOG_PATH + ".old")
        except OSError:
            pass
        try:
            with open(OPLOG_PATH, "a", encoding="utf-8") as f:
                f.write(f"[{line['t']}] [{cat}] {detail}\n")
        except OSError:
            pass
    return line


def read_oplog(max_bytes=131072):
    """读取日志文件尾部"""
    try:
        size = os.path.getsize(OPLOG_PATH)
        with open(OPLOG_PATH, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
            data = f.read().decode("utf-8", "replace")
        lines = data.splitlines()
        if lines and not data.startswith("["):     # 截断的首半行丢弃
            lines = lines[1:]
        return lines
    except OSError:
        return []


# ================================================================ 进程 + 控制台
_proc = None
_proc_lock = threading.Lock()
_console = deque(maxlen=3000)
_console_idx = [0]
_log_tails = {"rocket": {"off": 0, "partial": b""},
              "console": {"off": 0, "partial": b""}}
_server_code = [None]
ERR_RE = re.compile(r"(error|exception|fail|warn)", re.I)
CODE_RE = re.compile(r"Server Code:\s*([0-9]{6,})", re.I)
# 关卡加载进度: 游戏先打印 Server Code, 之后才到 100%, 所以代码要等加载完再放出
LEVEL_RE = re.compile(r"loading\s*level\s*[:：]\s*(\d{1,3})\s*%", re.I)
CODE_GATE_SEC = 90
_pending_code = [None]         # 抓到但尚未确认加载完成的代码
_code_seen_at = [0.0]
_level_loaded = [False]


def _reset_code_gate():
    """每次开服前调用: 上一局的代码绝不能继续显示"""
    _server_code[0] = None
    _pending_code[0] = None
    _code_seen_at[0] = 0.0
    _level_loaded[0] = False


def _release_code():
    """地图确实加载到 100% (或距代码行已超时) 才把代码交给仪表盘显示"""
    if _pending_code[0] is None:
        return
    if _level_loaded[0] or (time.time() - _code_seen_at[0] > CODE_GATE_SEC):
        _server_code[0] = _pending_code[0]
        _pending_code[0] = None


def _feed_code_gate(line):
    m = CODE_RE.search(line)
    if m:
        _pending_code[0] = m.group(1)
        _code_seen_at[0] = time.time()
        _level_loaded[0] = False          # 出现新代码 = 新的一局, 重新等加载完成
        return m
    lv = LEVEL_RE.search(line)
    if lv and int(lv.group(1)) >= 100:
        _level_loaded[0] = True
    return None


def _log(line, src="srv"):
    m = _feed_code_gate(line)
    if re.search(r"(error|exception|fail)", line, re.I):
        err = 1
    elif re.search(r"warn", line, re.I):
        err = 2
    elif m:
        err = 3
    else:
        err = 0
    with _proc_lock:
        _console_idx[0] += 1
        _console.append({"i": _console_idx[0], "t": line, "e": err, "s": src})


def _reader(proc):
    try:
        for raw in iter(proc.stdout.readline, b""):
            if not raw:
                break
            try:
                line = raw.decode("utf-8")
            except UnicodeDecodeError:
                try:
                    line = raw.decode("gbk")
                except UnicodeDecodeError:
                    line = raw.decode("utf-8", "replace")
            for ln in line.rstrip("\r\n").splitlines():
                if ln.strip():
                    _log(ln, "out")
    except (OSError, ValueError):
        pass


def _tail_file(path, state, src):
    if not path:
        return
    try:
        if not os.path.isfile(path):
            return
        size = os.path.getsize(path)
        if size < state["off"]:
            state.update(off=0, partial=b"")
        with open(path, "rb") as f:
            f.seek(state["off"])
            new = f.read()
        state["off"] += len(new)
        data = state["partial"] + new
        lines, consumed = [], 0
        while True:
            nl = data.find(b"\n", consumed)
            if nl == -1:
                break
            lines.append(data[consumed:nl])
            consumed = nl + 1
        state["partial"] = data[consumed:]
        for b in lines:
            t = b.decode("utf-8", "replace").rstrip("\r")
            if t.strip():
                _log(t, src)
    except OSError:
        pass


def _skip_old_log_lines():
    """新的一局从日志末尾开始读: 不重放上一次开服的历史
    (Rocket.log 是跨次启动追加的, 从头读会把上一局的 Server Code 当成当前的)"""
    for st in _log_tails.values():
        st.update(off=0, partial=b"")
    sd = server_dir()
    if not sd:
        return
    for key, rel in (("console", "console.log"),
                     ("rocket", os.path.join("Rocket", "Logs", "Rocket.log"))):
        try:
            size = os.path.getsize(os.path.join(sd, rel))
        except OSError:
            continue
        _log_tails[key].update(off=size, partial=b"")


def _push_log_tails():
    """网页日志来源: 原版控制台模式下优先 console.log(中继器写入), 否则 Rocket 日志"""
    sd = server_dir()
    if not sd:
        return
    clog = os.path.join(sd, "console.log")
    if os.path.isfile(clog):
        _tail_file(clog, _log_tails["console"], "out")
    else:
        _tail_file(os.path.join(sd, "Rocket", "Logs", "Rocket.log"),
                   _log_tails["rocket"], "rocket")
    _release_code()


_srv_check = {"t": 0.0, "res": (False, None)}


def server_running():
    """服务器是否在运行: 区分服务器进程和同名的 Unturned 游戏客户端"""
    global _proc
    with _proc_lock:
        if _proc is not None and _proc.poll() is None:
            return True, _proc.pid
        if _proc is not None:
            _proc = None
    now = time.time()
    if now - _srv_check["t"] < 4:
        return _srv_check["res"]
    res = (False, None)
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Unturned.exe", "/FO", "CSV", "/NH"],
            capture_output=True, timeout=10).stdout.decode("gbk", "replace")
        if "Unturned.exe" in out:
            pids = find_server_pids()      # 只有带本存档启动参数的才是服务器
            if pids:
                res = (True, pids[0])
    except Exception:
        pass
    _srv_check["t"] = now
    _srv_check["res"] = res
    return res


def map_warning():
    """开服前地图体检: 地图名探测不到时游戏不会报错, 而是默默退回默认 PEI"""
    cur = (cmd_info().get("map") or "").strip()
    if not cur or map_exists(cur):
        return ""
    long_msg = (f"⚠ WARNING: 地图「{cur}」在 U3DS\\Maps 和存档 Level 里都没有找到 —— "
                f"游戏不会报错, 但会默默退回默认地图 PEI。请核对地图名 (大小写和下划线照原样抄), "
                f"如果这是创意工坊地图, 要先在「创意工坊」页订阅它, 开服时才会自动下载")
    _log(long_msg, "sys")
    oplog("错误", f"地图体检失败: Commands.dat 写的地图是「{cur}」, 但 Maps/Level 里探测不到")
    return f" (⚠ 地图「{cur}」没找到, 可能会退回默认地图 PEI)"


def start_server():
    global _proc
    running, _ = server_running()
    if running:
        return False, "服务器已经在运行了"
    if not setup_done():
        err = bound_dir_error()
        if err:
            return False, err
        return False, "请先完成开服器初始设置 (游戏目录 / 存档)"
    warn = map_warning()
    patch_rcon()   # 临时启用 RCON, 用于控制台发命令与安全关服
    # 按「服务器设置」页配置的启动参数组装命令
    lc = get_launch_cfg()
    cmd = [os.path.join(game_dir(), "Unturned.exe")]
    if lc["batchmode"]:
        cmd.append("-batchmode")
    if lc["nographics"]:
        cmd.append("-nographics")
    cmd.append("+Secureserver/" + instance())   # 原版启动方式 (VAC 安全服)
    extra = (lc.get("extra") or "").split()
    if extra:
        cmd.extend(extra)
    _skip_old_log_lines()
    _reset_code_gate()   # 每次开服的代码都不同, 上一局的绝不能继承
    if lc.get("console", True):
        # 原版方式: 通过控制台中继器启动 —— 弹出独立控制台窗口显示全部输出,
        # 同时写入 console.log 供网页日志使用; 关闭开服器/网页不影响服务器
        srvname = (cmd_info().get("name") or instance() or "").strip()
        tfile = os.path.join(server_dir(), "title.tmp")
        with _proc_lock:
            _proc = subprocess.Popen(
                [sys.executable, os.path.join(BASE_DIR, "reader.py"),
                 os.path.join(server_dir(), "console.log"), tfile, srvname] + cmd,
                cwd=game_dir(), creationflags=subprocess.CREATE_NEW_CONSOLE)
        threading.Thread(target=_title_updater, args=(tfile,), daemon=True).start()
        _log("—— 服务器以原版控制台窗口启动 (独立驻留, 关闭本开服器不影响服务器)", "sys")
        _log("—— 网页「运行日志」实时同步自控制台窗口输出", "sys")
    else:
        with _proc_lock:
            _proc = subprocess.Popen(
                cmd, cwd=game_dir(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW)
            threading.Thread(target=_reader, args=(_proc,), daemon=True).start()
    _log("—— 服务器启动中, 地图加载到 100% 后本次的服务器代码(Server Code)会显示在仪表盘", "sys")
    oplog("操作", f"启动服务器: {' '.join(cmd)}")
    return True, ("服务器启动中, 大约需要 30~60 秒, 可到「控制台」页看进度" + warn)


def _procs_powershell():
    """[(pid, 命令行)] 走 PowerShell 的 Get-CimInstance; 拿不到返回 None (不是空列表)"""
    q = ('Get-CimInstance Win32_Process -Filter "Name=\'Unturned.exe\'" | '
         'ForEach-Object { "$($_.ProcessId)`t$($_.CommandLine)" }')
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", q],
                           capture_output=True, timeout=25)
    except (OSError, subprocess.TimeoutExpired):
        return None
    rows = []
    for line in (r.stdout or b"").decode("gbk", "replace").splitlines():
        pid, _, cl = line.partition("\t")
        if pid.strip().isdigit():
            rows.append((int(pid.strip()), cl.strip()))
    return rows if r.returncode == 0 else None


def _procs_wmic():
    """老路子, 留着当加速: Windows 11 24H2 起 wmic 已被微软退役, 精简/破解系统通常直接没有"""
    try:
        r = subprocess.run(
            ["wmic", "process", "where", "name='Unturned.exe'",
             "get", "ProcessId,CommandLine", "/FORMAT:CSV"],
            capture_output=True, timeout=20)
        txt = r.stdout.decode("gbk", "replace")
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode:
        return None
    rows = []
    for line in txt.splitlines():
        line = line.strip()
        if not line or line.startswith("Node,"):
            continue
        m = re.search(r",\s*\"?(\d+)\"?\s*$", line)
        if not m:
            continue
        cl = re.sub(r"^Node,[^,]*,", "", line)
        cl = re.sub(r",\s*\"?\d+\"?\s*$", "", cl).strip().strip('"')
        rows.append((int(m.group(1)), cl))
    return rows


def unturned_procs():
    """机器上所有 Unturned.exe: [(pid, 命令行)]; 命令行取不到时是空串

    三条路依次试: PowerShell → wmic → tasklist。tasklist 只能给 PID, 所以那种情况下
    调用方拿不到"这是哪个存档", 只能按名字粗判 —— 绝不能凭这个去强杀 (会误伤游戏客户端)。
    """
    for how in (_procs_powershell, _procs_wmic):
        rows = how()
        if rows is not None:
            return rows
    rows = []
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Unturned.exe", "/FO", "CSV", "/NH"],
            capture_output=True, timeout=15).stdout.decode("gbk", "replace")
    except (OSError, subprocess.TimeoutExpired):
        return rows
    for line in out.splitlines():
        f = re.findall(r'"([^"]*)"', line)
        if len(f) >= 2 and f[0].lower() == "unturned.exe" and f[1].isdigit():
            rows.append((int(f[1]), ""))
    return rows


def find_server_pids():
    """按启动参数精确找到本存档的 Unturned 服务器进程 (不会误伤正在玩的游戏客户端)"""
    inst = instance()
    pids = []
    if not inst:
        return pids
    for pid, cl in unturned_procs():
        if not cl:
            continue
        if inst in cl and any(s in cl for s in ("+InternetServer/", "+Secureserver/",
                                                "+LanServer/")):
            pids.append(pid)
    return pids


def adopt_rcon():
    """接管之前已启用的 RCON (例如开服器重启后服务器还在跑的情况)"""
    global _rcon_cfg
    if _rcon_cfg:
        return
    path = os.path.join(server_dir(), "Rocket", "Rocket.config.xml")
    if not os.path.isfile(path):
        return
    try:
        text, _ = read_file(path)
    except OSError:
        return
    m = re.search(r"<RCON\b[^>]*>", text)
    if not m:
        return
    tag = m.group(0)

    def attr(n):
        a = re.search(n + r'="([^"]*)"', tag)
        return a.group(1) if a else ""

    pw = attr("Password") or ""
    if (attr("Enabled") or "false").lower() == "true" and pw and pw != "changeme":
        try:
            port = int(attr("Port") or 27115)
        except ValueError:
            port = 27115
        globals()["_rcon_cfg"] = {"patched": False, "orig": None,
                                  "port": port, "password": pw}


def _relay_proc():
    with _proc_lock:
        return _proc


def _game_gone():
    """本存档的 Unturned 进程是否已经退出 (True=已经没了)

    命令行读不到时 (tasklist 兜底) 一律按「还在跑」处理, 免得误判成已关服。
    """
    if find_server_pids():
        return False
    rows = unturned_procs()
    if rows and all(not cl for _, cl in rows):
        return False
    return True


def _wait_gone(cap):
    """等服务器自己退出 (shutdown 会先保存世界, 存档大时要等一会儿)"""
    t0 = time.time()
    while time.time() - t0 < cap:
        time.sleep(2)
        if _game_gone():
            return True
    return False


def _reap_relay():
    """关掉控制台中继窗口 (reader.py), 并清掉窗口标题用的临时文件"""
    helper = _relay_proc()
    if helper is not None and helper.poll() is None:
        try:
            helper.kill()
        except OSError:
            pass
    try:
        os.remove(os.path.join(server_dir(), "title.tmp"))
    except OSError:
        pass


def _after_stop():
    _reap_relay()
    restore_rcon()
    _srv_check["t"] = 0


def stop_server():
    running, pid = server_running()
    if not running:
        return False, "服务器没有在运行"
    # 优先安全关服: RCON shutdown 会自动保存世界 (含接管此前已启用的 RCON)
    if not _rcon_cfg:
        adopt_rcon()
    sent = False
    if _rcon_cfg:
        try:
            rcon_command("shutdown")
            sent = True
        except (OSError, RconError) as e:
            why, ev = rcon_diag()
            oplog("错误", f"安全关服命令没送达: {e} | {ev['evidence']}")
            _log(f"—— 安全关服命令没能送进去: {why}", "sys")
    if sent:
        if _wait_gone(90):
            _after_stop()
            _log("—— 服务器已安全关闭, 世界已保存", "sys")
            return True, "✅ 已安全关闭, 世界已保存"
        # 命令已经进去了, 只是游戏还没退 —— 这时候强杀会丢存档
        return False, ("⏳ shutdown 命令已经发出去了, 但等了 90 秒服务器还没退出。"
                       "世界可能正在保存 (存档大时会更久), 请再等一会儿再点关服; "
                       "如果黑色控制台窗口还开着, 也可以直接在那个窗口里输入 shutdown")
    # 兜底: 只精确结束本存档的服务器进程 (不会误伤正在玩的 Unturned 游戏客户端)
    pids = find_server_pids()
    for p in pids:
        subprocess.run(["taskkill", "/PID", str(p), "/F"], capture_output=True)
    _after_stop()
    if pids:
        _log("—— 服务器已被强制关闭", "sys")
        return True, "已强制关闭 (未保存世界。下次请先在控制台执行 save 或直接点关服按钮)"
    _log("—— 机器上没有本存档的 Unturned 进程, 已关闭控制台中继窗口", "sys")
    return True, ("已关掉本开服器的控制台窗口, 但机器上并没有找到本存档的 Unturned 进程 —— "
                  "服务器通常是已经自己崩了 (常见原因: Rocket 没装好、目录混装了游戏本体文件)。"
                  "请到「运行日志」看最后几行, 按「控制台」页的提示修复 Rocket 后重新开服")


# ================================================================ RCON (控制台命令发送)
# Unturned 不接受管道 stdin 命令, 因此开服前临时启用 Rocket 的 RCON
# (密码=本次运行的登录密钥), 关服后自动把 Rocket.config.xml 还原。
# Rocket RCON 为行协议: 连接后先发 `login <密码>`, 之后每行一条命令。
_rcon_cfg = None


class RconError(Exception):
    pass


def _rcon_recv(sock, quiet, cap=20.0):
    """读到「连续 quiet 秒没有新内容」或服务器主动断链为止, 总时长不超过 cap 秒"""
    buf, t0, last = b"", time.time(), time.time()
    try:
        sock.settimeout(1.0)
    except OSError:
        pass
    while time.time() - last < quiet and time.time() - t0 < cap:
        try:
            d = sock.recv(4096)
        except socket.timeout:
            continue
        except OSError:
            break
        if not d:
            break                       # 服务器把这个连接关掉了: 回执到此为止
        buf += d
        last = time.time()
    return buf.decode("utf-8", "replace")


# 这些命令要先把世界存下来才回话, 等短了就会「命令执行了、回执没收到」
SLOW_COMMANDS = ("shutdown", "save", "exit", "reload", "restart", "saveworld")


def rcon_command(cmd, slow=None):
    if not _rcon_cfg:
        adopt_rcon()      # 开服器重启过、服务器还在跑: 从存档配置里把 RCON 接回来
    if not _rcon_cfg:
        raise RconError("RCON 未启用")
    if slow is None:
        slow = (cmd.strip().split() or [""])[0].lower() in SLOW_COMMANDS
    sock = socket.create_connection(("127.0.0.1", _rcon_cfg["port"]), timeout=3)
    try:
        sock.settimeout(3)
        buf = b""
        while b"\n" not in buf:                    # 读取欢迎行 RocketRcon vX
            d = sock.recv(256)
            if not d:
                raise RconError("连接被服务器关闭")
            buf += d
        sock.sendall(b"login " + _rcon_cfg["password"].encode("utf-8") + b"\n")
        resp = _rcon_recv(sock, 1.0, 6.0)
        if "error" in resp.lower() or "denied" in resp.lower():
            raise RconError(resp.strip() or "登录失败")
        sock.sendall(cmd.encode("utf-8") + b"\n")
        # 存盘类命令给足时间: 提前 close 会让 Rocket 写回执时报
        # ObjectDisposedException (真机 Rocket.log 里两次 shutdown 都是这个)
        return _rcon_recv(sock, 6.0 if slow else 1.5, 60.0 if slow else 8.0).strip()
    finally:
        try:
            sock.close()
        except OSError:
            pass


def _port_open(port, timeout=0.6):
    """这个端口到底有没有人在监听 (TCP 连得上)"""
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        s.close()
        return True
    except OSError:
        return False


def _file_age(path):
    """这个文件多少秒没更新过了; 文件不存在返回 None"""
    try:
        return max(0.0, time.time() - os.path.getmtime(path))
    except OSError:
        return None


def rocket_cfg_enabled():
    """本存档 Rocket 配置里 RCON 的 Enabled 是什么: True / False / None(读不到)"""
    sd = server_dir()
    path = os.path.join(sd, "Rocket", "Rocket.config.xml") if sd else None
    if not path or not os.path.isfile(path):
        return None
    try:
        text, _ = read_file(path)
    except OSError:
        return None
    m = re.search(r"<RCON\b[^>]*>", text)
    if not m:
        return None
    a = re.search(r'Enabled="([^"]*)"', m.group(0))
    return (a.group(1) or "false").lower() == "true" if a else None


def rcon_diag(port=None):
    """RCON 连不上时先体检, 把"为什么"说清楚, 而不是让人干等一分钟再试。

    真机反馈过的现场是: 网页显示 Rocket 已安装、日志确实在滚, 但每条指令都
    [WinError 10061] —— 那是 TCP 层就没人监听, 跟"还在启动"没关系。
    返回 (一句结论, 证据 dict), 证据 dict 也会进操作日志, 截一张图就能定位。
    """
    port = port or (_rcon_cfg or {}).get("port") or 27115
    sd = server_dir()
    rk = rocket_check()
    listening = _port_open(port)
    age_rocket = _file_age(os.path.join(sd, "Rocket", "Logs", "Rocket.log")) if sd else None
    age_console = _file_age(os.path.join(sd, "console.log")) if sd else None
    procs = unturned_procs()
    ev = {"port": port, "listening": listening, "rocket_ok": rk["installed"],
          "rocket_detail": rk["detail"], "rocket_cfg": rocket_cfg_enabled(),
          "rocket_log_age": None if age_rocket is None else int(age_rocket),
          "console_age": None if age_console is None else int(age_console),
          "unturned": len(procs)}
    def age_txt(v):
        return "没有这个文件" if v is None else f"{v} 秒前"
    line = (f"端口 {port} {'有人监听' if listening else '没人监听'} | "
            f"Rocket: {rk['detail']} | 配置 Enabled="
            + ("开" if ev["rocket_cfg"] else "关" if ev["rocket_cfg"] is False else "读不到")
            + " | Rocket 日志 " + age_txt(ev["rocket_log_age"])
            + " | 控制台日志 " + age_txt(ev["console_age"])
            + f" | 机器上 Unturned.exe {len(procs)} 个")
    ev["evidence"] = line
    if listening:
        return ("端口是通的, 那是密码或版本对不上 —— 把下面这行证据发过来", ev)
    if not rk["installed"]:
        return ("这次服务器根本没加载 Rocket: " + rk["detail"]
                + " —— 控制台指令、刷新玩家列表、安全关服都依赖它, 所以全部失败。"
                "去「服务器设置」页最底下点「一键安装 Rocket」(或手动双击游戏目录 Extras 里的"
                " Install Rocket.bat), 装完重启服务器", ev)
    if not procs:
        return ("机器上已经没有 Unturned.exe 进程了 —— 服务器其实早就退出或崩了, "
                "网页显示运行中是因为那个黑色控制台窗口(中继器)还开着。"
                "请到黑色窗口看最后几行报错", ev)
    if age_rocket is None or (age_console is not None and age_rocket > age_console + 5):
        return ("Rocket 文件是齐的, 但这次开服它一行日志都没写 —— 游戏没加载它。"
                "最常见的原因就是这份开服端里混了游戏本体的文件 "
                "(会抛 NullReferenceException at SDG.Unturned.Assets.Update()), "
                "或者 Modules 里的 dll 和这个开服端版本不配套", ev)
    if ev["rocket_cfg"] is False:
        return ("Rocket 配置里 RCON 是关闭的 —— 存档里已有的 Rocket.config.xml 把我们写的"
                "配置覆盖掉了, 请把本存档 Rocket 文件夹里的 Rocket.config.xml 备份后删掉再重启", ev)
    return ("Rocket 看起来是好的, 但端口没人听 —— 可能还在启动中, 过 30 秒再试一次", ev)


def patch_rcon():
    """开服前调用: 确保 RCON 可用。密码用本机密钥文件, 防止弱密码暴露。"""
    global _rcon_cfg
    _rcon_cfg = None
    path = os.path.join(server_dir(), "Rocket", "Rocket.config.xml")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.isfile(path):
        # 全新安装: Rocket 第一次运行前还没有配置文件, 主动生成 (含 RCON, 密码=本次登录密钥)
        if not rocket_installed():
            return False
        secret = login_key()
        cfg_tpl = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<RocketSettings xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n'
            f'  <RCON Enabled="true" Port="27115" Password="{secret}" '
            'EnableMaxGlobalConnections="true" MaxGlobalConnections="10" '
            'EnableMaxLocalConnections="true" MaxLocalConnections="3" />\n'
            '  <AutomaticShutdown Enabled="false" Interval="86400" />\n'
            '  <WebConfigurations Enabled="false" Url="" />\n'
            '  <WebPermissions Enabled="false" Url="" Interval="180" />\n'
            '  <LanguageCode>en</LanguageCode>\n'
            '  <MaxFrames>60</MaxFrames>\n'
            '</RocketSettings>\n')
        with open(path, "w", encoding="utf-8") as f:
            f.write(cfg_tpl)
        _rcon_cfg = {"patched": False, "orig": None, "port": 27115, "password": secret}
        return True
    try:
        text, _ = read_file(path)
    except OSError:
        return False
    m = re.search(r"<RCON\b[^>]*>", text)
    if not m:
        return False
    tag = m.group(0)

    def attr(name):
        a = re.search(name + r'="([^"]*)"', tag)
        return a.group(1) if a else ""

    enabled = (attr("Enabled") or "false").lower() == "true"
    try:
        port = int(attr("Port") or 27115)
    except ValueError:
        port = 27115
    password = attr("Password") or ""
    secret = login_key()

    if enabled and password and password != "changeme":
        _rcon_cfg = {"patched": False, "orig": None, "port": port, "password": password}
        return True

    newpass = password
    if not newpass or newpass == "changeme":
        newpass = secret
    newtag = re.sub(r'Enabled="[^"]*"', 'Enabled="true"', tag)
    newtag = re.sub(r'Password="[^"]*"', f'Password="{newpass}"', newtag)
    _rcon_cfg = {"patched": True, "orig": text, "port": port, "password": newpass}
    write_file(path, text[:m.start()] + newtag + text[m.end():])
    return True


def restore_rcon():
    global _rcon_cfg
    if _rcon_cfg and _rcon_cfg.get("patched") and _rcon_cfg.get("orig") is not None:
        path = os.path.join(server_dir(), "Rocket", "Rocket.config.xml")
        try:
            cur, _ = read_file(path)
            if _rcon_cfg["password"] in cur:      # 仍是我们的补丁才还原
                write_file(path, _rcon_cfg["orig"])
        except OSError:
            pass
    _rcon_cfg = None


def send_command(cmd):
    running, _ = server_running()
    if not running:
        return False, "服务器没有在运行"
    if not cmd.strip():
        return False, "命令不能为空"
    try:
        resp = rcon_command(cmd.strip())
    except (OSError, RconError) as e:
        why, ev = rcon_diag()
        oplog("错误", f"命令发送失败: {e} | {ev['evidence']}")
        return False, f"发送失败: {e} —— {why}"
    _log(f">>> {cmd.strip()}", "cmd")
    for ln in resp.splitlines():
        if ln.strip():
            _log(ln, "out")
    return True, "已发送"


# ================================================================ 启动参数 / Rocket
def get_launch_cfg():
    """当前存档的启动参数 (带默认值)"""
    cfg = (_settings.get("launch") or {}).get(instance() or "", {})
    return {
        "batchmode": bool(cfg.get("batchmode", True)),
        "nographics": bool(cfg.get("nographics", True)),
        "console": bool(cfg.get("console", True)),    # 原版控制台窗口 (推荐)
        "extra": cfg.get("extra", ""),
    }


def save_launch_cfg(lc):
    _settings.setdefault("launch", {})[instance() or ""] = lc
    save_settings()


# 反作弊两项从 v0.1.17 起只在「服务器设置」页那一张开 (玩法设置里同名的两项已撤掉),
# 免得同一个键在两个页面各有一份旧值, 后保存的那次把先改的覆盖回去。
ANTICHEAT_KEYS = {"vac": "VAC_Secure", "be": "BattlEye_Secure"}
ANTICHEAT_CN = {"vac": "VAC 反作弊", "be": "BattlEye (战眼) 反作弊"}


def anticheat_on(which):
    """当前存档 Config.txt 里这一项反作弊开没开 (文件里没写这一笔 = 游戏默认 = 开启)"""
    key = ANTICHEAT_KEYS[which]
    try:
        val = get_cfg(config_txt(), "Server", key)
    except OSError:
        return True
    return (val or "True").strip().lower() != "false"


def set_anticheat(which, on):
    """写 Config.txt 的 Server/VAC_Secure 或 Server/BattlEye_Secure; 返回 (是否成功, 提示)"""
    key = ANTICHEAT_KEYS[which]
    try:
        r = set_cfg_value(config_txt(), "Server", key, "True" if on else "False")
    except OSError as e:
        return False, str(e)
    if r == "fail":
        return False, "没写进去 —— 当前存档的 Config.txt 里找不到 Server 段, 请用「文件管理」直接改"
    oplog("操作", f"{'开启' if on else '关闭'}{ANTICHEAT_CN[which]} (Config.txt {key}={r})")
    if on:
        return True, f"已开启 {ANTICHEAT_CN[which]}, 重启服务器后生效"
    return True, (f"已关闭 {ANTICHEAT_CN[which]} —— 作弊玩家进服不会被拦, "
                  f"建筑和进度有被乱改的风险, 处理完记得开回来 (重启服务器生效)")


# 游戏加载 Rocket 必需的文件 —— 少任何一个, 游戏不会报错, 只是静默不加载 Rocket,
# 于是 RCON 端口根本没人监听 (网页上表现为「控制台连不上 / 刷不出玩家 / 关不掉服」)。
ROCKET_REQUIRED = ("Rocket.Unturned.dll", "Rocket.Core.dll", "Rocket.API.dll",
                   "Rocket.Unturned.module")


def rocket_dir(gdir=None):
    g = gdir or game_dir()
    return os.path.join(g, "Modules", "Rocket.Unturned") if g else None


def rocket_src_dir(gdir=None):
    g = gdir or game_dir()
    return os.path.join(g, "Extras", "Rocket.Unturned") if g else None


def _dir_files(p):
    """目录里的 {文件名: 字节数}; 目录不存在或读不到返回 {}"""
    if not p:
        return {}
    try:
        return {n: os.path.getsize(os.path.join(p, n))
                for n in os.listdir(p) if os.path.isfile(os.path.join(p, n))}
    except OSError:
        return {}


def rocket_check(gdir=None):
    """Rocket 到底装没装好: 看必需文件在不在、是不是空的, 不看「Modules 下有没有那个文件夹」。

    旧版只判 isdir, 于是半装 (xcopy 被杀软拦下一半 / Extras 里根本没有源文件) 也报「已安装」,
    游戏静默不加载 Rocket —— 用户看到界面说正常, 控制台却永远连不上。
    """
    have = _dir_files(rocket_dir(gdir))
    src = _dir_files(rocket_src_dir(gdir))
    missing = [n for n in ROCKET_REQUIRED if have.get(n, 0) <= 0]
    # 与官方 Extras 源文件大小不一致: 只当提示, 不算没装 (有人自己换过新版 Rocket 的 dll)
    stale = [n for n in sorted(src) if n in have and src[n] != have[n]]
    installed = not missing
    if installed:
        detail = f"必需文件齐 ({len(have)} 个)"
    elif have:
        detail = "目录里有 " + str(len(have)) + " 个文件, 但缺 " + "、".join(missing)
    else:
        detail = "Modules\\Rocket.Unturned 是空的 (或不存在)" if src else \
            "Modules\\Rocket.Unturned 不存在"
    return {"installed": installed, "missing": missing, "stale": stale,
            "files": len(have), "src": len(src), "detail": detail}


def rocket_installed():
    g = game_dir()
    return bool(g) and rocket_check(g)["installed"]


def rocket_status():
    """Rocket 前置状态: 是否真能加载 / 游戏目录里有没有官方安装脚本和源文件"""
    if not game_dir():
        return {"installed": False, "bat": False, "src": 0, "missing": [], "stale": [],
                "files": 0, "detail": "还没绑定游戏目录"}
    st = rocket_check()
    st["bat"] = os.path.isfile(os.path.join(game_dir(), "Extras", "Install Rocket.bat"))
    return st


def rocket_problem():
    """没装好时的一句人话; 装好了返回空串"""
    st = rocket_status()
    if st["installed"]:
        return ""
    if not st["src"]:
        return ("游戏目录 Extras 里没有 Rocket.Unturned 这个文件夹 —— 官方安装脚本没了源文件, "
                "跑它也是复制 0 个文件。请在 Steam 库右键开服端 → 管理 → 验证游戏文件完整性, "
                "再回来重装 Rocket")
    return "Rocket 没装好: " + st["detail"]


def _rocket_manual_hint(why):
    return (f"{why} 可以先手动双击游戏目录 Extras 里的「Install Rocket.bat」"
            "(弹出的黑窗口按一下回车就跑完了), 回来点「🔄 重新检测」。"
            "反复这样多半是杀软或 Windows「受控文件夹访问」拦住了往 Steam 目录写 dll —— "
            "临时放行再装一次")


def install_rocket():
    """运行 Extras\\Install Rocket.bat 安装/重装 Rocket。

    bat 里那句 xcopy 一个文件没复制也会返回 0, 所以成败只看必需文件有没有真的落地。
    """
    g = game_dir()
    if not g:
        return False, "还没有绑定游戏目录"
    bat = os.path.join(g, "Extras", "Install Rocket.bat")
    if not os.path.isfile(bat):
        return False, ("找不到 Extras\\Install Rocket.bat —— 请在 Steam 库右键开服端 → 管理 → "
                       "验证游戏文件完整性, 再回来重装")
    if not _dir_files(rocket_src_dir()):
        return False, (rocket_problem() or
                       ("Extras 里没有 Rocket.Unturned 源文件夹, 跑安装脚本只会复制 0 个文件 —— "
                        "Modules 里已经装好的 " + str(rocket_check()["files"]) + " 个文件不受影响, "
                        "但想重装/更新的话, 请先在 Steam 库右键开服端 → 管理 → 验证游戏文件完整性"))
    try:
        r = subprocess.run([bat], cwd=os.path.dirname(bat), capture_output=True,
                           stdin=subprocess.DEVNULL, timeout=180)
        out = ((r.stdout or b"") + (r.stderr or b"")).decode("gbk", "replace")
    except (OSError, subprocess.TimeoutExpired) as e:
        oplog("错误", f"Rocket 安装失败: {e}")
        return False, _rocket_manual_hint(f"安装脚本没能跑起来: {e}")
    st = rocket_check()
    src = _dir_files(rocket_src_dir())
    after = _dir_files(rocket_dir())
    same = sum(1 for n, s in src.items() if after.get(n) == s)
    denied = re.search(r"拒绝访问|Access is denied|not enough (memory|space)|内存不足|磁盘空间不足",
                       out, re.I)
    copied = re.search(r"(?:复制了|Copied)\s*[:：]?\s*(\d+)", out, re.I)
    oplog("操作", f"Rocket 安装脚本跑完: 必需文件齐={not st['missing']} 与官方源一致={same}/"
                  f"{len(src)} xcopy报复制={copied.group(1) if copied else '?'} "
                  f"返回码={r.returncode}" + (f" 拦截={denied.group(0)}" if denied else ""))
    if st["installed"] and not st["stale"]:
        return True, f"Rocket 安装/更新完成 (与官方源一致的有 {same} 个), 重启服务器后生效"
    if st["installed"]:
        return True, ("Rocket 必需文件已齐, 重启服务器后生效。另有 "
                      + str(len(st["stale"])) + " 个文件与官方 Extras 里的大小不同 —— "
                      "如果是你自己换过新版 Rocket 就没事")
    return False, _rocket_manual_hint(
        ("复制时被系统拦住了 (" + denied.group(0) + ") —— " if denied else "官方脚本跑完了、返回码也是 0, 但 ")
        + "Rocket 仍然没装好: " + st["detail"] + "。")


# ------------------------------------------------- Rocket 指令反馈汉化 (不是汉化控制台日志)
# 键 = Rocket 翻译文件里的英文 Value 原文 (已反转义), 值 = 中文
ROCKET_ZH = {
    # Rocket.en.translation.xml — RCON / 连接提示
    "{0} connected to the server": "{0} 已连接到服务器",
    "{0} disconnected from the server": "{0} 已从服务器断开连接",
    ("#{0}, Connection ID: {1}, Authed: {2}, Address: {3}, Time Connected: {4}, "
     "Connected For: {5}."): ("#{0}, 连接 ID: {1}, 已验证: {2}, 地址: {3}, 连接时间: {4}, "
                              "已连接时长: {5}。"),
    "Usage: rkick <ConnectionID> - Kicks a client off of RCON.":
        "用法: rkick <连接ID> - 把某个客户端从 RCON 踢下线。",
    "Error: RCON Client with Connection ID: {0} not found!":
        "错误: 找不到连接 ID 为 {0} 的 RCON 客户端!",
    "RCON Client kicked with Connection ID: {0}, Address: {1}!":
        "已踢出连接 ID 为 {0}、地址为 {1} 的 RCON 客户端!",
    "Usage: rflush <y> - kicks all connected RCON clients on the server.":
        "用法: rflush <y> - 踢掉服务器上所有已连接的 RCON 客户端。",
    "Closing {0} RCON connections.": "正在关闭 {0} 个 RCON 连接。",
    "#{0}, ConnectionID: {1}, Address: {2}, closed!": "#{0}, 连接 ID: {1}, 地址: {2}, 已关闭!",
    "You do not have permissions to execute this command.": "你没有权限执行这条指令。",
    "You have to wait {0} seconds before you can use this command again.":
        "你需要等待 {0} 秒才能再次使用这条指令。",

    # Rocket.Unturned.en.translation.xml — 游戏内指令反馈
    "Failed to find player": "没有找到玩家",
    "Invalid parameter": "参数不正确",
    "Target player not found": "没有找到目标玩家",
    "You cannot teleport while driving or riding in a vehicle.": "驾驶或乘坐载具时无法传送。",
    "{0} enabled Godmode": "{0} 已开启无敌模式",
    "You can feel the strength now...": "力量涌上来了……",
    "{0} disabled Godmode": "{0} 已关闭无敌模式",
    "The godly powers left you...": "神力离你而去……",
    "{0} enabled Vanishmode": "{0} 已开启隐身模式",
    "You are vanished now...": "你现在隐身了……",
    "{0} disabled Vanishmode": "{0} 已关闭隐身模式",
    "You are no longer vanished...": "你不再隐身了……",
    "{0} is in duty": "{0} 已进入值班模式",
    "You are in duty now...": "你现在处于值班模式……",
    "{0} is no longer in duty": "{0} 已退出值班模式",
    "You are no longer in duty...": "你已退出值班模式……",
    "You do not have a bed to teleport to.": "你没有可以传送过去的床。",
    "Your bed is obstructed.": "你的床被挡住了。",
    "You have tried to spawn too many items! The limit is {0}.": "你想生成的物品太多了!上限是 {0}。",
    "This item is restricted!": "这个物品被限制了!",
    "Giving {0} item {1}:{2}": "正在给 {0} 物品 {1}:{2}",
    "Giving you item {0}x {1} ({2})": "正在给你物品 {0}x {1} ({2})",
    "Spawning {1} zombies near {0}": "正在 {0} 附近生成 {1} 个僵尸",
    "Spawning {0} zombies nearby": "正在附近生成 {0} 个僵尸",
    "Failed giving you item {0}x {1} ({2})": "给你物品 {0}x {1} ({2}) 失败",
    "Giving {0} vehicle {1}": "正在给 {0} 载具 {1}",
    "This vehicle is restricted!": "这个载具被限制了!",
    "Giving you a {0} ({1})": "正在给你一辆 {0} ({1})",
    "Failed giving you a {0} ({1})": "给你 {0} ({1}) 失败",
    "TPS: {0}": "服务器 TPS: {0}",
    "Running since: {0} UTC": "开服至今: {0} UTC",
    "Reloaded permissions": "权限表已重新载入",
    "{0} groups are: {1}": "{0} 所在的用户组: {1}",
    "{0} permissions are: {1}": "{0} 的权限: {1}",
    "{0} teleported to {1}": "{0} 已传送到 {1}",
    "Teleported to {0}": "已传送到 {0}",
    "Failed to find destination": "没有找到传送目标",
    "The player you are trying to teleport is in a vehicle": "你要传送的玩家正在载具里",
    "{0} was teleported to {1}": "已把 {0} 传送到 {1}",
    "Teleported {0} to you": "已把 {0} 传送到你位置",
    "You were teleported to {0}": "你已被传送到 {0}",
    "There was an error clearing {0} inventory.": "清空 {0} 的物品栏时出错。",
    "Your inventory was cleared!": "你的物品栏已被清空!",
    "Your inventory was cleared by {0}!": "你的物品栏被 {0} 清空了!",
    "You successfully cleared {0} inventory.": "你成功清空了 {0} 的物品栏。",
    "{0} SteamID64 is {1}": "{0} 的 SteamID64 是 {1}",
    "{0} was successfully healed": "已成功治疗 {0}",
    "You were healed by {0}": "你被 {0} 治疗了",
    "You were healed": "你已被治疗",
    "You are facing {0}": "你当前朝向: {0}",
    "N": "北", "E": "东", "S": "南", "W": "西",
    "NW": "西北", "NE": "东北", "SW": "西南", "SE": "东南",
    "Loaded: {0}": "已加载: {0}",
    "Unloaded: {0}": "已卸载: {0}",
    "Failure: {0}": "失败: {0}",
    "Cancelled: {0}": "已取消: {0}",
    "Reloading {0}": "正在重载 {0}",
    "The plugin {0} is not loaded": "插件 {0} 没有在运行",
    "Unloading {0}": "正在卸载 {0}",
    "Loading {0}": "正在加载 {0}",
    "The plugin {0} is already loaded": "插件 {0} 已经加载过了",
    "Reloading Rocket": "正在重载 Rocket 前置",
    "Please reload individual plugins instead": "请改为逐个重载插件",
    "Group not found": "没有找到用户组",
    "{0} was added to the group {1}": "已把 {0} 加入用户组 {1}",
    "{0} was removed from from the group {1}": "已把 {0} 从用户组 {1} 移除",
    "Unknown error": "未知错误",
    "{0} was not found": "没有找到 {0}",
    "{1} was not found": "没有找到 {1}",
    "{0} is already in the group {1}": "{0} 已经在用户组 {1} 里了",
    "Permissions reloaded": "权限设置已重新载入",
    "Plugin {0} not found": "没有找到插件 {0}",
    "You successfully cleared {0} items": "你成功清理了 {0} 个物品",
    "Usage: /more <amount>": "用法: /more <数量>",
    "No item being held in hands.": "手上没有拿物品。",
    "Giving {0} of item: {1}.": "已把 {1} 的数量改为 {0}。",
    "invalid character name": "角色名称不合法",
    "Command not found.": "没有找到这条指令。",
}
_ROCKET_ZH_BACK = {v: k for k, v in ROCKET_ZH.items()}
ROCKET_ZH_FILES = ("Rocket.en.translation.xml", "Rocket.Unturned.en.translation.xml")
_XML_UNESC = (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'"), ("&#39;", "'"))
_VALUE_RE = re.compile(r'Value="([^"]*)"')


def xml_unescape(s):
    for e, c in _XML_UNESC:
        s = s.replace(e, c)
    return s.replace("&amp;", "&")


def xml_escape(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def rocket_trans_paths():
    sd = server_dir()
    return [os.path.join(sd, "Rocket", f) for f in ROCKET_ZH_FILES] if sd else []


def _translate_xml(text, mapping):
    """按英文 Value 原文整段替换 (同一 Id 可能对应多条 Value, 所以不能用 Id 匹配)"""
    hit = 0

    def rep(m):
        nonlocal hit
        key = xml_unescape(m.group(1))
        if key not in mapping:
            return m.group(0)
        hit += 1
        return 'Value="' + xml_escape(mapping[key]) + '"'

    return _VALUE_RE.sub(rep, text), hit


def rocket_zh_status():
    """指令反馈汉化状态: missing=还没生成翻译文件, on=已是中文"""
    paths = [p for p in rocket_trans_paths() if os.path.isfile(p)]
    if not paths:
        return {"files": 0, "on": False, "missing": True}
    en = zh = 0
    for p in paths:
        try:
            text = read_file(p)[0]
        except OSError:
            continue
        for v in _VALUE_RE.findall(text):
            v = xml_unescape(v)
            en += v in ROCKET_ZH
            zh += v in _ROCKET_ZH_BACK
    return {"files": len(paths), "on": zh > 0 and en == 0, "missing": False}


def set_rocket_zh(on):
    """开/关 Rocket 指令反馈汉化: 改写存档 Rocket 目录里的两个 .en.translation.xml"""
    st = rocket_zh_status()
    if st["missing"]:
        return False, ("存档的 Rocket 文件夹里还没有 .en.translation.xml — "
                       "请先安装 Rocket 前置插件并开一次服, 让 Rocket 自动生成这两个文件")
    mapping = ROCKET_ZH if on else _ROCKET_ZH_BACK
    total, files = 0, 0
    for p in rocket_trans_paths():
        if not os.path.isfile(p):
            continue
        try:
            old, enc = read_file(p)
            new, n = _translate_xml(old, mapping)
            if n:
                # 原样写回 (不追加换行/不转换换行符), 保证恢复英文时逐字节还原
                with open(p, "w", encoding=enc, newline="") as f:
                    f.write(new)
                files += 1
                total += n
        except OSError as e:
            oplog("错误", f"Rocket 指令汉化写入失败 {os.path.basename(p)}: {e}")
            return False, f"写入失败: {e}"
    if not total:
        return True, ("已经处于" + ("汉化" if on else "英文") + "状态了, 无需改动")
    oplog("操作", f"Rocket 指令反馈汉化 → {'开启' if on else '恢复英文'} ({files} 个文件 {total} 条文案)")
    return True, (f"已{'汉化' if on else '恢复英文'} {total} 条指令反馈 ({files} 个文件), 重启服务器后生效")


# ================================================================ 存档备份 / 删除
LEVEL_FILE_ZH = {
    "structures": "建筑主体 — 玩家搭建的房子、楼梯、地板等结构数据",
    "barricades": "障碍物 — 地基、墙、门、栅栏、路障、陷阱等摆放物",
    "objects": "场景物件 — 地图上被移动/破坏过的箱子、物品与物件",
    "vehicles": "载具 — 停放在地图上的车、船、飞机等",
    "groups": "建筑分组 — 把多个结构归成同一栋房的记录",
    "lighting": "光照数据 — 该地图的灯光/夜景缓存",
}
PLAYER_FILE_ZH = {
    "player": "玩家主档 — 角色、职业与出生点等基础信息",
    "inventory": "物品栏 — 背包、随身与存储的全部物品",
    "skills": "技能 — 各项技能等级与经验",
    "life": "生存状态 — 血量、饥饿、口渴、疾病等",
    "quests": "任务进度 — 各任务/成就的完成情况",
    "clothing": "外观穿着 — 当前穿戴的衣服装备外观",
    "anim": "动作数据 — 动画与姿态状态",
}


def save_file_desc(rel):
    """给加密的存档文件一句中文说明 (文件本身打不开, 只能按名字说明)"""
    name = os.path.basename(rel)
    stem = os.path.splitext(name)[0].lower()
    old = name.endswith("~")
    parts = rel.replace("\\", "/").split("/")
    d = (LEVEL_FILE_ZH if parts[0].lower() == "level" else PLAYER_FILE_ZH).get(stem)
    if not d:
        d = "存档数据文件 (未识别的名称, 属于该存档的地图/玩家数据)"
    return ("♻ 上一次保存的旧版本 · " + d) if old else d


def save_file_full(rel, must_exist=True):
    """把相对路径解析成绝对路径; 只允许 Level / Players 两个子树内的文件"""
    sd = server_dir()
    if not sd:
        return None
    top = str(rel).replace("\\", "/").strip("/")
    parts = [p for p in top.split("/") if p not in ("", ".", "..")]
    if len(parts) < 2 or parts[0].lower() not in ("level", "players"):
        return None
    base = os.path.abspath(sd)
    full = os.path.abspath(os.path.join(base, *parts))
    if not full.startswith(base + os.sep):
        return None
    if os.path.isdir(full):
        return None                              # 目录一律拒绝: 只处理文件
    if must_exist and not os.path.isfile(full):
        return None
    if os.path.splitext(full)[1].lower() not in (".dat", ".dat~"):
        return None                              # 白名单: 只认存档数据文件
    return full


def _file_row(full, rel):
    try:
        st = os.stat(full)
    except OSError:
        return None
    return {"rel": rel.replace("\\", "/"), "size": st.st_size,
            "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
            "desc": save_file_desc(rel), "old": rel.endswith("~")}


def save_inventory():
    """列出建筑存档(Level/<地图>) 与玩家存档(Players/<SteamID>_0) 的可备份文件"""
    sd = server_dir()
    out = {"level": [], "players": []}
    if not sd:
        return out
    lv = os.path.join(sd, "Level")
    if os.path.isdir(lv):
        for m in sorted(os.listdir(lv)):
            d = os.path.join(lv, m)
            if not os.path.isdir(d):
                continue
            rows = []
            for name in sorted(os.listdir(d)):
                p = os.path.join(d, name)
                if os.path.isfile(p):
                    r = _file_row(p, f"Level/{m}/{name}")
                    if r:
                        rows.append(r)
            out["level"].append({"map": m, "files": rows,
                                 "size": sum(r["size"] for r in rows)})
    pl = os.path.join(sd, "Players")
    if os.path.isdir(pl):
        for pid in sorted(os.listdir(pl)):
            d = os.path.join(pl, pid)
            if not os.path.isdir(d):
                continue
            rows = []
            for cur, _dirs, names in os.walk(d):
                for name in sorted(names):
                    p = os.path.join(cur, name)
                    rel = os.path.relpath(p, sd)
                    if not save_file_full(rel):
                        continue
                    r = _file_row(p, rel)
                    if r:
                        rows.append(r)
            rows.sort(key=lambda r: r["rel"])
            maps = sorted({r["rel"].split("/")[2] for r in rows if len(r["rel"].split("/")) > 3})
            out["players"].append({"id": pid, "files": rows, "maps": maps,
                                   "size": sum(r["size"] for r in rows)})
    return out


def backup_saves(items, dest):
    """把选中的存档文件按原目录结构复制到用户指定位置; 返回 (ok, msg, 备份目录)"""
    inst = instance() or "save"
    files = []
    for rel in items:
        full = save_file_full(rel)
        if not full:
            return False, f"路径不被允许 (只能备份 Level/Players 里的存档文件): {rel}", None
        files.append((rel.replace("\\", "/"), full))
    if not files:
        return False, "没有勾选任何存档文件", None
    dest = str(dest or "").strip().strip('"')
    if not dest:
        return False, "请先选择备份保存的位置", None
    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    target = os.path.normpath(os.path.join(dest, f"{inst}_备份_{stamp}"))
    try:
        os.makedirs(dest, exist_ok=True)
        test = os.path.join(dest, ".ds_storagetest")
        with open(test, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(test)
    except OSError as e:
        return False, f"目标位置无法写入: {e} — 请换一个文件夹", None
    done, skipped, total_bytes = 0, [], 0
    for rel, full in files:
        dst = os.path.join(target, *rel.split("/"))
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(full, dst)
            if not (os.path.isfile(dst) and os.path.getsize(dst) == os.path.getsize(full)):
                skipped.append(rel)
                continue
            done += 1
            total_bytes += os.path.getsize(full)
        except OSError as e:
            skipped.append(f"{rel} ({e})")
    if not done:
        return False, "备份失败, 一个文件都没有复制成功 — 已中止, 不会删除任何东西", None
    msg = (f"已备份 {done} 个文件 ({human_size(total_bytes)}) → {target}"
           + (f"; 失败 {len(skipped)} 个: {', '.join(skipped[:3])}" if skipped else ""))
    oplog("操作", f"备份存档 {done} 个文件 → {target}")
    return True, msg, target


def delete_saves(items, dest):
    """删除存档文件: 强制先备份成功, 且只删对应文件, 绝不删目录"""
    running, _ = server_running()
    if running:
        return False, "服务器正在运行 — 请先关服再删除存档, 否则服务器会把数据重新写回去", None
    fulls = []
    for rel in items:
        full = save_file_full(rel)
        if not full:
            return False, f"路径不被允许, 已中止 (不会删除任何东西): {rel}", None
        fulls.append((rel.replace("\\", "/"), full))
    if not fulls:
        return False, "没有勾选要删除的存档文件", None
    ok, msg, target = backup_saves([r for r, _ in fulls], dest)
    if not ok:
        return False, f"备份没有完成, 已取消删除: {msg}", None
    removed, failed = 0, []
    for rel, full in fulls:
        try:
            os.remove(full)
            removed += 1
        except OSError as e:
            failed.append(f"{rel} ({e})")
    oplog("操作", f"删除存档文件 {removed} 个 (备份在 {target})")
    m = (f"已备份并删除 {removed} 个存档文件; 空文件夹已保留, 备份在 {target}")
    if failed:
        m += f"; {len(failed)} 个删除失败: {', '.join(failed[:3])}"
    return True, m, target


def list_dirs(path):
    """浏览本机目录, 只返回目录名 (供选择备份位置)"""
    p = str(path or "").strip().strip('"')
    if not p:
        roots = [{"name": f"{d}:\\", "path": f"{d}:\\"} for d in "CDEFGHIJKLMNOPQRSTUVWXYZ"
                 if os.path.isdir(f"{d}:\\")]
        return {"ok": True, "cwd": "", "parent": "", "dirs": roots,
                "drives": True}
    full = os.path.abspath(p)
    parent = os.path.dirname(full)
    if parent == full:                              # 已在盘符根: 上一级回到盘符列表
        parent = ""
    out = []
    try:
        for name in sorted(os.listdir(full), key=str.lower):
            sub = os.path.join(full, name)
            try:
                if not os.path.isdir(sub):
                    continue
                if os.stat(sub).st_file_attributes & 2:      # 隐藏目录不显示
                    continue
            except OSError:
                continue
            out.append({"name": name, "path": sub})
            if len(out) >= 400:
                break
    except OSError as e:
        return {"ok": False, "msg": f"打不开该文件夹: {e}"}
    return {"ok": True, "cwd": full, "parent": parent, "dirs": out}


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n} B"


def _write_title_file(n):
    """把在线人数写给控制台中继器 (窗口标题用)"""
    try:
        sd = server_dir()
        if sd:
            with open(os.path.join(sd, "title.tmp"), "w", encoding="utf-8") as f:
                f.write(str(n))
    except OSError:
        pass


def _title_updater(tfile):
    """每 30 秒经 RCON 查询一次在线人数, 供服务器控制台窗口标题显示; 服务器退出后清理"""
    while True:
        time.sleep(30)
        with _proc_lock:
            alive = _proc is not None and _proc.poll() is None
        if not alive and server_running()[0] is False:
            try:
                os.remove(tfile)
            except OSError:
                pass
            return
        try:
            n = len(parse_players(rcon_command("players")))
            _write_title_file(n)
        except Exception:
            pass


# Rocket / 原版 players 输出的一行:
#   PlayerID: 76561198840375182 Name: Pippl Character: #小明 Ping: 86
PLAYER_ROW_RE = re.compile(
    r"playerid\s*[:：]\s*(\d{6,})\s+name\s*[:：]\s*(.*?)"
    r"(?:\s*character\s*[:：]\s*(.*?))?(?:\s+ping\s*[:：]\s*[\d.]+)?\s*$", re.I)
STEAMID_RE = re.compile(r"\b(765\d{14,16})\b")
# 这些词是表格表头/字段名, 绝不能被当成玩家名字
NOT_A_NAME = {"players", "player", "playerid", "steamid", "steam", "name",
              "character", "ping", "online", "offline", "id", "id:", "列表"}


def parse_players(resp):
    """解析 players 命令输出 -> [{name, steamid}]; 兼容各种输出格式"""
    players, seen, names = [], set(), set()
    if not resp:
        return players
    if re.search(r"fail(?:ed)?\s+to find|no (?:players|one)|nobody|0 players", resp, re.I):
        return players

    def add(name, sid):
        name = re.sub(r"^\d+\s*[.)]\s*", "", (name or "")).strip(" ,.-–—:.\t|()[]{}")
        sid = (sid or "").strip()
        if sid:
            if sid in seen:
                return
            seen.add(sid)
        elif name in names:
            return
        if (not name or len(name) > 40 or name.lower() in NOT_A_NAME
                or re.match(r"^(ping|character|name|playerid|steamid)\s*[:：]", name, re.I)):
            name = sid
        if not name:
            return
        names.add(name)
        players.append({"name": name, "steamid": sid})

    for raw in resp.splitlines():
        line = re.sub(r"^(\[[^\]]*\]\s*)+", "", raw.strip())   # 去 [Info] 等日志前缀
        if not line:
            continue
        # 跳过指令回显和明显不是玩家行的内容
        if re.search(r"executed command|has executed|mscorlib\s*>>", line, re.I):
            continue
        # 表头行 "Players:" / "Player list" —— 只认 "PlayerID: …" 那种数据行
        if re.match(r"^players?\s*[:：]?\s*$", line, re.I):
            continue
        # "Players: 名字1, 名字2" 逗号列表格式
        m0 = re.match(r"^players?\s*[:：]\s*(.+)$", line, re.I)
        if m0:
            listing = m0.group(1)
            if "," in listing or "、" in listing or "，" in listing:
                for n in re.split(r"[,，、]", listing):
                    ms = STEAMID_RE.search(n)
                    add(re.sub(STEAMID_RE, "", n), ms.group(1) if ms else "")
            else:
                ms = STEAMID_RE.search(listing)
                add(re.sub(STEAMID_RE, "", listing), ms.group(1) if ms else "")
            continue
        if re.match(r"^(successfully|saved|loading|updates|ticks)", line, re.I):
            continue
        m = PLAYER_ROW_RE.search(line)
        if m:
            add(m.group(2) or m.group(3), m.group(1))
            continue
        ms = STEAMID_RE.search(line)
        if ms:
            sid = ms.group(1)
            before = line[:ms.start()].strip(" -–—:.\t|")
            after = line[ms.end():].strip(" -–—:.\t|")
            cand = before or re.sub(r"^name\s*[:：]?\s*", "", after, flags=re.I)
            add(cand, sid)
            continue
        # 只剩纯文字行: 必须像人名才收 (避免把日志句子当成玩家)
        if len(line) > 40 or re.search(r"\d", line):
            continue
        add(line, "")
    return players


# ================================================================ 页面骨架 (明/暗双主题)
CSS = """
:root{--bg:hsl(222.2,84%,4.9%);--panel:hsl(222.2,60%,7%);--card:hsl(220,45%,9%);
--hov:hsl(217.2,32.6%,14%);--line:hsl(217.2,32.6%,17.5%);--gray:hsl(217.2,32.6%,20%);
--txt:hsl(210,40%,98%);--sub:hsl(215,20.2%,65.1%);
--acc:#3b82f6;--acc2:#60a5fa;--ok:#22c55e;--bad:#ef4444;--warn:#eab308;--code:#93c5fd;
--codebg:rgba(59,130,246,.13);--codebd:rgba(59,130,246,.3);
--bigtxt:#fff;--bigglow:rgba(59,130,246,.45)}
html.light{--bg:#eef1e8;--panel:#f5f7ef;--card:#fbfcf6;--hov:#e2e7d8;--line:#c9cfc0;--gray:#b9c2ab;
--txt:#14191e;--sub:#39443f;--acc:#2a6bc6;--acc2:#1d5cb0;--ok:#0f7a40;--bad:#c22626;--warn:#7d6007;
--code:#17457e;--codebg:rgba(42,107,198,.15);--codebd:rgba(42,107,198,.45);
--bigtxt:#123a6b;--bigglow:rgba(42,107,198,.22)}
*{box-sizing:border-box;margin:0;padding:0}
body{color:var(--txt);font:15px/1.65 "Microsoft YaHei","PingFang SC",sans-serif;
background:
 radial-gradient(900px at 12% -8%, rgba(59,130,246,.16), transparent 60%),
 radial-gradient(900px at 90% 112%, rgba(139,92,246,.15), transparent 60%),
 radial-gradient(650px at 58% 42%, rgba(14,165,233,.07), transparent 60%),
 var(--bg);
background-attachment:fixed}
.layout{display:flex;min-height:100vh}
.side{width:232px;flex:none;background:rgba(10,14,23,.66);border-right:1px solid rgba(255,255,255,.07);
backdrop-filter:blur(20px) saturate(150%);-webkit-backdrop-filter:blur(20px) saturate(150%);
display:flex;flex-direction:column;position:sticky;top:0;height:100vh}
html.light .side{background:rgba(255,255,255,.58);border-right:1px solid rgba(255,255,255,.7)}
.logo{padding:22px 20px 16px;display:flex;gap:11px;align-items:center}
.logo .ic{width:38px;height:38px;border-radius:10px;background:linear-gradient(135deg,#3b82f6,#8b5cf6);
display:flex;align-items:center;justify-content:center;font-size:20px}
.logo b{font-size:16px}.logo .sub{font-size:11px;color:var(--sub)}
.nav{padding:8px 12px;flex:1;overflow:auto}
.nav a{display:flex;gap:11px;align-items:center;padding:10px 13px;border-radius:10px;color:var(--sub);
text-decoration:none;font-size:14px;margin-bottom:2px;transition:.15s}
.nav a:hover{background:rgba(255,255,255,.05);color:var(--txt)}
html.light .nav a:hover{background:rgba(0,0,0,.05)}
.nav a.on{background:linear-gradient(90deg,rgba(59,130,246,.22),rgba(59,130,246,.06));color:var(--txt);
box-shadow:inset 0 0 0 1px rgba(59,130,246,.35)}
html.light .nav a.on{background:rgba(62,126,207,.14);color:#1d4f8f}
.nav .ico{width:20px;text-align:center;font-size:15px;display:flex;align-items:center;justify-content:center}
.nav .ico svg{width:17px;height:17px;fill:none;stroke:currentColor;stroke-width:1.8;
stroke-linecap:round;stroke-linejoin:round;display:block}
.side .foot{padding:14px 16px;border-top:1px solid rgba(255,255,255,.08);font-size:12px;color:var(--sub)}
html.light .side .foot{border-top:1px solid rgba(0,0,0,.08)}
.dot{width:9px;height:9px;border-radius:50%;display:inline-block;background:var(--bad);
box-shadow:0 0 8px var(--bad);margin-right:7px}
.dot.on{background:var(--ok);box-shadow:0 0 8px var(--ok)}
.main{flex:1;min-width:0;padding:26px 30px 60px}
.pagehead{display:flex;align-items:center;gap:14px;margin-bottom:20px;flex-wrap:wrap}
.pagehead h1{font-size:21px;letter-spacing:.5px}
.chip{font-size:12px;padding:4px 12px;border-radius:99px;border:1px solid rgba(255,255,255,.1);
background:rgba(255,255,255,.04);color:var(--sub)}
html.light .chip{border-color:rgba(0,0,0,.1);background:rgba(255,255,255,.5)}
.chip.on{color:var(--ok);border-color:var(--ok);background:rgba(34,197,94,.08)}
.chip.off{color:var(--bad);border-color:var(--bad);background:rgba(239,68,68,.08)}
.card{background:rgba(255,255,255,.035);border:1px solid rgba(255,255,255,.08);border-radius:16px;
padding:18px 20px;margin-bottom:16px;backdrop-filter:blur(16px) saturate(140%);
-webkit-backdrop-filter:blur(16px) saturate(140%);box-shadow:0 10px 34px rgba(0,0,0,.22)}
html.light .card{background:rgba(255,255,255,.55);border:1px solid rgba(255,255,255,.7);
box-shadow:0 10px 30px rgba(31,45,61,.08)}
.card h2{font-size:16px;margin-bottom:6px}.card .desc{color:var(--sub);font-size:13px;margin-bottom:4px}
.grid{display:grid;gap:14px}.g2{grid-template-columns:1fr 1fr}.g3{grid-template-columns:repeat(3,1fr)}
.g4{grid-template-columns:repeat(4,1fr)}@media(max-width:960px){.g2,.g3,.g4{grid-template-columns:1fr}}
.btn{background:linear-gradient(135deg,#3b82f6,#2563eb);color:#fff;border:0;border-radius:10px;
padding:9px 20px;cursor:pointer;font-size:14px;transition:.15s;letter-spacing:.5px}
.btn:hover{filter:brightness(1.15);box-shadow:0 4px 16px rgba(59,130,246,.35)}.btn:active{transform:scale(.97)}
.btn.gray{background:rgba(255,255,255,.09);border:1px solid rgba(255,255,255,.12)}
html.light .btn.gray{background:rgba(0,0,0,.06);border-color:rgba(0,0,0,.1)}
.btn.green{background:linear-gradient(135deg,#22c55e,#15803d)}
.btn.red{background:linear-gradient(135deg,#ef4444,#b91c1c)}
.btn.sm{padding:5px 13px;font-size:13px}.btn.big{padding:12px 30px;font-size:16px;border-radius:12px}
/* 傻瓜式开关 (设置项) */
.tog{position:relative;width:46px;height:26px;flex:none;cursor:pointer;display:inline-block}
.tog input{opacity:0;width:0;height:0;position:absolute}
.tog .tr{position:absolute;inset:0;background:rgba(255,255,255,.14);border-radius:99px;transition:.2s;
box-shadow:inset 0 1px 3px rgba(0,0,0,.35)}
html.light .tog .tr{background:rgba(0,0,0,.15);box-shadow:inset 0 1px 3px rgba(0,0,0,.15)}
.tog .tr:before{content:"";position:absolute;width:20px;height:20px;border-radius:50%;background:#cbd5e1;
top:3px;left:3px;transition:.2s;box-shadow:0 2px 6px rgba(0,0,0,.3)}
.tog input:checked+.tr{background:linear-gradient(135deg,#22c55e,#15803d)}
.tog input:checked+.tr:before{transform:translateX(20px);background:#fff}
/* 玻璃质感勾选框 (Uiverse.io 模板) — 勾选=蓝色高亮打勾 */
.container{display:inline-flex;align-items:center;gap:10px;position:relative;cursor:pointer;
user-select:none;-webkit-tap-highlight-color:transparent;color:var(--sub);font-size:13px;
vertical-align:middle;--ck:22px}
.container.ck-lg{--ck:26px}
.container:hover{color:var(--txt)}
.container input{position:absolute;opacity:0;cursor:pointer;height:0;width:0}
.container .checkmark{position:relative;display:inline-block;flex:none;height:var(--ck);width:var(--ck);
border-radius:38%;background:rgba(255,255,255,.12);backdrop-filter:blur(16px) saturate(180%);
-webkit-backdrop-filter:blur(16px) saturate(180%);border:1px solid rgba(255,255,255,.25);
box-shadow:0 8px 20px rgba(0,0,0,.12),0 2px 6px rgba(0,0,0,.08),
inset 0 1px 2px rgba(255,255,255,.5),inset 0 -1px 2px rgba(0,0,0,.1);
transition:all .4s cubic-bezier(.16,1,.3,1)}
.container:hover input:not(:checked)~.checkmark{background:rgba(255,255,255,.2);
border-color:rgba(255,255,255,.4);transform:translateY(-1px);
box-shadow:0 12px 24px rgba(0,0,0,.15),0 4px 8px rgba(0,0,0,.08),inset 0 1.5px 3px rgba(255,255,255,.7)}
.container input:checked~.checkmark{transform:scale(1.22);
background:linear-gradient(145deg,#004cff 0%,#0e34b3 50%,hsl(217,100%,25%) 100%);
border-color:rgba(255,255,255,.45);
box-shadow:0 12px 28px rgba(0,81,255,.45),0 4px 10px rgba(0,76,255,0),
inset 0 2px 4px rgba(255,255,255,.6),inset 0 -2px 6px rgba(255,255,255,.35)}
.container .checkmark:after{content:"";position:absolute;left:33%;top:18.75%;
width:20%;height:40%;border:solid #fff;border-width:0 calc(var(--ck)*.13) calc(var(--ck)*.13) 0;
border-radius:2px;transform:rotate(45deg) scale(.3);opacity:0;
filter:drop-shadow(0 1px 2px rgba(0,0,0,.3));transition:all .35s cubic-bezier(.34,1.56,.64,1)}
.container input:checked~.checkmark:after{opacity:1;transform:rotate(45deg) scale(1)}
.container:active input~.checkmark{transform:scale(.92);transition:transform .15s ease-out}
.container input:focus-visible~.checkmark{outline:2px solid var(--acc2);outline-offset:3px}
html.light .container input:not(:checked)~.checkmark{background:rgba(0,0,0,.05);
border-color:rgba(0,0,0,.14);
box-shadow:0 6px 16px rgba(31,45,61,.12),0 1px 3px rgba(31,45,61,.1),
inset 0 1px 2px rgba(255,255,255,.9),inset 0 -1px 2px rgba(0,0,0,.05)}
html.light .container:hover input:not(:checked)~.checkmark{background:rgba(0,0,0,.09);
border-color:rgba(0,0,0,.22)}
.container .lbl{white-space:nowrap}
/* 渐变旋转光晕按钮 (复制服务器代码) */
.button{display:inline-flex;align-items:center;justify-content:center;padding:28px 56px;border:0;
position:relative;overflow:hidden;border-radius:10rem;transition:all .02s;font-weight:bold;
cursor:pointer;color:rgb(37,37,37);z-index:0;font-size:1.35em;letter-spacing:1px;
background:#eaf6ff;box-shadow:0 0 7px -5px rgba(0,0,0,.5)}
.button:hover{background:rgb(193,228,248);color:rgb(33,0,85)}
.button:active{transform:scale(.97)}
.button .hoverEffect{position:absolute;bottom:0;top:0;left:0;right:0;display:flex;
align-items:center;justify-content:center;z-index:1}
.button .hoverEffect div{background:linear-gradient(90deg,rgba(222,0,75,1) 0%,
rgba(191,70,255,1) 49%,rgba(0,212,255,1) 100%);border-radius:40rem;width:10rem;height:10rem;
transition:.4s;filter:blur(20px);animation:effect infinite 3s linear;opacity:.5}
.button:hover .hoverEffect div{width:8rem;height:8rem}
.button .txt{position:relative;z-index:2}
@keyframes effect{0%{transform:rotate(0deg)}100%{transform:rotate(360deg)}}
.trow{display:flex;gap:14px;align-items:center;padding:13px 4px;border-bottom:1px solid rgba(255,255,255,.06)}
html.light .trow{border-bottom:1px solid rgba(0,0,0,.06)}
.trow:last-child{border-bottom:0}
.trow .bar{width:4px;border-radius:4px;align-self:stretch;margin:3px 0;flex:none;
background:linear-gradient(180deg,var(--acc),#8b5cf6);opacity:.85}
.trow .nm{font-size:15px;font-weight:bold;letter-spacing:.3px}.trow .ds{color:var(--sub);font-size:13px;margin-top:2px}
.trow .st{font-size:12px;margin-top:4px}.trow .st.on{color:var(--ok)}.trow .st.off{color:var(--sub)}
.trow .rgt{margin-left:auto;display:flex;flex-direction:column;align-items:flex-end;gap:4px}
code.k{background:var(--codebg);border:1px solid var(--codebd);color:var(--code);
font:12px Consolas,monospace;padding:1px 7px;border-radius:5px;margin:2px 3px 2px 0;display:inline-block}
label.f{display:block;margin:12px 0 3px;font-size:13px;color:var(--sub)}
label.f b{color:var(--txt);font-size:14px;margin-right:8px}
input.f,textarea.f,select.f{width:100%;background:rgba(255,255,255,.05);color:var(--txt);
border:1px solid rgba(255,255,255,.1);border-radius:9px;padding:9px 12px;font-size:14px;outline:0;transition:.15s}
html.light input.f,html.light textarea.f,html.light select.f{background:rgba(255,255,255,.65);
border-color:rgba(0,0,0,.12)}
input.f:focus,textarea.f:focus,select.f:focus{border-color:var(--acc);box-shadow:0 0 0 3px var(--codebg)}
textarea.f{font-family:Consolas,monospace;font-size:13px}
select.f option{background:#0b1120;color:#e2e8f0}
html.light select.f option{background:#fff;color:#161b20}
.pills{display:flex;gap:8px;flex-wrap:wrap}
.pills label{cursor:pointer}
.pills input{display:none}
.pills span{display:inline-block;padding:7px 18px;border-radius:99px;border:1px solid var(--line);
color:var(--sub);font-size:14px;transition:.15s;background:var(--bg)}
.pills input:checked+span{background:var(--codebg);border-color:var(--acc);color:var(--acc)}
.addr{text-align:center;padding:30px 20px 28px;position:relative}
.addr .lb{color:var(--sub);font-size:14px;letter-spacing:2px}
.addr .big{font:700 42px/1.3 Consolas,monospace;color:var(--bigtxt);margin:10px 0 6px;
text-shadow:0 0 30px var(--bigglow);word-break:break-all}
.addr .big.idle{font-family:inherit;font-size:17px;font-weight:600;line-height:1.9;
letter-spacing:0;color:var(--sub);text-shadow:none;max-width:560px;margin:14px auto 6px}
.addr .rfbtn{position:absolute;top:16px;right:16px;z-index:2;
display:inline-flex;align-items:center;gap:5px}
.addr .rfbtn .ric{display:inline-block;font-size:14px;line-height:1;transition:transform .3s}
.addr .rfbtn.spin .ric{animation:rfspin .7s linear infinite}
@keyframes rfspin{to{transform:rotate(360deg)}}
.addr .tip{color:var(--sub);font-size:13px}
.stat{background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.08);border-radius:13px;
padding:13px 16px;backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px)}
html.light .stat{background:rgba(255,255,255,.5);border-color:rgba(255,255,255,.65)}
.stat .k{color:var(--sub);font-size:12px}.stat .v{font-size:17px;font-weight:bold;margin-top:3px}
.banner{border-radius:12px;padding:12px 16px;font-size:14px;margin-bottom:16px;border:1px solid}
.banner.warn{background:rgba(234,179,8,.09);border-color:var(--warn);color:var(--warn)}
/* 帮助页置顶: 进不去服务器对照表 */
.card.sos{border-left:5px solid var(--bad);background:
linear-gradient(135deg,rgba(239,68,68,.10),rgba(255,255,255,.03) 45%)}
html.light .card.sos{background:linear-gradient(135deg,rgba(194,38,38,.08),rgba(255,255,255,.6) 45%)}
.card.sos h2{color:var(--bad)}
.sostag{font-size:11px;font-weight:bold;letter-spacing:1px;color:#fff;background:var(--bad);
border-radius:999px;padding:3px 10px;flex:none;animation:sospulse 2.2s ease-in-out infinite}
@keyframes sospulse{50%{opacity:.55}}
.sosrow{border:1px solid var(--line);border-left:3px solid var(--warn);border-radius:12px;
padding:11px 14px;margin-top:10px;background:rgba(255,255,255,.035)}
html.light .sosrow{background:rgba(255,255,255,.62)}
.sosrow>b{font-size:14px;color:var(--txt)}
.sosrow .fix{color:var(--sub);font-size:13px;line-height:1.95;margin-top:4px}
.sosrow .fix b{color:var(--txt)}
.sosqq{margin-top:12px;border:1px dashed var(--acc);border-radius:12px;padding:11px 14px;
font-size:13px;color:var(--sub);line-height:1.9}
.term{background:#0b1120;border:1px solid var(--line);border-radius:12px;padding:12px 14px;
height:480px;overflow-y:auto;font:13px/1.5 Consolas,monospace}
.term .l{white-space:pre-wrap;word-break:break-all;color:#cbd5e1}
.term .l.err{color:#fca5a5}.term .l.warn{color:#fde047}.term .l.cmd{color:#93c5fd}
.term .l.sys{color:#86efac}.term .l.ok{color:#6ee7b7;font-weight:bold}
#toast{position:fixed;top:20px;right:20px;z-index:99;display:flex;flex-direction:column;gap:8px}
.tmsg{background:var(--card);border:1px solid var(--acc);color:var(--acc2);border-radius:10px;
padding:11px 18px;font-size:14px;box-shadow:0 8px 30px rgba(0,0,0,.25);animation:tin .25s}
.tmsg.err{border-color:var(--bad);color:var(--bad)}
@keyframes tin{from{opacity:0;transform:translateX(30px)}to{opacity:1;transform:none}}
table{width:100%;border-collapse:collapse;font-size:14px}
td,th{padding:9px 10px;border-bottom:1px solid var(--line);text-align:left}
th{color:var(--sub);font-weight:normal;font-size:13px}
tr:hover td{background:var(--hov)}
a{color:var(--acc2);text-decoration:none}
a.btn{color:#fff}
.hint{color:var(--sub);font-size:12px;margin-top:6px}
.hint.ok{color:var(--ok)}.hint.err{color:var(--bad)}
/* 玩法设置: 数值规格 + 详细说明 */
.sp{font-size:11.5px;color:var(--acc2);margin:6px 0 0;line-height:1.6}
.det{font-size:12px;color:var(--sub);margin:3px 0 0;line-height:1.7}
.gperr{font-size:11.5px;color:var(--bad);margin:4px 0 0;min-height:0}
/* 玩法设置: 默认收起的高级配置 */
.advbox{border:1px solid var(--codebd);border-radius:16px;background:rgba(255,255,255,.02);margin-bottom:16px}
.advbox>summary{list-style:none;cursor:pointer;padding:15px 20px;font-size:14.5px;font-weight:700;
color:var(--acc2);user-select:none;display:flex;align-items:center;gap:9px;flex-wrap:wrap;outline:0}
.advbox>summary::-webkit-details-marker{display:none}
.advbox>summary::before{content:'▸';font-size:13px;line-height:1}
.advbox[open]>summary::before{content:'▾'}
.advbox>summary:hover{background:var(--codebg)}
.advbox>summary em{font-style:normal;font-weight:400;font-size:12px;color:var(--sub)}
.advbox[open]>summary{border-bottom:1px solid var(--line)}
.advbody{padding:14px 14px 0}
.advbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:0 6px 13px}
.advfilter{flex:1 1 250px;min-width:190px}
.advhide{display:none !important}
/* 启动参数: 默认收起 + 红色边框 (和帮助页置顶那个框同一套警告色) */
.advbox.sosbox{border-color:var(--bad);border-left:5px solid var(--bad);background:
linear-gradient(135deg,rgba(239,68,68,.10),rgba(255,255,255,.02) 45%)}
html.light .advbox.sosbox{background:linear-gradient(135deg,rgba(194,38,38,.08),rgba(255,255,255,.6) 45%)}
.advbox.sosbox>summary{color:var(--bad)}
.advbox.sosbox[open]>summary{border-bottom-color:rgba(239,68,68,.4)}
.advbox.dirty{box-shadow:0 0 0 1.5px rgba(234,179,8,.55)}
/* 顶栏「进不去服帮助」: 红底白字 */
.chip.sosbtn{background:var(--bad);border-color:var(--bad);color:#fff;font-weight:700;
cursor:pointer;transition:filter .18s,transform .18s}
.chip.sosbtn:hover{filter:brightness(1.14);transform:translateY(-1px)}
html.light .chip.sosbtn{background:var(--bad);border-color:var(--bad);color:#fff}
/* steamcmd 进度条 (初始设置下载 / 服务器设置更新 共用) */
.bar{height:9px;border-radius:999px;background:var(--line);overflow:hidden;margin:12px 0 6px}
.bar i{display:block;height:100%;width:0;border-radius:999px;transition:width .6s;
background:linear-gradient(90deg,#38bdf8,#8b5cf6,#38bdf8);background-size:200% 100%;
animation:flow 2.4s linear infinite}
@keyframes flow{to{background-position:200% 0}}
.dllog{max-height:132px;overflow:auto;background:var(--bg);border:1px solid var(--line);border-radius:9px;
padding:9px 11px;font:11.5px/1.7 Consolas,monospace;color:var(--sub);white-space:pre-wrap;
word-break:break-all;margin:8px 0 0}
/* 难度对照表 */
.difbox{margin-top:8px;border:1px solid var(--line);border-radius:10px;padding:9px 12px;background:var(--bg)}
.difbox summary{cursor:pointer;font-size:13px;color:var(--acc2);font-weight:600;outline:0}
.difft{width:100%;border-collapse:collapse;margin:6px 0 2px;font-size:12.5px}
.difft th,.difft td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line)}
.difft th{color:var(--sub);font-weight:600;white-space:nowrap}
.difft td+td,.difft th+th{white-space:nowrap;font-variant-numeric:tabular-nums}
.difft tbody tr:last-child td{border-bottom:none}
.pin{display:inline-block;margin-left:6px;padding:1px 6px;border-radius:999px;
background:rgba(245,158,11,.14);color:#f59e0b;font-size:11px}
.sep{height:1px;background:var(--line);margin:14px 0}
/* 白天/黑夜切换开关 (Uiverse.io by RiccardoRapelli) */
.switch{position:relative;display:inline-block;width:60px;height:34px;flex:none;cursor:pointer}
.switch #input{opacity:0;width:0;height:0}
.switch .slider{position:absolute;cursor:pointer;top:0;left:0;right:0;bottom:0;
background-color:#2196f3;transition:.4s;z-index:0;overflow:hidden}
.switch #input:checked+.slider{background-color:#0b1120}
.switch #input:focus+.slider{box-shadow:0 0 1px #2196f3}
.sun-moon{position:absolute;height:26px;width:26px;left:4px;bottom:4px;background-color:yellow;
transition:.4s;border-radius:50%;z-index:1}
.switch #input:checked+.slider .sun-moon{transform:translateX(26px);background-color:white;
animation:rotate-center .6s ease-in-out both}
@keyframes rotate-center{0%{transform:translateX(26px) rotate(0)}100%{transform:translateX(26px) rotate(180deg)}}
.moon-dot{opacity:0;transition:.4s;fill:gray}
.switch #input:checked+.slider .sun-moon .moon-dot{opacity:1}
.slider.round{border-radius:34px}
.slider.round .sun-moon{border-radius:50%}
#moon-dot-1{left:10px;top:3px;position:absolute;width:6px;height:6px;z-index:4}
#moon-dot-2{left:2px;top:10px;position:absolute;width:10px;height:10px;z-index:4}
#moon-dot-3{left:16px;top:18px;position:absolute;width:3px;height:3px;z-index:4}
#light-ray-1{left:-8px;top:-8px;position:absolute;width:43px;height:43px;z-index:-1;fill:#fff;opacity:10%}
#light-ray-2{left:-50%;top:-50%;position:absolute;width:55px;height:55px;z-index:-1;fill:#fff;opacity:10%}
#light-ray-3{left:-18px;top:-18px;position:absolute;width:60px;height:60px;z-index:-1;fill:#fff;opacity:10%}
.cloud-light{position:absolute;fill:#eee;animation:cloud-move 6s infinite}
.cloud-dark{position:absolute;fill:#ccc;animation:cloud-move 6s infinite;animation-delay:1s}
#cloud-1{left:30px;top:15px;width:40px}
#cloud-2{left:44px;top:10px;width:20px}
#cloud-3{left:18px;top:24px;width:30px}
#cloud-4{left:36px;top:18px;width:40px}
#cloud-5{left:48px;top:14px;width:20px}
#cloud-6{left:22px;top:26px;width:30px}
@keyframes cloud-move{0%{transform:translateX(0)}40%{transform:translateX(4px)}80%{transform:translateX(-4px)}100%{transform:translateX(0)}}
.stars{transform:translateY(-32px);opacity:0;transition:.4s}
.star{fill:#fff;position:absolute;transition:.4s;animation:star-twinkle 2s infinite}
.switch #input:checked+.slider .stars{transform:translateY(0);opacity:1}
#star-1{width:20px;top:2px;left:3px;animation-delay:.3s}
#star-2{width:6px;top:16px;left:3px}
#star-3{width:12px;top:20px;left:10px;animation-delay:.6s}
#star-4{width:18px;top:0;left:18px;animation-delay:1.3s}
@keyframes star-twinkle{0%{transform:scale(1)}40%{transform:scale(1.2)}80%{transform:scale(.8)}100%{transform:scale(1)}}
/* 玻璃拟态切换组 (单选 pills) */
.pills.glass{--g-bg:rgba(255,255,255,.06);--g-text:#e5e5e5;gap:0;
display:flex;position:relative;background:var(--g-bg);border-radius:1rem;
backdrop-filter:blur(12px);
box-shadow:inset 1px 1px 4px rgba(255,255,255,.2),inset -1px -1px 6px rgba(0,0,0,.3),0 4px 12px rgba(0,0,0,.15);
overflow:hidden;width:fit-content;max-width:100%}
html.light .pills.glass{--g-bg:rgba(0,0,0,.05);--g-text:#4a5462;
box-shadow:inset 1px 1px 3px rgba(255,255,255,.7),inset -1px -1px 5px rgba(0,0,0,.12),0 4px 12px rgba(0,0,0,.08)}
.pills.glass label{flex:1;display:flex;align-items:center;justify-content:center;
min-width:80px;font-size:14px;padding:.55rem 1.15rem;cursor:pointer;font-weight:600;
letter-spacing:.3px;color:var(--g-text);position:relative;z-index:2;transition:color .3s;
white-space:nowrap}
.pills.glass label:hover{color:#fff}
html.light .pills.glass label:hover{color:var(--txt)}
.pills.glass input{display:none}
.pills.glass span{all:unset}
.pills.glass input:checked+label,
.pills.glass input:checked+span,
.pills.glass label:has(input:checked) span,.pills.glass label:has(input:checked){color:#fff !important;text-shadow:0 1px 2px rgba(0,0,0,.4) !important}
.pills.glass .glass-glider{position:absolute;top:0;bottom:0;left:0;border-radius:1rem;z-index:1;
transition:transform .5s cubic-bezier(.37,1.95,.66,.56),background .4s ease-in-out,box-shadow .4s ease-in-out;
background:linear-gradient(135deg,#1e50c8,#3b82f6);
box-shadow:0 0 16px rgba(59,130,246,.55),inset 0 1px 2px rgba(255,255,255,.35)}
/* 头像 */
.logo-img{border-radius:14px;object-fit:cover;flex:none;
box-shadow:0 0 0 2px var(--line),0 4px 16px rgba(59,130,246,.35)}
/* ================ 高端交互升级 ================ */
body::before{content:"";position:fixed;inset:-25%;z-index:-1;pointer-events:none;
background:
 radial-gradient(430px 340px at 18% 28%, rgba(99,102,241,.17), transparent 65%),
 radial-gradient(470px 380px at 82% 16%, rgba(34,211,238,.13), transparent 65%),
 radial-gradient(530px 430px at 60% 88%, rgba(139,92,246,.15), transparent 65%);
animation:aurora 22s ease-in-out infinite alternate}
@keyframes aurora{from{transform:translate3d(-2%,-1%,0) scale(1)}to{transform:translate3d(2%,2%,0) scale(1.08)}}
html.light body::before{opacity:.5}
.layout{animation:pageIn .45s ease both}
@keyframes pageIn{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
.card{animation:cardUp .5s cubic-bezier(.2,.8,.3,1) backwards;
transition:transform .25s cubic-bezier(.2,.8,.3,1),box-shadow .25s,border-color .25s}
@keyframes cardUp{from{opacity:0;transform:translateY(16px)}}
.main .card:nth-child(2){animation-delay:.05s}.main .card:nth-child(3){animation-delay:.1s}
.main .card:nth-child(4){animation-delay:.15s}.main .card:nth-child(5){animation-delay:.2s}
.main .card:nth-child(n+6){animation-delay:.25s}
.card:hover{transform:translateY(-2px);border-color:rgba(99,102,241,.4);
box-shadow:0 16px 44px rgba(0,0,0,.28),0 0 0 1px rgba(99,102,241,.12)}
html.light .card:hover{box-shadow:0 14px 34px rgba(31,45,61,.14)}
.card h2{display:flex;align-items:center;gap:8px}
.card h2::before{content:"";width:4px;height:16px;border-radius:4px;flex:none;
background:linear-gradient(180deg,#60a5fa,#a78bfa);box-shadow:0 0 8px rgba(96,165,250,.6)}
.pagehead h1{background:linear-gradient(92deg,var(--txt),var(--acc2));
-webkit-background-clip:text;background-clip:text;color:transparent}
.nav a{position:relative;transition:.2s}
.nav a:hover{transform:translateX(3px)}
.nav a.on::before{content:"";position:absolute;left:-1px;top:22%;bottom:22%;width:3px;border-radius:3px;
background:linear-gradient(180deg,#60a5fa,#a78bfa);box-shadow:0 0 10px rgba(96,165,250,.9)}
.btn{position:relative;overflow:hidden}
.ink{position:absolute;border-radius:50%;background:rgba(255,255,255,.45);
transform:scale(0);pointer-events:none;animation:inkAn .6s ease-out forwards}
@keyframes inkAn{to{transform:scale(3.4);opacity:0}}
.chip.on{animation:softPulse 2.4s infinite}
@keyframes softPulse{0%,100%{box-shadow:0 0 0 0 rgba(34,197,94,.4)}50%{box-shadow:0 0 0 7px rgba(34,197,94,0)}}
.chip.warnch{color:var(--warn);border-color:var(--warn);background:rgba(234,179,8,.08)}
.dot.on{animation:dotPulse 1.8s infinite}
@keyframes dotPulse{0%,100%{box-shadow:0 0 6px var(--ok)}50%{box-shadow:0 0 16px var(--ok)}}
.addr .big{transition:.4s}
.addr .big.live{background:linear-gradient(90deg,#60a5fa,#a78bfa,#22d3ee,#60a5fa);
background-size:300% 100%;-webkit-background-clip:text;background-clip:text;color:transparent;
animation:shineMove 7s linear infinite;text-shadow:none}
@keyframes shineMove{to{background-position:300% 0}}
.stat{transition:transform .22s,border-color .22s,box-shadow .22s}
.stat:hover{transform:translateY(-3px);border-color:rgba(99,102,241,.45);
box-shadow:0 10px 26px rgba(0,0,0,.22)}
.stat .v.up{animation:numUp .5s cubic-bezier(.2,.9,.3,1.4)}
@keyframes numUp{from{opacity:0;transform:translateY(8px) scale(.94)}}
.trow{transition:background .2s;border-radius:10px}
.trow:hover{background:rgba(255,255,255,.04)}
html.light .trow:hover{background:rgba(0,0,0,.035)}
.pills span:hover{transform:translateY(-1px);border-color:var(--acc)}
.pills span{transition:.18s}
.tmsg{position:relative;overflow:hidden;background:rgba(11,17,32,.94);color:var(--txt);
border:1px solid rgba(99,102,241,.45);border-left:4px solid var(--acc);backdrop-filter:blur(14px);
box-shadow:0 12px 36px rgba(0,0,0,.45)}
html.light .tmsg{background:rgba(255,255,255,.95);border-color:rgba(42,107,198,.4);color:var(--txt)}
.tmsg.err{border-color:rgba(239,68,68,.55);border-left-color:var(--bad)}
.tmsg:before{content:"";position:absolute;left:0;right:0;bottom:0;height:2px;
background:linear-gradient(90deg,var(--acc),transparent);animation:tprog 3.2s linear forwards}
@keyframes tprog{from{transform:scaleX(1)}to{transform:scaleX(0)}}
.mask{position:fixed;inset:0;z-index:200;display:flex;align-items:center;justify-content:center;
padding:20px;background:rgba(2,6,23,.6);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);
animation:maskIn .18s ease}
@keyframes maskIn{from{opacity:0}}
.modal-box{width:450px;max-width:94vw;animation:popIn .3s cubic-bezier(.2,1.5,.4,1);
background:linear-gradient(160deg,rgba(30,41,59,.97),rgba(15,23,42,.98));
border:1px solid rgba(148,163,184,.25);border-radius:18px;padding:24px 26px 20px;
box-shadow:0 30px 80px rgba(0,0,0,.55),0 0 0 1px rgba(99,102,241,.14)}
html.light .modal-box{background:linear-gradient(160deg,rgba(255,255,255,.98),rgba(241,245,249,.98));
border-color:rgba(100,116,139,.3)}
@keyframes popIn{from{opacity:0;transform:scale(.9) translateY(14px)}to{opacity:1;transform:none}}
.modal-box h3{font-size:17px;margin-bottom:8px;display:flex;gap:10px;align-items:center}
.modal-box .mbd{color:var(--sub);font-size:14px;line-height:1.9;margin-bottom:18px}
.modal-box .mbtns{display:flex;gap:10px;justify-content:flex-end;flex-wrap:wrap}
.banner.warn{background:linear-gradient(120deg,rgba(234,179,8,.12),rgba(234,179,8,.05));
display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.banner.info{background:linear-gradient(120deg,rgba(59,130,246,.12),rgba(59,130,246,.05));
border-color:var(--acc);color:var(--acc2);display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.banner.block{display:block;line-height:1.95}
.banner.block b.t{display:block;font-size:15px;margin-bottom:7px;letter-spacing:.3px}
.banner.block p{margin:5px 0 0;padding-left:22px;position:relative}
.banner.block p::before{content:"•";position:absolute;left:6px;top:0}
.banner.block .hl{font-weight:bold;text-decoration:underline}
.sv{border:1px solid var(--line);border-radius:12px;padding:10px 14px 6px;margin-bottom:12px}
.svh{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:4px}
.svh b{font-size:14px}
.svh .sp{flex:1 1 40px;min-width:0}
.sv input.bx{width:16px;height:16px;accent-color:var(--acc);cursor:pointer;vertical-align:middle}
.sv td.old{opacity:.62}
.dirlist{max-height:330px;overflow:auto;border:1px solid var(--line);border-radius:10px;
padding:6px;margin:4px 0 14px;background:rgba(255,255,255,.03)}
.ditem{display:block;width:100%;text-align:left;background:transparent;border:0;color:var(--txt);
padding:8px 10px;border-radius:8px;cursor:pointer;font-size:14px;font-family:inherit}
.ditem:hover{background:rgba(59,130,246,.15);color:var(--acc)}
.dpath{font-size:12px;color:var(--sub);word-break:break-all;margin-bottom:4px}
.modal-box.wide{width:560px}
.modal-box .mbd{white-space:pre-line}
.logo-img{transition:transform .3s,box-shadow .3s}
.logo-img:hover{transform:rotate(-4deg) scale(1.08);
box-shadow:0 0 0 2px var(--acc),0 8px 26px rgba(59,130,246,.5)}
/* 防呆: 改过没保存的卡片描边 + 右下角提示条 */
.card.dirty{border-color:var(--warn);
box-shadow:0 10px 34px rgba(0,0,0,.22),0 0 0 1.5px rgba(234,179,8,.55)}
.card.dirty h2::before{background:linear-gradient(180deg,#fde047,#f59e0b);
box-shadow:0 0 10px rgba(253,224,71,.85)}
#dzpill{position:fixed;right:22px;bottom:22px;z-index:150;cursor:pointer;
display:flex;flex-direction:column;gap:1px;max-width:min(370px,62vw);
background:linear-gradient(140deg,rgba(234,179,8,.17),rgba(15,23,42,.94));
border:1px solid var(--warn);border-left:4px solid var(--warn);border-radius:14px;
padding:11px 16px;color:var(--txt);font-size:12px;line-height:1.65;
box-shadow:0 16px 44px rgba(0,0,0,.5);backdrop-filter:blur(14px);
animation:dzin .3s cubic-bezier(.2,1.5,.4,1)}
#dzpill b{color:var(--warn);font-size:13.5px;letter-spacing:.3px}
#dzpill span{color:var(--sub);word-break:break-all}
#dzpill i{font-style:normal;color:var(--acc2);font-size:11.5px;margin-top:3px}
html.light #dzpill{background:linear-gradient(140deg,rgba(234,179,8,.2),rgba(255,255,255,.96))}
@keyframes dzin{from{opacity:0;transform:translateY(16px)}}

/* 侧边栏: 项目仓库入口 (置于「存档」那一行上方, 只显示图标与名称, 不显示网址) */
.repo{display:flex;align-items:center;justify-content:center;gap:7px;margin:0 0 12px;
padding:7px 11px;border-radius:999px;border:1px solid rgba(255,255,255,.1);
background:linear-gradient(140deg,rgba(139,92,246,.16),rgba(59,130,246,.12));
color:var(--sub);font-size:11.5px;letter-spacing:.3px;text-decoration:none;transition:.24s}
.repo svg{width:15px;height:15px;fill:currentColor;flex:none;transition:.24s}
.repo:hover{color:var(--txt);border-color:var(--acc);transform:translateY(-1px);
box-shadow:0 8px 22px rgba(0,0,0,.32)}
.repo:hover svg{transform:scale(1.14) rotate(-8deg)}
html.light .repo{border-color:rgba(0,0,0,.1);
background:linear-gradient(140deg,rgba(139,92,246,.13),rgba(59,130,246,.09))}

/* 彩蛋: 连点 5 次图标 → 贡献者名单 */
.side .logo{cursor:pointer;user-select:none;-webkit-user-select:none}
.logo-img.tap{animation:tapback .26s ease-out}
@keyframes tapback{40%{transform:scale(.86) rotate(-6deg)}}
.crow{display:flex;align-items:center;gap:11px;padding:9px 12px;margin-top:9px;
border:1px solid rgba(255,255,255,.09);border-radius:12px;background:rgba(148,163,184,.08)}
.crow .cav{width:33px;height:33px;border-radius:10px;flex:none;display:grid;place-items:center;
font-size:16px;color:#fff;background:linear-gradient(135deg,#3b82f6,#8b5cf6);
box-shadow:0 6px 16px rgba(59,130,246,.32)}
.crow b{font-size:14.5px}
.crow i{font-style:normal;display:block;font-size:11px;color:var(--sub);margin-top:1px}
.crow em{font-style:normal;margin-left:auto;font-size:11px;color:var(--acc2);
padding:3px 8px;border-radius:999px;border:1px solid rgba(129,140,248,.35);flex:none}
html.light .crow{border-color:rgba(0,0,0,.07);background:rgba(15,23,42,.045)}
.cthanks{margin-top:14px;font-size:12.5px;line-height:1.8;color:var(--sub);
border-left:3px solid var(--acc2);padding-left:11px}
"""

# 白天/黑夜切换开关 (Uiverse.io by RiccardoRapelli)
THEME_SWITCH = """<label class="switch" title="切换白天 / 黑夜模式">
<input type="checkbox" id="input" onchange="onThemeSwitch(this)">
<span class="slider round">
<span class="sun-moon">
<svg id="moon-dot-1" class="moon-dot" viewBox="0 0 100 100"><circle cx="50" cy="50" r="50"/></svg>
<svg id="moon-dot-2" class="moon-dot" viewBox="0 0 100 100"><circle cx="50" cy="50" r="50"/></svg>
<svg id="moon-dot-3" class="moon-dot" viewBox="0 0 100 100"><circle cx="50" cy="50" r="50"/></svg>
<svg id="light-ray-1" class="light-ray" viewBox="0 0 100 100"><circle cx="50" cy="50" r="50"/></svg>
<svg id="light-ray-2" class="light-ray" viewBox="0 0 100 100"><circle cx="50" cy="50" r="50"/></svg>
<svg id="light-ray-3" class="light-ray" viewBox="0 0 100 100"><circle cx="50" cy="50" r="50"/></svg>
</span>
<span class="stars">
<svg id="star-1" class="star" viewBox="0 0 20 20"><path d="M10 0L12.5 7.5L20 10L12.5 12.5L10 20L7.5 12.5L0 10L7.5 7.5Z"/></svg>
<svg id="star-2" class="star" viewBox="0 0 20 20"><path d="M10 0L12.5 7.5L20 10L12.5 12.5L10 20L7.5 12.5L0 10L7.5 7.5Z"/></svg>
<svg id="star-3" class="star" viewBox="0 0 20 20"><path d="M10 0L12.5 7.5L20 10L12.5 12.5L10 20L7.5 12.5L0 10L7.5 7.5Z"/></svg>
<svg id="star-4" class="star" viewBox="0 0 20 20"><path d="M10 0L12.5 7.5L20 10L12.5 12.5L10 20L7.5 12.5L0 10L7.5 7.5Z"/></svg>
</span>
<svg id="cloud-1" class="cloud-light" viewBox="0 0 40 20"><ellipse cx="13" cy="14" rx="10" ry="5"/><ellipse cx="23" cy="10" rx="9" ry="6"/><ellipse cx="30" cy="15" rx="8" ry="4"/></svg>
<svg id="cloud-2" class="cloud-dark" viewBox="0 0 40 20"><ellipse cx="13" cy="14" rx="10" ry="5"/><ellipse cx="23" cy="10" rx="9" ry="6"/><ellipse cx="30" cy="15" rx="8" ry="4"/></svg>
<svg id="cloud-3" class="cloud-light" viewBox="0 0 40 20"><ellipse cx="13" cy="14" rx="10" ry="5"/><ellipse cx="23" cy="10" rx="9" ry="6"/><ellipse cx="30" cy="15" rx="8" ry="4"/></svg>
<svg id="cloud-4" class="cloud-light" viewBox="0 0 40 20"><ellipse cx="13" cy="14" rx="10" ry="5"/><ellipse cx="23" cy="10" rx="9" ry="6"/><ellipse cx="30" cy="15" rx="8" ry="4"/></svg>
<svg id="cloud-5" class="cloud-dark" viewBox="0 0 40 20"><ellipse cx="13" cy="14" rx="10" ry="5"/><ellipse cx="23" cy="10" rx="9" ry="6"/><ellipse cx="30" cy="15" rx="8" ry="4"/></svg>
<svg id="cloud-6" class="cloud-light" viewBox="0 0 40 20"><ellipse cx="13" cy="14" rx="10" ry="5"/><ellipse cx="23" cy="10" rx="9" ry="6"/><ellipse cx="30" cy="15" rx="8" ry="4"/></svg>
</span>
</label>"""

SHELL_JS = """
var savedTheme=localStorage.getItem('untheme');
if(savedTheme==='light')document.documentElement.classList.add('light');
function syncThemeSwitch(){
 var i=document.getElementById('input');
 if(i)i.checked=!document.documentElement.classList.contains('light');}
function onThemeSwitch(cb){
 var h=document.documentElement;
 h.classList.toggle('light',!cb.checked);
 localStorage.setItem('untheme',cb.checked?'dark':'light');}
function closeManager(){
 modal({icon:'⏻',title:'确定关闭开服器程序吗?',body:'游戏服务器不受影响, 继续在后台运行。',
  okText:'关闭程序',noText:'取消',danger:true}).then(function(ok){
 if(!ok)return;
 post('/api/manager/shutdown').finally(()=>{document.body.innerHTML='<div style="display:flex;align-items:center;justify-content:center;height:100vh;color:#94a3b8;font-size:16px">开服器已关闭, 可以关闭此页面了</div>';});});}
function toast(msg,err){var w=document.getElementById('toast');if(!w)return;var d=document.createElement('div');
d.className='tmsg'+(err?' err':'');d.textContent=msg;w.appendChild(d);
while(w.children.length>5)w.firstChild.remove();
setTimeout(()=>d.remove(),3200);}
function post(url,body){return fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify(body||{})}).then(r=>{
 if(r.status===401){location.href='/login';throw new Error('unauth');}
 return r.json();});}
/* ---- 玻璃拟态弹窗: 返回 Promise(true=确认/false=取消) ---- */
function modal(o){return new Promise(function(res){
 var m=document.createElement('div');m.className='mask';
 var box=document.createElement('div');box.className='modal-box'+(o.wide?' wide':'');
 var h=document.createElement('h3');h.textContent=(o.icon?o.icon+'  ':'')+(o.title||'');
 var bd=document.createElement('div');bd.className='mbd';
 if(o.html){bd.style.whiteSpace='normal';bd.innerHTML=o.html;}
 else bd.textContent=o.body||'';
 var bt=document.createElement('div');bt.className='mbtns';
 var no=document.createElement('button');no.className='btn gray';no.textContent=o.noText||'忽略';
 var okb=document.createElement('button');okb.className='btn'+(o.danger?' red':'');okb.textContent=o.okText||'确定';
 bt.appendChild(no);bt.appendChild(okb);
 box.appendChild(h);box.appendChild(bd);box.appendChild(bt);m.appendChild(box);
 document.body.appendChild(m);
 function done(v){m.remove();res(v);}
 no.onclick=function(){done(false);};
 okb.onclick=function(){done(true);};
 m.addEventListener('click',function(e){if(e.target===m)done(false);});
 document.addEventListener('keydown',function esc(e){
  if(e.key==='Escape'){document.removeEventListener('keydown',esc);done(false);}});
});}
/* ---- 玻璃拟态弹窗(多按钮): 返回 Promise(点击项的 v) ---- */
function pick(o){return new Promise(function(res){
 var m=document.createElement('div');m.className='mask';
 var box=document.createElement('div');box.className='modal-box';
 var h=document.createElement('h3');h.textContent=(o.icon?o.icon+'  ':'')+(o.title||'');
 var bd=document.createElement('div');bd.className='mbd';bd.textContent=o.body||'';
 var bt=document.createElement('div');bt.className='mbtns';
 function done(v){m.remove();res(v);}
 (o.btns||[]).forEach(function(b){
  var el=document.createElement('button');
  el.className='btn'+(b.cls?' '+b.cls:'');el.textContent=b.t;
  el.onclick=function(){done(b.v);};bt.appendChild(el);});
 box.appendChild(h);box.appendChild(bd);box.appendChild(bt);m.appendChild(box);
 document.body.appendChild(m);
 m.addEventListener('click',function(e){if(e.target===m)done(o.cancel||'stay');});
 document.addEventListener('keydown',function esc(e){
  if(e.key==='Escape'){document.removeEventListener('keydown',esc);done(o.cancel||'stay');}});
});}
/* ---- 开服后: 用的是创意工坊地图 → 弹 10 秒资产包提醒 ---- */
function wsNotice(o){
 if(document.querySelector('.mask[data-wsnote]'))return;
 var m=document.createElement('div');m.className='mask';m.setAttribute('data-wsnote','1');
 var box=document.createElement('div');box.className='modal-box wide';
 var h=document.createElement('h3');h.textContent='🧩 本次开的地图来自创意工坊: '+o.map;
 var bd=document.createElement('div');bd.className='mbd';bd.style.whiteSpace='normal';
 function para(t){var p=document.createElement('p');p.style.cssText='margin:0 0 12px';
  p.textContent=t;bd.appendChild(p);}
 para('创意工坊地图里, 有些还要另外下载一份资产包 (也是创意工坊文件)。'+
   '资产包没到位时地图会加载失败, 或者进去是一片空图。');
 para('检查这张地图有没有资产包, 请移步到它所在的创意工坊页面查看; '+
   '有的话一并订阅, 再启动一遍服务器让游戏把文件下载下来。');
 para('另外提醒: 更换地图后, 原先地图和角色存档仍然保存在这台服务器里, '+
   '但之前的建筑和角色数据不能转移到新地图, 相当于开了个新档。'+
   '把地图换回原来那张, 老档内容就还在。');
 var p=document.createElement('p');
 p.style.cssText='margin:0;color:#94a3b8;font-size:12px';
 p.textContent='您当前的服务器存档目录在: '+(o.inst||'(未设置)');
 bd.appendChild(p);
 var bt=document.createElement('div');bt.className='mbtns';
 var a=document.createElement('a');a.className='btn gray';
 a.textContent='🧩 在创意工坊打开这张地图';
 a.href=o.url;a.target='_blank';a.rel='noopener noreferrer';
 a.style.textDecoration='none';
 var okb=document.createElement('button');okb.className='btn';okb.disabled=true;
 okb.style.opacity='.5';okb.style.cursor='not-allowed';
 bt.appendChild(a);bt.appendChild(okb);
 box.appendChild(h);box.appendChild(bd);box.appendChild(bt);m.appendChild(box);
 document.body.appendChild(m);
 var left=10;
 okb.textContent='知道了 ('+left+' 秒)';
 var t=setInterval(function(){left--;
  if(left<=0){clearInterval(t);okb.disabled=false;okb.textContent='知道了';
   okb.style.opacity='';okb.style.cursor='';return;}
  okb.textContent='知道了 ('+left+' 秒)';},1000);
 okb.onclick=function(){if(okb.disabled)return;clearInterval(t);m.remove();};
}
/* ---- 彩蛋: 连点左上角图标 5 次 → 贡献者名单 ---- */
var CREDITS=[
 ['💻','Pippl','Dawn Sharkk 作者 · 界面 / 功能 / 文档','作者'],
 ['🧪','Nmaomao','功能测试','测试'],
 ['🪟','爱情是件奢侈品','Windows 11 适配测试','测试'],
 ['🎨','B站大禹只影','头像设计','头像']];
function showCredits(){
 if(document.querySelector('.mask[data-cred]'))return;
 var m=document.createElement('div');m.className='mask';m.setAttribute('data-cred','1');
 var box=document.createElement('div');box.className='modal-box';
 var h=document.createElement('h3');h.textContent='🦈 Dawn Sharkk 贡献者';
 var bd=document.createElement('div');
 CREDITS.forEach(function(c){
  var r=document.createElement('div');r.className='crow';
  var av=document.createElement('div');av.className='cav';av.textContent=c[0];
  var tx=document.createElement('div');
  var nm=document.createElement('b');nm.textContent=c[1];
  var rl=document.createElement('i');rl.textContent=c[2];
  tx.appendChild(nm);tx.appendChild(rl);
  var bg=document.createElement('em');bg.textContent=c[3];
  r.appendChild(av);r.appendChild(tx);r.appendChild(bg);bd.appendChild(r);});
 var th=document.createElement('div');th.className='cthanks';
 th.textContent='感谢 Nmaomao 与 爱情是件奢侈品 两位测试员的帮助 —— 一个个坑都是他们踩出来的, '+
  'Dawn Sharkk 才有今天这个稳定度。也谢谢 B站大禹只影 为项目画的这张鲨鱼头像。';
 var bt=document.createElement('div');bt.className='mbtns';
 var okb=document.createElement('button');okb.className='btn';okb.textContent='知道啦';
 bt.appendChild(okb);
 box.appendChild(h);box.appendChild(bd);box.appendChild(th);box.appendChild(bt);
 m.appendChild(box);document.body.appendChild(m);
 function done(){m.remove();}
 okb.onclick=done;
 m.addEventListener('click',function(e){if(e.target===m)done();});
 document.addEventListener('keydown',function esc(e){
  if(e.key==='Escape'){document.removeEventListener('keydown',esc);done();}});
}
(function(){
 var n=0,last=0;
 document.addEventListener('click',function(e){
  var nd=e.target&&e.target.closest?e.target.closest('.side .logo'):null;
  if(!nd)return;
  if(window.getSelection)window.getSelection().removeAllRanges();
  var now=Date.now();
  n=(now-last>900)?1:n+1;last=now;
  var img=nd.querySelector('.logo-img');
  if(img){img.classList.remove('tap');void img.offsetWidth;img.classList.add('tap');}
  if(n>=5){n=0;showCredits();}
 },true);
})();
function rocketChoice(title,bodyTxt){return modal({icon:'🚀',title:title,body:bodyTxt,
 okText:'开启 Rocket 前置插件',noText:'忽略'});}
async function doRocketInstall(){
 toast('正在通过 Install Rocket.bat 安装, 约需 30~60 秒…');
 var r=await post('/api/rocket/install');
 toast(r.msg,r.ok?0:1);refreshStatus();return r;}
function openRocket(){
 var s=(lastStatus&&lastStatus.rocket)||{};
 if(s.installed){toast('Rocket 已装好: 必需文件齐 (目录里 '+s.files+' 个文件) —— '
   +'插件 dll 放进存档的 Rocket/Plugins 文件夹即可',0);return;}
 if(!s.bat){toast('游戏目录 Extras 里没有 Install Rocket.bat — 请先在 Steam 校验游戏完整性',1);return;}
 if(!s.src){toast('Extras 里有安装脚本, 但没有 Rocket.Unturned 这个源文件夹 —— '
   +'跑脚本也只会复制 0 个文件。请在 Steam 库右键开服端 → 管理 → 验证游戏文件完整性, 再回来重装',1);return;}
 rocketChoice('如需使用更多功能请开启 Rocket 前置插件',
  '开启 Rocket 前置插件后可加载 .dll 插件、使用 RCON 指令与玩家管理等功能。'+
  '当前状态: '+(s.detail||'没装好')+'。将运行游戏自带 Extras 里的 Install Rocket.bat 完成安装, '+
  '装完重启服务器生效。')
 .then(function(go){if(go)doRocketInstall();else sessionStorage.setItem('rkIgnored','1');});}
var lastStatus=null;
async function refreshStatus(){
 try{var r=await fetch('/api/status');
 if(r.status===401){location.href='/login';return;}
 var d=await r.json();lastStatus=d;
 var chip=document.getElementById('chip');var dot=document.getElementById('dot');
 if(chip){chip.textContent=d.running?('运行中 · PID '+(d.pid>0?d.pid:'外部')):'未运行';
 chip.className='chip '+(d.running?'on':'off');}
 var fs=document.getElementById('foot-st');
 if(fs)fs.textContent=chip?chip.textContent:'';
 if(dot)dot.className='dot'+(d.running?' on':'');
 document.querySelectorAll('[data-show-run]').forEach(e=>e.style.display=d.running?'':'none');
 document.querySelectorAll('[data-show-stop]').forEach(e=>e.style.display=d.running?'none':'');
 var bc=document.getElementById('bigcode');
 if(bc){var idle='暂时没有启动服务器喔，启动服务器后联机代码会提示在这里';
 bc.textContent=d.code?d.code:(d.running?'服务器正在启动, 地图加载到 100% 后代码会出现在这里…':idle);
 bc.classList.toggle('idle',!d.code);
 bc.classList.toggle('live',!!d.code);
 var tip=document.getElementById('codetip');
 if(tip)tip.style.display=d.code?'':'none';
 var lk=document.getElementById('codelink');
 if(lk)lk.style.display=d.code?'':'none';}
 var rk=document.getElementById('rkchip');
 if(rk&&d.rocket){rk.textContent=d.rocket.installed?'🚀 Rocket 已装':'🚀 Rocket 没装好';
  rk.title='Rocket 文件目录: '+(d.rocket.detail||'')+
   (d.rocket.stale&&d.rocket.stale.length?(' (有 '+d.rocket.stale.length+' 个文件与 Extras 自带的大小不一致)'):'');
  rk.className='chip'+(d.rocket.installed?'':' warnch');}
 var rk2=document.getElementById('st-rk');
 if(rk2&&d.rocket){rk2.textContent=d.rocket.installed?'✅ 已装好':'⭕ 没装好';
  rk2.title=d.rocket.detail||'';}
 var st=document.getElementById('st-port');
 if(st&&d.port)st.textContent=d.port;
 var ch=document.getElementById('st-cheat');
 if(ch){var v=d.cheat?'✅ 已开启':'⭕ 未开启';
 if(ch.textContent!==v){ch.textContent=v;ch.classList.remove('up');void ch.offsetWidth;ch.classList.add('up');}}
 var ban=document.getElementById('rkban');
 if(ban&&d.rocket)ban.style.display=(!d.rocket.installed&&!sessionStorage.getItem('rkIgnored'))?'':'none';
 }catch(e){}}
function initGlass(){
 document.querySelectorAll('.pills').forEach(g=>{
  if(g.classList.contains('glass'))return;
  var radios=g.querySelectorAll('input[type=radio]');
  if(!radios.length)return;
  g.classList.add('glass');
  var gl=document.createElement('span');gl.className='glass-glider';g.appendChild(gl);
  var n=radios.length;
  function move(){
   var idx=0;
   radios.forEach((r,i)=>{if(r.checked)idx=i;});
   gl.style.width='calc(100%/'+n+')';
   gl.style.transform='translateX('+(idx*100)+'%)';}
  radios.forEach(r=>r.addEventListener('change',move));
  move();});}
initGlass();
/* 按钮水波纹 */
document.addEventListener('click',function(e){
 var b=e.target.closest('.btn');if(!b||b.disabled)return;
 var rc=b.getBoundingClientRect(),s=Math.max(rc.width,rc.height);
 var i=document.createElement('span');i.className='ink';
 i.style.width=i.style.height=s+'px';
 i.style.left=(e.clientX-rc.left-s/2)+'px';i.style.top=(e.clientY-rc.top-s/2)+'px';
 b.appendChild(i);setTimeout(()=>i.remove(),650);});
/* 开服/关服/重启 按钮 */
document.querySelectorAll('[data-act]').forEach(b=>b.addEventListener('click',async()=>{
 var a=b.dataset.act;
 if(a=='stop'&&!(await modal({icon:'⛔',title:'确定要关闭服务器吗?',
   body:'开服器会先自动保存世界, 再安全关闭。',okText:'关服',noText:'取消',danger:true})))return;
 if(a=='restart'&&!(await modal({icon:'🔄',title:'确定要重启服务器吗?',
   body:'重启前同样会自动保存世界。',okText:'重启',noText:'取消',danger:true})))return;
 if(a=='start'&&lastStatus&&lastStatus.rocket&&!lastStatus.rocket.installed
    &&!sessionStorage.getItem('rkIgnored')){
  var useRk=await rocketChoice('未检测到 Rocket 前置插件',
   '如需使用更多功能请开启 Rocket 前置插件 (加载 .dll 插件、RCON 指令、玩家管理、丰富日志)。'+
   '将通过游戏目录 Extras 文件夹里的 Install Rocket.bat 自动安装, 不影响本次开服。');
  if(useRk)await doRocketInstall();
  else sessionStorage.setItem('rkIgnored','1');}
 b.disabled=true;var r=await post('/api/'+a);b.disabled=false;toast(r.msg,r.ok?0:1);
 if(r&&r.wsmap)wsNotice(r.wsmap);
 setTimeout(refreshStatus,1200);}));
syncThemeSwitch();
refreshStatus();setInterval(refreshStatus,4000);
"""

# 防呆脚本: 只在带 DIRTY_GROUPS 的页面注入 (服务器设置 / 玩法设置)
DIRTY_JS = """
/* ================= 防呆: 设置改过没保存, 切页 / 开服前先问一句 ================= */
(function(){
 var GS=((typeof DIRTY_GROUPS=='undefined')?[]:DIRTY_GROUPS).filter(function(g){
  return g.save&&document.querySelector(g.sel);});
 if(!GS.length)return;
 function each(g,fn){var j=0;
  document.querySelectorAll(g.sel).forEach(function(nd){
   nd.querySelectorAll('input,select,textarea').forEach(function(el){
    if(el.closest('[data-auto]'))return;      /* 勾选即自动保存的项目不算改动 */
    fn(el,j++);});});}
 function snap(g){var m={};each(g,function(el,i){
  m[i]=(el.type=='checkbox'||el.type=='radio')?String(el.checked):el.value;});return m;}
 function restore(g){each(g,function(el,i){
  var v=g.base[i];
  if(el.type=='checkbox'||el.type=='radio')el.checked=(v=='true');
  else el.value=(v===undefined?'':v);});
  if(g.fix)g.fix(); initGlass();}
 GS.forEach(function(g){g.base=snap(g);});
 function changed(g){var m=snap(g),n=0,k;
  for(k in g.base){n++;if(m[k]!==g.base[k])return true;}
  for(k in m){if(g.base[k]===undefined)return true;}
  return n!==Object.keys(m).length;}
 function dirty(){return GS.filter(changed);}
 var pill=document.createElement('div');
 pill.id='dzpill';pill.style.display='none';
 pill.title='点这里跳到第一个没保存的卡片';
 var pb=document.createElement('b'),ps=document.createElement('span'),pi=document.createElement('i');
 pi.textContent='点这里定位 →';pill.appendChild(pb);pill.appendChild(ps);pill.appendChild(pi);
 document.body.appendChild(pill);
 pill.onclick=function(){var g=dirty()[0];if(!g)return;
  document.querySelector(g.sel).scrollIntoView({behavior:'smooth',block:'center'});
  toast('本页「'+g.name+'」改过了, 要点下面的保存才写进配置文件',1);};
 function paint(){
  var d=dirty();
  document.querySelectorAll('[data-grp]').forEach(function(nd){nd.classList.remove('dirty');});
  if(!d.length){pill.style.display='none';return;}
  d.forEach(function(g){document.querySelectorAll(g.sel).forEach(function(nd){
   if(nd.classList.contains('card')||nd.classList.contains('advbox'))nd.classList.add('dirty');});});
  pb.textContent='⚠ 有 '+d.length+' 处修改还没保存';
  ps.textContent=d.map(function(g){return g.name;}).join(' · ');
  pill.style.display='';}
 document.addEventListener('input',paint,true);
 document.addEventListener('change',paint,true);
 window.dirtySaved=function(sel){GS.forEach(function(g){if(g.sel==sel)g.base=snap(g);});paint();};
 window.dirtyCheck=paint;
 var busy=false;
 function target(e){
  var el=e.target&&e.target.closest?e.target.closest('a[href],[data-act]'):null;
  if(!el)return null;
  if(el.tagName=='A'){
   var h=el.getAttribute('href')||'';
   return (h.charAt(0)=='/'&&h!='/api/logout')?{node:el,kind:'nav',href:h}:null;}
  var a=el.getAttribute('data-act');
  return (a=='start'||a=='restart')?{node:el,kind:a}:null;}
 function ask(t,d){
  var names=d.map(function(g){return '「'+g.name+'」';}).join(' ');
  var act=t.kind=='nav'?'切到别的页面':'开服 / 重启服务器';
  return pick({icon:'⚠️',title:'这些设置改了还没保存',
   body:'你改动的 '+names+' 还没点保存, 直接'+act+'的话这些改动不会生效。\\n要先保存吗? (保存后仍需重启服务器才在游戏里生效)',
   btns:[{v:'discard',t:'🗑 取消更改',cls:'gray'},
         {v:'stay',t:'留在本页',cls:'gray'},
         {v:'save',t:'💾 先保存再继续'}]}).then(function(v){
   if(v=='stay')return false;
   if(v=='discard'){d.forEach(restore);paint();return true;}
   return Promise.all(d.map(function(g){
    try{return Promise.resolve(g.save()).catch(function(){return null;});}
    catch(e){return null;}})).then(function(){
     if(dirty().length){toast('还有设置没保存成功, 请按页面上的红色提示处理',1);return false;}
     return true;});});}
 document.addEventListener('click',function(e){
  if(busy)return;
  var t=target(e);if(!t)return;
  var d=dirty();if(!d.length)return;
  e.preventDefault();e.stopPropagation();
  busy=true;
  ask(t,d).then(function(go){busy=false;if(!go)return;
   if(t.kind=='nav')location.href=t.href;else t.node.click();});
 },true);
 window.addEventListener('beforeunload',function(e){
  if(!dirty().length)return;
  e.preventDefault();e.returnValue='';});
 paint();
})();
"""


def check_html(checked=False, onchange="", ident="", label="", cls="", style=""):
    """玻璃质感勾选框 (Uiverse.io 模板), cls="ck-lg" 为大号"""
    attrs = ((f' id="{ident}"' if ident else "") + (" checked" if checked else "")
             + (f" {onchange}" if onchange else ""))
    lbl = f'<span class="lbl">{label}</span>' if label else ""
    st = f' style="{style}"' if style else ""
    return (f'<label class="container {cls}"{st}>'
            f'<input type="checkbox"{attrs}>'
            f'<span class="checkmark"></span>{lbl}</label>')


def shell(page, title, content, script="", dirty=False):
    IC = {
        "dash": '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
        "quick": '<path d="M13 2L5 13h6l-1.5 9L19 9.5h-6.2L13 2z"/>',
        "cmds": '<rect x="2.5" y="7" width="19" height="10" rx="5"/><path d="M7 12h4M9 10v4"/><circle cx="15.5" cy="11" r="0.6"/><circle cx="17.5" cy="13" r="0.6"/>',
        "game": '<path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="2.2"/><circle cx="15" cy="17" r="2.2"/>',
        "ws": '<path d="M21 8l-9-5-9 5v8l9 5 9-5V8z"/><path d="M3 8l9 5 9-5M12 13v8"/>',
        "save": ('<ellipse cx="12" cy="5.5" rx="8" ry="2.8"/>'
                 '<path d="M4 5.5v5.6c0 1.6 3.6 2.8 8 2.8s8-1.2 8-2.8V5.5"/>'
                 '<path d="M4 11.1v5.6c0 1.6 3.6 2.8 8 2.8s8-1.2 8-2.8v-5.6"/>'),
        "term": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9l3 3-3 3M13 15h4"/>',
        "log": '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9l-6-6z"/><path d="M14 3v6h6M9 13h6M9 17h4"/>',
        "files": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"/>',
        "help": '<circle cx="12" cy="12" r="9"/><path d="M9.6 9.2a2.5 2.5 0 1 1 3.6 2.2c-.8.4-1.2 1-1.2 1.9"/><line x1="12" y1="16.6" x2="12" y2="16.7"/>',
    }
    nav = [
        ("dash", "仪表盘", "/"),
        ("quick", "一键设置", "/quick"),
        ("cmds", "服务器设置", "/commands"),
        ("game", "玩法设置", "/gameplay"),
        ("ws", "创意工坊", "/workshop"),
        ("save", "存档管理", "/saves"),
        ("term", "控制台", "/console"),
        ("log", "操作日志", "/logs"),
        ("files", "文件管理", "/files"),
        ("help", "帮助", "/help"),
    ]
    links = "".join(
        f'<a href="{href}" class="{"on" if key == page else ""}">'
        f'<span class="ico"><svg viewBox="0 0 24 24">{IC[key]}</svg></span>{name}</a>'
        for key, name, href in nav)
    foot = (f'<a class="repo" href="{esc(REPO_URL)}" target="_blank" rel="noopener noreferrer"'
            f' title="在项目页面看源码 / 提 issue / 求 star">{GH_ICON}<span>项目仓库地址</span></a>'
            f'存档: <b>{esc(instance())}</b> · <a href="/setup">切换/重设</a>'
            f' · <a href="/api/logout">退出登录</a><br>'
            f'<a href="#" onclick="closeManager();return false">关闭开服器程序</a><br>'
            f'<span class="dot" id="dot"></span><span id="foot-st">检测中…</span>')
    sos_btn = ("" if page == "help" else
               '<a class="chip sosbtn" href="/help" title="进不去服务器 / 游戏弹报错: '
               '帮助页置顶那四条对照着解决">🚨 进不去服帮助</a>')
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="/logo.png">
<title>{title} · Dawn Sharkk</title>
<script>try{{var t=new URLSearchParams(location.search).get('theme')||localStorage.getItem('untheme');
if(t==='light')document.documentElement.classList.add('light');
if(t)localStorage.setItem('untheme',t);}}catch(e){{}}</script>
<style>{CSS}</style></head><body>
<div class="layout">
<aside class="side">
  <div class="logo"><img class="logo-img" src="/logo.png" style="width:58px;height:58px" alt="Dawn Sharkk">
  <div><b>Dawn Sharkk</b>
  <div class="sub">Unturned 开服器 {VER_LABEL} · by Pippl</div></div></div>
  <nav class="nav">{links}</nav>
  <div class="foot">{foot}</div>
</aside>
<main class="main">
<div id="toast"></div>
<div class="pagehead"><h1>{title}</h1>
<span class="chip" id="chip">检测中…</span>
<span class="chip" id="rkchip" onclick="openRocket()" title="Rocket 前置插件状态, 点击查看/开启" style="cursor:pointer">🚀 检测中…</span>
{sos_btn}
<span style="flex:1"></span>
<button class="btn green" data-act="start" data-show-stop>▶ 开服</button>
<button class="btn gray" data-act="restart" data-show-run>🔄 重启</button>
<button class="btn red" data-act="stop" data-show-run>⛔ 关服</button>
{THEME_SWITCH}
</div>
{content}
</main></div>
<script>{SHELL_JS}
{script}
{DIRTY_JS if dirty else ""}</script></body></html>"""


# ================================================================ 仪表盘
def page_dash():
    info = cmd_info()
    cheat_on = cheat_enabled()
    check_rows = ""
    for t in TOGGLES:
        if t["id"] not in DASH_TOGGLE_IDS:
            continue
        on = all(get_cfg(config_txt(), s, k) == v for s, k, v in t["keys"])
        chk = check_html(on, onchange='onchange="dashTog(\'%s\',this)"' % t["id"],
                         ident="d_" + t["id"], cls="ck-lg")
        check_rows += f"""
<div class="trow" style="padding:9px 4px">
{chk}<div style="flex:1;min-width:0"><b style="font-size:14px">{t['icon']} {t['name']}</b>
<div class="ds" style="font-size:12px">{t['desc']}</div></div></div>"""
    cheat_check = check_html(cheat_on, onchange='onchange="setCheat(this)"',
                             ident="d_cheat", cls="ck-lg")
    content = f"""
<div class="banner warn" id="rkban" style="display:none">🚀 Rocket 没装好 —— 网页发指令 / 刷新玩家列表 / 安全关服都会失败, 服务器也只是在裸跑
<span style="flex:1"></span>
<button class="btn sm" onclick="doRocketInstall()">立即开启</button>
<button class="btn sm gray" onclick="ignoreRocket()">忽略</button></div>
<div class="card addr">
  <button class="btn sm gray rfbtn" id="rfbtn" onclick="refreshCode(this)"
   title="立即刷新服务器状态与代码"><span class="ric">↻</span>刷新</button>
  <div class="lb">服 务 器 代 码 ( P2P 直 连 )</div>
  <div class="big idle" id="bigcode">暂时没有启动服务器喔，启动服务器后联机代码会提示在这里</div>
  <div class="tip" id="codetip">开服后把这段 <b>服务器代码</b> 发给你的朋友 → 游戏内按 <b>Play → 输入服务器代码</b> 即可加入, 无需端口映射</div>
  <div style="margin-top:18px">
    <button class="button" onclick="copyCode()" id="codelink" style="display:none"><span class="hoverEffect"><div></div></span><span class="txt">复制服务器代码</span></button>
  </div>
  <div class="hint" style="margin-top:14px">本服走 Steam P2P, 只提供<b>服务器代码</b>联机 (IP+端口直连需要官方服务器和 Steam 服务器 Key, 本工具不适用)</div>
</div>
<div class="grid g3">
  <div class="stat"><div class="k">服务器名称</div><div class="v">{esc(info['name'])}</div></div>
  <div class="stat"><div class="k">地图 · 难度</div><div class="v">{esc(info['map'])} · {esc(info['mode'])}</div></div>
  <div class="stat"><div class="k">人数上限</div><div class="v">{esc(info['maxplayers'])}</div></div>
  <div class="stat"><div class="k">端口</div><div class="v" id="st-port">{esc(info['port'])}</div></div>
  <div class="stat"><div class="k">作弊选项 (cheats on)</div><div class="v" id="st-cheat">{'✅ 已开启' if cheat_on else '⭕ 未开启'}</div></div>
  <div class="stat"><div class="k">Rocket 前置</div><div class="v" id="st-rk">检测中…</div></div>
</div>
<div class="card" style="margin-top:14px"><h2>默认开服选项</h2>
<div class="desc">下面每一项都会<b>立即应用到当前服务器</b> (重启生效), 并自动保存为<b>以后新建服务器的默认设置</b>。</div>
<div class="trow" style="border-bottom:1px solid rgba(255,255,255,.06)">
{cheat_check}<div style="flex:1;min-width:0"><b style="font-size:14px">🎮 默认开启作弊</b>
<div class="ds">往 Commands.dat 写入 <code class="k">cheats on</code> — check/give/vehicle/teleport/god 等管理指令可用;
新建服务器时默认自带。建议只在和朋友联机时开启。</div></div></div>
<div class="grid g2">{check_rows}</div>
</div>
<div class="grid g2">
  <div class="card"><h2>新手推荐</h2>
    <div class="desc">不想研究复杂配置? 到「一键设置」页, 点开关就能调好服务器</div>
    <div style="margin-top:10px"><a class="btn" href="/quick">打开一键设置 →</a></div></div>
  <div class="card"><h2>服务器控制台</h2>
    <div class="desc">查看运行日志和报错, 输入命令管理服务器</div>
    <div style="margin-top:10px"><a class="btn gray" href="/console">打开控制台 →</a></div></div>
</div>
<div class="card"><h2>全部配置文件</h2>
<div class="desc">Config.txt / Commands.dat / Rocket 插件配置等, 都可以在线修改 (自动保存原编码, 中文不乱码)</div>
<div style="margin-top:10px"><a class="btn gray" href="/files">打开文件管理 →</a></div></div>
<div class="card"><h2>萌新必读</h2>
<div class="desc" style="line-height:2">
· 修改任何配置前请先 <b>关服</b>, 改完再开服, 否则修改会被覆盖<br>
· 点「关服」按钮会先自动保存世界再安全关闭, 请放心使用<br>
· 服务器代码每次开服都会变化, 所以只在服务器运行时显示; 而且<b>一定要地图加载到 100% 之后才会出现</b>,
刚点开服时看到「服务器正在启动」是正常现象, 过十几秒再刷新即可 (右上角「↻ 刷新」可立刻重新拉取)<br>
· 建议定期去 <b>存档管理</b> 备份建筑存档 / 玩家存档; 在那里删存档会先强制备份, 删的只是文件不是文件夹<br>
· 「一键设置」里有 <b>无需指南针/GPS/手绘地图也能看方向和地图</b> 三个开关, 萌新不再迷路;
服务器 <b>图标与大厅链接</b> 在「服务器设置」页最底下 (新建存档已自动带上 Dawn Sharkk 默认图标)<br>
· 以后往 <code class="k">Rocket\\Plugins</code> 里放插件, 它的配置文件会自动出现在文件管理里<br>
· 朋友进不去服务器? <b>「帮助」页最上方有一张置顶的「🚨 进不去服务器」对照表</b>
(连接超时 / Steam 经济验证 / 战眼未开 / 其他报错), 让他对着自己的提示词照着做 → <a href="/help">立即查看</a>
</div></div>
"""
    script = """
function copyCode(){navigator.clipboard.writeText(document.getElementById('bigcode').textContent)
 .then(()=>toast('✔ 服务器代码已复制, 发给朋友即可加入'));}
async function refreshCode(btn){btn.disabled=true;btn.classList.add('spin');
 await refreshStatus();
 setTimeout(function(){btn.disabled=false;btn.classList.remove('spin');},600);
 var d=lastStatus||{};
 if(d.running&&d.code)toast('服务器代码: '+d.code);
 else if(d.running)toast('服务器还在启动中: 必须等地图加载到 100% 才显示本次服务器代码, 过几秒再刷新');
 else toast('服务器未运行, 开服后联机代码会显示在这里');}
async function setCheat(el){el.disabled=true;
 var r=await post('/api/cheat',{on:el.checked});toast(r.msg,r.ok?0:1);
 if(!r.ok)el.checked=!el.checked;el.disabled=false;}
async function dashTog(id,el){el.disabled=true;
 var r=await post('/api/toggle',{id:id,on:el.checked,as_default:true});
 toast(r.msg,r.ok?0:1);if(!r.ok)el.checked=!el.checked;el.disabled=false;}
function ignoreRocket(){sessionStorage.setItem('rkIgnored','1');
 var b=document.getElementById('rkban');if(b)b.style.display='none';
 toast('已忽略 — 随时点右上角 🚀 徽章开启 Rocket');}
"""
    return shell("dash", "仪表盘", content, script)


# ================================================================ 一键设置
def page_quick():
    rows = []
    for t in TOGGLES:
        on = all(get_cfg(config_txt(), s, k) == v for s, k, v in t["keys"])
        keys = "".join(f'<code class="k">{k}</code>' for _, k, _ in t["keys"])
        chk = check_html(on, onchange='onchange="toggle(\'%s\',this)"' % t["id"],
                         ident="sw_" + t["id"], cls="ck-lg")
        rows.append(f"""
<div class="trow"><div class="bar"></div>
<div><div class="nm">{t['name']}</div><div class="ds">{t['desc']}</div>
<div class="st {'on' if on else 'off'}" id="st_{t['id']}">当前: {'✅ 已开启' if on else '⭕ 未开启 (默认)'}</div>
<div style="margin-top:5px">{keys}</div></div>
<div class="rgt">{chk}</div></div>""")

    sel_rows = []
    for s in SELECTS:
        cur = get_cfg(config_txt(), s["section"], s["key"]) or ""
        pills = ""
        for val, name in s["opts"]:
            on = "checked" if cur == val else ""
            pills += (f'<label><input type="radio" name="sel_{s["id"]}" value="{val}" {on} '
                      f'onchange="setsel(\'{s["id"]}\',\'{val}\',this)"><span>{name}</span></label>')
        sel_rows.append(f"""
<div class="trow"><div class="bar"></div>
<div><div class="nm">{s['name']}</div><div class="ds">{s['desc']}</div>
<div style="margin-top:4px"><code class="k">{s['key']}</code></div></div>
<div class="rgt"><div class="pills">{pills}</div></div></div>""")

    rz = rocket_zh_status()
    rz_now = "✅ 已汉化 (指令反馈)" if rz["on"] else "⭕ 当前是英文原文"
    if rz["missing"]:
        rz_now = "⭕ 存档里还没有 Rocket 翻译文件 (需先装 Rocket 前置并开一次服)"
    rz_check = check_html(rz["on"], onchange='onchange="rkZh(this)"',
                          ident="sw_rkzh", cls="ck-lg")
    rk_zh_card = f"""
<div class="card"><h2>🚀 Rocket 前置 · 指令反馈汉化</h2>
<div class="desc">注意: <b>这不是汉化控制台</b>, 服务器日志和控制台输出不受影响。
只汉化<b>玩家或管理员敲完指令之后 Rocket 回的那一句话</b> (例如 /tp、/give、/god 的提示,
以及玩家进服、权限不足、插件加载完成之类的反馈)。
开启后会把存档 <code class="k">Rocket\\</code> 里的
<code class="k">Rocket.en.translation.xml</code> 与
<code class="k">Rocket.Unturned.en.translation.xml</code> 覆盖为中文版 (共 95 条文案,
原文会自动保留在文件结构里), 关掉开关即可逐字还原成英文。改完重启服务器生效。</div>
<div class="trow"><div class="bar"></div>
<div><div class="nm">汉化 Rocket 指令反馈</div>
<div class="ds">只改反馈文字, 不动指令本身、不动控制台、不影响插件功能</div>
<div class="st {'on' if rz['on'] else 'off'}" id="st_rkzh">当前: {rz_now}</div></div>
<div class="rgt">{rz_check}</div></div></div>"""

    content = f"""
<div class="card"><h2>一键开关</h2>
<div class="desc">点一下开关就生效并自动保存到 Config.txt, 关掉开关恢复游戏默认。改完记得重启服务器。</div>
{''.join(rows)}</div>
<div class="card"><h2>常用强度</h2>
<div class="desc">选一个想要的档位即可, 自动写入配置。</div>
{''.join(sel_rows)}</div>
{rk_zh_card}
<div class="card"><h2>更多精细调节</h2>
<div class="desc">想调血量、刷车率、技能消耗等更多参数? 去「玩法设置」页, 每一项都有中文说明。</div>
<div style="margin-top:10px"><a class="btn gray" href="/gameplay">打开玩法设置 →</a></div></div>
"""
    script = """
async function toggle(id,el){
 el.disabled=true;
 var r=await post('/api/toggle',{id:id,on:el.checked});
 toast(r.msg,r.ok?0:1);
 if(!r.ok)el.checked=!el.checked;
 else{var st=document.getElementById('st_'+id);
 st.textContent='当前: '+(el.checked?'✅ 已开启':'⭕ 未开启 (默认)');
 st.className='st '+(el.checked?'on':'off');}
 el.disabled=false;}
async function setsel(id,val,el){
 var r=await post('/api/select',{id:id,value:val==='OFF'?null:val});
 toast(r.msg,r.ok?0:1);if(!r.ok)el.checked=false;}
async function rkZh(el){
 el.disabled=true;
 var r=await post('/api/rocketzh',{on:el.checked});
 el.disabled=false;
 if(!r.ok){el.checked=!el.checked;toast(r.msg,1);return;}
 var st=document.getElementById('st_rkzh');
 st.textContent='当前: '+(el.checked?'✅ 已汉化 (指令反馈)':'⭕ 当前是英文原文');
 st.className='st '+(el.checked?'on':'off');
 toast(r.msg,0);}
"""
    return shell("quick", "一键设置", content, script)


# ================================================================ 服务器设置 (Commands.dat)
def page_commands():
    if not os.path.isfile(commands_path()):
        try:
            ensure_instance_files(instance())
        except OSError:
            pass
    lc = get_launch_cfg()
    close_stop = bool(_settings.get("close_bat_stops_server"))
    rocket_ok = rocket_installed()
    ver_card = dedi_version_card()
    rocket_note = ("· " + rocket_check()["detail"]) if rocket_ok else \
        ("· " + rocket_check()["detail"] + " —— 游戏不会报错, 只是静默不加载 Rocket")
    text, _ = read_file(commands_path())
    entries = parse_commands(text)
    known = {k for k, *_ in CMD_FIELDS} | {"pvp", "pve", "cheat", "cheats"}
    vals = {}
    for e in entries:
        if e["key"] and e["key"].lower() in known:
            vals[e["key"].lower()] = e["value"]
    cur_pvpve = "pvp" if "pvp" in vals else ("pve" if "pve" in vals else "")

    def pills(key, options, cur=None, onchange=""):
        if cur is None:
            cur = vals.get(key, "")
        p = ""
        for v, label in options:
            on = "checked" if cur == v else ""
            p += (f'<label><input type="radio" name="p_{key}" value="{v}" {on}{onchange}>'
                  f'<span>{label}</span></label>')
        return p

    mode_cur = (vals.get("mode") or "normal").strip().lower()
    if mode_cur not in MODE_COLS:
        mode_cur = "normal"
    mode_pills = pills("mode", [("easy", "🟢 简单"), ("normal", "🟡 普通"), ("hard", "🔴 困难")],
                       cur=mode_cur, onchange=' onchange="askMode(this)"')
    pinned = difficulty_overrides()
    diff_full = difficulty_rows_html(pinned=pinned)
    mode_diff = {}
    for a in MODE_COLS:
        for b in MODE_COLS:
            if a != b:
                mode_diff[f"{a}>{b}"] = {
                    "from": MODE_LABELS[a], "to": MODE_LABELS[b],
                    "table": difficulty_rows_html(pinned=pinned, only=(a, b))}
    persp_pills = pills("perspective", [("first", "仅第一人称"), ("third", "仅第三人称"),
                                        ("both", "都可以 (推荐)"), ("vehicle", "载具内第三人称")])
    pvpve = pills("pvpve", [("pvp", "⚔️ PVP 玩家对战"), ("pve", "🧟 PVE 只打僵尸")])

    cur_map = (vals.get("map") or "").strip()
    detected = list_maps()
    ws_list = workshop_maps()
    ws_ids = {e["map"].lower(): e["id"] for e in ws_list}
    unknown_map = bool(cur_map) and cur_map.lower() not in {m.lower() for m in detected}
    # 存档里写的地图探测不到 (创意工坊还没下载 / 手填的名字): 也要原样显示, 绝不能假装是 PEI
    maps = ([cur_map] + detected) if unknown_map else detected
    map_opts = '<option value="">— 选择地图 —</option>' + "".join(
        f'<option value="{esc(m)}" data-ws="{ws_ids.get(m.lower(), "")}"'
        f' {"selected" if cur_map.lower() == m.lower() else ""}>'
        f'{esc(m)}'
        + (f' · 🧩 创意工坊 {ws_ids[m.lower()]}' if m.lower() in ws_ids
           else ' (探测不到, 请核对拼写)' if unknown_map and m == cur_map else '')
        + '</option>'
        for m in maps)
    ws_hint0 = ("已识别到 " + str(len(ws_list)) + " 张: " + "、".join(e["map"] for e in ws_list)
                if ws_list else
                "本存档还没下载好任何创意工坊地图 (填完 ID 要把服务器启动一遍, 游戏才会去下)")
    map_state = ("Commands.dat 当前写入的地图: " + cur_map) if cur_map \
        else "还没设置地图 (游戏会用默认的 PEI)"
    others = [e for e in entries if e["key"] and e["key"].lower() not in known
              and e["key"].lower() != "pvpve"]
    other_html = ""
    if others:
        rows = "".join(
            f'<div><label class="f"><b>{esc(e["key"])}</b><code class="k">{esc(e["key"])}</code></label>'
            f'<input class="f" data-key="{esc(e["key"])}" value="{esc(e["value"])}"></div>'
            for e in others)
        other_html = (f'<div class="card" data-grp="cmd"><h2>其他配置项 (自动识别)</h2>'
                      f'<div class="grid g3">{rows}</div></div>')

    try:
        apply_browser_defaults()          # 没设置过的存档自动补上默认图标与链接
    except OSError:
        pass
    bi = browser_cfg()
    has_br = bool(bi["icon"] or bi["thumb"] or bi["links"])
    cur_links = esc("\n".join(f"{m} | {u}" for m, u in bi["links"]))
    def_links = "&#10;".join(f"{m} | {u}" for m, u in BROWSER_DEFAULTS["links"])
    br_state = ("✅ 已设置 (图标 " + ("有" if bi["icon"] else "无")
                + " · 链接 " + str(len(bi["links"])) + " 条)") if has_br \
        else "⭕ 未设置 (游戏默认, 大厅不显示图标)"
    browser_card = f"""
<div class="card" data-grp="br"><h2>🖼️ 服务器图标与大厅链接 (图床设置)</h2>
<div class="desc">这几项写在当前存档 <code class="k">Config.txt</code> 的 <code class="k">Browser</code> 段
(本页其他项目改的是 Commands.dat, 只有这一块写 Config.txt, 两者互不影响):
<code class="k">Icon</code> = 玩家进服大厅左上角的图标(建议 64x64),
<code class="k">Thumbnail</code> = 服务器列表里的小图(建议 32x32),
<code class="k">Links</code> = 大厅里玩家能点的按钮 (群地址、GitHub 地址等)。
<b>图片必须是能直接访问的图片直链</b> (以 .jpg / .png 结尾的图床地址), 由玩家客户端自行下载;
留空 = 用游戏默认 (不显示图标)。<b>新建存档会自动填入 Dawn Sharkk 默认图标与 GitHub 链接</b>,
已有的存档只会在<b>第一次</b>打开时自动补齐一次, 之后你自己改过或清掉的值不会再被覆盖。
改完重启服务器生效。</div>
<div id="brcard" data-icon="{esc(BROWSER_DEFAULTS['icon'])}" data-thumb="{esc(BROWSER_DEFAULTS['thumb'])}" data-links="{def_links}">
<label class="f"><b>图标 Icon</b><code class="k">Browser/Icon</code></label>
<input class="f" id="bi_icon" value="{esc(bi['icon'])}" placeholder="https://.../icon-64x64.png">
<label class="f"><b>缩略图 Thumbnail</b><code class="k">Browser/Thumbnail</code></label>
<input class="f" id="bi_thumb" value="{esc(bi['thumb'])}" placeholder="https://.../thumb-32x32.png">
<label class="f"><b>大厅链接 Links</b><code class="k">Browser/Links</code>
<span style="font-size:12px"> 每行一条: 显示文字 | 网址 (最多 8 条)</span></label>
<textarea class="f" id="bi_links" rows="3">{cur_links}</textarea>
<div class="st {'on' if has_br else 'off'}" id="st_br">当前: {br_state}</div>
<div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap">
<button class="btn big" onclick="saveBr()">💾 保存并写入 Config.txt</button>
<button class="btn gray" onclick="fillBrDef()">↺ 填入 Dawn Sharkk 默认</button>
<button class="btn gray" onclick="clearBr()">🗑 清空输入框</button></div>
<div class="hint" style="margin-top:8px">「清空输入框」只是把框里的字清掉, 要点「保存」才会真的写进配置
(保存时留空 = 恢复游戏默认, 大厅不再显示图标)。</div>
</div></div>"""

    vac_on = anticheat_on("vac")
    be_on = anticheat_on("be")
    ac_card = f"""
<div class="card" data-grp="ac"><h2>🛡️ 反作弊 (VAC / BattlEye 战眼)</h2>
<div class="desc">这两项写在当前存档 <code class="k">Config.txt</code> 的 <code class="k">Server</code> 段,
<b>默认都是开启的</b> —— 开着的时候作弊玩家进服会被拦。
<b>没有特殊原因不要关闭</b>: 关掉的后果由服务器上所有人一起承担。</div>
<div style="margin-top:12px;display:flex;flex-direction:column;gap:12px">
<div style="display:flex;gap:14px;align-items:center;flex-wrap:wrap">
{check_html(vac_on, onchange='onchange="acToggle(this)"', ident="ac_vac", cls="ck-lg")}
<span><b>VAC 反作弊</b> <code class="k">Server/VAC_Secure</code> — Steam 官方的反作弊, 建议一直开着</span></div>
<div style="display:flex;gap:14px;align-items:center;flex-wrap:wrap">
{check_html(be_on, onchange='onchange="acToggle(this)"', ident="ac_be", cls="ck-lg")}
<span><b>BattlEye (战眼) 反作弊</b> <code class="k">Server/BattlEye_Secure</code> — 游戏自带的服务端反作弊;
朋友报「未开启 BattlEye 战眼」进不来时, 看「帮助」页置顶第 ③ 条</span></div></div>
<div style="margin-top:14px;display:flex;gap:10px;align-items:center;flex-wrap:wrap">
<button class="btn big" onclick="saveAc()">💾 保存反作弊设置</button>
<span class="hint" style="margin:0">这块的保存按钮是独立的 · 改完要重启服务器才生效</span></div>
<div class="hint">取消勾选 = 关掉这一层反作弊。关掉之后外挂进服<b>不会被拦</b>,
别人辛辛苦苦盖的建筑和攒的进度<b>可能被乱改乱拆</b>; 只有在排查误封、
或者朋友实在连不进来时才临时关一下, 处理完记得开回来。</div>
</div>"""

    content = f"""
<div class="card" data-grp="cmd"><h2>基础信息</h2>
<div class="grid g2">
<div><label class="f"><b>服务器名称</b><code class="k">name</code></label>
<input class="f" data-key="name" id="i_name" value="{esc(vals.get('name',''))}"></div>
<div><label class="f"><b>地图</b><code class="k">Map &lt;地图名&gt;</code></label>
<select class="f" id="i_map" onchange="showMap()">{map_opts}</select>
<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:8px">
<button class="btn sm gray" id="wsbtn" onclick="scanWs()">🧩 识别本存档的创意工坊地图</button>
<span class="hint" id="wshint" style="margin:0">{ws_hint0}</span></div>
<label class="f" style="font-size:12px">列表里没有你要的地图? 在这里照原样填 (填了就优先生效):</label>
<input class="f" id="i_map_custom" placeholder="手动输入地图名" oninput="showMap()">
<div class="st on" id="st_map">{map_state}</div>
<div class="hint">下拉框读的是 <code class="k">U3DS\\Maps</code>、存档 <code class="k">Level</code>
和已下载的<b>创意工坊地图</b>里真实存在的地图名 (大小写照原样)。
<b>创意工坊地图要先在「创意工坊」页填 ID, 并把服务器启动一遍</b>让游戏把地图下载下来,
回来点上面那个按钮才识别得到。<b>换地图 = 换一张全新的世界</b>:
角色与建筑存档都不会跟过去 (相当于开新档), 老地图的存档仍然保留在
<code class="k">Level\\旧地图名</code> 里, 名字改回去就能看到。</div></div>
</div>
<div class="grid g2">
<div><label class="f"><b>最大玩家数</b><code class="k">maxplayers</code></label>
<input class="f" data-key="maxplayers" type="number" value="{esc(vals.get('maxplayers','24'))}"></div>
<div><label class="f"><b>端口</b><code class="k">port</code></label>
<input class="f" data-key="port" type="number" value="{esc(vals.get('port','26010'))}">
<div class="hint">多个存档同时开服时端口不能相同</div></div>
</div></div>

<div class="card" data-grp="cmd"><h2>游戏方式</h2>
<div><label class="f"><b>对战模式</b><code class="k">PVP / PVE</code></label>
<div class="pills">{pvpve}</div></div>
<div><label class="f"><b>游戏难度</b><code class="k">mode</code></label>
<div class="pills">{mode_pills}</div>
<details class="difbox"><summary>📊 三档难度到底差在哪 (点开看全部 {len(DIFF_ROWS)} 项)</summary>
<div class="hint">下面是游戏自己的原版默认值。<b>换难度 = 整套换掉这些默认值</b>,
不是只换个名字。橙色标记的项表示你在「玩法设置」里单独填过数值,
那一项<b>以你填的为准</b>, 换难度不会再动它。</div>
{diff_full}</details></div>
<div><label class="f"><b>允许的视角</b><code class="k">perspective</code></label>
<div class="pills">{persp_pills}</div></div>
<div><label class="f"><b>开启作弊</b><code class="k">cheats on</code></label>
<div style="display:flex;gap:14px;align-items:center">
{check_html(cheat_enabled(), onchange='data-auto="1" onchange="setCheat(this)"', ident="i_cheat", cls="ck-lg")}
<span class="hint" style="margin:0">勾选 = 往 Commands.dat 写入 <code class="k">cheats on</code>, check/give/teleport 等管理指令可用; 与仪表盘「默认开服选项」保持同步</span></div></div>
<div><label class="f"><b>进服欢迎语</b><code class="k">welcome</code></label>
<input class="f" data-key="welcome" value="{esc(vals.get('welcome',''))}"></div>
<div><label class="f"><b>出生装备</b><code class="k">loadout</code></label>
<input class="f" data-key="loadout" value="{esc(vals.get('loadout',''))}">
<div class="hint">物品ID用 / 分隔, 例如 255/4/50193/1176 (255、4 表示默认服装槽)</div></div>
<div><label class="f"><b>进入密码</b><code class="k">password</code></label>
<input class="f" data-key="password" value="{esc(vals.get('password',''))}">
<div class="hint">留空 = 任何人都能进。公网开服建议设置密码。</div></div>
<div><label class="f"><b>服主 SteamID</b><code class="k">owner</code></label>
<input class="f" data-key="owner" value="{esc(vals.get('owner',''))}"></div>
</div>
{ac_card}
{other_html}
<details class="advbox sosbox" id="launchbox" data-grp="launch">
<summary>⚙️ 启动参数<em>不是开发人员不要动这一块 · 默认收起, 要点开才看得见</em></summary>
<div class="advbody">
<div style="border:1px solid var(--bad);border-radius:12px;padding:11px 14px;margin:6px 6px 13px;
background:rgba(239,68,68,.10);color:var(--bad);font-size:13.5px;line-height:1.9">
<b>启动参数不是开发人员请不要修改其他内容!</b> 这一栏里的选项会<b>原样拼进开服命令行</b>,
改错了服务器可能<b>直接开不起来</b>、<b>朋友进不来</b>、或者<b>网页看不到运行日志</b>。
看不懂是干什么用的框, 就保持现在这个样子别动 —— 日常开服联机用不到这里。</div>
<div class="desc">启动命令固定为原版方式: <code class="k">-nographics -batchmode +Secureserver/存档名</code>。
改完下面的选项点保存并重启服务器生效。</div>
<div class="tip">萌新联机只需要用<b>服务器代码</b> (仪表盘正中间那串数字): 代码联机走 Steam P2P,
<b>不需要</b> <code class="k">Login_Token</code>、不需要端口映射。不填 Login_Token 只影响"互联网服务器列表
/ 公网 IP 直连"这两种方式, 代码联机不受任何影响。</div>
<div style="margin-top:12px;display:flex;gap:16px 24px;flex-wrap:wrap;align-items:center">
{check_html(lc['console'], ident="l_console", cls="ck-lg")}<span><b>原版控制台窗口</b> (推荐) — 服务器有自己独立的黑色控制台窗口, 关闭开服器/网页后<b>服务器继续运行</b></span>
</div>
<div style="margin-top:10px;display:flex;gap:16px 24px;flex-wrap:wrap;align-items:center">
{check_html(lc['batchmode'], ident="l_batch", cls="ck-lg")}<span><b>-batchmode</b> 无窗口后台运行 (推荐, 默认勾选)</span>
{check_html(lc['nographics'], ident="l_nographics", cls="ck-lg")}<span><b>-nographics</b> 不加载显卡渲染 (推荐, 默认勾选)</span>
</div>
<div class="hint">"原版控制台窗口"关闭时: 服务器完全后台静默运行, 网页「运行日志」会更完整(实时接管输出)。</div>
<div style="margin-top:12px;display:flex;gap:16px 24px;flex-wrap:wrap;align-items:center">
{check_html(close_stop, ident="l_closestop", cls="ck-lg")}<span><b>关闭 bat 窗口时同时关闭服务器</b> (默认关闭 — 关闭后服务器会一直驻留, 直到手动关服)</span>
</div>
<label class="f"><b>自定义附加参数</b><code class="k">extra</code></label>
<input class="f" id="l_extra" value="{esc(lc['extra'])}" placeholder="用空格分隔, 不确定就不要填">
<div class="hint">Unturned / Unity 支持的其他命令行参数写在这里, 例如 <code class="k">-LogConsole</code>。参数会原样追加到启动命令后。</div>
<div style="margin-top:12px"><button class="btn" onclick="saveLaunch()">保存启动参数</button>
<span class="hint" style="margin-left:10px">重启服务器后生效 · 这一块的保存按钮和上面的「保存修改」是分开的</span></div>
</div></details>
{ver_card}
<div class="card"><h2>Rocket 插件框架</h2>
<div class="desc">Rocket 让服务器可以加载插件(.dll), 网页的控制台指令也靠它。当前状态:
<b style="color:{'var(--ok)' if rocket_ok else 'var(--bad)'}">{'已装好' if rocket_ok else '没装好'}</b>
{rocket_note}</div>
<div class="hint" style="margin-top:6px">如果这里显示没装好, 网页的「发送命令 / 刷新玩家列表 / 安全关服」会全部失败,
而游戏不会报错 —— 它只是静默地不加载 Rocket。</div>
<div style="margin-top:10px;display:flex;gap:10px;flex-wrap:wrap">
<button class="btn" onclick="installRocket()">{'重新安装 / 更新' if rocket_ok else '一键安装 Rocket'}</button>
<button class="btn gray" onclick="openPlugins()">打开本存档的插件文件夹</button>
</div>
<div class="hint">安装来源: 游戏目录 <code class="k">Extras\\Install Rocket.bat</code> (由 Steam 校验提供)。
安装后把插件 <code class="k">.dll</code> 放进 <code class="k">Servers\\{esc(instance())}\\Rocket\\Plugins</code>,
插件配置文件会自动出现在「文件管理」里。</div>
</div>
<div class="card">
<button class="btn big" onclick="save()">💾 保存修改</button>
<a class="btn big gray" href="/edit?path=Server/Commands.dat">原始编辑器</a>
<span class="hint" style="margin-left:12px">保存后需要重启服务器才会生效 —— 改过没保存就切页或开服, 会弹框提醒并帮你直接保存</span></div>
{browser_card}
"""
    script = DL_CORE_JS + "\nvar MODE_CUR=" + json.dumps(mode_cur) + ";" + \
        "var MODE_DIFF=" + json.dumps(mode_diff, ensure_ascii=False) + ";" + \
        "var MAP_BASE=" + json.dumps(detected, ensure_ascii=False) + ";" + \
        "var WS_MAPS=" + json.dumps(ws_list, ensure_ascii=False) + ";" + \
        "var MAP_CUR=" + json.dumps(cur_map, ensure_ascii=False) + ";" + \
        "var INST_DIR=" + json.dumps(server_dir() or "", ensure_ascii=False) + ";" + """
function esc1(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){
 return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
function mapVal(){
 var c=document.getElementById('i_map_custom').value.trim();
 return c||document.getElementById('i_map').value.trim();}
function mapOpt(v,label,ws){var e=document.createElement('option');e.value=v;
 if(ws)e.dataset.ws=ws;e.textContent=label;return e;}
function renderMapSel(){
 var sel=document.getElementById('i_map'),cur=mapVal();
 sel.innerHTML='';
 sel.appendChild(mapOpt('','— 选择地图 —'));
 MAP_BASE.forEach(function(m){sel.appendChild(mapOpt(m,m));});
 WS_MAPS.forEach(function(m){sel.appendChild(mapOpt(m.map,m.map+' · 🧩 创意工坊 '+m.id,m.id));});
 var hit=cur&&[].some.call(sel.options,function(o){
  return o.value&&o.value.toLowerCase()==cur.toLowerCase();});
 if(hit)sel.value=cur;
 else if(cur)document.getElementById('i_map_custom').value=cur;   /* 探测不到的名字留在手填框里, 别丢 */
 showMap();}
async function scanWs(){
 var b=document.getElementById('wsbtn');b.disabled=true;
 var r=null;
 try{r=await post('/api/maps/workshop');}catch(e){r={ok:false,msg:'请求失败: '+e};}
 finally{b.disabled=false;}
 if(!r||!r.ok){toast((r&&r.msg)||'识别失败',1);return;}
 WS_MAPS=r.maps||[];
 renderMapSel();
 document.getElementById('wshint').textContent=WS_MAPS.length?
  ('已识别到 '+WS_MAPS.length+' 张: '+WS_MAPS.map(function(m){return m.map;}).join('、')+
   ' · 本存档共订阅 '+r.subscribed+' 个创意工坊内容'):r.msg;
 toast(WS_MAPS.length?('识别到 '+WS_MAPS.length+' 张创意工坊地图, 已经放进下拉框'):
  '没识别到创意工坊地图: 填完 ID 要先启动一遍服务器, 游戏才会去下地图');}
function mapChangeOk(mp){
 var ws=WS_MAPS.filter(function(m){return m.map.toLowerCase()==mp.toLowerCase();})[0];
 return modal({icon:'🗺️',title:'换成地图 '+mp+' 之前, 先看这几句',wide:true,danger:true,
  okText:'我懂了, 换地图',noText:'先不换',
  html:'换地图<b>相当于开一个新档</b>: 你的角色数据和建筑都不会跟到新地图里去。'+
   '<br><br>· 老档<b>不会丢</b> —— 原地图的建筑仍然在 <code>Level\\\\'+esc1(MAP_CUR||'旧地图名')+
   '</code> 里, 角色数据也还在,<b>把地图名换回去就能接着玩</b>。'+
   '<br>· 您当前的服务器存档目录在: <code>'+esc1(INST_DIR)+'</code>'+
   (ws?('<br>· 这次要换的是<b>创意工坊地图</b> '+esc1(ws.map)+' (模组 ID '+ws.id+
        '), 本存档必须已经把它下载好了; 还没下载就先去「创意工坊」页填 ID, 再启动一遍服务器。'):'')+
   '<br><br>改完记得<b>重启服务器</b>才生效 (改之前先关服, 否则服务器退出时会把旧配置写回去)。'});}
function showMap(){
 var v=mapVal(),st=document.getElementById('st_map');
 st.textContent=v?('保存后将写入 Commands.dat: Map '+v):'⚠ 地图不能为空, 请选一个或手动填一个';
 st.className='st '+(v?'on':'off');}
async function save(){
 var kv={};var bad='';var LB={maxplayers:'最大玩家数',port:'端口'};
 document.querySelectorAll('input.f[data-key]').forEach(el=>{
  var v=el.value.trim();
  if(el.dataset.key==='maxplayers'||el.dataset.key==='port'){
   if(!/^[0-9]+$/.test(v))bad=(LB[el.dataset.key]||el.dataset.key)+' 要填数字, 不能留空';}
  kv[el.dataset.key]=v;});
 var mp=mapVal();
 if(!mp){toast('地图不能为空: 请从下拉框选一个地图, 或在下面手动填写地图名',1);return;}
 if(MAP_CUR&&mp.toLowerCase()!==MAP_CUR.toLowerCase()&&!(await mapChangeOk(mp)))return;
 kv.map=mp;
 var mode=document.querySelector('input[name=p_mode]:checked');
 if(mode)kv.mode=mode.value;
 var pv=document.querySelector('input[name=p_pvpve]:checked');
 if(pv)kv[pv.value]='';
 var pe=document.querySelector('input[name=p_perspective]:checked');
 if(pe)kv.perspective=pe.value;
 if(bad){toast('✘ '+bad,1);return;}
 return post('/api/commands',{kv:kv}).then(r=>{
  if(!r.ok){toast('✘ '+(r.msg||'保存失败'),1);return;}
  var m=document.querySelector('input[name=p_mode]:checked');if(m)MODE_CUR=m.value;
  if(window.dirtySaved)dirtySaved('[data-grp=cmd]');
  toast(r.warn?('⚠ 已保存, 但'+r.warn):'✔ 已保存! 重启服务器后生效',r.warn?1:0);});}
async function saveLaunch(){
 var r=await post('/api/launch',{
  batchmode:document.getElementById('l_batch').checked,
  nographics:document.getElementById('l_nographics').checked,
  console:document.getElementById('l_console').checked,
  close_stop:document.getElementById('l_closestop').checked,
  extra:document.getElementById('l_extra').value.trim()});
 if(r.ok&&window.dirtySaved)dirtySaved('[data-grp=launch]');
 toast(r.msg,r.ok?0:1);}
async function setCheat(el){el.disabled=true;
 var r=await post('/api/cheat',{on:el.checked});toast(r.msg,r.ok?0:1);
 if(!r.ok)el.checked=!el.checked;el.disabled=false;}
/* ---- 反作弊: 取消勾选先弹一次确认; 这一块有自己独立的保存按钮 ---- */
async function acToggle(el){
 if(el.checked)return;
 var nm=el.id=='ac_be'?'BattlEye (战眼) 反作弊':'VAC 反作弊';
 var go=await modal({icon:'⚠️',title:'要关闭 '+nm+' 吗?',danger:true,
  okText:'✔ 确定关闭',noText:'↩ 还是开着',
  html:'<b>关闭反作弊可能不安全, 不建议这么做。</b><br><br>'+
   '· 关掉以后用外挂的玩家进服<b>不会被拦</b>, 你和其他玩家盖的建筑、攒的进度都可能被乱改乱拆。<br>'+
   '· 只有<b>排查误封</b>, 或者朋友报「未开启 BattlEye 战眼」连不进来时才临时关一下, 弄完记得开回来。<br>'+
   '· 现在还没写进配置 —— 要点这块的「保存反作弊设置」, 再<b>重启服务器</b>才生效。<br><br>确定吗?'});
 if(!go){el.checked=true;if(window.dirtyCheck)dirtyCheck();}}
async function saveAc(){
 var r=await post('/api/anticheat',{
  vac:document.getElementById('ac_vac').checked,
  be:document.getElementById('ac_be').checked});
 toast(r.msg,r.ok?0:1);
 if(r.ok&&window.dirtySaved)dirtySaved('[data-grp=ac]');}
async function installRocket(){
 if(!(await modal({icon:'🚀',title:'安装 / 更新 Rocket',
  body:'将运行游戏自带 Extras 里的 Install Rocket.bat。安装完成后重启服务器生效。'})))return;
 var r=await doRocketInstall();if(r.ok)setTimeout(()=>location.reload(),1500);}
function openPlugins(){post('/api/rocket/open');}
function brVal(id){return document.getElementById(id).value.trim();}
async function saveBr(){
 var i=brVal('bi_icon'), t=brVal('bi_thumb'), l=document.getElementById('bi_links').value;
 if(i&&!/^https?:\/\//i.test(i)){toast('图标 Icon 必须是 http:// 或 https:// 开头的图片直链',1);return;}
 if(t&&!/^https?:\/\//i.test(t)){toast('缩略图 Thumbnail 必须是 http:// 或 https:// 开头的图片直链',1);return;}
 var r=await post('/api/browser',{icon:i,thumb:t,links:l});
 toast(r.msg,r.ok?0:1);
 if(r.ok){if(window.dirtySaved)dirtySaved('[data-grp=br]');
  var st=document.getElementById('st_br');
 st.textContent='当前: '+(i||t||r.links?'✅ 已设置 (图标 '+(i?'有':'无')+' · 链接 '+r.links+' 条)':'⭕ 未设置 (游戏默认, 大厅不显示图标)');
 st.className='st '+(i||t||r.links?'on':'off');}}
function fillBrDef(){var c=document.getElementById('brcard'), d=c.dataset;
 document.getElementById('bi_icon').value=d.icon;
 document.getElementById('bi_thumb').value=d.thumb;
 document.getElementById('bi_links').value=d.links;
 if(window.dirtyCheck)dirtyCheck();
 toast('已填入 Dawn Sharkk 默认图标与链接, 记得点保存');}
function clearBr(){['bi_icon','bi_thumb','bi_links'].forEach(function(id){
 document.getElementById(id).value='';});
 if(window.dirtyCheck)dirtyCheck();
 toast('输入框已清空 — 点保存即恢复游戏默认(不显示图标)');}
/* ---- 换难度: 把这一档与上一档的数值差异摆到眼前再确认 ---- */
function modeRadio(m){return document.querySelector('input[name=p_mode][value="'+m+'"]');}
async function askMode(el){
 var to=el.value, from=MODE_CUR;
 if(to===from)return;
 var d=MODE_DIFF[from+'>'+to];
 if(!d){MODE_CUR=to;return;}
 var note='以上是游戏原版的默认值, 换难度不会重建世界, 也不会动你已有的存档建筑。'+
          '带橙色标记的项你已经在「玩法设置」里单独填过, 那一项仍然以你填的为准。'+
          '选完记得点「保存修改」并重启服务器。';
 var go=await modal({icon:'⚖️',title:(d.from+' → '+d.to)+': 会改变这些数值',
  html:d.table+'<div class="hint" style="margin-top:12px;line-height:1.8">'+note+'</div>',
  okText:'✔ 就换成这个',noText:'↩ 不换回去了',wide:true});
 if(go){MODE_CUR=to;toast('难度改成了'+d.to+', 点下面的「保存修改」才会真的写进 Commands.dat');}
 else{var back=modeRadio(from);if(back)back.checked=true;}
 if(window.dirtyCheck)dirtyCheck();}
/* 进度恢复: 页面重开/刷新时, steamcmd 还在跑就接着显示进度条 */
(function(){dlGet().then(function(s){
 if(!s||!s.state)return;
 if(s.state==='running'){dlVis('dlbar','block');dlDraw(s);dlPoll();}
 else if(s.state==='error'||s.state==='cancelled')dlDraw(s);});})();
/* 防呆登记: 这四块各有自己的保存按钮 */
var DIRTY_GROUPS=[
 {sel:'[data-grp=cmd]',name:'基础信息与游戏方式',save:save,fix:showMap},
 {sel:'[data-grp=ac]',name:'反作弊设置',save:saveAc},
 {sel:'[data-grp=launch]',name:'启动参数',save:saveLaunch},
 {sel:'[data-grp=br]',name:'图标与大厅链接',save:saveBr}];
/* 启动参数那一栏: 记住开合 (和「高级配置」一样, 默认收起) */
(function(){var b=document.getElementById('launchbox');if(!b)return;
 try{if(localStorage.getItem('ds_launchg')==='1')b.open=true;}catch(e){}
 b.addEventListener('toggle',function(){
  try{localStorage.setItem('ds_launchg',b.open?'1':'0');}catch(e){}});})();
"""
    return shell("cmds", "服务器设置", content, script, dirty=True)


# ================================================================ 玩法设置 (Config.txt 汉化表单)
def page_gameplay():
    sections_html = ""
    for sec, sec_cn, fields in GAMEPLAY_FIELDS:
        rows = ""
        for key, label, typ, hint, spec, detail in fields:
            sp = CFG_SPECS[f"{sec}|{key}"]
            fid = f"g_{sec}|{key}"
            val = get_cfg(config_txt(), sec, key)
            note = spec_text(sp)
            note_html = f'<div class="sp">{esc(note)}</div>' if note else ""
            det_html = f'<div class="det">{esc(detail)}</div>' if detail else ""
            if typ == "bool":
                cur_true = val is not None and val.lower() == "true"
                cur_false = val is not None and val.lower() == "false"
                inp = f"""<div class="pills">
<label><input type="radio" name="{fid}" value="True" {'checked' if cur_true else ''}><span>开启</span></label>
<label><input type="radio" name="{fid}" value="False" {'checked' if cur_false else ''}><span>关闭</span></label>
<label><input type="radio" name="{fid}" value="" {'checked' if val is None else ''}><span>默认</span></label>
</div>"""
            else:
                attrs = ""
                if sp["lo"] is not None:
                    attrs += f' data-lo="{num_out(sp["lo"])}"'
                if sp["hi"] is not None:
                    attrs += f' data-hi="{num_out(sp["hi"])}"'
                inp = (f'<input class="f" type="text" id="{esc(fid)}"{attrs} placeholder="留空 = 游戏默认"'
                       f' value="{esc(val or "")}" oninput="gpCheck(this)">'
                       f'<div class="gperr" id="m_{esc(fid)}"></div>')
            rows += (f'<div><label class="f"><b>{label}</b><code class="k">{sec}/{key}</code></label>'
                     f'{inp}{note_html}{det_html}</div>')
        sections_html += f"""
<div class="card" data-grp="game"><h2>{sec_cn} <code class="k">{sec}</code></h2>
<div class="grid g3">{rows}</div></div>"""

    adv_groups, adv_total = cfg_adv_rows()
    adv_cards = ""
    for sec, sec_cn, arows in adv_groups:
        arows_html = ""
        for r in arows:
            fid, typ = f"a_{r['dk']}", r["type"]
            cur, hint = r["cur"], r["hint"]
            label, key = r["label"], r["key"]
            tx = " ".join([label, key, sec, sec_cn, hint]).lower().replace('"', "'")
            tag = ('<span class="pin">这一笔存档里还没写过</span>' if r["untouched"] else "")
            if typ == "bool":
                low = cur.lower()
                inp = f"""<div class="pills">
<label><input type="radio" name="{esc(fid)}" value="True" {'checked' if low == 'true' else ''}><span>开启</span></label>
<label><input type="radio" name="{esc(fid)}" value="False" {'checked' if low == 'false' else ''}><span>关闭</span></label>
<label><input type="radio" name="{esc(fid)}" value="" {'checked' if low not in ('true', 'false') else ''}><span>默认</span></label>
</div>"""
            else:
                inp = (f'<input class="f" type="text" id="{esc(fid)}" data-t="{esc(typ)}"'
                       f' placeholder="留空 = 游戏默认" value="{esc(cur)}" oninput="advCheck(this)">'
                       f'<div class="gperr" id="m_{esc(fid)}"></div>')
            arows_html += (f'<div class="advrow" data-tx="{esc(tx)}"><label class="f"><b>{label}</b>'
                           f'<code class="k">{sec}/{key}</code></label>{tag}{inp}'
                           f'<div class="sp">{esc(ADV_TYPE_CN[typ])} · {esc(hint)}</div></div>')
        adv_cards += f"""
<div class="card advsec" data-grp="gameadv"><h2>{sec_cn} <code class="k">{sec}</code></h2>
<div class="grid g3">{arows_html}</div></div>"""

    adv_box = f"""
<details class="advbox" id="advbox"><summary>🔧 高级配置 — 另外 {adv_total} 项 Config.txt 设置
<em>默认收起，不点开就当它不存在</em></summary>
<div class="advbody">
<div class="hint" style="margin:2px 6px 12px;padding:11px 14px;border:1px solid var(--line);
border-left:3px solid var(--warn);border-radius:10px;background:var(--bg);font-size:12.5px;line-height:1.9">
上面那批是把大家最常调的挑出来做的表单，这一批是<b>官方维基里剩下的那些 Config.txt 设置项</b>：
僵尸首领掉落、天气频率、爆炸击退、服务器防刷屏之类。官方<b>没有公布这些项的安全取值范围</b>，
所以开服器只挡住「一定会写坏文件」的填法 (负数、非数字、概率大于 1、含大括号或注释符号)，
剩下的一律照你填的写。<b>不知道填什么的就留空</b> —— 留空 = 这一项交回游戏自己决定；
填了字再清空并保存，才会把已经写进去的这一笔删回默认。
登录凭据、官方服务器、Steam 测试分支这类涉及账号安全的项<b>有意没放进来</b>。
<b>这块的保存按钮和上面是分开的</b>，填完记得点最底下那个「保存高级配置」。</div>
<div class="advbar">
<input class="f advfilter" type="text" id="advq" placeholder="🔍 关键词筛选: 中文或英文都行, 比如 僵尸 / Respawn / 掉落 / Vehicles" oninput="advFilter()">
<span class="hint" id="advcnt"></span></div>
{adv_cards}
<div class="card"><button class="btn big" onclick="saveAdv()">💾 保存高级配置</button>
<span class="hint" style="margin-left:12px">只写这一批 (上面「保存全部修改」管不到它)，重启服务器后生效</span></div>
</div></details>"""

    content = f"""
<div class="card"><h2>玩法参数精细调节</h2>
<div class="desc">每一项都会写入当前存档的 <code class="k">Config.txt</code>。
每个框下面都写了<b>这一项能填多大、游戏原版默认是多少、以及 0 和 1 分别代表什么</b>。
数字留空或选「默认」= 交回给游戏自己决定 (此时会跟着「服务器设置」里的游戏难度走);
填了数字就以你填的为准, 换难度也不会再改这一项。改完点下面的保存并重启服务器。
常用的开关建议先去「一键设置」页搞定。</div>
<div class="tip">不确定就别填: 填错方向不会毁存档, 点「默认」再保存就能退回原版行为。
要找的设置这一页没有? 最底下还有个默认收起的「🔧 高级配置」, 收着另外 {adv_total} 项 Config.txt 设置, 不点开就当它不存在。</div></div>
{sections_html}
<div class="card">
<button class="btn big" onclick="save()">💾 保存全部修改</button>
<a class="btn big gray" href="/edit?path=Config.txt">原始编辑器</a>
<span class="hint" style="margin-left:12px">改过没保存就切页或开服, 会弹框提醒并帮你直接保存</span></div>
{adv_box}
"""
    script = """
function gpCheck(el){
 var msg=document.getElementById('m_'+el.id), v=el.value.trim();
 el.style.borderColor='';if(msg)msg.textContent='';
 if(v==='')return true;
 if(isNaN(Number(v))){el.style.borderColor='var(--bad)';
  if(msg)msg.textContent='✘ 这一项要填数字, 现在填的看不懂';return false;}
 var n=Number(v),lo=el.dataset.lo,hi=el.dataset.hi,txt='';
 if(lo!==undefined&&n<Number(lo))txt='最小是 '+lo;
 if(hi!==undefined&&n>Number(hi))txt=(txt?txt+'、':'')+'最大是 '+hi;
 if(txt){el.style.borderColor='var(--bad)';
  if(msg)msg.textContent='✘ 超出范围: '+txt+' (你填了 '+n+')';return false;}
 return true;}
async function save(){
 var kv={},bad=null;
 document.querySelectorAll('input[type=radio][name^=g_]:checked').forEach(el=>{
  var p=el.name.slice(2).split('|');
  kv[p[0]+'|'+p[1]]=el.value===''?null:el.value;});
 document.querySelectorAll('input.f[id^=g_]').forEach(el=>{
  if(!gpCheck(el)&&!bad)bad=el;
  var p=el.id.slice(2).split('|');
  kv[p[0]+'|'+p[1]]=el.value.trim()===''?null:el.value.trim();});
 if(bad){bad.focus();toast('有数值超出范围, 已经帮你定位到那一格 (看框下面的红字)',1);return;}
 var r=await post('/api/gameplay',{kv:kv});
 if(r.ok&&window.dirtySaved)dirtySaved('[data-grp=game]');
 if(!r.ok&&(r.items||[]).length){
  r.items.forEach(function(k){var el=document.getElementById('g_'+k);if(el)gpCheck(el);});
  var first=document.getElementById('g_'+r.items[0]);if(first)first.focus();}
 toast(r.ok?'✔ 已保存! 重启服务器后生效 (共 '+r.count+' 项)':'✘ '+r.msg,r.ok?0:1);}
/* ---- 高级配置: 默认收起的那一批, 有自己独立的保存按钮 ---- */
function advShow(){var b=document.getElementById('advbox');if(b)b.open=true;}
function advCheck(el){
 var msg=document.getElementById('m_'+el.id), v=el.value.trim(), t=el.dataset.t||'num';
 el.style.borderColor='';if(msg)msg.textContent='';
 if(v==='')return true;
 if(t==='text'){
  if(v.length>200){el.style.borderColor='var(--bad)';
   if(msg)msg.textContent='✘ 太长了, 最多 200 字 (现在 '+v.length+' 字)';return false;}
  if(/[{}\[\]]/.test(v)||v.indexOf('//')>=0){el.style.borderColor='var(--bad)';
   if(msg)msg.textContent='✘ 不能有括号或 // —— 会把 Config.txt 的段落结构写坏';return false;}
  return true;}
 var n=Number(v);
 if(isNaN(n)||!isFinite(n)){el.style.borderColor='var(--bad)';
  if(msg)msg.textContent='✘ 这一项要填数字, 现在填的看不懂';return false;}
 if(n<0){el.style.borderColor='var(--bad)';
  if(msg)msg.textContent='✘ 这一项不能是负数 (你填了 '+n+')';return false;}
 if(t==='count'&&n!==Math.floor(n)){el.style.borderColor='var(--bad)';
  if(msg)msg.textContent='✘ 这一项只能填整数 (你填了 '+n+')';return false;}
 if(t==='pct'&&n>1){el.style.borderColor='var(--bad)';
  if(msg)msg.textContent='✘ 这一项是概率, 只能填 0 ~ 1 (你填了 '+n+')';return false;}
 return true;}
function advCount(){var el=document.getElementById('advcnt');if(!el)return;
 var all=document.querySelectorAll('.advrow'), n=0;
 all.forEach(function(r){if(!r.classList.contains('advhide'))n++;});
 var q=document.getElementById('advq');
 el.textContent=(q&&q.value.trim())?('筛出 '+n+' / '+all.length+' 项'):('一共 '+all.length+' 项');}
function advFilter(){
 var q=(document.getElementById('advq').value||'').trim().toLowerCase();
 document.querySelectorAll('.advsec').forEach(function(card){
  var hit=0;
  card.querySelectorAll('.advrow').forEach(function(r){
   var ok=!q||(r.dataset.tx||'').indexOf(q)>=0;
   r.classList.toggle('advhide',!ok);if(ok)hit++;});
  card.classList.toggle('advhide',hit===0);});
 advCount();}
async function saveAdv(){
 var kv={},bad=null,errs=0;
 document.querySelectorAll('input[type=radio][name^=a_]:checked').forEach(el=>{
  var p=el.name.slice(2).split('|');
  kv[p[0]+'|'+p[1]]=el.value===''?null:el.value;});
 document.querySelectorAll('input.f[id^=a_]').forEach(el=>{
  if(!advCheck(el)){errs++;if(!bad)bad=el;}
  var p=el.id.slice(2).split('|');
  kv[p[0]+'|'+p[1]]=el.value.trim()===''?null:el.value.trim();});
 if(bad){advShow();bad.focus();
  toast('高级配置里有 '+errs+' 项填得不对, 已帮你定位到第一格 (看框下面的红字)',1);return;}
 var r=await post('/api/gameplay/advanced',{kv:kv});
 if(r.ok&&window.dirtySaved)dirtySaved('[data-grp=gameadv]');
 if(!r.ok&&(r.items||[]).length){
  advShow();
  r.items.forEach(function(k){var el=document.getElementById('a_'+k);
   if(el&&el.tagName==='INPUT')advCheck(el);});
  var first=document.getElementById('a_'+r.items[0]);if(first)first.focus();}
 toast(r.ok?'✔ 高级配置已保存! '+(r.count?('重启服务器后生效 (共 '+r.count+' 项)')
   :'这一页没有需要写的改动 (填的值和游戏/文件里的一样)'):'✘ '+r.msg,r.ok?0:1);}
/* 展开状态记在浏览器里: 点开过一次, 下次进这一页还是开着的 */
(function(){var b=document.getElementById('advbox');if(!b)return;
 try{if(localStorage.getItem('ds_advg')==='1')b.open=true;}catch(e){}
 b.addEventListener('toggle',function(){try{
  localStorage.setItem('ds_advg',b.open?'1':'0');}catch(e){}});
 advCount();})();
/* 防呆登记: 常规那一批和高级那一批各有各的保存按钮 */
var DIRTY_GROUPS=[{sel:'[data-grp=game]',name:'玩法参数',save:save},
 {sel:'[data-grp=gameadv]',name:'高级配置',save:saveAdv}];
"""
    return shell("game", "玩法设置", content, script, dirty=True)


# ================================================================ 创意工坊
WS_INFO_API = "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/"
WS_COLL_API = "https://api.steampowered.com/ISteamRemoteStorage/GetCollectionDetails/v1/"
_ws_cache = {}
_ws_lock = threading.Lock()


def ws_extract_ids(text):
    """从创意工坊网址或混排文本里提取模组 ID (网址里的 ?id=数字 或纯数字)"""
    s = re.sub(r"id\s*=\s*", " ", text or "", flags=re.I)
    out, seen = [], set()
    for m in re.finditer(r"\d{6,}", s):
        v = m.group(0)
        if v not in seen:
            seen.add(v)
            out.append(int(v))
    return out


def _steam_post(url, payload, timeout=8):
    req = urllib.request.Request(
        url, data=urlencode(payload).encode(), method="POST",
        headers={"User-Agent": "DawnSharkk", "Content-Type":
                 "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _ws_date(v):
    try:
        return time.strftime("%Y-%m-%d", time.localtime(int(v)))
    except Exception:
        return "—"


def _ws_fetch(ids):
    """批量查询模组详情, 返回 ({id: 条目}, 错误文本)"""
    out, err = {}, ""
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        payload = {f"publishedfileids[{n}]": pid for n, pid in enumerate(chunk)}
        payload["itemcount"] = len(chunk)
        payload["details"] = 1
        try:
            data = _steam_post(WS_INFO_API, payload)
        except urllib.error.HTTPError as e:
            err = f"Steam 接口返回状态 {e.code} (可能是短时间内查询过多), 稍等几秒再试"
            continue
        except Exception as e:
            err = f"连不上 Steam 接口 ({type(e).__name__}), 请检查网络后重试"
            continue
        for d in (data.get("response") or {}).get("publishedfiledetails") or []:
            try:
                pid = int(d.get("publishedfileid"))
            except (TypeError, ValueError):
                continue
            ok = d.get("result") in (None, 1)
            out[pid] = {
                "id": pid,
                "title": d.get("title") or ("已删除/不可见" if not ok else "未知名称"),
                "updated": _ws_date(d.get("time_updated")),
                "created": _ws_date(d.get("time_created")),
                "dead": not ok,
                "url": f"https://steamcommunity.com/sharedfiles/filedetails/?id={pid}",
            }
    return out, err


def workshop_info(ids):
    """查询模组名称与最后更新时间; 合集(收藏)自动展开为里面的子模组。失败不抛异常"""
    ids = list(dict.fromkeys(int(i) for i in ids))[:300]
    if not ids:
        return [], "没有识别到模组 ID"
    colls = {}
    try:
        payload = {f"publishedfileids[{n}]": pid for n, pid in enumerate(ids)}
        payload["itemcount"] = payload["collectioncount"] = len(ids)
        data = _steam_post(WS_COLL_API, payload)
        for d in (data.get("response") or {}).get("collectiondetails") or []:
            kids = [int(c["publishedfileid"]) for c in d.get("children") or []
                    if str(c.get("publishedfileid", "")).isdigit()]
            if kids and str(d.get("publishedfileid", "")).isdigit():
                colls[int(d["publishedfileid"])] = kids
    except Exception:
        colls = {}          # 合集展开失败不影响主查询
    extra = [c for kids in colls.values() for c in kids if c not in ids]
    need = ids + list(dict.fromkeys(extra))
    with _ws_lock:
        cached = {i: _ws_cache[i] for i in need if i in _ws_cache}
    fresh, err = _ws_fetch([i for i in need if i not in cached])
    found = dict(cached)
    found.update(fresh)
    with _ws_lock:
        for pid, item in fresh.items():
            _ws_cache[pid] = item
    rows, missing = [], []
    for pid in ids:
        if pid in colls:
            base = dict(found.get(pid) or {})
            kids = [dict(found[k], child=True) for k in colls[pid] if k in found]
            rows.append({
                "id": pid,
                "title": (base.get("title") or "合集 / 收藏包") +
                         f" 〔合集, 含 {len(colls[pid])} 个子模组〕",
                "updated": base.get("updated") or "—",
                "created": base.get("created") or "—",
                "dead": bool(base.get("dead")),
                "url": f"https://steamcommunity.com/sharedfiles/filedetails/?id={pid}",
                "kids": kids,
            })
        elif pid in found:
            rows.append(found[pid])
        else:
            missing.append(pid)
    if err and not rows:
        return rows, err
    if err:
        rows.append({"id": 0, "title": err, "updated": "—", "created": "—",
                     "dead": True, "url": "", "kids": [], "warn": True})
    if missing:
        rows.append({"id": 0, "title": "未查到: " + ", ".join(map(str, missing)) +
                     " (可能已删除、设为私有, 或网址里其实是游戏/物品 ID)",
                     "updated": "—", "created": "—", "dead": True, "url": "",
                     "kids": [], "warn": True})
    return rows, ""


def page_workshop():
    full = os.path.join(server_dir(), "WorkshopDownloadConfig.json")
    try:
        data = json.loads(read_file(full)[0]) if os.path.isfile(full) else {}
    except Exception:
        data = {}
    ids_text = "\n".join(str(i) for i in data.get("File_IDs", []))
    ign_text = "\n".join(str(i) for i in data.get("Ignore_IDs", []))
    content = f"""
<div class="banner warn block"><b class="t">⚠️ 安装创意工坊内容前，请先看完这几句</b>
<p>创意工坊内容可能导致服务器<b class="hl">崩溃、冲突或进不去服务器</b>，请各位妥善安装创意工坊内容。</p>
<p>如果出现<b class="hl">启动时长时间卡死</b>，可使用 steam302 软件进行网络调试来解决该问题。</p>
<p>请各位最好安装<b class="hl">熟悉或多人在订阅</b>的模组；陌生模组可能会通过 unturned 本地漏洞
<b class="hl">非法侵入本地计算机</b>。</p></div>
<div class="card"><h2>要下载的模组 <code class="k">File_IDs</code></h2>
<div class="desc">把 Steam 创意工坊模组页面的<b>完整网址</b>直接粘进来就行（每行一个），开服器会自动提纯网址里的数字 ID，
只填数字也可以，合集/收藏包网址同样支持。服务器启动时会自动下载，玩家进服也会自动加载。</div>
<div class="hint" id="hit" style="margin-top:8px"></div>
<textarea class="f" id="ids" rows="7" style="margin-top:10px" oninput="hintIds()"
 placeholder="https://steamcommunity.com/sharedfiles/filedetails/?id=123456789&#10;https://steamcommunity.com/sharedfiles/filedetails/?id=987654321&#10;或者直接写数字: 666777888">{esc(ids_text)}</textarea>
<div style="display:flex;gap:10px;margin-top:12px;flex-wrap:wrap;align-items:center">
<button class="btn" id="qbtn" onclick="wsQuery()">🔍 查询模组信息</button>
<button class="btn big" onclick="save()">💾 保存</button>
</div>
<div class="hint" style="margin-top:8px">「查询模组信息」会联网访问 Steam 官方接口，用来确认模组名称与最后更新时间；
查询失败不影响保存，离线时直接填 ID 保存即可。</div>
<div id="wsres"></div></div>
<div class="card"><h2>忽略的模组 ID <code class="k">Ignore_IDs</code></h2>
<div class="desc">一般不用填。支持同样写法（网址或数字），填写后服务器会跳过这些模组。</div>
<textarea class="f" id="ign" rows="4" style="margin-top:10px">{esc(ign_text)}</textarea></div>
"""
    script = """
function wsIds(t){
 var s=(t===undefined?document.getElementById('ids').value:t).replace(/id\\s*=\\s*/gi,' ');
 var m=s.match(/\\d{6,}/g)||[],seen={},out=[];
 m.forEach(function(v){if(!seen[v]){seen[v]=1;out.push(v);}});
 return out;}
function hintIds(){var n=wsIds().length,e=document.getElementById('hit');
 e.textContent=n?('已识别到 '+n+' 个模组 ID (输入框里的网址会被自动提纯成数字)'):'';}
function el(p,tag,txt,cls){var e=document.createElement(tag);
 if(txt!=null)e.textContent=txt;if(cls)e.className=cls;p.appendChild(e);return e;}
function modRow(tb,m){
 var tr=tb.insertRow(),c0=el(tr,'td');
 el(c0,'b',(m.child?'└ ':'')+m.title);
 if(m.dead)el(c0,'div','⚠ 可能已删除、设为私有或无法访问','hint');
 el(tr,'td',String(m.id));el(tr,'td',m.updated||'—');el(tr,'td',m.created||'—');
 if(m.url){var a=el(el(tr,'td'),'a','创意工坊页面');a.href=m.url;a.target='_blank';a.rel='noopener';}
 else el(tr,'td','—');}
async function wsQuery(){
 var ids=wsIds();
 var box=document.getElementById('wsres');
 if(!ids.length){toast('先粘贴创意工坊网址或模组 ID',1);return;}
 box.innerHTML='';
 var btn=document.getElementById('qbtn');btn.disabled=true;
 var card=el(box,'div',null,'card');
 el(card,'h2','查询中…').id='wshd';
 var tip=el(card,'div','正在联网查询 Steam 创意工坊 ('+ids.length+' 个)，请稍候','hint');
 var r=null;
 try{r=await post('/api/workshop/info',{ids:ids.map(Number)});}
 catch(e){r={ok:false,msg:'网络请求失败: '+e};}
 finally{btn.disabled=false;tip.remove();}
 card.querySelector('h2').textContent=r&&r.ok?'查询结果':'查询失败';
 if(!r||!r.ok){
  var w=el(card,'div',null,'banner warn');
  w.textContent=(r&&r.msg||'未知错误')+' — 不影响直接保存';return;}
 var t=el(card,'table');
 el(t,'thead').innerHTML='<tr><th>模组名称</th><th>模组 ID</th><th>最后更新</th><th>首次发布</th><th></th></tr>';
 var tb=el(t,'tbody');
 (r.items||[]).forEach(function(m){
  modRow(tb,m);
  (m.kids||[]).forEach(function(k){modRow(tb,k);});});
 if(r.err)el(card,'div',r.err,'hint');}
async function save(){
 var ids=wsIds().map(Number);
 var ign=wsIds(document.getElementById('ign').value).map(Number);
 var r=await post('/api/workshop',{ids:ids,ignores:ign});
 if(r.ok){
  document.getElementById('ids').value=ids.join('\\n');
  document.getElementById('ign').value=ign.join('\\n');
  hintIds();
  toast('✔ 已保存 (网址已提纯为 '+ids.length+' 个 ID), 重启服务器后生效');}
 else toast('✘ '+(r&&r.msg||'保存失败'),1);}
hintIds();
"""
    return shell("ws", "创意工坊", content, script)


# ================================================================ 存档管理 (备份 / 删除)
def page_saves():
    inv = save_inventory()
    sd = server_dir()
    suggested = os.path.join(BASE_DIR, "存档备份")

    def file_rows(files):
        out = ""
        for r in files:
            out += (f'<tr><td><input class="bx" type="checkbox" value="{esc(r["rel"])}"></td>'
                    f'<td><b>{esc(os.path.basename(r["rel"]))}</b></td>'
                    f'<td class="hint{" old" if r["old"] else ""}">{esc(r["desc"])}</td>'
                    f'<td class="hint">{human_size(r["size"])}</td>'
                    f'<td class="hint">{esc(r["mtime"])}</td></tr>')
        return out or '<tr><td colspan="5" class="hint">这个文件夹里没有存档文件</td></tr>'

    def grp_html(gid, title, sub, files):
        return f"""<div class="sv" id="{gid}">
<div class="svh"><b>{title}</b><span class="hint">{sub}</span><span class="sp"></span>
<button class="btn sm gray" onclick="pickAll('{gid}',true)">全选</button>
<button class="btn sm gray" onclick="pickAll('{gid}',false)">取消全选</button>
<button class="btn sm" onclick="backup('{gid}','all')">📦 整体备份</button>
<button class="btn sm" onclick="backup('{gid}','sel')">⬇ 备份勾选</button>
<button class="btn sm red" onclick="del('{gid}')">🗑 备份后删除勾选</button></div>
<table><tr><th style="width:34px"></th><th>文件</th><th>这是什么存档</th><th>大小</th><th>最后保存</th></tr>
{file_rows(files)}</table></div>"""

    lv_html = "".join(
        grp_html(f"L{n}", f"🏠 地图 {esc(g['map'])}",
                 f"{len(g['files'])} 个建筑存档文件 · 共 {human_size(g['size'])}", g["files"])
        for n, g in enumerate(inv["level"])) or \
        '<div class="hint">这个存档还没有建筑存档文件 (Level 文件夹是空的) — 开服并进游戏建东西后回来看看。</div>'

    pl_html = "".join(
        grp_html(f"P{n}", f"🧑 玩家 {esc(p['id'])}",
                 f"地图 {'/'.join(map(esc, p['maps'])) or '—'} · {len(p['files'])} 个文件 · 共 {human_size(p['size'])}",
                 p["files"])
        for n, p in enumerate(inv["players"])) or \
        '<div class="hint">还没有玩家进过服 (Players 文件夹是空的), 所以没有玩家存档可备份。</div>'

    allp = "PALL"
    if inv["players"]:
        pl_html = f"""<div id="{allp}">
<div class="svh" style="margin-bottom:10px"><b>👥 全部玩家 ({len(inv['players'])} 人)</b>
<span class="hint">整体备份所有人的玩家存档; 删除则是先备份再删掉这些人的存档</span>
<span class="sp"></span>
<button class="btn sm gray" onclick="pickAll('{allp}',true)">全选</button>
<button class="btn sm gray" onclick="pickAll('{allp}',false)">取消全选</button>
<button class="btn sm" onclick="backup('{allp}','all')">📦 整体备份全部玩家</button>
<button class="btn sm red" onclick="del('{allp}')">🗑 备份后删除勾选</button></div>
{pl_html}</div>"""

    content = f"""
<div class="banner warn block"><b class="t">📦 备份 / 删除存档前, 先看这几句</b>
<p>Unturned 的存档文件是<b class="hl">加密的</b>, 人工打不开也改不了 —— 所以下面只写明每个文件是什么,
你按名字勾选就够了。</p>
<p><b class="hl">建筑存档</b>在 <code class="k">Level\\地图名\\</code>；
<b class="hl">玩家存档</b>在 <code class="k">Players\\SteamID_0\\</code>。
路径全部按你在「切换/重设」里选的游戏目录 + 当前存档自动读取。</p>
<p>带 ♻ 说明的是游戏自己留的<b class="hl">上一次保存的旧版本</b>文件, 一般跟着一起备份即可。</p>
<p>删除操作<b class="hl">一定会先备份</b>: 先把你勾选的内容复制到你指定的位置, 备份成功后才开始删;
只要备份有一丁点失败, 就一个文件都不会动。</p>
<p>删除只删勾选到的<b class="hl">那几个文件</b>, 不会删 Level / Players 这些文件夹,
空文件夹会原样保留, 下次开服正常使用。</p>
<p>请<b class="hl">先关服</b>再删存档 —— 服务器运行中删掉的文件, 会在关服自动保存时被重新写回去。</p></div>

<div class="card"><h2>① 备份要保存在哪里 (自己选)</h2>
<div class="desc">选一个文件夹就行, 桌面、其他盘、移动硬盘都可以。每次备份都会在里面新建一个
「{esc(instance())}_备份_年月日_时分秒」子文件夹, 文件夹内部保持 Level / Players 的原目录结构, 不会覆盖你上一次的备份。</div>
<div style="display:flex;gap:10px;margin-top:12px;flex-wrap:wrap">
<input class="f" id="dest" style="flex:1;min-width:300px" placeholder="例如 D:\\我的存档备份"
 value="{esc(suggested)}">
<button class="btn gray" onclick="browse()">📂 浏览文件夹…</button></div>
<div class="hint" id="lastbk"></div></div>

<div class="card"><h2>② 建筑存档 · Level</h2>
<div class="desc">玩家搭的房子、地基、墙、门、箱子、停着的车等<b>地图上的东西</b>。删掉 = 这些建筑从地图上消失。</div>
<div class="hint" style="margin-bottom:8px">当前位置: <code class="k">{esc(os.path.join(sd, 'Level'))}</code></div>
{lv_html}</div>

<div class="card"><h2>③ 玩家存档 · Players</h2>
<div class="desc">每个文件夹是一个玩家, 文件夹名就是他的 <b>SteamID64</b> (17 位数字)。
物品栏、技能、血量、任务、穿着都在里面。删掉 = 该玩家从零开始重新玩。</div>
<div class="hint" style="margin-bottom:8px">当前位置: <code class="k">{esc(os.path.join(sd, 'Players'))}</code>
 · 想知道哪个 ID 是谁: 开服后在「控制台 → 在线玩家」里对照即可</div>
{pl_html}</div>
"""
    script = """
function destPath(){var d=document.getElementById('dest'),v=d.value.trim();
 if(!v){toast('请先选择备份保存在哪个文件夹',1);d.focus();}
 return v;}
function boxes(g){return [].slice.call(document.querySelectorAll('#'+g+' input.bx'));}
function pickAll(g,ck){boxes(g).forEach(function(b){b.checked=ck;});}
function picked(g){return boxes(g).filter(function(b){return b.checked}).map(function(b){return b.value});}
function allFiles(g){return boxes(g).map(function(b){return b.value});}
function setLast(p){if(p)document.getElementById('lastbk').textContent='📁 最近一次备份位置: '+p;}
async function backup(g,mode){
 var items=mode==='all'?allFiles(g):picked(g);
 if(!items.length){toast('先勾选要备份的存档文件',1);return;}
 var d=destPath();if(!d)return;
 toast('正在备份 '+items.length+' 个存档文件…');
 var r=await post('/api/saves/backup',{items:items,dest:d});
 toast(r.msg,r.ok?0:1);if(r.ok)setLast(r.dest);}
async function del(g){
 var items=picked(g);
 if(!items.length){toast('先勾选要删除的存档文件 (删除必须先勾选)',1);return;}
 var d=destPath();if(!d)return;
 var go=await modal({icon:'🗑',title:'删除前会先自动备份',
  body:'第 1 步: 把你勾选的 '+items.length+' 个存档文件完整备份到\\n'+d+
       '\\n第 2 步: 备份确认成功后, 才删除这些文件本身。\\n'+
       'Level / Players 等文件夹会保留, 只删对应的文件。\\n备份失败则不会删除任何东西。\\n\\n确定继续吗?',
  okText:'先备份, 再删除',noText:'取消',danger:true});
 if(!go)return;
 toast('正在备份并删除…');
 var r=await post('/api/saves/delete',{items:items,dest:d,confirm:true});
 toast(r.msg,r.ok?0:1);
 if(r.ok){pickAll(g,false);setLast(r.dest);setTimeout(function(){location.reload();},2600);}}
function browse(){
 var m=document.createElement('div');m.className='mask';
 var box=document.createElement('div');box.className='modal-box wide';
 var h=document.createElement('h3');h.textContent='📂 选择备份保存的文件夹';
 var path=document.createElement('div');path.className='dpath';
 var list=document.createElement('div');list.className='dirlist';
 var bt=document.createElement('div');bt.className='mbtns';
 function btn(t,cls){var b=document.createElement('button');b.className=cls;b.textContent=t;
  bt.appendChild(b);return b;}
 var up=btn('⬆ 上一级','btn gray'),no=btn('取消','btn gray'),ok=btn('✔ 保存在这里','btn');
 box.appendChild(h);box.appendChild(path);box.appendChild(list);box.appendChild(bt);
 m.appendChild(box);document.body.appendChild(m);
 var st={cwd:'',parent:''};
 function show(p){
  list.textContent='加载中…';
  post('/api/dirs',{path:p}).then(function(d){
   list.textContent='';
   if(!d.ok){list.textContent=d.msg||'打不开该文件夹';return;}
   st={cwd:d.cwd,parent:d.parent};
   path.textContent=d.cwd?('当前文件夹: '+d.cwd):'先选一个盘:';
   if(!d.dirs.length){list.textContent='这个里面没有子文件夹 —— 右边点「✔ 保存在这里」即可选它。';return;}
   d.dirs.forEach(function(x){
    var b=document.createElement('button');b.className='ditem';b.textContent='📁 '+x.name;
    b.onclick=function(){show(x.path);};list.appendChild(b);});});}
 up.onclick=function(){show(st.parent||'');};
 no.onclick=function(){m.remove();};
 ok.onclick=function(){
  if(!st.cwd){toast('先进入一个文件夹再确定',1);return;}
  document.getElementById('dest').value=st.cwd;m.remove();};
 m.addEventListener('click',function(e){if(e.target===m)m.remove();});
 show('');}
"""
    return shell("save", "存档管理", content, script)


# ================================================================ 控制台
def page_console():
    content = f"""
<div class="card"><h2>控制台通道体检</h2>
<div class="desc">网页上的<b>发送命令 / 刷新玩家列表 / 安全关服</b>都走 Rocket 的 RCON 通道。
这一栏会直接告诉你卡在哪一环: Rocket 没装好、服务器进程已经崩了、还是配置被覆盖。
<b>发指令失败时这里也会自动更新</b>, 不用自己猜。</div>
<div id="rkline" class="hint" style="margin-top:10px">正在体检…</div>
<div id="rkev" class="hint" style="opacity:.72;margin-top:6px;word-break:break-all"></div>
<div style="display:flex;gap:8px;margin-top:12px;flex-wrap:wrap">
<button class="btn sm gray" onclick="rkDiag()">🔍 重新体检</button>
<button class="btn sm" onclick="rkFix()">🚀 修复 Rocket</button>
</div></div>
<div class="card"><h2>在线玩家</h2>
<div class="desc">点「刷新玩家列表」获取在线玩家 (会显示玩家名 + SteamID)。给物品/给车辆/传送/踢出属于作弊指令, 需要服务器开启作弊 (Commands.dat 里的 <code class="k">cheats on</code>)。<br>
按钮会自动用 <b>SteamID</b> 定位玩家, 中文玩家名也不会匹配失败。<br>
<b>无敌和隐身</b>需玩家自己在游戏内使用指令: <code class="k">/god</code> 开无敌、<code class="k">/vanish</code> 开隐身 (开服器不提供按钮)。</div>
<div style="display:flex;gap:10px;margin:10px 0;flex-wrap:wrap">
<button class="btn" onclick="refreshPlayers()">刷新玩家列表</button>
<span class="hint" id="pcount" style="align-self:center"></span></div>
<div id="plist" class="hint">尚未获取。获取后可对玩家执行: 给物品 / 给车辆 / 传送到其他玩家身边 / 踢出</div>
</div>
<div class="card"><h2>快捷指令</h2>
<div class="desc">一键发送常用服务器指令 (原版/ Rocket 通用)。</div>
<div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">
<button class="btn sm gray" onclick="quick('save')">保存世界</button>
<button class="btn sm gray" onclick="quick('day')">白天</button>
<button class="btn sm gray" onclick="quick('night')">黑夜</button>
<button class="btn sm gray" onclick="quick('weather none')">无天气</button>
<button class="btn sm gray" onclick="quick('players')">在线玩家</button>
<button class="btn sm gray" onclick="promptCmd('broadcast', '要全服广播的内容:')">全服广播</button>
<button class="btn sm gray" onclick="promptCmd('experience', '给谁加经验(玩家名/SteamID):', '加多少经验(数字):', 'experience')">加经验</button>
<button class="btn sm gray" onclick="promptCmd('admin', '提升谁为管理员(玩家名/SteamID):')">设管理员</button>
<button class="btn sm gray" onclick="promptCmd('kick', '踢出谁(玩家名):', '踢出原因(可空):', 'kick')">踢出玩家</button>
<button class="btn sm gray" onclick="promptCmd('ban', '封禁谁(玩家名/SteamID):', '封禁时长小时(留空=永久):', 'ban')">封禁玩家</button>
<button class="btn sm gray" onclick="quick('rocket reload')">重载插件</button>
<button class="btn sm red" onclick="quick('shutdown')">安全关服</button>
</div>
<div class="hint">输入框也可以直接敲任何命令, 例如 <code class="k">give 玩家名 253 2</code>、<code class="k">teleport 甲 乙</code></div>
</div>
<div class="card"><h2>发送命令</h2>
<div class="desc">通过 Rocket RCON 发送命令 (开服器会在开服时自动临时启用 RCON, 密码用本机密钥文件,
关服后自动还原)。常用: <code class="k">save</code> 保存世界、<code class="k">shutdown</code> 安全关服、<code class="k">players</code> 查看玩家</div>
<div style="display:flex;gap:10px;margin-top:10px;flex-wrap:wrap">
<input class="f" id="cmd" placeholder="输入命令后回车, 例如: save" style="flex:1;min-width:200px">
<button class="btn" onclick="send()">发送</button>
</div></div>
<div class="card"><h2>运行日志
{check_html(ident="f_err", label="只看报错/警告", style="margin-left:14px")}
</h2>
<div class="term" id="term"></div>
<div class="hint" id="logsrc"></div>
<div class="hint">实时滚动 · <span style="color:#fca5a5">红色</span>=报错
<span style="color:#fde047">黄色</span>=警告 <span style="color:#6ee7b7">绿色</span>=服务器代码 · 来自 Rocket 日志;
刚装完 Rocket 或刚开服时日志需要等服务器完全启动后才会出现, 服务器自己的黑色控制台窗口里始终有实时输出</div>
<div class="banner info block" style="margin-top:10px">
<b class="t">ℹ️ 启动服务器时出现报错和警告属于正常行为</b>
<p>Unturned 服务端启动阶段本身会打印一些红字/黄字信息, 只要服务器能正常开服、朋友能进服就没有影响。</p>
<p><b class="hl">如果没有安装其他插件(Rocket 插件/模组), 不用管这些报错和警告</b>。</p>
<p>只有当服务器起不来、地图加载卡住或插件明显不生效时, 再针对性排查对应的日志行。</p></div>
</div>
"""
    script = r"""
var lastIdx=0,auto=true;
var term=document.getElementById('term');
term.addEventListener('scroll',()=>{auto=term.scrollTop+term.clientHeight>term.scrollHeight-40;});
function line(d){var div=document.createElement('div');
var cls='l'+(d.e==1?' err':d.e==2?' warn':d.e==3?' ok':d.s=='cmd'?' cmd':d.s=='sys'?' sys':'');
div.className=cls;div.textContent=d.t;div.dataset.err=(d.e==1||d.e==2)?'1':'0';term.appendChild(div);
while(term.children.length>1500)term.firstChild.remove();}
async function poll(){
 try{var r=await(await fetch('/api/console?after='+lastIdx)).json();
 r.lines.forEach(line);lastIdx=r.last;
 if(r.lines.length&&auto)term.scrollTop=term.scrollHeight;
 if(r.src!==undefined){var ls=document.getElementById('logsrc');
 if(ls)ls.textContent='日志源: '+(r.src.console?'控制台中继 console.log':r.src.rocket?'Rocket 日志':'暂无日志文件 — 服务器完全启动后生成; 若一直为空请安装 Rocket 并重启服务器');}
 applyFilter();}catch(e){}}
function applyFilter(){var only=document.getElementById('f_err').checked;
 term.querySelectorAll('.l').forEach(l=>{l.style.display=(only&&l.dataset.err!=='1')?'none':'';});}
var fe=document.getElementById('f_err');if(fe)fe.addEventListener('change',applyFilter);
async function rkDiag(){
 var box=document.getElementById('rkline'),ev=document.getElementById('rkev');
 if(!box)return;
 box.textContent='体检中…';
 var r;
 try{r=await (await fetch('/api/rcon/diag')).json();}
 catch(e){box.textContent='体检没能跑起来: '+e;return;}
 if(r.ok){box.innerHTML='<span style="color:#6ee7b7">✅ RCON 端口通了, 网页控制台可以正常发指令</span>'
  +' · 游戏目录里 Rocket 文件 '+(r.rocket&&r.rocket.files||0)+' 个';}
 else{box.innerHTML='<span style="color:#fca5a5">⚠ '+ (r.msg||'连不上服务器') + '</span>';}
 if(ev)ev.textContent='证据: '+(r.evidence||'');
}
async function rkFix(){
 var box=document.getElementById('rkline');
 box.textContent='正在运行官方安装脚本 (Extras\\Install Rocket.bat)…';
 var r=await post('/api/rocket/install',{});
 toast(r.msg,r.ok?0:1);
 if(r.ok)setTimeout(rkDiag,600);else rkDiag();
}
async function send(){var el=document.getElementById('cmd');var c=el.value.trim();if(!c)return;
 el.value='';var r=await post('/api/console',{cmd:c});if(!r.ok){toast(r.msg,1);rkDiag();}}
async function quick(c){var r=await post('/api/console',{cmd:c});
 if(!r.ok){toast(r.msg,1);rkDiag();}else toast('已发送: '+c);}
async function promptCmd(base,q1,q2,verb){
 var a=prompt(q1);if(!a)return a.trim()===''?null:a.trim();
 var cmd=base+' '+a.trim();
 if(q2){var b=prompt(q2);if(b===null)return;cmd+=' '+b.trim();}
 var r=await post('/api/console',{cmd:cmd});
 if(!r.ok){toast(r.msg,1);rkDiag();}else toast('已发送: '+cmd);}
async function refreshPlayers(){
 var box=document.getElementById('plist');
 box.textContent='获取中…';
 var r=await post('/api/players');
 if(!r.ok){box.textContent=r.msg||'获取失败';document.getElementById('pcount').textContent='';
  rkDiag();return;}
 renderPlayers(r.players,r.raw);}
function renderPlayers(players,raw){
 var box=document.getElementById('plist');
 document.getElementById('pcount').textContent=players.length?('共 '+players.length+' 人在线'):'';
 if(!players.length){box.textContent='当前没有玩家在线 —— 让朋友进服或自己进服后再刷新即可看到列表';return;}
 box.innerHTML='';
 players.forEach(p=>{
  var row=document.createElement('div');
  row.style.cssText='display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:8px 4px;border-bottom:1px solid rgba(255,255,255,.06)';
  var nm=document.createElement('b');nm.textContent=p.name;row.appendChild(nm);
  if(p.steamid){var sid=document.createElement('code');sid.className='k';sid.textContent=p.steamid;row.appendChild(sid);}
  var mk=(label,fn,color)=>{var b=document.createElement('button');b.className='btn sm gray';
   if(color)b.style.color=color;b.textContent=label;b.onclick=fn;row.appendChild(b);};
  var target=p.steamid||p.name;
  mk('给物品',()=>{var id=prompt('给「'+p.name+'」哪个物品ID?');if(!id)return;
    var n=prompt('数量(默认1):');var cmd='give '+target+' '+id.trim()+(n&&n.trim()?' '+n.trim():' 1');
    post('/api/console',{cmd:cmd}).then(r=>toast(r.ok?'已发送: '+cmd:r.msg,r.ok?0:1));});
  mk('给车辆',()=>{var id=prompt('给「'+p.name+'」哪个载具ID?');if(!id)return;
    var cmd='vehicle '+target+' '+id.trim();
    post('/api/console',{cmd:cmd}).then(r=>toast(r.ok?'已发送: '+cmd:r.msg,r.ok?0:1));});
  mk('传送到…',()=>{var dst=prompt('把「'+p.name+'」传送到哪位玩家身边?');if(!dst)return;
    var cmd='teleport '+target+' '+dst.trim();
    post('/api/console',{cmd:cmd}).then(r=>toast(r.ok?'已发送: '+cmd:r.msg,r.ok?0:1));});
  mk('踢出',()=>{var rs=prompt('踢出「'+p.name+'」的原因(可空):');if(rs===null)return;
    var cmd='kick '+target+(rs.trim()?' '+rs.trim():'');
    post('/api/console',{cmd:cmd}).then(r=>toast(r.ok?'已发送: '+cmd:r.msg,r.ok?0:1));});
  box.appendChild(row);});
}
document.getElementById('cmd').addEventListener('keydown',e=>{if(e.key==='Enter')send();});
poll();setInterval(poll,1500);rkDiag();
"""
    return shell("term", "控制台", content, script)


# ================================================================ 帮助页
def page_help():
    content = """
<div class="card sos"><h2><span class="sostag">置顶</span>🚨 进不去服务器? 对着报错一条条解决</h2>
<div class="desc">你或朋友用服务器代码连接失败时, 游戏会在屏幕上弹一句提示。下面四条覆盖了日常遇到的
绝大多数情况, <b>先看清提示是哪一条, 再照着做</b>, 不用一头雾水地乱重启。<br>
<span class="hint">这一页从哪个页面都能一步跳到: 每个页面<b>最上面那排的红色按钮
「🚨 进不去服帮助」</b>点一下就到这里 (v0.1.17 起)。</span></div>

<div class="sosrow"><b>① 服务器连接超时 / 连接失败</b>
<div class="fix">一般是<b>本机网络</b>的问题, 不是服务器坏了。让进不去的那个人<b>连着试两次</b>;
两次都进不来, 就把<b>服务器重启一次</b>, 再把仪表盘最新的那串代码发给他。</div></div>

<div class="sosrow"><b>② 无法验证 Steam 经济 / 连接 Steam 服务器失败</b>
<div class="fix">这条<b>最常见</b>, 十个人里八个会撞到 —— 它跟你的服务器没半点关系, 是 Steam 那边一时
没连通。<b>不用重启服务器</b>, 直接<b>硬挤</b>: 反复点连接多试几次, 只要有一次跟 Steam 服务器通上就进来了。
<b>特别注意: 开了加速器反而更容易一直跳这一句</b>, 解决办法还是硬挤, 或者重启服务器。</div></div>

<div class="sosrow"><b>③ 未开启 BattlEye (战眼反作弊)</b>
<div class="fix">两条路选一条:<br>
① 在开服器<b>「服务器设置」页</b>中间的 <b>「🛡️ 反作弊 (VAC / BattlEye 战眼)」</b> 卡片里,
把 <code class="k">BattlEye (战眼) 反作弊</code> 的勾<b>取消</b> → 点「保存反作弊设置」→ 重启服务器
(关反作弊开服器会先弹框跟你确认一次);<br>
② 给<b>游戏本体</b>(不是服务器)重装战眼: Steam 库里右键 Unturned → 管理 → 浏览本地文件 →
进 <code class="k">BattlEye</code> 文件夹运行 <code class="k">Install_BattlEye.bat</code>,
装完重启电脑再进服。</div></div>

<div class="sosrow"><b>④ 其他没写到的报错</b>
<div class="fix">绝大多数<b>重启服务器</b>就能解决。只有两种重启没用: <b>模组冲突</b>(开服卡住、闪退、
进不去, 见「创意工坊」页顶部的风险提示)和<b>插件冲突</b>, 这两种要一个个卸载排查。<br>
<b>联机模式请不要使用来路不明的插件</b> —— 它们可能带病毒, 也可能直接把存档搞坏,
到时候丢的是所有人的建筑和进度。</div></div>

<div class="sosqq">遇到上面四条都没覆盖的特殊问题, 加开发者 QQ <b>2497194220</b> 私聊问询。
<b>添加好友时请把问题一并写明</b>(报的是哪句错、从什么时候开始、是服务器进不去还是客户端崩了),
我看到了都会回复。</div>
</div>

<div class="card"><h2>第一次开服 (3 步)</h2>
<div class="desc" style="line-height:2.1">
1. 首次打开会进入「初始设置」: 选游戏目录 → 选存档(或取名新建)<br>
2. 回到<b>仪表盘</b>点「开服」, 等 30~60 秒 (地图<b>加载到 100%</b> 后), 中间出现一串数字 = <b>服务器代码</b><br>
3. 点「复制服务器代码」发给朋友 → 朋友游戏内 <b>Play → 输入服务器代码</b> 即可联机 (无需端口映射)<br>
<span class="hint">注意: 服务器代码每次开服都会变化, 以最新的为准。开服器会<b>严格等到地图加载到 100%</b> 才显示代码,
所以刚开服时先看到"服务器正在启动"是正常现象, 不会把上一局的旧代码显示出来。
朋友输入代码无反应/报错时, 先确认你这边服务器显示"运行中", 再让朋友重启 Steam 后重试。</span>
</div></div>

<div class="card"><h2>⚠️ 别把「游戏本体」当成开服端</h2>
<div class="desc" style="line-height:2.1">
Steam 上有两个 Unturned: <b>Unturned</b> 是你平时玩的游戏本体, <b>Unturned - Dedicated Server</b>
是免费开的专用服务器端 (大家都叫它 U3DS)。两个文件夹里都有 <code class="k">Unturned.exe</code>,
但<b>只有开服端能开服</b> —— 拿本体开服会在启动时抛
<code class="k">NullReferenceException at SDG.Unturned.Assets.Update()</code>, 服务器根本起不来,
还会往本体目录里写一个 Servers 文件夹, 有搞坏游戏文件的风险。<br>
从 v0.1.10 起开服器会<b>直接拦下这种绑定</b>: 第 1 步选了本体会红字告诉你为什么不行;
之前已经绑到本体上的, 打开后会退回第 1 步并说明原因。<br>
<b>在本体里建过存档怎么办</b>: 打开 <code class="k">...common/Unturned/Servers</code>,
把里面那个存档文件夹整个<b>剪切</b>到开服端的 <code class="k">Servers</code> 目录下,
重新选一次目录就能接着玩, 建筑和玩家数据都不会丢。</div></div>

<div class="card"><h2>🔍 它是怎么认出「这是本体」的 (v0.1.13 起)</h2>
<div class="desc" style="line-height:2.1">
两个文件夹里都有 <code class="k">Unturned.exe</code>, 光看 exe 分不出来。开服器按<b>三层</b>判:
<br>· <b>第一层 · Steam 的记录</b>: 读 <code class="k">steamapps\\appmanifest_304930.acf</code>
(本体) 和 <code class="k">appmanifest_1110390.acf</code> (开服端) 里 Steam 自己记的安装目录。
这份记录<b>你改不动</b>, 命中就一锤定音 —— Steam 亲装的开服端就算被本体文件污染过也不会误判。<br>
· <b>第二层 · 目录名</b>: 文件夹叫 <code class="k">U3DS</code> (Steam 默认安装名) 就放行,
只在本体有 <code class="k">Unturned_BE.exe</code> 时例外 (开服端绝不会有这个文件, 它单独定罪)。<br>
· <b>第三层 · 运行残留</b>: 名字对不上、也不在 Steam 记录里 (自己拷贝的) 时, 要<b>同时命中两条</b>
才算本体 —— <code class="k">Unturned_BE.exe</code> /
<code class="k">steam_appid.txt</code> 写着 304930 / 同时有 <code class="k">Worlds</code> 和
<code class="k">Preferences.json</code>。<br>
<span class="hint">只命中一条不定罪, 改成<b>黄字提醒</b>照样放行 —— 整合包里混了几个本体文件是常见事,
不该因此不让你开服。报错末尾会附上「(本目录实测: …)」把三条判据的实测结果都列出来,
反馈问题时<b>截那一句</b>就能一眼看出是哪条判据命中的, 不用来回猜。</span></div></div>

<div class="card"><h2>🛒 开服端怎么拿到: 优先用 Steam 商店装</h2>
<div class="desc" style="line-height:2.1">
<b>Unturned - Dedicated Server</b> (AppID 1110390) 在 Steam 上是<b>完全免费</b>的, 谁都能点安装,
不用买、不用激活码、也不要你是会员。走商店这条路最省心: 下载、校验、以后自动更新全交给
Steam 自己的下载器, <b>不会卡在 0%, 也不用管路径里有没有中文</b>。<br>
<b>怎么做</b>: 打开「初始设置」第 1 步, 点<b>「🛒 在 Steam 商店打开开服端 (推荐)」</b>
—— 它只在一个<b>新标签页</b>里打开商店, 你现在这个开服器页面<b>不会跳走</b>, 填好的设置也还在。
到了商店页点「安装」, 装完回到开服器点「🔄 重新检测本机」, 下拉里直接就能选到它。<br>
<span class="hint">商店装会占你 Steam 库里的一个「工具」位置 (和玩游戏的本体是两回事, 不会搞混)。
不想让它进游戏库、或者这台机器没装 Steam 客户端, 再用下面那条备用的 SteamCMD。</span></div></div>

<div class="card"><h2>🔻 备用方案 SteamCMD: 下载/更新失败九成是路径里有中文</h2>
<div class="desc" style="line-height:2.1">
商店那条路走不通时才用它 (没装 Steam 客户端、不想让它进游戏库)。黑框里出现下面这两行之一,
<b>不是网络问题</b>, 加速器帮不上忙 ——<br>
<code class="k">Fatal Error: %appname% cannot run from a folder path that includes non-English characters</code><br>
<code class="k">main.cpp (478) : Assertion Failed: Unable to access Steam files due to incompatible path</code><br>
Valve 的 steamcmd 一开机就检查<b>它自己所在的文件夹</b>和<b>要下载到的文件夹</b>,
只要路径里出现中文 (或者任何其他非英文字符) 就直接退出, 连 0% 都跑不满。
桌面上、用户名里带中文都算, 所以 <code class="k">C:\\Users\\小明\\Desktop\\工具\\steamcmd</code> 这种位置一定失败。<br>
<b>v0.1.11 起开服器自己处理掉了</b>: steamcmd <b>已经自带在完整包里</b> (不用先下那不到 1 MB 的工具),
位置也会自动挑纯英文的 (例如 <code class="k">D:\\DawnSharkTools\\steamcmd</code>),
下载位置默认同样避开中文, 你在网页上填了中文路径会当场红字提醒你换。<br>
· 以前卡在「① 下载官方 SteamCMD」九成就报 <code class="k">The read operation timed out</code>:
那是下工具的这一步, 和 1.5 GB 开服端无关 —— 现在包里自带, 这一步整个跳过了<br>
· 报 <code class="k">ERROR! Failed to install app '1110390' (Missing configuration)</code>:
这是 Steam 一时没把开服端的应用信息递过来, <b>不是你的路径也不是你的网络设置</b> ——
v0.1.11 起开服器会自己隔几秒重跑, 最多三趟 (实测第二趟就能把 1.8 GB 一路装完), 你不用管<br>
· 报 <code class="k">No license</code> / 一直卡在下载不动: 这个才是网络问题, 开一下 Steam 加速器再点一次<br>
· 反复都不行就别跟它耗: 点上面那个「🛒 在 Steam 商店打开开服端 (推荐)」, 让 Steam 自己装一次,
回到第 1 步点「🔄 重新检测本机」就能选到, <b>一次网都不用再折腾</b><br>
· 更新完记得重启服务器才生效; 更新<b>不会碰 Servers 里的存档</b>。</div></div>

<div class="card"><h2>⚠️ 朋友进不去: 开服端版本落后于游戏本体</h2>
<div class="desc" style="line-height:2.1">
Steam 会自动把你的<b>游戏本体</b>更新到最新, 但<b>开服端不会自己动</b> —— 游戏版本一旦对不上,
朋友点你的服务器代码就会<b>卡在加载那一格</b>, 或者提示版本不一致。<br>
现在「服务器设置 → 开服端版本与更新」会直接把两边版本号摆出来对比, 落后了会红字提醒,
点<b>「检查并更新开服端」</b>就用 steamcmd 补到最新 (走 Valve 官方服务器, 匿名登录, 不碰你的 Steam 账号)。<br>
<span class="hint">当初是用 Steam 商店装的开服端, 最省心的更新方式是直接在 Steam 库里右键它 → 更新;
用「🛒 在 Steam 商店打开开服端」那个按钮随时可以跳过去。</span><br>
· 版本号是从开服端自己写的日志里读的, 所以这个开服端<b>至少完整跑过一次</b>才读得到</div></div>

<div class="card"><h2>🧩 创意工坊地图: 识别、换图、资产包</h2>
<div class="desc" style="line-height:2.1">
<b>怎么识别</b>: 「服务器设置 → 地图」那个下拉框会直接列出<b>本存档已经下载好的创意工坊地图</b>,
名字后面标着 <code class="k">· 🧩 创意工坊 模组ID</code>, 点一下就填进配置; 旁边的
<b>「🧩 识别本存档的创意工坊地图」</b>按钮用来<b>不重启页面</b>就地重扫一遍
(刚下完地图想让下拉框立刻认到, 就点它)。<br>
· <b>填完模组 ID 必须把服务器启动一遍</b>, 游戏才会真的去下载那张地图 —— 没下载过地图时
下拉框里不会出现它, 按钮扫出来也会告诉你「本存档还没有下载好的创意工坊地图」<br>
· 认地图的办法: 存档的 <code class="k">Workshop\\Steam\\content\\304930\\&lt;模组ID&gt;</code> 里
有 <code class="k">Map.meta</code> 的, 就按它旁边那个文件夹的名字当地图名 (资产包没有
<code class="k">Map.meta</code>, 不会被错认成地图)<br>
· <b>换地图等于开一个新档</b>: 建筑不会跟过去、角色数据也不会跟过去。老档<b>不会丢</b> ——
原地图仍然在存档的 <code class="k">Level\\旧地图名</code> 里, 把地图名换回去就能接着玩。
真正换图之前开服器会先把这几句弹给你确认, 并写出<b>你当前的服务器存档目录</b><br>
· <b>用的是创意工坊地图时, 开服成功后会弹一个 10 秒的提醒框</b>: 有些工坊地图还要<b>另外订阅一份
资产包</b> (也是创意工坊文件), 资产包没到位时地图会加载失败、或者进去是一片空图。
要不要资产包请到那张地图的创意工坊页面看; 框里会带上你当前的存档目录, 还有一个
<b>「🧩 在创意工坊打开这张地图」</b>的按钮 (新标签页打开, 不会把这个页面顶掉)</div></div>

<div class="card"><h2>🔧 玩法设置最底下那个「高级配置」(v0.1.14 起)</h2>
<div class="desc" style="line-height:2.1">
「玩法设置」页平时看到的那几十项是<b>挑出来最常调的</b>; 官方 Config.txt 里还剩一大把参数没地方填。
这一版把它们收进了页面最底下的 <b>「🔧 高级配置」</b> —— <b>默认是收起的, 不点开就当它不存在</b>,
一行代码都不会往存档里写, 原来那一页多长还是多长。<br>
· 一共 <b>191 项</b>, 分 13 个段落 (玩家 / 僵尸 / 动物 / 载具 / 建筑 / 物品 / 场景物件 / 事件天气 /
玩法手感 / 服务器 / 模组事件 / 大厅展示), 每项都有中文名和一句人话说明, 顶部还有个<b>关键词筛选框</b>,
输入「僵尸」「掉落」「Respawn」都能立刻筛到那一堆<br>
· <b>这一批官方没有公布安全取值范围</b>, 所以开服器只挡住「一定会写坏文件」的填法: 负数、非数字、
概率大于 1、掉落个数带小数、还有含大括号或注释符号的文字。剩下的一律照你填的写 ——
<b>不知道该填什么的就留空</b>, 留空 = 这一项交回游戏自己决定<br>
· <b>这块的保存按钮和上面是分开的</b>: 上面的「保存全部修改」管不到它, 填完要点它自己的
「💾 保存高级配置」。改过没点保存就切页 / 开服, 一样会被防呆拦下来 (它会报「高级配置」这一组)<br>
· 文件里原本没有这一笔时, 开服器会<b>把它插进对应的段落</b> (段落都没有就整段补一段),
而不是像以前那样"写不进去还没人吭声"; 你手动改完后清空并按保存, 那一笔会<b>退回只写键名不带值</b>
的样子, 也就是交还给游戏默认<br>
· 涉及账号安全的项<b>有意没有放进来</b>: 登录凭据 <code class="k">Login_Token</code>、
服务器书签地址、官方服务器与 Steam 测试分支 —— 这几项就算你手动构造请求, 接口那边也会直接拒
</div></div>

<div class="card"><h2>每个菜单是干什么的</h2>
<div class="desc" style="line-height:2.1">
<b>仪表盘</b> — 开服 / 重启 / 关服, 查看服务器代码和基本信息<br>
<b>一键设置</b> — 最常用的傻瓜开关: 死亡不掉落、建筑无敌、车辆无敌、僵尸不拆家、摔落伤害、组队友伤、空投、出生满技能、<b>无需指南针/GPS/手绘地图也能看方向和地图</b>; 僵尸强度 / 经验倍率 / 物资丰富度 / 天气; 还有 <b>Rocket 指令反馈汉化</b><br>
<b>服务器设置</b> — 服务器名称、地图、人数、端口、PVP/PVE、<b>难度 (换难度会列出到底改了哪些数值)</b>、视角、进服密码、<b>开启作弊 (cheats on)</b>、<b>反作弊开关 (VAC / BattlEye 战眼, v0.1.17 起)</b>; 以及默认收起的<b>红框「启动参数」</b>、<b>开服端版本检查与一键更新</b>、<b>Rocket 安装</b>, 页面最底下是 <b>服务器图标与大厅链接 (图床设置)</b><br>
<b>玩法设置</b> — 进阶参数: 血量、经验、刷怪、刷车、建筑承伤、空投频率、天气等。每一项都标了<b>能填的范围、游戏默认值、0 和 1 分别是什么意思</b>, 超出范围的会被拦下来不让保存; 页面最底下还有一个默认收起的 <b>「🔧 高级配置」(191 项, 官方 Config.txt 里剩下的那些参数, 有关键词筛选)</b><br>
<b>创意工坊</b> — 填模组 ID 自动下载 Steam 创意工坊模组 (已下载的工坊地图会自己出现在「服务器设置」的地图下拉框里)<br>
<b>存档管理</b> — <b>建筑存档 / 玩家存档</b>的备份与删除 (删除前强制先备份, 位置自己选)<br>
<b>控制台</b> — 实时日志和报错、发送命令、<b>在线玩家管理</b> (给物品/车辆、传送、踢出；无敌/隐身由玩家自行使用 /god 与 /vanish 指令)、白天/黑夜等快捷指令<br>
<b>操作日志</b> — 记录你在网页里的每次操作和错误, 出问题先来这里看<br>
<b>文件管理</b> — 直接编辑所有配置文件 (含 Rocket 插件配置)
</div></div>

<div class="card"><h2>服务器启动参数 (服务器设置页那个红框, 点开才有)</h2>
<div class="desc" style="line-height:2.1">
从 <b>v0.1.17</b> 起这一栏改成<b>默认收起</b>的了 (和「玩法设置」页底下的「高级配置」一个样子),
边框是<b>红色</b>的 —— 这不是为了好看: 这一栏里的每一项都会<b>原样拼进开服命令行</b>,
<b>不是开发人员请不要修改其他内容</b>, 改错了服务器可能直接开不起来、朋友进不来。
日常开服联机<b>一个都不用动</b>, 不点开就当它不存在。<br>
启动命令固定为: <code class="k">-nographics -batchmode +Secureserver/存档名</code> (原版方式)<br>
<b>联机只用服务器代码</b> (仪表盘复制): 走 Steam P2P, 不需要 Login_Token、不需要端口映射;<br>
不填 Login_Token 只影响"互联网列表 / 公网 IP 直连", 不影响代码联机<br>
<code class="k">-batchmode</code> — 无窗口后台运行 (推荐勾选)<br>
<code class="k">-nographics</code> — 不加载显卡渲染, 省资源 (推荐勾选)<br>
<code class="k">原版控制台窗口</code> (推荐开启) — 服务器以原版方式启动, 有自己独立的黑色控制台窗口;<br>
　　　　<b>关闭开服器 / 关闭网页后, 服务器会一直在后台运行</b>, 想关服回来点「关服」或在服务器窗口按 Ctrl+C<br>
　　　　关闭此选项则完全后台静默运行, 网页「运行日志」会更完整<br>
<code class="k">关闭 bat 窗口时同时关闭服务器</code> — 默认关闭 (服务器驻留); 打开后关掉 bat 窗口会自动保存并关闭服务器<br>
<code class="k">自定义参数</code> — 其他官方支持的启动参数写在这里, 用空格分隔; 不确定就不要填<br>
<span class="hint">这一块有<b>自己的「保存启动参数」按钮</b>, 和上面的「保存修改」是分开的;
红框那一栏<b>记住你开没点开</b>, 这次点开了, 下次回到这页还是开着的。</span>
</div></div>

<div class="card"><h2>🛡️ 反作弊开关: VAC 与 BattlEye 战眼 (v0.1.17 起)</h2>
<div class="desc" style="line-height:2.1">
「服务器设置」页中间新加了一张 <b>「反作弊 (VAC / BattlEye 战眼)」</b> 卡片, 两个勾<b>默认都是开启的</b>:<br>
· <code class="k">VAC_Secure</code> — Steam 官方的反作弊; <code class="k">BattlEye_Secure</code> — 游戏自带的服务端战眼<br>
· 两项写在当前存档 <code class="k">Config.txt</code> 的 <code class="k">Server</code> 段,
这一块<b>有自己独立的「保存反作弊设置」按钮</b>, 改完要<b>重启服务器</b>才生效<br>
· <b>取消勾选时开服器会先弹框问你一次</b> —— 关闭反作弊可能不安全, 不建议这么做:
关掉之后外挂进服<b>不会被拦</b>, 别人盖的建筑、攒的进度都可能被乱改乱拆。点「还是开着」勾会自动退回去<br>
· 什么时候真的要关: 排查误封, 或者朋友报<b>「未开启 BattlEye (战眼反作弊)」</b>连不进来
(见本页置顶第 ③ 条), 处理完记得开回来<br>
· 这两项<b>以前在「玩法设置」页的网络/安全那一段里</b>, v0.1.17 起已从那一页撤走 ——
同一个键在两个页面各有一份表单值, 后保存的那次会把先改的覆盖回去, 所以只留一个地方能改
</div></div>

<div class="card"><h2>Rocket 插件 (服务器设置页)</h2>
<div class="desc" style="line-height:2.1">
· Unturned 服务器<b>默认不带 Rocket</b>, 需要在「服务器设置」页点「一键安装」(用的是游戏自带的
<code class="k">Extras\\Install Rocket.bat</code>), <b>装完必须重启服务器才会加载插件</b><br>
· 全新存档第一次启动时会自动生成 Rocket 配置文件 (RCON 已按本次密钥自动启用), 无需手动创建<br>
· 之后把插件的 <code class="k">.dll</code> 文件放进 <code class="k">Servers\\你的存档\\Rocket\\Plugins</code> 文件夹<br>
· 插件的配置文件会自动出现在「文件管理」里, 点开即可修改<br>
· 控制台里输入 <code class="k">rocket reload</code> 可以重载插件
</div></div>

<div class="card"><h2>存档备份 / 删除 (存档管理页)</h2>
<div class="desc" style="line-height:2.1">
· 存档文件是<b>加密的</b>, 人工打不开 —— 这一页只告诉你每个文件是什么, 你按名字勾选即可<br>
· <b>建筑存档</b> = <code class="k">Servers\\存档名\\Level\\地图名\\</code>: 房子、地基墙门、场景物件、停着的车、光照等<br>
· <b>玩家存档</b> = <code class="k">Servers\\存档名\\Players\\SteamID_0\\</code>: 物品栏、技能、血量、任务、穿着等<br>
· 备份位置<b>完全由你自己选</b> (点「📂 浏览文件夹」挑一个), 每次备份新建
<code class="k">存档名_备份_年月日_时分秒</code> 子文件夹, 不覆盖上次备份<br>
· 可以<b>整体备份</b>一个地图 / 一个玩家 / 全部玩家, 也可以只勾几个文件<b>单个备份</b><br>
· 文件名带 <code class="k">~</code> 的是游戏自己留的<b>上一次保存的旧版本</b><br>
· <b>删除一定会先备份</b>: 备份成功后才删, 备份失败则一个文件都不动;<br>
　　　　删除只删你勾选的那几个文件, <b>不会删除 Level / Players 文件夹</b>, 空文件夹原样保留<br>
· 删档前请<b>先关服</b> —— 服务器运行中删掉的文件会在关服自动保存时写回去
</div></div>

<div class="card"><h2>Rocket 指令反馈汉化 (一键设置页)</h2>
<div class="desc" style="line-height:2.1">
· 这个开关<b>不是汉化控制台</b>, 服务器日志 / 控制台输出完全不受影响<br>
· 它只汉化<b>玩家或管理员敲完指令之后 Rocket 回的那句话</b>: 例如 /tp、/give、/god 的提示,
玩家进服、权限不足、插件加载完成等反馈 (共 95 条)<br>
· 开启 = 覆盖 <code class="k">Servers\\存档名\\Rocket\\</code> 里的
<code class="k">Rocket.en.translation.xml</code> 与 <code class="k">Rocket.Unturned.en.translation.xml</code>;<br>
· 关掉开关会<b>逐字节还原</b>成英文原文; 改完重启服务器生效<br>
· 存档里还没有这两个文件时开不了 —— 先装 Rocket 前置插件并开一次服, Rocket 会自动生成
</div></div>

<div class="card"><h2>不用道具也能看方向和地图 (一键设置页)</h2>
<div class="desc" style="line-height:2.1">
· <b>无需指南针即可显示方向</b> — 玩家不用捡指南针, 屏幕上一直显示朝向 (北/东/南/西)<br>
· <b>无需GPS即可开启卫星地图</b> — 不用捡 GPS 物品也能打开卫星地图看全图<br>
· <b>无需手绘地图即可开启手绘地图</b> — 不用捡纸质/手绘地图也能打开手绘地图<br>
· 对应 <code class="k">Config.txt</code> 的 <code class="k">Gameplay</code> 段
<code class="k">Compass</code> / <code class="k">Satellite</code> / <code class="k">Chart</code>,
关掉开关即恢复游戏默认 (想更细地调可以去「玩法设置」页, 同一批参数也在那里)<br>
· 改完重启服务器生效
</div></div>

<div class="card"><h2>服务器图标与大厅链接 (服务器设置页 · 最底下的图床设置)</h2>
<div class="desc" style="line-height:2.1">
· 写在 <code class="k">Config.txt</code> 的 <code class="k">Browser</code> 段:
<code class="k">Icon</code> = 玩家进服大厅左上角图标 (建议 64x64),
<code class="k">Thumbnail</code> = 服务器列表小图 (建议 32x32),
<code class="k">Links</code> = 大厅里玩家能点的按钮<br>
· <b>新建存档会自动填入 Dawn Sharkk 默认图标和 GitHub 链接</b>; 已有存档第一次打开「服务器设置」时也会自动补齐一次,
之后你自己改过或清掉的值不会再被覆盖<br>
· 图片必须是能直接访问的<b>图片直链</b> (以 .jpg / .png 结尾的图床地址), 由玩家客户端自行下载;
留空 = 不显示图标<br>
· 链接每行一条, 格式 <code class="k">显示文字 | 网址</code>, 最多 8 条<br>
· 改完重启服务器生效; 想直接看原文可在「文件管理」打开 Config.txt 的 Browser 段
</div></div>

<div class="card"><h2>控制台玩家管理</h2>
<div class="desc" style="line-height:2.1">
「刷新玩家列表」后可对每位玩家: <b>给物品</b>(give) / <b>给车辆</b>(vehicle) / <b>传送到其他玩家身边</b>(teleport) / <b>踢出</b>(kick)<br>
物品和载具 ID 可在游戏内按物品分类查询, 或搜索 "Unturned Item ID"<br>
<span class="hint">这些属于作弊指令, 需要服务器开启作弊 (本工具新建的存档默认已在 Commands.dat 写入 <code class="k">cheats on</code>; 也可在「服务器设置」页勾选开启)。
按钮会自动用玩家的 <b>SteamID</b> 定位玩家, 名字里有中文或空格也不怕匹配失败。<br>
<b>无敌 / 隐身</b>开服器不提供按钮, 需玩家自己在游戏内输入指令: <code class="k">/god</code> (无敌) 和 <code class="k">/vanish</code> (隐身), 再输入一次取消。</span>
</div></div>

<div class="card"><h2>常见问题</h2>
<div class="desc" style="line-height:2.1">
<b>改了配置没生效?</b> 配置修改后必须<b>重启服务器</b>; 而且改之前先关服, 否则服务器退出时会把旧配置写回去<br>
<b>朋友用代码进不来?</b> ① 确认代码是<b>最新一次开服</b>的 (每次开服都会变) ② 双方重启 Steam 再试 ③ 出现 "Lost connection" 多为 Steam P2P 网络中转问题, 让对方隔几分钟重试代码联机<br>
<b>能用 IP+端口 连吗?</b> 不能 —— Unturned 的这种 P2P 服没有局域网/公网 IP 直连方式, IP 直连只适用于<b>官方服务器</b>(需在 Config.txt 填 Steam 官方 <code class="k">Login_Token</code>), 本工具基于方便快捷不做这条路<br>
<b>怎么安全关服?</b> 点「关服」或控制台输入 <code class="k">shutdown</code>, 都会自动保存世界; 强制结束进程才会丢档<br>
<b>存档删错了怎么办?</b> 「存档管理」的删除一定会先把删掉的文件备份到你选的位置 —— 关服状态下把备份文件夹里的
<code class="k">Level</code> / <code class="k">Players</code> 对应文件复制回存档目录即可恢复<br>
<b>服务器图标/链接不显示?</b> ① 地址必须是 <code class="k">http(s)://</code> 开头的<b>图片直链</b>
(网页浏览地址、带 ? 参数的分享链接都不行) ② 改完要<b>重启服务器</b> ③ 图标由玩家客户端下载,
玩家网络访问不了该图床就看不到<br>
<b>服务器代码一直显示"正在启动"?</b> 正常现象 —— 开服器<b>严格要求地图加载到 100%</b> 才显示代码,
每次开服(含重启)都是全新代码, 绝不会把上一局的代码拿来顶上; 等十几秒点右上角「↻ 刷新」即可<br>
<b>给物品/踢出提示找不到玩家?</b> 玩家名里有中文或空格时游戏按名字匹配会失败 ——
「刷新玩家列表」后点按钮, 开服器会自动改用该玩家的 <b>SteamID</b> 定位, 一般不会再失败;
手工敲命令时把名字换成那一串 17 位 SteamID 即可<br>
<b>玩家老是迷路?</b> 打开「一键设置」里的 <b>无需指南针即可显示方向</b> +
<b>无需GPS即可开启卫星地图</b>, 不用捡道具也能看方向和全图<br>
<b>选了别的地图却还是 PEI?</b> 「服务器设置」页的下拉框现在会<b>真正写入</b> <code class="k">Map 地图名</code>
(旧版本只认手动输入框, 所以下拉框选了没反应)。保存后本页会显示真实地图名; 地图名探测不到时
<b>开服会给黄字警告</b>, 不会再一声不响地退回 PEI。创意工坊地图要先在「创意工坊」页订阅才会下载。
另外<b>换地图等于换一张全新的世界</b>, 老地图的建筑仍然存在 <code class="k">Level\\旧地图名</code> 里, 名字改回去就能看到<br>
<b>存档名、地图名能不能用中文?</b> <b>能。</b>「Servers」里<b>已经存在</b>的存档, 文件夹叫中文照样选得中
(v0.1.16 起, 别人给你的存档包不用再改名); 创意工坊地图和 <code class="k">Level</code> 里的地图名叫中文,
下拉框认得、也换得动。<b>只有在这里新建存档时要求英文</b> —— 新名字会原样写进开服命令行
<code class="k">+Secureserver/存档名</code>, 英文最稳。万一某个中文名字的存档出现
「网页显示在运行、点关服却关不掉」, 把 <code class="k">Servers\\那个文件夹</code> 改成英文名就好,
里面的建筑存档和玩家存档都不用动<br>
<b>想调的参数「玩法设置」翻遍了也没有?</b> 拉到那一页<b>最底下</b>, 点开默认收起的
「🔧 高级配置」—— 官方 Config.txt 里剩下的 191 项都在里面, 顶部有关键词筛选框;
那一块<b>有自己独立的保存按钮</b>, 填完别只点上面那个「保存全部修改」<br>
<b>设置改了没保存, 切个页面就白改了?</b> 「服务器设置」和「玩法设置」已经加了防呆, 不用再担心保存按钮藏得深:
只要有改动没保存, 右下角会一直挂着「⚠ 有 N 处修改还没保存」, 改过的卡片描上黄边;
这时候点侧边栏换页、点「▶ 开服 / 🔄 重启」、甚至刷新网页, 都会先弹窗问你要怎么办 ——
<b>💾 先保存再继续</b> (替你按下各块的保存按钮再走) / <b>🗑 取消更改</b> (把页面恢复成保存前的样子再走) /
<b>留在本页</b> (什么都不动)。点右下角的提示条还能直接跳到没保存的那一块。<b>最多点一次保存, 配置不会白改</b><br>
<b>网页报错?</b> 打开「操作日志」看红色错误行, 把它截图反馈
</div></div>
"""
    return shell("help", "帮助", content, "")


# ================================================================ 操作日志页
def page_logs():
    content = f"""
<div class="card"><h2>操作日志</h2>
<div class="desc">自动记录开服/关服、修改配置、登录等操作和错误, 保存在开服器文件夹的
<code class="k">操作日志.txt</code> (超过 2MB 自动滚动备份为 .old)。</div>
<div style="display:flex;gap:10px;margin-top:12px;flex-wrap:wrap;align-items:center">
<div class="pills">
<label><input type="radio" name="logcat" value="" checked onchange="load()"><span>全部</span></label>
<label><input type="radio" name="logcat" value="操作" onchange="load()"><span>操作</span></label>
<label><input type="radio" name="logcat" value="安全" onchange="load()"><span>安全</span></label>
<label><input type="radio" name="logcat" value="错误" onchange="load()"><span>错误</span></label>
</div>
<span style="flex:1"></span>
{check_html(True, ident="autoref", label="自动刷新")}
<button class="btn sm gray" onclick="load()">刷新</button>
<button class="btn sm red" onclick="clearLog()">🗑 清空日志</button>
</div></div>
<div class="card" style="padding:0;overflow:hidden">
<div class="term" id="opbox" style="height:520px;border:0;border-radius:0"></div></div>
"""
    script = r"""
function lineEl(t){
 var div=document.createElement('div');div.className='l';
 if(t.indexOf('[错误]')>=0){div.classList.add('err');}
 else if(t.indexOf('[安全]')>=0){div.style.color='#93c5fd';}
 else if(t.indexOf('[操作]')>=0){div.style.color='#86efac';}
 div.textContent=t;return div;}
async function load(){
 var cat=document.querySelector('input[name=logcat]:checked').value;
 try{
  var r=await fetch('/api/oplog?cat='+encodeURIComponent(cat));
  if(r.status===401){location.href='/login';return;}
  var d=await r.json();
  var box=document.getElementById('opbox');box.innerHTML='';
  if(!d.lines.length){box.innerHTML='<div class="l" style="color:#64748b">暂无日志</div>';return;}
  d.lines.slice().reverse().forEach(t=>box.appendChild(lineEl(t)));
  box.scrollTop=0;
 }catch(e){}}
async function clearLog(){
 if(!confirm('确定清空全部操作日志吗?'))return;
 var r=await post('/api/oplog/clear');
 toast(r.msg,r.ok?0:1);load();}
setInterval(()=>{if(document.getElementById('autoref').checked)load();},3000);
load();
"""
    return shell("log", "操作日志", content, script)


# ================================================================ 文件管理
def page_files():
    rows = "".join(
        f'<tr><td>{esc(f["path"])}</td><td>{esc(file_desc(f["path"]))}</td>'
        f'<td class="hint">{f["size"]} B</td>'
        f'<td><a class="btn sm" href="/edit?path={f["path"]}">编辑</a></td></tr>'
        for f in list_files())
    content = f"""
<div class="card" style="padding:8px 16px">
<table><tr><th>文件</th><th>说明</th><th>大小</th><th></th></tr>{rows}</table></div>
<div class="card"><h2>提示</h2>
<div class="desc" style="line-height:2">
· 插件的配置文件 (<code class="k">Rocket\\Plugins\\*.config.xml</code>) 会自动出现在列表里, 点编辑即可修改<br>
· 编辑器会自动识别 UTF-8 / GBK 编码, 中文保存不乱码<br>
· 修改前请先关服, 防止服务器退出时覆盖你的修改</div></div>
"""
    return shell("files", "文件管理", content, "")


def file_desc(path):
    if path in FILE_DESC:
        return FILE_DESC[path]
    if "Plugins" in path:
        return "🔌 插件配置文件"
    if path.startswith("Rocket/"):
        return "Rocket 配置 / 翻译文件"
    return "配置文件"


def page_edit(path):
    full = safe_path(path)
    if not full:
        return None
    text, enc = read_file(full)
    content = f"""
<div class="card"><h2 style="font-size:15px">{esc(path)}</h2>
<div class="desc">{esc(file_desc(path))} · 检测到编码 {enc} (保存时保持原编码)</div>
<div style="margin-top:12px;display:flex;gap:10px">
<button class="btn big" onclick="save()">💾 保存</button>
<a class="btn big gray" href="/files">← 返回</a></div>
<textarea class="f" id="c" style="min-height:480px;margin-top:12px;white-space:pre">{esc(text)}</textarea></div>
"""
    script = f"""
async function save(){{
 var r=await post('/api/file',{{path:{json.dumps(path)},content:document.getElementById('c').value}});
 toast(r.ok?'✔ 已保存':'✘ '+r.msg,r.ok?0:1);}}
"""
    return shell("files", "编辑 " + path, content, script)


# ================================================================ 登录页 (密钥登录)
def page_login():
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>登录 · Dawn Sharkk</title>
<script>try{{var t=new URLSearchParams(location.search).get('theme')||localStorage.getItem('untheme');
if(t==='light')document.documentElement.classList.add('light');
if(t)localStorage.setItem('untheme',t);}}catch(e){{}}</script>
<style>{CSS}
body{{display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px}}
.login{{width:430px;max-width:94vw;background:rgba(255,255,255,.045);border:1px solid rgba(255,255,255,.09);
backdrop-filter:blur(18px) saturate(140%);-webkit-backdrop-filter:blur(18px) saturate(140%);
border-radius:18px;padding:30px 30px 26px;text-align:center;box-shadow:0 14px 44px rgba(0,0,0,.3)}}
body label.switch{{position:fixed;top:16px;right:18px;z-index:9}}
.logo{{display:flex;gap:12px;align-items:center;justify-content:center;margin-bottom:6px}}
.logo .ic{{width:46px;height:46px;border-radius:12px;background:linear-gradient(135deg,#3b82f6,#8b5cf6);
display:flex;align-items:center;justify-content:center;font-size:24px}}
h2{{font-size:19px}}.desc{{color:var(--sub);font-size:13px;margin:8px 0 18px;line-height:1.9}}
#pw{{text-align:center;font-size:16px;letter-spacing:1px}}
.btn{{width:100%;margin-top:12px}}
.keyhint{{background:var(--codebg);border:1px solid var(--codebd);color:var(--code);
border-radius:8px;padding:8px 12px;font-size:12px;margin-top:14px;line-height:1.8}}
#toast{{position:fixed;top:20px;right:20px}}
</style></head><body>
<div id="toast"></div>
{THEME_SWITCH}
<div class="login">
<img class="logo-img" src="/logo.png" style="width:84px;height:84px" alt="logo">
<h2 style="margin-top:12px">Dawn Sharkk</h2><div class="desc">Unturned 开服器 v{VERSION} · by Pippl</div>
<div class="desc" id="tip">正在通过网址密钥自动登录…</div>
<div id="box" style="display:none">
<input class="f" id="pw" placeholder="粘贴或输入密钥" maxlength="32"
 onkeydown="if(event.key==='Enter')doLogin()" autofocus>
<button class="btn" onclick="doLogin()">登 录</button>
<div class="keyhint">密钥在 DawnSharkk.exe 的黑色命令框里 (命令框里那行<b>完整网址</b>已自动复制到剪贴板, 直接 Ctrl+V 到地址栏即可自动登录)<br>
注意: 网址自动登录只在开服器启动后 <b>30 分钟</b>内有效, 超时请手动输入密钥; 密钥每次启动开服器都会变化</div>
</div>
</div>
<script>
try{{var t0=new URLSearchParams(location.search).get('theme')||localStorage.getItem('untheme');
if(t0==='light')document.documentElement.classList.add('light');
if(t0)localStorage.setItem('untheme',t0);}}catch(e){{}}
function syncSwitch(){{var i=document.getElementById('input');
if(i)i.checked=!document.documentElement.classList.contains('light');}}
function onThemeSwitch(cb){{var h=document.documentElement;
h.classList.toggle('light',!cb.checked);
localStorage.setItem('untheme',cb.checked?'dark':'light');}}
function toast(m,err){{var d=document.createElement('div');d.className='tmsg'+(err?' err':'');
d.textContent=m;document.getElementById('toast').appendChild(d);setTimeout(()=>d.remove(),3000);}}
async function submit(pw,via){{
 try{{var r=await fetch('/api/login',{{method:'POST',headers:{{'Content-Type':'application/json'}},
  body:JSON.stringify({{password:pw,via:via}})}});
 if(r.status===200){{location.href='/';return true;}}
 return await r.json();}}catch(e){{return {{msg:'网络异常, 请重试'}};}}}}
function showBox(msg,err){{document.getElementById('box').style.display='';
 document.getElementById('tip').innerHTML=msg?'<b>需要输入登录密钥</b>':'请输入<b>登录密钥</b>进入管理页面';
 if(msg)toast(msg,err===undefined?1:err);
 var p=document.getElementById('pw');if(p)p.focus();}}
async function doLogin(){{
 var pw=document.getElementById('pw').value.trim();
 if(!pw){{toast('请输入密钥',1);return;}}
 var r=await submit(pw,'manual');
 if(r!==true)toast((r&&r.msg)||'密钥不对, 请查看命令框里的登录密钥',1);}}
var q=new URLSearchParams(location.search), k=q.get('key');
if(k){{try{{history.replaceState({{}},'',location.pathname);}}catch(e){{}}
 submit(k,'url').then(function(r){{if(r!==true)showBox((r&&r.msg)||'自动登录失败, 请手动输入密钥');}});}}
else showBox('');
syncSwitch();
</script></body></html>"""


# ================================================================ 开服端下载/更新前端
# DL_CORE_JS = steamcmd 进度组件 (初始设置的下载面板 + 服务器设置的更新卡片都用它)
# DEDI_JS    = 再加上初始设置页专用的那套 (选目录 / 扫描本机 / 开始下载)
DL_CORE_JS = r"""
/* ---- steamcmd 进度组件: 初始设置页的「下载开服端」和服务器设置页的「更新开服端」共用 ----
   页面上没有对应的块就自动跳过, 所以两个页面可以用同一套轮询代码。 */
var _dlt=null;
function dlEl(id){return document.getElementById(id);}
function dlTxt(id,t,cls){var e=dlEl(id);if(!e)return;if(cls!=null)e.className=cls;e.textContent=t||'';}
function dlVis(id,v){var e=dlEl(id);if(e)e.style.display=v;}
function fmtBytes(n){n=Number(n)||0;var u=['B','KB','MB','GB'],i=0;
 while(n>=1024&&i<3){n/=1024;i++;}
 return (i?n.toFixed(1):n.toFixed(0))+' '+u[i];}
async function dlGet(){
 try{var r=await fetch('/api/serverdedi/status');return await r.json();}catch(e){return null;}}
function dlPoll(){if(_dlt)clearInterval(_dlt);_dlt=setInterval(dlTick,1000);dlTick();}
function dlHalt(){if(_dlt){clearInterval(_dlt);_dlt=null;}}
async function dlTick(){var s=await dlGet();if(s)dlDraw(s);}
function dlDraw(s){
 var st=s.state||'idle';
 if(st==='running')dlVis('dlbox','block');   /* 面板默认收起, 一有任务就自己摊开给人看进度 */
 if(st==='running'||s.stage)dlVis('dlbar','block');
 var p=Math.max(0,Math.min(100,Number(s.pct)||0)),f=dlEl('dlfill');
 if(f)f.style.width=p.toFixed(1)+'%';
 var line=(s.stage||'等待中')+'   '+p.toFixed(1)+'%';
 if(s.total)line+='   '+fmtBytes(s.got)+' / '+fmtBytes(s.total);
 if(s.speed&&st==='running')line+='   '+fmtBytes(s.speed)+'/s';
 dlTxt('dlstage',(s.detail?(line+'\n'+s.detail):line),null);
 var lg=(s.log||[]).slice(-9).join('\n'),el=dlEl('dllog');
 if(lg&&el){el.textContent=lg;el.style.display='block';el.scrollTop=el.scrollHeight;}
 var run=(st==='running'),g=dlEl('dlgo');
 if(g){g.style.display=run?'none':'inline-block';g.disabled=false;}
 dlVis('dlcancel',run?'inline-block':'none');
 if(st==='done'){
  dlHalt();dlVis('dlok','flex');dlTxt('dlres',s.result,null);
  dlTxt('dlmsg',s.msg||'开服端下载完成','hint ok');
 }else if(st==='error'||st==='cancelled'){
  dlHalt();dlTxt('dlmsg',s.msg,'hint'+(st==='error'?' err':''));
 }
}
function dlStartReq(url,body){
 var g=dlEl('dlgo');if(g)g.disabled=true;
 dlTxt('dlmsg','','hint');dlVis('dlok','none');
 return post(url,body).then(function(r){
  if(!r||!r.ok){
   if(g)g.disabled=false;
   return r;
  }
  dlVis('dlbar','block');dlPoll();return r;});}
/* 一键更新当前绑定的开服端 (steamcmd 会自己比对 Steam 上的最新构建) */
async function updateDedi(){
 var r=await dlStartReq('/api/serverdedi/update',{});
 if(!r)return;
 if(r.ok)toast('开始检查了: steamcmd 会先向 Steam 核对最新构建, 本来就最新的话几十秒就完');
 else{dlTxt('dlmsg',r.msg||'没能开始更新','hint err');toast(r.msg||'没能开始更新',1);}
}
async function cancelDl(){
 var r=await post('/api/serverdedi/cancel',{});
 toast((r&&r.msg)||'已请求取消',r&&r.ok?0:1);
 setTimeout(dlTick,700);
}
"""


DEDI_JS = DL_CORE_JS + r"""
function openDl(){dlEl('dlbox').style.display='block';dlEl('dldest').focus();}
/* 非英文字符直接拦下: steamcmd 碰到中文路径是当场 Fatal Error, 不是慢, 是根本不跑 */
function dlDestCheck(){
 var e=dlEl('dldest'),m=dlEl('dldestmsg');if(!e||!m)return true;
 var v=(e.value||'').trim();
 if(v&&/[^\x20-\x7e]/.test(v)){
  m.textContent='⚠ 路径里有中文或其他非英文字符, steamcmd 会直接报错退出 (和网速无关)。' +
                 '换成纯英文的文件夹, 例如 G:\\U3DS';
  return false;}
 m.textContent='';return true;}
async function reScan(){
 var m=dlEl('scanmsg');m.className='hint';m.textContent='正在重新扫描本机…';
 var r=await post('/api/serverdedi/scan',{});
 var f=(r&&r.found)||[];
 if(f.length){m.textContent='找到 '+f.length+' 个开服端, 正在刷新上面的列表…';
  setTimeout(function(){location.reload();},800);}
 else m.textContent='还是没找到 —— 优先点「🛒 在 Steam 商店打开开服端」装一次, ' +
                    '或点「🔻 用 SteamCMD 下载」, 也可以在上面手动粘贴路径';
}
async function usePath(p){
 if(!p){toast2('还不知道是哪个目录',1);return false;}
 var r=await post('/api/serverdedi/use',{path:p});
 if(!r||!r.ok){toast2((r&&r.msg)||'这个目录用不了',1);return false;}
 toast2('已把开服端设为游戏目录, 正在进入第 2 步…');
 setTimeout(function(){location.reload();},700);
 return true;
}
function useDl(){return usePath(dlEl('dlres').textContent||dlEl('dldest').value.trim());}
async function startDl(force){
 var g=dlEl('dlgo');g.disabled=true;
 var dest=(dlEl('dldest').value||'').trim();
 if(!dest){toast2('先填一个下载位置',1);g.disabled=false;return;}
 if(!dlDestCheck()){toast2('下载位置只能有英文字母和数字, 改一下再点',1);g.disabled=false;return;}
 dlEl('dlmsg').textContent='';dlEl('dlmsg').className='hint';
 try{
  var r=await post('/api/serverdedi/start',{dest:dest,force:!!force});
  if(!r||!r.ok){
   var f=(r&&r.exists)||[];
   if(f.length){
    var ls=f.map(function(x,i){
      return (i+1)+'. '+x.path+(x.instances?('   里面已有 '+x.instances+' 个存档'):'');}).join('\n');
    var c=await pick({icon:'🔎',title:'本机已经有开服端了',
      body:'点下载之前又查了一遍这台电脑, 找到这些现成的 Unturned 开服端 (U3DS):\n\n'+ls+
           '\n\n用现成的就行, 不用再下 1.5 GB。确实想要一份新的, 点「仍然下载」。',
      btns:[{t:'就用第 1 个',v:'use'},{t:'仍然下载',v:'go',cls:'gray'},{t:'先不下了',v:'stay',cls:'gray'}],
      cancel:'stay'});
    g.disabled=false;
    if(c==='use')await usePath(f[0].path);
    else if(c==='go')await startDl(true);
    return;
   }
   toast2((r&&r.msg)||'没能开始下载',1);g.disabled=false;return;
  }
  toast2('开始下载了, 别关这个页面, 进度在下面');
  dlEl('dlok').style.display='none';dlEl('dlbar').style.display='block';
  dlPoll();
 }catch(e){toast2('请求失败: '+e+' (开服器窗口还开着吗?)',1);}
 g.disabled=false;
}
async function cancelDl(){
 var r=await post('/api/serverdedi/cancel',{});
 toast2((r&&r.msg)||'已请求取消',r&&r.ok?0:1);
 setTimeout(dlTick,700);
}
function browseDl(){
 var m=document.createElement('div');m.className='mask';
 var box=document.createElement('div');box.className='modal-box wide';
 var h=document.createElement('h3');h.textContent='📂 选一个放开服端的文件夹';
 var tip=document.createElement('div');tip.className='mbd';
 tip.textContent='开服端文件会直接铺在你选定的这个文件夹里 (不会删掉里面已有的东西)。' +
   '建议单独新建一个空的英文文件夹, 例如 G:\\U3DS —— 路径里有中文 steamcmd 会拒绝下载。';
 var path=document.createElement('div');path.className='dpath';
 var list=document.createElement('div');list.className='dirlist';
 var bt=document.createElement('div');bt.className='mbtns';
 function mk(t,cls){var b=document.createElement('button');b.className=cls;b.textContent=t;
  bt.appendChild(b);return b;}
 var up=mk('⬆ 上一级','btn gray'),no=mk('取消','btn gray'),ok=mk('✔ 就放在这里','btn');
 box.appendChild(h);box.appendChild(tip);box.appendChild(path);box.appendChild(list);box.appendChild(bt);
 m.appendChild(box);document.body.appendChild(m);
 var st={cwd:'',parent:''};
 function show(p){
  list.textContent='加载中…';
  post('/api/dirs',{path:p}).then(function(d){
   list.textContent='';
   if(!d||!d.ok){list.textContent=(d&&d.msg)||'打不开该文件夹';return;}
   st={cwd:d.cwd,parent:d.parent};
   path.textContent=d.cwd?('当前文件夹: '+d.cwd):'先选一个盘:';
   if(!d.dirs.length){list.textContent='这个里面没有子文件夹 —— 直接点「✔ 就放在这里」选它。';return;}
   d.dirs.forEach(function(x){
    var b=document.createElement('button');b.className='ditem';b.textContent='📁 '+x.name;
    b.onclick=function(){show(x.path);};list.appendChild(b);});});
 }
 up.onclick=function(){show(st.parent||'');};
 no.onclick=function(){m.remove();};
 ok.onclick=function(){
  if(!st.cwd){toast('先进入一个文件夹再确定',1);return;}
  dlEl('dldest').value=st.cwd;dlEl('dldest').focus();dlDestCheck();m.remove();};
 m.addEventListener('click',function(e){if(e.target===m)m.remove();});
 show('');
}
(function(){
 var b=dlEl('dlbox');
 if(!b)return;
 var s2=dlEl('step2');
 // 第 2 步已经解锁说明这个开服端正在用了, 就别再把上次「下载完成」的面板弹出来挡路
 var pending=s2&&s2.style.display!=='block';
 dlGet().then(function(s){
  if(!s)return;
  if(s.state==='running'){b.style.display='block';dlPoll();}
  else if(s.state==='done'&&pending){b.style.display='block';dlDraw(s);}
  else if(s.state==='error'||s.state==='cancelled')dlDraw(s);
 });
})();
"""


# ================================================================ 初始设置向导 (游戏目录 + 存档)
SETUP_CSS = """
body{background:var(--bg);color:var(--txt);font:15px/1.65 "Microsoft YaHei",sans-serif;
display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px}
.wiz{width:680px;max-width:96vw}
.logo{display:flex;gap:12px;align-items:center;margin-bottom:18px}
.logo .ic{width:44px;height:44px;border-radius:12px;background:linear-gradient(135deg,#3b82f6,#8b5cf6);
display:flex;align-items:center;justify-content:center;font-size:23px}
.step{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:24px 26px;margin-bottom:14px}
h2{font-size:18px;margin-bottom:4px}.desc{color:var(--sub);font-size:13px;margin-bottom:14px}
.dirbtn{display:block;width:100%;text-align:left;background:var(--bg);border:1px solid var(--line);
color:var(--txt);border-radius:9px;padding:10px 14px;margin-bottom:8px;cursor:pointer;font-size:14px;transition:.15s}
.dirbtn:hover{border-color:var(--acc)}
.dirbtn b{color:var(--acc2)}
.instbtn{display:flex;justify-content:space-between;align-items:center}
input.f,select.f{width:100%;background:var(--bg);color:var(--txt);border:1px solid var(--line);
border-radius:8px;padding:10px 12px;font-size:14px;outline:0}
input.f:focus{border-color:var(--acc)}
label.r{display:flex;gap:10px;align-items:center;background:var(--bg);border:1px solid var(--line);
border-radius:9px;padding:11px 14px;margin-bottom:8px;cursor:pointer;transition:.15s}
label.r:hover{border-color:var(--acc)}
label.r input{accent-color:var(--acc)}
.btn{background:var(--acc);color:#fff;border:0;border-radius:9px;padding:10px 24px;cursor:pointer;font-size:15px}
.btn.gray{background:var(--gray)}
.hint{color:var(--sub);font-size:12px;margin-top:8px}
.ok{color:var(--ok)}.err{color:var(--bad)}
#toast{position:fixed;top:20px;right:20px}
body label.switch{position:fixed;top:16px;right:18px;z-index:9}
.dedi{margin-top:16px;border:1px dashed var(--line);border-radius:12px;padding:14px 16px;background:var(--bg)}
.dedi.warn{border-style:solid;border-color:rgba(248,113,113,.45);box-shadow:0 0 0 3px rgba(248,113,113,.08)}
.dedi .dt{font-size:14.5px;margin-bottom:6px}
.dedi .dd{color:var(--sub);font-size:12.5px;line-height:1.85}
.dedi .dd code{color:var(--acc2)}
.drow{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:10px}
.drow input.f{flex:1;min-width:240px;margin:0}
.btn.sm{padding:9px 15px;font-size:13px}
.btn:disabled{opacity:.5;cursor:not-allowed}
.dlbox{margin-top:12px;padding-top:12px;border-top:1px solid var(--line)}
.bar{height:9px;border-radius:999px;background:var(--line);overflow:hidden;margin:12px 0 6px}
.bar i{display:block;height:100%;width:0;border-radius:999px;transition:width .6s;
background:linear-gradient(90deg,#38bdf8,#8b5cf6,#38bdf8);background-size:200% 100%;
animation:flow 2.4s linear infinite}
@keyframes flow{to{background-position:200% 0}}
.dllog{max-height:132px;overflow:auto;background:var(--bg);border:1px solid var(--line);border-radius:9px;
padding:9px 11px;font:11.5px/1.7 Consolas,monospace;color:var(--sub);white-space:pre-wrap;
word-break:break-all;margin:8px 0 0}
"""


def page_setup():
    bound = game_dir()
    bad_dir = check_game_dir(bound) if bound else ""
    dir_note = "" if bad_dir else client_soft_note(bound)
    gdir = None if bad_dir else bound
    detected = detect_game_dirs()
    if gdir and os.path.normcase(gdir) not in map(os.path.normcase, detected):
        detected.insert(0, gdir)
    insts = list_instances() if gdir else []
    step2_visible = bool(gdir)

    dir_btns = "".join(
        f'<button class="dirbtn" onclick="pickDir(this.dataset.p)" data-p="{esc(d)}"><b>{esc(d)}</b></button>'
        for d in detected[:6])
    _names = [i["name"] for i in insts]
    _pre = instance() if instance() in _names else (_names[0] if _names else "__new__")
    inst_rows = "".join(
        f'<label class="r"><input type="radio" name="inst" value="{esc(i["name"])}"'
        f'{" checked" if i["name"] == _pre else ""}><span><b>{esc(i["name"])}</b>'
        + ('<span class="hint" style="margin-left:8px">· 名字含中文, 可以用</span>'
           if not ascii_path(i["name"]) else "")
        + f'</span><span style="margin-left:auto" class="hint">上次运行 {esc(i["time"])}</span></label>'
        for i in insts)

    # 第 1 步默认只推商店那条路; steamcmd 面板收起来, 点「备用」按钮或真有任务在跑时才摊开
    has_dedi = bool(gdir or detected)
    saved_dest = _settings.get("dedi_dest") or ""
    # steamcmd 只认英文路径, 上次记的中文位置不能再用
    default_dest = saved_dest if saved_dest and ascii_path(saved_dest) else dedi_default_dest()
    dest_bad = "" if ascii_path(default_dest) else (
        "⚠ 这个默认位置的路径里有中文, steamcmd 不肯往里下。点「📂 浏览文件夹」或直接改成一个"
        "纯英文文件夹, 例如 " + default_dest[:2] + "\\U3DS")
    dedi_cls = "" if has_dedi else " warn"
    dedi_title = ("上面列表里没有你要的目录?" if has_dedi else "本机没有检测到 Unturned 开服端")
    dedi_dd = (
        "开服用的是 Steam 免费提供的 <code>Unturned - Dedicated Server</code> (大家都叫它 U3DS, "
        "文件夹里有 Unturned.exe), 跟平时玩游戏的 Unturned 客户端不是同一个东西。"
        + ("上面这些应该都是, 点一下就填好了; 想要一份全新的, 优先点「🛒 在 Steam 商店打开开服端」装 "
           "(免费, 走 Steam 自己的下载器最稳), 装完点「🔄 重新检测本机」就能选到; "
           "不想让它进你的游戏库, 再用备用的「🔻 用 SteamCMD 下载」。"
           if has_dedi else
           ("这个版本不带下载功能: 点「🛒 在 Steam 商店打开开服端」→ 在商店页点安装 (免费) → "
            "装完回到这一页点「🔄 重新检测本机」, 直接选它就行。"
            if LITE else
            "最省事的走法: 点「🛒 在 Steam 商店打开开服端」, 在商店页点安装 (免费, 会进你的 Steam 库), "
            "装完回到这一页点「🔄 重新检测本机」就能选到它。商店那条路走不动 (没装 Steam 客户端、"
            "不想让它进游戏库) 再用备用的「🔻 用 SteamCMD 下载」—— 开服器自带工具, "
            "匿名下到你自己挑的英文文件夹, 不登录你的账号、不碰你的游戏库。")))
    store_btn = ('<a class="btn" href="' + DEDI_STORE_URL +
                 '" target="_blank" rel="noopener noreferrer" title="在新标签页打开 Steam 商店, '
                 '这个页面不会跳走">🛒 在 Steam 商店打开开服端 (推荐)</a>')
    dedi_dl_btn = ("" if LITE else
                   '<button class="btn gray" onclick="openDl()">🔻 用 SteamCMD 下载 (备用)</button>')
    dl_html = ("" if LITE else f"""<div class="dlbox" id="dlbox" style="display:none">
<div class="dd"><b>备用方案</b> (优先还是上面那个商店按钮, Steam 自己的下载器最稳)。
这条路是让开服器替你下, 不进你的 Steam 库。<br>
下载位置: 默认放在开服器自己这个文件夹里 (整套绿色便携, 不想要了直接删文件夹),
也可以「📂 浏览文件夹」选到别的盘。<b>steamcmd 已经自带在包里</b>, 不用先下工具,
点开始就直接进 1.5 GB 开服端 —— 全程走 Valve 官方服务器, 不登录你的 Steam 账号、不碰你的游戏库;
下不动就先开个 Steam 加速器再重试。<br>
<b>位置必须全是英文</b> (字母、数字、- 和 _): Valve 的 steamcmd 一看路径里有中文就直接报错退出,
和网速无关, 加速器帮不上。</div>
<div class="drow"><input class="f" id="dldest" value="{esc(default_dest)}" spellcheck="false"
 placeholder="例如 G:\\U3DS (只能有英文字母和数字)" oninput="dlDestCheck()">
<button class="btn gray" onclick="browseDl()">📂 浏览文件夹</button></div>
<div class="hint err" id="dldestmsg" style="margin-top:2px">{esc(dest_bad)}</div>
<div class="drow"><button class="btn" id="dlgo" onclick="startDl(false)">▶ 开始下载</button>
<button class="btn gray" id="dlcancel" onclick="cancelDl()" style="display:none">✖ 取消下载</button>
<span class="hint" id="dlmsg"></span></div>
<div class="bar" id="dlbar" style="display:none"><i id="dlfill"></i></div>
<div class="hint" id="dlstage" style="white-space:pre-line"></div>
<pre class="dllog" id="dllog" style="display:none"></pre>
<div class="drow" id="dlok" style="display:none">
<button class="btn" onclick="useDl()">✔ 就用这个开服端, 继续第 2 步</button>
<span class="hint" id="dlres"></span></div>
</div>""")
    dedi_card = f"""<div class="dedi{dedi_cls}">
<div class="dt">🛠 <b>{dedi_title}</b></div>
<div class="dd">{dedi_dd}</div>
<div class="drow">{store_btn}{dedi_dl_btn}
<button class="btn gray" onclick="reScan()">🔄 重新检测本机</button>
<span class="hint" id="scanmsg"></span></div>
{dl_html}
</div>"""

    # 已经绑到游戏本体上的老用户: 先说清楚为什么被退回第 1 步
    bad_card = f"""<div class="step" style="border-color:rgba(248,113,113,.45)">
<h2 class="err">⚠ 之前绑的目录不能开服</h2>
<div class="desc"><b>{esc(bound)}</b><br>{esc(bad_dir)}</div>
<div class="hint">已经在游戏本体的 Servers 文件夹里建过存档? 把那个存档文件夹整个剪切到开服端的
Servers 下面, 再重新设置一次就能接着用, 数据不会丢。</div>
</div>""" if bad_dir else ""

    # 目录能用、但里面留着本体的文件: 黄字说清楚, 别等开服崩了才发现
    note_card = f"""<div class="step" style="border-color:rgba(251,191,36,.45)">
<h2 style="color:#fbbf24">🟡 这个目录能用, 但有句提醒</h2>
<div class="desc">{esc(dir_note)}</div>
</div>""" if dir_note else ""

    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>初始设置 · Dawn Sharkk</title>
<script>try{{var t=new URLSearchParams(location.search).get('theme')||localStorage.getItem('untheme');
if(t==='light')document.documentElement.classList.add('light');
if(t)localStorage.setItem('untheme',t);}}catch(e){{}}</script>
<style>{CSS}{SETUP_CSS}</style></head><body>
<div id="toast"></div>
{THEME_SWITCH}
<div class="wiz">
<div class="logo"><img class="logo-img" src="/logo.png" style="width:72px;height:72px" alt="logo">
<div><b style="font-size:20px">Dawn Sharkk</b>
<div class="desc">首次使用, 两步完成设置 (保存在本文件夹 settings.json)
{' · 简装版 v' + VERSION + ' (不含开服端下载)' if LITE else ' · 完整版 v' + VERSION}</div></div></div>
{bad_card}
{note_card}
<div class="step" id="step1">
<h2>第 1 步 · 选择游戏目录</h2>
<div class="desc">就是 Unturned 服务器 (U3DS) 所在的文件夹, 里面应该有 Unturned.exe。下面是自动探测到的位置:</div>
{dir_btns or '<div class="desc err">没有自动找到, 请手动粘贴路径</div>'}
<label class="f" style="display:block;margin:12px 0 4px;color:var(--sub);font-size:13px">或手动输入游戏目录完整路径:</label>
<input class="f" id="gdir" placeholder="例如 E:\\SteamLibrary\\steamapps\\common\\U3DS"
 value="{esc(bound or '')}">
<div style="margin-top:12px"><button class="btn" onclick="pickDir(document.getElementById('gdir').value.trim())">✔ 确认这个目录</button>
<span id="s1msg" class="hint"></span></div>
<div class="hint">提示: 目录确认后会列出该游戏里已有的服务器存档。别选成平时玩游戏的 Unturned 本体 ——
两个文件夹里都有 Unturned.exe, 但用本体开服会直接报错, 开服器现在会拦下来</div>
{dedi_card}
</div>

<div class="step" id="step2" style="display:{'block' if step2_visible else 'none'}">
<h2>第 2 步 · 选择或创建服务器存档</h2>
<div class="desc">存档 = 一个独立的服务器实例 (有自己的地图、玩家数据、配置)。Servers 文件夹里每个子文件夹都是一个存档。
<b>别人给你的存档、或者游戏里建的存档, 文件夹名是中文也能直接选</b>; 只有在这里<b>新建</b>存档时
才要求用英文 (新名字会写进开服命令行 <code class="k">+Secureserver/存档名</code>, 英文最稳)。
万一某个名字带中文的存档出现「网页显示在运行却关不掉服」, 把 <code class="k">Servers\\那个文件夹</code>
改名成英文就行, 里面的数据不用动。</div>
<div id="instlist">{inst_rows or '<div class="hint">这个游戏目录下还没有任何存档</div>'}</div>
<label class="r"><input type="radio" name="inst" value="__new__"{" checked" if _pre == "__new__" else ""}>
<span><b>创建新存档</b></span></label>
<div id="newbox" style="display:{'block' if not insts else 'none'};margin:10px 0 4px 10px">
<input class="f" id="newname" placeholder="给新存档起个名字 (英文/数字, 例如 myserver)" maxlength="32"
 onkeydown="if(event.key==='Enter')chooseInst()">
<div class="hint">创建后会自动生成基础配置 (默认地图 PEI, 24 人, 自动分配端口)</div></div>
<div style="margin-top:14px"><button class="btn" id="donebtn" onclick="chooseInst()">完成设置, 进入开服器</button>
<span id="s2msg" class="hint"></span></div>
</div>
</div>
<script>{SHELL_JS}
function toast2(m,err){{var d=document.createElement('div');d.className='tmsg'+(err?' err':'');
d.textContent=m;document.getElementById('toast').appendChild(d);setTimeout(()=>d.remove(),3000);}}
document.querySelectorAll('input[name=inst]').forEach(r=>r.addEventListener('change',()=>{{
 document.getElementById('newbox').style.display=
  (document.querySelector('input[name=inst]:checked').value==='__new__')?'block':'none';}}));
async function pickDir(p){{
 if(!p){{toast2('请输入或选择一个目录',1);return;}}
 try{{
  var r=await post('/api/setup',{{game_dir:p}});
  if(!r.ok){{var msg=document.getElementById('s1msg');
   msg.textContent=r.msg||'验证失败';msg.className='hint err';return;}}
  location.reload();
 }}catch(e){{toast2('请求失败: '+e+' (开服器窗口还开着吗?)',1);}}}}
async function chooseInst(){{
 var btn=document.getElementById('donebtn');btn.disabled=true;
 try{{
  var sel=document.querySelector('input[name=inst]:checked');
  if(!sel){{toast2('请先选择一个存档, 或勾选"创建新存档"',1);return;}}
  var name=sel.value==='__new__'?document.getElementById('newname').value.trim():sel.value;
  if(sel.value==='__new__'&&!name){{toast2('先给新存档起个名字 (英文/数字)',1);
   document.getElementById('newname').focus();return;}}
  var r=await post('/api/setup/instance',{{name:name}});
  if(r&&r.ok){{
   if(sel.value==='__new__'&&r.rocket&&!r.rocket.installed){{
    var go=await rocketChoice('服务器存档创建成功 🎉',
     '未检测到 Rocket 前置插件。如需使用更多功能请开启 Rocket 前置插件 '+
     '(加载 .dll 插件、RCON 指令、玩家管理等)。将运行游戏自带 Extras 里的 Install Rocket.bat 安装; '+
     '点「忽略」直接进入开服器, 之后在「服务器设置」页随时可以开启。');
    if(go)await doRocketInstall();}}
   location.href='/';return;}}
  toast2((r&&r.msg)||'操作失败, 请重试',1);
 }}catch(e){{toast2('请求失败: '+e+' (开服器窗口还开着吗?)',1);}}
 finally{{btn.disabled=false;}}}}
{DEDI_JS}
</script></body></html>"""


# ================================================================ HTTP
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _html(self, body, code=200):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, to):
        self.send_response(302)
        self.send_header("Location", to)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        try:
            return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except Exception:
            return {}

    def _authed(self):
        m = re.search(r"sess=([A-Za-z0-9]{16,64})", self.headers.get("Cookie", "") or "")
        return bool(m and m.group(1) == _settings.get("session"))

    def _unauth_json(self):
        data = b'{"ok":false,"auth":true,"msg":"\xe6\x9c\xaa\xe7\x99\xbb\xe5\xbd\x95"}'
        self.send_response(401)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        try:
            self._route_get()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass
        except Exception as e:
            oplog("错误", f"处理请求 GET {self.path} 出错: {exc_summary(e)}")
            try:
                self._html("<h1>开服器内部错误, 详情见「操作日志」</h1>", 500)
            except OSError:
                pass

    def do_POST(self):
        try:
            self._route_post()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass
        except Exception as e:
            oplog("错误", f"处理请求 POST {self.path} 出错: {exc_summary(e)}")
            try:
                self._json({"ok": False, "msg": "开服器内部错误, 详情见「操作日志」"})
            except OSError:
                pass

    def _route_get(self):
        u = urlparse(self.path)
        if u.path == "/logo.png":
            p = os.path.join(BASE_DIR, "logo.png")
            if os.path.isfile(p):
                with open(p, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
            return
        if u.path == "/login":
            return self._html(page_login())
        if u.path == "/api/logout":
            _settings["session"] = None
            save_settings()
            oplog("操作", "退出登录")
            return self._redirect("/login")
        if not self._authed():
            if u.path.startswith("/api/"):
                return self._unauth_json()
            key = (parse_qs(u.query).get("key") or [""])[0]
            return self._redirect("/login" + (f"?key={quote(key, safe='')}" if key else ""))
        if u.path == "/api/status":
            _push_log_tails()   # 仪表盘轮询时同步日志, 确保 Server Code 及时被抓取
            running, pid = server_running()
            info = cmd_info()
            return self._json({
                "running": running, "pid": pid, "port": info["port"],
                "name": info["name"], "map": info["map"], "mode": info["mode"],
                "code": _server_code[0] if running else None,
                "setup": setup_done(),
                "rocket": rocket_status(), "cheat": cheat_enabled(),
                "maxplayers": info["maxplayers"],
                "instance": instance(), "game_dir": game_dir()})
        if u.path == "/setup":
            return self._html(page_setup())
        if u.path == "/api/serverdedi/status":
            return self._json(dl_snapshot())
        if not setup_done():
            return self._redirect("/setup")
        if u.path == "/":
            return self._html(page_dash())
        if u.path == "/quick":
            return self._html(page_quick())
        if u.path == "/commands":
            return self._html(page_commands())
        if u.path == "/gameplay":
            return self._html(page_gameplay())
        if u.path == "/workshop":
            return self._html(page_workshop())
        if u.path == "/saves":
            return self._html(page_saves())
        if u.path == "/console":
            return self._html(page_console())
        if u.path == "/logs":
            return self._html(page_logs())
        if u.path == "/help":
            return self._html(page_help())
        if u.path == "/files":
            return self._html(page_files())
        if u.path == "/edit":
            qs = parse_qs(u.query)
            page = page_edit(qs.get("path", [""])[0])
            return self._html(page if page else "<h1>文件不存在</h1>", 200 if page else 404)
        if u.path == "/api/rcon/diag":
            why, ev = rcon_diag()
            return self._json({"ok": bool(ev["listening"]),
                               "listening": bool(ev["listening"]),
                               "running": server_running()[0], "msg": why,
                               "evidence": ev["evidence"],
                               "rocket": rocket_status()})

        if u.path == "/api/console":
            _push_log_tails()
            after = int(parse_qs(u.query).get("after", ["0"])[0])
            sd = server_dir()
            has_clog = bool(sd) and os.path.isfile(os.path.join(sd, "console.log"))
            has_rlog = bool(sd) and os.path.isfile(os.path.join(sd, "Rocket", "Logs", "Rocket.log"))
            with _proc_lock:
                lines = [d for d in _console if d["i"] > after]
                last = _console_idx[0]
            return self._json({"lines": lines, "last": last,
                               "src": {"console": has_clog, "rocket": has_rlog}})
        if u.path == "/api/oplog":
            cat = parse_qs(u.query).get("cat", [""])[0]
            lines = [l for l in read_oplog() if not cat or f"[{cat}]" in l]
            return self._json({"lines": lines})
        return self._html("<h1>404</h1>", 404)

    def _route_post(self):
        u = urlparse(self.path)
        body = self._body()

        if u.path == "/api/login":
            import hmac as _hmac
            raw_pw = body.get("password")
            pw = raw_pw.strip() if isinstance(raw_pw, str) else ""
            auto = body.get("via") == "url"          # 网址 ?key= 自动登录
            if auto and pw and not key_auto_ok():
                oplog("安全", "自动登录已过期(超过 30 分钟), 需手动输入密钥")
                return self._json({"ok": False, "expired": True,
                                   "msg": "自动登录已超过 30 分钟, 请手动输入密钥"}, 401)
            if pw and _hmac.compare_digest(pw.encode("utf-8"), login_key().encode("utf-8")):
                token = "".join(random.choices(string.ascii_letters + string.digits, k=32))
                _settings["session"] = token
                save_settings()
                oplog("安全", "登录成功")
                data = b'{"ok":true}'
                self.send_response(200)
                self.send_header("Set-Cookie",
                                 f"sess={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000")
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                oplog("安全", "登录失败(密钥错误)")
                data = '{"ok":false,"msg":"密钥不正确"}'.encode("utf-8")
                self.send_response(401)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            return

        if not self._authed():
            return self._unauth_json()

        # ---- 初始设置 (不受 setup_done 限制)
        if u.path == "/api/setup":
            gdir = str(body.get("game_dir", "")).strip().strip('"')
            err = check_game_dir(gdir)
            if err:
                # 拒绑必须留痕: 报错里带着三条判据的实测结果, 用户截一张图就能定位
                oplog("错误", f"拒绑目录 {gdir}: {err}")
                return self._json({"ok": False, "msg": err})
            _settings["game_dir"] = gdir
            if instance() and not os.path.isdir(os.path.join(gdir, "Servers", instance())):
                _settings["instance"] = None
            save_settings()
            oplog("操作", f"设置游戏目录: {gdir}")
            note = client_soft_note(gdir)
            if note:
                oplog("操作", f"目录体检提醒: {note}")
            return self._json({"ok": True, "instances": list_instances(),
                               "msg": "游戏目录已保存", "warn": note})

        if u.path == "/api/setup/instance":
            err = bound_dir_error()
            if err:
                return self._json({"ok": False, "msg": err})
            name = str(body.get("name", "")).strip()
            running, _ = server_running()
            if running and name != instance():
                return self._json({"ok": False, "msg": "服务器正在运行, 请先关服再切换存档"})
            inst_dir = os.path.join(game_dir(), "Servers", name)
            if os.path.isdir(inst_dir):
                if not existing_instance_ok(name):
                    return self._json({"ok": False,
                                       "msg": "这个存档文件夹名用不了 (名字里不能带路径符号)"})
                ensure_instance_files(name)   # 补齐缺失的 Commands.dat / Config.txt
                created = False
            else:
                # 目录不存在 (包括与旧 settings.json 同名的情况) -> 新建, 这一条仍要英文名
                ok, msg = create_instance(name)
                if not ok:
                    return self._json({"ok": False, "msg": msg})
                created = True
            _settings["instance"] = name
            save_settings()
            oplog("操作", f"{'创建' if created else '切换'}存档: {name}")
            hint = "" if created else non_ascii_name_hint(name)
            if hint:
                oplog("操作", f"选中非英文存档名: {name} | {hint}")
            return self._json({"ok": True, "created": created,
                               "rocket": rocket_status(),
                               "msg": f"已选择存档「{name}」"})

        # ---- 开服端下载 (初始设置阶段就要能用, 所以在 setup_done 门槛之前)
        if u.path == "/api/dirs":
            return self._json(list_dirs(str(body.get("path", ""))[:500]))

        if u.path == "/api/serverdedi/scan":
            return self._json({"ok": True, "found": scan_dedi_installs()})

        if u.path == "/api/serverdedi/start":
            if LITE:
                return self._json({"ok": False,
                                   "msg": "简装版不带开服端下载, 请自己去 Steam 装 "
                                          "Unturned - Dedicated Server (U3DS)"})
            found = scan_dedi_installs()   # 点下载时必须再查一遍本机, 别白下 1.5 GB
            if found and not body.get("force"):
                return self._json({"ok": False, "exists": found,
                                   "msg": "检测到本机已经有可用的开服端, 不用重复下载"})
            ok, msg = start_download(str(body.get("dest", ""))[:500])
            return self._json({"ok": ok, "msg": msg})

        if u.path == "/api/serverdedi/cancel":
            if LITE:
                return self._json({"ok": False, "msg": "简装版不带开服端下载"})
            ok, msg = cancel_download()
            return self._json({"ok": ok, "msg": msg})

        if u.path == "/api/serverdedi/update":
            ok, msg = start_update()
            return self._json({"ok": ok, "msg": msg})

        if u.path == "/api/serverdedi/use":
            ok, msg, path = adopt_dedi(str(body.get("path", ""))[:500])
            return self._json({"ok": ok, "msg": msg, "game_dir": path,
                               "instances": list_instances() if ok else []})

        if not setup_done():
            return self._json({"ok": False, "msg": "请先完成初始设置"})

        # ---- 常规接口
        if u.path == "/api/start":
            ok, msg = start_server()
            oplog("操作", f"点击开服 → {msg}")
            return self._json({"ok": ok, "msg": msg, "wsmap": ws_start_notice(ok)})
        if u.path == "/api/stop":
            ok, msg = stop_server()
            oplog("操作", f"点击关服 → {msg}")
            return self._json({"ok": ok, "msg": msg})
        if u.path == "/api/restart":
            ok, msg = stop_server()
            if not ok and msg != "服务器没有在运行":
                # 关服这一步没成 (例如 shutdown 发出去了但世界还在存), 就别急着开新局
                oplog("操作", f"点击重启被关服结果拦下 → {msg}")
                return self._json({"ok": False, "msg": msg})
            if ok:
                time.sleep(2)
            ok2, msg2 = start_server()
            oplog("操作", f"点击重启 → {msg2}")
            return self._json({"ok": ok2, "msg": ("已关闭, " + msg2) if ok else msg2,
                               "wsmap": ws_start_notice(ok2)})

        if u.path == "/api/console":
            cmd = str(body.get("cmd", ""))[:500]
            ok, msg = send_command(cmd)
            if ok:
                oplog("操作", f"发送控制台命令: {cmd}")
            return self._json({"ok": ok, "msg": msg if not ok else "已发送"})

        if u.path == "/api/players":
            running, _ = server_running()
            if not running:
                return self._json({"ok": False, "players": [], "raw": "",
                                   "msg": "服务器没有在运行"})
            try:
                resp = rcon_command("players")
            except (OSError, RconError) as e:
                why, ev = rcon_diag()
                oplog("错误", f"刷新玩家列表失败: {e} | {ev['evidence']}")
                return self._json({"ok": False, "players": [], "raw": "",
                                   "msg": f"获取失败: {e} —— {why}"})
            players = parse_players(resp)
            _write_title_file(len(players))
            oplog("操作", f"刷新玩家列表: {len(players)} 人在线")
            return self._json({"ok": True, "players": players, "raw": resp})

        if u.path == "/api/launch":
            lc = {"batchmode": bool(body.get("batchmode", True)),
                  "nographics": bool(body.get("nographics", True)),
                  "console": bool(body.get("console", True)),
                  "extra": str(body.get("extra", ""))[:500]}
            save_launch_cfg(lc)
            _settings["close_bat_stops_server"] = bool(body.get("close_stop", False))
            save_settings()
            oplog("操作", f"保存启动参数: {lc}, 关bat关服={lc and _settings['close_bat_stops_server']}")
            return self._json({"ok": True, "msg": "启动参数已保存, 重启服务器后生效"})

        if u.path == "/api/anticheat":
            vac, be = bool(body.get("vac", True)), bool(body.get("be", True))
            ok_v, msg_v = set_anticheat("vac", vac)
            ok_b, msg_b = set_anticheat("be", be)
            if not (ok_v and ok_b):
                bad = " / ".join(m for ok, m in ((ok_v, msg_v), (ok_b, msg_b)) if not ok)
                oplog("错误", f"保存反作弊设置失败: {bad}")
                return self._json({"ok": False, "msg": "没保存成功: " + bad})
            if not (vac and be):
                return self._json({"ok": True, "msg": "⚠ " + (msg_v if not vac else msg_b)})
            return self._json({"ok": True, "msg": "✔ 反作弊已全部开启 (VAC + BattlEye), 重启服务器后生效"})

        if u.path == "/api/rocket/install":
            oplog("操作", "点击安装/更新 Rocket")
            ok, msg = install_rocket()
            return self._json({"ok": ok, "msg": msg})

        if u.path == "/api/rocket/open":
            p = os.path.join(server_dir(), "Rocket", "Plugins")
            os.makedirs(p, exist_ok=True)
            try:
                subprocess.Popen(["explorer", p])
                oplog("操作", f"打开插件文件夹: {p}")
                return self._json({"ok": True, "msg": "已打开文件夹"})
            except OSError as e:
                return self._json({"ok": False, "msg": str(e)})

        if u.path == "/api/rocketzh":
            ok, msg = set_rocket_zh(bool(body.get("on")))
            return self._json({"ok": ok, "msg": msg})

        if u.path == "/api/browser":
            icon = str(body.get("icon", "")).strip()[:300]
            thumb = str(body.get("thumb", "")).strip()[:300]
            links = parse_link_lines(str(body.get("links", ""))[:2000])
            for label, val in (("图标 Icon", icon), ("缩略图 Thumbnail", thumb)):
                if val and not val.startswith(("http://", "https://")):
                    return self._json({"ok": False,
                                       "msg": f"{label} 必须是 http:// 或 https:// 开头的图片直链"})
            try:
                set_browser_cfg(icon, thumb, links)
            except OSError as e:
                oplog("错误", f"保存服务器图标失败: {e}")
                return self._json({"ok": False, "msg": str(e)})
            oplog("操作", f"服务器图标/链接: Icon={icon or '(清空)'} "
                          f"Thumbnail={thumb or '(清空)'} 链接 {len(links)} 条")
            return self._json({"ok": True, "links": len(links),
                               "msg": f"已写入 Config.txt 的 Browser 段 "
                                      f"(图标 {'已设置' if icon else '已清空'}, 链接 {len(links)} 条), "
                                      f"重启服务器后玩家端生效"})

        if u.path == "/api/saves/backup":
            items = [str(x)[:400] for x in body.get("items", [])][:600]
            ok, msg, target = backup_saves(items, str(body.get("dest", ""))[:500])
            return self._json({"ok": ok, "msg": msg, "dest": target})

        if u.path == "/api/saves/delete":
            items = [str(x)[:400] for x in body.get("items", [])][:600]
            if not body.get("confirm"):
                return self._json({"ok": False, "msg": "请先在确认弹窗里勾选同意"})
            ok, msg, target = delete_saves(items, str(body.get("dest", ""))[:500])
            return self._json({"ok": ok, "msg": msg, "dest": target})

        if u.path == "/api/file":
            full = safe_path(str(body.get("path", "")))
            if not full:
                return self._json({"ok": False, "msg": "路径不允许或文件不存在"})
            try:
                write_file(full, str(body.get("content", "")))
                oplog("操作", f"保存文件: {body.get('path')}")
                return self._json({"ok": True})
            except OSError as e:
                oplog("错误", f"保存文件失败 {body.get('path')}: {e}")
                return self._json({"ok": False, "msg": str(e)})

        if u.path == "/api/commands":
            if not os.path.isfile(commands_path()):
                ensure_instance_files(instance())
            text, _ = read_file(commands_path())
            entries = parse_commands(text)
            kv = {str(k).lower(): ("" if v is None else str(v)) for k, v in body.get("kv", {}).items()}
            warn = ""
            if "map" in kv:
                kv["map"] = kv["map"].strip()
                if not kv["map"]:
                    # 空地图会让游戏悄悄退回 PEI, 一律拒绝写入这一项
                    del kv["map"]
                    warn = "地图不能为空, 本次没有修改地图"
                else:
                    kv["map"], found = norm_map_name(kv["map"])
                    if not found:
                        warn = (f"地图「{kv['map']}」在 Maps 文件夹和存档 Level 里都没找到, "
                                f"重启服务器后可能退回默认地图 PEI")
            for k in ("maxplayers", "port"):
                # 空数字会写成没有值的 "Maxplayers" 行, 游戏当成没设置 -> 直接拒写这一项
                if k in kv and not re.fullmatch(r"[0-9]+", kv[k].strip()):
                    del kv[k]
                    warn = (warn + "; " if warn else "") + f"{k} 要填数字, 本次没有修改它"
            seen = set()
            for e in entries:
                if not e["key"]:
                    continue
                low = e["key"].lower()
                if low in ("pvp", "pve"):
                    continue
                if low in kv:
                    e["value"] = kv[low]
                    seen.add(low)
            for k, v in kv.items():
                if k in seen or k in ("pvp", "pve"):
                    continue
                entries.append({"key": k, "value": v, "comment": ""})
            # PVP/PVE 互斥: 只有当本次提交包含其中之一时才重写, 否则保留原行
            if "pvp" in kv or "pve" in kv:
                entries = [e for e in entries
                           if not (e["key"] and e["key"].lower() in ("pvp", "pve"))]
                if kv.get("pvp") == "" and kv.get("pve") != "":
                    entries.append({"key": "PVP", "value": "", "comment": ""})
                elif kv.get("pve") == "":
                    entries.append({"key": "PVE", "value": "", "comment": "//PVE 或 PVP"})
            write_file(commands_path(), build_commands(entries))
            if "map" in kv:
                oplog("操作", f"保存服务器设置 (Commands.dat) · 地图 = {kv['map']}")
                if warn:
                    oplog("错误", f"地图检查: {warn}")
            else:
                oplog("操作", "保存服务器设置 (Commands.dat)")
            return self._json({"ok": True, "warn": warn})

        if u.path == "/api/workshop":
            full = os.path.join(server_dir(), "WorkshopDownloadConfig.json")
            try:
                data = json.loads(read_file(full)[0]) if os.path.isfile(full) else {}
            except Exception:
                data = {}
            try:
                fids = [int(i) for i in body.get("ids", [])]
                iids = [int(i) for i in body.get("ignores", [])]
            except (TypeError, ValueError):
                return self._json({"ok": False, "msg": "模组 ID 必须是数字"}, 400)
            data["File_IDs"] = list(dict.fromkeys(fids))
            data["Ignore_IDs"] = list(dict.fromkeys(iids))
            write_file(full, json.dumps(data, indent=2, ensure_ascii=False))
            oplog("操作", f"保存创意工坊列表 ({len(data['File_IDs'])} 个模组)")
            return self._json({"ok": True})

        if u.path == "/api/maps/workshop":
            maps = workshop_maps()
            oplog("操作", f"识别本存档的创意工坊地图: "
                          + (", ".join("%s(%s)" % (m["map"], m["id"]) for m in maps) or "没识别到"))
            return self._json({"ok": True, "maps": maps, "dir": server_dir() or "",
                               "subscribed": len(ws_subscribed_ids()),
                               "msg": (f"识别到 {len(maps)} 张创意工坊地图" if maps else
                                       "本存档里还没有下载好的创意工坊地图: 先在「创意工坊」页填 ID, "
                                       "再把服务器启动一遍让游戏把地图下载下来, 然后回来点这个按钮")})

        if u.path == "/api/workshop/info":
            raw = body.get("text") or ""
            ids = ws_extract_ids(raw) if raw else [
                int(i) for i in body.get("ids", []) if str(i).strip().isdigit()]
            if not ids:
                return self._json({"ok": False, "msg": "没有识别到模组 ID (需要 6 位以上数字)"})
            rows, err = workshop_info(ids)
            if err and not rows:
                oplog("错误", f"创意工坊查询失败: {err}")
                return self._json({"ok": False, "msg": err})
            oplog("操作", f"查询创意工坊模组信息 ({len(rows)} 条)")
            return self._json({"ok": True, "items": rows, "err": err})

        if u.path == "/api/cheat":
            on = bool(body.get("on"))
            ok, msg = set_cheat(on)
            if ok:
                d = get_server_defaults()
                d["cheat"] = on
                save_server_defaults(d)
                oplog("操作", f"仪表盘默认开服选项: 作弊 → {'开启' if on else '关闭'} (当前存档+新建默认)")
            else:
                oplog("错误", f"作弊开关失败: {msg}")
            return self._json({"ok": ok, "msg": msg})

        if u.path == "/api/toggle":
            tid = body.get("id")
            on = bool(body.get("on"))
            t = next((x for x in TOGGLES if x["id"] == tid), None)
            if not t:
                return self._json({"ok": False, "msg": "未知开关"})
            try:
                for sec, key, val in t["keys"]:
                    set_cfg(config_txt(), sec, key, val if on else None)
            except OSError as e:
                oplog("错误", f"开关写入失败 {tid}: {e}")
                return self._json({"ok": False, "msg": str(e)})
            extra = ""
            if body.get("as_default") and tid in DASH_TOGGLE_IDS:
                d = get_server_defaults()
                tl = set(d["toggles"])
                (tl.add if on else tl.discard)(tid)
                d["toggles"] = [x for x in DASH_TOGGLE_IDS if x in tl]
                save_server_defaults(d)
                extra = "; 已存为新建服务器默认"
            oplog("操作", f"一键开关「{t['name']}」→ {'开启' if on else '恢复默认'}")
            return self._json({"ok": True,
                               "msg": f"「{t['name']}」已{'开启' if on else '恢复默认'}, 重启服务器后生效" + extra})

        if u.path == "/api/select":
            s = next((x for x in SELECTS if x["id"] == body.get("id")), None)
            if not s:
                return self._json({"ok": False, "msg": "未知选项"})
            val = body.get("value")
            if val is not None:
                val = str(val)
            try:
                set_cfg(config_txt(), s["section"], s["key"], val)
            except OSError as e:
                oplog("错误", f"选项写入失败 {s['id']}: {e}")
                return self._json({"ok": False, "msg": str(e)})
            name = next((n for v, n in s["opts"] if v == val), "默认")
            oplog("操作", f"「{s['name']}」设为 {name}")
            return self._json({"ok": True, "msg": f"「{s['name']}」已设为 {name}, 重启服务器后生效"})

        if u.path == "/api/gameplay":
            kv = body.get("kv", {}) or {}
            clean, errs, bad_ids = {}, [], []
            for fullkey, val in kv.items():
                dk = str(fullkey)
                if dk not in CFG_SPECS:
                    errs.append(f"{dk}: 不认识的配置项")
                    bad_ids.append(dk)
                    continue
                norm, err = gameplay_check(dk, "" if val is None else val)
                if err:
                    errs.append(err)
                    bad_ids.append(dk)
                else:
                    clean[dk] = norm
            if errs:
                oplog("错误", f"玩法设置校验未通过 ({len(errs)} 项): " + " | ".join(errs[:6]))
                return self._json({"ok": False, "count": 0, "items": bad_ids,
                                   "msg": "有 " + str(len(errs)) + " 项填得不对, 这次一个都没写入: "
                                          + " / ".join(errs[:4])
                                          + ("…" if len(errs) > 4 else "")})
            count, fail = 0, []
            for dk, norm in clean.items():
                sec, key = dk.split("|", 1)
                try:
                    if set_cfg(config_txt(), sec, key, norm):
                        count += 1
                    else:
                        fail.append(key)
                except OSError as e:
                    fail.append(f"{key}: {e}")
            msg = f"已写入 {count} 项" + (f", 未识别: {','.join(fail)}" if fail else "")
            oplog("操作", f"保存玩法设置 ({msg})")
            return self._json({"ok": True, "count": count, "msg": msg})

        if u.path == "/api/gameplay/advanced":
            kv = body.get("kv", {}) or {}
            clean, errs, bad_ids = {}, [], []
            for fullkey, val in kv.items():
                dk = str(fullkey)
                sp = ADV_SPECS.get(dk)
                if not sp:
                    errs.append(f"{dk}: 这一项开服器没有收录, 不能写")
                    bad_ids.append(dk)
                    continue
                if (sp["sec"], sp["key"]) in ADVANCED_DENY:
                    errs.append(f"{sp['sec_cn']} · {sp['label']}: 涉及登录凭据或官方服务器, 有意不提供")
                    bad_ids.append(dk)
                    continue
                norm, err = adv_check(dk, val)
                if err:
                    errs.append(err)
                    bad_ids.append(dk)
                else:
                    clean[dk] = norm
            if errs:
                oplog("错误", f"高级配置校验未通过 ({len(errs)} 项): " + " | ".join(errs[:6]))
                return self._json({"ok": False, "count": 0, "items": bad_ids,
                                   "msg": "高级配置有 " + str(len(errs)) + " 项填得不对, 这次一个都没写入: "
                                          + " / ".join(errs[:4])
                                          + ("…" if len(errs) > 4 else "")})
            counts = {"set": 0, "new": 0, "skip": 0}
            fail, path = [], config_txt()
            cur = cfg_section_keys(path)
            same = 0
            for dk, norm in clean.items():
                if dk.lower() in cur and (cur[dk.lower()] or "") == (norm or ""):
                    same += 1                     # 值没动过就不碰文件, 免得"保存"虚报项数
                    continue
                sec, key = dk.split("|", 1)
                try:
                    r = set_cfg_value(path, sec, key, norm)
                except OSError as e:
                    r = "fail"
                    fail.append(f"{key}: {e}")
                if r in counts:
                    counts[r] += 1
                else:
                    fail.append(key)
            msg = f"已修改 {counts['set']} 项"
            if counts["new"]:
                msg += f", 新增 {counts['new']} 项"
            if counts["skip"]:
                msg += f", {counts['skip']} 项交回游戏默认"
            if same:
                msg += f" (另有 {same} 项与文件里的值一样, 没动)"
            if fail:
                msg += ", 未写入: " + ",".join(fail[:6])
            oplog("操作", f"保存高级配置 ({msg})")
            return self._json({"ok": not fail, "count": counts["set"] + counts["new"],
                               "msg": msg if not fail else "有 " + str(len(fail)) + " 项写不进去: "
                                    + " / ".join(fail[:4]) + ("…" if len(fail) > 4 else "")})

        if u.path == "/api/manager/shutdown":
            oplog("操作", "从网页关闭开服器程序")
            if _settings.get("close_bat_stops_server"):
                for p in find_server_pids():
                    subprocess.run(["taskkill", "/PID", str(p), "/F"], capture_output=True)
                oplog("操作", "已按选项同时关闭服务器")
            threading.Timer(0.3, lambda: os._exit(0)).start()
            return self._json({"ok": True, "msg": "开服器已关闭"})

        if u.path == "/api/oplog/clear":
            try:
                with open(OPLOG_PATH, "w", encoding="utf-8") as f:
                    f.write("")
            except OSError as e:
                return self._json({"ok": False, "msg": str(e)})
            oplog("操作", "操作日志已清空")
            return self._json({"ok": True, "msg": "日志已清空"})

        return self._json({"ok": False, "msg": "未知接口"})


def main():
    import sys
    load_settings()

    def _on_console_close(sig, frame):
        """bat 窗口被关闭 (CTRL_CLOSE_EVENT -> SIGBREAK)"""
        if _settings.get("close_bat_stops_server"):
            pids = find_server_pids()
            for p in pids:
                subprocess.run(["taskkill", "/PID", str(p), "/F"], capture_output=True)
            if pids:
                oplog("操作", f"开服器窗口关闭, 已同时关闭服务器 (PID {pids})")
        os._exit(0)

    try:
        signal.signal(signal.SIGBREAK, _on_console_close)
    except (OSError, ValueError):
        pass
    try:
        sys.stdout.reconfigure(errors="replace")   # 防止个别字符让打印崩溃
    except Exception:
        pass
    secret = login_key()
    url = f"http://{HOST}:{PORT}/?key={secret}"
    # 把带密钥的完整网址放进剪贴板: 粘到浏览器地址栏即可一步自动登录
    try:
        p = subprocess.Popen(["clip"], stdin=subprocess.PIPE)
        p.communicate(url.encode("ascii", "ignore"))
    except OSError:
        pass
    oplog("操作", "开服器启动")
    print("=" * 58)
    print("  Dawn Sharkk 已启动 (Unturned 开服器 v%s)" % VERSION)
    print(f"  网页地址: {url}")
    print(f"  登录密钥: {secret}")
    print("  >> 上面那行完整网址已复制到剪贴板: 打开浏览器地址栏 Ctrl+V 回车, 免输密钥直接登录 <<")
    print(f"  >> 网址自动登录有效期 30 分钟 (到 {time.strftime('%H:%M', time.localtime(time.time() + KEY_AUTO_WINDOW))}), 超时需在登录框手动粘贴密钥 <<")
    print("=" * 58)
    if setup_done():
        print(f"  游戏目录: {game_dir()}")
        print(f"  存档(实例): {instance()}")
    else:
        print("  首次使用: 打开网页后会引导你完成初始设置")
    print()
    print("  请勿关闭本窗口; 关闭窗口 = 退出开服器 (不影响已开的游戏服)")
    print()
    try:
        srv = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError:
        print(f"  [!] 端口 {PORT} 已被占用, 开服器无法启动。")
        print("      可能开服器已经在运行: 直接打开上面的网页即可。")
        print("      若要重新启动, 请在任务管理器里结束旧的 python.exe 再试。")
        oplog("错误", f"端口 {PORT} 被占用, 启动失败")
        return
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出 (不影响正在运行的服务器)")
        oplog("操作", "开服器退出")
    except Exception as e:
        oplog("错误", "服务异常退出: " + exc_summary(e))
        print("[!] 发生异常, 详情见 操作日志.txt")


if __name__ == "__main__":
    main()
