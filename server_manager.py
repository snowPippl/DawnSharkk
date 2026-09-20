# -*- coding: utf-8 -*-
"""
Unturned 服务器开服器 (本地网页版 · 萌新友好版)
- 首次启动在网页里选择游戏目录与存档(实例), 之后自动记住
- 功能: 开服/安全关服/重启、控制台(实时日志+Server Code 抓取+发送命令+报错高亮)、
        傻瓜式一键设置(死亡不掉落/建筑无敌/车辆无敌/免道具看地图等)、Commands.dat / Config.txt 汉化编辑、
        服务器图标与大厅链接(图床)、Rocket 指令反馈汉化、
        创意工坊模组、建筑与玩家存档备份/删除(删前强制备份)、全部配置文件(含 Rocket 插件)读取与修改、明/暗主题切换
- 纯 Python 标准库, 自带免安装 Python, 双击 启动开服器.bat 即用
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
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote, urlencode
import urllib.error
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(BASE_DIR, "settings.json")
OPLOG_PATH = os.path.join(BASE_DIR, "操作日志.txt")
HOST, PORT = "127.0.0.1", 8787
VERSION = "0.1.8"            # 发布版本号: 改这里 + 新增 更新内容-版本号.md + 跑 发布打包.bat

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
    return bool(sd and os.path.isfile(os.path.join(game_dir(), "Unturned.exe"))
                and os.path.isdir(sd))


def game_exe_ok(gdir):
    return bool(gdir) and os.path.isfile(os.path.join(gdir, "Unturned.exe"))


def detect_game_dirs():
    """探测常见 Steam 库里的 U3DS 目录"""
    found = []

    def add(p):
        p = os.path.normpath(p)
        if game_exe_ok(p) and os.path.normcase(p) not in map(os.path.normcase, found):
            found.append(p)

    steam_roots = []
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            steam_roots.append(winreg.QueryValueEx(k, "SteamPath")[0])
    except OSError:
        pass
    for drv in "CDEFGH":
        steam_roots.append(f"{drv}:\\Program Files (x86)\\Steam")
        steam_roots.append(f"{drv}:\\Steam")
        steam_roots.append(f"{drv}:\\SteamLibrary")
        steam_roots.append(f"{drv}:\\Epic Games\\Steam")

    libs = set()
    for root in steam_roots:
        add(os.path.join(root, "steamapps", "common", "U3DS"))
        vdf = os.path.join(root, "steamapps", "libraryfolders.vdf")
        if os.path.isfile(vdf):
            try:
                with open(vdf, encoding="utf-8", errors="replace") as f:
                    for m in re.finditer(r'"path"\s+"([^"]+)"', f.read()):
                        libs.add(m.group(1).replace("\\\\", "\\"))
            except OSError:
                pass
    for lib in libs:
        add(os.path.join(lib, "steamapps", "common", "U3DS"))
        add(os.path.join(lib, "SteamLibrary", "steamapps", "common", "U3DS"))
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


def create_instance(name):
    if not INSTANCE_NAME_RE.match(name or ""):
        return False, "存档名只能用英文字母、数字、- 和 _ (1~32 字符)"
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
GAMEPLAY_FIELDS = [
    ("Players", "玩家", [
        ("Health_Default", "出生生命值", "number", ""),
        ("Food_Default", "出生饱食度", "number", "0~100"),
        ("Water_Default", "出生含水量", "number", "0~100"),
        ("Armor_Multiplier", "玩家受伤倍率", "number", "0.5=减半 0=无敌"),
        ("Experience_Multiplier", "经验获取倍率", "number", ""),
        ("Skill_Cost_Multiplier", "技能升级消耗倍率", "number", ""),
        ("Can_Hurt_Legs", "摔落伤害", "bool", ""),
        ("Can_Start_Bleeding", "会流血", "bool", ""),
        ("Allow_Instakill_Headshots", "狙击爆头一击必杀", "bool", ""),
        ("Spawn_With_Max_Skills", "出生满技能", "bool", ""),
        ("Lose_Items_PvP", "PVP死亡掉落物品", "number", "0~1, 0=不掉 1=全掉"),
        ("Lose_Items_PvE", "PVE死亡掉落物品", "number", "0~1"),
        ("Lose_Clothes_PvP", "PVP死亡掉落穿戴", "bool", ""),
        ("Lose_Clothes_PvE", "PVE死亡掉落穿戴", "bool", ""),
        ("Lose_Weapons_PvP", "PVP死亡掉落武器", "bool", ""),
        ("Lose_Weapons_PvE", "PVE死亡掉落武器", "bool", ""),
    ]),
    ("Zombies", "僵尸", [
        ("Spawn_Chance", "僵尸刷新率", "number", "0~1"),
        ("Damage_Multiplier", "僵尸伤害倍率", "number", ""),
        ("Armor_Multiplier", "僵尸承伤倍率", "number", "越低越耐打"),
        ("Sprinter_Chance", "疾跑僵尸概率", "number", "0~1"),
        ("Crawler_Chance", "爬行僵尸概率", "number", "0~1"),
        ("Loot_Chance", "僵尸掉落物品概率", "number", "0~1"),
        ("Respawn_Day_Time", "僵尸复活时间(秒)", "number", ""),
        ("Can_Target_Barricades", "僵尸攻击家具", "bool", ""),
        ("Can_Target_Structures", "僵尸攻击建筑", "bool", ""),
        ("Can_Target_Vehicles", "僵尸攻击载具", "bool", ""),
    ]),
    ("Items", "物品", [
        ("Spawn_Chance", "物品刷新率", "number", "0~1"),
        ("Respawn_Time", "物品刷新间隔(秒)", "number", ""),
        ("Despawn_Dropped_Time", "丢在地上的物品消失时间(秒)", "number", ""),
        ("Has_Durability", "物品耐久损耗", "bool", ""),
    ]),
    ("Vehicles", "载具", [
        ("Armor_Multiplier", "载具受伤倍率", "number", "0=无敌"),
        ("Decay_Time", "载具自然损坏时间(秒)", "number", ""),
        ("Respawn_Time", "载具爆炸后重刷时间(秒)", "number", ""),
        ("Has_Battery_Chance", "刷车自带电瓶概率", "number", "0~1"),
        ("Has_Tire_Chance", "刷车自带轮胎概率", "number", "0~1"),
        ("Max_Instances_Medium", "刷车上限(中型地图)", "number", ""),
    ]),
    ("Barricades", "家具/路障", [
        ("Decay_Time", "家具自然损坏时间(秒)", "number", ""),
        ("Armor_Lowtier_Multiplier", "低级家具承伤倍率", "number", "0=无敌"),
        ("Armor_Hightier_Multiplier", "高级家具承伤倍率", "number", ""),
        ("Melee_Damage_Multiplier", "近战对家具伤害倍率", "number", ""),
    ]),
    ("Structures", "建筑", [
        ("Decay_Time", "建筑自然损坏时间(秒)", "number", ""),
        ("Armor_Lowtier_Multiplier", "低级建筑承伤倍率", "number", "0=无敌"),
        ("Armor_Hightier_Multiplier", "高级建筑承伤倍率", "number", ""),
        ("Melee_Damage_Multiplier", "近战对建筑伤害倍率", "number", ""),
    ]),
    ("Gameplay", "玩法规则", [
        ("Hitmarkers", "命中标记", "bool", "打中人出现白叉"),
        ("Crosshair", "显示准星", "bool", ""),
        ("Chart", "常驻纸质地图", "bool", "不用捡地图物品"),
        ("Satellite", "常驻卫星地图", "bool", ""),
        ("Compass", "常驻指南针", "bool", ""),
        ("Group_Map", "队友显示在地图上", "bool", ""),
        ("Group_HUD", "队友名字透视", "bool", ""),
        ("Friendly_Fire", "组队友伤", "bool", ""),
        ("Can_Suicide", "允许自杀按钮", "bool", ""),
        ("Timer_Respawn", "死亡后重生等待(秒)", "number", ""),
        ("Timer_Home", "回床等待(秒)", "number", ""),
        ("Timer_Exit", "退出服务器等待(秒)", "number", ""),
        ("Bypass_Building_In_Safezones", "允许安全区内建造", "bool", ""),
        ("Allow_Shoulder_Camera", "第三人称越肩视角", "bool", ""),
    ]),
    ("Events", "事件", [
        ("Use_Airdrops", "开启空投", "bool", ""),
        ("Airdrop_Frequency_Min", "空投最小间隔(天)", "number", ""),
        ("Airdrop_Frequency_Max", "空投最大间隔(天)", "number", ""),
        ("Weather_Duration_Multiplier", "天气时长倍率", "number", "0=关闭天气"),
        ("Arena_Min_Players", "竞技场最少队伍数", "number", ""),
    ]),
    ("Server", "网络/安全", [
        ("Max_Ping_Milliseconds", "高延迟踢出(毫秒)", "number", "默认750"),
        ("Timeout_Game_Seconds", "无响应踢出(秒)", "number", ""),
        ("VAC_Secure", "VAC 反作弊", "bool", ""),
        ("BattlEye_Secure", "BattlEye 反作弊", "bool", "不建议关闭"),
    ]),
]

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


def find_server_pids():
    """按启动参数精确找到本存档的 Unturned 服务器进程 (不会误伤正在玩的游戏客户端)"""
    pids = []
    inst = instance()
    try:
        r = subprocess.run(
            ["wmic", "process", "where", "name='Unturned.exe'",
             "get", "ProcessId,CommandLine", "/FORMAT:CSV"],
            capture_output=True, timeout=20)
        txt = r.stdout.decode("gbk", "replace")
    except (OSError, subprocess.TimeoutExpired):
        return pids
    for line in txt.splitlines():
        line = line.strip()
        if not line or line.startswith("Node,"):
            continue
        if inst and inst in line and ("+InternetServer/" in line or "+Secureserver/" in line
                                      or "+LanServer/" in line):
            m = re.search(r",\s*\"?(\d+)\"?\s*$", line)
            if m:
                pids.append(int(m.group(1)))
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


def stop_server():
    running, pid = server_running()
    if not running:
        return False, "服务器没有在运行"
    # 优先安全关服: RCON shutdown 会自动保存世界 (含接管此前已启用的 RCON)
    if not _rcon_cfg:
        adopt_rcon()
    if _rcon_cfg:
        try:
            rcon_command("shutdown")
            for _ in range(15):
                time.sleep(2)
                if not server_running()[0]:
                    restore_rcon()
                    _log("—— 服务器已安全关闭, 世界已保存", "sys")
                    return True, "✅ 已安全关闭, 世界已保存"
        except (OSError, RconError):
            pass
    # 兜底: 只精确结束本存档的服务器进程 (不会误伤正在玩的 Unturned 游戏客户端)
    pids = find_server_pids()
    if not pids:
        return False, "没有找到本存档的服务器进程 (如果只开着 Unturned 游戏客户端, 不会被关闭)"
    for p in pids:
        subprocess.run(["taskkill", "/PID", str(p), "/F"], capture_output=True)
    with _proc_lock:
        helper = _proc
    if helper is not None and helper.poll() is None:
        try:
            helper.kill()   # 控制台中继器随服务器退出
        except OSError:
            pass
    restore_rcon()
    _srv_check["t"] = 0
    _log("—— 服务器已被强制关闭", "sys")
    return True, "已强制关闭 (未保存世界。下次请先在控制台执行 save 或直接点关服按钮)"


# ================================================================ RCON (控制台命令发送)
# Unturned 不接受管道 stdin 命令, 因此开服前临时启用 Rocket 的 RCON
# (密码=本次运行的登录密钥), 关服后自动把 Rocket.config.xml 还原。
# Rocket RCON 为行协议: 连接后先发 `login <密码>`, 之后每行一条命令。
_rcon_cfg = None


class RconError(Exception):
    pass


def _rcon_recv(sock, quiet):
    buf = b""
    end = time.time() + quiet
    while time.time() < end:
        try:
            d = sock.recv(4096)
            if not d:
                break
            buf += d
            end = time.time() + quiet
        except (socket.timeout, OSError):
            break
    return buf.decode("utf-8", "replace")


def rcon_command(cmd):
    if not _rcon_cfg:
        raise RconError("RCON 未启用")
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
        resp = _rcon_recv(sock, 1.0)
        if "error" in resp.lower() or "denied" in resp.lower():
            raise RconError(resp.strip() or "登录失败")
        sock.sendall(cmd.encode("utf-8") + b"\n")
        return _rcon_recv(sock, 1.5).strip()
    finally:
        try:
            sock.close()
        except OSError:
            pass


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
        return False, f"发送失败: {e} (服务器可能还在启动, 等 1 分钟再试)"
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


def rocket_installed():
    g = game_dir()
    return bool(g) and os.path.isdir(os.path.join(g, "Modules", "Rocket.Unturned"))


def rocket_status():
    """Rocket 前置状态: 是否已安装 / 游戏目录里是否有官方安装脚本"""
    if not game_dir():
        return {"installed": False, "bat": False}
    g = game_dir()
    return {"installed": rocket_installed(),
            "bat": os.path.isfile(os.path.join(g, "Extras", "Install Rocket.bat"))}


def install_rocket():
    """运行 Extras\Install Rocket.bat 安装/重装 Rocket"""
    bat = os.path.join(game_dir(), "Extras", "Install Rocket.bat")
    if not os.path.isfile(bat):
        return False, "找不到 Extras\\Install Rocket.bat (请先通过 Steam 校验游戏完整性)"
    try:
        r = subprocess.run([bat], cwd=os.path.dirname(bat), capture_output=True,
                           stdin=subprocess.DEVNULL, timeout=180)
        out = (r.stdout or b"").decode("gbk", "replace")
    except (OSError, subprocess.TimeoutExpired) as e:
        oplog("错误", f"Rocket 安装失败: {e}")
        return False, f"安装失败: {e}"
    ok = rocket_installed()
    oplog("操作", f"Rocket 安装{'完成' if ok else '异常'}: {out.strip()[:120]}")
    if ok:
        return True, "Rocket 安装/更新完成, 重启服务器后生效"
    return False, "安装脚本已执行, 但未检测到 Modules\\Rocket.Unturned, 请截图命令框报错反馈"


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
 var box=document.createElement('div');box.className='modal-box';
 var h=document.createElement('h3');h.textContent=(o.icon?o.icon+'  ':'')+(o.title||'');
 var bd=document.createElement('div');bd.className='mbd';bd.textContent=o.body||'';
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
/* ---- 彩蛋: 连点左上角图标 5 次 → 贡献者名单 ---- */
var CREDITS=[
 ['💻','Pippl','Dawn Sharkk 作者 · 界面 / 功能 / 文档','作者'],
 ['🧪','Nmaomao','功能测试','测试'],
 ['🪟','爱情是件奢侈品','Windows 11 适配测试','测试']];
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
  'Dawn Sharkk 才有今天这个稳定度。';
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
 if(s.installed){toast('Rocket 前置插件已安装 🚀 插件 dll 放进存档的 Rocket/Plugins 文件夹即可');return;}
 if(!s.bat){toast('游戏目录 Extras 里没有 Install Rocket.bat — 请先在 Steam 校验游戏完整性',1);return;}
 rocketChoice('如需使用更多功能请开启 Rocket 前置插件',
  '开启 Rocket 前置插件后可加载 .dll 插件、使用 RCON 指令与玩家管理等功能。'+
  '将运行游戏自带 Extras 里的 Install Rocket.bat 完成安装, 装完重启服务器生效。')
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
 if(rk&&d.rocket){rk.textContent=d.rocket.installed?'🚀 Rocket 已装':'🚀 Rocket 未安装';
 rk.className='chip'+(d.rocket.installed?'':' warnch');}
 var rk2=document.getElementById('st-rk');
 if(rk2&&d.rocket)rk2.textContent=d.rocket.installed?'✅ 已安装':'⭕ 未安装';
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
   if(nd.classList.contains('card'))nd.classList.add('dirty');});});
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
  <div class="sub">Unturned 开服器 v{VERSION} · by Pippl</div></div></div>
  <nav class="nav">{links}</nav>
  <div class="foot">{foot}</div>
</aside>
<main class="main">
<div id="toast"></div>
<div class="pagehead"><h1>{title}</h1>
<span class="chip" id="chip">检测中…</span>
<span class="chip" id="rkchip" onclick="openRocket()" title="Rocket 前置插件状态, 点击查看/开启" style="cursor:pointer">🚀 检测中…</span>
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
<div class="banner warn" id="rkban" style="display:none">🚀 未检测到 Rocket 前置插件 — 如需使用更多功能请开启 Rocket 前置插件 (加载插件 / RCON 指令 / 玩家管理)
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
    rocket_note = "· 检测到 Modules\\Rocket.Unturned" if rocket_ok else "· 不安装的话 Rocket 插件不会加载"
    text, _ = read_file(commands_path())
    entries = parse_commands(text)
    known = {k for k, *_ in CMD_FIELDS} | {"pvp", "pve", "cheat", "cheats"}
    vals = {}
    for e in entries:
        if e["key"] and e["key"].lower() in known:
            vals[e["key"].lower()] = e["value"]
    cur_pvpve = "pvp" if "pvp" in vals else ("pve" if "pve" in vals else "")

    def pills(key, options, cur=None):
        if cur is None:
            cur = vals.get(key, "")
        p = ""
        for v, label in options:
            on = "checked" if cur == v else ""
            p += f'<label><input type="radio" name="p_{key}" value="{v}" {on}><span>{label}</span></label>'
        return p

    mode_pills = pills("mode", [("easy", "🟢 简单"), ("normal", "🟡 普通"), ("hard", "🔴 困难")])
    persp_pills = pills("perspective", [("first", "仅第一人称"), ("third", "仅第三人称"),
                                        ("both", "都可以 (推荐)"), ("vehicle", "载具内第三人称")])
    pvpve = pills("pvpve", [("pvp", "⚔️ PVP 玩家对战"), ("pve", "🧟 PVE 只打僵尸")])

    cur_map = (vals.get("map") or "").strip()
    detected = list_maps()
    unknown_map = bool(cur_map) and cur_map.lower() not in {m.lower() for m in detected}
    # 存档里写的地图探测不到 (创意工坊还没下载 / 手填的名字): 也要原样显示, 绝不能假装是 PEI
    maps = ([cur_map] + detected) if unknown_map else detected
    map_opts = '<option value="">— 选择地图 —</option>' + "".join(
        f'<option value="{esc(m)}" {"selected" if cur_map.lower() == m.lower() else ""}>'
        f'{esc(m)}{" (探测不到, 请核对拼写)" if unknown_map and m == cur_map else ""}</option>'
        for m in maps)
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

    content = f"""
