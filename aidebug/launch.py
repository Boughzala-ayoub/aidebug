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
    # Keep virtual-environment symlinks: resolving bin/python loses sys.prefix on POSIX.
    return os.path.abspath(found)


def resolve(args):
    workspace = Path(args["workspace"])
    suffix = Path(args["script"]).suffix.lower()
    inferred = {".py": "python", ".js": "javascript", ".cjs": "javascript", ".mjs": "javascript", ".ts": "typescript"}
    args["language"] = args["language"] or ("python" if args["module"] else
        "javascript" if args["target"] == "browser" else inferred.get(suffix))
    if not args["language"]:
        raise DebugError("invalid_request", "Specify --language python, javascript, typescript, or rust")
    args["language"] = {"js": "javascript", "ts": "typescript"}.get(args["language"], args["language"])
    if args["module"] and (args["language"] != "python" or
            len(args["script"]) > 256 or not all(part.isidentifier() for part in args["script"].split("."))):
        raise DebugError("invalid_request", "--module requires a dotted Python module name and Python language")
    is_js = args["language"] in ("javascript", "typescript")
    args["target"] = args["target"] or ("node" if is_js else "process")
    if (is_js and args["target"] not in ("node", "browser")) or (not is_js and args["target"] != "process"):
        raise DebugError("invalid_request", "Node/browser targets require JavaScript or TypeScript")
    if args["headed"] and args["target"] != "browser":
        raise DebugError("invalid_request", "--headed requires --target browser")
    if (args["disable_gpu"] or args["url_path"]) and args["target"] != "browser":
        raise DebugError("invalid_request", "--disable-gpu and --url-path require --target browser")
    args["cwd"] = str((workspace / (args["cwd"] or ".")).resolve())
    if not Path(args["cwd"]).is_dir():
        raise DebugError("invalid_request", "Working directory does not exist")
    if args["target"] == "browser":
        url = urlsplit(args["script"])
        if url.scheme not in ("http", "https") or url.hostname not in ("localhost", "127.0.0.1", "::1") or url.username:
            raise DebugError("invalid_request", "Browser launch requires an http(s) URL on localhost")
        if args["args"]:
            raise DebugError("invalid_request", "Browser URLs do not accept program arguments")
    elif not args["module"]:
        args["script"] = str((workspace / args["script"]).resolve())
        if not Path(args["script"]).is_file():
            raise DebugError("invalid_request", "Script or executable does not exist")
        if args["language"] == "rust" and Path(args["script"]).suffix == ".rs":
            raise DebugError("invalid_request", "Build Rust with debug symbols first; launch the executable, not the .rs source")
    if args["language"] == "python":
        project_python = workspace / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        active_env = os.environ.get("VIRTUAL_ENV")
        # Activating aidebug itself says nothing about the target's environment.
        if active_env and Path(active_env).resolve() == Path(sys.prefix).resolve():
            active_env = None
        active_python = Path(active_env) / ("Scripts/python.exe" if os.name == "nt" else "bin/python") if active_env else None
        selected = args["python"] or (str(active_python) if active_python else str(project_python) if project_python.is_file() else sys.executable)
        if not Path(selected).is_absolute() and ("/" in selected or "\\" in selected):
            selected = str(workspace / selected)
        args["python"] = executable(selected, "Python interpreter")
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
            args["path_mapping"] = {}
            url_path = urlsplit(args["script"]).path or "/"
            index = Path(args["web_root"]) / url_path.lstrip("/") / "index.html"
            if url_path.endswith("/") and index.is_file() and index.resolve().is_relative_to(Path(args["web_root"])):
                args["path_mapping"][url_path] = str(index.resolve())
            if len(args["url_path"]) > 20:
                raise DebugError("invalid_request", "At most 20 URL mappings")
            for mapping in args["url_path"]:
                path, separator, local = mapping.partition("=")
                file = (workspace / local).resolve()
                if not separator or not path.startswith("/") or path.startswith("//") or "?" in path or "#" in path or not file.is_file():
                    raise DebugError("invalid_request", "URL mappings require /URL_PATH=EXISTING_LOCAL_FILE")
                if file.name != (path.rsplit("/", 1)[-1] or "index.html"):
                    raise DebugError("invalid_request", "URL mappings require matching filenames, or a directory URL mapped to index.html")
                args["path_mapping"][path] = str(file)
    args["breakpoints"] = {}
    if args["condition"] is not None and not args["breakpoint"]:
        raise DebugError("invalid_request", "Launch --condition requires --breakpoint")
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
        args["breakpoints"].setdefault(file, {})[line] = args["condition"]
    if args["args"][:1] == ["--"]:
        args["args"] = args["args"][1:]


def arguments(args):
    argv = ["--workspace", args["workspace"], "--timeout", str(args["timeout"]), "launch"]
    for key in ("language", "target", "cwd", "python", "adapter", "node", "browser", "web_root", "condition"):
        if args.get(key):
            argv += ["--" + key.replace("_", "-"), args[key]]
    for flag in ("run", "uncaught", "headed", "module", "no_record", "no_wait", "disable_gpu"):
        if args[flag]:
            argv.append("--" + flag.replace("_", "-"))
    for pattern in args["out_files"]:
        argv += ["--out-files", pattern]
    for mapping in args["url_path"]:
        argv += ["--url-path", mapping]
    for file, lines in args["breakpoints"].items():
        for line in lines:
            argv += ["--breakpoint", f"{file}:{line}"]
    return argv + [args["script"], *args["args"]]


def doctor(args):
    """Use the launch validator without launching a program or creating state."""
    from importlib.metadata import version
    import sys
    from .cli import parse
    argv = ["--workspace", args["workspace"], "launch", "--language", args["language"]]
    for key in ("target", "python", "adapter", "browser", "node"):
        if args.get(key):
            argv += ["--" + key, args[key]]
    argv.append("http://127.0.0.1/" if args["target"] == "browser" else str(Path(__file__).resolve()))
    result = {"ok": True, "version": version("aidebug-cli"), "workspace": args["workspace"],
              "cli_python": sys.executable, "cli_on_path": shutil.which("aidebug"),
              "language": args["language"], "runs_program": False}
    try:
        resolved = parse(argv)
        result["tools"] = {key: resolved.get(key) for key in ("python", "adapter", "node", "browser")}
        result["target"] = resolved["target"]
    except DebugError as exc:
        result.update(ok=False, error=exc.as_dict())
    result["next_action"] = "Ready to launch an explicitly selected program" if result["ok"] else "Correct the reported dependency or pass its explicit path, then rerun doctor"
    return result
