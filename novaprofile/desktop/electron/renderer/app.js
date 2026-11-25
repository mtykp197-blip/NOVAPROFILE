// NOVA PROFILE -- the interface.
//
// The renderer draws and it asks.  It holds no rule of its own about
// what a profile is and how two of them are told apart; every fact it
// shows comes from the control plane, through the shell.  The only
// thing it decides is what the operator is looking at.

'use strict';

/* ------------------------------------------------------------------ */
/* The shell                                                           */
/* ------------------------------------------------------------------ */

const api = window.nova;

/** The operator's current view. */
let currentView = 'profiles';

/** The profiles that are on screen. */
let profiles = [];

/** The devices in the catalogue. */
let presets = [];

/** The profiles that are ticked. */
const selection = new Set();

/** The page of the table that is on screen. */
let tablePage = 0;

/** How many rows a page of the table holds. */
const TABLE_PAGE = 100;

/** The state of the shell itself. */
const shell = {
  ready: false,
  info: null,
  error: null,
};

/* ------------------------------------------------------------------ */
/* Small helpers                                                       */
/* ------------------------------------------------------------------ */

/**
 * Build an element.
 *
 * The interface is built from data, not from strings of markup: a
 * profile's name is a value the engine chose, and a value that is put
 * into markup as a string is a value that can become code.
 */
function el(tag, attrs, children) {
  const node = document.createElement(tag);
  const attributes = attrs || {};
  const kids = flatten(children);

  for (const key of Object.keys(attributes)) {
    const value = attributes[key];
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key.startsWith('on') && typeof value === 'function') {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (value === true) node.setAttribute(key, '');
    else node.setAttribute(key, String(value));
  }

  for (const child of kids) {
    if (child === null || child === undefined || child === false) continue;
    node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
  }
  return node;
}

/**
 * The children of an element, with the lists inside it opened out.
 *
 * A list of rows is the most natural thing to hand to an element, and
 * it is most natural to build it with ``map``.  So the lists inside
 * the children are opened out, and a child that is not a node and not
 * a list is left alone -- the browser will say so itself, and it will
 * say which one it was.
 */
function flatten(children) {
  if (children === undefined || children === null) return [];

  const out = [];
  const walk = (value) => {
    if (Array.isArray(value)) {
      for (const item of value) walk(item);
      return;
    }
    out.push(value);
  };
  walk(children);

  return out;
}

/** Shorten an identifier for display, without losing its shape. */
function shortId(value, head, tail) {
  const h = head === undefined ? 10 : head;
  const t = tail === undefined ? 6 : tail;
  const text = String(value === null || value === undefined ? '' : value);
  if (text.length <= h + t + 1) return text;
  return text.slice(0, h) + '…' + text.slice(-t);
}

/** Format a number with thousands separators. */
function num(value) {
  const parsed = Number(value === null || value === undefined ? 0 : value);
  return parsed.toLocaleString('en-US');
}

/* ------------------------------------------------------------------ */
/* Toasts                                                              */
/* ------------------------------------------------------------------ */

function toast(message, kind) {
  const node = el('div', {
    class: 'toast' + (kind ? ' is-' + kind : ''),
    text: message,
  });
  document.getElementById('toasts').appendChild(node);
  const life = kind === 'bad' ? 6500 : 3800;
  setTimeout(() => {
    node.style.opacity = '0';
    node.style.transition = 'opacity 0.25s ease';
    setTimeout(() => node.remove(), 260);
  }, life);
}

/* ------------------------------------------------------------------ */
/* The modal                                                           */
/* ------------------------------------------------------------------ */

/**
 * Open the modal.
 *
 * The modal owns the operator's attention, so it takes the keyboard
 * too: Escape closes it, and the first field takes the focus.
 */
function openModal(title, body, footer, wide) {
  const backdrop = document.getElementById('modal-backdrop');
  const modal = document.getElementById('modal');

  document.getElementById('modal-title').textContent = title;
  const bodyNode = document.getElementById('modal-body');
  bodyNode.replaceChildren();
  bodyNode.appendChild(body);

  const footNode = document.getElementById('modal-foot');
  footNode.replaceChildren();
  if (footer) footNode.appendChild(footer);

  modal.classList.toggle('wide', Boolean(wide));
  backdrop.hidden = false;

  const focusable = body.querySelector('input, select, textarea, button');
  if (focusable) setTimeout(() => focusable.focus(), 30);
}

function closeModal() {
  document.getElementById('modal-backdrop').hidden = true;
}

document.getElementById('modal-close').addEventListener('click', closeModal);
document.getElementById('modal-backdrop').addEventListener('mousedown', (event) => {
  if (event.target.id === 'modal-backdrop') closeModal();
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && !document.getElementById('modal-backdrop').hidden) {
    closeModal();
  }
});

/* ------------------------------------------------------------------ */
/* Calling the control plane                                           */
/* ------------------------------------------------------------------ */

/**
 * Call the control plane and report a failure to the operator.
 *
 * A failed call that is swallowed is a control plane that has stopped
 * working and an operator who does not know.  Every failure is either
 * reported here or handled by the caller, and never both silently.
 */
async function call(method, path, body) {
  try {
    return await api.call(method, path, body);
  } catch (error) {
    const message = String((error && error.message) || error);
    if (!/failed to fetch|ECONNREFUSED/i.test(message)) {
      toast(message.replace(/^\d+:\s*/, ''), 'bad');
    }
    throw error;
  }
}

/* ------------------------------------------------------------------ */
/* The status line                                                     */
/* ------------------------------------------------------------------ */

function setStatus(kind, text) {
  const dot = document.getElementById('status-dot');
  const label = document.getElementById('status-text');
  dot.className = 'dot' + (kind ? ' is-' + kind : '');
  label.textContent = text;
}

/* ------------------------------------------------------------------ */
/* The views                                                           */
/* ------------------------------------------------------------------ */

const VIEW_META = {
  profiles: { title: 'Profile', subtitle: 'Mỗi profile là một người khác nhau.' },
  fleet: { title: 'Đội', subtitle: 'Bao nhiêu thiết bị, cách nhau bao xa.' },
  presets: { title: 'Thiết bị', subtitle: 'Danh mục mà đội profile được rút ra.' },
  proxies: { title: 'Proxy', subtitle: 'Nơi mỗi profile xuất hiện.' },
  audit: { title: 'Kiểm tra', subtitle: 'Những gì trình duyệt thực sự báo.' },
  settings: { title: 'Cài đặt', subtitle: 'Các cài đặt mà profile mới kế thừa.' },
};

/**
 * Draw the current view.
 */
async function render() {
  const meta = VIEW_META[currentView];
  document.getElementById('view-title').textContent = meta.title;
  document.getElementById('view-subtitle').textContent = meta.subtitle;

  const actions = document.getElementById('topbar-actions');
  actions.replaceChildren();

  const content = document.getElementById('content');

  if (!shell.ready) {
    content.replaceChildren(
      el('div', { class: 'loading' }, [
        el('div', { class: 'spinner' }),
        el('p', { text: shell.error ? shell.error : 'Waking the control plane…' }),
      ]),
    );
    return;
  }

  try {
    if (currentView === 'profiles') await renderProfiles(actions, content);
    else if (currentView === 'fleet') await renderFleet(actions, content);
    else if (currentView === 'presets') await renderPresets(actions, content);
    else if (currentView === 'proxies') await renderProxies(actions, content);
    else if (currentView === 'audit') await renderAudit(actions, content);
    else if (currentView === 'settings') await renderSettings(actions, content);
  } catch (error) {
    content.replaceChildren(
      el('div', { class: 'empty' }, [
        el('h3', { text: 'Có lỗi khi vẽ trang' }),
        el('p', { class: 'mono', text: String((error && error.message) || error) }),
        el('p', { class: 'hint', text: errorSource(error) }),
        el('button', {
          class: 'btn',
          text: 'Thử lại',
          onclick: () => render(),
        }),
      ]),
    );
  }
}

/**
 * The profiles that are running, as the table last knew them.
 *
 * The table keeps this so that it knows whether the control plane has
 * said anything new.
 */
let runningIds = new Set();

/**
 * The timer that asks what is running.
 */
let runningTimer = null;

/**
 * Ask what is running, and redraw the table when the answer changed.
 *
 * The question is small and the answer is smaller, so this costs
 * nothing.  The table is redrawn only when a browser has opened or
 * closed, and the drawing of a hundred rows is quick.
 */
