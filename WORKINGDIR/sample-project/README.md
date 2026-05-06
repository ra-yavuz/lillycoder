# sample-project

A tiny scratch project for trying out lilly-console. There's some
deliberately broken stuff in here for her to find and fix.

## what's in here

- `src/calculator.js` — adds + subtracts. There's a bug in the divide
  function (try `lilly: find any obvious bugs in calculator.js`).
- `src/server.js` — a tiny http server. Has no error handling.
- `src/util.py` — a small utility script. Has a `TODO` comment that
  should get done.
- `data/users.csv` — 10 fake user rows. Try `lilly: how many users have
  emails ending in @gmail.com?`
- `data/log.txt` — a few hundred fake log lines, mix of INFO/WARN/ERROR.
- `notes/TODO.md` — a list of things to do.

## ideas to try

- "what's in this project?"
- "read the README and TODO and tell me what to work on first"
- "find the bug in src/calculator.js and fix it"
- "add try/catch + a proper error response to src/server.js"
- "do the TODO in src/util.py"
- "how many ERROR lines are in data/log.txt?"
- "scaffold a tests/ folder with one test for the calculator"

If you want her to just go without asking each time, exit and start her
again with `--bypass-permissions`.
