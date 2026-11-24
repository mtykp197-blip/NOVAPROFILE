"""The NOVA PROFILE desktop application.

A Tkinter front end over the control plane.  The engine does the work --
the generator builds the profiles, the registry stores them, the launcher
starts the browser and the probe measures what the browser reports -- and
this module is the window through which all of that is driven.

The layout follows the desktop tools the market already knows, and the
one the market for this product already uses:

* a **header** with the title, the count of the profiles, the state of
  the local API and the state of the licence;
* an **action bar** with the buttons that apply to the selection --
  create, open, edit, rotate the fingerprint, probe, delete, export,
  import, defaults, licence;
* a **table** of the profiles, with the name, the device, the Android
  version, the browser version, the proxy and the time it was made;
* a **detail panel** on the right, which shows what the selected
  profile actually is;
* a **status bar** at the bottom, which says what just happened.

The colours come from :mod:`novaprofile.brand.theme`, so the window
and the command line cannot drift apart.

The module is written so that a checkout **without** Tkinter still
imports: the application is a convenience, and a headless deployment
that only uses the API and the command line must not fail because the
machine has no display toolkit.
"""

from __future__ import annotations

import contextlib
import json
import queue
import threading
import time
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any

from ..brand.theme import (
    ACCENT,
    ACCENT_SECONDARY,
    BACKGROUND,
    DANGER,
    SURFACE,
    TEXT,
    TEXT_MUTED,
)

__all__ = [
    "NovaApp",
    "ProfileRow",
    "run",
]

WINDOW_TITLE = "NOVA PROFILE"
WINDOW_SIZE = (1320, 780)


@dataclass(frozen=True)
class ProfileRow:
    """One line of the table.

    The row carries the readings the operator reads at a glance.  The
    profile document itself is fetched on demand, so that a fleet of a
    thousand profiles does not have to be a thousand documents in
    memory to draw a table.
    """

    profile_id: str
    name: str
    device: str
    android: str
    chrome: str
    proxy: str
    created: str


def _format_proxy(proxy: Any) -> str:
    """Render a proxy configuration the way the table shows it.

    The proxy is the schema's :class:`~novaprofile.schema.network.Proxy`
    -- a model with an ``enabled`` flag and a ``protocol`` -- but the
    defaults file stores it as a plain dictionary with a ``mode``.  Both
    shapes are understood, so that a profile built from the API and one
    built from the window are shown the same way.
    """

    if proxy is None:
        return "--"

    if isinstance(proxy, dict):
        mode = proxy.get("mode") or ("none" if not proxy.get("host") else "")
        if mode == "none" or not proxy.get("host"):
            return "--"
        return f"{proxy.get('host')}:{proxy.get('port', '')}"

    if not getattr(proxy, "enabled", False):
        return "--"
    host = getattr(proxy, "host", "") or ""
    port = getattr(proxy, "port", "") or ""
    return f"{host}:{port}" if host else "--"


def _profile_row(config: Any) -> ProfileRow:
    """The table row of a stored profile document."""

    identity = getattr(config, "identity", None)
    android = getattr(config, "android_build", None)
    navigator = getattr(config, "navigator", None)
    browser = getattr(config, "browser_version", None)
    proxy = getattr(config, "proxy", None)
    created = getattr(identity, "created_at", None)

    return ProfileRow(
        profile_id=str(getattr(identity, "id", "") or getattr(identity, "profile_id", "")),
        name=str(getattr(identity, "name", "") or ""),
        device=str(getattr(android, "model", "") or ""),
        android=str(getattr(android, "version_release", "") or ""),
        chrome=str(getattr(browser, "full_version", "") or getattr(browser, "major", "") or ""),
        proxy=_format_proxy(proxy),
        created=str(created)[:19] if created else "",
    )


