# JavaScript and TypeScript

Use Node.js and Microsoft's standalone js-debug DAP server. Pass its
`dapDebugServer.js` through `--adapter PATH` or `AIDEBUG_JS_DEBUG`. The CLI does not
download adapters. Read `launch --help` and report missing dependencies precisely.

## Node.js

```sh
aidebug launch --language javascript --target node --adapter /tools/js-debug/src/dapDebugServer.js --run --uncaught --breakpoint src/app.js:12 src/app.js -- sample.json
```

Compile TypeScript with source maps using the project's existing build command.
Launch the generated JavaScript and set breakpoints in the TypeScript source:

```sh
aidebug launch --language typescript --target node --out-files "dist/**/*.js" --run --breakpoint src/app.ts:12 dist/app.js
```

The second command assumes `AIDEBUG_JS_DEBUG` is set. `--out-files` describes the
generated JavaScript and defaults to `WORKSPACE/**/*.js`. Inspect breakpoint
verification and the actual stopped source. If mapping fails, check generated
files and source maps before stepping through unrelated generated code.

## Browser UI

Start the project's local development server using its documented command and
confirm the requested local URL responds. aidebug launches its own browser;
it does not attach to the user's existing browser or start a server. Only
localhost HTTP and HTTPS URLs are accepted.

```sh
aidebug launch --language typescript --target browser --headed --web-root . --run --breakpoint src/app.ts:12 http://127.0.0.1:5173/
```

Launch is headless unless `--headed` is given. Use `--browser PATH` if Chrome,
Chromium, or Edge cannot be found. Each session gets a fresh profile in the system
temporary directory, outside the workspace so dev-server file watchers never see
it; `stop` removes it and status reports its path under `launch.profile`.

Interact with the launched page to trigger the bug, using browser automation when
available. Do not add self-play code solely to click a button.

## Automate the launched page

Run `automation` after `phase: ready` to get a private `websocket_url` and the
installed `client_module`. The URL reuses js-debug's local page CDP proxy, is
omitted from recordings, and grants control of the page: keep it private.

The module exports `connectPuppeteer(puppeteer, automation)` for an already
installed `puppeteer-core` on Node.js 22 or newer:

```javascript
import puppeteer from 'puppeteer-core';
import {execFileSync} from 'node:child_process';
import {pathToFileURL} from 'node:url';
const result = JSON.parse(execFileSync('aidebug', ['--workspace', process.cwd(), 'automation'], {encoding: 'utf8'}));
if (!result.ok) throw new Error(result.error.message);
const {connectPuppeteer} = await import(pathToFileURL(result.automation.client_module));
const browser = await connectPuppeteer(puppeteer, result.automation);
try {
  const [page] = await browser.pages();
  await page.click('#start');
  await page.screenshot({path: 'game.png'});
} finally {
  await browser.disconnect();
}
```

A click or page evaluation can wait while JavaScript is paused, so inspect and
continue from a separate CLI invocation. Only the launched page is exposed: new
tabs, browser contexts, debugger commands through CDP, and `browser.close()` are
rejected. Use `aidebug stop` for cleanup. Automation actions are not recorded;
add `note` entries for actions worth keeping. Playwright is unverified.

## Source mapping

Use `sources --filter NAME` to inspect loaded sources and breakpoint verification.
Directory URLs with a matching local `index.html` under `--web-root` get a source
mapping. For a custom route, pass `--url-path /ROUTE/=path/to/index.html` at launch.
File URLs require matching local filenames; sibling scripts resolve relative to
that local directory. Other URL rewrites need server paths or source maps. The
requested URL is preserved. Check the actual stopped file and source excerpt; an
unbound breakpoint provides no coverage.

## Timing and limits

Headless launches keep Chrome's default GPU behavior. Use `--disable-gpu` only
when deliberately testing that configuration. Browser launch settings are returned
in status. Match those settings when comparing a debugger run with a sampling run.
The debugger inspects JavaScript execution; use browser tools separately to
inspect layout, DOM, network requests, and screenshots. `value` follows the
adapter's children, so inherited properties and DOM getters can be absent. An idle
event loop can delay `pause` until a new JavaScript task. Check `status` after any
timeout.

Historical frames after an async boundary have `async: true` and
`scopes_available: false`; select a live frame for scopes and locals.

js-debug does not forward some Node exit codes, so a finished Node target can end
as `terminated` with `target_exit_confirmed: false`. Framework dev servers, workers,
and child processes can require capabilities beyond the current single-target
session. Do not claim coverage of a worker or server-side process based only on a
stop in the page.