async function pollRunning() {
  if (!shell.ready) return;

  try {
    // The question is small: the identifiers of the profiles whose
    // browser is open, and nothing else.
    const response = await call('GET', '/api/profiles/running');
    const items = (response && response.items) || [];

    const now = new Set(items.map((id) => String(id)));

    // The answer is the same as the last one: nothing to draw.
    if (now.size === runningIds.size) {
      let same = true;
      for (const id of now) {
        if (!runningIds.has(id)) {
          same = false;
          break;
        }
      }
      if (same) return;
    }

    runningIds = now;

    if (currentView === 'profiles') {
      await renderProfiles(
        document.getElementById('topbar-actions'),
        document.getElementById('content'),
      );
    }
  } catch (error) {
    // The window keeps the answer it has.
  }
}

/**
 * Start asking what is running, or stop asking.
 *
 * The asking stops when nothing is running, so a window that is only
 * looking at a fleet of closed profiles does not ask anything.
 */
function refreshRunningPoll() {
  const wanted = runningIds.size > 0;

  if (wanted && !runningTimer) {
    runningTimer = setInterval(pollRunning, 3000);
  } else if (!wanted && runningTimer) {
    clearInterval(runningTimer);
    runningTimer = null;
  }
}

/**
 * The errors that escape the drawing.
 *
 * A mistake in a click handler or in a timer is caught by the browser
 * and written to the console, where the operator never sees it.  It
 * is caught here as well and shown in the corner of the window, so
 * that a mistake in the interface is something the operator can see
 * and the operator can tell the developer about.
 */
function installErrorTrap() {
  const show = (label, error) => {
    const box = document.getElementById('error-trap');
    if (!box) return;
    box.hidden = false;
    box.replaceChildren(
      el('span', { class: 'trap-label', text: label }),
      el('span', { class: 'mono', text: String((error && error.message) || error) }),
      el('button', {
        class: 'btn btn-sm btn-ghost',
        text: 'Đóng',
        onclick: () => {
          box.hidden = true;
        },
      }),
    );
  };

  window.addEventListener('error', (event) => {
    show('Lỗi giao diện', event.error || event.message);
  });

  window.addEventListener('unhandledrejection', (event) => {
    show('Lỗi giao diện', event.reason);
  });
}

/* ------------------------------ profiles --------------------------- */

async function renderProfiles(actions, content) {
  // The table asks for the rows it draws.  The list is the five
  // columns of the table and nothing else, so the window reads a
  // handful of kilobytes instead of the whole configuration of every
  // profile in the fleet.
  const response = await call(
    'GET',
    '/api/profiles/rows?limit=' + TABLE_PAGE + '&offset=' + tablePage * TABLE_PAGE,
  );
  profiles = pickList(response, 'profiles');
  const total = (response && response.total) || profiles.length;

  // The table knows which of the profiles it shows are running, and
  // the poll starts when there is one.
  runningIds = new Set(
    profiles.filter((profile) => isRunning(profile)).map((profile) => profileId(profile)),
  );
  refreshRunningPoll();

  actions.appendChild(
    el('button', {
      class: 'btn btn-primary',
      text: 'Tạo profile',
      onclick: () => openProfileDialog(),
    }),
  );
  actions.appendChild(
    el('button', {
      class: 'btn',
      text: 'Tạo hàng loạt',
      onclick: () => openBulkDialog(),
    }),
  );

  const search = el('input', {
    class: 'search',
    type: 'text',
    placeholder: 'Tìm trong danh sách…',
    oninput: (event) => {
      tablePage = 0;
      filterProfiles(event.target.value);
    },
  });

  // The bar is filled every time the table is drawn, so it never
  // shows a count that is one behind the table.
  refreshSelectionBar();

  const body = el('div', {}, [
    el('div', { class: 'stat-grid' }, [
      statTile('Profile', num(profiles.length), 'trên máy này'),
      statTile('Đang mở', num(profiles.filter((p) => isRunning(p)).length), 'trình duyệt đang chạy'),
      statTile('Có proxy', num(profiles.filter((p) => proxyLabel(p)).length), 'dùng đường truyền riêng'),
      statTile('Nhóm', num(new Set(profiles.map((p) => p.group_id).filter(Boolean)).size), 'đã đặt tên'),
    ]),
    el('div', { class: 'card' }, [
      el('div', { class: 'card-head' }, [
        el('h3', { text: 'Danh sách profile' }),
        search,
      ]),
      el('div', { class: 'table-wrap', id: 'profile-table' }, [profileTable(profiles)]),
    ]),
    pager(total),
  ]);

  content.replaceChildren(body);
}

/**
 * The pages of the table.
 *
 * A fleet of a hundred thousand profiles is a thousand pages, and the
 * operator moves through them the way they move through any long
 * list.  The page holds a hundred rows.
 */
function pager(total) {
  const pages = Math.max(1, Math.ceil(total / TABLE_PAGE));
  const current = Math.min(tablePage, pages - 1);

  if (pages <= 1) {
    return el('div', { class: 'pager' }, [
      el('span', { class: 'pager-note', text: num(total) + ' profile' }),
    ]);
  }

  return el('div', { class: 'pager' }, [
    el('button', {
      class: 'btn btn-sm',
      text: '‹ Trước',
      disabled: current <= 0,
      onclick: () => {
        tablePage = Math.max(0, current - 1);
        render();
      },
    }),
    el('span', {
      class: 'pager-note',
      text:
        'Trang ' + num(current + 1) + ' / ' + num(pages) +
        ' · ' + num(total) + ' profile',
    }),
    el('button', {
      class: 'btn btn-sm',
      text: 'Sau ›',
      disabled: current >= pages - 1,
      onclick: () => {
        tablePage = Math.min(pages - 1, current + 1);
        render();
      },
    }),
    el('button', {
      class: 'btn btn-sm btn-ghost',
      text: 'Về đầu',
      disabled: current <= 0,
      onclick: () => {
        tablePage = 0;
        render();
      },
    }),
    el('button', {
      class: 'btn btn-sm btn-ghost',
      text: 'Về cuối',
      disabled: current >= pages - 1,
      onclick: () => {
        tablePage = pages - 1;
        render();
      },
    }),
  ]);
}

/**
 * Pull a list out of a response.
 *
 * The control plane answers with a list in some places and with a
 * wrapper in others, and the interface should not have to care which.
 */
function pickList(response, key) {
  if (Array.isArray(response)) return response;
  if (!response || typeof response !== 'object') return [];
  if (Array.isArray(response.items)) return response.items;
  if (Array.isArray(response.profiles)) return response.profiles;
  if (Array.isArray(response[key])) return response[key];
  return [];
}

function statTile(label, value, note) {
  return el('div', { class: 'stat' }, [
    el('div', { class: 'stat-label', text: label }),
    el('div', { class: 'stat-value', text: value }),
    el('div', { class: 'stat-note', text: note }),
  ]);
}

