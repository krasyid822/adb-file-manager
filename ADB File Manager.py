import os
import shutil
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, simpledialog
import threading
import time
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Callable, Tuple

try:
    import pty
    HAS_PTY = True
except ImportError:
    HAS_PTY = False

CHECKBOX_OFF = "☐"
CHECKBOX_ON  = "☑"


def format_bytes(b: float) -> str:
    """Format angka byte menjadi string human-readable (B, KB, MB, GB, TB)."""
    if b <= 0:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {unit}" if unit != "B" else f"{int(b)} B"
        b /= 1024
    return f"{b:.1f} PB"


def format_speed(bps: float) -> str:
    """Format kecepatan transfer bytes per detik menjadi string KB/s atau MB/s."""
    if bps <= 0:
        return "0 KB/s"
    if bps < 1024:
        return f"{bps:.0f} B/s"
    elif bps < 1024 * 1024:
        return f"{bps / 1024:.1f} KB/s"
    else:
        return f"{bps / (1024 * 1024):.2f} MB/s"


# ╔══════════════════════════════════════════════════════════════════════╗
# ║                         DATA MODEL                                  ║
# ╚══════════════════════════════════════════════════════════════════════╝

@dataclass
class FileItem:
    name:       str
    size_raw:   str
    size_bytes: int
    is_dir:     bool
    is_link:    bool
    modified:   str
    file_type:  str     # 'DIR', 'LINK', 'MP4', 'TXT', '—', …
    tree_id:    str = ""

    # ── Properties ────────────────────────────────────────────────────

    @property
    def icon(self) -> str:
        if self.is_link: return "🔗"
        if self.is_dir:  return "📁"
        return "📄"

    def human_size(self) -> str:
        if self.is_dir:
            return ""
        b = self.size_bytes
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if b < 1024:
                return f"{b:.0f} {unit}"
            if unit != "TB":
                b /= 1024
        return f"{b:.1f} TB"

    # ── Factory helpers ────────────────────────────────────────────────

    @staticmethod
    def _detect_type(name: str, is_dir: bool, is_link: bool) -> str:
        if is_link: return "LINK"
        if is_dir:  return "DIR"
        ext = Path(name).suffix.lstrip(".")
        return ext.upper() if ext else "—"

    @classmethod
    def from_ls_line(cls, name: str, size_raw: str,
                     is_dir: bool, is_link: bool, modified: str) -> "FileItem":
        try:
            size_bytes = int(size_raw) if size_raw.isdigit() else 0
        except Exception:
            size_bytes = 0
        return cls(name=name, size_raw=size_raw, size_bytes=size_bytes,
                   is_dir=is_dir, is_link=is_link, modified=modified,
                   file_type=cls._detect_type(name, is_dir, is_link))

    @classmethod
    def from_direntry(cls, entry: os.DirEntry) -> "FileItem":
        is_dir  = entry.is_dir(follow_symlinks=False)
        is_link = entry.is_symlink()
        try:
            stat       = entry.stat(follow_symlinks=False)
            size_bytes = stat.st_size
            modified   = time.strftime("%Y-%m-%d %H:%M",
                                       time.localtime(stat.st_mtime))
        except OSError:
            size_bytes = 0
            modified   = ""
        return cls(name=entry.name, size_raw=str(size_bytes),
                   size_bytes=size_bytes, is_dir=is_dir, is_link=is_link,
                   modified=modified,
                   file_type=cls._detect_type(entry.name, is_dir, is_link))


# ╔══════════════════════════════════════════════════════════════════════╗
# ║                       BASE FILE PANEL                               ║
# ╚══════════════════════════════════════════════════════════════════════╝

