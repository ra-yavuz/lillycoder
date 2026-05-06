// Bare http server. Intentionally has no error handling.
const http = require('http');
const calc = require('./calculator');

const server = http.createServer((req, res) => {
  // Parse query like /add?a=2&b=3
  const url = new URL(req.url, `http://${req.headers.host}`);
  const op = url.pathname.slice(1);
  const a = Number(url.searchParams.get('a'));
  const b = Number(url.searchParams.get('b'));
  const result = calc[op](a, b);   // crashes if op isn't a key on calc
  res.setHeader('content-type', 'application/json');
  res.end(JSON.stringify({ op, a, b, result }));
});

server.listen(3000, () => console.log('listening on :3000'));
