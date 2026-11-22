"""Giao dien quan ly profile local-first, phong cach Hidemium (tu trien khai).

Code viet lai moi. Chay: python app.py
"""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import core

BG = "#1b1e27"
BG2 = "#232733"
CARD = "#262b3a"
FG = "#e8eaf0"
MUTED = "#9aa0b4"
ACCENT = "#4f7cff"
GREEN = "#35c26e"
RED = "#e5484d"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Local Profiles — quan ly profile Android local")
        self.geometry("1080x640")
        self.configure(bg=BG)
        self._style()
        self._build()
        self.refresh()

    def _style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("TFrame", background=BG)
        s.configure("Card.TFrame", background=CARD)
        s.configure("TLabel", background=BG, foreground=FG, font=("Segoe UI", 10))
        s.configure("Card.TLabel", background=CARD, foreground=FG)
        s.configure("Muted.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 9))
        s.configure("Title.TLabel", background=BG, foreground=FG, font=("Segoe UI", 15, "bold"))
        s.configure("TButton", background=ACCENT, foreground="white", borderwidth=0,
                    font=("Segoe UI", 9, "bold"), padding=7)
        s.map("TButton", background=[("active", "#3d66d6")])
        s.configure("Ghost.TButton", background=BG2, foreground=FG)
        s.map("Ghost.TButton", background=[("active", "#2d3345")])
        s.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=FG,
                    rowheight=30, borderwidth=0, font=("Segoe UI", 10))
        s.configure("Treeview.Heading", background=BG2, foreground=MUTED,
                    font=("Segoe UI", 9, "bold"))
        s.map("Treeview", background=[("selected", ACCENT)])

    def _build(self):
        top = ttk.Frame(self)
        top.pack(fill="x", padx=16, pady=(14, 6))
        ttk.Label(top, text="Local Profiles", style="Title.TLabel").pack(side="left")
        self.count = ttk.Label(top, text="", style="Muted.TLabel")
        self.count.pack(side="left", padx=12)
        ttk.Button(top, text="API local: bat", command=self._toggle_api).pack(side="right")
        self.api_lbl = ttk.Label(top, text=":18300", style="Muted.TLabel")
        self.api_lbl.pack(side="right", padx=8)

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=16, pady=6)
        for txt, cmd, ghost in [
            ("+ Tao profile", self._create, False),
            ("Mo", self._open, False),
            ("Sua", self._edit, True),
            ("Xoay van tay", self._rotate, True),
            ("Xoa", self._delete, True),
            ("Mac dinh", self._defaults, True),
            ("Xuat", self._export, True),
            ("Nhap", self._import, True),
        ]:
            ttk.Button(bar, text=txt, command=cmd,
                       style="Ghost.TButton" if ghost else "TButton").pack(side="left", padx=3)

        cols = ("name", "device", "os", "proxy", "created")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", selectmode="extended")
        heads = [("name", "Ten"), ("device", "Thiet bi"), ("os", "Android"),
                 ("proxy", "Proxy"), ("created", "Tao luc")]
        for c, h in heads:
            self.tree.heading(c, text=h)
        self.tree.column("name", width=200)
        self.tree.column("device", width=220)
        self.tree.column("os", width=90)
        self.tree.column("proxy", width=220)
        self.tree.column("created", width=160)
        self.tree.pack(fill="both", expand=True, padx=16, pady=8)
        self.tree.bind("<Double-1>", lambda e: self._open())

        self.status = ttk.Label(self, text="San sang.", style="Muted.TLabel")
        self.status.pack(fill="x", padx=16, pady=(0, 12))
        self._api = core.start_api()
        self.api_lbl.config(text=":18300 dang chay")

    # ---- helpers ----
    def _sel(self):
        ids = []
        for it in self.tree.selection():
            ids.append(self.tree.item(it, "values")[0])
        return ids

    def _sel_one(self):
        ids = self._sel()
        if len(ids) != 1:
            messagebox.showinfo("Chon", "Chon dung 1 profile.")
            return None
        return ids[0]

    def refresh(self):
        for it in self.tree.get_children():
            self.tree.delete(it)
        ps = core.list_profiles()
        for p in ps:
            px = p.get("proxy", {})
            pxt = "khong" if px.get("mode") in ("none", "", None) else \
                f"{px.get('mode')}://{px.get('host')}:{px.get('port')}"
            fp = p.get("fingerprint", {})
            self.tree.insert("", "end", iid=p["id"],
                             values=(p.get("name"), fp.get("device"),
                                     fp.get("os_version"), pxt, p.get("created_at")))
        self.count.config(text=f"{len(ps)} profile — khong gioi han")
        self.status.config(text=f"Da tai {len(ps)} profile.")

    # ---- actions ----
    def _create(self):
        name = simpledialog.askstring("Tao profile", "Ten profile:", parent=self) or ""
        try:
            p = core.create_profile(name)
        except OSError as e:
            return messagebox.showerror("Loi", str(e))
        self.status.config(text=f"Da tao {p['name']} ({p['fingerprint']['device']}).")
        self.refresh()

    def _open(self):
        pid = self._sel_one()
        if not pid:
            return
        try:
            core.launch_profile(pid)
            self.status.config(text=f"Dang mo {pid}...")
        except (FileNotFoundError, OSError) as e:
            messagebox.showerror("Loi mo", str(e))

    def _edit(self):
        pid = self._sel_one()
        if not pid:
            return
        try:
            p = core.get_profile(pid)
        except KeyError:
            return messagebox.showerror("Loi", "Profile khong ton tai.")
        w = tk.Toplevel(self)
        w.title(f"Sua {p['name']}")
        w.configure(bg=BG)
        w.geometry("460x480")
        f = ttk.Frame(w)
        f.pack(fill="both", expand=True, padx=14, pady=14)
        vars_ = {}

        def row(lbl, key, value=""):
            ttk.Label(f, text=lbl).pack(anchor="w", pady=(8, 2))
            v = tk.StringVar(value=str(value))
            ttk.Entry(f, textvariable=v).pack(fill="x")
            vars_[key] = v

        row("Ten", "name", p.get("name"))
        row("URL mo dau", "startup_url", p.get("startup_url"))
        px = p.get("proxy", {})
        row("Proxy mode (none/http/socks5)", "px_mode", px.get("mode", "none"))
        row("Proxy host", "px_host", px.get("host", ""))
        row("Proxy port", "px_port", px.get("port", 0))
        row("Proxy user", "px_user", px.get("username", ""))
        row("Proxy pass", "px_pass", px.get("password", ""))
        row("Ghi chu", "notes", p.get("notes", ""))
        ttk.Label(f, text="Thiet bi: "
                  f"{p.get('fingerprint', {}).get('device')} / "
                  f"Android {p.get('fingerprint', {}).get('os_version')}",
                  style="Muted.TLabel").pack(anchor="w", pady=8)

        def save():
            try:
                port = int(vars_["px_port"].get() or 0)
            except ValueError:
                return messagebox.showerror("Loi", "Port phai la so.")
            core.update_profile(pid, {
                "name": vars_["name"].get(),
                "startup_url": vars_["startup_url"].get(),
                "proxy": {"mode": vars_["px_mode"].get(), "host": vars_["px_host"].get(),
                          "port": port, "username": vars_["px_user"].get(),
                          "password": vars_["px_pass"].get()},
                "notes": vars_["notes"].get(),
            })
            w.destroy()
            self.refresh()
            self.status.config(text=f"Da luu {pid}.")

        ttk.Button(f, text="Luu").pack(pady=12)
        ttk.Button(f, text="Chon thu muc extension", style="Ghost.TButton",
                   command=lambda: self._pick_ext(pid, w)).pack()

    def _pick_ext(self, pid, parent):
        d = filedialog.askdirectory(parent=parent, title="Thu muc extension (unpacked)")
        if not d:
            return
        try:
            p = core.get_profile(pid)
        except KeyError:
            return
        exts = p.get("extensions", []) + [d]
        core.update_profile(pid, {"extensions": exts})
        messagebox.showinfo("Xong", f"Da them extension:\n{d}")

    def _rotate(self):
        pid = self._sel_one()
        if not pid:
            return
        try:
            p = core.rotate_fingerprint(pid)
        except KeyError:
            return messagebox.showerror("Loi", "Profile khong ton tai.")
        self.status.config(
            text=f"Da xoay van tay {pid} -> {p['fingerprint']['device']}.")
        self.refresh()

    def _delete(self):
        ids = self._sel()
        if not ids:
            return messagebox.showinfo("Xoa", "Chon it nhat 1 profile.")
        if not messagebox.askyesno("Xoa", f"Xoa {len(ids)} profile + du lieu?"):
            return
        for pid in ids:
            try:
                core.delete_profile(pid)
            except KeyError:
                pass
        self.refresh()

    def _defaults(self):
        d = core.load_defaults()
        w = tk.Toplevel(self)
        w.title("Cai dat mac dinh cho moi profile")
        w.configure(bg=BG)
        w.geometry("440x420")
        f = ttk.Frame(w)
        f.pack(fill="both", expand=True, padx=14, pady=14)
        vars_ = {}

        def row(lbl, key, value=""):
            ttk.Label(f, text=lbl).pack(anchor="w", pady=(8, 2))
            v = tk.StringVar(value=str(value))
            ttk.Entry(f, textvariable=v).pack(fill="x")
            vars_[key] = v

        row("Timezone", "timezone", d.get("timezone"))
        row("Locale", "locale", d.get("locale"))
        row("URL mo dau", "startup_url", d.get("startup_url"))
        px = d.get("proxy", {})
        row("Proxy mode", "px_mode", px.get("mode", "none"))
        row("Proxy host", "px_host", px.get("host", ""))
        row("Proxy port", "px_port", px.get("port", 0))

        def save():
            try:
                port = int(vars_["px_port"].get() or 0)
            except ValueError:
                return messagebox.showerror("Loi", "Port phai la so.")
            core.save_defaults({
                "timezone": vars_["timezone"].get(), "locale": vars_["locale"].get(),
                "startup_url": vars_["startup_url"].get(),
                "proxy": {"mode": vars_["px_mode"].get(), "host": vars_["px_host"].get(),
                          "port": port, "username": "", "password": ""},
            })
            w.destroy()
            self.status.config(text="Da luu cai dat mac dinh.")

        ttk.Button(f, text="Luu mac dinh").pack(pady=12)

    def _export(self):
        pid = self._sel_one()
        if not pid:
            return
        dest = filedialog.asksaveasfilename(parent=self, defaultextension=".json",
                                            filetypes=[("JSON", "*.json")])
        if dest:
            core.export_profile(pid, dest)
            self.status.config(text=f"Da xuat {pid}.")

    def _import(self):
        src = filedialog.askopenfilename(parent=self, filetypes=[("JSON", "*.json")])
        if src:
            try:
                p = core.import_profile(src)
            except (OSError, ValueError, KeyError) as e:
                return messagebox.showerror("Loi", str(e))
            self.refresh()
            self.status.config(text=f"Da nhap {p['id']}.")

    def _toggle_api(self):
        messagebox.showinfo("API local", "API dang chay tai http://127.0.0.1:18300\n"
                                        "GET /profiles | POST /profiles | PATCH /profiles/<id> | "
                                        "DELETE /profiles/<id>")


if __name__ == "__main__":
    App().mainloop()