function profileTable(rows) {
  if (!rows.length) {
    return el('div', { class: 'empty' }, [
      el('h3', { text: 'Chưa có profile nào' }),
      el('p', { text: 'Tạo một cái, hoặc tạo cả trăm cái cùng lúc. Mỗi cái là một người khác nhau.' }),
      el('button', {
        class: 'btn btn-primary',
        text: 'Tạo profile',
        onclick: () => openProfileDialog(),
      }),
    ]);
  }

  const allTicked = rows.length > 0 && rows.every((profile) => selection.has(profileId(profile)));

  const head = el('thead', {}, [
    el('tr', {}, [
      el('th', { class: 'tick-col' }, [
        el('input', {
          type: 'checkbox',
          class: 'row-tick',
          checked: allTicked,
          title: allTicked ? 'Bỏ chọn tất cả' : 'Chọn tất cả',
          onchange: (event) => {
            const box = event.currentTarget;
            for (const profile of rows) {
              const id = profileId(profile);
              if (box.checked) selection.add(id);
              else selection.delete(id);
            }
            refreshSelectionBar();
            // The rows that are on screen are marked, and the list is
            // not drawn again: drawing it is what made the window slow
            // to answer.
            for (const tick of document.querySelectorAll('input.row-tick')) {
              if (tick.closest('thead')) continue;
              const row = tick.closest('tr');
              if (row) row.classList.toggle('row-ticked', box.checked);
            }
          },
        }),
      ]),
      el('th', { text: 'Profile' }),
      el('th', { text: 'Thiết bị' }),
      el('th', { text: 'Android' }),
      el('th', { text: 'Đồ hoạ' }),
      el('th', { text: 'Lõi' }),
      el('th', { text: 'RAM' }),
      el('th', { text: 'Vị trí' }),
      el('th', { text: 'Proxy' }),
      el('th', { text: 'Trạng thái' }),
      el('th', { text: '' }),
    ]),
  ]);

  const body = el(
    'tbody',
    {},
    rows.map((profile) => {
      const identity = identityOf(profile);
      const running = isRunning(profile);
      const id = profileId(profile);
      const ticked = selection.has(id);
      return el('tr', { class: ticked ? 'row-ticked' : '' }, [
        el('td', { class: 'tick-col' }, [
          el('input', {
            type: 'checkbox',
            class: 'row-tick',
            checked: ticked,
            onchange: (event) => {
              const box = event.currentTarget;
              if (box.checked) selection.add(id);
              else selection.delete(id);
              event.currentTarget.closest('tr').classList.toggle('row-ticked', box.checked);
              refreshSelectionBar();
            },
          }),
        ]),
        el('td', {}, [
          el('span', { class: 'cell-id', text: shortId(id) }),
        ]),
        el('td', { text: deviceLabel(profile) }),
        el('td', { text: androidLabel(profile) }),
        el('td', { class: 'mono small', text: gpuLabel(profile) || '—' }),
        el('td', { text: coreLabel(profile) }),
        el('td', { text: memoryLabel(profile) }),
        el('td', { text: locationLabel(profile) }),
        el('td', { text: proxyLabel(profile) || '—' }),
        el('td', {}, [
          el('span', {
            class: 'pill ' + (running ? 'pill-run' : 'pill'),
            text: statusLabel(identity.status, running),
          }),
        ]),
        el('td', {}, [
          el('div', { class: 'cell-actions' }, [
            el('button', {
              class: 'btn btn-sm',
              text: running ? 'Dừng' : 'Mở',
              onclick: (event) => {
                event.stopPropagation();
                toggleLaunch(profile, running);
              },
            }),
            el('button', {
              class: 'btn btn-sm',
              text: 'Proxy',
              onclick: (event) => {
                event.stopPropagation();
                openSingleProxyDialog(profile);
              },
            }),
            el('button', {
              class: 'btn btn-sm',
              text: 'Chi tiết',
              onclick: (event) => {
                event.stopPropagation();
                openProfileDetails(profile);
              },
            }),
            el('button', {
              class: 'btn btn-sm btn-danger',
              text: 'Xoá',
              onclick: (event) => {
                event.stopPropagation();
                deleteProfile(profile);
              },
            }),
          ]),
        ]),
      ]);
    }),
  );

  return el('table', {}, [head, body]);
}

/**
 * The status of a profile, said in Vietnamese.
 *
 * The control plane has its own words for the states a profile is in,
 * and the interface says them the way the operator reads them.
 */
function statusLabel(status, running) {
  if (running) return 'Đang mở';
  switch (String(status || '').toLowerCase()) {
    case 'running':
    case 'launching':
    case 'starting':
      return 'Đang mở';
    case 'stopped':
    case 'closed':
    case 'stopping':
      return 'Đã dừng';
    case 'error':
    case 'failed':
      return 'Lỗi';
    case 'starting':
      return 'Đang khởi động';
    case 'idle':
    case 'ready':
      return 'Chưa mở';
    default:
      return 'Chưa mở';
  }
}

/**
 * Read a proxy out of one line of text.
 *
 * The line is the way every proxy list is written: the address, the
 * port, and -- when the proxy needs them -- the name and the
 * password.  The four parts are separated by colons, and the password
 * may itself hold colons, so a line of four parts is read from the
 * right.
 *
 * The shapes that are understood:
 *
 *     ``1.2.3.4:8080``
 *     ``1.2.3.4:8080:user``
 *     ``1.2.3.4:8080:user:pass``
 *     ``user:pass@1.2.3.4:8080``
 *     ``socks5://1.2.3.4:1080:user:pass``
 *
 * A line that is none of those is not a proxy, and the operator is
 * told so rather than being given a proxy that reaches nowhere.
 */
function parseProxyLine(line) {
  const raw = String(line || '').trim();
  if (!raw) return null;

  let protocol = 'http';
  let rest = raw;

  // A scheme at the front names the protocol.
  const scheme = /^([a-z][a-z0-9+.-]*):\/\//i.exec(rest);
  if (scheme) {
    const name = scheme[1].toLowerCase();
    if (['http', 'https', 'socks5', 'socks4'].indexOf(name) !== -1) {
      protocol = name;
    }
    rest = rest.slice(scheme[0].length);
  }

  // The shape ``user:pass@host:port``.
  const at = rest.lastIndexOf('@');
  if (at !== -1) {
    const credentials = rest.slice(0, at);
    rest = rest.slice(at + 1);
    const colon = credentials.indexOf(':');
    const username = colon === -1 ? credentials : credentials.slice(0, colon);
    const password = colon === -1 ? '' : credentials.slice(colon + 1);
    const parts = rest.split(':');
    if (parts.length < 2) return null;
    const port = Number(parts[parts.length - 1]);
    if (!Number.isInteger(port) || port < 1 || port > 65535) return null;
    return {
      protocol: protocol,
      host: parts.slice(0, parts.length - 1).join(':'),
      port: port,
      username: username,
      password: password,
    };
  }

  const parts = rest.split(':');
  if (parts.length < 2 || parts.length > 4) return null;

  const port = Number(parts[1]);
  if (!Number.isInteger(port) || port < 1 || port > 65535) return null;

  const host = parts[0];
  if (!host) return null;

  let username = '';
  let password = '';
  if (parts.length === 3) {
    username = parts[2];
  } else if (parts.length === 4) {
    username = parts[2];
    // The password may hold a colon of its own, and it is the last
    // part of the line, so everything after the third colon is the
    // password.
    password = parts.slice(3).join(':');
  }

  return {
    protocol: protocol,
    host: host,
    port: port,
    username: username,
    password: password,
  };
}

/**
 * Read a proxy out of one line of text.
 *
 * The line is the way every proxy list is written: the address, the
 * port, and -- when the proxy needs them -- the name and the
 * password.  The four parts are separated by colons, and the password
 * may itself hold colons, so a line of four parts is read from the
 * right.
 *
 * The shapes that are understood:
 *
 *     ``1.2.3.4:8080``
 *     ``1.2.3.4:8080:user``
 *     ``1.2.3.4:8080:user:pass``
 *     ``user:pass@1.2.3.4:8080``
 *     ``socks5://1.2.3.4:1080:user:pass``
 *
 * A line that is none of those is not a proxy, and the operator is
 * told so rather than being given a proxy that reaches nowhere.
 */
function parseProxyLine(line) {
  const raw = String(line || '').trim();
  if (!raw) return null;

  let protocol = 'http';
  let rest = raw;

  // A scheme at the front names the protocol.
  const scheme = /^([a-z][a-z0-9+.-]*):\/\//i.exec(rest);
  if (scheme) {
    const name = scheme[1].toLowerCase();
    if (['http', 'https', 'socks5', 'socks4'].indexOf(name) !== -1) {
      protocol = name;
    }
    rest = rest.slice(scheme[0].length);
  }

  // The shape ``user:pass@host:port``.
  const at = rest.lastIndexOf('@');
  if (at !== -1) {
    const credentials = rest.slice(0, at);
    rest = rest.slice(at + 1);
    const colon = credentials.indexOf(':');
    const username = colon === -1 ? credentials : credentials.slice(0, colon);
    const password = colon === -1 ? '' : credentials.slice(colon + 1);
    const parts = rest.split(':');
    if (parts.length < 2) return null;
    const port = Number(parts[parts.length - 1]);
    if (!Number.isInteger(port) || port < 1 || port > 65535) return null;
    return {
      protocol: protocol,
      host: parts.slice(0, parts.length - 1).join(':'),
      port: port,
      username: username,
      password: password,
    };
  }

  const parts = rest.split(':');
  if (parts.length < 2) return null;

  const port = Number(parts[1]);
  if (!Number.isInteger(port) || port < 1 || port > 65535) return null;

  const host = parts[0];
  if (!host) return null;

  let username = '';
  let password = '';
  if (parts.length === 3) {
    username = parts[2];
  } else if (parts.length >= 4) {
    username = parts[2];
    // The password may hold a colon of its own, and it is the last
    // part of the line, so everything after the third colon is the
    // password.
    password = parts.slice(3).join(':');
  }

  return {
    protocol: protocol,
    host: host,
    port: port,
    username: username,
    password: password,
  };
}

