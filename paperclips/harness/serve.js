// Tiny static server for the local Universal Paperclips copy.
// Injects harness/shim.js (and optionally harness/bot.js) before the game scripts,
// and strips the Google Analytics tags so the run is fully offline.
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..');
const HARNESS = __dirname;
const PORT = Number(process.env.PC_PORT || 8899);

const MIME = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.png': 'image/png', '.svg': 'image/svg+xml', '.mp3': 'audio/mpeg', '.json': 'application/json' };

function buildIndex() {
  let html = fs.readFileSync(path.join(ROOT, 'index2.html'), 'utf8');
  // strip GA
  html = html.replace(/\s*<script async src="https:\/\/www\.googletagmanager[^>]*><\/script>/, '');
  html = html.replace(/<!-- Global Site Tag[\s\S]*?<\/script>\s*<\/script>/, '');
  html = html.replace(/<script>\s*window\.dataLayer[\s\S]*?<\/script>/, '');
  const inject = ['<script src="/__harness/shim.js"></script>'];
  if (process.env.PC_BOT !== '0') inject.push('<script src="/__harness/bot.js"></script>');
  html = html.replace('<script type="text/javascript" src="combat.js', inject.join('\n        ') + '\n        <script type="text/javascript" src="combat.js');
  return html;
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1');
  let p = decodeURIComponent(url.pathname);
  if (p === '/__harness/reload') {
    // rebuild index on demand (no caching)
    res.writeHead(200, { 'content-type': 'text/plain' });
    return res.end('ok');
  }
  if (p.startsWith('/__harness/')) {
    const f = path.join(HARNESS, p.slice('/__harness/'.length).replace(/\?.*$/, ''));
    if (fs.existsSync(f)) {
      res.writeHead(200, { 'content-type': MIME[path.extname(f)] || 'text/plain', 'cache-control': 'no-store' });
      return res.end(fs.readFileSync(f));
    }
    res.writeHead(404); return res.end('no');
  }
  if (p === '/' || p === '/index2.html') {
    res.writeHead(200, { 'content-type': MIME['.html'], 'cache-control': 'no-store' });
    return res.end(buildIndex());
  }
  const f = path.join(ROOT, p.replace(/^\//, '').replace(/\?.*$/, ''));
  if (!f.startsWith(ROOT) || !fs.existsSync(f)) { res.writeHead(404); return res.end('404'); }
  res.writeHead(200, { 'content-type': MIME[path.extname(f)] || 'application/octet-stream', 'cache-control': 'no-store' });
  res.end(fs.readFileSync(f));
});

server.listen(PORT, '127.0.0.1', () => console.log('paperclips harness on http://127.0.0.1:' + PORT + '/index2.html'));
