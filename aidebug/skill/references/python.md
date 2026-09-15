# Python scripts and tests

Run these commands from the target workspace. Replace paths and line numbers
with the actual reproduction. Set `--python PATH` when the project environment
is known. Otherwise inspect the returned `python` path: aidebug checks an active
virtual environment other than its own, the workspace `.venv`, then its own Python.
Dependencies such as pytest must already be installed in the target interpreter.

```sh
aidebug launch --run --uncaught --breakpoint app.py:24 app.py -- --input sample.json
aidebug inspect
aidebug step over
aidebug continue
aidebug output
aidebug stop
```

For a failing test, use module launch and a breakpoint at the assertion or the
application code it calls:

```sh
aidebug launch --module --run --breakpoint pricing.py:6 pytest -- -q test_checkout.py
# Alternative runner:
aidebug launch --module --run --breakpoint pricing.py:6 unittest -- -q test_checkout
```

These are alternative launches; stop an owned session before starting another.
Test runners catch assertions. `--uncaught` may stop later at their `SystemExit`,
where the original test locals are gone. Breakpoints preserve the useful frame.
After collecting exception evidence, `exceptions off` then `continue` lets the
runner finish. Read `output` and the exit code before cleanup.