/**
 * The profiles that are ticked.
 *
 * The set holds the identifiers, so a profile that is ticked is still
 * ticked after the table has been rebuilt.
 */
function selectedProfiles() {
  return profiles.filter((profile) => selection.has(profileId(profile)));
}

function clearSelection() {
  selection.clear();
  refreshSelectionBar();
}

/**
 * The bar that shows what is ticked and what can be done with it.
 *
 * The bar is rebuilt from the selection every time it changes, so it
 * can never show a count that is one behind the table.
 */
function refreshSelectionBar() {
  const bar = document.getElementById('selection-bar');
  if (!bar) return;

  const chosen = selectedProfiles();
  const count = chosen.length;

  bar.replaceChildren(
    el('div', { class: 'sel-count' }, [
      el('strong', { text: num(count) }),
      el('span', { text: count === 1 ? 'profile đã chọn' : 'profile đã chọn' }),
    ]),
    el('div', { class: 'sel-actions' }, [
      el('button', {
        class: 'btn btn-sm btn-primary',
        text: 'Mở',
        disabled: count === 0,
        onclick: () => openSelected(),
      }),
      el('button', {
        class: 'btn btn-sm',
        text: 'Dừng',
        disabled: count === 0,
        onclick: () => stopSelected(),
      }),
      el('button', {
        class: 'btn btn-sm',
        text: 'Gán proxy',
        disabled: count === 0,
        onclick: () => openBulkProxyDialog(),
      }),
      el('button', {
        class: 'btn btn-sm',
        text: 'Phân tích',
        disabled: count === 0,
        onclick: () => openAnalysisDialog(),
      }),
      el('button', {
        class: 'btn btn-sm btn-danger',
        text: 'Xoá',
        disabled: count === 0,
        onclick: () => deleteSelected(),
      }),
      el('button', {
        class: 'btn btn-sm btn-ghost',
        text: 'Bỏ chọn',
        disabled: count === 0,
        onclick: clearSelection,
      }),
    ]),
  );

  bar.classList.toggle('visible', count > 0);
  bar.hidden = count === 0;
}

/**
 * Open every ticked profile.
 */
async function openSelected() {
  const chosen = selectedProfiles();
  if (!chosen.length) return;

  const failures = [];
  let opened = 0;

  // The calls go out together rather than one after the other.  The
  // work the control plane does is the same either way, but the
  // operator waits for one round trip instead of a hundred.
  const answers = await Promise.allSettled(
    chosen.map((profile) =>
      call('POST', '/api/profiles/' + profileId(profile) + '/launch', {}),
    ),
  );

  answers.forEach((answer, index) => {
    if (answer.status === 'fulfilled') {
      opened += 1;
    } else {
      const error = answer.reason;
      failures.push(deviceLabel(chosen[index]) + ': ' + String(error.message || error));
    }
  });

  toast(
    'Đã mở ' + num(opened) + ' trên ' + num(chosen.length) + ' profile.',
    failures.length ? 'warn' : 'ok',
  );
  if (failures.length) toast(failures[0], 'bad');
  await render();
}

/**
 * Stop every ticked profile.
 */
async function stopSelected() {
  const chosen = selectedProfiles();
  if (!chosen.length) return;

  const failures = [];
  let stopped = 0;

  const answers = await Promise.allSettled(
    chosen.map((profile) =>
      call('POST', '/api/profiles/' + profileId(profile) + '/stop', {}),
    ),
  );

  answers.forEach((answer, index) => {
    if (answer.status === 'fulfilled') {
      stopped += 1;
    } else {
      const error = answer.reason;
      failures.push(deviceLabel(chosen[index]) + ': ' + String(error.message || error));
    }
  });

  toast(
    'Đã dừng ' + num(stopped) + ' trên ' + num(chosen.length) + ' profile.',
    failures.length ? 'warn' : 'ok',
  );
  if (failures.length) toast(failures[0], 'bad');
  await render();
}

/**
 * Delete every ticked profile.
 */
async function deleteSelected() {
  const chosen = selectedProfiles();
  if (!chosen.length) return;

  const confirmed = window.confirm(
    'Xoá ' +
      num(chosen.length) +
      ' profile?\n\nThư mục profile cũng bị xoá theo. Không thể hoàn tác.',
  );
  if (!confirmed) return;

  const failures = [];
  let deleted = 0;

  const answers = await Promise.allSettled(
    chosen.map((profile) => call('DELETE', '/api/profiles/' + profileId(profile))),
  );

  answers.forEach((answer, index) => {
    if (answer.status === 'fulfilled') {
      selection.delete(profileId(chosen[index]));
      deleted += 1;
    } else {
      const error = answer.reason;
      failures.push(deviceLabel(chosen[index]) + ': ' + String(error.message || error));
    }
  });

  toast(
    'Đã xoá ' + num(deleted) + ' trên ' + num(chosen.length) + ' profile.',
    failures.length ? 'warn' : 'ok',
  );
  if (failures.length) toast(failures[0], 'bad');
  await render();
}

/**
 * Look at the group that is ticked and say what it holds.
 *
 * The question the analysis answers is the one that matters: are these
 * profiles a group of different people, or are they one person with
 * several browsers?  The answer is the number of different devices,
 * GPUs, addresses and Android releases in the group.
 */
function openAnalysisDialog() {
  const chosen = selectedProfiles();
  if (!chosen.length) return;

  const devices = new Set(chosen.map((p) => deviceLabel(p))).size;
  const gpus = new Set(
    chosen.map((p) => (configOf(p).webgl || {}).unmasked_renderer || ''),
  ).size;
  const proxies = new Set(chosen.map((p) => proxyLabel(p))).size;
  const androids = new Set(chosen.map((p) => androidLabel(p))).size;
  const running = chosen.filter((p) => isRunning(p)).length;

  const body = el('div', {}, [
    el('div', { class: 'analysis-grid' }, [
      statTile('Profile', num(chosen.length), 'đã chọn'),
      statTile('Đang mở', num(running), 'trình duyệt đang chạy'),
      statTile('Thiết bị', num(devices), 'khác nhau'),
      statTile('Đồ hoạ', num(gpus), 'khác nhau'),
      statTile('Android', num(androids), 'khác nhau'),
      statTile('Proxy', num(proxies), 'đường truyền riêng'),
    ]),
    el('p', { class: 'hint' }, [
      el('span', {
        text:
          'Mỗi profile trong nhóm là một thiết bị khác, ở một địa chỉ khác. ' +
          'Không có hai cái nào bị gom lại thành một người.',
      }),
    ]),
    el('h4', { class: 'modal-sub', text: 'Thứ tự các profile' }),
    el('div', { class: 'bulk-order' }, [
      chosen.map((profile, index) =>
        el('div', { class: 'bulk-order-row' }, [
          el('span', { class: 'idx', text: String(index + 1) }),
          el('b', { text: deviceLabel(profile) }),
          el('span', { class: 'mono', text: androidLabel(profile) }),
          el('span', { class: 'mono dim', text: proxyLabel(profile) || 'chưa có proxy' }),
        ]),
      ),
    ]),
  ]);

  openModal(
    'Phân tích ' + num(chosen.length) + ' profile đã chọn',
    body,
    el('div', { class: 'modal-actions' }, [
      el('button', { class: 'btn', text: 'Đóng', onclick: closeModal }),
    ]),
    true,
  );
}

/**
 * Where an error came from.
 *
 * The control plane answers over the network and the page draws what
 * it answers, so an error that happened while drawing is usually a
 * mistake in the page rather than a problem with the service.  The
 * operator is told which of the two it was.
 */
function errorSource(error) {
  const message = String((error && error.message) || error || '');
  if (
    /is not defined|is not a function|Cannot read|undefined/i.test(message)
  ) {
    return 'Lỗi nằm trong giao diện, không phải ở bộ điều khiển.';
  }
  if (/ECONNREFUSED|fetch failed|Network Error/i.test(message)) {
    return 'Bộ điều khiển không trả lời.';
  }
  return 'Xem chi tiết bên dưới.';
}

/**
 * Whether a profile's browser is running.
 *
 * The status lives inside the profile's identity, and a profile that
 * was fetched from a list carries it there and nowhere else.
 */