class FilePanel(ttk.Frame):
    """Reusable file-browser panel.  Subclasses implement refresh() and
    the action toolbar/context-menu.  Provides:
      • Treeview with sel / name / size / type / modified columns
      • Click-able ☐/☑ checkboxes (col #1)
      • Ctrl+Click / Shift+Click multi-select (native Treeview)
      • 3-state column sorting (Asc → Desc → Default)
      • Drag & drop support (wired by the App after instantiation)
    """

    def __init__(self, parent, panel_label: str,
                 log_fn: Callable[[str], None], **kwargs):
        super().__init__(parent, **kwargs)
        self._log        = log_fn
        self.panel_label = panel_label
        self.current_path:  str            = ""
        self.raw_items:     List[FileItem] = []
        self.items:         List[FileItem] = []
        self.sort_state                    = {"col": None, "dir": None}
        self._checked_ids: set             = set()
        # Drag state
        self._drag_source_id: Optional[str]   = None
        self._drag_start_xy:  Tuple[int, int]  = (0, 0)
        self._drag_active:    bool             = False
        # Set by App after construction
        self._is_in_drop_target: Callable = lambda _: False
        self._drag_target_cb:    Optional[Callable] = None

        self._build_panel()

    # ── Panel scaffold ─────────────────────────────────────────────────

    def _build_panel(self):
        # Header row
        hdr = ttk.Frame(self)
        hdr.pack(fill=tk.X, padx=2, pady=2)
        ttk.Label(hdr, text=self.panel_label,
                  font=("Arial", 11, "bold")).pack(side=tk.LEFT)
        nav = ttk.Frame(hdr)
        nav.pack(side=tk.RIGHT)
        for txt, cmd in (("⬆", self.go_up), ("🏠", self.go_home),
                         ("🔄", self.refresh)):
            ttk.Button(nav, text=txt, width=3, command=cmd).pack(
                side=tk.LEFT, padx=1)

        # Path entry
        self.path_var = tk.StringVar()
        pe = ttk.Entry(self, textvariable=self.path_var)
        pe.pack(fill=tk.X, padx=2, pady=2)
        pe.bind("<Return>", lambda _: self.go_to_path(self.path_var.get()))

        # Subclass toolbar
        toolbar = ttk.Frame(self)
        toolbar.pack(fill=tk.X, padx=2, pady=1)
        self._build_toolbar(toolbar)

        # Treeview container
        tree_wrap = ttk.Frame(self)
        tree_wrap.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        self._build_treeview(tree_wrap)

        # Status bar & empty footer area (selalu dapat diklik untuk deselect & context menu walau file ribuan)
        status_frame = ttk.Frame(self)
        status_frame.pack(fill=tk.X, padx=4, pady=2)

        self.status_var = tk.StringVar(value="0 items")
        self.status_lbl = ttk.Label(status_frame, textvariable=self.status_var,
                                    font=("Arial", 8), foreground="gray", cursor="hand2")
        self.status_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Klik pada status bar / footer berfungsi sebagai klik area kosong permanen
        self.status_lbl.bind("<Button-1>", lambda _: self._on_empty_area_click())
        self.status_lbl.bind("<Button-3>", self._on_right_click)
        status_frame.bind("<Button-1>", lambda _: self._on_empty_area_click())
        status_frame.bind("<Button-3>", self._on_right_click)

    def _on_empty_area_click(self):
        """Deselect item fokus saat area kosong/status bar diklik."""
        self.tree.selection_remove(self.tree.selection())

    def _build_toolbar(self, parent: ttk.Frame):
        """Override in subclass."""

    def _build_treeview(self, parent: ttk.Frame):
        cols = ("sel", "name", "size", "type", "modified")
        self.tree = ttk.Treeview(parent, columns=cols,
                                 show="headings", selectmode="extended")

        # Visual highlight tags:
        # 'checked' = item yang dicentang checkbox (Biru Terang tegas terpilih)
        # 'normal'  = item biasa yang tidak dicentang
        self.tree.tag_configure("checked", background="#0078d4", foreground="#ffffff")
        self.tree.tag_configure("normal",  background="#2b2b2b", foreground="#e8e8e8")

        self.tree.heading("sel",      text="☐",
                          command=self._toggle_all_checkboxes)
        self.tree.heading("name",     text="Name",
                          command=lambda: self.toggle_sort("name"))
        self.tree.heading("size",     text="Size",
                          command=lambda: self.toggle_sort("size"))
        self.tree.heading("type",     text="Type",
                          command=lambda: self.toggle_sort("type"))
        self.tree.heading("modified", text="Modified",
                          command=lambda: self.toggle_sort("modified"))

        self.tree.column("sel",      width=32,  minwidth=32,
                         anchor="center", stretch=False)
        self.tree.column("name",     width=260, minwidth=120)
        self.tree.column("size",     width=78,  minwidth=55,
                         anchor="e",      stretch=False)
        self.tree.column("type",     width=58,  minwidth=40,
                         anchor="center", stretch=False)
        self.tree.column("modified", width=132, minwidth=90)

        vsb = ttk.Scrollbar(parent, orient=tk.VERTICAL,   command=self.tree.yview)
        hsb = ttk.Scrollbar(parent, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        parent.grid_rowconfigure(0, weight=1)
        parent.grid_columnconfigure(0, weight=1)

        # Events
        self.tree.bind("<Button-1>",        self._on_button_press)
        self.tree.bind("<B1-Motion>",        self._on_drag_motion)
        self.tree.bind("<ButtonRelease-1>",  self._on_button_release)
        self.tree.bind("<Double-1>",         self._on_double_click)
        self.tree.bind("<Button-3>",         self._on_right_click)

        # Context menu
        self._ctx = tk.Menu(self.tree, tearoff=0)
        self._build_context_menu(self._ctx)

        # Keyboard shortcuts
        self.tree.bind("<Control-c>", lambda _: self._copy_selected())
        self.tree.bind("<Control-x>", lambda _: self._cut_selected())
        self.tree.bind("<Control-v>", lambda _: self._paste_selected())
        self.tree.bind("<Control-a>", lambda _: (self.select_all(), "break"))
        self.tree.bind("<Escape>",    lambda _: (self.deselect_all(), self._on_empty_area_click()))
        self.tree.bind("<Delete>",    lambda _: self._delete_selected())

        # Bind area kosong di wrapper frame
        parent.bind("<Button-1>", lambda _: self._on_empty_area_click())
        parent.bind("<Button-3>", self._on_right_click)

    def _build_context_menu(self, menu: tk.Menu):
        """Override in subclass."""

    # ── Clipboard Hooks ───────────────────────────────────────────────

    def _copy_selected(self):
        checked = self.get_checked_items()
        if not checked:
            messagebox.showwarning("Warning", "Pilih item dulu!", parent=self)
            return
        if hasattr(self, "_on_copy_cb") and self._on_copy_cb:
            self._on_copy_cb(self, self.current_path, checked)

    def _cut_selected(self):
        checked = self.get_checked_items()
        if not checked:
            messagebox.showwarning("Warning", "Pilih item dulu!", parent=self)
            return
        if hasattr(self, "_on_cut_cb") and self._on_cut_cb:
            self._on_cut_cb(self, self.current_path, checked)

    def _paste_selected(self):
        if hasattr(self, "_on_paste_cb") and self._on_paste_cb:
            self._on_paste_cb(self, self.current_path)

    def _delete_selected(self):
        """Override in subclass."""

    # ── Checkbox & Highlight ──────────────────────────────────────────

    def _on_button_press(self, event):
        col     = self.tree.identify_column(event.x)
        item_id = self.tree.identify_row(event.y)

        # Klik di area kosong (di bawah item) -> bersihkan seleksi
        if not item_id:
            self.tree.selection_remove(self.tree.selection())
            self._drag_source_id = None
            self._drag_active    = False
            return

        # Prepare drag state
        self._drag_source_id = item_id
        self._drag_start_xy  = (event.x_root, event.y_root)
        self._drag_active    = False

        # Checkbox column → toggle and stop
        if col == "#1":
            self._toggle_checkbox(item_id)
            self._drag_source_id = None
            return "break"

    def _toggle_checkbox(self, item_id: str):
        if item_id in self._checked_ids:
            self._checked_ids.discard(item_id)
            cb = CHECKBOX_OFF
            tag = "normal"
        else:
            self._checked_ids.add(item_id)
            cb = CHECKBOX_ON
            tag = "checked"
        vals = list(self.tree.item(item_id, "values"))
        if vals:
            vals[0] = cb
            self.tree.item(item_id, values=vals, tags=(tag,))
        self._update_status_bar()

    def _toggle_all_checkboxes(self):
        children = self.tree.get_children()
        if children and all(c in self._checked_ids for c in children):
            self.deselect_all()
        else:
            self.select_all()

    def select_all(self):
        for iid in self.tree.get_children():
            self._checked_ids.add(iid)
            vals = list(self.tree.item(iid, "values"))
            if vals:
                vals[0] = CHECKBOX_ON
                self.tree.item(iid, values=vals, tags=("checked",))
        self._update_status_bar()

    def deselect_all(self):
        for iid in list(self._checked_ids):
            vals = list(self.tree.item(iid, "values"))
            if vals:
                vals[0] = CHECKBOX_OFF
                self.tree.item(iid, values=vals, tags=("normal",))
        self._checked_ids.clear()
        self._update_status_bar()

    def _update_status_bar(self):
        n = len(self.items)
        n_chk = len(self._checked_ids)
        if n_chk > 0:
            self.status_var.set(f"📊 {n} item{'s' if n != 1 else ''}  ({n_chk} selected) • [Esc / Klik bar ini untuk unselect]")
        else:
            self.status_var.set(f"📁 {n} item{'s' if n != 1 else ''}")

    def get_checked_items(self) -> List[FileItem]:
        result = [fi for fi in self.items if fi.tree_id in self._checked_ids]
        if not result:
            # Fallback: highlighted Treeview selection
            sel = set(self.tree.selection())
            result = [fi for fi in self.items if fi.tree_id in sel]
        return result

    # ── Drag & Drop ───────────────────────────────────────────────────

    def _on_drag_motion(self, event):
        if not self._drag_source_id:
            return
        dx = abs(event.x_root - self._drag_start_xy[0])
        dy = abs(event.y_root - self._drag_start_xy[1])
        if (dx > 5 or dy > 5) and not self._drag_active:
            self._drag_active = True
            self.tree.config(cursor="fleur")

    def _on_button_release(self, event):
        self.tree.config(cursor="")
        if self._drag_active and self._drag_source_id:
            target = self.winfo_toplevel().winfo_containing(
                event.x_root, event.y_root)
            if target and self._is_in_drop_target(target):
                # Auto-check dragged item if not already
                if self._drag_source_id not in self._checked_ids:
                    self._toggle_checkbox(self._drag_source_id)
                if self._drag_target_cb:
                    self._drag_target_cb()
        self._drag_source_id = None
        self._drag_active    = False

    # ── Sorting ───────────────────────────────────────────────────────

    def toggle_sort(self, col: str):
        s = self.sort_state
        if s["col"] != col:
            s["col"], s["dir"] = col, "asc"
        elif s["dir"] == "asc":
            s["dir"] = "desc"
        else:
            s["col"], s["dir"] = None, None
        self._apply_sort_and_render()

    def _apply_sort_and_render(self):
        col, direction = self.sort_state["col"], self.sort_state["dir"]

        if not col:
            self.items = list(self.raw_items)
        else:
            rev = (direction == "desc")
            key_map = {
                "name":     lambda x: x.name.lower(),
                "size":     lambda x: x.size_bytes,
                "type":     lambda x: (0 if x.is_dir else 1, x.file_type.lower()),
                "modified": lambda x: x.modified,
            }
            key = key_map.get(col, key_map["name"])
            dirs  = sorted([x for x in self.raw_items if x.is_dir],      key=key, reverse=rev)
            files = sorted([x for x in self.raw_items if not x.is_dir],  key=key, reverse=rev)
            self.items = dirs + files

        self._render_items()
        self._refresh_headers()

    def _refresh_headers(self):
        col, dir_ = self.sort_state["col"], self.sort_state["dir"]
        arrow = {"asc": " ▲", "desc": " ▼"}.get(dir_, "")
        mapping = {"name": "Name", "size": "Size",
                   "type": "Type", "modified": "Modified"}
        for c, base in mapping.items():
            self.tree.heading(c, text=base + (arrow if col == c else ""))

    def _render_items(self):
        # Preserve checked state by name
        prev_checked = {fi.name for fi in self.items if fi.tree_id in self._checked_ids}
        self._checked_ids.clear()
        for iid in self.tree.get_children():
            self.tree.delete(iid)

        for fi in self.items:
            is_chk = fi.name in prev_checked
            cb = CHECKBOX_ON if is_chk else CHECKBOX_OFF
            tag = "checked" if is_chk else "normal"
            iid = self.tree.insert("", "end", values=(
                cb,
                f"{fi.icon}  {fi.name}",
                fi.human_size(),
                fi.file_type,
                fi.modified,
            ), tags=(tag,))
            fi.tree_id = iid
            if is_chk:
                self._checked_ids.add(iid)

        self._update_status_bar()

    # ── Navigation ────────────────────────────────────────────────────

    def go_to_path(self, path: str):
        path = path.strip()
        if path:
            self.current_path = path
            self.refresh()

    def go_up(self):
        parent = os.path.dirname(self.current_path.rstrip("/\\"))
        if parent and parent != self.current_path:
            self.go_to_path(parent)

    def go_home(self):
        self.go_to_path(self._home_path())

    def _home_path(self) -> str:
        return "/"

    def refresh(self):
        """Override in subclass."""

    # ── Events ────────────────────────────────────────────────────────

    def _on_double_click(self, event):
        col     = self.tree.identify_column(event.x)
        item_id = self.tree.identify_row(event.y)
        if col == "#1" or not item_id:
            return
        fi = self._item_by_id(item_id)
        if fi and fi.is_dir:
            self.go_to_path(os.path.join(self.current_path, fi.name))

    def _on_right_click(self, event):
        item_id = self.tree.identify_row(event.y)
        if item_id:
            if item_id not in self.tree.selection():
                self.tree.selection_set(item_id)
        else:
            self.tree.selection_remove(self.tree.selection())

        try:
            self._ctx.tk_popup(event.x_root, event.y_root)
        finally:
            self._ctx.grab_release()

    # ── Helpers ───────────────────────────────────────────────────────

    def _item_by_id(self, tree_id: str) -> Optional[FileItem]:
        return next((fi for fi in self.items if fi.tree_id == tree_id), None)


# ╔══════════════════════════════════════════════════════════════════════╗
# ║                       ANDROID PANEL                                 ║
# ╚══════════════════════════════════════════════════════════════════════╝

class AndroidPanel(FilePanel):

    def __init__(self, parent,
                 device_id_fn:    Callable[[], Optional[str]],
                 run_adb_fn:      Callable,
                 set_progress_fn: Callable,
                 log_fn:          Callable, **kwargs):
        self._get_device_id = device_id_fn
        self._run_adb       = run_adb_fn
        self._set_progress  = set_progress_fn
        super().__init__(parent, "📱  Android", log_fn, **kwargs)
        self.current_path = "/storage/emulated/0"
        self.path_var.set(self.current_path)

    def _home_path(self): return "/storage/emulated/0"

    # ── Toolbar ───────────────────────────────────────────────────────

    def _build_toolbar(self, parent):
        for txt, cmd in (
            ("📂 Open",   self._open_selected),
            ("📁 New",    self._new_folder),
            ("📋 Copy",   self._copy_selected),
            ("✂️ Cut",    self._cut_selected),
            ("📌 Paste",  self._paste_selected),
            ("✏️ Rename", self._rename_selected),
            ("🗑 Delete",  self._delete_selected),
            ("☑ All",     self.select_all),
            ("☐ None",    self.deselect_all),
        ):
            ttk.Button(parent, text=txt, command=cmd,
                       width=8).pack(side=tk.LEFT, padx=1, pady=1)

    def _build_context_menu(self, m):
        m.add_command(label="Open",          command=self._open_selected)
        m.add_command(label="New Folder",    command=self._new_folder)
        m.add_separator()
        m.add_command(label="Copy", command=self._copy_selected)
        m.add_command(label="Cut",  command=self._cut_selected)
        m.add_command(label="Paste",command=self._paste_selected)
        m.add_separator()
        m.add_command(label="Rename",        command=self._rename_selected)
        m.add_command(label="Delete",        command=self._delete_selected)

    # ── Refresh ───────────────────────────────────────────────────────

    def refresh(self):
        if not self._get_device_id():
            self._log("⚠️ No device connected")
            return

        def worker():
            try:
                base_dir = self.current_path.rstrip('/') or '/'
                result = self._run_adb(["shell", f'ls -la "{base_dir}"'])
                if result.returncode != 0:
                    result = self._run_adb(["shell", "ls", "-la", base_dir])
                if result.returncode != 0:
                    result = self._run_adb(["shell", f'ls "{base_dir}"'])
                    if result.returncode != 0:
                        self._log(f"❌ Cannot read: {result.stderr[:80]}")
                        return
                    names = [n.strip() for n in result.stdout.splitlines()
                             if n.strip() not in ("", ".", "..")]
                    raw = [FileItem.from_ls_line(n, "0", False, False, "")
                           for n in names]
                else:
                    raw = self._parse_ls(result.stdout)

                self.raw_items = raw
                self.winfo_toplevel().after(
                    0, lambda: self.path_var.set(self.current_path))
                self.winfo_toplevel().after(0, self._apply_sort_and_render)
                self._log(f"📂 {len(raw)} items — {self.current_path}")
            except Exception as exc:
                self._log(f"❌ Android refresh: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _parse_ls(output: str) -> List[FileItem]:
        items = []
        # ISO timestamp pattern: [perms] [links] [owner] [group] <size> YYYY-MM-DD HH:MM <name>
        iso_re = re.compile(
            r'^([bcdlsp-][rwxstST-]{9})\s+.*?\s+(\d+)\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s+(.+)$'
        )
        # Unix timestamp pattern: [perms] [links] [owner] [group] <size> Mon DD HH:MM/YYYY <name>
        trad_re = re.compile(
            r'^([bcdlsp-][rwxstST-]{9})\s+.*?\s+(\d+)\s+([A-Za-z]{3}\s+\d+\s+(?:\d{2}:\d{2}|\d{4}))\s+(.+)$'
        )

        for line in output.splitlines():
            line = line.strip()
            if not line or line.startswith("total"):
                continue

            m = iso_re.match(line)
            if not m:
                m = trad_re.match(line)

            if m:
                perm, size_raw, modified, name = m.group(1), m.group(2), m.group(3), m.group(4)
                name = name.strip()
                if name in (".", ".."):
                    continue
                is_dir  = perm.startswith("d")
                is_link = perm.startswith("l")
                if is_link and " -> " in name:
                    name = name.split(" -> ")[0].strip()
                items.append(FileItem.from_ls_line(
                    name, size_raw, is_dir, is_link, modified))
            else:
                parts = line.split()
                if len(parts) >= 8 and len(parts[0]) >= 10:
                    perm = parts[0]
                    size_raw = "0"
                    for i in range(len(parts)-3, 0, -1):
                        if parts[i].isdigit():
                            size_raw = parts[i]
                            name = " ".join(parts[i+3:]) if i+3 < len(parts) else parts[-1]
                            modified = f"{parts[i+1]} {parts[i+2]}" if i+2 < len(parts) else ""
                            break
                    else:
                        name = parts[-1]
                        modified = ""
                    if name not in (".", ".."):
                        is_dir = perm.startswith("d")
                        is_link = perm.startswith("l")
                        if is_link and " -> " in name:
                            name = name.split(" -> ")[0].strip()
                        items.append(FileItem.from_ls_line(
                            name, size_raw, is_dir, is_link, modified))
        return items

    # ── Actions ───────────────────────────────────────────────────────

    def _open_selected(self):
        sel = self.tree.selection()
        if sel:
            fi = self._item_by_id(sel[0])
            if fi and fi.is_dir:
                self.go_to_path(f"{self.current_path.rstrip('/')}/{fi.name}")

    def _new_folder(self):
        name = simpledialog.askstring("New Folder", "Folder name:", parent=self)
        if not name:
            return

        def w():
            base_dir = self.current_path.rstrip('/')
            target_p = f"{base_dir}/{name}"
            r = self._run_adb(["shell", f'mkdir -p "{target_p}"'])
            if r.returncode != 0:
                r = self._run_adb(["shell", "mkdir", "-p", target_p])
            if r.returncode == 0:
                self._log(f"📁 Created: {name}")
                self.winfo_toplevel().after(0, self.refresh)
            else:
                self._log(f"❌ mkdir: {r.stderr}")
        threading.Thread(target=w, daemon=True).start()

    def _rename_selected(self):
        checked = self.get_checked_items()
        if not checked:
            messagebox.showwarning("Warning", "Pilih item dulu!", parent=self)
            return
        fi = checked[0]
        new = simpledialog.askstring("Rename", f"Rename '{fi.name}':",
                                     initialvalue=fi.name, parent=self)
        if not new or new == fi.name:
            return

        def w():
            base_dir = self.current_path.rstrip('/')
            old_p = f"{base_dir}/{fi.name}"
            new_p = f"{base_dir}/{new}"
            r = self._run_adb(["shell", f'mv "{old_p}" "{new_p}"'])
            if r.returncode != 0:
                r = self._run_adb(["shell", "mv", old_p, new_p])
            self._log(f"✏️ Renamed: {fi.name} → {new}" if r.returncode == 0
                      else f"❌ Rename: {r.stderr}")
            if r.returncode == 0:
                self.winfo_toplevel().after(0, self.refresh)
        threading.Thread(target=w, daemon=True).start()

    def _delete_selected(self):
        checked = self.get_checked_items()
        if not checked:
            messagebox.showwarning("Warning", "Pilih item dulu!", parent=self)
            return
        preview = ", ".join(fi.name for fi in checked[:3])
        if len(checked) > 3:
            preview += f" (+{len(checked)-3} more)"
        if not messagebox.askyesno("Confirm Delete",
                                   f"Hapus {len(checked)} item(s)?\n{preview}",
                                   parent=self):
            return
        self._set_progress(True, status="Deleting…", mode="indeterminate")

        def w():
            try:
                base_dir = self.current_path.rstrip('/')
                for fi in checked:
                    target_p = f"{base_dir}/{fi.name}"
                    r = self._run_adb(["shell", f'rm -rf "{target_p}"'])
                    if r.returncode != 0:
                        r = self._run_adb(["shell", "rm", "-rf", target_p])
                    self._log(f"🗑 Deleted: {fi.name}" if r.returncode == 0
                              else f"❌ Delete {fi.name}: {r.stderr}")
                self.deselect_all()
                self.winfo_toplevel().after(0, self.refresh)
            finally:
                self._set_progress(False)
        threading.Thread(target=w, daemon=True).start()


# ╔══════════════════════════════════════════════════════════════════════╗
# ║                         PC PANEL                                    ║
# ╚══════════════════════════════════════════════════════════════════════╝

class PCPanel(FilePanel):

    def __init__(self, parent,
                 set_progress_fn: Callable,
                 log_fn:          Callable, **kwargs):
        self._set_progress = set_progress_fn
        super().__init__(parent, "💻  PC", log_fn, **kwargs)
        self.current_path = str(Path.home())
        self.path_var.set(self.current_path)

    def _home_path(self): return str(Path.home())

    # ── Toolbar ───────────────────────────────────────────────────────

    def _build_toolbar(self, parent):
        for txt, cmd in (
            ("📂 Open",   self._open_selected),
            ("📁 New",    self._new_folder),
            ("📋 Copy",   self._copy_selected),
            ("✂️ Cut",    self._cut_selected),
            ("📌 Paste",  self._paste_selected),
            ("✏️ Rename", self._rename_selected),
            ("🗑 Delete",  self._delete_selected),
            ("☑ All",     self.select_all),
            ("☐ None",    self.deselect_all),
        ):
            ttk.Button(parent, text=txt, command=cmd,
                       width=8).pack(side=tk.LEFT, padx=1, pady=1)

    def _build_context_menu(self, m):
        m.add_command(label="Open",          command=self._open_selected)
        m.add_command(label="New Folder",    command=self._new_folder)
        m.add_separator()
        m.add_command(label="Copy", command=self._copy_selected)
        m.add_command(label="Cut",  command=self._cut_selected)
        m.add_command(label="Paste",command=self._paste_selected)
        m.add_separator()
        m.add_command(label="Rename",        command=self._rename_selected)
        m.add_command(label="Delete",        command=self._delete_selected)

    # ── Refresh ───────────────────────────────────────────────────────

    def refresh(self):
        def worker():
            try:
                entries = list(os.scandir(self.current_path))
                raw = []
                for e in entries:
                    if e.name.startswith("."):
                        continue
                    try:
                        raw.append(FileItem.from_direntry(e))
                    except Exception:
                        pass
                self.raw_items = raw
                self.winfo_toplevel().after(
                    0, lambda: self.path_var.set(self.current_path))
                self.winfo_toplevel().after(0, self._apply_sort_and_render)
                self._log(f"💻 {len(raw)} items — {self.current_path}")
            except PermissionError:
                self._log(f"❌ Permission denied: {self.current_path}")
            except Exception as exc:
                self._log(f"❌ PC refresh: {exc}")
        threading.Thread(target=worker, daemon=True).start()

    # ── Actions ───────────────────────────────────────────────────────

    def _open_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        fi = self._item_by_id(sel[0])
        if not fi:
            return
        full = os.path.join(self.current_path, fi.name)
        if fi.is_dir:
            self.go_to_path(full)
        else:
            try:
                subprocess.Popen(["xdg-open", full])
            except Exception:
                pass

    def _new_folder(self):
        name = simpledialog.askstring("New Folder", "Folder name:", parent=self)
        if not name:
            return
        try:
            os.makedirs(os.path.join(self.current_path, name), exist_ok=True)
            self._log(f"📁 Created: {name}")
            self.winfo_toplevel().after(0, self.refresh)
        except Exception as exc:
            self._log(f"❌ mkdir: {exc}")

    def _rename_selected(self):
        checked = self.get_checked_items()
        if not checked:
            messagebox.showwarning("Warning", "Pilih item dulu!", parent=self)
            return
        fi = checked[0]
        new = simpledialog.askstring("Rename", f"Rename '{fi.name}':",
                                     initialvalue=fi.name, parent=self)
        if not new or new == fi.name:
            return
        try:
            os.rename(os.path.join(self.current_path, fi.name),
                      os.path.join(self.current_path, new))
            self._log(f"✏️ Renamed: {fi.name} → {new}")
            self.winfo_toplevel().after(0, self.refresh)
        except Exception as exc:
            self._log(f"❌ Rename: {exc}")

    def _delete_selected(self):
        checked = self.get_checked_items()
        if not checked:
            messagebox.showwarning("Warning", "Pilih item dulu!", parent=self)
            return
        if not messagebox.askyesno("Confirm Delete",
                                   f"Hapus {len(checked)} item(s)?",
                                   parent=self):
            return
        for fi in checked:
            path = os.path.join(self.current_path, fi.name)
            try:
                shutil.rmtree(path) if fi.is_dir else os.remove(path)
                self._log(f"🗑 Deleted: {fi.name}")
            except Exception as exc:
                self._log(f"❌ Delete {fi.name}: {exc}")
        self.winfo_toplevel().after(0, self.refresh)


# ╔══════════════════════════════════════════════════════════════════════╗
# ║                     MAIN APPLICATION                                ║
# ╚══════════════════════════════════════════════════════════════════════╝

class ADBFileManagerApp:
    """Orchestrates AndroidPanel + PCPanel, ADB connectivity, and transfers."""

    def __init__(self, root: tk.Tk):
        self.root          = root
        self.device_id:    Optional[str] = None
        self.is_operating: bool          = False
        self._current_transfer_proc: Optional[subprocess.Popen] = None
        self._cancel_requested: bool = False

        # Clipboard State (Copy / Cut / Paste)
        self._clipboard_items: List[FileItem] = []
        self._clipboard_action: Optional[str] = None  # "copy" | "cut"
        self._clipboard_source_panel: Optional[FilePanel] = None
        self._clipboard_source_dir: str = ""

        root.title("ADB File Manager")
        root.geometry("1440x780")
        root.minsize(920, 520)
        root.resizable(True, True)

        self._setup_style()
        self._build_ui()
        self._check_adb()

    # ── Style ─────────────────────────────────────────────────────────

    def _setup_style(self):
        s = ttk.Style()
        s.theme_use("clam")
        BG, FG = "#2b2b2b", "#e8e8e8"
        ACCENT = "#0078d4"       # Biru aksen tombol transfer & checked
        CLICK_FOCUS = "#404850"  # Abu-abu slate untuk baris yang HANYA diklik

        s.configure("Treeview",
                    background=BG, foreground=FG,
                    fieldbackground=BG, rowheight=22)
        s.map("Treeview",
              background=[("selected", CLICK_FOCUS)],
              foreground=[("selected", "#ffffff")])
        s.configure("TFrame",  background=BG)
        s.configure("TLabel",  background=BG, foreground=FG)
        s.configure("TEntry",  fieldbackground="#3c3c3c", foreground=FG)
        s.configure("TButton", padding=3)
        s.configure("Transfer.TButton",
                    background=ACCENT, foreground="#ffffff", padding=(8, 4))
        s.configure("Cancel.TButton",
                    background="#d32f2f", foreground="#ffffff", padding=(6, 2))

    # ── Build UI ──────────────────────────────────────────────────────

    def _build_ui(self):
        main = ttk.Frame(self.root)
        main.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        self._build_device_bar(main)

        paned = ttk.PanedWindow(main, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, pady=4)

        self.android = AndroidPanel(
            paned,
            device_id_fn=lambda: self.device_id,
            run_adb_fn=self._run_adb,
            set_progress_fn=self._set_progress,
            log_fn=self._log,
        )
        self.pc = PCPanel(
            paned,
            set_progress_fn=self._set_progress,
            log_fn=self._log,
        )
        paned.add(self.android, weight=1)
        paned.add(self.pc,      weight=1)

        # Wire clipboard
        self.android._on_copy_cb  = self._clipboard_copy
        self.android._on_cut_cb   = self._clipboard_cut
        self.android._on_paste_cb = self._clipboard_paste

        self.pc._on_copy_cb  = self._clipboard_copy
        self.pc._on_cut_cb   = self._clipboard_cut
        self.pc._on_paste_cb = self._clipboard_paste

        # Wire drag & drop
        self.android._is_in_drop_target = \
            lambda w: self._child_of(w, self.pc)
        self.pc._is_in_drop_target = \
            lambda w: self._child_of(w, self.android)
        self.android._drag_target_cb = self._transfer_android_to_pc
        self.pc._drag_target_cb      = self._transfer_pc_to_android

        self._build_transfer_bar(main)
        self._build_log(main)

    def _build_device_bar(self, parent):
        bar = ttk.Frame(parent)
        bar.pack(fill=tk.X, pady=2)
        ttk.Label(bar, text="Device:").pack(side=tk.LEFT, padx=4)
        self.device_var   = tk.StringVar()
        self.device_combo = ttk.Combobox(bar, textvariable=self.device_var,
                                         width=26, state="readonly")
        self.device_combo.pack(side=tk.LEFT, padx=3)
        self.device_combo.bind("<<ComboboxSelected>>", self._on_device_selected)
        ttk.Button(bar, text="🔄 Refresh",
                   command=self._refresh_devices).pack(side=tk.LEFT, padx=3)
        ttk.Button(bar, text="🔌 Connect",
                   command=self._connect_device).pack(side=tk.LEFT, padx=3)
        self.status_lbl = ttk.Label(bar, text="Status: Disconnected",
                                    foreground="#ff7070")
        self.status_lbl.pack(side=tk.RIGHT, padx=8)

    def _build_transfer_bar(self, parent):
        bar = ttk.Frame(parent)
        bar.pack(fill=tk.X, pady=3)

        btn_box = ttk.Frame(bar)
        btn_box.pack(side=tk.LEFT)

        ttk.Button(btn_box, text="📤  Android → PC",
                   style="Transfer.TButton",
                   command=self._transfer_android_to_pc,
                   width=18).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_box, text="PC → Android  📥",
                   style="Transfer.TButton",
                   command=self._transfer_pc_to_android,
                   width=18).pack(side=tk.LEFT, padx=4)

        # Progress bar container
        self._progress_box = ttk.Frame(bar)
        self._progress_box.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=8)

        lbl_row = ttk.Frame(self._progress_box)
        lbl_row.pack(fill=tk.X)

        self._progress_status = tk.StringVar()
        self._progress_lbl = ttk.Label(lbl_row, textvariable=self._progress_status,
                                       font=("Arial", 9, "bold"))
        self._progress_lbl.pack(side=tk.LEFT)

        self._progress_speed_text = tk.StringVar()
        self._progress_speed_lbl = ttk.Label(lbl_row, textvariable=self._progress_speed_text,
                                             font=("Arial", 9), foreground="#69f0ae")
        self._progress_speed_lbl.pack(side=tk.RIGHT)

        bar_row = ttk.Frame(self._progress_box)
        bar_row.pack(fill=tk.X, pady=(2, 0))

        self.progress_var = tk.DoubleVar()
        self._progress_bar = ttk.Progressbar(bar_row, variable=self.progress_var,
                                             maximum=100)
        self._progress_bar.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self._cancel_btn = ttk.Button(bar_row, text="🛑 Cancel",
                                      command=self.cancel_transfer,
                                      width=9)
        self._cancel_btn.pack(side=tk.RIGHT, padx=(6, 0))

        # Initially hidden
        self._progress_box.pack_forget()

    def _build_log(self, parent):
        frm = ttk.Frame(parent)
        frm.pack(fill=tk.X, pady=3)
        ttk.Label(frm, text="Log:", font=("Arial", 8)).pack(anchor=tk.W)
        self.log_widget = scrolledtext.ScrolledText(
            frm, height=4, bg="#1a1a1a", fg="#00e676",
            font=("Consolas", 8))
        self.log_widget.pack(fill=tk.X)

    # ── ADB ───────────────────────────────────────────────────────────

    def _check_adb(self):
        try:
            subprocess.run(["adb", "version"], capture_output=True, check=True)
            self._log("✅ ADB detected")
            self._refresh_devices()
        except Exception as exc:
            messagebox.showerror("Error", f"ADB not found!\n{exc}")
            self.status_lbl.config(text="ADB Error", foreground="red")

    def _refresh_devices(self):
        try:
            r = subprocess.run(["adb", "devices"], capture_output=True, text=True)
            lines   = r.stdout.strip().splitlines()[1:]
            devices = [l.split("\t")[0] for l in lines
                       if l.strip() and "device" in l]
            self.device_combo["values"] = devices
            if devices:
                self.device_combo.set(devices[0])
                self.device_id = devices[0]
                self.status_lbl.config(
                    text=f"Device: {devices[0]}", foreground="#69f0ae")
                self._log(f"📱 Found: {', '.join(devices)}")
                self._connect_device()
            else:
                self.device_combo.set("")
                self.status_lbl.config(
                    text="Status: No device", foreground="#ff7070")
                self._log("❌ No devices detected")
        except Exception as exc:
            self._log(f"❌ Refresh: {exc}")

    def _on_device_selected(self, _=None):
        self.device_id = self.device_var.get()
        self._connect_device()

    def _connect_device(self):
        if not self.device_id:
            return
        try:
            r = subprocess.run(
                ["adb", "-s", self.device_id, "shell", "echo", "ok"],
                capture_output=True, text=True, timeout=5)
            if "ok" in r.stdout:
                self.status_lbl.config(
                    text=f"✅ {self.device_id}", foreground="#69f0ae")
                self._log(f"✅ Connected: {self.device_id}")
                self.android.refresh()
                self.pc.refresh()
            else:
                self._log(f"❌ Connect failed: {r.stderr[:60]}")
        except Exception as exc:
            self._log(f"❌ Connect: {exc}")

    def _run_adb(self, command: list, timeout: int = 60) -> subprocess.CompletedProcess:
        if not self.device_id:
            raise RuntimeError("No device connected")
        return subprocess.run(
            ["adb", "-s", self.device_id] + command,
            capture_output=True, text=True, timeout=timeout)

    # ── Helpers ───────────────────────────────────────────────────────

    def _log(self, message: str):
        def _do():
            self.log_widget.insert(
                tk.END, f"[{time.strftime('%H:%M:%S')}] {message}\n")
            self.log_widget.see(tk.END)
        self.root.after(0, _do)

    def _set_progress(self, visible: bool, value: float = 0,
                      status: str = "", speed_info: str = "",
                      mode: str = "determinate"):
        def _do():
            if visible:
                self._progress_status.set(status)
                self._progress_speed_text.set(speed_info)
                self._progress_bar.config(mode=mode)
                if mode == "indeterminate":
                    self._progress_bar.start(12)
                else:
                    self._progress_bar.stop()
                    self.progress_var.set(value)
                self._progress_box.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=8)
            else:
                self._progress_bar.stop()
                self._progress_box.pack_forget()
        self.root.after(0, _do)

    @staticmethod
    def _child_of(widget, panel: FilePanel) -> bool:
        w = widget
        while w:
            if w is panel:
                return True
            try:
                w = w.master
            except AttributeError:
                break
        return False

    def cancel_transfer(self):
        """Batalkan proses transfer yang sedang berjalan."""
        if not self.is_operating:
            return
        self._cancel_requested = True
        self._log("🛑 Membatalkan transfer...")
        if self._current_transfer_proc:
            try:
                self._current_transfer_proc.terminate()
            except Exception:
                try:
                    self._current_transfer_proc.kill()
                except Exception:
                    pass

    # ── Clipboard Handlers (Copy, Cut, Paste) ─────────────────────────

    def _clipboard_copy(self, panel: FilePanel, source_dir: str, items: List[FileItem]):
        self._clipboard_items = list(items)
        self._clipboard_action = "copy"
        self._clipboard_source_panel = panel
        self._clipboard_source_dir = source_dir
        self._log(f"📋 Salin {len(items)} item ke clipboard")

    def _clipboard_cut(self, panel: FilePanel, source_dir: str, items: List[FileItem]):
        self._clipboard_items = list(items)
        self._clipboard_action = "cut"
        self._clipboard_source_panel = panel
        self._clipboard_source_dir = source_dir
        self._log(f"✂️ Potong (Cut) {len(items)} item ke clipboard")

    def _clipboard_paste(self, target_panel: FilePanel, target_dir: str):
        if not self._clipboard_items or not self._clipboard_action:
            messagebox.showinfo("Info", "Clipboard kosong! Silakan Copy atau Cut item terlebih dahulu.", parent=self.root)
            return
        if self.is_operating:
            messagebox.showwarning("Busy", "Operasi transfer sedang berjalan!", parent=self.root)
            return

        src_panel = self._clipboard_source_panel
        src_dir   = self._clipboard_source_dir
        action    = self._clipboard_action
        items     = list(self._clipboard_items)

        if src_panel == target_panel:
            # Internal paste di dalam panel yang sama
            if target_panel == self.android:
                self._paste_android_internal(items, action, src_dir, target_dir)
            else:
                self._paste_pc_internal(items, action, src_dir, target_dir)
        else:
            # Cross-panel paste (Android <-> PC)
            is_cut = (action == "cut")
            if src_panel == self.android and target_panel == self.pc:
                self._run_transfer(items, "android_to_pc", src_dir, target_dir, delete_source=is_cut)
            elif src_panel == self.pc and target_panel == self.android:
                self._run_transfer(items, "pc_to_android", src_dir, target_dir, delete_source=is_cut)

        if action == "cut":
            self._clipboard_items = []
            self._clipboard_action = None

    def _paste_android_internal(self, items: List[FileItem], action: str, src_dir: str, dst_dir: str):
        def w():
            self._set_progress(True, status=f"{'Memindahkan' if action == 'cut' else 'Menyalin'} item…", mode="indeterminate")
            try:
                src_base = src_dir.rstrip('/')
                dst_base = dst_dir.rstrip('/')
                for fi in items:
                    sp = f"{src_base}/{fi.name}"
                    dp = f"{dst_base}/{fi.name}"

                    if src_base == dst_base:
                        if action == "cut":
                            self._log(f"ℹ️ {fi.name} sudah berada di folder tujuan.")
                            continue
                        else:
                            # Jika copy di folder yang sama, buat nama unik
                            dp = f"{dst_base}/copy_{fi.name}"

                    if action == "cut":
                        # Coba mv langsung, jika beda filesystem fallback ke cp + rm
                        cmd = f'if mv "{sp}" "{dp}" 2>/dev/null; then echo SUCCESS; elif cp -r "{sp}" "{dp}" 2>/dev/null && rm -rf "{sp}" 2>/dev/null; then echo SUCCESS; else echo FAIL; fi'
                        r = self._run_adb(["shell", cmd])
                        if "SUCCESS" in r.stdout:
                            self._log(f"✂️ Pindah: {fi.name} → {dst_dir}")
                        else:
                            r_err = self._run_adb(["shell", "mv", sp, dp])
                            if r_err.returncode == 0 and not r_err.stderr.strip():
                                self._log(f"✂️ Pindah: {fi.name} → {dst_dir}")
                            else:
                                self._log(f"❌ Cut error {fi.name}: {r_err.stderr or r.stdout or 'Gagal'}")
                    else:
                        cmd = f'if cp -r "{sp}" "{dp}" 2>/dev/null; then echo SUCCESS; else echo FAIL; fi'
                        r = self._run_adb(["shell", cmd])
                        if "SUCCESS" in r.stdout:
                            self._log(f"📋 Salin: {fi.name} → {dst_dir}")
                        else:
                            r_err = self._run_adb(["shell", "cp", "-r", sp, dp])
                            if r_err.returncode == 0 and not r_err.stderr.strip():
                                self._log(f"📋 Salin: {fi.name} → {dst_dir}")
                            else:
                                self._log(f"❌ Copy error {fi.name}: {r_err.stderr or r.stdout or 'Gagal'}")

                self.android.deselect_all()
                self.root.after(0, self.android.refresh)
            finally:
                self._set_progress(False)
        threading.Thread(target=w, daemon=True).start()

    def _paste_pc_internal(self, items: List[FileItem], action: str, src_dir: str, dst_dir: str):
        def w():
            self._set_progress(True, status=f"{'Memindahkan' if action == 'cut' else 'Menyalin'} item…", mode="indeterminate")
            try:
                for fi in items:
                    sp = os.path.join(src_dir, fi.name)
                    dp = os.path.join(dst_dir, fi.name)

                    if os.path.abspath(src_dir) == os.path.abspath(dst_dir):
                        if action == "cut":
                            self._log(f"ℹ️ {fi.name} sudah berada di folder tujuan.")
                            continue
                        else:
                            dp = os.path.join(dst_dir, f"copy_{fi.name}")

                    try:
                        if action == "cut":
                            shutil.move(sp, dp)
                            self._log(f"✂️ Pindah: {fi.name} → {dst_dir}")
                        else:
                            if fi.is_dir:
                                shutil.copytree(sp, dp, dirs_exist_ok=True)
                            else:
                                shutil.copy2(sp, dp)
                            self._log(f"📋 Salin: {fi.name} → {dst_dir}")
                    except Exception as exc:
                        self._log(f"❌ Paste error {fi.name}: {exc}")

                self.pc.deselect_all()
                self.root.after(0, self.pc.refresh)
            finally:
                self._set_progress(False)
        threading.Thread(target=w, daemon=True).start()

    # ── Transfer ──────────────────────────────────────────────────────

    def _transfer_android_to_pc(self):
        items = self.android.get_checked_items()
        if not items:
            messagebox.showinfo("Info", "Pilih file di panel Android terlebih dahulu.")
            return
        if self.is_operating:
            messagebox.showwarning("Busy", "Transfer sedang berjalan!")
            return
        self._run_transfer(items, "android_to_pc",
                           self.android.current_path, self.pc.current_path)

    def _transfer_pc_to_android(self):
        items = self.pc.get_checked_items()
        if not items:
            messagebox.showinfo("Info", "Pilih file di panel PC terlebih dahulu.")
            return
        if self.is_operating:
            messagebox.showwarning("Busy", "Transfer sedang berjalan!")
            return
        self._run_transfer(items, "pc_to_android",
                           self.pc.current_path, self.android.current_path)

    def _run_transfer(self, items: List[FileItem], direction: str,
                      src_dir: str, dst_dir: str, delete_source: bool = False):
        self.is_operating = True
        total_items = len(items)
        label = ("📤 Android→PC" if direction == "android_to_pc" else "📥 PC→Android")

        # Hitung total bytes semua item (jika diketahui)
        def get_item_size(fi: FileItem) -> int:
            if direction == "pc_to_android":
                local_path = os.path.join(src_dir, fi.name)
                if os.path.exists(local_path) and not fi.is_dir:
                    try:
                        return os.path.getsize(local_path)
                    except OSError:
                        pass
            return fi.size_bytes

        item_sizes = [get_item_size(fi) for fi in items]
        total_all_bytes = sum(item_sizes)

        pct_re = re.compile(r"\[\s*(\d+)%\]|(\d+)%")
        speed_re = re.compile(r"(\d+(?:\.\d+)?\s*(?:[KkMmGg]?[Bb]/s|[KkMmGg]B/s|[KkMmGg]b/s))")

        def build_cmd(fi: FileItem) -> list:
            if direction == "android_to_pc":
                return ["adb", "-s", self.device_id, "pull",
                        f"{src_dir}/{fi.name}",
                        os.path.join(dst_dir, fi.name)]
            return ["adb", "-s", self.device_id, "push",
                    os.path.join(src_dir, fi.name),
                    f"{dst_dir}/{fi.name}"]

        def worker():
            self._cancel_requested = False
            try:
                # Cek jika ada ukuran 0 pada Android -> PC, coba query stat
                for i, fi in enumerate(items):
                    if direction == "android_to_pc" and item_sizes[i] == 0 and not fi.is_dir:
                        try:
                            r_stat = self._run_adb(["shell", "stat", "-c", "%s", f"{src_dir}/{fi.name}"], timeout=3)
                            if r_stat.returncode == 0 and r_stat.stdout.strip().isdigit():
                                item_sizes[i] = int(r_stat.stdout.strip())
                        except Exception:
                            pass
                total_bytes_calc = sum(item_sizes)

                bytes_transferred_prev = 0

                for idx, fi in enumerate(items):
                    if self._cancel_requested:
                        self._log("⚠️ Transfer dibatalkan oleh pengguna.")
                        break

                    file_total_bytes = item_sizes[idx]
                    dst_file_path = (os.path.join(dst_dir, fi.name)
                                     if direction == "android_to_pc" else None)

                    # Initial progress status
                    base_pct = (bytes_transferred_prev / total_bytes_calc * 100
                                if total_bytes_calc > 0 else (idx / total_items * 100))
                    self._set_progress(
                        True, value=base_pct,
                        status=f"{label}: {fi.name} ({idx+1}/{total_items})",
                        speed_info=f"0% • 0 KB/s",
                        mode="determinate")
                    self._log(f"  → Mulai transfer: {fi.name} ({format_bytes(file_total_bytes)})")

                    # Jalankan subprocess dengan PTY di Linux/macOS agar ADB memancarkan status realtime
                    master_fd = None
                    slave_fd = None
                    if HAS_PTY and os.name != 'nt':
                        master_fd, slave_fd = pty.openpty()
                        proc = subprocess.Popen(
                            build_cmd(fi),
                            stdin=subprocess.DEVNULL,
                            stdout=slave_fd,
                            stderr=slave_fd,
                            close_fds=True
                        )
                        os.close(slave_fd)
                        out_stream = os.fdopen(master_fd, 'rb', buffering=0)
                    else:
                        proc = subprocess.Popen(
                            build_cmd(fi),
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            bufsize=0
                        )
                        out_stream = proc.stdout

                    self._current_transfer_proc = proc

                    transfer_done = threading.Event()
                    active_cur_bytes = [0]
                    active_speed_str = ["0 KB/s"]
                    last_sample = {"time": time.time(), "bytes": 0}

                    # Background poller untuk memantau ukuran file secara realtime
                    def poller():
                        while not transfer_done.is_set() and not self._cancel_requested:
                            cur = None
                            if direction == "android_to_pc" and dst_file_path:
                                if os.path.exists(dst_file_path):
                                    try:
                                        cur = os.path.getsize(dst_file_path)
                                    except OSError:
                                        cur = None
                            elif direction == "pc_to_android":
                                try:
                                    # Query ukuran file target di Android via stat shell
                                    target_android_path = f"{dst_dir.rstrip('/')}/{fi.name}"
                                    r_sz = subprocess.run(
                                        ["adb", "-s", self.device_id, "shell", "stat", "-c", "%s", f'"{target_android_path}"'],
                                        capture_output=True, text=True, timeout=1.5)
                                    out_text = r_sz.stdout.strip()
                                    if out_text.isdigit():
                                        cur = int(out_text)
                                except Exception:
                                    cur = None

                            if cur is not None:
                                now = time.time()
                                dt = now - last_sample["time"]
                                if dt >= 0.2:
                                    db = max(0, cur - last_sample["bytes"])
                                    speed = db / dt if dt > 0 else 0
                                    active_speed_str[0] = format_speed(speed)
                                    last_sample["time"] = now
                                    last_sample["bytes"] = cur
                                active_cur_bytes[0] = cur

                                if file_total_bytes > 0:
                                    fpct = min(100.0, cur / file_total_bytes * 100.0)
                                    overall = min(100.0, (bytes_transferred_prev + cur) / total_bytes_calc * 100.0) if total_bytes_calc > 0 else (idx + fpct/100)/total_items*100
                                    self._set_progress(
                                        True, value=overall,
                                        status=f"{label}: {fi.name} ({idx+1}/{total_items}) - {fpct:.1f}%",
                                        speed_info=f"{format_bytes(cur)} / {format_bytes(file_total_bytes)} • {active_speed_str[0]}",
                                        mode="determinate")

                            time.sleep(0.1 if direction == "android_to_pc" else 0.25)

                    if not fi.is_dir:
                        t_poll = threading.Thread(target=poller, daemon=True)
                        t_poll.start()

                    # Stream reader untuk output ADB (membaca chunk \r dan \n dari TTY/Pipe)
                    raw_buf = b""
                    while not self._cancel_requested and proc.poll() is None:
                        try:
                            chunk = out_stream.read(64)
                            if not chunk:
                                break
                            raw_buf += chunk

                            while b'\r' in raw_buf or b'\n' in raw_buf:
                                ir = raw_buf.find(b'\r')
                                in_ = raw_buf.find(b'\n')
                                idx_split = min(ir if ir != -1 else 999999, in_ if in_ != -1 else 999999)
                                line_bytes = raw_buf[:idx_split]
                                raw_buf = raw_buf[idx_split+1:]
                                line = line_bytes.decode('utf-8', errors='ignore').strip()
                                if not line:
                                    continue

                                # Cari persen dan speed
                                m_pct = pct_re.search(line)
                                m_spd = speed_re.search(line)

                                if m_spd:
                                    active_speed_str[0] = m_spd.group(1).strip()

                                if m_pct:
                                    pct_val = int(m_pct.group(1) or m_pct.group(2))
                                    cur_b = int(file_total_bytes * (pct_val / 100)) if file_total_bytes > 0 else 0
                                    overall = ((bytes_transferred_prev + cur_b) / total_bytes_calc * 100.0
                                               if total_bytes_calc > 0 else (idx + pct_val / 100) / total_items * 100)
                                    self._set_progress(
                                        True, value=overall,
                                        status=f"{label}: {fi.name} ({idx+1}/{total_items}) - {pct_val}%",
                                        speed_info=f"{format_bytes(cur_b)} / {format_bytes(file_total_bytes)} • {active_speed_str[0]}",
                                        mode="determinate")
                                elif m_spd and file_total_bytes > 0:
                                    self._set_progress(
                                        True, value=base_pct,
                                        status=f"{label}: {fi.name} ({idx+1}/{total_items})",
                                        speed_info=f"{format_bytes(active_cur_bytes[0])} / {format_bytes(file_total_bytes)} • {active_speed_str[0]}",
                                        mode="determinate")
                        except Exception:
                            break

                    transfer_done.set()
                    proc.wait()
                    try:
                        out_stream.close()
                    except Exception:
                        pass
                    self._current_transfer_proc = None

                    if self._cancel_requested:
                        self._log(f"🛑 Dibatalkan pada file: {fi.name}")
                        break

                    bytes_transferred_prev += file_total_bytes
                    if proc.returncode == 0:
                        self._log(f"✅ Selesai: {fi.name} ({format_bytes(file_total_bytes)})")
                        # Jika aksi adalah CUT, hapus file sumber setelah transfer sukses
                        if delete_source:
                            try:
                                if direction == "android_to_pc":
                                    self._run_adb(["shell", f'rm -rf "{src_dir.rstrip("/")}/{fi.name}"'])
                                else:
                                    local_src = os.path.join(src_dir, fi.name)
                                    shutil.rmtree(local_src) if fi.is_dir else os.remove(local_src)
                                self._log(f"🗑 Dihapus dari sumber: {fi.name}")
                            except Exception as ex_del:
                                self._log(f"⚠️ Gagal hapus sumber: {ex_del}")
                    else:
                        self._log(f"❌ Gagal: {fi.name}")

                if not self._cancel_requested:
                    self._log(f"🎉 Selesai — {total_items} item(s) berhasil ditransfer ({format_bytes(total_bytes_calc)})")
                dest = (self.pc if direction == "android_to_pc" else self.android)
                self.root.after(0, dest.refresh)
                if delete_source:
                    src_p = (self.android if direction == "android_to_pc" else self.pc)
                    self.root.after(0, src_p.refresh)
            except Exception as exc:
                self._log(f"❌ Transfer error: {exc}")
            finally:
                self._current_transfer_proc = None
                self.is_operating = False
                self._set_progress(False)

        threading.Thread(target=worker, daemon=True).start()


# ╔══════════════════════════════════════════════════════════════════════╗
# ║                        ENTRY POINT                                  ║
# ╚══════════════════════════════════════════════════════════════════════╝

def main():
    root = tk.Tk()
    ADBFileManagerApp(root)
    try:
        root.iconbitmap(default="icon.ico")
    except Exception:
        pass
    root.mainloop()


if __name__ == "__main__":
    main()
