"""Validate explicit launch choices before starting any helper or target."""

import os
from pathlib import Path
import shutil
import sys
from urllib.parse import urlsplit

from .dap import DebugError


def executable(value, label):
    found = shutil.which(value) if value else None
    if not found:
        raise DebugError("missing_dependency", f"{label} not found: {value}")
    return str(Path(found).resolve())


def resolve(args):
    workspace = Path(args["workspace"])
    suffix = Path(args["script"]).suffix.lower()
    inferred = {".py": "python", ".js": "javascript", ".cjs": "javascript", ".mjs": "javascript", ".ts": "typescript"}
    args["language"] = args["language"] or ("javascript" if args["target"] == "browser" else inferred.get(suffix))
    if not args["language"]:
        raise DebugError("invalid_request", "Specify --language python, javascript, typescript, or rust")
    args["language"] = {"js": "javascript", "ts": "typescript"}.get(args["language"], args["language"])
    is_js = args["language"] in ("javascript", "typescript")
    args["target"] = args["target"] or ("node" if is_js else "process")
    if (is_js and args["target"] not in ("node", "browser")) or (not is_js and args["target"] != "process"):
        raise DebugError("invalid_request", "Node/browser targets require JavaScript or TypeScript")
    if args["headed"] and args["target"] != "browser":
        raise DebugError("invalid_request", "--headed requires --target browser")
    args["cwd"] = str((workspace / (args["cwd"] or ".")).resolve())
    if not Path(args["cwd"]).is_dir():
        raise DebugError("invalid_request", "Working directory does not exist")
    if args["target"] == "browser":
        url = urlsplit(args["script"])
        if url.scheme not in ("http", "https") or url.hostname not in ("localhost", "127.0.0.1", "::1") or url.username:
            raise DebugError("invalid_request", "Browser launch requires an http(s) URL on localhost")
        if args["args"]:
            raise DebugError("invalid_request", "Browser URLs do not accept program arguments")
    else:
        args["script"] = str((workspace / args["script"]).resolve())
        if not Path(args["script"]).is_file():
            raise DebugError("invalid_request", "Script or executable does not exist")
        if args["language"] == "rust" and Path(args["script"]).suffix == ".rs":
            raise DebugError("invalid_request", "Build Rust with debug symbols first; launch the executable, not the .rs source")
    if args["language"] == "python":
        args["python"] = executable(args["python"] or sys.executable, "Python interpreter")
    else:
        default = (workspace / ".tools" / "js-debug" / "src" / "dapDebugServer.js" if is_js else
                   workspace / ".tools" / "codelldb" / "extension" / "adapter" / ("codelldb.exe" if os.name == "nt" else "codelldb"))
        adapter = args["adapter"] or os.environ.get("AIDEBUG_JS_DEBUG" if is_js else "AIDEBUG_CODELLDB") or str(default)
        args["adapter"] = str((workspace / adapter).resolve())
        if not Path(args["adapter"]).is_file():
            raise DebugError("missing_dependency", "Adapter not found. Pass --adapter PATH or install it under .tools; see README.md")
    if is_js:
        args["node"] = executable(args["node"] or "node", "Node.js")
        args["out_files"] = [str(workspace / pattern).replace("\\", "/") for pattern in args["out_files"]] or [str(workspace).replace("\\", "/") + "/**/*.js"]
        args["web_root"] = str((workspace / (args["web_root"] or ".")).resolve())
        if not Path(args["web_root"]).is_dir():
            raise DebugError("invalid_request", "Web root does not exist")
        if args["target"] == "browser":
            candidates = [args["browser"]] if args["browser"] else ["google-chrome", "chromium", "msedge",
                "C:/Program Files/Google/Chrome/Application/chrome.exe", "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
            args["browser"] = next((shutil.which(p) for p in candidates if p and shutil.which(p)), None)
            if not args["browser"]:
                raise DebugError("missing_dependency", "Chrome/Chromium/Edge not found; pass --browser EXE")
    args["breakpoints"] = {}
    if len(args["breakpoint"]) > 100 or len(args["out_files"]) > 20:
        raise DebugError("invalid_request", "At most 100 launch breakpoints and 20 output globs")
    for value in args["breakpoint"]:
        try:
            file, line = value.rsplit(":", 1)
            file, line = str((workspace / file).resolve()), int(line)
            if line < 1 or not Path(file).is_file():
                raise ValueError()
        except ValueError:
            raise DebugError("invalid_request", "Breakpoints use existing FILE:positive-LINE")
        args["breakpoints"].setdefault(file, []).append(line)
    if args["args"][:1] == ["--"]:
        args["args"] = args["args"][1:]


def arguments(args):
    argv = ["--workspace", args["workspace"], "--timeout", str(args["timeout"]), "launch"]
    for key in ("language", "target", "cwd", "python", "adapter", "node", "browser", "web_root"):
        if args.get(key):
            argv += ["--" + key.replace("_", "-"), args[key]]
    for flag in ("run", "uncaught", "headed"):
        if args[flag]:
            argv.append("--" + flag)
    for pattern in args["out_files"]:
        argv += ["--out-files", pattern]
    for file, lines in args["breakpoints"].items():
        for line in lines:
            argv += ["--breakpoint", f"{file}:{line}"]
    return argv + [args["script"], *args["args"]]