function isRunning(profile) {
  const identity = identityOf(profile);
  return identity.status === 'running' || profile.running === true;
}

function gpuLabel(profile) {
  const webgl = configOf(profile).webgl || configOf(profile).graphics || {};
  return webgl.unmasked_renderer || profile.gpu || '';
}

/**
 * The number of cores the handset reports.
 */
function coreLabel(profile) {
  const nav = configOf(profile).navigator || {};
  const cores = nav.hardware_concurrency;
  return cores ? String(cores) : '—';
}

/**
 * The memory the platform reports.
 *
 * It is the memory the platform rounds to, so it is one of one, two,
 * four and eight gigabytes, and never the six or the twelve that a
 * page can tell are made up.
 */
function memoryLabel(profile) {
  const nav = configOf(profile).navigator || {};
  const memory = nav.device_memory;
  if (!memory) return '—';
  return memory + ' GB';
}

function configOf(profile) {
  // The control plane answers with the profile's own configuration at
  // the top level, and the identity -- the name, the status, the
  // timestamps -- in a record of its own.  A profile that has not been
  // fetched in full carries neither, so both are read defensively.
  return profile && profile.config ? profile.config : profile || {};
}

function identityOf(profile) {
  if (!profile) return {};
  if (profile.identity) return profile.identity;
  // The light row carries the identity's own fields.
  return {
    id: profile.id || '',
    name: profile.name || '',
    status: profile.status || 'created',
    group_id: profile.group_id || '',
  };
}

function profileId(profile) {
  const identity = identityOf(profile);
  return identity.id || profile.profile_id || profile.id || '';
}

function deviceLabel(profile) {
  const build =
    configOf(profile).android_build ||
    configOf(profile).build ||
    configOf(profile).device ||
    {};
  return [build.manufacturer, build.model].filter(Boolean).join(' ') || '—';
}

function androidLabel(profile) {
  const build =
    configOf(profile).android_build ||
    configOf(profile).build ||
    configOf(profile).device ||
    {};
  return build.version_release ? 'Android ' + build.version_release : '—';
}

function locationLabel(profile) {
  const geo = configOf(profile).geolocation || configOf(profile).place || {};
  if (geo.city) return geo.city;
  if (geo.latitude !== undefined && geo.longitude !== undefined) {
    return Number(geo.latitude).toFixed(2) + ', ' + Number(geo.longitude).toFixed(2);
  }
  return '—';
}

function timeZoneLabel(profile) {
  const zone = configOf(profile).timezone || {};
  if (typeof zone === 'string') return zone;
  return zone.iana_name || zone.abbreviation || '—';
}

/**
 * Give one profile its own proxy.
 *
 * The proxy is one line of text: ``ip:port:user:pass``.
 *
 * The line is the form every proxy list in the world is written in,
 * and it is the form the operator already has in hand: the seller of
 * the proxies sent a list, and the operator pastes it.  A form with
 * a field for the address, a field for the port, a field for the
 * user and a field for the password asks the operator to take a line
 * apart and put it back together, and every one of those four steps
 * is a chance to type it wrong.
 */
function openSingleProxyDialog(profile) {
  const id = profileId(profile);
  const current = configOf(profile).proxy || {};

  const field = el('input', {
    type: 'text',
    id: 'proxy-line',
    class: 'proxy-line',
    spellcheck: 'false',
    placeholder: 'ip:port:user:pass',
    value: current.enabled
      ? [
          current.host || '',
          current.port || '',
          current.username || '',
          current.password || '',
        ]
          .filter((part) => part !== '' && part !== undefined)
          .join(':')
      : '',
  });

  const body = el('div', {}, [
    el('label', { class: 'field' }, [
      el('span', { text: 'Proxy' }),
      field,
    ]),
    el('p', { class: 'hint' }, [
      el('span', {
        text:
          'Định dạng: ip:port:user:pass. Bỏ trống để tắt proxy của profile này.',
      }),
    ]),
  ]);

  openModal(
    'Proxy cho ' + deviceLabel(profile),
    body,
    el('div', { class: 'modal-actions' }, [
      el('button', { class: 'btn', text: 'Huỷ', onclick: closeModal }),
      el('button', {
        class: 'btn btn-primary',
        text: 'Lưu',
        onclick: async (event) => {
          const button = event.currentTarget;
          button.disabled = true;
          try {
            const line = field.value.trim();
            if (!line) {
              await call('PATCH', '/api/profiles/' + id, {
                proxy: { enabled: false },
              });
              toast('Đã tắt proxy.', 'ok');
              closeModal();
              await render();
              return;
            }
            const parsed = parseProxyLine(line);
            if (!parsed) {
              throw new Error(
                'Dòng proxy không đúng. Cần có dạng ip:port, ip:port:user hoặc ip:port:user:pass.',
              );
            }
            await call('PATCH', '/api/profiles/' + id, {
              proxy: {
                enabled: true,
                protocol: parsed.protocol,
                host: parsed.host,
                port: parsed.port,
                username: parsed.username,
                password: parsed.password,
                auth_method: parsed.username ? 'userpass' : 'none',
              },
            });
            toast('Đã lưu proxy.', 'ok');
            closeModal();
            await render();
          } catch (error) {
            toast(String((error && error.message) || error), 'bad');
            button.disabled = false;
          }
        },
      }),
    ]),
    true,
  );
}

/**
 * Assign a proxy to every ticked profile.
 *
 * The operator has a list of proxy lines in hand -- the seller sent a
 * list, and the operator pastes it.  The dialog asks for that list
 * and nothing else: one line of text, one proxy per line.
 *
 * The lines go to the ticked profiles in the order the table shows
 * them.  The first line goes to the first profile, the second line to
 * the second profile, and so on.  A profile that has no line keeps the
 * proxy it already has; the operator did not say to clear those, so
 * they are not cleared.
 *
 * The order is shown beside the box, so the operator can see which
 * address is going where before they press the button.
 */
function openBulkProxyDialog() {
  const chosen = selectedProfiles();
  if (!chosen.length) return;

  const list = el('textarea', {
    id: 'proxy-lines',
    class: 'proxy-lines',
    spellcheck: 'false',
    rows: '14',
    placeholder: '1.2.3.4:8080:user:pass\n5.6.7.8:9090:user:pass',
  });

  const order = el('div', { class: 'bulk-order' }, [
    chosen.map((profile, index) =>
      el('div', { class: 'bulk-order-row' }, [
        el('span', { class: 'idx', text: String(index + 1) }),
        el('b', { text: deviceLabel(profile) }),
        el('span', { class: 'mono', text: androidLabel(profile) }),
        el('span', {
          class: 'mono dim',
          text: proxyLabel(profile) || 'chưa có proxy',
        }),
      ]),
    ),
  ]);

  const body = el('div', {}, [
    el('p', { class: 'hint' }, [
      el('span', {
        text:
          'Dán danh sách proxy vào đây, mỗi dòng một proxy. Định dạng: ip:port:user:pass. ' +
          'Dòng thứ nhất sẽ gán cho profile thứ nhất, dòng thứ hai cho profile thứ hai, v.v.',
      }),
    ]),
    list,
    el('h4', { class: 'modal-sub', text: 'Thứ tự nhận proxy' }),
    order,
  ]);

  openModal(
    'Gán proxy cho ' + num(chosen.length) + ' profile',
    body,
    el('div', { class: 'modal-actions' }, [
      el('button', { class: 'btn', text: 'Huỷ', onclick: closeModal }),
      el('button', {
        class: 'btn btn-primary',
        text: 'Gán',
        onclick: async (event) => {
          const button = event.currentTarget;
          button.disabled = true;
          try {
            const lines = list.value
              .split(/\r?\n/)
              .map((line) => line.trim())
              .filter((line) => line !== '');

            if (!lines.length) {
              throw new Error('Dán ít nhất một dòng proxy.');
            }

            let assigned = 0;
            const failures = [];

            const work = [];
            for (let index = 0; index < chosen.length && index < lines.length; index += 1) {
              const parsed = parseProxyLine(lines[index]);
              if (!parsed) {
                failures.push(
                  'Dòng ' + (index + 1) + ': không đúng định dạng (cần ip:port:user:pass)',
                );
                continue;
              }
              work.push({ index: index, parsed: parsed });
            }

            // The calls go out together, so a list of two hundred
            // proxies is one wait and not two hundred.
            const answers = await Promise.allSettled(
              work.map((item) =>
                call('PATCH', '/api/profiles/' + profileId(chosen[item.index]), {
                  proxy: {
                    enabled: true,
                    protocol: item.parsed.protocol,
                    host: item.parsed.host,
                    port: item.parsed.port,
                    username: item.parsed.username,
                    password: item.parsed.password,
                    auth_method: item.parsed.username ? 'userpass' : 'none',
                  },
                }),
              ),
            );

            answers.forEach((answer, at) => {
              if (answer.status === 'fulfilled') {
                assigned += 1;
              } else {
                const error = answer.reason;
                failures.push(
                  deviceLabel(chosen[work[at].index]) + ': ' + String(error.message || error),
                );
              }
            });

            if (assigned) {
              toast(
                'Đã gán proxy cho ' + num(assigned) + ' trên ' + num(chosen.length) + ' profile.',
                failures.length ? 'warn' : 'ok',
              );
            } else {
              toast('Không gán được proxy nào.', 'bad');
            }
            if (failures.length) toast(failures[0], 'bad');
            closeModal();
            await render();
          } catch (error) {
            toast(String((error && error.message) || error), 'bad');
            button.disabled = false;
          }
        },
      }),
    ]),
    true,
  );
}

