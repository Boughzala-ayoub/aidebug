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

The second command assumes `AIDEBUG_JS_DEBUG` is set. Inspect breakpoint
verification and the actual stopped source. If mapping fails, check generated
files and source maps before stepping through unrelated generated code.

## Browser UI

Start the project's local development server using its documented command and
confirm the requested local URL responds. aidebug launches its own browser;
it does not attach to the user's existing browser or start a server.

```sh
aidebug launch --language typescript --target browser --headed --web-root . --run --breakpoint src/app.ts:12 http://127.0.0.1:5173/
```

Use `--browser PATH` if Chrome, Chromium, or Edge cannot be found. Interact with
the launched page to trigger the bug, using browser automation when available.
Run `automation` after `phase: ready` to get a private page CDP connection and
the installed `client_module`. That module exports `connectPuppeteer(puppeteer,
automation)` for an already installed Puppeteer and Node.js 22 or newer. It returns
a browser object with the one launched page. Use `browser.pages()`, `page.click`,
and `page.screenshot`; call `browser.disconnect()` afterward, not `browser.close()`.
Keep the connection URL private. Do not add self-play code solely to click a button.

Use `sources --filter NAME` to inspect loaded sources and breakpoint verification.
Directory URLs with a matching local `index.html` get a source mapping.
For a custom route, pass `--url-path /ROUTE/=path/to/index.html` at launch.
File URLs require matching local filenames; sibling scripts resolve relative to
that local directory. Other URL rewrites need server paths or source maps. The requested
URL is preserved. Check the actual stopped file and source excerpt.

Headless launches keep Chrome's default GPU behavior. Use `--disable-gpu` only
when deliberately testing that configuration. Browser launch settings are returned
in status. Match those settings when comparing a debugger run with a sampling run.
The debugger inspects JavaScript execution; use browser tools separately to
inspect layout, DOM, network requests, and screenshots. An idle event loop can
delay `pause` until a new JavaScript task. Check `status` after any timeout.

Framework dev servers, workers, and child processes can require capabilities
beyond the current single-target session. Do not claim coverage of a worker or
server-side process based only on a stop in the page.
