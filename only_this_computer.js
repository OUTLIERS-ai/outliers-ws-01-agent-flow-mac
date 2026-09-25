// Loaded into agent-flow by guard.py when it starts agent-flow (Node.js option --require).
// We change none of agent-flow's own files: this runs first, inside the same program.
//
// agent-flow 0.9.1 opens 2 web servers on your computer: the page (your conversation,
// streamed live) and the port that receives events from hook.js. Neither checks where a
// request came from. Tested on 2026-09-24 with the real package: a request whose Host
// named another website got the page and the live stream back, and a made-up event sent
// with another website's Origin was drawn on the screen.
//
// This file makes both servers answer only this computer. A request is refused (403) when
//   - its Host is not 127.0.0.1, localhost or [::1] with the server's own port
//     (a website that points its own name at your computer sends its own name here), or
//   - it has an Origin other than that same address (a browser adds Origin when a
//     website sends a POST, or asks for a page on another address; hook.js sends none).
'use strict';
const http = require('http');

const THIS_COMPUTER = ['127.0.0.1', 'localhost', '[::1]'];

function allowed(req, server) {
  const addr = server.address();
  const port = addr && typeof addr === 'object' ? addr.port : null;
  if (!port) return false;
  const hosts = THIS_COMPUTER.map((h) => `${h}:${port}`);
  const host = String(req.headers.host || '').trim().toLowerCase();
  if (!hosts.includes(host)) return false;
  const origin = req.headers.origin;
  if (origin === undefined) return true;
  return hosts.map((h) => `http://${h}`).includes(String(origin).trim().toLowerCase());
}

const originalCreateServer = http.createServer;
http.createServer = function createServer(...args) {
  const server = originalCreateServer.apply(this, args);
  const emit = server.emit;
  server.emit = function (event, req, res, ...rest) {
    if (event === 'request' && !allowed(req, server)) {
      res.writeHead(403, { 'Content-Type': 'text/plain; charset=utf-8' });
      res.end('agent-flow only answers requests from this computer\n');
      return true;
    }
    return emit.call(this, event, req, res, ...rest);
  };
  return server;
};
