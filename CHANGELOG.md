# Changelog

## Unreleased

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