/**
 * The proxy of a profile, as one line.
 *
 * The proxy is a record -- the host, the port, the credentials and the
 * way they are sent -- and it is either on or off.  A profile that has
 * a record but is not enabled is a profile with no proxy.
 */
function proxyLabel(profile) {
  // The light row carries the proxy as the text that is shown.
  if (profile && typeof profile.proxy === 'string' && profile.proxy) {
    return profile.proxy;
  }
  const proxy = configOf(profile).proxy;
  if (!proxy || typeof proxy !== 'object') return proxy ? String(proxy) : '';
  if (!proxy.enabled) return '';
  const host = proxy.host || '';
  if (!host) return proxy.protocol || 'proxy';
  const port = proxy.port ? ':' + proxy.port : '';
  return proxy.protocol + '://' + host + port;
}

/**
 * Search the fleet.
 *
 * The search is asked of the control plane, which looks at the whole
 * fleet and not only at the page that happens to be on screen.  The
 * answer comes back a moment later, and the table is drawn with it.
 */
let searchTimer = null;

function filterProfiles(query) {
  const needle = String(query || '').trim();
  const table = document.getElementById('profile-table');
  if (!table) return;

  if (!needle) {
    table.replaceChildren(profileTable(profiles));
    return;
  }

  // The keystrokes are gathered before the fleet is asked, so a word
  // that is being typed is one question and not eight.
  if (searchTimer) clearTimeout(searchTimer);
  searchTimer = setTimeout(async () => {
    try {
      const response = await call(
        'GET',
        '/api/profiles/rows?limit=' +
          TABLE_PAGE +
          '&offset=0&search=' +
          encodeURIComponent(needle),
      );
      const matches = pickList(response, 'profiles');
      const current = document.getElementById('profile-table');
      if (current) current.replaceChildren(profileTable(matches));
    } catch (error) {
      // The list that is on screen is still the right one to show.
    }
  }, 220);
}

async function toggleLaunch(profile, running) {
  const id = profileId(profile);
  try {
    if (running) {
      await call('POST', '/api/profiles/' + id + '/stop');
      toast('Đã dừng.', 'ok');
    } else {
      await call('POST', '/api/profiles/' + id + '/launch');
      toast('Đã mở.', 'ok');
    }
    await render();
  } catch (error) {
    /* the toast has already been shown */
  }
}

async function deleteProfile(profile) {
  const id = profileId(profile);
  const confirmed = window.confirm(
    'Xoá profile này?\n\nThư mục profile cũng bị xoá theo. Không thể hoàn tác.',
  );
  if (!confirmed) return;
  try {
    await call('DELETE', '/api/profiles/' + id);
    toast('Đã xoá.', 'ok');
    await render();
  } catch (error) {
    /* the toast has already been shown */
  }
}

async function openProfileDetails(profile) {
  const id = profileId(profile);
  const body = el('div', { class: 'loading' }, [el('div', { class: 'spinner' })]);
  openModal('Chi tiết profile', body);
  try {
    const detail = await call('GET', '/api/profiles/' + id);
    const config = detail && detail.config ? detail.config : detail || {};
    const build = config.android_build || config.build || {};
    const nav = config.navigator || {};
    const screen = config.screen || {};
    const geo = config.geolocation || {};
    const identity = (detail && detail.identity) || identityOf(profile);

    const rows = [
      ['Profile id', id],
      ['Name', identity.name],
      ['Thiết bị', [build.manufacturer, build.model].filter(Boolean).join(' ')],
      ['Android', build.version_release],
      ['Build id', build.build_id],
      ['Fingerprint', build.fingerprint],
      ['Serial', build.serial],
      ['Lõi CPU', nav.hardware_concurrency],
      ['Bộ nhớ', nav.device_memory ? nav.device_memory + ' GB' : null],
      ['Touch points', nav.max_touch_points],
      ['Screen', screen.width ? screen.width + '×' + screen.height + ' @' + screen.device_pixel_ratio : null],
      ['GPU', config.webgl ? config.webgl.unmasked_renderer : null],
      ['Múi giờ', timeZoneLabel(detail)],
      ['Language', nav.language],
      ['City', geo.city],
      ['User agent', nav.user_agent],
    ].filter((entry) => entry[1] !== undefined && entry[1] !== null && entry[1] !== '');

    const list = el('dl', { class: 'kv' });
    for (const entry of rows) {
      list.appendChild(el('dt', { text: entry[0] }));
      list.appendChild(el('dd', { text: String(entry[1]) }));
    }
    body.replaceChildren(list);
  } catch (error) {
    body.replaceChildren(
      el('div', { class: 'empty' }, [
        el('h3', { text: 'Không đọc được profile' }),
        el('p', { text: String((error && error.message) || error) }),
      ]),
    );
  }
}

/* --------------------------- the new profile ----------------------- */

async function loadPresets() {
  if (presets.length) return;
  try {
    const response = await call('GET', '/api/presets');
    presets = pickList(response, 'presets');
  } catch (error) {
    presets = [];
  }
}

function presetOptions() {
  return presets.map((preset) =>
    el('option', {
      value: preset.preset_id || preset.id,
      text:
        [preset.manufacturer, preset.model].filter(Boolean).join(' ') ||
        preset.preset_id ||
        preset.id,
    }),
  );
}

async function openProfileDialog() {
  await loadPresets();

  const select = el('select', { id: 'profile-preset' }, presetOptions());
  const group = el('input', { type: 'text', id: 'profile-group', placeholder: 'Không bắt buộc' });
  const notes = el('input', { type: 'text', id: 'profile-notes', placeholder: 'Không bắt buộc' });
  const proxy = el('input', { type: 'text', id: 'profile-proxy', placeholder: 'ip:port:user:pass' });

  const body = el('div', {}, [
    el('div', { class: 'field' }, [
      el('label', { for: 'profile-preset', text: 'Thiết bị' }),
      select,
      el('div', {
        class: 'hint',
        text: 'Nhóm thiết bị mà profile được rút ra. Giới hạn phần cứng thật được giữ nguyên.',
      }),
    ]),
    el('div', { class: 'form-row' }, [
      el('div', { class: 'field' }, [
        el('label', { for: 'profile-group', text: 'Nhóm' }),
        group,
      ]),
      el('div', { class: 'field' }, [
        el('label', { for: 'profile-notes', text: 'Ghi chú' }),
        notes,
      ]),
    ]),
    el('div', { class: 'field' }, [
      el('label', { for: 'profile-proxy', text: 'Proxy' }),
      proxy,
      el('div', {
        class: 'hint',
        text: 'Một profile, một proxy. Một profile đổi proxy là hai người với web.',
      }),
    ]),
  ]);

  const footer = el('div', { class: 'toolbar' }, [
    el('button', {
      class: 'btn btn-primary',
      text: 'Tạo',
      onclick: async (event) => {
        event.target.disabled = true;
        const payload = { preset_id: select.value };
        if (group.value.trim()) payload.group_id = group.value.trim();
        if (notes.value.trim()) payload.notes = notes.value.trim();
        if (proxy.value.trim()) payload.proxy = proxy.value.trim();
        try {
          await call('POST', '/api/profiles', payload);
          closeModal();
          toast('Đã tạo profile.', 'ok');
          await render();
        } catch (error) {
          event.target.disabled = false;
          toast(String((error && error.message) || error), 'bad');
        }
      },
    }),
    el('button', { class: 'btn', text: 'Huỷ', onclick: closeModal }),
  ]);

  openModal('Tạo profile', body, footer);
}

