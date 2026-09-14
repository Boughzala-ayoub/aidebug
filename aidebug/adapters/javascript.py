"""JS/TS launch policy for the official standalone js-debug DAP server."""

ID = "pwa-node"
TRANSPORT = "ipc"


def command(args):
    return [args["node"], args["adapter"]]


def configuration(args):
    common = {"cwd": args["cwd"], "rootPath": args["workspace"], "__workspaceFolder": args["workspace"],
              "sourceMaps": True, "pauseForSourceMap": True, "outFiles": args["out_files"],
              "resolveSourceMapLocations": ["**", "!**/node_modules/**"]}
    if args["target"] == "browser":
        return {**common, "type": "pwa-chrome", "url": args["script"], "webRoot": args["web_root"],
                "runtimeExecutable": args["browser"], "runtimeArgs": ["--no-first-run"] + ([] if args["headed"] else ["--headless=new", "--disable-gpu"]),
                "userDataDir": args["profile"], "cleanUp": "wholeBrowser"}
    # Node can accept instrumentation breakpoints without firing them. Predict
    # mapped locations from the generated files before letting the process run.
    return {**common, "type": ID, "pauseForSourceMap": False, "program": args["script"], "args": args["args"],
            "runtimeExecutable": args["node"], "console": "internalConsole", "outputCapture": "std",
            "stopOnEntry": not args["run"], "autoAttachChildProcesses": False}


def exception_filters(enabled):
    return ["uncaught"] if enabled else []
