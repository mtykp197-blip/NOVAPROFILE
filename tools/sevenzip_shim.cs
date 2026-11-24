using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;

// A stand-in for 7-Zip's console binary.
//
// The Windows build of electron-builder unpacks its signing tools with
// "7za -snld", and the -snld switch asks the machine for the
// symbolic-link privilege.  A Windows machine that has Developer Mode
// switched off refuses, and the unpack fails on the two Darwin
// libraries the archive happens to carry -- libraries that a Windows
// build never reads.
//
// This wrapper drops the -snld switch and hands the rest of the
// command line to the real binary.  The symbolic links then land as
// ordinary files, which is all the Windows build needs.
internal static class Program
{
    private static int Main(string[] args)
    {
        string real = FindRealBinary();

        List<string> forwarded = new List<string>();
        foreach (string arg in args)
        {
            // "-snld" asks for symlinks; "-snl-" asks for them to be
            // treated as plain files.  Everything else is passed on
            // untouched.
            if (string.Equals(arg, "-snld", StringComparison.OrdinalIgnoreCase))
            {
                forwarded.Add("-snl-");
                continue;
            }

            forwarded.Add(arg);
        }

        ProcessStartInfo start = new ProcessStartInfo
        {
            FileName = real,
            UseShellExecute = false,
            Arguments = Quote(forwarded),
        };

        // The unpack is noisy and electron-builder reads its output, so
        // the streams are passed straight through.
        start.RedirectStandardOutput = false;
        start.RedirectStandardError = false;

        using (Process child = Process.Start(start))
        {
            if (child == null)
            {
                return 1;
            }

            child.WaitForExit();
            return child.ExitCode;
        }
    }

    private static string Quote(IEnumerable<string> args)
    {
        StringBuilder line = new StringBuilder();
        foreach (string arg in args)
        {
            if (line.Length > 0)
            {
                line.Append(' ');
            }

            if (arg.Length > 0 && arg.IndexOf(' ') >= 0)
            {
                line.Append('"').Append(arg).Append('"');
            }
            else
            {
                line.Append(arg);
            }
        }

        return line.ToString();
    }

    private static string FindRealBinary()
    {
        string directory = Path.GetDirectoryName(
            Path.GetFullPath(Process.GetCurrentProcess().MainModule.FileName));

        string candidate = Path.Combine(directory, "7za-real.exe");
        if (File.Exists(candidate))
        {
            return candidate;
        }

        // The wrapper was installed somewhere else, so look next to
        // the archive it is expected to unpack.
        return Path.Combine(directory, "7za.exe");
    }
}