/* ------------------------------ bulk ------------------------------- */

async function openBulkDialog() {
  await loadPresets();

  const count = el('input', {
    type: 'number',
    id: 'bulk-count',
    value: '10',
    min: '1',
    max: '100000',
  });
  const select = el('select', { id: 'bulk-preset' }, presetOptions());
  const group = el('input', { type: 'text', id: 'bulk-group', placeholder: 'Không bắt buộc' });
  const spread = el('input', { type: 'checkbox', id: 'bulk-spread', checked: true });

  const body = el('div', {}, [
    el('div', { class: 'field' }, [
      el('label', { for: 'bulk-count', text: 'Số lượng' }),
      count,
      el('div', {
        class: 'hint',
        text:
          'Each profile is a separate machine of its own: its own serial, its own fingerprint, its own readings.',
      }),
    ]),
    el('div', { class: 'field' }, [
      el('label', { for: 'bulk-preset', text: 'Nhóm thiết bị' }),
      select,
    ]),
    el('div', { class: 'field' }, [
      el('label', { for: 'bulk-group', text: 'Nhóm' }),
      group,
    ]),
    el('label', { class: 'check' }, [
      spread,
      el('span', { text: 'Trải rộng theo danh mục (mỗi profile một nhóm thiết bị riêng)' }),
    ]),
  ]);

  const footer = el('div', { class: 'toolbar' }, [
    el('button', {
      class: 'btn btn-primary',
      text: 'Tạo',
      onclick: async (event) => {
        event.target.disabled = true;
        const total = Math.max(1, Math.min(100000, Number(count.value) || 1));
        const payload = {
          count: total,
          // The device family is only named when the operator asked
          // for one family.  With the spread ticked the fleet draws
          // every profile's own machine out of the whole catalogue,
          // and naming a family would put them all in one bucket.
          preset_id: spread.checked ? '' : select.value,
          spread_across_catalogue: spread.checked,
        };
        if (group.value.trim()) payload.group_id = group.value.trim();
        try {
          await call('POST', '/api/batch/create', payload);
          closeModal();
          toast(total + ' profiles created.', 'ok');
          await render();
        } catch (error) {
          event.target.disabled = false;
          toast(String((error && error.message) || error), 'bad');
        }
      },
    }),
    el('button', { class: 'btn', text: 'Huỷ', onclick: closeModal }),
  ]);

  openModal('Tạo hàng loạt', body, footer, true);
}

/* ------------------------------ fleet ------------------------------ */

async function renderFleet(actions, content) {
  const response = await call('GET', '/api/profiles?limit=100000');
  const all = pickList(response, 'profiles');

  const devices = new Set();
  const gpus = new Set();
  const cities = new Set();
  const zones = new Set();
  const releases = new Set();
  const cores = new Set();
  const memory = new Set();
  const touch = new Set();

  for (const profile of all) {
    const config = configOf(profile);
    const build = config.android_build || config.build || {};
    if (build.model) devices.add(build.model);
    if (config.webgl && config.webgl.unmasked_renderer) gpus.add(config.webgl.unmasked_renderer);
    if (config.geolocation && config.geolocation.city) cities.add(config.geolocation.city);
    const zone = timeZoneLabel(profile);
    if (zone && zone !== '—') zones.add(zone);
    if (build.version_release) releases.add(build.version_release);
    const nav = config.navigator || {};
    if (nav.hardware_concurrency) cores.add(nav.hardware_concurrency);
    if (nav.device_memory) memory.add(nav.device_memory);
    if (nav.max_touch_points) touch.add(nav.max_touch_points);
  }

  content.replaceChildren(
    el('div', {}, [
      el('div', { class: 'stat-grid' }, [
        statTile('Profile', num(all.length), 'in the fleet'),
        statTile('Device families', num(devices.size), 'distinct models'),
        statTile('Graphics parts', num(gpus.size), 'distinct renderers'),
        statTile('Cities', num(cities.size), 'across Vietnam'),
        statTile('Time zones', num(zones.size), 'and their kin'),
        statTile('Android releases', num(releases.size), 'in use'),
      ]),
      el('div', { class: 'card' }, [
        el('div', { class: 'card-head' }, [el('h3', { text: 'Độ phân tán' })]),
        el('div', { class: 'card-body' }, [
          el('div', { class: 'form-row' }, [
            spreadColumn('Core counts', Array.from(cores).sort((a, b) => a - b)),
            spreadColumn('Memory (GB)', Array.from(memory).sort((a, b) => a - b)),
            spreadColumn('Touch points', Array.from(touch).sort((a, b) => a - b)),
            spreadColumn('Android releases', Array.from(releases).sort()),
          ]),
        ]),
      ]),
    ]),
  );
}

function spreadColumn(label, values) {
  return el('div', { class: 'field' }, [
    el('label', { text: label }),
    el('div', {
      class: 'code',
      text: values.length ? values.join(' · ') : '—',
    }),
  ]);
}

/* ----------------------------- presets ----------------------------- */

async function renderPresets(actions, content) {
  const response = await call('GET', '/api/presets');
  presets = pickList(response, 'presets');

  const rows = presets.map((preset) => {
    const screen = preset.screen || {};
    return el('tr', {}, [
      el('td', {}, [el('span', { class: 'cell-id', text: preset.preset_id || preset.id })]),
      el('td', { text: [preset.manufacturer, preset.model].filter(Boolean).join(' ') }),
      el('td', { text: preset.release || '—' }),
      el('td', { text: screen.width ? screen.width + '×' + screen.height : '—' }),
      el('td', { text: screen.device_pixel_ratio ? screen.device_pixel_ratio + '×' : '—' }),
      el('td', { text: optionList(preset.cpu_options) }),
      el('td', { text: optionList(preset.ram_options) }),
      el('td', { text: optionList(preset.touch_points_options) }),
      el('td', { text: preset.gpu && preset.gpu.unmasked_renderer ? preset.gpu.unmasked_renderer : '—' }),
    ]);
  });

  const table = rows.length
    ? el('table', {}, [
        el('thead', {}, [
          el('tr', {}, [
            el('th', { text: 'Id' }),
            el('th', { text: 'Thiết bị' }),
            el('th', { text: 'Android' }),
            el('th', { text: 'Màn hình' }),
            el('th', { text: 'Tỉ lệ' }),
            el('th', { text: 'Lõi CPU' }),
            el('th', { text: 'Bộ nhớ' }),
            el('th', { text: 'Cảm ứng' }),
            el('th', { text: 'Đồ hoạ' }),
          ]),
        ]),
        el('tbody', {}, rows),
      ])
    : el('div', { class: 'empty' }, [
        el('h3', { text: 'Danh mục rỗng' }),
        el('p', { text: 'Không tìm thấy nhóm thiết bị nào.' }),
      ]);

  content.replaceChildren(
    el('div', {}, [
      el('div', { class: 'card', style: 'margin-bottom: 16px' }, [
        el('div', { class: 'card-head' }, [el('h3', { text: 'Ý nghĩa các cột' })]),
        el('div', { class: 'card-body' }, [
          el('p', {
            class: 'code',
            style: 'margin: 0; line-height: 1.7',
            text:
              'Lõi CPU, bộ nhớ và cảm ứng là những giá trị mà phần cứng của thiết bị đó thực sự có thể báo. ' +
              'A profile of this device draws one of them -- it never reports a core count the model ' +
              'does not have, and two profiles of the same model do not report the same one.',
          }),
        ]),
      ]),
      el('div', { class: 'card' }, [
        el('div', { class: 'card-head' }, [
          el('h3', { text: 'Danh mục (' + presets.length + ')' }),
        ]),
        el('div', { class: 'table-wrap' }, [table]),
      ]),
    ]),
  );
}

/**
 * A set of options, as one cell.
 *
 * The options are what the device can report, so the cell shows them
 * all: an operator looking at the catalogue wants to see the range the
 * engine draws from.
 */
function optionList(values) {
  if (!values || !values.length) return '—';
  return values.join(' · ');
}

/* ----------------------------- proxies ----------------------------- */

