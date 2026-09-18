# Adapter exits and the state-restoration trial

## Node exit codes

On Windows with Node 24.18.0 and js-debug 1.117.0, both `process.exitCode = N`
and `process.exit(N)` produced `terminated` with no target exit code for `N = 0`
and `N = 7`. Running each fixture directly returned the requested code.
Waiting another half-second after termination did not produce a confirmed exit.

A separate probe wrapped the existing DAP event callback on both connections.
The child sent `terminated {}` and the root sent `terminated {restart: false}`.
Neither sent `exited`. The installed adapter bundle matches the upstream path:
[NodeLauncher](https://github.com/microsoft/vscode-js-debug/blob/v1.117.0/src/targets/node/nodeLauncher.ts)
passes the process result to
[NodeLauncherBase.onProgramTerminated](https://github.com/microsoft/vscode-js-debug/blob/v1.117.0/src/targets/node/nodeLauncherBase.ts),
but [Binder](https://github.com/microsoft/vscode-js-debug/blob/v1.117.0/src/binder.ts)
uses the metadata to report termination and restart, without forwarding an exit code.
This is an adapter limitation in the tested configuration, not a lost aidebug event.

aidebug keeps `exit_code: null` and `target_exit_confirmed: false`. Do not use
the adapter's exit code as the target's code. For a safe reproduction, stop the
debugger and run the test separately. That result belongs to the separate run.
The check accepts a future adapter reporting an exit only when the code is exact.

## Scripted state-restoration trial

No current Loom bug was supplied, and history searches did not yield a runnable stale-state reproduction.
`examples/javascript/restore.js` is a synthetic fixture, not extracted Loom code.
It uses promises to force an old workspace load to finish after a new workspace
load, without depending on sleep durations. The old load overwrites the new tabs.

Both workflows establish the same mismatch: `workspace` is `old`, the active
workspace is `new`, and the pending tabs contain `old-tab`. One workflow adds a
temporary log. The other inspects the stopped function and live module scope
without editing the target. Both verify a fix in an isolated copy.

One local run measured these command costs:

| Measurement | Test and log | aidebug |
| --- | ---: | ---: |
| Time to captured causal evidence | 0.18 s | 2.27 s |
| Total through fix verification and cleanup | 0.25 s | 4.52 s |
| Commands | 3 | 13 |
| Temporary logging edits | 1 | 0 |
| Unexpected command failures | 0 | 0 |

The debugger measurement includes 0.28 s for `doctor`; the adapter was already
installed. Neither measurement includes initial installation or human/agent
reasoning time. The author knew the cause, breakpoint, and fix before this replay.
These numbers describe workflow overhead on a tiny fixture. They do not establish
that aidebug speeds up diagnosis or handles an intermittent Loom bug.

The logs were faster here. aidebug obtained the same evidence without adding a
log, and checked the repaired code in a fresh debug process. A safe direct rerun
verified exit zero because the adapter did not report it.

Reproduce with:

```sh
python checks/node_workflows.py --cli PATH/TO/aidebug --adapter PATH/TO/dapDebugServer.js
```

The summary is `checks/node-workflows-results.json`. Full commands, captured
values, output, and timings are in `checks/artifacts/node-workflows-results.json`.
Timings vary per run. To measure diagnosis benefit, use a real unfamiliar bug and
independent investigations with the same model, setup, and acceptance test.
