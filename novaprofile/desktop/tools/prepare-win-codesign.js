#!/usr/bin/env node
/*
 * Windows build support for electron-builder.
 *
 * Problem
 * -------
 * electron-builder downloads the `winCodeSign` helper archive from GitHub and
 * extracts it with:
 *
 *     7za.exe x -snld -bd <archive> -o<dir>
 *
 * `-snld` makes 7-Zip materialise symbolic links that are stored inside the
 * archive. The archive ships the macOS libcrypto/libssl as symlinks, and
 * Windows can only create those with SeCreateSymbolicLinkPrivilege
 * (Developer Mode or an elevated shell). Without the privilege 7-Zip exits
 * with status 2, app-builder treats that as a fatal error and the whole
 * electron-builder run aborts -- even though the two missing files are
 * darwin-only and irrelevant for a Windows build.
 *
 * Fix
 * ---
 * We repack the archive without the symlink entries, serve it from a local
 * HTTP server (app-builder refuses file:// URLs) and point electron-builder at
 * it. The repacked archive extracts cleanly with the original `-snld` flags.
 *
 * Usage
 * -----
 *   node tools/prepare-win-codesign.js          # repack + patch
 *   node tools/prepare-win-codesign.js --serve  # repack + patch + serve
 */

'use strict';

const { execFileSync } = require('child_process');
const fs = require('fs');
const http = require('http');
const os = require('os');
const path = require('path');

const DESKTOP_ROOT = path.resolve(__dirname, '..');
const NODE_MODULES = path.join(DESKTOP_ROOT, 'node_modules');
const CACHE_ROOT = path.join(
  process.env.LOCALAPPDATA || process.env.HOME || '.',
  'electron-builder',
  'Cache',
  'winCodeSign'
);
const BIN_DIR = path.join(DESKTOP_ROOT, 'tools', 'electron-builder-binar');
const ORIGINAL_NAME = 'winCodeSign-2.6.0.7z';
const REPACKED_NAME = 'winCodeSign-2.6.0-nolinks.7z';
const SERVER_PORT = Number(process.env.NOVA_BIN_SERVER_PORT || 8731);

function findReal7za() {
  const candidates = [
    path.join(NODE_MODULES, '7zip-bin', 'win', 'x64', 'real', '7za.exe'),
    path.join(NODE_MODULES, '7zip-bin', 'win', 'x64', '7za.exe'),
  ];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }
  throw new Error('unable to locate the 7-Zip binary under node_modules/7zip-bin');
}

/**
 * Locates the most recently downloaded winCodeSign archive in the electron
 * builder cache.
 */
function findDownloadedArchive() {
  if (!fs.existsSync(CACHE_ROOT)) {
    return null;
  }
  const archives = fs
    .readdirSync(CACHE_ROOT)
    .filter((name) => name.endsWith('.7z'))
    .map((name) => path.join(CACHE_ROOT, name))
    .filter((full) => fs.statSync(full).size > 4 * 1024 * 1024)
    .sort((a, b) => fs.statSync(b).mtimeMs - fs.statSync(a).mtimeMs);

  return archives.length ? archives[0] : null;
}

/**
 * Extracts the archive and repacks it so that the darwin symlink entries are
 * stored as ordinary files.
 */
function repackWithoutSymlinks(sevenZip, sourceArchive, targetArchive) {
  const staging = path.join(os.tmpdir(), `nova-wincodesign-${process.pid}-${Date.now()}`);
  fs.mkdirSync(staging, { recursive: true });

  try {
    // Extracting without any -snl switch makes 7-Zip copy the symlink
    // payloads as ordinary files, which works on a stock Windows account.
    // The command still reports the two darwin symlinks as errors, but every
    // file we need is present, so the exit status is not fatal here.
    try {
      execFileSync(sevenZip, ['x', '-bd', '-y', `-o${staging}`, sourceArchive], {
        stdio: 'inherit',
      });
    } catch (error) {
      if (!fs.existsSync(path.join(staging, 'rcedit-x64.exe'))) {
        throw error;
      }
      console.warn('[win-codesign] extraction reported symlink errors, continuing with the extracted files');
    }

    // Repack from the staging folder. There are no symlinks left in the
    // staging folder, so the resulting archive contains plain files only.
    fs.rmSync(targetArchive, { force: true });
    execFileSync(
      sevenZip,
      ['a', '-t7z', '-mx=5', '-snl', '-bso0', '-bsp0', targetArchive, path.join(staging, '*')],
      { stdio: 'inherit' }
    );
  } finally {
    fs.rmSync(staging, { recursive: true, force: true });
  }
}