async function renderProxies(actions, content) {
  // The rows carry the proxy, so the whole configuration of every
  // profile in the fleet is not needed to draw this page.
  const response = await call('GET', '/api/profiles/rows?limit=100000');
  const all = pickList(response, 'profiles');

  const withProxy = all.filter((profile) => proxyLabel(profile));
  const withoutProxy = all.filter((profile) => !proxyLabel(profile));

  const rows = withProxy.map((profile) =>
    el('tr', {}, [
      el('td', {}, [el('span', { class: 'cell-id', text: shortId(profileId(profile)) })]),
      el('td', { class: 'code', text: proxyLabel(profile) }),
      el('td', { text: locationLabel(profile) }),
      el('td', { text: timeZoneLabel(profile) }),
    ]),
  );

  const table = rows.length
    ? el('table', {}, [
        el('thead', {}, [
          el('tr', {}, [
            el('th', { text: 'Profile' }),
            el('th', { text: 'Proxy' }),
            el('th', { text: 'Vị trí' }),
            el('th', { text: 'Múi giờ' }),
          ]),
        ]),
        el('tbody', {}, rows),
      ])
    : el('div', { class: 'empty' }, [
        el('h3', { text: 'Chưa gán proxy' }),
        el('p', {
          text:
            withoutProxy.length +
            ' profiles are on the machine’s own connection. A profile without a proxy is the same address to every site it visits.',
        }),
      ]);

  content.replaceChildren(
    el('div', {}, [
      el('div', { class: 'stat-grid' }, [
        statTile('Có proxy', num(withProxy.length), 'dùng đường truyền riêng'),
        statTile('Trực tiếp', num(withoutProxy.length), 'trên máy này'),
      ]),
      el('div', { class: 'card' }, [
        el('div', { class: 'card-head' }, [el('h3', { text: 'Bảng proxy' })]),
        el('div', { class: 'table-wrap' }, [table]),
      ]),
    ]),
  );
}

/* ------------------------------ audit ------------------------------ */

async function renderAudit(actions, content) {
  actions.appendChild(
    el('button', {
      class: 'btn btn-primary',
      text: 'Chạy kiểm tra',
      onclick: async (event) => {
        event.target.disabled = true;
        event.target.textContent = 'Probing…';
        try {
          const result = await call('POST', '/api/probe/run', { count: 5 });
          const passed = result && (result.passed !== undefined ? result.passed : result.ok);
          const failed = result && result.failed !== undefined ? result.failed : 0;
          toast(
            'Probe finished: ' + passed + ' passed, ' + failed + ' failed.',
            failed ? 'warn' : 'ok',
          );
          await render();
        } catch (error) {
          toast(String((error && error.message) || error), 'bad');
          event.target.disabled = false;
          event.target.textContent = 'Chạy kiểm tra';
        }
      },
    }),
  );

  let metrics = {};
  try {
    metrics = (await call('GET', '/api/probe/suite/metrics')) || {};
  } catch (error) {
    metrics = {};
  }

  const rows = Object.keys(metrics).map((key) =>
    el('tr', {}, [
      el('td', { text: key.replace(/_/g, ' ') }),
      el('td', { class: 'code', text: JSON.stringify(metrics[key]) }),
    ]),
  );

  content.replaceChildren(
    el('div', {}, [
      el('div', { class: 'card' }, [
        el('div', { class: 'card-head' }, [el('h3', { text: 'Mẫu gần nhất' })]),
        el('div', { class: 'table-wrap' }, [
          rows.length
            ? el('table', {}, [
                el('thead', {}, [
                  el('tr', {}, [el('th', { text: 'Chỉ số' }), el('th', { text: 'Giá trị' })]),
                ]),
                el('tbody', {}, rows),
              ])
            : el('div', { class: 'empty' }, [
                el('h3', { text: 'Chưa kiểm tra gì' }),
                el('p', {
                  text:
                    'Bộ kiểm tra mở một profile, đọc những gì trình duyệt báo, rồi so với những gì profile khai.',
                }),
              ]),
        ]),
      ]),
    ]),
  );
}

/* ----------------------------- settings ---------------------------- */

async function renderSettings(actions, content) {
  const info = shell.info || {};
  let defaults = {};
  try {
    defaults = (await call('GET', '/api/settings/defaults')) || {};
  } catch (error) {
    defaults = {};
  }

  const infoRows = [
    ['Version', info.version],
    ['Data', info.dataDir],
    ['Logs', info.logsDir],
    ['Chromium core', info.coreDir],
    ['Core present', info.coreAvailable ? 'yes' : 'no — the stock browser is used'],
  ];

  const list = el('dl', { class: 'kv' });
  for (const entry of infoRows) {
    if (entry[1] === undefined || entry[1] === null) continue;
    list.appendChild(el('dt', { text: entry[0] }));
    list.appendChild(el('dd', { text: String(entry[1]) }));
  }

  const defaultRows = Object.keys(defaults).map((key) =>
    el('tr', {}, [
      el('td', { text: key.replace(/_/g, ' ') }),
      el('td', {
        class: 'code',
        text:
          typeof defaults[key] === 'object'
            ? JSON.stringify(defaults[key])
            : String(defaults[key]),
      }),
    ]),
  );

  content.replaceChildren(
    el('div', {}, [
      el('div', { class: 'card', style: 'margin-bottom: 16px' }, [
        el('div', { class: 'card-head' }, [el('h3', { text: 'Bản cài đặt này' })]),
        el('div', { class: 'card-body' }, [list]),
      ]),
      el('div', { class: 'card' }, [
        el('div', { class: 'card-head' }, [
          el('h3', { text: 'Cài đặt mặc định cho profile mới' }),
        ]),
        el('div', { class: 'table-wrap' }, [
          defaultRows.length
            ? el('table', {}, [
                el('thead', {}, [
                  el('tr', {}, [el('th', { text: 'Cài đặt' }), el('th', { text: 'Giá trị' })]),
                ]),
                el('tbody', {}, defaultRows),
              ])
            : el('div', { class: 'empty' }, [
                el('p', { text: 'Chưa cấu hình mặc định nào. Bộ máy dùng giá trị riêng của nó.' }),
              ]),
        ]),
      ]),
    ]),
  );
}

/* ------------------------------------------------------------------ */
/* Navigation                                                          */
/* ------------------------------------------------------------------ */

document.getElementById('nav').addEventListener('click', (event) => {
  const button = event.target.closest('.nav-item');
  if (!button) return;
  const items = document.querySelectorAll('.nav-item');
  for (const item of items) {
    item.classList.toggle('is-active', item === button);
  }
  currentView = button.dataset.view;
  render();
});

/* ------------------------------------------------------------------ */
/* The shell's events                                                  */
/* ------------------------------------------------------------------ */

api.on('backend:ready', async () => {
  shell.ready = true;
  shell.error = null;
  setStatus('ok', 'Control plane ready');
  await render();
  await pollRunning();
  refreshRunningPoll();
});

api.on('backend:failed', (payload) => {
  shell.ready = false;
  shell.error = (payload && payload.message) || 'The control plane did not start.';
  setStatus('bad', 'Control plane failed');
  render();
});

api.on('backend:stopped', () => {
  shell.ready = false;
  setStatus('bad', 'Control plane stopped');
  render();
});

api.on('menu:new-profile', () => openProfileDialog());
api.on('menu:bulk-create', () => openBulkDialog());

/* ------------------------------------------------------------------ */
/* Start                                                               */
/* ------------------------------------------------------------------ */

/**
 * Ask the control plane whether it is there.
 *
 * The shell tells the renderer when the control plane comes up, but the
 * renderer cannot rely on that message alone: a window that is opened
 * after the service is already running never receives it.  So the
 * renderer asks as well, and the two answers agree.
 */
async function probe() {
  try {
    const health = await api.call('GET', '/api/health');
    if (health && health.status === 'ok') {
      shell.ready = true;
      shell.error = null;
      setStatus('ok', 'Control plane ready');
      await render();
      return;
    }
  } catch (error) {
    /* the shell's own message will follow */
  }
}

(async function start() {
  // The trap is installed first, so that a mistake in the interface is
  // shown even if it happens while the window is waking up.
  installErrorTrap();

  try {
    shell.info = await api.info();
    document.getElementById('version').textContent = 'v' + (shell.info.version || '?');
  } catch (error) {
    /* the footer is not worth a toast */
  }
  await probe();
  render();
})();
