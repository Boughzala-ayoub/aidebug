"""JS/TS launch policy for the official standalone js-debug DAP server."""
from pathlib import Path
import re
from urllib.parse import urlsplit

ID = "pwa-node"
TRANSPORT = "ipc"


def command(args):
    return [args["node"], args["adapter"]]


def configuration(args):
    common = {"cwd": args["cwd"], "rootPath": args["workspace"], "__workspaceFolder": args["workspace"],
              "sourceMaps": True, "pauseForSourceMap": True, "outFiles": args["out_files"],
              "resolveSourceMapLocations": ["**", "!**/node_modules/**"]}
    if args["target"] == "browser":
        config = {**common, "type": "pwa-chrome", "url": args["script"], "webRoot": args["web_root"],
                "runtimeExecutable": args["browser"], "runtimeArgs": ["--no-first-run"] + ([] if args["headed"] else ["--headless=new"]) + (["--disable-gpu"] if args.get("disable_gpu") else []),
                "userDataDir": args["profile"], "cleanUp": "wholeBrowser"}
        if args.get("path_mapping"):
            config["pathMapping"] = {"/": args["web_root"]}
            # js-debug replaces this dictionary, so retain its documented defaults.
            overrides = {"webpack:///./~/*": "${webRoot}/node_modules/*", "webpack:////*": "/*",
                "webpack://@?:*/?:*/*": "${webRoot}/*", "webpack://?:*/*": "${webRoot}/*",
                "webpack:///([a-z]):/(.+)": "$1:/$2", "meteor://\U0001f4bbapp/*": "${webRoot}/*",
                "turbopack://[project]/*": "${workspaceFolder}/*", "turbopack:///[project]/*": "${workspaceFolder}/*"}
            parsed = urlsplit(args["script"])
            for path, file in args["path_mapping"].items():
                # Breakpoint prediction uses pathMapping in the reverse direction.
                config["pathMapping"][path.rsplit("/", 1)[0] or "/"] = str(Path(file).parent)
                url = f"{parsed.scheme}://{parsed.netloc}{path}"
                # A capture replacement selects js-debug's exact-regex mapping;
                # mapping a directory prefix to a file would break sibling scripts.
                overrides[re.escape(url) + "()"] = Path(file).as_uri() + "$1"
            config["sourceMapPathOverrides"] = overrides
        return config
    # Node can accept instrumentation breakpoints without firing them. Predict
    # mapped locations from the generated files before letting the process run.
    return {**common, "type": ID, "pauseForSourceMap": False, "program": args["script"], "args": args["args"],
            "runtimeExecutable": args["node"], "console": "internalConsole", "outputCapture": "std",
            "stopOnEntry": not args["run"], "autoAttachChildProcesses": False}


def exception_filters(enabled):
    return ["uncaught"] if enabled else []
