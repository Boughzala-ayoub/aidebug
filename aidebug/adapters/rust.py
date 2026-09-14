"""Rust launch policy for CodeLLDB. Build the executable before launching."""

ID = "lldb"
# On Windows the debuggee can inherit adapter stdout; keep DAP on its own stream.
TRANSPORT = "connect"


def command(args):
    return [args["adapter"]]


def configuration(args):
    return {"type": ID, "program": args["script"], "args": args["args"], "cwd": args["cwd"],
            # Optimized Rust runtime symbols can put the inferred prologue end on
            # an unexecuted abort path, so the built-in panic filter must stop at entry.
            "initCommands": ["settings set target.skip-prologue false"],
            "terminal": "console", "stopOnEntry": not args["run"], "sourceLanguages": ["rust"]}


def exception_filters(enabled):
    return ["rust_panic"] if enabled else []