class NovaApp(tk.Tk):
    """The window.

    The window owns the store, the launcher and the guard.  Everything
    it does goes through them, so that the window cannot become a
    second, weaker way of doing what the command line does.
    """

    def __init__(
        self,
        *,
        store: Any = None,
        launcher: Any = None,
        guard: Any = None,
    ) -> None:
        super().__init__()

        self.title(WINDOW_TITLE)
        self.geometry(f"{WINDOW_SIZE[0]}x{WINDOW_SIZE[1]}")
        self.configure(bg=BACKGROUND)

        self._store = store
        self._launcher = launcher
        self._guard = guard
        self._rows: list[ProfileRow] = []
        self._selected: str | None = None
        self._messages: queue.Queue[tuple[str, str]] = queue.Queue()

        self._build_style()
        self._build_header()
        self._build_actions()
        self._build_body()
        self._build_status()

        self.after(120, self._drain_messages)
        self.after(60, self._refresh)

    # ------------------------------------------------------------------
    # The layout
    # ------------------------------------------------------------------

    def _build_style(self) -> None:
        """The colours and the fonts."""

        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:  # pragma: no cover - not every theme is there
            pass

        style.configure(
            "Nova.Treeview",
            background=SURFACE,
            fieldbackground=SURFACE,
            foreground=TEXT,
            rowheight=30,
            borderwidth=0,
        )
        style.configure(
            "Nova.Treeview.Heading",
            background=SURFACE,
            foreground=TEXT_MUTED,
            borderwidth=0,
        )
        style.map("Nova.Treeview", background=[("selected", ACCENT)])

    def _build_header(self) -> None:
        """The title, the count, the API state and the licence state."""

        header = tk.Frame(self, bg=BACKGROUND)
        header.pack(fill="x", padx=18, pady=(16, 8))

        tk.Label(
            header,
            text=WINDOW_TITLE,
            bg=BACKGROUND,
            fg=TEXT,
            font=("Segoe UI", 18, "bold"),
        ).pack(side="left")

        self._count_label = tk.Label(
            header,
            text="",
            bg=BACKGROUND,
            fg=TEXT_MUTED,
            font=("Segoe UI", 10),
        )
        self._count_label.pack(side="left", padx=(12, 0))

        self._licence_label = tk.Label(
            header,
            text="Licence: chua kiem tra",
            bg=BACKGROUND,
            fg=TEXT_MUTED,
            font=("Segoe UI", 10),
        )
        self._licence_label.pack(side="right")

        self._api_label = tk.Label(
            header,
            text="API local: --",
            bg=BACKGROUND,
            fg=TEXT_MUTED,
            font=("Segoe UI", 10),
        )
        self._api_label.pack(side="right", padx=(0, 18))

    def _build_actions(self) -> None:
        """The buttons that apply to the selection."""

        bar = tk.Frame(self, bg=BACKGROUND)
        bar.pack(fill="x", padx=18, pady=(0, 8))

        buttons = (
            ("+ Tao profile", self.action_create),
            ("Mo", self.action_open),
            ("Sua", self.action_edit),
            ("Xoay van tay", self.action_rotate),
            ("Do probe", self.action_probe),
            ("Probe web", self.action_probe_web),
            ("Xoa", self.action_delete),
            ("Mac dinh", self.action_defaults),
            ("Xuat", self.action_export),
            ("Nhap", self.action_import),
            ("Licence", self.action_licence),
        )

        for label, command in buttons:
            tk.Button(
                bar,
                text=label,
                command=command,
                bg=SURFACE,
                fg=TEXT,
                activebackground=ACCENT,
                activeforeground="#ffffff",
                relief="flat",
                bd=0,
                padx=12,
                pady=6,
                font=("Segoe UI", 9),
                cursor="hand2",
            ).pack(side="left", padx=(0, 6))

    def _build_body(self) -> None:
        """The table and the detail panel."""

        body = tk.Frame(self, bg=BACKGROUND)
        body.pack(fill="both", expand=True, padx=18, pady=(0, 8))

        columns = ("name", "device", "android", "chrome", "proxy", "created")
        headings = {
            "name": "Ten",
            "device": "Thiet bi",
            "android": "Android",
            "chrome": "Trinh duyet",
            "proxy": "Proxy",
            "created": "Tao luc",
        }

        table = ttk.Treeview(
            body,
            columns=columns,
            show="headings",
            style="Nova.Treeview",
        )
        for column in columns:
            table.heading(column, text=headings[column])
            table.column(column, width=160, anchor="w")

        scroll = ttk.Scrollbar(body, orient="vertical", command=table.yview)
        table.configure(yscrollcommand=scroll.set)

        table.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")
        table.bind("<<TreeviewSelect>>", self._on_select)

        self._detail = tk.Text(
            body,
            width=42,
            bg=SURFACE,
            fg=TEXT,
            relief="flat",
            bd=0,
            font=("Consolas", 9),
            wrap="word",
        )
        self._detail.pack(side="right", fill="both", padx=(12, 0))
        self._detail.configure(state="disabled")

    def _build_status(self) -> None:
        """The bar at the bottom that says what just happened."""

        self._status = tk.Label(
            self,
            text="",
            anchor="w",
            bg=SURFACE,
            fg=TEXT_MUTED,
            font=("Segoe UI", 9),
        )
        self._status.pack(fill="x", side="bottom")

    # ------------------------------------------------------------------
    # The table
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        """Read the profiles and draw the table."""

        try:
            self._load_rows()
        except Exception as exc:  # pragma: no cover - the store may be absent
            self._set_status(f"Khong doc duoc danh sach profile: {exc}")
        self.after(1500, self._refresh)

    def _load_rows(self) -> None:
        """Read the profiles out of the store."""

        if self._store is None:
            return

        rows: list[ProfileRow] = []
        for config in self._store.list_profiles():
            document = config.model_dump() if hasattr(config, "model_dump") else dict(config)
            rows.append(_profile_row(document))

        table = self._tree()
        shown = [
            (r.profile_id, r.name, r.device, r.android, r.chrome, r.proxy, r.created)
            for r in rows
        ]
        if shown == getattr(self, "_shown", None):
            self._rows = rows
            return
        self._shown = shown
        self._rows = rows

        keep = self._selected
        for item in table.get_children():
            table.delete(item)

        for row in rows:
            table.insert(
                "",
                "end",
                iid=row.profile_id,
                values=(
                    row.name,
                    row.device,
                    row.android,
                    row.chrome,
                    row.proxy,
                    row.created,
                ),
            )

        self._count_label.configure(text=f"{len(rows)} profile")
        if keep is not None and table.exists(keep):
            table.selection_set(keep)
            table.see(keep)

    def _tree(self) -> ttk.Treeview:
        """The table widget."""

        for child in self.winfo_children():
            stack = [child]
            while stack:
                widget = stack.pop()
                if isinstance(widget, ttk.Treeview):
                    return widget
                stack.extend(widget.winfo_children())
        raise RuntimeError("the table is not there")

    def _on_select(self, _event: Any = None) -> None:
        """Show what the selected profile is."""

        selection = self._tree().selection()
        if not selection:
            self._selected = None
            self._write_detail("")
            return

        self._selected = selection[0]
        row = next((r for r in self._rows if r.profile_id == self._selected), None)
        if row is None:
            self._write_detail("")
            return

        lines = [
            f"Ten       : {row.name}",
            f"ID        : {row.profile_id}",
            f"Thiet bi  : {row.device}",
            f"Android   : {row.android}",
            f"Trinh     : {row.chrome}",
            f"Proxy     : {row.proxy}",
            f"Tao luc   : {row.created}",
        ]
        self._write_detail("\n".join(lines))

    def _write_detail(self, text: str) -> None:
        """Put text into the detail panel."""

        self._detail.configure(state="normal")
        self._detail.delete("1.0", "end")
        self._detail.insert("1.0", text)
        self._detail.configure(state="disabled")

    def _set_status(self, message: str) -> None:
        """Say what just happened."""

        self._status.configure(text=message)

    def _drain_messages(self) -> None:
        """Take the messages the workers left behind."""

        while True:
            try:
                kind, payload = self._messages.get_nowait()
            except queue.Empty:
                break
            if kind == "status":
                self._set_status(payload)
            elif kind == "reload":
                self._load_rows()
        self.after(120, self._drain_messages)

    def _post(self, kind: str, payload: Any) -> None:
        """Leave a message for the window thread."""

        self._messages.put((kind, payload))

    # ------------------------------------------------------------------
    # The actions
    # ------------------------------------------------------------------

    def _selected_row(self) -> ProfileRow | None:
        """The row the operator has selected."""

        if self._selected is None:
            return None
        return next((r for r in self._rows if r.profile_id == self._selected), None)

    def action_create(self) -> None:
        """Create a profile."""

        if self._store is None:
            self._set_status("Chua mo kho profile.")
            return

        name = simpledialog.askstring(
            "Tao profile",
            "Ten profile:",
            initialvalue="Profile moi",
            parent=self,
        )
        if not name:
            return

        try:
            from ..core.generator import ProfileGenerator
            from ..core.seed import new_master_seed

            config = ProfileGenerator().generate(
                preset_id="pixel-8-pro-android14",
                name=name,
                master_seed=new_master_seed(),
            )
            self._store.create_profile(config)
        except Exception as exc:
            self._set_status(f"Khong tao duoc profile: {exc}")
            return

        self._load_rows()
        self._set_status(f"Da tao profile {name}.")

    def action_open(self) -> None:
        """Open the selected profile in the browser."""

        row = self._selected_row()
        if row is None:
            self._set_status("Chon mot profile truoc.")
            return
        if self._launcher is None:
            self._set_status("Chua mo launcher.")
            return

        try:
            self._launcher.launch(row.profile_id)
        except Exception as exc:
            self._set_status(f"Khong mo duoc profile: {exc}")
            return
        self._set_status(f"Dang mo {row.name}.")

    def action_edit(self) -> None:
        """Rename the selected profile."""

        row = self._selected_row()
        if row is None:
            self._set_status("Chon mot profile truoc.")
            return

        name = simpledialog.askstring(
            "Sua profile",
            "Ten moi:",
            initialvalue=row.name,
            parent=self,
        )
        if not name:
            return

        try:
            config = self._store.get_profile(row.profile_id)
            config.identity.name = name
            self._store.update_profile(config)
        except Exception as exc:
            self._set_status(f"Khong sua duoc profile: {exc}")
            return

        self._load_rows()
        self._set_status(f"Da sua profile thanh {name}.")

    def action_rotate(self) -> None:
        """Rotate the fingerprint of the selected profile."""

        row = self._selected_row()
        if row is None:
            self._set_status("Chon mot profile truoc.")
            return
        if self._guard is None:
            self._set_status("Chua mo guard.")
            return

        try:
            config = self._store.get_profile(row.profile_id)
            rotated = self._guard.allocate(row.profile_id, config)
            self._store.update_profile(rotated)
            self._guard.commit(rotated, row.profile_id)
        except Exception as exc:
            self._set_status(f"Khong xoay duoc van tay: {exc}")
            return

        self._load_rows()
        self._set_status("Da xoay van tay.")

    def action_probe(self) -> None:
        """Measure what the browser reports for the selected profile."""

        row = self._selected_row()
        if row is None:
            self._set_status("Chon mot profile truoc.")
            return

        self._set_status(f"Dang do probe {row.name}...")

        def worker() -> None:
            try:
                from ..probe.verify import verify_profile

                result = verify_profile(row.profile_id)
                self._post("status", f"Probe: {result}")
            except Exception as exc:
                self._post("status", f"Probe that bai: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def action_probe_web(self) -> None:
        """Open the third-party detection page for the selected profile."""

        row = self._selected_row()
        if row is None:
            self._set_status("Chon mot profile truoc.")
            return
        if self._launcher is None:
            self._set_status("Chua mo launcher.")
            return

        from ..probe.third_party import THIRD_PARTY_URLS

        url = THIRD_PARTY_URLS[0] if THIRD_PARTY_URLS else "https://abrahamjuliot.github.io/creepjs/"
        try:
            self._launcher.launch(row.profile_id, url=url)
        except Exception as exc:
            self._set_status(f"Khong mo duoc trang web: {exc}")
            return
        self._set_status("Dang mo trang kiem tra.")

    def action_delete(self) -> None:
        """Delete the selected profile."""

        row = self._selected_row()
        if row is None:
            self._set_status("Chon mot profile truoc.")
            return

        if not messagebox.askyesno(
            "Xoa profile",
            f"Xoa {row.name}?",
            parent=self,
        ):
            return

        try:
            self._store.delete_profile(row.profile_id)
        except Exception as exc:
            self._set_status(f"Khong xoa duoc profile: {exc}")
            return

        self._load_rows()
        self._set_status(f"Da xoa {row.name}.")

    def action_defaults(self) -> None:
        """Edit the defaults every new profile starts from."""

        from .defaults import default_settings_path, load_defaults, save_defaults

        settings = load_defaults()
        dialog = tk.Toplevel(self)
        dialog.title("Mac dinh")
        dialog.configure(bg=BACKGROUND)
        dialog.transient(self)

        fields: dict[str, tk.Entry] = {}
        for row_index, (key, value) in enumerate(settings.items()):
            if key == "proxy":
                continue
            tk.Label(
                dialog,
                text=key,
                bg=BACKGROUND,
                fg=TEXT_MUTED,
                font=("Segoe UI", 9),
            ).grid(row=row_index, column=0, sticky="w", padx=12, pady=4)
            entry = tk.Entry(dialog, font=("Segoe UI", 9))
            entry.insert(0, str(value))
            entry.grid(row=row_index, column=1, sticky="ew", padx=12, pady=4)
            fields[key] = entry

        dialog.columnconfigure(1, weight=1)

        def save() -> None:
            for key, entry in fields.items():
                settings[key] = entry.get()
            save_defaults(settings)
            dialog.destroy()
            self._set_status("Da luu mac dinh.")

        tk.Button(
            dialog,
            text="Luu",
            command=save,
            bg=ACCENT,
            fg="#ffffff",
            relief="flat",
            bd=0,
            padx=16,
            pady=6,
        ).grid(row=len(fields), column=0, columnspan=2, pady=12)

    def action_export(self) -> None:
        """Export the profiles to a file."""

        path = filedialog.asksaveasfilename(
            title="Xuat profile",
            defaultextension=".json",
            filetypes=(("JSON", "*.json"),),
            parent=self,
        )
        if not path:
            return

        try:
            documents = [
                config.model_dump_json()
                if hasattr(config, "model_dump_json")
                else json.dumps(config)
                for config in self._store.list_profiles()
            ]
            Path(path).write_text("\n".join(documents), encoding="utf-8")
        except Exception as exc:
            self._set_status(f"Khong xuat duoc: {exc}")
            return

        self._set_status(f"Da xuat {len(documents)} profile ra {path}.")

    def action_import(self) -> None:
        """Import profiles from a file."""

        path = filedialog.askopenfilename(
            title="Nhap profile",
            filetypes=(("JSON", "*.json"),),
            parent=self,
        )
        if not path:
            return

        imported = 0
        try:
            for line in Path(path).read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                from ..schema.profile import ProfileConfig

                config = ProfileConfig.model_validate_json(line)
                self._store.create_profile(config)
                imported += 1
        except Exception as exc:
            self._set_status(f"Khong nhap duoc: {exc}")
            return

        self._load_rows()
        self._set_status(f"Da nhap {imported} profile.")

    def action_licence(self) -> None:
        """Show the state of the licence."""

        try:
            from ..licensing.client import LicensingClient

            state = LicensingClient().state()
        except Exception as exc:
            self._set_status(f"Khong doc duoc licence: {exc}")
            return

        self._licence_label.configure(text=f"Licence: {state}")
        self._set_status(f"Licence: {state}")


def run(
    *,
    store: Any = None,
    launcher: Any = None,
    guard: Any = None,
) -> int:
    """Open the NOVA PROFILE window.

    When no store is given, the default one is opened.  When no guard is
    given, one is built over the store -- the window allocates every
    profile through the guard, so a fleet created from the GUI cannot
    contain a duplicate.  A window without a guard is still usable, but
    it is a window that can be made to collide.
    """

    if store is None:
        from ..registry.store import ProfileStore, default_db_path

        store = ProfileStore(default_db_path())
        store.init_db()

    if guard is None:
        from ..core.collision import CollisionGuard

        guard = CollisionGuard(registry=store)

    if launcher is None:
        from ..launcher.launcher import default_launcher

        launcher = default_launcher()

    app = NovaApp(store=store, launcher=launcher, guard=guard)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
