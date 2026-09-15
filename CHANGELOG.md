# Changelog

## Unreleased

- Ignore `VIRTUAL_ENV` when it identifies aidebug's own environment, so activating the tool does not override the workspace's Python environment. The regression check failed before the fix and passed on Windows and Ubuntu WSL afterward.
- Keep check summaries in Git and full reports under the ignored `checks/artifacts/` directory.

## 0.5.0 (2026-09-14)

Added conditional breakpoints, Python module launch, interpreter selection, and pause. The CLI help includes the agent debugging loop. CI covers Windows and Ubuntu.

Windows verification used Python 3.11.0, debugpy 1.8.21, Node 24.18.0, js-debug 1.117.0, CodeLLDB 1.12.3, and Rust 1.97.1. Nine Python, thirteen multi-language, three project, and six viewer check groups passed. The viewer checks captured 72 screenshots across nine views and eight widths. Transport, recording, and the average calculation check also passed.

Ubuntu WSL verification used Python 3.14.4 and debugpy 1.8.21. Transport, recording, the average calculation check, eight Python integration groups, and three project groups passed. Hosted GitHub CI had not yet run.

An earlier Linux check found that exited adapters were not reaped while the helper remained available. The transport now reaps them. A Windows browser cleanup check encountered a transient profile file lock. Retrying `stop` after the lock cleared removed the profile and session metadata.

The original average-calculation investigation stopped at line 4 with `prices = [10, 20]` and `count = 0`. Subtracting paid orders from all orders counted unpaid orders. The corrected example divides by the number of paid orders, rejects an empty set, and prints `15.0`. The integration check preserves the original failure in its temporary fixture.
