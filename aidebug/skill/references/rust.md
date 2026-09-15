# Rust executables

Build the requested target with debug symbols using its normal Cargo profile.
Keep the binary and source in sync; optimized code can obscure locals and steps.
CodeLLDB and its bundled LLDB libraries must already be installed. Point
`AIDEBUG_CODELLDB` or `--adapter PATH` at the CodeLLDB executable.

```sh
cargo build
aidebug launch --language rust --run --uncaught --breakpoint src/main.rs:18 target/debug/my_app -- sample.json
aidebug inspect
aidebug step over
aidebug continue
aidebug output
aidebug stop
```

Use `target/debug/my_app.exe` on Windows. aidebug launches a binary, not Cargo,
and does not compile automatically. For a test, build the test executable with
`cargo test --no-run` and launch the selected executable with its test filter.
Test harnesses can catch panics; use a source breakpoint for reliable evidence.

`--uncaught` requests Rust panic stops. Inspect application frames if the first
stop is in runtime code. Macros can map one source line to several locations,
causing repeated stops. Conditions use CodeLLDB expression syntax; check adapter
errors and observed values rather than assuming Python or JavaScript syntax.
