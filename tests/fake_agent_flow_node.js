// A stand-in for agent-flow 0.9.1's 2 web servers, used only by tests/test_other_websites.py.
// Like the real package it answers every request and checks neither Host nor Origin:
//   - the page server, on the port given with --port (the real one streams your conversation)
//   - the event server, on a port picked at random (the real one takes events from hook.js)
// It writes the event server's port into event-port.txt in the folder it was started from.
'use strict';
const fs = require('fs');
const http = require('http');
const path = require('path');

const i = process.argv.indexOf('--port');
const pagePort = i > 0 ? parseInt(process.argv[i + 1], 10) : 0;

http.createServer((req, res) => {
  res.writeHead(200, { 'Content-Type': 'text/html' });
  res.end('<!DOCTYPE html><title>Agent Flow</title>');
}).listen(pagePort, '127.0.0.1');

const events = http.createServer((req, res) => {
  let body = '';
  req.on('data', (c) => { body += c; });
  req.on('end', () => { res.writeHead(200); res.end(); });
});
events.listen(0, '127.0.0.1', () => {
  fs.writeFileSync(path.join(process.cwd(), 'event-port.txt'), String(events.address().port));
});