<div class="card" data-grp="cmd"><h2>基础信息</h2>
<div class="grid g2">
<div><label class="f"><b>服务器名称</b><code class="k">name</code></label>
<input class="f" data-key="name" id="i_name" value="{esc(vals.get('name',''))}"></div>
<div><label class="f"><b>地图</b><code class="k">Map &lt;地图名&gt;</code></label>
<select class="f" id="i_map" onchange="showMap()">{map_opts}</select>
<label class="f" style="font-size:12px">列表里没有你要的地图? 在这里照原样填 (填了就优先生效):</label>
<input class="f" id="i_map_custom" placeholder="手动输入地图名" oninput="showMap()">
<div class="st on" id="st_map">{map_state}</div>
<div class="hint">下拉框读的是 <code class="k">U3DS\\Maps</code> 和存档 <code class="k">Level</code> 里<b>真实存在的地图名</b>
(大小写照原样)。创意工坊地图要先在「创意工坊」页订阅, 开服时才会自动下载。<b>换地图 = 换一张全新的世界</b>:
建筑会重新生成, 老地图的建筑存档仍然保留在 <code class="k">Level\\旧地图名</code> 里, 名字改回去就能看到。</div></div>
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
<div class="pills">{mode_pills}</div></div>
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
{other_html}
<div class="card" data-grp="launch"><h2>启动参数</h2>
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
<span class="hint" style="margin-left:10px">重启服务器后生效</span></div>
</div>
<div class="card"><h2>Rocket 插件框架</h2>
<div class="desc">Rocket 让服务器可以加载插件(.dll)。当前状态:
<b style="color:{'var(--ok)' if rocket_ok else 'var(--bad)'}">{'已安装' if rocket_ok else '未安装'}</b>
{rocket_note}</div>
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
    script = """
function mapVal(){
 var c=document.getElementById('i_map_custom').value.trim();
 return c||document.getElementById('i_map').value.trim();}
function showMap(){
 var v=mapVal(),st=document.getElementById('st_map');
 st.textContent=v?('保存后将写入 Commands.dat: Map '+v):'⚠ 地图不能为空, 请选一个或手动填一个';
 st.className='st '+(v?'on':'off');}
function save(){
 var kv={};var bad='';var LB={maxplayers:'最大玩家数',port:'端口'};
 document.querySelectorAll('input.f[data-key]').forEach(el=>{
  var v=el.value.trim();
  if(el.dataset.key==='maxplayers'||el.dataset.key==='port'){
   if(!/^[0-9]+$/.test(v))bad=(LB[el.dataset.key]||el.dataset.key)+' 要填数字, 不能留空';}
  kv[el.dataset.key]=v;});
 var mp=mapVal();
 if(!mp){toast('地图不能为空: 请从下拉框选一个地图, 或在下面手动填写地图名',1);return;}
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
/* 防呆登记: 这三块各有自己的保存按钮 */
var DIRTY_GROUPS=[
 {sel:'[data-grp=cmd]',name:'基础信息与游戏方式',save:save,fix:showMap},
 {sel:'[data-grp=launch]',name:'启动参数',save:saveLaunch},
 {sel:'[data-grp=br]',name:'图标与大厅链接',save:saveBr}];
"""
    return shell("cmds", "服务器设置", content, script, dirty=True)


