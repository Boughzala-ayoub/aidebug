# aidebug

`aidebug` lets a coding agent debug local Python, JavaScript, TypeScript, and Rust programs through one JSON CLI. Run a program, stop at a breakpoint or failure, inspect runtime values, and step through the code before changing it. JavaScript and TypeScript support both Node.js and browser UI code.

Each invocation connects to the same background helper in the selected workspace. The helper owns the debug adapter, matches DAP responses by request sequence, and processes asynchronous events on reader threads. Python uses debugpy, JavaScript and TypeScript use Microsoft's js-debug, and Rust uses CodeLLDB. There is no general expression evaluation command or generic DAP passthrough.

The local session viewer shows the agent's debugging activity live and replays saved sessions after they end. Recording is enabled by default; use `launch --no-record` to disable it for a session.

Run `aidebug --help` for the agent usage loop, including inspection, handle expiry, and timeouts. [AGENTS.md](AGENTS.md) contains contributor rules.

Install with Python 3.10 or newer:

```sh
python -m venv .venv
# Windows
.venv\Scripts\python -m pip install .
.venv\Scripts\aidebug --help
# Linux/macOS
.venv/bin/python -m pip install .
.venv/bin/aidebug --help
```

Activate the environment or use the full executable path for the commands below. debugpy is the only Python runtime dependency. Other languages require the external tools below. A regular install copies the package, so reinstall after editing the debugger source. Stop an existing session before reinstalling.

| Language and target | Required tools | Adapter selection |
| --- | --- | --- |
| Python script or module | Python and installed debugpy | Included with `pip install .` |
| JavaScript or TypeScript on Node | Node.js and the standalone js-debug DAP server | `--adapter PATH/TO/dapDebugServer.js` or `AIDEBUG_JS_DEBUG` |
| JavaScript or TypeScript in a browser | The same Node/js-debug tools, Chrome, Chromium, or Edge, and your running local web server | Same JS adapter; optional `--browser EXE` |
| Rust executable | CodeLLDB with its bundled LLDB libraries; Rust to build your program with debug symbols | `--adapter PATH/TO/codelldb.exe` or `AIDEBUG_CODELLDB` |

