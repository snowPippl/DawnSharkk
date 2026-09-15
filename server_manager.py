# -*- coding: utf-8 -*-
"""
Unturned 服务器开服器 (本地网页版 · 萌新友好版)
- 首次启动在网页里选择游戏目录与存档(实例), 之后自动记住
- 功能: 开服/安全关服/重启、控制台(实时日志+Server Code 抓取+发送命令+报错高亮)、
        傻瓜式一键设置(死亡不掉落/建筑无敌/车辆无敌等)、Commands.dat / Config.txt 汉化编辑、
        创意工坊模组、全部配置文件(含 Rocket 插件)读取与修改、明/暗主题切换
- 纯 Python 标准库, 自带免安装 Python, 双击 启动开服器.bat 即用
"""
import json
import os
import random
import re
import signal
import socket
import string
import subprocess
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(BASE_DIR, "settings.json")
OPLOG_PATH = os.path.join(BASE_DIR, "操作日志.txt")
HOST, PORT = "127.0.0.1", 8787

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
    with open(path, "rb") as f:
        enc = detect_encoding(f.read())
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


def default_commands_text(name, port):
    return (f"name {name}\nMap PEI\nMaxplayers 24\nPort {port}\nMode normal\n"
            f"perspective both\nWelcome 欢迎来到我的服务器!\n//PVE 或 PVP\nPVE\n")


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
    if not os.path.isfile(cmd_path):
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
            f.write(default_commands_text(name, port))
    _ensure_cfg_file(os.path.join(base, "Config.txt"))
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
        m = re.match(r"^\s*([A-Za-z_][\w.]*)\s*(.*?)\s*(//.*)?$", s)
        if m:
            entries.append({"key": m.group(1), "value": m.group(2),
                            "comment": m.group(3) or ""})
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
    """直接改 Commands.dat 的若干键(供进服密码等使用)"""
    full = commands_path()
    if not os.path.isfile(full):
        return False
    text, _ = read_file(full)
    entries = parse_commands(text)
    for k, v in pairs.items():
        hit = False
        for e in entries:
            if e["key"] and e["key"].lower() == k.lower():
                e["value"] = v
                hit = True
                break
        if not hit:
            entries.append({"key": k, "value": v, "comment": ""})
    write_file(full, build_commands(entries))
    return True


def list_maps():
    maps = []
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
                if os.path.isdir(os.path.join(base, name)) and name.lower() not in maps:
                    maps.append(name.lower())
    return maps


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


# ================================================================ 登录密钥 (每次启动随机生成)
_login_key = [None]


def login_key():
    """本次运行的网页登录密钥: 每次启动开服器都会重新随机生成, 不再固定"""
    if not _login_key[0]:
        _login_key[0] = "".join(random.choices(string.ascii_letters + string.digits, k=24))
    return _login_key[0]


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


def _log(line, src="srv"):
    m = CODE_RE.search(line)
    if m:
        _server_code[0] = m.group(1)
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


def start_server():
    global _proc
    running, _ = server_running()
    if running:
        return False, "服务器已经在运行了"
    if not setup_done():
        return False, "请先完成开服器初始设置 (游戏目录 / 存档)"
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
    for _st in _log_tails.values():
        _st.update(off=0, partial=b"")
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
    _log("—— 服务器启动中, 服务器代码(Server Code)出现后会显示在仪表盘", "sys")
    oplog("操作", f"启动服务器: {' '.join(cmd)}")
    return True, "服务器启动中, 大约需要 30~60 秒, 可到「控制台」页看进度"


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
    return os.path.isdir(os.path.join(game_dir(), "Modules", "Rocket.Unturned"))


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