# ================================================================ 玩法设置 (Config.txt 汉化表单)
def page_gameplay():
    sections_html = ""
    for sec, sec_cn, fields in GAMEPLAY_FIELDS:
        rows = ""
        for key, label, typ, hint in fields:
            fid = f"g_{sec}|{key}"
            val = get_cfg(config_txt(), sec, key)
            if typ == "bool":
                cur_true = val is not None and val.lower() == "true"
                cur_false = val is not None and val.lower() == "false"
                inp = f"""<div class="pills">
<label><input type="radio" name="{fid}" value="True" {'checked' if cur_true else ''}><span>开启</span></label>
<label><input type="radio" name="{fid}" value="False" {'checked' if cur_false else ''}><span>关闭</span></label>
<label><input type="radio" name="{fid}" value="" {'checked' if val is None else ''}><span>默认</span></label>
</div>"""
            else:
                inp = (f'<input class="f" type="text" id="{esc(fid)}" value="{esc(val or "")}">'
                       f'<div class="hint">留空 = 使用游戏默认值</div>')
            rows += f"""<div><label class="f"><b>{label}</b><code class="k">{key}</code></label>{inp}</div>"""
        sections_html += f"""
<div class="card" data-grp="game"><h2>{sec_cn} <code class="k">{sec}</code></h2>
<div class="grid g3">{rows}</div></div>"""

    content = f"""
<div class="card"><h2>玩法参数精细调节</h2>
<div class="desc">每一项都会写入 <code class="k">Config.txt</code>。数字项留空 = 游戏默认; 改完点下面的保存并重启服务器。
推荐先去「一键设置」页搞定常用的。</div></div>
{sections_html}
<div class="card">
<button class="btn big" onclick="save()">💾 保存全部修改</button>
<a class="btn big gray" href="/edit?path=Config.txt">原始编辑器</a>
<span class="hint" style="margin-left:12px">改过没保存就切页或开服, 会弹框提醒并帮你直接保存</span></div>
"""
    script = """
async function save(){
 var kv={};
 document.querySelectorAll('input[type=radio][name^=g_]:checked').forEach(el=>{
  var p=el.name.slice(2).split('|');
  kv[p[0]+'|'+p[1]]=el.value===''?null:el.value;});
 document.querySelectorAll('input.f[id^=g_]').forEach(el=>{
  var p=el.id.slice(2).split('|');
  kv[p[0]+'|'+p[1]]=el.value.trim()===''?null:el.value.trim();});
 var r=await post('/api/gameplay',{kv:kv});
 if(r.ok&&window.dirtySaved)dirtySaved('[data-grp=game]');
 toast(r.ok?'✔ 已保存! 重启服务器后生效 (共 '+r.count+' 项)':'✘ '+r.msg,r.ok?0:1);}
/* 防呆登记: 整页共用一个保存按钮 */
var DIRTY_GROUPS=[{sel:'[data-grp=game]',name:'玩法参数',save:save}];
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
async function send(){var el=document.getElementById('cmd');var c=el.value.trim();if(!c)return;
 el.value='';var r=await post('/api/console',{cmd:c});if(!r.ok)toast(r.msg,1);}
async function quick(c){var r=await post('/api/console',{cmd:c});if(!r.ok)toast(r.msg,1);
 else toast('已发送: '+c);}
async function promptCmd(base,q1,q2,verb){
 var a=prompt(q1);if(!a)return a.trim()===''?null:a.trim();
 var cmd=base+' '+a.trim();
 if(q2){var b=prompt(q2);if(b===null)return;cmd+=' '+b.trim();}
 var r=await post('/api/console',{cmd:cmd});
 if(!r.ok)toast(r.msg,1);else toast('已发送: '+cmd);}
async function refreshPlayers(){
 var box=document.getElementById('plist');
 box.textContent='获取中…';
 var r=await post('/api/players');
 if(!r.ok){box.textContent=r.msg||'获取失败';document.getElementById('pcount').textContent='';return;}
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
poll();setInterval(poll,1500);
"""
    return shell("term", "控制台", content, script)


