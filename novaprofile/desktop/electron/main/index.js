// NOVA PROFILE -- the desktop shell.
//
// The shell owns three things and nothing else:
//
//   1. the control plane, a Python service that holds the profiles and
//      answers every question about them;
//   2. the window the operator works in;
//   3. the browser core, the patched Chromium that carries a profile's
//      own readings.
//
// The shell does not know what a profile is.  It starts the service,
// waits for it to report itself alive, and forwards the operator's
// actions to it.  Everything that knows about fingerprints lives
// behind that boundary, which is what keeps the two halves honest:
// there is one implementation of the rules, and it is the one that is
// tested.

'use strict';

const { app, BrowserWindow, ipcMain, dialog, shell, Menu } = require('electron');
const { spawn } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');
const net = require('node:net');
const http = require('node:http');

const IS_DEV = process.argv.includes('--dev');

// The port the control plane listens on.  A fixed port keeps the
// shell's own address book simple; the shell checks that the port is
// free before it starts anything, and refuses to start if it is taken
// by a process that is not the control plane.
const DEFAULT_PORT = 8731;

const paths = {
  resources: () => (app.isPackaged ? process.resourcesPath : path.join(__dirname, '..', '..', '..')),
  // The control plane's source tree.
  //
  // The shell lives in ``novaprofile/desktop`` and the engine lives in
  // ``novaprofile/control_plane``.  This file lives in
  // ``novaprofile/desktop/electron/main``, so the engine is three
  // levels up: ``main`` -> ``electron`` -> ``desktop`` -> ``novaprofile``.
  backendDir: () =>
    app.isPackaged
      ? path.join(process.resourcesPath, 'backend')
      : path.join(__dirname, '..', '..', '..', 'control_plane'),
  // The Chromium core.
  //
  // A packaged shell carries the core beside itself.  A development
  // shell is pointed at a build directory, because the core is built
  // outside the tree -- it takes hours and a diskful of toolchain, and
  // it is rebuilt whenever the patch series changes.
  coreDir: () =>
    app.isPackaged
      ? path.join(process.resourcesPath, 'core')
      : process.env.NOVA_CORE_DIR || path.join(__dirname, '..', '..', '..', 'core', 'build', 'nova-win'),
  dataDir: () => path.join(app.getPath('userData'), 'data'),
  logsDir: () => path.join(app.getPath('userData'), 'logs'),
};

/** The running control plane, if there is one. */
let backend = null;

/** The window the operator works in. */
let mainWindow = null;

/** The address of the control plane. */
let apiBase = null;

/**
 * Whether a port can be bound.
 *
 * The shell does not take a port by force: a profile manager that
 * fights another program for a port loses profiles to it.  The check
 * is a bind attempt on the loopback interface, and the port is free
 * when the attempt fails with the address already in use.
 */
function portIsFree(port) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once('error', () => resolve(false));
    server.once('listening', () => server.close(() => resolve(true)));
    server.listen(port, '127.0.0.1');
  });
}

/**
 * Find a port the control plane can take.
 *
 * The preferred port is tried first, and the ones after it are tried
 * in turn.  The port that is finally used is reported to the renderer,
 * so the renderer never has to guess where the service is.
 */
async function choosePort(preferred) {
  const candidates = [preferred, DEFAULT_PORT + 1, DEFAULT_PORT + 2, DEFAULT_PORT + 3];
  for (const port of candidates) {
    if (await portIsFree(port)) return port;
  }
  throw new Error('no free port for the control plane');
}

/**
 * Wait for the control plane to report itself alive.
 *
 * The service prints one line of JSON when it is ready -- the port it
 * bound and the key it wants -- and the shell reads that line rather
 * than polling for it, because a poll cannot tell the difference
 * between a service that is starting and a service that is wedged.
 */
function waitForReady(child, timeoutMs = 60000) {
  return new Promise((resolve, reject) => {
    let buffer = '';
    let settled = false;

    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      reject(new Error('the control plane did not report itself ready in time'));
    }, timeoutMs);

    const finish = (value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(value);
    };

    const fail = (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(error);
    };

    child.stdout.setEncoding('utf8');
    child.stdout.on('data', (chunk) => {
      buffer += chunk;
      const lines = buffer.split('\n');
      buffer = lines.pop() ?? '';
      for (const line of lines) {
        const text = line.trim();
        if (!text.startsWith('{')) continue;
        try {
          const payload = JSON.parse(text);
          if (payload && typeof payload.port === 'number') {
            finish(payload);
            return;
          }
        } catch {
          // A line that is not the handshake is not the handshake.
        }
      }
    });

    child.stderr.setEncoding('utf8');
    child.stderr.on('data', (chunk) => {
      const text = chunk.toString();
      process.stderr.write(text);
      if (/Traceback|Error:|ERROR/i.test(text) && !settled) {
        // Give the service a moment to print its own line before
        // giving up on it: the traceback may be the last thing it
        // writes.
        setTimeout(() => fail(new Error(text.split('\n')[0])), 500);
      }
    });

    child.on('exit', (code) => {
      fail(new Error(`the control plane stopped with code ${code}`));
    });
  });
}

