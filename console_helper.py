# -*- coding: utf-8 -*-
"""Dawn Sharkk 控制台窗口助手:

服务器必须由它自己拥有控制台窗口 (原版起法), 否则那个窗口里打不进指令 ——
实测: 一旦把 Unturned 的 stdout 接进管道, 它就不再接收键盘输入。
所以这里不中继输出, 只做两件锦上添花的事: 给服务器那个控制台窗口换上开服器图标,
并把「当前玩家 N」写进窗口标题。
用法: python console_helper.py <服务器PID> <标题文件> <服务器名称>
"""
import ctypes
import os
import sys
import time
from ctypes import wintypes

sys.stdout.reconfigure(errors="replace")

srv_pid = int(sys.argv[1])
title_file = sys.argv[2]
srv_name = sys.argv[3] if len(sys.argv) > 3 else ""

# ================================================================ Win32 API 声明
k32 = ctypes.windll.kernel32
u32 = ctypes.windll.user32

# 64 位系统必须显式声明句柄类型, 否则 HICON/HWND 被 32 位截断导致图标设置失败
k32.GetConsoleWindow.argtypes = []
k32.GetConsoleWindow.restype = wintypes.HWND
k32.SetConsoleTitleW.argtypes = [ctypes.c_wchar_p]
k32.AttachConsole.argtypes = [wintypes.DWORD]
k32.AttachConsole.restype = wintypes.BOOL
k32.FreeConsole.restype = wintypes.BOOL
k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
k32.OpenProcess.restype = wintypes.HANDLE
k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
k32.GetExitCodeProcess.restype = wintypes.BOOL

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
STILL_ACTIVE    = 259
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def alive():
    """服务器进程还在不在 (没了本助手就自己退出, 不留僵尸)"""
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, srv_pid)
    if not h:
        return False
    try:
        code = wintypes.DWORD(0)
        if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        k32.CloseHandle(h)


def attach():
    """接到服务器的控制台上去; 服务器刚起步时可能还没建好控制台, 重试一会儿"""
    for _ in range(60):
        if not alive():
            return False
        k32.FreeConsole()
        if k32.AttachConsole(srv_pid):
            return True
        time.sleep(0.5)
    return False


def _load_ico(size):
    """从脚本同目录加载 logo.ico, 失败返回 None"""
    ico = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.ico")
    if not os.path.isfile(ico):
        return None
    h = u32.LoadImageW(None, ico, IMAGE_ICON, size, size, LR_LOADFROMFILE)
    return h or None


def set_icon():
    """把服务器控制台窗口/任务栏图标换成开服器头像 (logo.ico)

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


def set_title(t):
    try:
        k32.SetConsoleTitleW(t)
    except Exception:
        pass


# ================================================================ 接管那个窗口
if not attach():
    sys.exit(0)
set_icon()
set_title(f"DawnSharkk - {srv_name} | 当前玩家 0")
time.sleep(3)          # 少数窗口建得慢, 延迟几秒再补一次图标
set_icon()

while alive():
    try:
        if os.path.isfile(title_file):
            n = open(title_file, encoding="utf-8", errors="replace").read().strip()
            if n.isdigit():
                set_title(f"DawnSharkk - {srv_name} | 当前玩家 {n}")
    except Exception:
        pass
    time.sleep(3)