# ================================================================ 帮助页
def page_help():
    content = """
<div class="card sos"><h2><span class="sostag">置顶</span>🚨 进不去服务器? 对着报错一条条解决</h2>
<div class="desc">你或朋友用服务器代码连接失败时, 游戏会在屏幕上弹一句提示。下面四条覆盖了日常遇到的
绝大多数情况, <b>先看清提示是哪一条, 再照着做</b>, 不用一头雾水地乱重启。</div>

<div class="sosrow"><b>① 服务器连接超时 / 连接失败</b>
<div class="fix">一般是<b>本机网络</b>的问题, 不是服务器坏了。让进不去的那个人<b>连着试两次</b>;
两次都进不来, 就把<b>服务器重启一次</b>, 再把仪表盘最新的那串代码发给他。</div></div>

<div class="sosrow"><b>② 无法验证 Steam 经济 / 连接 Steam 服务器失败</b>
<div class="fix">这条<b>最常见</b>, 十个人里八个会撞到 —— 它跟你的服务器没半点关系, 是 Steam 那边一时
没连通。<b>不用重启服务器</b>, 直接<b>硬挤</b>: 反复点连接多试几次, 只要有一次跟 Steam 服务器通上就进来了。
<b>特别注意: 开了加速器反而更容易一直跳这一句</b>, 解决办法还是硬挤, 或者重启服务器。</div></div>

<div class="sosrow"><b>③ 未开启 BattlEye (战眼反作弊)</b>
<div class="fix">两条路选一条:<br>
① 在开服器「玩法设置」页 → 网络/安全 → 把 <code class="k">BattlEye 反作弊</code> 设为<b>关闭</b>,
保存后重启服务器;<br>
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

<div class="card"><h2>每个菜单是干什么的</h2>
<div class="desc" style="line-height:2.1">
<b>仪表盘</b> — 开服 / 重启 / 关服, 查看服务器代码和基本信息<br>
<b>一键设置</b> — 最常用的傻瓜开关: 死亡不掉落、建筑无敌、车辆无敌、僵尸不拆家、摔落伤害、组队友伤、空投、出生满技能、<b>无需指南针/GPS/手绘地图也能看方向和地图</b>; 僵尸强度 / 经验倍率 / 物资丰富度 / 天气; 还有 <b>Rocket 指令反馈汉化</b><br>
<b>服务器设置</b> — 服务器名称、地图、人数、端口、PVP/PVE、难度、视角、进服密码、<b>开启作弊 (cheats on)</b>; 以及<b>启动参数</b>、<b>Rocket 安装</b>, 页面最底下是 <b>服务器图标与大厅链接 (图床设置)</b><br>
<b>玩法设置</b> — 进阶参数: 血量、经验、刷怪、刷车、建筑承伤、空投频率、天气等 (每项都有中文说明)<br>
<b>创意工坊</b> — 填模组 ID 自动下载 Steam 创意工坊模组<br>
<b>存档管理</b> — <b>建筑存档 / 玩家存档</b>的备份与删除 (删除前强制先备份, 位置自己选)<br>
<b>控制台</b> — 实时日志和报错、发送命令、<b>在线玩家管理</b> (给物品/车辆、传送、踢出；无敌/隐身由玩家自行使用 /god 与 /vanish 指令)、白天/黑夜等快捷指令<br>
<b>操作日志</b> — 记录你在网页里的每次操作和错误, 出问题先来这里看<br>
<b>文件管理</b> — 直接编辑所有配置文件 (含 Rocket 插件配置)
</div></div>

<div class="card"><h2>服务器启动参数 (服务器设置页底部)</h2>
<div class="desc" style="line-height:2.1">
启动命令固定为: <code class="k">-nographics -batchmode +Secureserver/存档名</code> (原版方式, VAC 反作弊开启)<br>
<b>联机只用服务器代码</b> (仪表盘复制): 走 Steam P2P, 不需要 Login_Token、不需要端口映射;<br>
不填 Login_Token 只影响"互联网列表 / 公网 IP 直连", 不影响代码联机<br>
<code class="k">-batchmode</code> — 无窗口后台运行 (推荐勾选)<br>
<code class="k">-nographics</code> — 不加载显卡渲染, 省资源 (推荐勾选)<br>
<code class="k">原版控制台窗口</code> (推荐开启) — 服务器以原版方式启动, 有自己独立的黑色控制台窗口;<br>
　　　　<b>关闭开服器 / 关闭网页后, 服务器会一直在后台运行</b>, 想关服回来点「关服」或在服务器窗口按 Ctrl+C<br>
　　　　关闭此选项则完全后台静默运行, 网页「运行日志」会更完整<br>
<code class="k">关闭 bat 窗口时同时关闭服务器</code> — 默认关闭 (服务器驻留); 打开后关掉 bat 窗口会自动保存并关闭服务器<br>
<code class="k">自定义参数</code> — 其他官方支持的启动参数写在这里, 用空格分隔; 不确定就不要填
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
<div class="keyhint">密钥在启动开服器.bat 的黑色命令框里 (命令框里那行<b>完整网址</b>已自动复制到剪贴板, 直接 Ctrl+V 到地址栏即可自动登录)<br>
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
"""