/**
 * Patches app-builder-lib so that a `getBin` call without an explicit URL
 * still honours ELECTRON_BUILDER_BINARIES_DOWNLOAD_OVERRIDE_URL. This is the
 * only hook point we have: `getSignVendorPath()` calls `getBin("winCodeSign")`
 * without a URL, so the override would otherwise never apply.
 */
function patchBinDownload() {
  const target = path.join(NODE_MODULES, 'app-builder-lib', 'out', 'binDownload.js');
  if (!fs.existsSync(target)) {
    console.warn(`[win-codesign] ${target} not found, skipping patch`);
    return false;
  }

  const original = fs.readFileSync(target, 'utf8');
  const anchor = `function doGetBin(name, url, checksum) {
    const args = ["download-artifact", "--name", name];`;
  if (!original.includes(anchor)) {
    console.warn('[win-codesign] binDownload.js already patched or has an unexpected layout');
    return false;
  }

  const patched = original.replace(
    anchor,
    `function doGetBin(name, url, checksum) {
    if (url == null) {
        const overrideBase = process.env.ELECTRON_BUILDER_BINARIES_DOWNLOAD_OVERRIDE_URL;
        if (overrideBase != null && overrideBase !== "") {
            url = overrideBase + "/" + name + "-2.6.0.7z";
        }
    }
    const args = ["download-artifact", "--name", name];`
  );

  fs.writeFileSync(target, patched);
  console.log('[win-codesign] patched app-builder-lib/out/binDownload.js');
  return true;
}

/**
 * Serves the repacked archive over http:// -- app-builder refuses file:// URLs.
 */
function startServer() {
  const root = path.resolve(BIN_DIR);
  const server = http.createServer((req, res) => {
    const relative = decodeURIComponent((req.url || '/').split('?')[0]).replace(/^\/+/, '');
    const target = path.resolve(root, relative);

    if (!target.startsWith(root) || !fs.existsSync(target)) {
      res.writeHead(404).end('not found');
      return;
    }

    const stat = fs.statSync(target);
    res.writeHead(200, {
      'Content-Type': 'application/octet-stream',
      'Content-Length': stat.size,
      'Accept-Ranges': 'bytes',
    });
    fs.createReadStream(target).pipe(res);
  });

  server.listen(SERVER_PORT, '127.0.0.1', () => {
    console.log(`[win-codesign] serving ${BIN_DIR} on http://127.0.0.1:${SERVER_PORT}`);
  });

  return server;
}

function main() {
  fs.mkdirSync(BIN_DIR, { recursive: true });

  const sevenZip = findReal7za();
  const source = findDownloadedArchive();
  if (!source) {
    throw new Error(
      `no winCodeSign archive found in ${CACHE_ROOT}; run electron-builder once to download it`
    );
  }

  const repacked = path.join(BIN_DIR, REPACKED_NAME);
  console.log(`[win-codesign] repacking ${path.basename(source)} without symlinks`);
  repackWithoutSymlinks(sevenZip, source, repacked);

  // app-builder asks for "<name>-2.6.0.7z"; expose the repacked archive under
  // that exact name.
  fs.copyFileSync(repacked, path.join(BIN_DIR, ORIGINAL_NAME));

  patchBinDownload();

  if (process.argv.includes('--serve')) {
    startServer();
  } else {
    console.log(
      '[win-codesign] done. Run with --serve (or use tools/build-desktop.js) to serve the archive.'
    );
  }
}

main();
