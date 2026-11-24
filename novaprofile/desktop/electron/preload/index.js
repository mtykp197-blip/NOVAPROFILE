// NOVA PROFILE -- the bridge between the window and the shell.
//
// The renderer gets a small, named surface and nothing else.  It
// cannot reach the file system, it cannot spawn a process, and it
// cannot read the control plane's credential: the shell keeps the
// credential and the renderer asks the shell to make the call.
//
// The window never loads a remote page.  It loads the control plane's
// own interface from the loopback interface, and the shell's own
// navigation policy refuses anything that is not that address.

'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('nova', {
  /**
   * What the shell knows about itself.
   *
   * The renderer draws its footer from this, so the operator always
   * knows which build they are looking at and where the data lives.
   */
  info: () => ipcRenderer.invoke('app:info'),

  /**
   * Call the control plane.
   *
   * The credential never crosses this boundary: the shell adds it.
   */
  call: (method, path, body) => ipcRenderer.invoke('api:call', { method, path, body }),

  /**
   * Bring the window back.
   */
  show: () => ipcRenderer.invoke('window:show'),

  /**
   * The shell's events.
   */
  on: (channel, handler) => {
    const allowed = [
      'backend:ready',
      'backend:failed',
      'backend:stopped',
      'menu:new-profile',
      'menu:bulk-create',
    ];
    if (!allowed.includes(channel)) return () => {};
    const listener = (_event, payload) => handler(payload);
    ipcRenderer.on(channel, listener);
    return () => ipcRenderer.removeListener(channel, listener);
  },
});