def page_setup():
    gdir = game_dir() if game_exe_ok(game_dir()) else None
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
        f'</span><span style="margin-left:auto" class="hint">上次运行 {esc(i["time"])}</span></label>'
        for i in insts)

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
<div class="desc">首次使用, 两步完成设置 (保存在本文件夹 settings.json)</div></div></div>

<div class="step" id="step1">
<h2>第 1 步 · 选择游戏目录</h2>
<div class="desc">就是 Unturned 服务器 (U3DS) 所在的文件夹, 里面应该有 Unturned.exe。下面是自动探测到的位置:</div>
{dir_btns or '<div class="desc err">没有自动找到, 请手动粘贴路径</div>'}
<label class="f" style="display:block;margin:12px 0 4px;color:var(--sub);font-size:13px">或手动输入游戏目录完整路径:</label>
<input class="f" id="gdir" placeholder="例如 E:\\SteamLibrary\\steamapps\\common\\U3DS"
 value="{esc(gdir or '')}">
<div style="margin-top:12px"><button class="btn" onclick="pickDir(document.getElementById('gdir').value.trim())">✔ 确认这个目录</button>
<span id="s1msg" class="hint"></span></div>
<div class="hint">提示: 目录确认后会列出该游戏里已有的服务器存档</div>
</div>

