import os
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext
import threading
import time
from pathlib import Path
import re

class ADBFileManagerGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("ADB File Manager GUI")
        self.root.geometry("1200x700")
        self.root.minsize(700, 450)
        self.root.resizable(True, True)

        # Variabel
        self.device_id = None
        self.current_path = "/storage/emulated/0"
        self.items = []
        self.raw_items = []
        self.clipboard = None
        self.clipboard_type = None
        self.debug_mode = True
        self.is_operating = False

        # State Pengurutan (None = original, 'asc' = ascending, 'desc' = descending)
        self.sort_state = {'column': None, 'dir': None}

        # Warna tema
        self.colors = {
            'bg': '#2b2b2b',
            'fg': '#ffffff',
            'select': '#3a3a3a',
            'accent': '#0078d4',
            'success': '#4caf50',
            'danger': '#f44336'
        }

        self.setup_ui()
        self.check_adb_and_devices()

    def setup_ui(self):
        """Setup tampilan GUI"""
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('Treeview',
                       background='#2b2b2b',
                       foreground='white',
                       fieldbackground='#2b2b2b')
        style.map('Treeview', background=[('selected', '#0078d4')])

        # Main container
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Top bar
        top_frame = ttk.Frame(main_frame)
        top_frame.pack(fill=tk.X, pady=5)

        ttk.Label(top_frame, text="Device:").pack(side=tk.LEFT, padx=5)
        self.device_var = tk.StringVar()
        self.device_combo = ttk.Combobox(top_frame, textvariable=self.device_var,
                                        width=30, state='readonly')
        self.device_combo.pack(side=tk.LEFT, padx=5)
        self.device_combo.bind('<<ComboboxSelected>>', self.on_device_selected)

        ttk.Button(top_frame, text="🔄 Refresh Devices",
                  command=self.refresh_devices).pack(side=tk.LEFT, padx=5)
        ttk.Button(top_frame, text="📁 Connect",
                  command=self.connect_device).pack(side=tk.LEFT, padx=5)

        self.status_label = ttk.Label(top_frame, text="Status: Disconnected",
                                     foreground='red')
        self.status_label.pack(side=tk.RIGHT, padx=10)

        # Path bar
        path_frame = ttk.Frame(main_frame)
        path_frame.pack(fill=tk.X, pady=5)

        ttk.Label(path_frame, text="Path:").pack(side=tk.LEFT, padx=5)
        self.path_var = tk.StringVar(value=self.current_path)
        path_entry = ttk.Entry(path_frame, textvariable=self.path_var, width=70)
        path_entry.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        ttk.Button(path_frame, text="Go", command=self.go_to_path).pack(side=tk.LEFT, padx=5)
        ttk.Button(path_frame, text="⬆ Up", command=self.go_up).pack(side=tk.LEFT, padx=5)
        ttk.Button(path_frame, text="🏠 Home", command=self.go_home).pack(side=tk.LEFT, padx=5)

        # Main content
        content_frame = ttk.Frame(main_frame)
        content_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        # File list
        list_frame = ttk.Frame(content_frame)
        list_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        tree_frame = ttk.Frame(list_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        self.tree = ttk.Treeview(tree_frame, columns=('Size', 'Modified'), show='tree headings')
        
        # Heading & event bind untuk sorting
        self.tree.heading('#0', text='Name', command=lambda: self.toggle_sort('name'))
        self.tree.heading('Size', text='Size', command=lambda: self.toggle_sort('size'))
        self.tree.heading('Modified', text='Modified', command=lambda: self.toggle_sort('modified'))
        
        self.tree.column('#0', width=500)
        self.tree.column('Size', width=150)
        self.tree.column('Modified', width=200)

        v_scroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        h_scroll = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=v_scroll.set, xscrollcommand=h_scroll.set)

        self.tree.grid(row=0, column=0, sticky='nsew')
        v_scroll.grid(row=0, column=1, sticky='ns')
        h_scroll.grid(row=1, column=0, sticky='ew')
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        self.tree.bind('<Double-1>', self.on_item_double_click)
        self.tree.bind('<Button-3>', self.show_context_menu)
        self.tree.bind('<<TreeviewSelect>>', self._on_selection_change)

        # Context menu state
        self._context_menu_from_rightclick = False

        # Right panel (Scrollable agar responsif di layar kecil)
        action_frame = ttk.Frame(content_frame, width=220)
        action_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=5)

        action_title = ttk.Label(action_frame, text="Actions", font=('Arial', 12, 'bold'))
        action_title.pack(pady=5, anchor=tk.W)

        # Canvas + Scrollbar untuk menampung tombol action
        canvas = tk.Canvas(action_frame, width=200, bg='#2b2b2b', highlightthickness=0)
        action_scrollbar = ttk.Scrollbar(action_frame, orient=tk.VERTICAL, command=canvas.yview)
        scrollable_btn_frame = ttk.Frame(canvas)

        scrollable_btn_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas_window = canvas.create_window((0, 0), window=scrollable_btn_frame, anchor="nw")
        
        def _on_canvas_configure(event):
            canvas.itemconfig(canvas_window, width=event.width)
        canvas.bind('<Configure>', _on_canvas_configure)
        canvas.configure(yscrollcommand=action_scrollbar.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        action_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        buttons = [
            ('📂 Open', self.open_selected),
            ('📋 Copy', self.copy_selected),
            ('✂️ Cut', self.cut_selected),
            ('📌 Paste', self.paste_selected),
            ('🗑 Delete', self.delete_selected),
            ('🔄 Rename', self.rename_selected),
            ('📁 New Folder', self.create_folder),
            ('📤 Copy to PC', self.copy_to_pc),
            ('📥 Copy from PC', self.copy_from_pc),
            ('🔍 Search', self.search_files),
            ('📊 Storage Info', self.show_storage_info),
            ('🔄 Refresh', self.refresh_current),
            ('🐛 Debug Info', self.show_debug_info)
        ]

        for text, command in buttons:
            btn = ttk.Button(scrollable_btn_frame, text=text, command=command)
            btn.pack(fill=tk.X, pady=2, padx=2)

        # Progress indicator panel (tetap terlihat di bagian kanan bawah atau log)
        self.progress_frame = ttk.Frame(action_frame)
        self.progress_frame.pack(fill=tk.X, pady=5)

        self.progress_status_label = ttk.Label(self.progress_frame, text="", font=('Arial', 9))
        self.progress_status_label.pack(fill=tk.X, pady=1)

        self.progress_var = tk.DoubleVar()
        self.progress = ttk.Progressbar(self.progress_frame, variable=self.progress_var,
                                       maximum=100, length=180)
        self.progress.pack(fill=tk.X, pady=2)
        self.progress.pack_forget()
        self.progress_status_label.pack_forget()

        # Log
        log_frame = ttk.Frame(main_frame)
        log_frame.pack(fill=tk.X, pady=5)

        ttk.Label(log_frame, text="Log:").pack(anchor=tk.W)
        self.log_text = scrolledtext.ScrolledText(log_frame, height=5,
                                                 bg='#1e1e1e', fg='#00ff00',
                                                 font=('Consolas', 9))
        self.log_text.pack(fill=tk.X)

        # Context menu
        self.context_menu = tk.Menu(self.root, tearoff=0)
        self.context_menu.add_command(label="Open", command=self.open_selected)
        self.context_menu.add_command(label="Copy", command=self.copy_selected)
        self.context_menu.add_command(label="Cut", command=self.cut_selected)
        self.context_menu.add_command(label="Paste", command=self.paste_selected)
        self.context_menu.add_command(label="Delete", command=self.delete_selected)
        self.context_menu.add_command(label="Rename", command=self.rename_selected)
        self.context_menu.add_command(label="Copy to PC", command=self.copy_to_pc)

        self.root.bind('<Control-c>', lambda e: self.copy_selected())
        self.root.bind('<Control-v>', lambda e: self.paste_selected())
        self.root.bind('<Delete>', lambda e: self.delete_selected())
        self.root.bind('<F5>', lambda e: self.refresh_current())

    def check_adb_and_devices(self):
        try:
            subprocess.run(['adb', 'version'], capture_output=True, check=True)
            self.log("✅ ADB terdeteksi")
            self.refresh_devices()
        except Exception as e:
            messagebox.showerror("Error", f"ADB tidak ditemukan!\n{str(e)}")
            self.status_label.config(text="Status: ADB Error", foreground='red')

    def refresh_devices(self):
        try:
            result = subprocess.run(['adb', 'devices'], capture_output=True, text=True)
            devices = result.stdout.strip().split('\n')[1:]
            devices = [d.split('\t')[0] for d in devices if d.strip() and 'device' in d]

            self.device_combo['values'] = devices
            if devices:
                self.device_combo.set(devices[0])
                self.device_id = devices[0]
                self.status_label.config(text="Status: Device selected", foreground='yellow')
                self.log(f"📱 Perangkat terdeteksi: {', '.join(devices)}")
                self.connect_device()
            else:
                self.device_combo.set('')
                self.status_label.config(text="Status: No device", foreground='red')
                self.log("❌ Tidak ada perangkat terdeteksi")
        except Exception as e:
            self.log(f"❌ Error refresh devices: {str(e)}")

    def on_device_selected(self, event):
        self.device_id = self.device_var.get()
        self.connect_device()

    def connect_device(self):
        if not self.device_id:
            messagebox.showwarning("Warning", "Pilih perangkat terlebih dahulu!")
            return

        try:
            self.log(f"🔌 Mencoba koneksi ke {self.device_id}...")
            cmd = ['adb', '-s', self.device_id, 'shell', 'echo', 'connected']
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)

            if 'connected' in result.stdout:
                self.status_label.config(text=f"Status: Connected to {self.device_id}",
                                        foreground='green')
                self.log(f"✅ Terhubung ke {self.device_id}")
                self.refresh_current()
            else:
                self.log(f"❌ Gagal terhubung: {result.stderr}")
                messagebox.showerror("Error", f"Gagal terhubung ke perangkat\n{result.stderr}")
        except Exception as e:
            self.log(f"❌ Error koneksi: {str(e)}")
            messagebox.showerror("Error", f"Gagal terhubung: {str(e)}")

    def log(self, message):
        def _append_log():
            timestamp = time.strftime('%H:%M:%S')
            self.log_text.insert(tk.END, f"[{timestamp}] {message}\n")
            self.log_text.see(tk.END)
        self.root.after(0, _append_log)

    def set_progress_ui(self, visible, value=0, status="", mode="determinate"):
        def _update():
            if visible:
                self.progress_status_label.config(text=status)
                self.progress_status_label.pack(fill=tk.X, pady=1)
                self.progress.config(mode=mode)
                if mode == "indeterminate":
                    self.progress.start(10)
                else:
                    self.progress.stop()
                    self.progress_var.set(value)
                self.progress.pack(fill=tk.X, pady=2)
            else:
                self.progress.stop()
                self.progress.pack_forget()
                self.progress_status_label.pack_forget()
        self.root.after(0, _update)

    def run_adb_command(self, command, timeout=30):
        if not self.device_id:
            raise Exception("No device connected")

        full_cmd = ['adb', '-s', self.device_id] + command

        if self.debug_mode:
            self.log(f"🐛 CMD: {' '.join(full_cmd)}")

        result = subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)
        return result

    def parse_ls_output(self, lines):
        """Parse output ls -la dengan format Android"""
        items = []

        for line in lines:
            if not line.strip() or line.startswith('total'):
                continue

            parts = line.split()
            if len(parts) < 8:
                continue

            try:
                permissions = parts[0]
                if not permissions or len(permissions) < 10:
                    continue

                size_index = -1
                for i, part in enumerate(parts):
                    if part.isdigit() and i > 2:
                        size_index = i
                        break

                if size_index == -1:
                    continue

                size = parts[size_index]

                if size_index + 2 < len(parts):
                    date_part = parts[size_index + 1]
                    time_part = parts[size_index + 2]
                    modified = f"{date_part} {time_part}"
                    name_start = size_index + 3
                else:
                    name_start = 8
                    modified = ""

                name_parts = parts[name_start:]
                name = ' '.join(name_parts)

                if not name or name in ['.', '..']:
                    continue

                is_dir = permissions.startswith('d')
                is_link = permissions.startswith('l')

                items.append({
                    'name': name,
                    'size': size,
                    'is_dir': is_dir,
                    'is_link': is_link,
                    'permissions': permissions,
                    'modified': modified
                })

            except Exception as e:
                continue

        return items

    def update_treeview(self):
        """Render item yang tersimpan di self.items ke Treeview"""
        for item in self.tree.get_children():
            self.tree.delete(item)

        for item in self.items:
            icon = '📁' if item['is_dir'] else ('🔗' if item.get('is_link') else '📄')
            item_id = self.tree.insert('', 'end',
                                      text=f"{icon} {item['name']}",
                                      values=(item['size'], item['modified']))
            item['id'] = item_id

        # Update indikator header
        name_hdr = "Name"
        size_hdr = "Size"
        mod_hdr = "Modified"

        if self.sort_state['column'] == 'name':
            name_hdr += " ▲" if self.sort_state['dir'] == 'asc' else " ▼"
        elif self.sort_state['column'] == 'size':
            size_hdr += " ▲" if self.sort_state['dir'] == 'asc' else " ▼"
        elif self.sort_state['column'] == 'modified':
            mod_hdr += " ▲" if self.sort_state['dir'] == 'asc' else " ▼"

        self.tree.heading('#0', text=name_hdr)
        self.tree.heading('Size', text=size_hdr)
        self.tree.heading('Modified', text=mod_hdr)

    def toggle_sort(self, column):
        """Mengatur siklus sort: Ascending -> Descending -> Default (Original)"""
        if self.sort_state['column'] != column:
            self.sort_state['column'] = column
            self.sort_state['dir'] = 'asc'
        elif self.sort_state['dir'] == 'asc':
            self.sort_state['dir'] = 'desc'
        else:
            self.sort_state['column'] = None
            self.sort_state['dir'] = None

        self.apply_sorting()

    def parse_size_to_bytes(self, size_str):
        """Konversi string size ke integer untuk pengurutan akurat"""
        if not size_str or not str(size_str).isdigit():
            return -1
        return int(size_str)

    def apply_sorting(self):
        col = self.sort_state['column']
        direction = self.sort_state['dir']

        if not col or not direction:
            # Kembali ke urutan asli dari raw_items
            self.items = list(self.raw_items)
        else:
            reverse = (direction == 'desc')

            # Pisahkan direktori dan file agar direktori selalu berada di atas (atau disesuaikan)
            dirs = [x for x in self.raw_items if x['is_dir']]
            files = [x for x in self.raw_items if not x['is_dir']]

            if col == 'name':
                dirs.sort(key=lambda x: x['name'].lower(), reverse=reverse)
                files.sort(key=lambda x: x['name'].lower(), reverse=reverse)
            elif col == 'size':
                dirs.sort(key=lambda x: self.parse_size_to_bytes(x['size']), reverse=reverse)
                files.sort(key=lambda x: self.parse_size_to_bytes(x['size']), reverse=reverse)
            elif col == 'modified':
                dirs.sort(key=lambda x: x['modified'], reverse=reverse)
                files.sort(key=lambda x: x['modified'], reverse=reverse)

            self.items = dirs + files

        self.update_treeview()

    def refresh_current(self):
        """Refresh tampilan direktori saat ini secara terpisah"""
        if not self.device_id:
            self.log("⚠️ No device connected")
            return

        def worker():
            try:
                self.log(f"📂 Reading directory: {self.current_path}")
                cmd = ['shell', 'ls', '-la', self.current_path]
                result = self.run_adb_command(cmd)

                if result.returncode != 0:
                    cmd = ['shell', 'ls', self.current_path]
                    result = self.run_adb_command(cmd)
                    if result.returncode == 0:
                        lines = result.stdout.strip().split('\n')
                        raw = []
                        for name in lines:
                            if name.strip() and name not in ['.', '..']:
                                raw.append({
                                    'name': name,
                                    'size': '',
                                    'is_dir': False,
                                    'is_link': False,
                                    'permissions': '',
                                    'modified': ''
                                })
                        self.raw_items = raw
                        self.root.after(0, self.apply_sorting)
                        self.root.after(0, lambda: self.path_var.set(self.current_path))
                        self.log(f"📂 Found {len(self.raw_items)} items")
                        return
                    else:
                        self.log(f"❌ Gagal membaca direktori: {result.stderr}")
                        return

                lines = result.stdout.strip().split('\n')
                self.raw_items = self.parse_ls_output(lines)
                self.root.after(0, self.apply_sorting)
                self.root.after(0, lambda: self.path_var.set(self.current_path))
                self.log(f"✅ Successfully loaded {len(self.raw_items)} items from {self.current_path}")

            except Exception as e:
                self.log(f"❌ Error refresh: {str(e)}")

        threading.Thread(target=worker, daemon=True).start()

    def show_debug_info(self):
        """Tampilkan informasi debug"""
        debug_window = tk.Toplevel(self.root)
        debug_window.title("Debug Information")
        debug_window.geometry("700x500")

        text_widget = scrolledtext.ScrolledText(debug_window)
        text_widget.pack(fill=tk.BOTH, expand=True)

        info = []
        info.append("=== DEBUG INFORMATION ===")
        info.append(f"Device: {self.device_id}")
        info.append(f"Current Path: {self.current_path}")
        info.append(f"Items Count: {len(self.items)}")
        info.append(f"Sort State: {self.sort_state}")
        info.append("")

        info.append("=== CURRENT ITEMS ===")
        for item in self.items[:20]:
            info.append(f"  {item['name']} - {'DIR' if item['is_dir'] else 'FILE'} - {item['size']}")
        if len(self.items) > 20:
            info.append(f"  ... dan {len(self.items) - 20} lainnya")

        text_widget.insert(tk.END, '\n'.join(info))
        text_widget.configure(state='disabled')

    def go_to_path(self):
        path = self.path_var.get().strip()
        if path:
            self.current_path = path
            self.refresh_current()

    def go_up(self):
        parent = os.path.dirname(self.current_path)
        if parent and parent != self.current_path:
            self.current_path = parent
            self.refresh_current()

    def go_home(self):
        self.current_path = "/storage/emulated/0"
        self.refresh_current()

    def on_item_double_click(self, event):
        selection = self.tree.selection()
        if selection:
            item = selection[0]
            name = self.tree.item(item, 'text')
            name = name.split(' ', 1)[1] if ' ' in name else name

            for file_item in self.items:
                if file_item['name'] == name and file_item['is_dir']:
                    new_path = os.path.join(self.current_path, name)
                    self.current_path = new_path
                    self.refresh_current()
                    return

    def hide_context_menu(self, event=None):
        try:
            self.context_menu.unpost()
        except Exception:
            pass

    def _on_selection_change(self, event=None):
        """Tutup context menu saat berpindah baris via klik kiri / keyboard.
        Gunakan after(10) agar tidak bertabrakan dengan event klik kanan."""
        if self._context_menu_from_rightclick:
            # Seleksi ini berasal dari right-click show_context_menu, abaikan
            self._context_menu_from_rightclick = False
            return
        self.root.after(10, self.hide_context_menu)

    def show_context_menu(self, event):
        item_id = self.tree.identify_row(event.y)
        if item_id:
            # Tandai bahwa perubahan seleksi ini berasal dari right-click
            self._context_menu_from_rightclick = True
            self.tree.selection_set(item_id)
            self.tree.focus(item_id)
        try:
            self.context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.context_menu.grab_release()

    def get_selected_item(self):
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("Warning", "Pilih item terlebih dahulu!")
            return None

        item = selection[0]
        name = self.tree.item(item, 'text')
        name = name.split(' ', 1)[1] if ' ' in name else name

        for file_item in self.items:
            if file_item['name'] == name:
                return file_item
        return None

    def open_selected(self):
        item = self.get_selected_item()
        if item and item['is_dir']:
            self.current_path = os.path.join(self.current_path, item['name'])
            self.refresh_current()

    def copy_selected(self):
        item = self.get_selected_item()
        if item:
            self.clipboard = {
                'path': os.path.join(self.current_path, item['name']),
                'name': item['name'],
                'is_dir': item['is_dir']
            }
            self.clipboard_type = 'copy'
            self.log(f"📋 Copied: {item['name']}")

    def cut_selected(self):
        item = self.get_selected_item()
        if item:
            self.clipboard = {
                'path': os.path.join(self.current_path, item['name']),
                'name': item['name'],
                'is_dir': item['is_dir']
            }
            self.clipboard_type = 'cut'
            self.log(f"✂️ Cut: {item['name']}")

    def paste_selected(self):
        if not self.clipboard:
            messagebox.showinfo("Info", "Clipboard kosong")
            return

        if self.is_operating:
            messagebox.showwarning("Warning", "Proses transfer/operasi lain sedang berjalan!")
            return

        source = self.clipboard['path']
        dest = os.path.join(self.current_path, self.clipboard['name'])

        if self.check_if_exists(dest):
            if not messagebox.askyesno("Confirm", f"File {self.clipboard['name']} already exists. Overwrite?"):
                return

        def worker():
            self.is_operating = True
            try:
                if self.clipboard_type == 'copy':
                    self.log(f"📋 Copying: {source} -> {dest}")
                    self.set_progress_ui(True, status="Copying...", mode="indeterminate")
                    cmd = ['shell', 'cp', '-r', source, dest]
                    result = self.run_adb_command(cmd, timeout=300)
                    if result.returncode == 0:
                        self.log(f"✅ Copied: {source} -> {dest}")
                        self.refresh_current()
                    else:
                        self.log(f"❌ Copy failed: {result.stderr}")
                elif self.clipboard_type == 'cut':
                    self.log(f"✂️ Moving: {source} -> {dest}")
                    self.set_progress_ui(True, status="Moving...", mode="indeterminate")
                    cmd = ['shell', 'mv', source, dest]
                    result = self.run_adb_command(cmd, timeout=300)
                    if result.returncode == 0:
                        self.log(f"✅ Moved: {source} -> {dest}")
                        self.clipboard = None
                        self.refresh_current()
                    else:
                        self.log(f"❌ Move failed: {result.stderr}")
            except Exception as e:
                self.log(f"❌ Error: {str(e)}")
            finally:
                self.is_operating = False
                self.set_progress_ui(False)

        threading.Thread(target=worker, daemon=True).start()

    def delete_selected(self):
        item = self.get_selected_item()
        if item:
            if messagebox.askyesno("Confirm Delete", f"Yakin hapus {item['name']}?"):
                def worker():
                    self.set_progress_ui(True, status="Deleting...", mode="indeterminate")
                    try:
                        path = os.path.join(self.current_path, item['name'])
                        cmd = ['shell', 'rm', '-rf', path]
                        result = self.run_adb_command(cmd)
                        if result.returncode == 0:
                            self.log(f"🗑 Deleted: {item['name']}")
                            self.refresh_current()
                        else:
                            self.log(f"❌ Delete failed: {result.stderr}")
                    except Exception as e:
                        self.log(f"❌ Error: {str(e)}")
                    finally:
                        self.set_progress_ui(False)
                threading.Thread(target=worker, daemon=True).start()

    def rename_selected(self):
        item = self.get_selected_item()
        if item:
            new_name = tk.simpledialog.askstring("Rename",
                                                f"Rename {item['name']}:",
                                                initialvalue=item['name'])
            if new_name and new_name != item['name']:
                def worker():
                    try:
                        old_path = os.path.join(self.current_path, item['name'])
                        new_path = os.path.join(self.current_path, new_name)
                        cmd = ['shell', 'mv', old_path, new_path]
                        result = self.run_adb_command(cmd)
                        if result.returncode == 0:
                            self.log(f"🔄 Renamed: {item['name']} -> {new_name}")
                            self.refresh_current()
                        else:
                            self.log(f"❌ Rename failed: {result.stderr}")
                    except Exception as e:
                        self.log(f"❌ Error: {str(e)}")
                threading.Thread(target=worker, daemon=True).start()

    def create_folder(self):
        name = tk.simpledialog.askstring("New Folder", "Folder name:")
        if name:
            def worker():
                try:
                    path = os.path.join(self.current_path, name)
                    cmd = ['shell', 'mkdir', path]
                    result = self.run_adb_command(cmd)
                    if result.returncode == 0:
                        self.log(f"📁 Created folder: {name}")
                        self.refresh_current()
                    else:
                        self.log(f"❌ Create folder failed: {result.stderr}")
                except Exception as e:
                    self.log(f"❌ Error: {str(e)}")
            threading.Thread(target=worker, daemon=True).start()

    def copy_to_pc(self):
        item = self.get_selected_item()
        if not item:
            return

        if self.is_operating:
            messagebox.showwarning("Warning", "Proses transfer sedang berjalan!")
            return

        pc_path = filedialog.askdirectory(title="Pilih folder tujuan di PC")
        if pc_path:
            android_path = os.path.join(self.current_path, item['name'])
            dest_path = os.path.join(pc_path, item['name'])
            self.run_adb_transfer_async(['pull', android_path, dest_path],
                                        f"📤 Pulling {item['name']} to PC")

    def copy_from_pc(self):
        if self.is_operating:
            messagebox.showwarning("Warning", "Proses transfer sedang berjalan!")
            return

        pc_path = filedialog.askopenfilename(title="Pilih file dari PC")
        if pc_path:
            file_name = os.path.basename(pc_path)
            android_path = os.path.join(self.current_path, file_name)
            self.run_adb_transfer_async(['push', pc_path, android_path],
                                        f"📥 Pushing {file_name} to Android",
                                        refresh_after=True)

    def run_adb_transfer_async(self, command, desc_label, refresh_after=False):
        """Menjalankan ADB push/pull dengan real-time progress parsing secara asynchronous"""
        if not self.device_id:
            messagebox.showwarning("Warning", "Tidak ada device terhubung!")
            return

        full_cmd = ['adb', '-s', self.device_id] + command

        def worker():
            self.is_operating = True
            self.log(desc_label)
            self.set_progress_ui(True, value=0, status=f"{desc_label}...", mode="determinate")

            try:
                process = subprocess.Popen(
                    full_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1
                )

                progress_regex = re.compile(r'\[\s*(\d+)%\]')

                for line in process.stdout:
                    match = progress_regex.search(line)
                    if match:
                        pct = int(match.group(1))
                        self.set_progress_ui(True, value=pct, status=f"{desc_label}: {pct}%", mode="determinate")

                process.wait()
                if process.returncode == 0:
                    self.log(f"✅ Selesai: {desc_label}")
                    if refresh_after:
                        self.refresh_current()
                else:
                    self.log(f"❌ Transfer Gagal ({process.returncode})")
            except Exception as e:
                self.log(f"❌ Error Transfer: {str(e)}")
            finally:
                self.is_operating = False
                self.set_progress_ui(False)

        threading.Thread(target=worker, daemon=True).start()

    def search_files(self):
        pattern = tk.simpledialog.askstring("Search", "Pattern to search:")
        if pattern:
            def worker():
                try:
                    self.log(f"🔍 Searching: {pattern} in {self.current_path}")
                    self.set_progress_ui(True, status="Searching...", mode="indeterminate")
                    cmd = ['shell', 'find', self.current_path, '-name', f'*{pattern}*']
                    result = self.run_adb_command(cmd, timeout=60)

                    if result.returncode == 0:
                        results = result.stdout.strip().split('\n')
                        if results and results[0]:
                            def _show_results():
                                result_window = tk.Toplevel(self.root)
                                result_window.title("Search Results")
                                result_window.geometry("600x400")

                                text_widget = scrolledtext.ScrolledText(result_window)
                                text_widget.pack(fill=tk.BOTH, expand=True)

                                text_widget.insert(tk.END, f"Found {len(results)} files:\n\n")
                                for file_path in results:
                                    text_widget.insert(tk.END, f"{file_path}\n")
                                text_widget.configure(state='disabled')
                            self.root.after(0, _show_results)

                            self.log(f"🔍 Found {len(results)} files")
                        else:
                            self.log("❌ No files found")
                    else:
                        self.log(f"❌ Search failed: {result.stderr}")
                except Exception as e:
                    self.log(f"❌ Error: {str(e)}")
                finally:
                    self.set_progress_ui(False)
            threading.Thread(target=worker, daemon=True).start()

    def show_storage_info(self):
        def worker():
            try:
                cmd = ['shell', 'df', '-h', '/storage/emulated']
                result = self.run_adb_command(cmd)

                def _show():
                    info_window = tk.Toplevel(self.root)
                    info_window.title("Storage Information")
                    info_window.geometry("500x300")

                    text_widget = scrolledtext.ScrolledText(info_window)
                    text_widget.pack(fill=tk.BOTH, expand=True)
                    text_widget.insert(tk.END, result.stdout)
                    text_widget.configure(state='disabled')
                self.root.after(0, _show)

                self.log("📊 Storage info displayed")
            except Exception as e:
                self.log(f"❌ Error: {str(e)}")
        threading.Thread(target=worker, daemon=True).start()

    def check_if_exists(self, path):
        try:
            cmd = ['shell', 'ls', path]
            result = self.run_adb_command(cmd)
            return result.returncode == 0
        except:
            return False

def main():
    root = tk.Tk()
    app = ADBFileManagerGUI(root)

    try:
        root.iconbitmap(default='icon.ico')
    except:
        pass

    root.mainloop()

if __name__ == "__main__":
    main()
