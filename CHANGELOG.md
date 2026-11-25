# Changelog

## 0.8.0 (2026-09-22)

- Create browser profiles in the system temporary directory instead of the workspace's `.aidebug/`. With the workspace at a Vite 6.4.3 project root, the dev server crashed with `EBUSY` watching Chrome's locked `Network\Cookies`; after the change it kept serving through launch and stop. Status reports the path as `launch.profile`.
- Close the Puppeteer transport on a malformed CDP frame, so pending requests fail immediately instead of waiting out their timers.
- Shorten `--help` from 102 to 84 lines and the skill from 6.6 KB to 4.3 KB; the skill now points to `--help` instead of repeating it. Move browser automation and source mapping details from the README into the JavaScript skill reference. The README went from 50 KB to 28 KB.
- Multi-language check: wait for `phase: ready` before pausing Node and Rust targets, inspect again after `inspection_truncated`, give conditional launches the 60-second maximum deadline, allow Chrome descendants 20 seconds to exit, retry `stop` up to six times, and record which process holds a locked profile file.
- Run the Python CI checks on macOS as well.

All installed-CLI checks passed on Windows: transport, recording, the average calculation, nine Python integration groups, the experience checks, three project groups with pytest, seven skill groups, PowerShell, Node workflows, four browser experience groups, thirteen multi-language groups, and report stability. Both managed user skills were updated.

On an idle machine the unchanged 0.7.0 multi-language check passed 10 of 10 runs. With about 80% background CPU load, interleaved runs passed 1 of 5 for 0.7.0 and 3 of 5 for this build. Every failure was a check race that both builds hit: pausing during `phase: initializing`, a truncated inspection, a short exit wait, or Chrome holding `CrashpadMetrics-active.pma` past three `stop` calls. The new lock record named the session's own Chrome process in each case, and it exited later without leaking. Whether that Chrome process was slow to terminate or outside the Job Object is not established. Linux, macOS, and hosted CI were not run for these changes.

- Make missing-adapter errors name the attempted path, environment variable, and installed setup help. Clarify that `doctor` checks paths only. Add Windows PowerShell 5.1 examples tested through the actual shell, and guidance for choosing debugging and verifying unknown target exits. Add CI checks for shell quoting, Node exit outcomes, and an isolated stale-restoration trial.

These changes passed nine Python integration groups, five workflow groups, thirteen multi-language groups, seven skill lifecycle groups, the PowerShell check, two Node workflow groups, and report checks on Windows. The package was reinstalled and both managed user skills updated. The first multi-language run failed on a Chrome profile file lock during cleanup; an isolated unchanged rerun passed. Its full report is retained in `checks/artifacts/multilanguage-workflow-cleanup-failure.json`. Linux, macOS, and hosted CI were not run for these changes.

Node exits `0` and `7` remain unknown with the tested js-debug 1.117.0 termination path, which does not forward the target exit code. The new checks require an exact code when confirmed and otherwise retain an unknown result. A synthetic state-restoration replay measured 0.18 seconds to evidence with a temporary log and 2.27 seconds with live inspection. It uses a known cause and fix and does not measure AI diagnosis speed. See [the investigation and trial](checks/agent-workflows.md).

- Limit each workspace to one active `wait`; duplicate waits return `busy` so they cannot accumulate and exhaust control connections. Preserve structured adapter explanations and IDs in CLI errors, session failures, and recordings. Fetch scopes once per inspection. Add a Windows CI job for multi-language debugging, browser automation, and the installed viewer, with report and screenshot artifacts.

These changes passed nine Python integration groups, five workflow groups, thirteen multi-language groups, four browser groups, seven viewer groups with 80 screenshots, and the transport, recording, and report checks on Windows. The Rust conditional-breakpoint fixture now uses `black_box` instead of repeated printing. The printing fixture stalled with both the committed and updated debugger; its cause remains unconfirmed. Failure reports are retained in `checks/artifacts/multilanguage-rust-deadline*.json`. CI YAML, shell syntax, and adapter download URLs were checked locally; hosted CI has not run because no GitHub remote is configured. These changes have not been checked on Linux or macOS.

## 0.7.0 (2026-09-17)

- Add output-only `--fields` projection for nested JSON and arrays, retaining errors and truncation/paging flags. Preserve async stack labels, reject historical frame inspection with `frame_not_inspectable`, and identify parent handles in failed value lookups. Document UTF-8 output for Python consumers on Windows.