<div class="step" id="step2" style="display:{'block' if step2_visible else 'none'}">
<h2>第 2 步 · 选择或创建服务器存档</h2>
<div class="desc">存档 = 一个独立的服务器实例 (有自己的地图、玩家数据、配置)。Servers 文件夹里每个子文件夹都是一个存档。</div>
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
            if not game_exe_ok(gdir):
                return self._json({"ok": False,
                                   "msg": "该目录下没有找到 Unturned.exe, 请确认是 U3DS 服务器目录"})
            _settings["game_dir"] = gdir
            if instance() and not os.path.isdir(os.path.join(gdir, "Servers", instance())):
                _settings["instance"] = None
            save_settings()
            oplog("操作", f"设置游戏目录: {gdir}")
            return self._json({"ok": True, "instances": list_instances(),
                               "msg": "游戏目录已保存"})

        if u.path == "/api/setup/instance":
            if not game_exe_ok(game_dir()):
                return self._json({"ok": False, "msg": "请先设置有效的游戏目录"})
            name = str(body.get("name", "")).strip()
            running, _ = server_running()
            if running and name != instance():
                return self._json({"ok": False, "msg": "服务器正在运行, 请先关服再切换存档"})
            if not INSTANCE_NAME_RE.match(name):
                return self._json({"ok": False, "msg": "存档名只能用英文字母、数字、- 和 _"})
            created = False
            inst_dir = os.path.join(game_dir(), "Servers", name)
            if not os.path.isdir(inst_dir):
                # 目录不存在(包括与旧 settings.json 同名的情况) -> 直接创建补齐
                ok, msg = create_instance(name)
                if not ok:
                    return self._json({"ok": False, "msg": msg})
                created = True
            else:
                ensure_instance_files(name)   # 补齐缺失的 Commands.dat / Config.txt
            _settings["instance"] = name
            save_settings()
            oplog("操作", f"{'创建' if created else '切换'}存档: {name}")
            return self._json({"ok": True, "created": created,
                               "rocket": rocket_status(),
                               "msg": f"已选择存档「{name}」"})

        if not setup_done():
            return self._json({"ok": False, "msg": "请先完成初始设置"})

        # ---- 常规接口
        if u.path == "/api/start":
            ok, msg = start_server()
            oplog("操作", f"点击开服 → {msg}")
            return self._json({"ok": ok, "msg": msg})
        if u.path == "/api/stop":
            ok, msg = stop_server()
            oplog("操作", f"点击关服 → {msg}")
            return self._json({"ok": ok, "msg": msg})
        if u.path == "/api/restart":
            ok, msg = stop_server()
            if ok:
                time.sleep(2)
                ok2, msg2 = start_server()
                oplog("操作", f"点击重启 → {msg2}")
                return self._json({"ok": ok2, "msg": "已关闭, " + msg2})
            ok2, msg2 = start_server()
            oplog("操作", f"点击重启 → {msg2}")
            return self._json({"ok": ok2, "msg": msg2})

        if u.path == "/api/console":
            ok, msg = send_command(str(body.get("cmd", ""))[:500])
            if ok:
                oplog("操作", f"发送控制台命令: {str(body.get('cmd', ''))[:200]}")
            else:
                oplog("错误", f"命令发送失败: {msg}")
            return self._json({"ok": ok, "msg": msg if not ok else "已发送"})

        if u.path == "/api/players":
            running, _ = server_running()
            if not running:
                return self._json({"ok": False, "players": [], "raw": "",
                                   "msg": "服务器没有在运行"})
            try:
                resp = rcon_command("players")
            except (OSError, RconError) as e:
                return self._json({"ok": False, "players": [], "raw": "",
                                   "msg": f"获取失败: {e}"})
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

        if u.path == "/api/dirs":
            return self._json(list_dirs(str(body.get("path", ""))[:500]))

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
            kv = body.get("kv", {})
            count, fail = 0, []
            for fullkey, val in kv.items():
                sec, key = fullkey.split("|", 1)
                val = str(val) if val is not None and str(val) != "" else None
                try:
                    if set_cfg(config_txt(), sec, key, val):
                        count += 1
                    else:
                        fail.append(key)
                except OSError as e:
                    fail.append(f"{key}: {e}")
            msg = f"已写入 {count} 项" + (f", 未识别: {','.join(fail)}" if fail else "")
            oplog("操作", f"保存玩法设置 ({msg})")
            return self._json({"ok": True, "count": count, "msg": msg})

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
