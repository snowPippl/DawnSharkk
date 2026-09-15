# -*- coding: utf-8 -*-
"""Dawn Sharkk 控制台中继器:
以原版方式启动 Unturned 服务器, 把输出实时显示在本窗口(等同原版控制台),
并同步写入日志文件供网页「运行日志」读取。
窗口标题: DawnSharkk - 服务器名称 | 当前玩家 N (玩家数由开服器写入 title.tmp)
用法: python reader.py <日志文件> <标题文件> <服务器名称> <Unturned.exe> <启动参数...>
"""
import ctypes
import os
import subprocess
import sys
import threading
import time
from ctypes import wintypes

sys.stdout.reconfigure(errors="replace")

log_path = sys.argv[1]
title_file = sys.argv[2]
srv_name = sys.argv[3]
cmd = sys.argv[4:]

# ================================================================ Win32 API 声明
k32 = ctypes.windll.kernel32
u32 = ctypes.windll.user32

# 64 位系统必须显式声明句柄类型, 否则 HICON/HWND 被 32 位截断导致图标设置失败
k32.GetConsoleWindow.argtypes = []
k32.GetConsoleWindow.restype = wintypes.HWND
k32.SetConsoleTitleW.argtypes = [ctypes.c_wchar_p]

u32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                           ctypes.c_int, ctypes.c_int, wintypes.UINT]
u32.LoadImageW.restype = wintypes.HANDLE
u32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM]
u32.SendMessageW.restype = wintypes.LPARAM
u32.GetSystemMetrics.argtypes = [ctypes.c_int]
u32.GetSystemMetrics.restype = ctypes.c_int

# ---- 常量 (Win32 真实值) ----
IMAGE_ICON      = 1
LR_LOADFROMFILE = 0x0010      # ★ 正解: 0x10, 不是 1
LR_DEFAULTSIZE  = 0x0040
WM_SETICON      = 0x0080      # ★ 正解
ICON_SMALL      = 0
ICON_BIG        = 1
SM_CXICON, SM_CYICON, SM_CXSMICON, SM_CYSMICON = 11, 12, 49, 50


def set_title(t):
    k32.SetConsoleTitleW(t)


def _load_ico(size):
    """从脚本同目录加载 logo.ico, 失败返回 None"""
    ico = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.ico")
    if not os.path.isfile(ico):
        return None
    h = u32.LoadImageW(None, ico, IMAGE_ICON, size, size, LR_LOADFROMFILE)
    return h or None


def set_icon():
    """把本控制台窗口/任务栏图标设置为开服器头像 (logo.ico)

    注意: 加载出来的两个 HICON 必须一直持有, 不能 DestroyIcon,
    否则窗口/任务栏会重新变回默认图标。
    """
    try:
        hwnd = k32.GetConsoleWindow()
        if not hwnd:
            return
        big_w   = u32.GetSystemMetrics(SM_CXICON)    or 32
        small_w = u32.GetSystemMetrics(SM_CXSMICON)  or 16
        h_big   = _load_ico(big_w)
        h_small = _load_ico(small_w)
        if h_big:
            u32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, h_big)
        if h_small:
            u32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, h_small)
    except Exception:
        pass


# ================================================================ 初始化窗口
set_icon()
set_title(f"DawnSharkk - {srv_name} | 当前玩家 0")
# 双保险: 极少数情况下窗口尚未完全建好, 延迟再设一次
threading.Timer(0.3, set_icon).start()


def _title_loop():
    """每 3 秒读一次开服器写入的在线人数, 更新窗口标题"""
    while True:
        try:
            if os.path.isfile(title_file):
                n = open(title_file, encoding="utf-8", errors="replace").read().strip()
                if n.isdigit():
                    set_title(f"DawnSharkk - {srv_name} | 当前玩家 {n}")
        except Exception:
            pass
        time.sleep(3)


threading.Thread(target=_title_loop, daemon=True).start()

# ================================================================ 启动服务器并转发输出
p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
log = open(log_path, "w", encoding="utf-8", errors="replace")
try:
    for raw in iter(p.stdout.readline, b""):
        if not raw:
            break
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = raw.decode("gbk")
            except UnicodeDecodeError:
                text = raw.decode("utf-8", "replace")
        sys.stdout.write(text)
        sys.stdout.flush()
        log.write(text)
        log.flush()
finally:
    log.close()
try:
    os.remove(title_file)
except OSError:
    pass
print()
print("服务器已退出, 可以关闭本窗口。")