/**
 * Start the control plane.
 *
 * A packaged shell runs the frozen executable.  A development shell
 * runs the service out of the source tree with the interpreter of the
 * machine, so that a change in the engine does not need a rebuild.
 */
async function startBackend() {
  fs.mkdirSync(paths.logsDir(), { recursive: true });
  fs.mkdirSync(paths.dataDir(), { recursive: true });

  const port = await choosePort(DEFAULT_PORT);
  const packaged = app.isPackaged;

  const pythonCandidates = [
    path.join(paths.backendDir(), 'nova-backend.exe'),
    path.join(paths.backendDir(), 'nova-backend'),
  ];
  const executable = packaged
    ? pythonCandidates.find((candidate) => fs.existsSync(candidate))
    : null;

  let command;
  let args;
  let spawnOptions;

  if (executable) {
    command = executable;
    args = ['--port', String(port), '--data-dir', paths.dataDir()];
    spawnOptions = { windowsHide: true };
  } else {
    command = process.env.NOVA_PYTHON || 'python';
    args = [
      '-m',
      'novaprofile.cli.main',
      'serve',
      '--port',
      String(port),
      '--data-dir',
      paths.dataDir(),
      '--handshake',
    ];
    spawnOptions = {
      cwd: paths.backendDir(),
      windowsHide: true,
      env: { ...process.env, PYTHONPATH: paths.backendDir() },
    };
  }

  const logFile = path.join(paths.logsDir(), 'backend.log');
  const logStream = fs.createWriteStream(logFile, { flags: 'a' });

  const child = spawn(command, args, {
    ...spawnOptions,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  child.stdout.pipe(logStream);
  child.stderr.pipe(logStream);

  const handshake = await waitForReady(child);
  apiBase = `http://127.0.0.1:${handshake.port}`;
  backend = child;
  backend.on('exit', () => {
    backend = null;
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('backend:stopped');
    }
  });

  return { base: apiBase, apiKey: handshake.api_key ?? '', port: handshake.port };
}

/**
 * Stop the control plane.
 *
 * The service is asked to stop first, and it is killed only if it does
 * not go on its own: a service that is killed mid-write leaves a
 * profile half-written.
 */
function stopBackend() {
  return new Promise((resolve) => {
    if (!backend) {
      resolve();
      return;
    }
    const child = backend;
    const timer = setTimeout(() => {
      try {
        child.kill();
      } catch {
        // The process is already gone.
      }
    }, 5000);

    child.once('exit', () => {
      clearTimeout(timer);
      backend = null;
      resolve();
    });
    try {
      child.kill();
    } catch {
      clearTimeout(timer);
      backend = null;
      resolve();
    }
  });
}

/**
 * Call the control plane.
 *
 * The renderer never speaks to the service directly: it asks the
 * shell, and the shell holds the credential.  A key that lives in the
 * renderer's memory is a key a page injected into the window can read.
 */
async function callApi(method, apiPath, body) {
  if (!apiBase) {
    throw new Error('the control plane is not running');
  }
  const headers = { accept: 'application/json' };
  if (body !== undefined) headers['content-type'] = 'application/json';
  if (apiKey) headers['x-nova-key'] = apiKey;

  const response = await new Promise((resolve, reject) => {
    const url = new URL(apiBase + apiPath);
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
          let payload = null;
          try {
            payload = text ? JSON.parse(text) : null;
          } catch {
            payload = { detail: text };
          }
          resolve({ status: res.statusCode, body: payload });
        });
      },
    );
    request.on('error', reject);
    if (body !== undefined) request.write(JSON.stringify(body));
    request.end();
  });

  if (response.status >= 400) {
    const detail =
      response.body && typeof response.body === 'object'
        ? response.body.detail || response.body.error || JSON.stringify(response.body)
        : String(response.body);
    throw new Error(`${response.status}: ${detail}`);
  }
  return response.body;
}

