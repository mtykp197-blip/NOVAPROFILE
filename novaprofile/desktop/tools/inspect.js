// NOVA PROFILE -- check the interface from the inside.
//
// A screenshot proves the interface is not blank; it does not prove the
// interface shows the fleet.  This script asks the window what it drew
// and prints the answer, so a check can be made on the text rather than
// on a picture of the text.
//
// Usage:  node tools/inspect.js [output.json]

'use strict';

const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('node:path');
const fs = require('node:fs');
const http = require('node:http');

const OUTPUT = process.argv[2] || path.join(process.cwd(), 'inspect.json');

// The control plane the shell started, if it is running.  The renderer
// reaches it through the shell's own channels, so the tool has to
// stand in for the shell.
const API_BASE = process.env.NOVA_API_BASE || 'http://127.0.0.1:8731';
const API_KEY = process.env.NOVA_API_KEY || '';

ipcMain.handle('app:info', async () => ({
  version: 'dev',
  apiBase: API_BASE,
  dataDir: '',
  logsDir: '',
  coreDir: '',
  coreAvailable: false,
}));

ipcMain.handle('api:call', async (_event, { method, path: apiPath, body }) => {
  const headers = { accept: 'application/json' };
  if (body !== undefined) headers['content-type'] = 'application/json';
  if (API_KEY) headers['x-nova-key'] = API_KEY;

  return new Promise((resolve, reject) => {
    const url = new URL(API_BASE + apiPath);
    const request = http.request(
      {
        method,
        hostname: url.hostname,
        port: url.port,
        path: url.pathname + url.search,
        headers,
      },
      (res) => {
        const chunks = [];
        res.on('data', (chunk) => chunks.push(chunk));
        res.on('end', () => {
          const text = Buffer.concat(chunks).toString('utf8');
          try {
            resolve(text ? JSON.parse(text) : null);
          } catch {
            resolve({ detail: text });
          }
        });
      },
    );
    request.on('error', reject);
    if (body !== undefined) request.write(JSON.stringify(body));
    request.end();
  });
});

ipcMain.handle('window:show', async () => true);

app.whenReady().then(async () => {
  const window = new BrowserWindow({
    width: 1440,
    height: 900,
    show: false,
    backgroundColor: '#0B1020',
    webPreferences: {
      preload: path.join(__dirname, '..', 'electron', 'preload', 'index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  const renderer = path.join(__dirname, '..', 'electron', 'renderer', 'index.html');
  await window.loadFile(renderer);
  await new Promise((resolve) => setTimeout(resolve, 5000));

  const report = await window.webContents.executeJavaScript(`
    (() => {
      const text = (node) => (node ? node.textContent.trim() : null);
      const rows = [...document.querySelectorAll('tbody tr')].slice(0, 5).map((tr) =>
        [...tr.querySelectorAll('td')].map((td) => td.textContent.trim()),
      );
      return {
        title: text(document.getElementById('view-title')),
        status: text(document.getElementById('status-text')),
        version: text(document.getElementById('version')),
        stats: [...document.querySelectorAll('.stat')].map((s) => ({
          label: text(s.querySelector('.stat-label')),
          value: text(s.querySelector('.stat-value')),
          note: text(s.querySelector('.stat-note')),
        })),
        headings: [...document.querySelectorAll('thead th')].map((th) => th.textContent.trim()),
        rowCount: document.querySelectorAll('tbody tr').length,
        rows,
        empty: text(document.querySelector('.empty h3')),
      };
    })()
  `);

  fs.writeFileSync(OUTPUT, JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
  app.quit();
});
