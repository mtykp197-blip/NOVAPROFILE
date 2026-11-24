// NOVA PROFILE -- capture the window to a file.
//
// A development helper.  It is not part of the product: it exists
// because a screenshot of the interface is the only honest way to
// check that the interface looks the way it is meant to look, and
// asking a human to describe a window is not a check.
//
// Usage:  node tools/capture.js <output.png>

'use strict';

const { app, BrowserWindow } = require('electron');
const path = require('node:path');
const fs = require('node:fs');

const OUTPUT = process.argv[2] || path.join(process.cwd(), 'capture.png');

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

  // The interface needs a moment to reach the control plane and draw
  // the fleet; a capture taken too early shows a spinner.
  await new Promise((resolve) => setTimeout(resolve, 4000));

  const image = await window.webContents.capturePage();
  fs.writeFileSync(OUTPUT, image.toPNG());
  console.log('wrote ' + OUTPUT);

  app.quit();
});