The follow-up CLI changes passed nine Python integration groups, thirteen multi-language groups, four workflow groups, four browser groups, and seven skill lifecycle groups on Windows. The installed viewer passed seven groups and produced 80 screenshots across eight widths, including async stack labels. The browser regression reproduced the original async-frame failure before the fix and verified the corrected error, live scopes, DOM lookup guidance, and intact UTF-8 source excerpts afterward. An initial multi-language run failed its browser descendant-cleanup deadline; the unchanged check passed on an isolated rerun. Its failure report remains in `checks/artifacts/multilanguage-initial-cleanup-failure.json`. These follow-up changes have not been checked on Linux or macOS.
- Add all-scope inspection, selected-value traversal, asynchronous launch, responsive waits, and setup diagnostics. Report debugging termination without a target exit as an unknown target outcome with diagnostics.
- Add private same-page CDP access and a bundled Puppeteer transport, browser source mappings, and replay of selected values and scope expansions. Headless launches now preserve Chrome's default GPU behavior; `--disable-gpu` is explicit.

Version 0.7.0 verification passed nine Python integration groups, thirteen multi-language groups, three agent-workflow groups, three browser-automation groups, and seven skill lifecycle groups on Windows. The viewer passed six groups with 72 screenshots across eight widths; selected values appear in replay. Transport and recording checks passed. Ubuntu WSL passed eight Python integration groups, three project groups, three agent-workflow groups, seven skill groups, and transport, recording, and average-calculation checks. The official skill validator passed, and both managed user skills were updated. macOS, Playwright, and actual AI tool-selection behavior were not tested.

The browser check reads a module-scoped geometry count of 731 without modifying the page to expose a local snapshot. Puppeteer clicks and captures the same page while aidebug stops and resumes it. Directory, explicit HTML, and custom-route URLs resolve to the expected local source, including a sibling script. Closing the page yields an unconfirmed termination with event history; the automation secret is absent from recordings. Custom file mappings require matching filenames, and directory routes require `index.html`.

An initial concurrent verification run hit a CDP test deadline and CodeLLDB's connection deadline. Isolated reruns passed; the cause of those intermittent timeouts was not established. The earlier unexplained exit in the user's game was not reproduced or attributed to a renderer crash.

## 0.6.0 (2026-09-15)

- Add `launch --no-record` and `recordings prune --older-than DAYS [--dry-run]`. Cleanup uses the workspace lock, preserves unrelated files and links, and reports matched and deleted bytes.

- Bundle a shared runtime debugging skill for Codex and Claude Code, with Python, JavaScript/TypeScript, and Rust references. Add JSON `skills install`, `status`, and `uninstall` commands for user or project scope. Track owned files and preserve local edits during updates and removal.
- Ignore `VIRTUAL_ENV` when it identifies aidebug's own environment, so activating the tool does not override the workspace's Python environment. The regression check failed before the fix and passed on Windows and Ubuntu WSL afterward.
- Keep check summaries in Git and full reports under the ignored `checks/artifacts/` directory.

Recording controls passed the installed-package recording checks, nine Python integration groups, and thirteen multi-language groups on Windows. Opt-out was exercised with Python modules, Node JavaScript and TypeScript, browser TypeScript, and Rust. Cleanup checks include interrupted recordings, write-age filtering, active-session exclusion, deletion failures, and real Windows junction protection. These recording controls have not been verified on Linux or macOS.

Version 0.6.0 verification passed seven skill lifecycle groups on Windows with Python 3.11.0 and Ubuntu WSL with Python 3.14.4. The checks include real Windows junction and POSIX symlink protection. The official skill validator passed. Real debugpy integration passed nine Windows and eight Linux groups; Linux transport, recording, average calculation, and three project groups also passed. Both actual user-scope registrations were installed, removed, and reinstalled successfully. Agent model selection and macOS discovery were not tested.

## 0.5.0 (2026-09-14)

Added conditional breakpoints, Python module launch, interpreter selection, and pause. The CLI help includes the agent debugging loop. CI covers Windows and Ubuntu.

Windows verification used Python 3.11.0, debugpy 1.8.21, Node 24.18.0, js-debug 1.117.0, CodeLLDB 1.12.3, and Rust 1.97.1. Nine Python, thirteen multi-language, three project, and six viewer check groups passed. The viewer checks captured 72 screenshots across nine views and eight widths. Transport, recording, and the average calculation check also passed.

Ubuntu WSL verification used Python 3.14.4 and debugpy 1.8.21. Transport, recording, the average calculation check, eight Python integration groups, and three project groups passed. Hosted GitHub CI had not yet run.

An earlier Linux check found that exited adapters were not reaped while the helper remained available. The transport now reaps them. A Windows browser cleanup check encountered a transient profile file lock. Retrying `stop` after the lock cleared removed the profile and session metadata.

The original average-calculation investigation stopped at line 4 with `prices = [10, 20]` and `count = 0`. Subtracting paid orders from all orders counted unpaid orders. The corrected example divides by the number of paid orders, rejects an empty set, and prints `15.0`. The integration check preserves the original failure in its temporary fixture.
