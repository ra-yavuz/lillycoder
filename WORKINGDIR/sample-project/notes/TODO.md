# TODO

Things to chip away at, roughly in priority order:

- [ ] `src/util.py` has an unfinished `count_users_by_domain` — implement it.
- [ ] `src/calculator.js` divide function blows up on zero. Fix it (return
      `null` or throw a clear error, your call).
- [ ] `src/server.js` has zero error handling — wrap the request handler
      so a bad URL or unknown op responds with 400 + a JSON error.
- [ ] add a tiny `tests/` folder with at least one assertion for the
      calculator. node's built-in `assert` is fine.
- [ ] write a `package.json` so `npm test` actually does something.
