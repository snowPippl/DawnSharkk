// Dawn Sharkk 启动器 —— 双击弹出一个控制台窗口, 在里面用文件夹自带的 python 跑 server_manager.py。
// 用 Windows 自带的 C# 编译器编译, 不引入任何第三方依赖:  python\python.exe 构建启动器.py
// 想自己核对的人: 本文件就是启动器的全部源码, 它只做上面这一件事。
using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;

[assembly: AssemblyTitle("Dawn Sharkk 开服器")]
[assembly: AssemblyDescription("Dawn Sharkk —— Unturned 开服器 (本地网页版)")]
[assembly: AssemblyCompany("Pippl")]
[assembly: AssemblyProduct("Dawn Sharkk")]
[assembly: AssemblyCopyright("Copyright © 2026 snowPippl · MIT License")]
[assembly: AssemblyVersion("__VERSION__.0")]
[assembly: AssemblyFileVersion("__VERSION__.0")]
[assembly: AssemblyInformationalVersion("__VERSION__")]

namespace DawnSharkk
{
    internal static class Launcher
    {
        private const string Title = "Dawn Sharkk";

        private static int Main(string[] args)
        {
            try { Console.Title = Title; } catch (Exception) { }

            // exe 所在目录就是开服器根目录: 整个文件夹可以随便放, 不写死任何人的电脑路径
            string root = AppDomain.CurrentDomain.BaseDirectory;
            string python = Path.Combine(Path.Combine(root, "python"), "python.exe");
            string script = Path.Combine(root, "server_manager.py");

            Console.WriteLine("============================================================");
            Console.WriteLine("  " + Title + "  -  Unturned 开服器  (by Pippl)");
            Console.WriteLine("============================================================");
            Console.WriteLine();

            if (!File.Exists(python))
            {
                Fail("这个文件夹里没有自带的 python 运行库:\n  " + python +
                     "\n\n请确认整个 DawnSharkk 文件夹是完整解压出来的 (不能只拷这一个 exe)。");
                return Pause(1, false);
            }
            if (!File.Exists(script))
            {
                Fail("这个文件夹里没有 server_manager.py:\n  " + script +
                     "\n\n请把启动器放回开服器文件夹里再双击。");
                return Pause(1, false);
            }

            Console.WriteLine("正在启动, 请稍候...");
            Console.WriteLine();
            try
            {
                var psi = new ProcessStartInfo
                {
                    FileName = python,
                    Arguments = "\"" + script + "\"",
                    WorkingDirectory = root,
                    UseShellExecute = false,
                };
                using (var p = Process.Start(psi))
                {
                    if (p == null)
                    {
                        Fail("启动 python 失败, 请截图本窗口报错反馈给作者。");
                        return Pause(1, false);
                    }
                    p.WaitForExit();
                    return Pause(p.ExitCode, true);
                }
            }
            catch (Exception e)
            {
                Fail("启动失败: " + e.Message +
                     "\n\n若杀毒软件拦截过 python\\python.exe, 请把它加入白名单后重试。");
                return Pause(1, false);
            }
        }

        private static void Fail(string msg)
        {
            Console.WriteLine();
            Console.WriteLine("[x] " + msg);
        }

        private static int Pause(int code, bool ranServer)
        {
            Console.WriteLine();
            if (ranServer)
            {
                Console.WriteLine("[!] Dawn Sharkk 已退出。游戏会把数据和配置写回存档, 地图可能仍在关闭中。");
            }
            Console.WriteLine();
            Console.WriteLine("按任意键关闭本窗口...");
            try { Console.ReadKey(true); } catch (InvalidOperationException) { }
            return code;
        }
    }
}