Download the standalone DAP archive from [js-debug releases](https://github.com/microsoft/vscode-js-debug/releases). The tested archive is `js-debug-dap-v1.117.0.tar.gz`. Extract it under `.tools` so `.tools/js-debug/src/dapDebugServer.js` exists.

Download the matching platform archive from [CodeLLDB releases](https://github.com/vadimcn/codelldb/releases). The tested Windows archive is `codelldb-win32-x64.vsix` version 1.12.3. It is a ZIP file. Extract its entire contents under `.tools/codelldb`, retaining the `extension/adapter` and `extension/lldb` directories. The default Windows executable path is `.tools/codelldb/extension/adapter/codelldb.exe`. On other platforms, the executable is `codelldb` without the suffix.

These `.tools` paths are relative to the selected workspace and are ignored by Git. Use an environment variable or `--adapter` for a shared installation. aidebug never downloads adapters, compiles code, starts a web server, or runs package scripts automatically. Missing tools return a JSON `missing_dependency` error before a session starts.

## Install the agent skill

After installing the CLI, register its bundled skill for Codex and Claude Code:

```sh
aidebug skills install --agent codex --agent claude-code
aidebug skills status
```

The default user scope copies the same [skill](aidebug/skill/SKILL.md) and language references into `~/.agents/skills/aidebug` for Codex and `~/.claude/skills/aidebug` for Claude Code. Agents can select it for runtime debugging tasks. Invoke it explicitly with `$aidebug` in Codex or `/aidebug` in Claude Code. Start a new agent session if it is not listed. Skill discovery does not guarantee the model will choose it for every bug.

For one project, add `--scope project`; directories are relative to the selected workspace:

```sh
aidebug --workspace PATH/TO/PROJECT skills install --agent codex --agent claude-code --scope project
aidebug --workspace PATH/TO/PROJECT skills status --scope project
```

Installation is explicit; `pip install` does not edit agent configuration. Setup never starts a debug session or downloads adapters. It leaves `AGENTS.md` and `CLAUDE.md` alone. The generated `installation.json` records file hashes, the package version, and an interpreter command the skill can use if `aidebug` is absent from PATH. That interpreter path is machine-specific. For shared repositories, prefer user scope or ignore the generated project skill directories.

Run `skills install` again after upgrading or moving the CLI. Repeated installation is a no-op when current. Status reports `not_installed`, `current`, `outdated`, or `conflict`. Modified, missing, or unmanaged skill files cause a JSON error; back up or restore those files before retrying. Installation refuses directory links and junctions. Uninstall removes only unchanged managed files and preserves unrelated files. A remaining directory becomes unmanaged. A small installer lock file stays in the parent `skills` directory.

Remove skills before removing the CLI environment, using the same scope used to install them:

```sh
aidebug skills uninstall --agent codex --agent claude-code
aidebug skills status
```

The skill format and locations follow the official [Codex skills](https://learn.chatgpt.com/docs/build-skills), [Claude Code skills](https://code.claude.com/docs/en/skills), and [Agent Skills specification](https://agentskills.io/specification). Other agents are not registered by this version.

## Debug a Python project or test

`--module` treats the positional target as a Python module name, like `python -m`. Target arguments follow the name:

```sh
aidebug --workspace PATH/TO/PROJECT launch --module --run --breakpoint pricing.py:6 pytest -- -q test_checkout.py
aidebug --workspace PATH/TO/PROJECT inspect
aidebug --workspace PATH/TO/PROJECT continue
aidebug --workspace PATH/TO/PROJECT output
aidebug --workspace PATH/TO/PROJECT stop
```

Install pytest in the target environment first. For unittest, use `--module unittest -- -q test_checkout`. Script launches still work as before. A module name is resolved by the target interpreter from `--cwd`, which defaults to the workspace. Module launch does not import project code into the CLI or helper.

Python selection uses explicit `--python`, then the environment named by `VIRTUAL_ENV`, then the workspace's `.venv`, then aidebug's own interpreter. `VIRTUAL_ENV` is ignored when its directory resolves to aidebug's `sys.prefix`: activating the tool's environment must not override the project's environment. Environments use `Scripts/python.exe` on Windows or `bin/python` on POSIX. An invalid activated environment returns an error instead of silently selecting another interpreter. Status and execution responses include the selected `python` path. Interpreter paths containing directories are relative to the workspace; bare executable names use PATH. Virtual-environment symlinks are preserved.

Test runners catch assertion failures, so `--uncaught` alone does not stop at every failing test. Set a breakpoint on the assertion or the function it calls. The [three-file checkout fixture](examples/project/test_checkout.py) deliberately computes 10 instead of 15 because `pricing.py` counts all orders after `orders.py` filters unpaid ones. The project check copies it, inspects both frames, fixes the divisor, and reruns successfully. The original fixture remains buggy.

With `--uncaught`, a failed test run can stop later at the runner's `SystemExit`, after the assertion has been caught. Use `exceptions off` and `continue` to collect its final exit code. That stop does not preserve the failing test's locals.

To inspect a running program after a timeout:

```sh
aidebug --timeout 2 pause
aidebug inspect
aidebug --timeout 2 continue
aidebug stop
```

Pause selects the previously stopped thread if it still exists, otherwise the first thread offered by the adapter. It waits for a stopped event and returns bounded stack and local values. A blocked native call may delay that event. An idle browser can defer suspension until its next JavaScript task. If the deadline expires, the response reports the current state with `timed_out: true`; use `status` or `stop`. Pause requires a configured running session and does not interrupt another CLI command already in progress.

## Conditional breakpoints

Use an adapter-native condition to stop at a particular loop iteration:

```sh
aidebug launch --run --breakpoint worker.py:12 --condition "i == 437" worker.py
aidebug break set worker.py 12 --condition "i == 439"
aidebug continue
# Setting the line without a condition makes it unconditional.
aidebug break set worker.py 12
aidebug break remove worker.py 12
aidebug stop
```

At launch, one `--condition` applies to every supplied `--breakpoint`. Conditions must be nonempty and at most 512 characters. The adapter must advertise conditional breakpoint support. Breakpoint responses include the condition, requested and actual lines, and verification result. JavaScript/TypeScript uses JavaScript expressions; Rust follows [CodeLLDB's expression rules](https://github.com/vadimcn/codelldb/blob/master/MANUAL.md#conditional-breakpoints). An expression error can cause an adapter to stop rather than skip the breakpoint.

Conditions are an explicit opt-in to executing code in the target and can have side effects. General expression evaluation remains outside this version.

Native source lines can map to several breakpoint locations. For example, Rust's `println!` can stop more than once within the same iteration. Inspect the values after each stop.

## Watch live or replay a session

Start the viewer in the same workspace the agent uses:

```sh
aidebug view --open
```

This starts a hidden local server and opens its browser page. Without `--open`, the command returns its URL as JSON. Run the usual debugger commands from another terminal or let your coding agent run them. The viewer updates automatically, even when it was opened before the debug session started.

```sh
aidebug launch --uncaught examples/walk.py
aidebug break set examples/walk.py 12
aidebug continue
aidebug note "I am checking the value passed to add_one before changing the code."
aidebug step into
aidebug step over
aidebug continue
aidebug output
aidebug stop
```

The viewer includes an activity timeline, source with the current line highlighted, inspected locals, recorded variable expansions, the call stack, separate output channels, and agent notes. Changed values are highlighted between consecutive inspections of the same frame. Clicking a stack frame or expanding a value only shows evidence already recorded by the agent. It does not query the debugger.

Choose **Follow live** to follow incoming activity. Drag the replay slider or select a timeline entry to examine an earlier observation while the agent continues. **Play replay**, pause, previous/next, and the speed selector work on completed recordings too. Long idle gaps are shortened to keep replay watchable. On phones, **Activity** opens the recordings and timeline navigation.

After `stop`, select a recording in the sidebar to replay it. The viewer can be stopped and restarted without losing recordings:

```sh
aidebug view --stop
aidebug view --open
```

`view --stop` stops only the viewer. `stop` stops only the debug session. A viewer can run alongside one live debugging session and any saved recordings in that workspace. Use the same `--workspace DIR` for both commands.

Agent explanations are explicit. `aidebug note "TEXT"` records up to 4,000 characters during an existing session, including after the target exits but before `stop`. The viewer does not infer the agent's private reasoning or capture unrelated shell commands. Source snapshots are saved when a location is inspected. A note or `stop` also checks previously inspected files for edits, so **Changes** can show the patch. Source shown beside old runtime values stays paired with its original inspection. A rerun creates a separate recording.

## Run the visual demo

This command creates a fresh demo source file, runs a real Python debugging session, records the diagnosis and fix, and verifies the corrected program:

```sh
# Windows; use .venv/bin paths on Linux/macOS.
.venv/Scripts/python checks/demo.py --cli .venv/Scripts/aidebug.exe --open
```

The demo stops before a denominator changes from `2` to `0`, expands the prices, steps through the faulty subtraction, and reproduces division by zero. It then records a one-line fix and reruns with `15.0` and exit code `0`. Two recordings remain available in the viewer. This is a scripted demonstration of the agent-facing commands; it does not call an AI model.

The demo refuses to replace an existing session. Each run creates its own source directory under `.aidebug/demos`, preserving the buggy fixture in `checks/demo.py`. It leaves the viewer running for replay. The existing user-edited examples are untouched.

## Recording and viewer boundaries

To debug without saving observations, put `--no-record` before the target:

```sh
aidebug launch --no-record examples/walk.py
```

Inspection, stepping, and output still work. Responses report `recording.enabled: false`. This session creates no recording file, has no live or replay activity in the viewer, and rejects `note`. Existing recordings remain untouched. The flag applies to every supported language and target.

After stopping the debug session, preview and remove recordings whose last write was more than 30 days ago:

```sh
aidebug recordings prune --older-than 30 --dry-run
aidebug recordings prune --older-than 30
```

`--older-than` is required and accepts whole days from 0 to 365000. Use `0` to match all recordings. Cleanup applies only to the selected `--workspace`, includes interrupted recordings with valid session headers, and preserves unrelated files, links, and malformed recordings. It holds the workspace lock and returns `busy` until the debug session is stopped, even if its target has exited. A linked recordings directory is rejected.

JSON results include `matched`, `matched_bytes`, `deleted`, `deleted_bytes`, and `skipped`. Dry runs delete no recordings. Cleanup stops at the first filesystem error and reports `ok: false` with any deletions already completed. There is no automatic retention policy.

Recordings live in `.aidebug/recordings/SESSION_ID.jsonl`, inside the protected workspace session directory. They contain command arguments, source, output, inspected values, and explicit notes. Treat these files as local debugging data; they may include secrets from your program. They are ignored by Git and never uploaded.

Each recording is capped at 32 MiB. An observation is capped at 128 KiB, source snapshots at 24,000 characters, and output chunks at 8,192 characters. The recorder marks limits explicitly. A full recording stops collecting new activity without stopping the debug session. Recording write errors appear in CLI responses under `recording.error`. The viewer lists the 100 most recent recordings and reads them in bounded pages. It displays recorded values only, not every variable or every executed instruction.

The viewer binds to `127.0.0.1`, checks the request host and origin, and requires its own random access token for recording APIs. The token is passed in the URL fragment, removed from the address bar, and retained in that tab's session storage. Fonts and assets are bundled locally; the page makes no external requests. Keep the returned URL private. This protects against unrelated clients, not processes running as your own account.

The viewer reads recordings without a second DAP connection. Opening it, replaying, or selecting a frame cannot resume or modify the target. A helper crash can leave a usable partial recording, shown as interrupted. This is replay of saved observations, not time-travel execution. Recordings remain until you prune them or remove their files.

## Debugger commands

An agent can follow this sequence from the repository root:

```sh
aidebug launch examples/walk.py "hello world"
aidebug break set examples/walk.py 12
aidebug continue
aidebug inspect
# Copy the handle for nested from locals.items into the next command.
aidebug variables HANDLE
aidebug step into
aidebug step over
aidebug step out
aidebug break remove examples/walk.py
aidebug continue
aidebug output
aidebug stop
```

At line 12, `known` is `41`. Stepping into `add_one` exposes its `value` argument. Stepping over the addition produces `answer = 42`.

| Command | Behavior |
| --- | --- |
| `launch [OPTIONS] TARGET [ARGS...]` | Launch a script, executable, local browser URL, or Python module with `--module`. Process targets stop at entry unless `--run` is present. Use a launch breakpoint for a browser. |
| `break set FILE LINE [LINE...] [--condition EXPR]` | Add or update breakpoints. Omitting the condition clears it on the supplied lines. Report requested and actual lines, condition, and verification result. |
| `break remove FILE [LINE...]` | Remove the given lines, or all breakpoints for the file when no lines are supplied. |
| `exceptions on` / `exceptions off` | Toggle uncaught Python/JS exceptions or Rust panics. Default is off unless launch includes `--uncaught`. Rust also stops on panics that the application later catches. |
| `continue` | Resume until a stop, exit, failure, or deadline. |
| `pause` | Request a stop in a configured running target and wait until stopped, exited, failed, or timed out. |
| `step into` / `step over` / `step out` | Step in the thread that triggered the current stop. |
| `inspect [--frame HANDLE] [--limit N]` | Return the stack, selected source location, and locals. Default frame is the top frame. |
| `variables HANDLE [--start N] [--limit N]` | Expand children of a value or locals scope. |
| `status` | Report state. Include inspection when stopped. |
| `output` | Read separate bounded stdout, stderr, and adapter diagnostic buffers without clearing them. |
| `stop` | Disconnect, terminate owned processes, remove session metadata, and exit the helper. Safe to repeat. |
| `note TEXT` | Add an explicit agent explanation and capture edits to previously inspected source files. |
| `view [--open]` | Start or locate the local viewer for live activity and saved replay. |
| `view --stop` | Stop the viewer server, preserving debug sessions and recordings. |
| `recordings prune --older-than DAYS [--dry-run]` | Preview or remove old recordings after stopping the workspace's debug session. Use `0` for all recordings. |

Global options go before the command:

```sh
aidebug --workspace /path/to/project --timeout 2 continue
aidebug --workspace /path/to/project launch --cwd tests --python /path/to/python script.py --flag value
```

The workspace defaults to the current directory. Script, breakpoint, adapter, working-directory, and explicit relative interpreter paths resolve relative to the workspace. Use the same `--workspace` on later invocations. Bare interpreter names resolve through `PATH`. Launch options must precede the target name.

Launch options common to the adapters are `--language`, `--cwd`, `--run`, `--uncaught`, `--no-record`, repeatable `--breakpoint FILE:LINE`, and `--condition EXPR`. Python also accepts `--python EXE` and `--module`. JS/TS accepts `--target node|browser`, `--node EXE`, and repeatable `--out-files GLOB`. Browser launches also accept `--browser EXE`, `--web-root DIR`, and `--headed`. Rust requires `--language rust`; Python, JavaScript, and TypeScript can be inferred from their source extensions. `--module` infers Python. `js` and `ts` are accepted aliases.

Set initial breakpoints in the launch command to avoid racing a fast program:

```sh
aidebug launch --run --breakpoint examples/javascript/walk.js:8 examples/javascript/walk.js "hello world"
aidebug inspect
aidebug step into
aidebug step over
aidebug step out
aidebug continue
aidebug output
aidebug stop
```

For TypeScript, build JavaScript and source maps first using your project's compiler. With `tsc` installed, this repository's fixture can be run as follows:

```sh
tsc examples/typescript/walk.ts --sourceMap --inlineSources --target ES2020 --outDir .tools/ts-demo
aidebug launch --language typescript --out-files ".tools/ts-demo/**/*.js" --run --breakpoint examples/typescript/walk.ts:8 .tools/ts-demo/walk.js
aidebug inspect
aidebug continue
aidebug stop
```

The stop is reported in the original `.ts` file. `--out-files` describes generated JavaScript, not TypeScript sources. Its default is `WORKSPACE/**/*.js`. Source maps must match the current build. Node launches predict mapped breakpoints from these files before execution.

For a browser UI, start your development server separately and use its localhost URL. This example assumes your app has a save handler at `src/save.ts:12`:

```sh
aidebug --timeout 2 launch --language typescript --target browser --headed --web-root . --breakpoint src/save.ts:12 http://localhost:5173
# Click Save in the launched browser, then inspect from a separate CLI invocation.
aidebug status
aidebug inspect
aidebug step over
aidebug continue
aidebug output
aidebug stop
```

Browser launch is headless by default. `--headed` opens the target browser for interaction; the helper stays hidden. Each browser session uses its own temporary profile, which `stop` removes. A browser that is waiting for an interaction stays `running` after the deadline. The CLI controls debugging, not clicks, screenshots, network requests, or DOM queries. The integration check triggers a real button handler from its test page. Bundlers must serve usable source maps, and `--web-root` must match the local source tree. Only localhost HTTP and HTTPS launch URLs are accepted.

For Rust, build an executable with debug symbols and launch that executable:

```sh
# Windows example; use a binary without .exe on Linux/macOS.
rustc -g -C opt-level=0 examples/rust/walk.rs -o .tools/rust-walk.exe
aidebug launch --language rust --run --breakpoint examples/rust/walk.rs:10 .tools/rust-walk.exe
aidebug inspect
aidebug step into
aidebug step over
aidebug step out
aidebug continue
aidebug output
aidebug stop
```

For Cargo projects, run `cargo build` first and launch `target/debug/YOUR_BINARY`. Optimized or stripped binaries can omit locals and move source locations. Rust entry stops can be in native startup code; pre-launch source breakpoints give a more useful first stop. Panic stops can be inside the Rust runtime and CodeLLDB reports them as `reason: breakpoint`. Select the caller frame from `stack.frames` with `inspect --frame HANDLE` to inspect your own values.

All public commands, including help and argument errors, print one JSON object to stdout. Target output never shares that stream. Command errors exit with code 1. Successful debugger operations exit with code 0, including observing a target that exited unsuccessfully. Check `exit_code` for the target's result. It is `null` when the adapter does not report an exit code, as observed with js-debug. Likewise, `processes.target` can be `null`; process ownership and cleanup do not depend on that reported PID. Live responses include `language` and `target`.

A stopped response has this shape. Handles below are illustrative; copy the actual returned strings.

```json
{
  "ok": true,
  "state": "stopped",
  "reason": "breakpoint",
  "session": "a719...",
  "generation": 3,
  "timed_out": false,
  "location": {
    "file": "/project/examples/walk.py",
    "line": 12,
    "frame": "a719...:3:f:8",
    "source": [{"line": 12, "text": "result = add_one(known)", "truncated": false}]
  },
  "stack": {"frames": [], "truncated": false},
  "locals": {
    "handle": "a719...:3:v:9",
    "items": [
      {"name": "known", "type": "int", "value": "41", "handle": null, "truncated": false},
      {"name": "nested", "type": "dict", "value": "{'items': [3, 5, 8], 'label': 'test'}", "handle": "a719...:3:v:12", "truncated": false}
    ],
    "start": 0,
    "next_start": null,
    "truncated": false
  }
}
```

Values are debugger display strings, not serialized objects. A non-null handle allows expansion through `variables`. Frame and variable handles belong to one session and one stopped generation. All expire as soon as execution resumes. Never cache them across steps or launches.

States are `stopped`, `running`, `exited`, and `failed`. `running` with `phase: initializing` means launch setup is in progress. `failed` identifies an adapter or launch failure, while an uncaught target exception is a `stopped` state with `reason: exception`. A nonzero target exit remains `exited`. `unknown` is used for client-side errors when the session state could not be read.

Errors include an `error` object with a stable `code` and a readable `message`. Codes include `invalid_request`, `invalid_state`, `invalid_handle`, `no_session`, `session_exists`, `busy`, `timeout`, `adapter_failure`, and `control_failure`. Error responses from the live helper include its actual state.

The command deadline defaults to 10 seconds and accepts 0.01 to 60 seconds. Launch, continue, step, and pause wait until the next stop or exit. On a wait deadline they return `timed_out: true` with the current state and leave the session alive. Use `status`, `output`, `pause`, or `stop` next. If there is insufficient time to inspect a new stop, `inspection_truncated: true` requests a fresh `inspect` call. Helper startup can add up to five seconds before the command begins. Process cleanup and local transport have separate bounded grace periods. Adapter initialization and resume-request acknowledgments have a 30-second ceiling; a stuck adapter becomes `failed` and remains stoppable. Pause requests use the command's deadline and can be inspected afterward even if their response arrives late.

Responses are capped at 64 KiB. Inspection returns at most 20 frames, five source lines, and 20 variables by default. `--limit` accepts 1 to 30. Variable previews have a 512-character ceiling. Source lines have a 240-character ceiling. `truncated` marks shortened previews, omitted items, or a response section dropped to meet the total byte cap. Preview ellipses from debugpy also mark possible truncation. Use `next_start` for the next page. Each output channel retains its last 8,192 characters and reports `dropped_chars`. Output buffers last until `stop`.

The control socket binds only to `127.0.0.1` and requires a random 256-bit token. `.aidebug/session.json` contains the endpoint and token. The session directory has a user-only Windows DACL or POSIX mode `0700`; metadata uses mode `0600` on POSIX. An OS file lock enforces one helper per workspace. `stop` removes the live metadata. An empty diagnostic file and the small lock file may remain, so competing invocations always lock the same file. Stale metadata is removed only after obtaining that lock. Do not share the session token. Processes running as your own user can read it; this is not isolation from your own account or from the target program.

Windows helper and adapter launches use `CREATE_NO_WINDOW`. An owned Job Object terminates adapter descendants when the helper closes or dies. A browser window appears only with `--headed`. POSIX cleanup kills the adapter's process group on `stop`. Sessions remain available after target exit so output can be read; finish with `stop` to release all resources.

The adapter channel is also separate from CLI output. debugpy uses stdio. js-debug uses a random named pipe on Windows or a socket inside the protected session directory on POSIX. Its `startDebugging` reverse request creates a child DAP connection for the launched Node process or browser page. CodeLLDB connects back to a temporary loopback listener and authenticates with a random 256-bit token; the listener then closes. This keeps native target output from corrupting DAP on Windows. Console output from adapters may include startup and exit notices alongside target output.

The source is split by responsibility:

```text
aidebug/
  cli.py                 JSON commands, protected workspace helper and control channel
  launch.py              Validate and normalize explicit launch choices
  dap.py                 DAP framing, response matching, transports and owned processes
  session.py             State, deadlines, breakpoints, handles and bounded inspection
  recording.py           Bounded, persistent command/event/source observations
  viewer.py              Protected local HTTP server for saved observations
  web/                   Packaged viewer HTML, CSS, JavaScript, fonts and licenses
  adapters/
    __init__.py          Static language-to-module mapping
    python.py            debugpy process and launch settings
    javascript.py        js-debug settings for Node and browser, including TypeScript maps
    rust.py              CodeLLDB launch settings and panic policy
checks/
  integration.py         Installed Python CLI regression checks
  multilanguage.py       Installed Node, TS, browser and Rust checks
  transport.py           Authentication and DAP framing checks
  recording.py           Persistence, bounds, partial writes and source-version checks
  viewer.mjs             Real CLI plus headless live/replay and responsive UI checks
  demo.py                Record a diagnosis, fix and successful rerun for a demo
examples/
  javascript/walk.js
  typescript/walk.ts
  rust/walk.rs
```

A new DAP language starts with an adapter module and explicit launch validation. Shared state, inspection, bounds, and cleanup stay in the core. Add a transport only when a real adapter requires it. The registry is static; there is no plugin loader or dependency-injection framework.

Run the integration checks against the installed executable:

```sh
# Windows
.venv\Scripts\python checks/integration.py --cli .venv\Scripts\aidebug.exe
# Linux/macOS
.venv/bin/python checks/integration.py --cli .venv/bin/aidebug
```

The checks launch separate CLI processes in a temporary workspace and use real debugpy. They cover breakpoints, all three steps, nested values, frame selection, arguments, working directory, interpreter, exceptions, deadlines, pause/resume, authentication, adapter failure, truncation, and process cleanup. [Python results](checks/results.json) contain the latest summary.

Tracked `checks/*-results.json` and `checks/results.json` contain counts, check groups, and available version information. Full reports, CLI transcripts, timings, and screenshots go under the Git-ignored `checks/artifacts/` directory. Repeating a run with the same outcome and tool versions does not change its summary.

Run `python checks/reports.py` to check summary stability and preservation of full failure reports.

Verify skill setup against the installed CLI with `.venv/Scripts/python checks/skills.py --cli .venv/Scripts/aidebug.exe`, or `.venv/bin` paths on POSIX. The check uses isolated homes and workspaces to exercise both agents, both scopes, repeat installation, upgrades, a missing PATH entry, conflict protection, and uninstall. See the [Windows skill summary](checks/skills-results.json) and [Linux skill summary](checks/skills-linux-results.json).

Verify module launch and a real multi-file test investigation:

```sh
# unittest needs no additional test dependency.
.venv/Scripts/python checks/project.py --cli .venv/Scripts/aidebug.exe
# Also verify pytest; this is a development dependency, not an aidebug runtime dependency.
.venv/Scripts/python -m pip install pytest
.venv/Scripts/python checks/project.py --cli .venv/Scripts/aidebug.exe --pytest-python .venv/Scripts/python.exe
```

Use `.venv/bin` paths on Linux/macOS. The check creates a separate workspace `.venv` without debugpy, adds a workspace-only dependency, verifies selection of a custom activated environment and explicit interpreter override, then diagnoses and repairs a temporary copy of the checkout fixture. [Project results](checks/project-results.json) summarize the checks. The check installs nothing itself.

[GitHub Actions](.github/workflows/checks.yml) installs the package and runs transport, recording, the average calculation check, skill installation and removal, Python integration, and both test-runner investigations on Ubuntu and Windows with Python 3.10 and 3.13. It needs no JS or Rust adapter downloads. The workflow runs on pushes and pull requests; no GitHub run is claimed until this repository is published and the job succeeds.

For all other languages, install the adapters above, Node, Rust, a Chromium browser, and TypeScript. If your project already has TypeScript, pass its `lib/typescript.js` path. Otherwise install a local test compiler:

```sh
npm install --prefix .tools/ts-compiler --no-save --package-lock=false typescript
# Windows; use .venv/bin paths on Linux/macOS.
.venv/Scripts/python checks/multilanguage.py --cli .venv/Scripts/aidebug.exe --typescript-compiler .tools/ts-compiler/node_modules/typescript/lib/typescript.js
```

Optional check flags `--js-adapter` and `--rust-adapter` select shared installations. The check compiles its own TypeScript and Rust fixtures, serves a local test page, and launches the installed CLI from temporary workspaces. It checks original TypeScript breakpoints in Node and a browser button handler, nested values, all step operations, expired handles, JS exceptions, Rust panic caller values, output, deadlines, invalid launches, and cleanup. [Multi-language results](checks/multilanguage-results.json) summarize the checks. It uses no browser automation package or Python test framework.

Run `.venv/Scripts/python -I checks/transport.py` to check adapter authentication, fragmented UTF-8 DAP messages, out-of-order responses, asynchronous events, output isolation, and cleanup against a small test adapter. `-I` ensures this check imports the installed package.

Verify recording behavior with `.venv/Scripts/python -I checks/recording.py`. This includes real installed-CLI checks for recording opt-out, cleanup previews, age filtering, active-session protection, deletion errors, and links. To run the viewer integration checks, install the browser test dependency separately from the Python package:

```sh
npm install --prefix .tools/viewer-check --no-save --package-lock=false puppeteer-core
node checks/viewer.mjs --cli .venv/Scripts/aidebug.exe --puppeteer .tools/viewer-check/node_modules/puppeteer-core
```

Pass `--browser EXE` if Chrome, Chromium, or Edge is not found automatically. The check verifies the installed package's served assets in fresh headless browser contexts. It drives real CLI commands, live updates, nested values, source changes, exceptions, successful reruns, replay, viewer restart, access controls, and untrusted text rendering. It checks every view and open navigation at 320, 375, 600, 768, 850, 1024, 1141, and 1280 pixels. Results are saved to `checks/viewer-results.json`; screenshots are generated under `checks/artifacts/viewer`.

See the [viewer summary](checks/viewer-results.json) and [Linux summary](checks/linux-results.json) for platform results. Release verification notes and development history are in [CHANGELOG.md](CHANGELOG.md).

Run `python checks/test_buggy.py` to verify paid, mixed, zero-priced, empty, and unpaid-only inputs. `examples/buggy.py` preserves the repaired divisor and puts its unpaid-only demonstration under a main guard, so importing it does not run the demonstration. Running that example directly still demonstrates the intended `ValueError`. Inspect the successful corrected example with:

```sh
aidebug launch --uncaught examples/fixed.py
aidebug break set examples/fixed.py 6
aidebug continue
aidebug inspect
aidebug continue
aidebug output
aidebug stop
```

aidebug supports one local target and the thread that caused the stop. It has no stdin/interactive terminal support, arbitrary expression evaluation command, variable mutation, remote attachment, worker/subprocess debugging, concurrent sessions per workspace, or thread-selection command. Additional js-debug child targets are rejected. Commands are serialized; overlapping commands receive `busy`. Set breakpoints with `launch --breakpoint` or after a process entry stop.

General expression evaluation remains outside this version because it can invoke arbitrary application code and mutate the state the agent is investigating. Conditional breakpoints are the explicit opt-in exception. Inspection stays on DAP variable requests. Formatter side effects remain possible, as described below.

Inspection uses only DAP stack, scopes, and variable requests. Debugger object formatting can still invoke representation, property, or formatter code. Adapters materialize variable children before the CLI pages them, so response limits are not debuggee memory or CPU limits. The DAP transport rejects messages larger than 16 MiB. This debugger does not provide performance profiling, browser layout inspection, or network tracing. Killing a POSIX helper with `SIGKILL`, or a target deliberately escaping its process group, can leave descendants behind. Windows covers all supported adapters; Ubuntu WSL covers the Python CLI, transport, and recording checks. Other platform/adapter combinations remain unverified. Browser profile files can remain after a helper crash. If an OS file lock outlasts the cleanup grace period, `stop` returns `cleanup_failure` and preserves session metadata for retry.

Protocol implementation references checked during development: [debugpy](https://github.com/microsoft/debugpy), [debugpy launch settings](https://github.com/microsoft/debugpy/wiki/Debug-configuration-settings), [DAP overview](https://microsoft.github.io/debug-adapter-protocol/overview), [DAP specification](https://github.com/microsoft/debug-adapter-protocol/blob/main/specification.md), [js-debug](https://github.com/microsoft/vscode-js-debug), [js-debug launch settings](https://github.com/microsoft/vscode-js-debug/blob/main/OPTIONS.md), [js-debug standalone server](https://github.com/microsoft/vscode-js-debug/blob/main/src/dapDebugServer.ts), and [CodeLLDB manual](https://github.com/vadimcn/codelldb/blob/master/MANUAL.md).
