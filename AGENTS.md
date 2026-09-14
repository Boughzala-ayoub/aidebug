# Contributor rules

Read README.md and the affected code before changing the debugger. Preserve unrelated user edits.
Keep the runtime to Python's standard library and debugpy. Use the existing DAP and session layers.
Keep agent usage instructions in the installed CLI's `--help`, where agents in other projects can find them.
After source changes, reinstall the package and run the relevant installed-CLI checks from README.md.
Changes to shared session or transport code require Python and multi-language checks when adapters are available.
After any viewer change, run checks/viewer.mjs against the installed, served assets in a fresh headless browser.
Check every view and open navigation at 320/375/768/1024/1280, including screenshots for readability.
Do not claim a platform or adapter combination works without a real successful run. Keep verification results current.