// The credential the control plane asked for.  It is kept in the
// main process and never handed to the renderer except through the
// shell's own channels.
let apiKey = '';

/**
 * Build the window.
 *
 * The window opens on the control plane's own address, so there is no
 * second copy of the interface to keep in step with the service.
 */
function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1100,
    minHeight: 700,
    show: false,
    backgroundColor: '#0B1020',
    title: 'NOVA PROFILE',
    icon: path.join(__dirname, '..', 'build', 'icon.ico'),
    webPreferences: {
      preload: path.join(__dirname, '..', 'preload', 'index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      spellcheck: false,
    },
  });

  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
    if (IS_DEV) mainWindow.webContents.openDevTools({ mode: 'detach' });
  });

  // The window shows the interface that ships with the application.
  //
  // It is a local document, not a page: the operator's profiles are on
  // this machine and the interface that reads them must not be one
  // fetch away from being somebody else's.  The control plane is
  // reached through the shell's own channels, never through the
  // renderer's own network stack.
  mainWindow.loadFile(path.join(__dirname, '..', 'renderer', 'index.html'));

  // A window that is closed does not close the application: the
  // control plane keeps running, and the shell's tray entry brings
  // the window back.
  mainWindow.on('close', (event) => {
    if (app.isQuitting) return;
    event.preventDefault();
    mainWindow.hide();
  });

  // Nothing navigates away from the local document.  A link that
  // points outward is opened in the operator's own browser, where the
  // operator can see it and close it.
  mainWindow.webContents.on('will-navigate', (event, url) => {
    event.preventDefault();
    if (url !== mainWindow.webContents.getURL()) shell.openExternal(url);
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  return mainWindow;
}

/**
 * Show the window and bring it to the front.
 */
function showWindow() {
  if (!mainWindow || mainWindow.isDestroyed()) {
    createWindow();
  }
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
}

function buildMenu() {
  const template = [
    {
      label: 'File',
      submenu: [
        {
          label: 'New Profile',
          accelerator: 'CmdOrCtrl+N',
          click: () => mainWindow?.webContents.send('menu:new-profile'),
        },
        {
          label: 'Bulk Create',
          accelerator: 'CmdOrCtrl+Shift+N',
          click: () => mainWindow?.webContents.send('menu:bulk-create'),
        },
        { type: 'separator' },
        { role: 'quit', label: 'Exit' },
      ],
    },
    {
      label: 'View',
      submenu: [
        { role: 'reload' },
        { role: 'forceReload' },
        { type: 'separator' },
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'togglefullscreen' },
        { role: 'toggleDevTools' },
      ],
    },
    {
      label: 'Help',
      submenu: [
        {
          label: 'Open Logs',
          click: () => shell.openPath(paths.logsDir()),
        },
        {
          label: 'About NOVA PROFILE',
          click: () => {
            dialog.showMessageBox(mainWindow, {
              type: 'info',
              title: 'NOVA PROFILE',
              message: 'NOVA PROFILE',
              detail:
                `Version ${app.getVersion()}\n` +
                `Chromium core: ${path.join(paths.coreDir(), 'chrome.exe')}\n` +
                `Control plane: ${apiBase ?? 'not running'}`,
              buttons: ['Close'],
            });
          },
        },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

// ---------------------------------------------------------------------
// The channels the renderer uses.
// ---------------------------------------------------------------------

ipcMain.handle('app:info', async () => ({
  version: app.getVersion(),
  apiBase,
  dataDir: paths.dataDir(),
  logsDir: paths.logsDir(),
  coreDir: paths.coreDir(),
  coreAvailable: fs.existsSync(path.join(paths.coreDir(), 'chrome.exe')),
}));

ipcMain.handle('api:call', async (_event, { method, path: apiPath, body }) => {
  return callApi(method, apiPath, body);
});

ipcMain.handle('window:show', async () => {
  showWindow();
  return true;
});

// ---------------------------------------------------------------------
// The life of the application.
// ---------------------------------------------------------------------

app.whenReady().then(async () => {
  buildMenu();
  createWindow();

  try {
    const handshake = await startBackend();
    apiKey = handshake.apiKey;
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('backend:ready', handshake);
    }
  } catch (error) {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('backend:failed', { message: String(error?.message ?? error) });
    }
  }

  app.on('activate', () => showWindow());
});

app.on('window-all-closed', () => {
  // On Windows the shell stays in the tray; on the other platforms it
  // leaves, because a menu-bar-only application is a stranger there.
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('before-quit', async () => {
  app.isQuitting = true;
  await stopBackend();
});