def parse_players(resp):
    """解析 players 命令输出 -> [{name, steamid}]; 兼容各种输出格式"""
    players, seen = [], set()
    if not resp:
        return players
    if re.search(r"fail(?:ed)?\s+to find|no (?:players|one)|nobody|0 players", resp, re.I):
        return players
    for raw in resp.splitlines():
        line = re.sub(r"^(\[[^\]]*\]\s*)+", "", raw.strip())   # 去 [Info] 等日志前缀
        if not line:
            continue
        # 跳过指令回显和明显不是玩家行的内容
        if re.search(r"executed command|has executed|mscorlib\s*>>", line, re.I):
            continue
        # "Players: 名字1, 名字2" 逗号列表格式
        if re.match(r"^players?$", line, re.I):
            continue                                   # 裸 "players" 指令头行
        m0 = re.match(r"^players?\s*[:：]\s*(.+)$", line, re.I)
        if m0:
            listing = m0.group(1)
            if "," in listing or "、" in listing:
                for n in re.split(r"[,，、]", listing):
                    n = n.strip(" -–—:.\t|")
                    if n and len(n) <= 40 and n not in [p["name"] for p in players]:
                        players.append({"name": n, "steamid": ""})
            continue
        if re.match(r"^(successfully|saved|loading|updates|ticks)", line, re.I):
            continue
        m = re.search(r"(.{0,40}?)\s*[\(\[]\s*(7654\d{13,15})\s*[\)\]]", line)
        if m:
            name, sid = m.group(1), m.group(2)
        else:
            m = re.search(r"(7654\d{13,15})", line)
            if m:
                sid = m.group(1)
                before = line[:m.start()].strip(" -–—:.\t|")
                after = line[m.end():].strip(" -–—:.\t|")
                name = before or after
            else:
                sid = ""
                name = re.sub(r"^\d+\s*[.)]\s*", "", line).strip(" .-–—:.\t|")
                if not name or len(name) > 40:
                    continue
        name = re.sub(r"^\d+\s*[.)]\s*", "", name).strip(" .-–—:.\t")
        if sid and sid in seen:
            continue
        if sid:
            seen.add(sid)
        players.append({"name": name or sid, "steamid": sid})
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
.nav{padding:8px 12px;flex:1}
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
/* 火把勾选框 (Uiverse.io by kelvyn_8843) — 勾选=点燃发光 */
.container{display:inline-flex;align-items:center;gap:10px;position:relative;cursor:pointer;
user-select:none;color:var(--sub);font-size:13px;vertical-align:middle}
.container:hover{color:var(--txt)}
.container input{position:absolute;opacity:0;cursor:pointer;height:0;width:0}
.container .torch{display:flex;justify-content:center;height:150px;zoom:.38;margin:-28px -10px;flex:none}
.container.torch-lg .torch{zoom:.5;margin:-12px -14px}
.container .head,.container .stick{position:absolute;width:30px;transform-style:preserve-3d;
transform:rotateX(-30deg) rotateY(45deg);backface-visibility:hidden}
.container .stick{position:relative;height:120px}
.container .face{position:absolute;transform-style:preserve-3d;width:30px;height:30px;
display:grid;grid-template-columns:50% 50%;grid-template-rows:50% 50%;gap:0;
background-color:#1a140d;transition:filter .3s ease;backface-visibility:hidden}
.container .top{transform:rotateX(90deg) translateZ(15px)}
.container .left{transform:rotateY(-90deg) translateZ(15px)}
.container .right{transform:rotateY(0deg) translateZ(15px)}
.container .top div,.left div,.right div,.side-left div,.side-right div{width:100%;height:100%}
.top div:nth-child(1),.left div:nth-child(3),.right div:nth-child(3){background-color:#2c2c25}
.top div:nth-child(2),.left div:nth-child(1),.right div:nth-child(1){background-color:#221f11}
.top div:nth-child(3),.left div:nth-child(4),.right div:nth-child(4){background-color:#21211c}
.top div:nth-child(4),.left div:nth-child(2),.right div:nth-child(2){background-color:#1a140d}
.container .side{position:absolute;width:30px;height:120px;display:grid;grid-template-columns:50% 50%;
grid-template-rows:repeat(8,12.5%);gap:0;cursor:pointer;translate:0 12px;backface-visibility:hidden}
.container .side-left{transform:rotateY(-90deg) translateZ(15px) translateY(8px)}
.container .side-right{transform:rotateY(0deg) translateZ(15px) translateY(8px)}
.container .side div:nth-child(1){background-color:#443622}
.container .side div:nth-child(2){background-color:#2e2517}
.container .side div:nth-child(3),.side div:nth-child(5){background-color:#4b3b23}
.container .side div:nth-child(4),.side div:nth-child(10){background-color:#251e12}
.container .side div:nth-child(6){background-color:#292115}
.container .side div:nth-child(7){background-color:#4b3c26}
.container .side div:nth-child(8){background-color:#292115}
.container .side div:nth-child(9){background-color:#4b3a21}
.container .side div:nth-child(11),.side div:nth-child(15){background-color:#3d311d}
.container .side div:nth-child(12){background-color:#2c2315}
.container .side div:nth-child(13){background-color:#493a22}
.container .side div:nth-child(14){background-color:#2b2114}
.container .side div:nth-child(16){background-color:#271e10}
.container input:checked~.torch .face{filter:drop-shadow(0 0 2px rgb(255,255,255))
 drop-shadow(0 0 10px rgba(255,237,156,.7)) drop-shadow(0 0 25px rgba(255,227,101,.4))}
.container input:checked~.torch .top div:nth-child(1),
.container input:checked~.torch .left div:nth-child(3),
.container input:checked~.torch .right div:nth-child(3){background-color:#ffff97}
.container input:checked~.torch .top div:nth-child(2),
.container input:checked~.torch .left div:nth-child(1),
.container input:checked~.torch .right div:nth-child(1){background-color:#ffd800}
.container input:checked~.torch .top div:nth-child(3),
.container input:checked~.torch .left div:nth-child(4),
.container input:checked~.torch .right div:nth-child(4){background-color:#fff}
.container input:checked~.torch .top div:nth-child(4),
.container input:checked~.torch .left div:nth-child(2),
.container input:checked~.torch .right div:nth-child(2){background-color:#ff8f00}
.container input:checked~.torch .side div:nth-child(1){background-color:#7c623e}
.container input:checked~.torch .side div:nth-child(2){background-color:#4c3d26}
.container input:checked~.torch .side div:nth-child(3),
.container input:checked~.torch .side div:nth-child(5){background-color:#937344}
.container input:checked~.torch .side div:nth-child(4),
.container input:checked~.torch .side div:nth-child(10){background-color:#3c2f1c}
.container input:checked~.torch .side div:nth-child(6){background-color:#423522}
.container input:checked~.torch .side div:nth-child(7){background-color:#9f7f50}
.container input:checked~.torch .side div:nth-child(8){background-color:#403320}
.container input:checked~.torch .side div:nth-child(9){background-color:#977748}
.container input:checked~.torch .side div:nth-child(11),
.container input:checked~.torch .side div:nth-child(15){background-color:#675231}
.container input:checked~.torch .side div:nth-child(12){background-color:#3d301d}
.container input:checked~.torch .side div:nth-child(13){background-color:#987849}
.container input:checked~.torch .side div:nth-child(14){background-color:#3b2e1b}
.container input:checked~.torch .side div:nth-child(16){background-color:#372a17}
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
.addr{text-align:center;padding:30px 20px 28px}
.addr .lb{color:var(--sub);font-size:14px;letter-spacing:2px}
.addr .big{font:700 42px/1.3 Consolas,monospace;color:var(--bigtxt);margin:10px 0 6px;
text-shadow:0 0 30px var(--bigglow);word-break:break-all}
.addr .tip{color:var(--sub);font-size:13px}
.stat{background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.08);border-radius:13px;
padding:13px 16px;backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px)}
html.light .stat{background:rgba(255,255,255,.5);border-color:rgba(255,255,255,.65)}
.stat .k{color:var(--sub);font-size:12px}.stat .v{font-size:17px;font-weight:bold;margin-top:3px}
.banner{border-radius:12px;padding:12px 16px;font-size:14px;margin-bottom:16px;border:1px solid}
.banner.warn{background:rgba(234,179,8,.09);border-color:var(--warn);color:var(--warn)}
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
 if(!confirm('确定关闭开服器程序吗? (游戏服务器不受影响, 继续在后台运行)'))return;
 post('/api/manager/shutdown').finally(()=>{document.body.innerHTML='<div style="display:flex;align-items:center;justify-content:center;height:100vh;color:#94a3b8;font-size:16px">开服器已关闭, 可以关闭此页面了</div>';});}
function toast(msg,err){var w=document.getElementById('toast');var d=document.createElement('div');
d.className='tmsg'+(err?' err':'');d.textContent=msg;w.appendChild(d);
setTimeout(()=>d.remove(),3200);}
function post(url,body){return fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify(body||{})}).then(r=>{
 if(r.status===401){location.href='/login';throw new Error('unauth');}
 return r.json();});}
async function refreshStatus(){
 try{var r=await fetch('/api/status');
 if(r.status===401){location.href='/login';return;}
 var d=await r.json();
 var chip=document.getElementById('chip');var dot=document.getElementById('dot');
 if(chip){chip.textContent=d.running?('运行中 · PID '+(d.pid>0?d.pid:'外部')):'未运行';
 chip.className='chip '+(d.running?'on':'off');}
 var fs=document.getElementById('foot-st');
 if(fs)fs.textContent=chip?chip.textContent:'';
 if(dot)dot.className='dot'+(d.running?' on':'');
 document.querySelectorAll('[data-show-run]').forEach(e=>e.style.display=d.running?'':'none');
 document.querySelectorAll('[data-show-stop]').forEach(e=>e.style.display=d.running?'none':'');
 var bc=document.getElementById('bigcode');
 if(bc){bc.textContent=d.code?d.code:'开服后自动出现在这里…';
 var lk=document.getElementById('codelink');
 if(lk)lk.style.display=d.code?'':'none';}
 }catch(e){}}
function initGlass(){
 document.querySelectorAll('.pills').forEach(g=>{
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
document.querySelectorAll('[data-act]').forEach(b=>b.addEventListener('click',async()=>{
 var a=b.dataset.act;
 if(a=='stop'&&!confirm('确定要关闭服务器吗?\\n开服器会先自动保存世界, 再安全关闭。'))return;
 if(a=='restart'&&!confirm('确定要重启服务器吗? 也会自动保存世界。'))return;
 b.disabled=true;var r=await post('/api/'+a);b.disabled=false;toast(r.msg,r.ok?0:1);
 setTimeout(refreshStatus,1200);}));
syncThemeSwitch();
refreshStatus();setInterval(refreshStatus,4000);
"""


def torch_html(checked=False, onchange="", ident="", cls=""):
    """3D 火把勾选框 (Uiverse.io by kelvyn_8843), checked=点燃"""
    face4 = "<div></div>" * 4
    side16 = "<div></div>" * 16
    attrs = (f'id="{ident}"' if ident else "") + (" checked" if checked else "") + f' {onchange}'
    return (f'<label class="container {cls}">'
            f'<input type="checkbox"{attrs}>'
            f'<span class="torch">'
            f'<span class="head">'
            f'<span class="face top">{face4}</span>'
            f'<span class="face left">{face4}</span>'
            f'<span class="face right">{face4}</span>'
            f'</span>'
            f'<span class="stick">'
            f'<span class="face side side-left">{side16}</span>'
            f'<span class="face side side-right">{side16}</span>'
            f'</span>'
            f'</span>'
            f'</label>')


def shell(page, title, content, script=""):
    IC = {
        "dash": '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
        "quick": '<path d="M13 2L5 13h6l-1.5 9L19 9.5h-6.2L13 2z"/>',
        "cmds": '<rect x="2.5" y="7" width="19" height="10" rx="5"/><path d="M7 12h4M9 10v4"/><circle cx="15.5" cy="11" r="0.6"/><circle cx="17.5" cy="13" r="0.6"/>',
        "game": '<path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="2.2"/><circle cx="15" cy="17" r="2.2"/>',
        "ws": '<path d="M21 8l-9-5-9 5v8l9 5 9-5V8z"/><path d="M3 8l9 5 9-5M12 13v8"/>',
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
        ("term", "控制台", "/console"),
        ("log", "操作日志", "/logs"),
        ("files", "文件管理", "/files"),
        ("help", "帮助", "/help"),
    ]
    links = "".join(
        f'<a href="{href}" class="{"on" if key == page else ""}">'
        f'<span class="ico"><svg viewBox="0 0 24 24">{IC[key]}</svg></span>{name}</a>'
        for key, name, href in nav)
    foot = (f'存档: <b>{esc(instance())}</b> · <a href="/setup">切换/重设</a>'
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
  <div class="sub">Unturned 开服器 · by Pippl</div></div></div>
  <nav class="nav">{links}</nav>
  <div class="foot">{foot}</div>
</aside>
<main class="main">
<div id="toast"></div>
<div class="pagehead"><h1>{title}</h1>
<span class="chip" id="chip">检测中…</span>
<span style="flex:1"></span>
<button class="btn green" data-act="start" data-show-stop>开服</button>
<button class="btn gray" data-act="restart" data-show-run>重启</button>
<button class="btn red" data-act="stop" data-show-run>关服</button>
{THEME_SWITCH}
</div>
{content}
</main></div>
<script>{SHELL_JS}
{script}</script></body></html>"""


# ================================================================ 仪表盘
def page_dash():
    info = cmd_info()
    content = f"""
<div class="card addr">
  <div class="lb">服 务 器 代 码 ( P2P 直 连 )</div>
  <div class="big" id="bigcode">开服后自动出现在这里…</div>
  <div class="tip">开服后把这段 <b>服务器代码</b> 发给你的朋友 → 游戏内按 <b>Play → 输入服务器代码</b> 即可加入, 无需端口映射</div>
  <div style="margin-top:18px">
    <button class="button" onclick="copyCode()" id="codelink" style="display:none"><span class="hoverEffect"><div></div></span><span class="txt">复制服务器代码</span></button>
  </div>
  <div class="hint" style="margin-top:14px">同一 WiFi/局域网的朋友也可以直接连: <b id="st-ip">…</b> (游戏内 Play → 直连)</div>
</div>
<div class="grid g4">
  <div class="stat"><div class="k">服务器名称</div><div class="v">{esc(info['name'])}</div></div>
  <div class="stat"><div class="k">地图</div><div class="v">{esc(info['map'])}</div></div>
  <div class="stat"><div class="k">难度</div><div class="v">{esc(info['mode'])}</div></div>
  <div class="stat"><div class="k">人数上限</div><div class="v">{esc(info['maxplayers'])}</div></div>
</div>
<div class="grid g2" style="margin-top:14px">
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
· 服务器代码每次开服可能会变化, 以最新显示的为准<br>
· 以后往 <code class="k">Rocket\\Plugins</code> 里放插件, 它的配置文件会自动出现在文件管理里
</div></div>
"""
    script = """
function copyCode(){navigator.clipboard.writeText(document.getElementById('bigcode').textContent)
 .then(()=>toast('✔ 服务器代码已复制, 发给朋友即可加入'));}
"""
    return shell("dash", "仪表盘", content, script)


# ================================================================ 一键设置
def page_quick():
    rows = []
    for t in TOGGLES:
        on = all(get_cfg(config_txt(), s, k) == v for s, k, v in t["keys"])
        keys = "".join(f'<code class="k">{k}</code>' for _, k, _ in t["keys"])
        torch = torch_html(on, onchange='onchange="toggle(\'%s\',this)"' % t["id"],
                           ident="sw_" + t["id"], cls="torch-lg")
        rows.append(f"""
<div class="trow"><div class="bar"></div>
<div><div class="nm">{t['name']}</div><div class="ds">{t['desc']}</div>
<div class="st {'on' if on else 'off'}" id="st_{t['id']}">当前: {'✅ 已开启' if on else '⭕ 未开启 (默认)'}</div>
<div style="margin-top:5px">{keys}</div></div>
<div class="rgt">{torch}</div></div>""")

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

    content = f"""
<div class="card"><h2>一键开关</h2>
<div class="desc">点一下开关就生效并自动保存到 Config.txt, 关掉开关恢复游戏默认。改完记得重启服务器。</div>
{''.join(rows)}</div>
<div class="card"><h2>常用强度</h2>
<div class="desc">选一个想要的档位即可, 自动写入配置。</div>
{''.join(sel_rows)}</div>
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
    known = {k for k, *_ in CMD_FIELDS} | {"pvp", "pve"}
    maps = list_maps()
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

    map_opts = '<option value="">— 选择地图 —</option>' + "".join(
        f'<option value="{m}" {"selected" if vals.get("map", "").lower() == m else ""}>{m}</option>'
        for m in maps)
    others = [e for e in entries if e["key"] and e["key"].lower() not in known
              and e["key"].lower() != "pvpve"]
    other_html = ""
    if others:
        rows = "".join(
            f'<div><label class="f"><b>{esc(e["key"])}</b><code class="k">{esc(e["key"])}</code></label>'
            f'<input class="f" data-key="{esc(e["key"])}" value="{esc(e["value"])}"></div>'
            for e in others)
        other_html = f'<div class="card"><h2>其他配置项 (自动识别)</h2><div class="grid g3">{rows}</div></div>'

    content = f"""
<div class="card"><h2>基础信息</h2>
<div class="grid g2">
<div><label class="f"><b>服务器名称</b><code class="k">name</code></label>
<input class="f" data-key="name" id="i_name" value="{esc(vals.get('name',''))}"></div>
<div><label class="f"><b>地图</b><code class="k">map</code></label>
<select class="f" id="i_map">{map_opts}</select>
<label class="f" style="font-size:12px">地图列表里没有? 在这里自己填:</label>
<input class="f" id="i_map_custom" placeholder="手动输入地图名"></div>
</div>
<div class="grid g2">
<div><label class="f"><b>最大玩家数</b><code class="k">maxplayers</code></label>
<input class="f" data-key="maxplayers" type="number" value="{esc(vals.get('maxplayers','24'))}"></div>
<div><label class="f"><b>端口</b><code class="k">port</code></label>
<input class="f" data-key="port" type="number" value="{esc(vals.get('port','26010'))}">
<div class="hint">多个存档同时开服时端口不能相同</div></div>
</div></div>

<div class="card"><h2>游戏方式</h2>
<div><label class="f"><b>对战模式</b><code class="k">PVP / PVE</code></label>
<div class="pills">{pvpve}</div></div>
<div><label class="f"><b>游戏难度</b><code class="k">mode</code></label>
<div class="pills">{mode_pills}</div></div>
<div><label class="f"><b>允许的视角</b><code class="k">perspective</code></label>
<div class="pills">{persp_pills}</div></div>
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
<div class="card"><h2>启动参数</h2>
<div class="desc">启动命令固定为原版方式: <code class="k">-nographics -batchmode +Secureserver/存档名</code>。
改完下面的选项点保存并重启服务器生效。</div>
<div class="tip">萌新联机只需要用<b>服务器代码</b> (仪表盘正中间那串数字): 代码联机走 Steam P2P,
<b>不需要</b> <code class="k">Login_Token</code>、不需要端口映射。不填 Login_Token 只影响"互联网服务器列表
/ 公网 IP 直连"这两种方式, 代码联机不受任何影响。</div>
<div style="margin-top:12px;display:flex;gap:16px 24px;flex-wrap:wrap;align-items:center">
{torch_html(lc['console'], ident="l_console", cls="torch-lg")}<span><b>原版控制台窗口</b> (推荐) — 服务器有自己独立的黑色控制台窗口, 关闭开服器/网页后<b>服务器继续运行</b></span>
</div>
<div style="margin-top:10px;display:flex;gap:16px 24px;flex-wrap:wrap;align-items:center">
{torch_html(lc['batchmode'], ident="l_batch")}<span><b>-batchmode</b> 无窗口后台运行 (推荐, 默认勾选)</span>
{torch_html(lc['nographics'], ident="l_nographics")}<span><b>-nographics</b> 不加载显卡渲染 (推荐, 默认勾选)</span>
</div>
<div class="hint">"原版控制台窗口"关闭时: 服务器完全后台静默运行, 网页「运行日志」会更完整(实时接管输出)。</div>
<div style="margin-top:12px;display:flex;gap:16px 24px;flex-wrap:wrap;align-items:center">
{torch_html(close_stop, ident="l_closestop", cls="torch-lg")}<span><b>关闭 bat 窗口时同时关闭服务器</b> (默认关闭 — 关闭后服务器会一直驻留, 直到手动关服)</span>
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
<span class="hint" style="margin-left:12px">保存后需要重启服务器才会生效</span></div>
"""
    script = """
function save(){
 var kv={};var bad='';
 document.querySelectorAll('input.f[data-key]').forEach(el=>{
  var v=el.value.trim();
  if(el.dataset.key==='maxplayers'||el.dataset.key==='port'){
   if(v!==''&&!/^[0-9]+$/.test(v))bad=el.dataset.key;}
  kv[el.dataset.key]=v;});
 var c=document.getElementById('i_map_custom').value.trim();
 if(c)kv.map=c;
 var mode=document.querySelector('input[name=p_mode]:checked');
 if(mode)kv.mode=mode.value;
 var pv=document.querySelector('input[name=p_pvpve]:checked');
 if(pv)kv[pv.value]='';
 var pe=document.querySelector('input[name=p_perspective]:checked');
 if(pe)kv.perspective=pe.value;
 if(bad){toast('「'+bad+'」应该填数字',1);return;}
 post('/api/commands',{kv:kv}).then(r=>toast(r.ok?'✔ 已保存! 重启服务器后生效':'✘ '+r.msg,r.ok?0:1));}
async function saveLaunch(){
 var r=await post('/api/launch',{
  batchmode:document.getElementById('l_batch').checked,
  nographics:document.getElementById('l_nographics').checked,
  console:document.getElementById('l_console').checked,
  close_stop:document.getElementById('l_closestop').checked,
  extra:document.getElementById('l_extra').value.trim()});
 toast(r.msg,r.ok?0:1);}
async function installRocket(){
 if(!confirm('确定安装/更新 Rocket 吗? 安装来源为游戏自带的 Install Rocket.bat。'))return;
 toast('正在安装, 请稍候…');
 var r=await post('/api/rocket/install');
 toast(r.msg,r.ok?0:1);setTimeout(()=>location.reload(),1500);}
function openPlugins(){post('/api/rocket/open');}
"""
    return shell("cmds", "服务器设置", content, script)


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
<div class="card"><h2>{sec_cn} <code class="k">{sec}</code></h2>
<div class="grid g3">{rows}</div></div>"""

    content = f"""
<div class="card"><h2>玩法参数精细调节</h2>
<div class="desc">每一项都会写入 <code class="k">Config.txt</code>。数字项留空 = 游戏默认; 改完点下面的保存并重启服务器。
推荐先去「一键设置」页搞定常用的。</div></div>
{sections_html}
<div class="card">
<button class="btn big" onclick="save()">💾 保存全部修改</button>
<a class="btn big gray" href="/edit?path=Config.txt">原始编辑器</a></div>
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
 toast(r.ok?'✔ 已保存! 重启服务器后生效 (共 '+r.count+' 项)':'✘ '+r.msg,r.ok?0:1);}
"""
    return shell("game", "玩法设置", content, script)


# ================================================================ 创意工坊
def page_workshop():
    full = os.path.join(server_dir(), "WorkshopDownloadConfig.json")
    try:
        data = json.loads(read_file(full)[0]) if os.path.isfile(full) else {}
    except Exception:
        data = {}
    ids_text = "\n".join(str(i) for i in data.get("File_IDs", []))
    ign_text = "\n".join(str(i) for i in data.get("Ignore_IDs", []))
    content = f"""
<div class="card"><h2>要下载的模组 ID <code class="k">File_IDs</code></h2>
<div class="desc">在 Steam 创意工坊模组页面的网址里找 <code class="k">id=数字</code>, 那串数字就是模组 ID。
服务器启动时会自动下载, 玩家进服也会自动加载。每行填一个。</div>
<textarea class="f" id="ids" rows="7" style="margin-top:10px">{esc(ids_text)}</textarea></div>
<div class="card"><h2>忽略的模组 ID <code class="k">Ignore_IDs</code></h2>
<div class="desc">一般不用填。</div>
<textarea class="f" id="ign" rows="4" style="margin-top:10px">{esc(ign_text)}</textarea></div>
<div class="card"><button class="btn big" onclick="save()">💾 保存</button></div>
"""
    script = """
async function save(){
 var parse=t=>t.split(/\\s+/).map(s=>s.trim()).filter(s=>/^[0-9]+$/.test(s)).map(Number);
 var r=await post('/api/workshop',{ids:parse(document.getElementById('ids').value),
  ignores:parse(document.getElementById('ign').value)});
 toast(r.ok?'✔ 已保存, 重启服务器后生效':'✘ '+r.msg,r.ok?0:1);}
"""
    return shell("ws", "创意工坊", content, script)


# ================================================================ 控制台
def page_console():
    content = f"""
<div class="card"><h2>在线玩家</h2>
<div class="desc">点「刷新玩家列表」获取在线玩家。给物品/车辆/无敌/隐身/传送属于作弊指令, 需要服务器开启 Cheats。</div>
<div style="display:flex;gap:10px;margin:10px 0;flex-wrap:wrap">
<button class="btn" onclick="refreshPlayers()">刷新玩家列表</button>
<span class="hint" id="pcount" style="align-self:center"></span></div>
<div id="plist" class="hint">尚未获取。获取后可对玩家执行: 给物品 / 给车辆 / 无敌 / 隐身 / 传送到其他玩家身边 / 踢出</div>
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
<label class="container" style="margin-left:14px">{torch_html(ident="f_err")}<span class="lbl">只看报错/警告</span></label>
</h2>
<div class="term" id="term"></div>
<div class="hint" id="logsrc"></div>
<div class="hint">实时滚动 · <span style="color:#fca5a5">红色</span>=报错
<span style="color:#fde047">黄色</span>=警告 <span style="color:#6ee7b7">绿色</span>=服务器代码 · 来自 Rocket 日志;
刚装完 Rocket 或刚开服时日志需要等服务器完全启动后才会出现, 服务器自己的黑色控制台窗口里始终有实时输出</div></div>
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
  mk('无敌',()=>post('/api/console',{cmd:'god '+target}).then(r=>toast(r.ok?'已发送: god '+target:r.msg,r.ok?0:1)));
  mk('隐身',()=>post('/api/console',{cmd:'vanish '+target}).then(r=>toast(r.ok?'已发送: vanish '+target:r.msg,r.ok?0:1)));
  mk('传送到…',()=>{var dst=prompt('把「'+p.name+'」传送到哪位玩家身边?');if(!dst)return;
    var cmd='teleport '+target+' '+dst.trim();
    post('/api/console',{cmd:cmd}).then(r=>toast(r.ok?'已发送: '+cmd:r.msg,r.ok?0:1));});
  mk('踢出',()=>{var rs=prompt('踢出「'+p.name+'」的原因(可空):');if(rs===null)return;
    var cmd='kick '+p.name+(rs.trim()?' '+rs.trim():'');
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
<div class="card"><h2>第一次开服 (3 步)</h2>
<div class="desc" style="line-height:2.1">
1. 首次打开会进入「初始设置」: 选游戏目录 → 选存档(或取名新建)<br>
2. 回到<b>仪表盘</b>点「开服」, 等 30~60 秒, 中间出现一串数字 = <b>服务器代码</b><br>
3. 点「复制服务器代码」发给朋友 → 朋友游戏内 <b>Play → 输入服务器代码</b> 即可联机 (无需端口映射)<br>
<span class="hint">注意: 服务器代码每次开服都会变化, 以最新的为准。朋友输入代码无反应/报错时, 先确认你这边服务器显示"运行中", 再让朋友重启 Steam 后重试。</span>
</div></div>

<div class="card"><h2>每个菜单是干什么的</h2>
<div class="desc" style="line-height:2.1">
<b>仪表盘</b> — 开服 / 重启 / 关服, 查看服务器代码和基本信息<br>
<b>一键设置</b> — 最常用的傻瓜开关: 死亡不掉落、建筑无敌、车辆无敌、僵尸不拆家、摔落伤害、组队友伤、空投、出生满技能; 僵尸强度 / 经验倍率 / 物资丰富度 / 天气<br>
<b>服务器设置</b> — 服务器名称、地图、人数、端口、PVP/PVE、难度、视角、进服密码; 以及<b>启动参数</b>和 <b>Rocket 安装</b><br>
<b>玩法设置</b> — 进阶参数: 血量、经验、刷怪、刷车、建筑承伤、空投频率、天气等 (每项都有中文说明)<br>
<b>创意工坊</b> — 填模组 ID 自动下载 Steam 创意工坊模组<br>
<b>控制台</b> — 实时日志和报错、发送命令、<b>在线玩家管理</b> (给物品/车辆、无敌、隐身、传送、踢出)、白天/黑夜等快捷指令<br>
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

<div class="card"><h2>控制台玩家管理</h2>
<div class="desc" style="line-height:2.1">
「刷新玩家列表」后可对每位玩家: <b>给物品</b>(give) / <b>给车辆</b>(vehicle) / <b>无敌</b>(god, 再点一次取消) /
<b>隐身</b>(vanish, 再点一次取消) / <b>传送到其他玩家身边</b>(teleport) / <b>踢出</b>(kick)<br>
物品和载具 ID 可在游戏内按物品分类查询, 或搜索 "Unturned Item ID"<br>
<span class="hint">这些属于作弊指令, 需要服务器开启 Cheats (本工具新建的存档默认已开启; 也可在 Commands.dat 里加一行 Cheats)。
给物品/车辆也可以直接用玩家名或 SteamID 写在命令里。</span>
</div></div>

<div class="card"><h2>常见问题</h2>
<div class="desc" style="line-height:2.1">
<b>改了配置没生效?</b> 配置修改后必须<b>重启服务器</b>; 而且改之前先关服, 否则服务器退出时会把旧配置写回去<br>
<b>朋友用代码进不来?</b> ① 确认代码是最新一次开服的 ② 双方重启 Steam 再试 ③ 出现 "Lost connection" 多为网络中转问题, 换直连 IP 方式: 同一网络用 仪表盘显示的 局域网IP:端口, 远程朋友则需要路由器端口映射(端口见服务器设置)或内网穿透<br>
<b>怎么安全关服?</b> 点「关服」或控制台输入 <code class="k">shutdown</code>, 都会自动保存世界; 强制结束进程才会丢档<br>
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
<label class="container">{torch_html(True, ident="autoref")}<span class="lbl">自动刷新</span></label>
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
<h2 style="margin-top:12px">Dawn Sharkk</h2><div class="desc">Unturned 开服器 · by Pippl</div>
<div class="desc">请输入<b>登录密钥</b>进入管理页面</div>
<input class="f" id="pw" placeholder="粘贴或输入密钥" maxlength="32"
 onkeydown="if(event.key==='Enter')doLogin()" autofocus>
<button class="btn" onclick="doLogin()">登 录</button>
<div class="keyhint">密钥在启动开服器.bat 的黑色命令框里 (已自动复制到剪贴板, 直接 Ctrl+V)<br>
注意: 密钥每次启动开服器都会变化, 以命令框里显示的最新密钥为准</div>
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
async function doLogin(){{
 var pw=document.getElementById('pw').value.trim();
 if(!pw){{toast('请输入密钥',1);return;}}
 var r=await fetch('/api/login',{{method:'POST',headers:{{'Content-Type':'application/json'}},
  body:JSON.stringify({{password:pw}})}});
 if(r.status===200){{location.href='/';return;}}
 toast('密钥不对, 请查看命令框里的登录密钥',1);}}
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
  if(r&&r.ok){{location.href='/';return;}}
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

    def _json(self, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
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
            return self._redirect("/login")
        if u.path == "/api/status":
            _push_log_tails()   # 仪表盘轮询时同步日志, 确保 Server Code 及时被抓取
            running, pid = server_running()
            info = cmd_info()
            return self._json({
                "running": running, "pid": pid, "ip": lan_ip(), "port": info["port"],
                "name": info["name"], "map": info["map"], "mode": info["mode"],
                "code": _server_code[0],
                "setup": setup_done(),
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
            return self._json({"ok": True, "msg": f"已选择存档「{name}」"})

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
            oplog("操作", "保存服务器设置 (Commands.dat)")
            return self._json({"ok": True})

        if u.path == "/api/workshop":
            full = os.path.join(server_dir(), "WorkshopDownloadConfig.json")
            try:
                data = json.loads(read_file(full)[0]) if os.path.isfile(full) else {}
            except Exception:
                data = {}
            data["File_IDs"] = [int(i) for i in body.get("ids", [])]
            data["Ignore_IDs"] = [int(i) for i in body.get("ignores", [])]
            write_file(full, json.dumps(data, indent=2, ensure_ascii=False))
            oplog("操作", f"保存创意工坊列表 ({len(data['File_IDs'])} 个模组)")
            return self._json({"ok": True})

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
            oplog("操作", f"一键开关「{t['name']}」→ {'开启' if on else '恢复默认'}")
            return self._json({"ok": True, "msg": f"「{t['name']}」已{'开启' if on else '恢复默认'}, 重启服务器后生效"})

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
    # 把本次登录密钥放进剪贴板, 打开网页后直接 Ctrl+V
    try:
        p = subprocess.Popen(["clip"], stdin=subprocess.PIPE)
        p.communicate(secret.encode("ascii", "ignore"))
    except OSError:
        pass
    oplog("操作", "开服器启动")
    print("=" * 58)
    print("  Dawn Sharkk 已启动 (Unturned 开服器)")
    print(f"  网页地址: http://{HOST}:{PORT}   (请自行用浏览器打开)")
    print(f"  登录密钥: {secret}")
    print("  >> 已自动复制到剪贴板, 打开网页后直接 Ctrl+V 粘贴 <<")
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
