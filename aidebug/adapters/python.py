"""Python launch policy for debugpy."""

import sys

ID = "python"
TRANSPORT = "stdio"


def command(args):
    return [sys.executable, "-m", "debugpy.adapter"]


def configuration(args):
    return {"type": ID, "program": args["script"], "args": args["args"], "cwd": args["cwd"],
            "python": [args["python"]], "console": "internalConsole", "redirectOutput": True,
            "stopOnEntry": not args["run"], "justMyCode": False, "subProcess": False}


def exception_filters(enabled):
    return ["uncaught"] if enabled else []
