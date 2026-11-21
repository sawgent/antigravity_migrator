# -*- coding: utf-8 -*-
"""
Antigravity Taşıyıcı (Antigravity Migrator) v1.2
Antigravity projelerini, özel eklentilerini (plugins), becerilerini (skills),
kurallarını (rules), konuşma veritabanlarını (conversations & conversation_summaries.db),
bilgi tabanını (knowledge), notları (annotations) ve artifact'ları (brain)
güvenle başka bir bilgisayara taşımak ve mevcut veritabanlarıyla kayıpsız
birleştirmek için tek dosyalık araç.
"""

import os
import sys
import json
import shutil
import sqlite3
import datetime
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

SYSTEM_EXCLUDE_DIRS = {
    "bin", "builtin", "lib", "daemon", "cache", "sandbox",
    "__pycache__", ".pytest_cache"
}
SYSTEM_EXCLUDE_FILES = {
    "installation_id", "knowledge.lock",
    "conversation_summaries.db-wal", "conversation_summaries.db-shm"
}


def is_excluded_file(filename):
    low = filename.lower()
    if low in SYSTEM_EXCLUDE_FILES:
        return True
    if low.endswith(".db-wal") or low.endswith(".db-shm") or low.endswith(".lock"):
        return True
    return False


def format_size(size_bytes):
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def get_dir_size(path, exclude_system=True):
    total = 0
    try:
        for root, dirs, files in os.walk(path):
            if exclude_system:
                dirs[:] = [d for d in dirs if d.lower() not in SYSTEM_EXCLUDE_DIRS]
            for f in files:
                if exclude_system and is_excluded_file(f):
                    continue
                fp = os.path.join(root, f)
                try:
                    total += os.path.getsize(fp)
                except Exception:
                    pass
    except Exception:
        pass
    return total


def backup_sqlite_db(src_db, dst_db):
    """
    SQLite veritabanını WAL dosyasını da dahil ederek temiz, tutarlı bir
    tekil .db dosyası olarak yedekler.
    """
    os.makedirs(os.path.dirname(dst_db), exist_ok=True)
    if os.path.exists(dst_db):
        try:
            os.remove(dst_db)
        except Exception:
            pass
    try:
        src_conn = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True, timeout=10.0)
        dst_conn = sqlite3.connect(dst_db, timeout=10.0)
        with dst_conn:
            src_conn.backup(dst_conn)
        dst_conn.close()
        src_conn.close()
    except Exception:
        # Salt okunur URI veya kilit durumunda normal kopyalamaya düş
        shutil.copy2(src_db, dst_db)


def merge_conversation_summaries_db(src_db, dst_db):
    """
    Eski bilgisayardan gelen conversation_summaries.db ile hedef bilgisayardaki
    mevcut conversation_summaries.db veritabanını kayıpsız birleştirir.
    Dönüş: (eklenen_konusma_sayisi, toplam_konusma_sayisi)
    """
    if not os.path.exists(src_db):
        return (0, 0)

    os.makedirs(os.path.dirname(dst_db), exist_ok=True)

    # Hedef veritabanı yoksa doğrudan temiz kopyala
    if not os.path.exists(dst_db):
        backup_sqlite_db(src_db, dst_db)
        try:
            conn = sqlite3.connect(dst_db, timeout=10.0)
            total = conn.execute("SELECT COUNT(*) FROM conversation_summaries").fetchone()[0]
            conn.close()
            return (total, total)
        except Exception:
            return (0, 0)

    # Mevcut veritabanı varsa önce güvenlik yedeği al
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bak_path = f"{dst_db}.bak_{ts}"
    backup_sqlite_db(dst_db, bak_path)

    conn = sqlite3.connect(dst_db, timeout=15.0)
    try:
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass

        conn.execute("ATTACH DATABASE ? AS src_db", (src_db,))

        # 1. conversation_summaries tablosunu ortak sütunlar üzerinden birleştir
        dst_cols = [r[1] for r in conn.execute("PRAGMA main.table_info(conversation_summaries)").fetchall()]
        src_cols = [r[1] for r in conn.execute("PRAGMA src_db.table_info(conversation_summaries)").fetchall()]
        common_cols = [c for c in dst_cols if c in src_cols]

        added_count = 0
        if common_cols:
            cols_sql = ", ".join(f'"{c}"' for c in common_cols)
            cur = conn.execute(
                f"INSERT OR IGNORE INTO main.conversation_summaries ({cols_sql}) "
                f"SELECT {cols_sql} FROM src_db.conversation_summaries"
            )
            added_count = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0

        # 2. conversation_parents tablosunu birleştir (varsa)
        dst_tables = {r[0] for r in conn.execute("SELECT name FROM main.sqlite_master WHERE type='table'").fetchall()}
        src_tables = {r[0] for r in conn.execute("SELECT name FROM src_db.sqlite_master WHERE type='table'").fetchall()}

        if "conversation_parents" in dst_tables and "conversation_parents" in src_tables:
            conn.execute(
                "INSERT OR IGNORE INTO main.conversation_parents (conversation_id, parent_id) "
                "SELECT conversation_id, parent_id FROM src_db.conversation_parents"
            )

        # 3. conversations_fts arama indeksini birleştir (yalnızca yeni eklenen konuşmalar için)
        if "conversations_fts" in dst_tables and "conversations_fts" in src_tables:
            dst_fts_cols = [r[1] for r in conn.execute("PRAGMA main.table_info(conversations_fts)").fetchall()]
            src_fts_cols = [r[1] for r in conn.execute("PRAGMA src_db.table_info(conversations_fts)").fetchall()]
            common_fts_cols = [c for c in dst_fts_cols if c in src_fts_cols]
            if common_fts_cols:
                fts_sql = ", ".join(f'"{c}"' for c in common_fts_cols)
                conn.execute(
                    f"INSERT INTO main.conversations_fts ({fts_sql}) "
                    f"SELECT {fts_sql} FROM src_db.conversations_fts "
                    f"WHERE conversation_id NOT IN (SELECT DISTINCT conversation_id FROM main.conversations_fts)"
                )

        conn.commit()
        conn.execute("DETACH DATABASE src_db")

        total_count = conn.execute("SELECT COUNT(*) FROM main.conversation_summaries").fetchone()[0]
        return (added_count, total_count)
    finally:
        conn.close()


def safe_copy_tree(src, dst, log_cb=None, progress_cb=None, exclude_system=True, skip_existing=False):
    os.makedirs(dst, exist_ok=True)
    for root, dirs, files in os.walk(src):
        if exclude_system:
            dirs[:] = [d for d in dirs if d.lower() not in SYSTEM_EXCLUDE_DIRS]
        rel_root = os.path.relpath(root, src)
        dest_root = dst if rel_root == "." else os.path.join(dst, rel_root)
        os.makedirs(dest_root, exist_ok=True)

        for f in files:
            if exclude_system and is_excluded_file(f):
                continue
            src_file = os.path.join(root, f)
            dest_file = os.path.join(dest_root, f)
            if skip_existing and os.path.exists(dest_file):
                continue
            try:
                if f.lower().endswith(".db"):
                    backup_sqlite_db(src_file, dest_file)
                else:
                    shutil.copy2(src_file, dest_file)
                if progress_cb:
                    progress_cb()
            except Exception as e:
                if log_cb:
                    log_cb(f"Uyarı: {f} kopyalanamadı ({e})")


def is_antigravity_running():
    """Windows üzerinde Antigravity uygulamasının çalışıp çalışmadığını kontrol eder."""
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        out = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq Antigravity.exe"],
            startupinfo=si,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            text=True,
            errors="ignore"
        )
        return "antigravity.exe" in out.lower()
    except Exception:
        return False


class AntigravityMigratorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Antigravity Proje ve Veri Taşıyıcı v1.2")
        self.geometry("900x740")
        self.minsize(800, 620)

        self._set_style()
        self._setup_context_menu()

        # Varsayılan Konumlar
        self.default_user_home = os.path.expanduser("~")
        self.default_gemini_path = os.path.join(self.default_user_home, ".gemini")

        # Tarama öğeleri
        self.export_items = []
        self.import_items = []
        self.custom_project_folders = []

        self._build_ui()

        # Başlangıçta aynı dizindeki yedekleri otomatik algıla
        self.after(350, self.check_auto_detect_backup)

    def _set_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except Exception:
            pass
        style.configure("TNotebook.Tab", font=("Segoe UI", 10, "bold"), padding=[14, 6])
        style.configure("Header.TLabel", font=("Segoe UI", 11, "bold"))
        style.configure("Action.TButton", font=("Segoe UI", 10, "bold"), padding=7)
        style.configure("Status.TLabel", font=("Segoe UI", 9))

    # =========================================================================
    # SAĞ TUŞ MENÜSÜ (KES, KOPYALA, YAPIŞTIR, TÜMÜNÜ SEÇ)
    # =========================================================================
    def _setup_context_menu(self):
        self.context_menu = tk.Menu(self, tearoff=0)
        self.context_menu.add_command(label="Kes", command=self._ctx_cut)
        self.context_menu.add_command(label="Kopyala", command=self._ctx_copy)
        self.context_menu.add_command(label="Yapıştır", command=self._ctx_paste)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Tümünü Seç", command=self._ctx_select_all)

        self.bind_class("Entry", "<Button-3>", self._show_context_menu)
        self.bind_class("TEntry", "<Button-3>", self._show_context_menu)
        self.bind_class("Text", "<Button-3>", self._show_context_menu)

    def _show_context_menu(self, event):
        w = event.widget
        self.active_context_widget = w
        try:
            w.focus_set()
        except Exception:
            pass

        state = "normal"
        try:
            if str(w.cget("state")) in ("disabled", "readonly"):
                state = "disabled"
        except Exception:
            pass

        self.context_menu.entryconfigure("Kes", state=state)
        self.context_menu.entryconfigure("Yapıştır", state=state)
        self.context_menu.tk_popup(event.x_root, event.y_root)

    def _ctx_cut(self):
        if hasattr(self, 'active_context_widget'):
            try:
                self.active_context_widget.event_generate("<<Cut>>")
            except Exception:
                pass

    def _ctx_copy(self):
        if hasattr(self, 'active_context_widget'):
            try:
                self.active_context_widget.event_generate("<<Copy>>")
            except Exception:
                pass

    def _ctx_paste(self):
        if hasattr(self, 'active_context_widget'):
            try:
                self.active_context_widget.event_generate("<<Paste>>")
            except Exception:
                pass

    def _ctx_select_all(self):
        if hasattr(self, 'active_context_widget'):
            w = self.active_context_widget
            try:
                if isinstance(w, (tk.Entry, ttk.Entry)):
                    w.selection_range(0, 'end')
                    w.icursor('end')
                elif isinstance(w, tk.Text):
                    w.tag_add("sel", "1.0", "end")
                else:
                    w.event_generate("<<SelectAll>>")
            except Exception:
                pass

    # =========================================================================
    # ANA ARAYÜZ (LAYOUT)
    # =========================================================================
    def _build_ui(self):
        header_frame = tk.Frame(self, bg="#0f172a", height=65)
        header_frame.pack(fill="x", side="top")
        header_frame.pack_propagate(False)

        title_lbl = tk.Label(header_frame, text="Antigravity Taşıyıcı (Migrator) v1.2",
                             font=("Segoe UI", 14, "bold"), fg="#ffffff", bg="#0f172a")
        title_lbl.pack(anchor="w", padx=20, pady=(8, 1))

        subtitle_lbl = tk.Label(header_frame,
                                text="Projelerinizi, konuşma veritabanlarınızı, eklentilerinizi ve kurallarınızı güvenle taşıyın",
                                font=("Segoe UI", 9), fg="#94a3b8", bg="#0f172a")
        subtitle_lbl.pack(anchor="w", padx=20)

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=8)

        self.export_tab = ttk.Frame(self.notebook)
        self.import_tab = ttk.Frame(self.notebook)

        self.notebook.add(self.export_tab, text=" 📤 Dışa Aktar (Yedekle) ")
        self.notebook.add(self.import_tab, text=" 📥 İçe Aktar (Geri Yükle) ")

        self._build_export_tab()
        self._build_import_tab()

    # =========================================================================
    # DIŞA AKTAR (EXPORT) SEKMESİ
    # =========================================================================
    def _build_export_tab(self):
        action_frame = ttk.Frame(self.export_tab, padding=8)
        action_frame.pack(side="bottom", fill="x", padx=10, pady=(2, 6))

        self.exp_progress = ttk.Progressbar(action_frame, mode="determinate")
        self.exp_progress.pack(fill="x", pady=(0, 6))

        bot_row = ttk.Frame(action_frame)
        bot_row.pack(fill="x")

        self.exp_status_lbl = ttk.Label(bot_row, text="Hazır. 'Projeleri ve Verileri Tara' butonuna basarak başlayabilirsiniz.", style="Status.TLabel")
        self.exp_status_lbl.pack(side="left", padx=2, fill="x", expand=True)

        self.btn_start_export = ttk.Button(bot_row, text="🚀 Yedeklemeyi Başlat", style="Action.TButton", command=self.start_export)
        self.btn_start_export.pack(side="right", padx=2)

        paths_group = ttk.LabelFrame(self.export_tab, text=" Kaynak ve Hedef Konumlar ", padding=8)
        paths_group.pack(side="top", fill="x", padx=10, pady=5)

        lbl1 = ttk.Label(paths_group, text="Antigravity Kök (.gemini):")
        lbl1.grid(row=0, column=0, sticky="w", pady=2)
        self.exp_gemini_var = tk.StringVar(value=self.default_gemini_path)
        ent1 = ttk.Entry(paths_group, textvariable=self.exp_gemini_var, width=54)
        ent1.grid(row=0, column=1, padx=6, pady=2, sticky="we")
        btn1 = ttk.Button(paths_group, text="Gözat...", command=self._browse_exp_gemini)
        btn1.grid(row=0, column=2, padx=2, pady=2)

        lbl2 = ttk.Label(paths_group, text="Hedef Yedek Klasörü:")
        lbl2.grid(row=1, column=0, sticky="w", pady=2)
        default_backup_dest = os.path.join(self.default_user_home, "Desktop", f"Antigravity_Yedek_{datetime.datetime.now().strftime('%Y%m%d')}")
        self.exp_dest_var = tk.StringVar(value=default_backup_dest)
        ent2 = ttk.Entry(paths_group, textvariable=self.exp_dest_var, width=54)
        ent2.grid(row=1, column=1, padx=6, pady=2, sticky="we")
        btn2 = ttk.Button(paths_group, text="Gözat...", command=self._browse_exp_dest)
        btn2.grid(row=1, column=2, padx=2, pady=2)

        paths_group.columnconfigure(1, weight=1)

        btn_row = ttk.Frame(paths_group)
        btn_row.grid(row=2, column=1, sticky="we", pady=(6, 2))

        btn_scan = ttk.Button(btn_row, text="🔍 Projeleri ve Verileri Tara", command=self.scan_export_items)
        btn_scan.pack(side="left")

        btn_add_custom = ttk.Button(btn_row, text="➕ Ekstra Proje Klasörü Ekle...", command=self._add_custom_project)
        btn_add_custom.pack(side="right")

        items_group = ttk.LabelFrame(self.export_tab, text=" Aktarılacak Öğeler (Gruplara Göre Seçim Yapın) ", padding=6)
        items_group.pack(side="top", fill="both", expand=True, padx=10, pady=4)

        sel_btn_frame = ttk.Frame(items_group)
        sel_btn_frame.pack(fill="x", pady=(0, 4))

        ttk.Button(sel_btn_frame, text="Tümünü Seç", command=lambda: self._set_all_export(True)).pack(side="left", padx=2)
        ttk.Button(sel_btn_frame, text="Tümünü Kaldır", command=lambda: self._set_all_export(False)).pack(side="left", padx=2)

        self.exp_summary_lbl = ttk.Label(sel_btn_frame, text="Henüz tarama yapılmadı.", font=("Segoe UI", 9, "italic"))
        self.exp_summary_lbl.pack(side="right", padx=6)

        canvas_container = ttk.Frame(items_group)
        canvas_container.pack(fill="both", expand=True)

        self.exp_canvas = tk.Canvas(canvas_container, borderwidth=0, highlightthickness=0, bg="#f8fafc")
        scrollbar = ttk.Scrollbar(canvas_container, orient="vertical", command=self.exp_canvas.yview)
        self.exp_scroll_frame = ttk.Frame(self.exp_canvas)

        self.exp_scroll_frame.bind(
            "<Configure>",
            lambda e: self.exp_canvas.configure(scrollregion=self.exp_canvas.bbox("all"))
        )

        self.exp_canvas_window = self.exp_canvas.create_window((0, 0), window=self.exp_scroll_frame, anchor="nw")
        self.exp_canvas.configure(yscrollcommand=scrollbar.set)

        self.exp_canvas.bind('<Configure>', lambda e: self.exp_canvas.itemconfig(self.exp_canvas_window, width=e.width))
        self._bind_mousewheel(self.exp_canvas, self.exp_scroll_frame)

        self.exp_canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def _bind_mousewheel(self, canvas, frame):
        def _on_wheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind("<MouseWheel>", _on_wheel)
        frame.bind("<MouseWheel>", _on_wheel)

    def _browse_exp_gemini(self):
        d = filedialog.askdirectory(title="Antigravity .gemini Klasörünü Seçin", initialdir=self.exp_gemini_var.get())
        if d:
            self.exp_gemini_var.set(d)
            self.scan_export_items()

    def _browse_exp_dest(self):
        d = filedialog.askdirectory(title="Hedef Yedek Klasörünü Seçin", initialdir=os.path.dirname(self.exp_dest_var.get()))
        if d:
            self.exp_dest_var.set(d)

    def _add_custom_project(self):
        d = filedialog.askdirectory(title="Yedeklemek İstediğiniz Proje Klasörünü Seçin")
        if d and d not in self.custom_project_folders:
            self.custom_project_folders.append(d)
            self.scan_export_items()

    def _set_all_export(self, val):
        for item in self.export_items:
            item["var"].set(val)
        self._update_export_summary()

    def _set_group_export(self, group_name, val):
        for item in self.export_items:
            if item.get("group") == group_name:
                item["var"].set(val)
        self._update_export_summary()

    def _update_export_summary(self):
        selected = [i for i in self.export_items if i["var"].get()]
        total_size = sum(i["size"] for i in selected)
        self.exp_summary_lbl.config(
            text=f"Seçilen: {len(selected)} / {len(self.export_items)} öğe ({format_size(total_size)})"
        )

    def scan_export_items(self):
        for widget in self.exp_scroll_frame.winfo_children():
            widget.destroy()
        self.export_items.clear()

        gemini_root = self.exp_gemini_var.get().strip()
        agy_root = os.path.join(gemini_root, "antigravity")
        config_dir = os.path.join(gemini_root, "config")
        scratch_dir = os.path.join(agy_root, "scratch")
        brain_dir = os.path.join(agy_root, "brain")
        conv_dir = os.path.join(agy_root, "conversations")
        summaries_db = os.path.join(agy_root, "conversation_summaries.db")
        annotations_dir = os.path.join(agy_root, "annotations")
        knowledge_dir = os.path.join(agy_root, "knowledge")

        # 1. Projeler Grubu
        project_items = []

        if os.path.exists(scratch_dir):
            try:
                entries = sorted(os.listdir(scratch_dir))
                loose_files = []
                for entry in entries:
                    if entry in ("__pycache__", "antigravity-migrator"):
                        continue
                    full_p = os.path.join(scratch_dir, entry)
                    if os.path.isdir(full_p):
                        p_size = get_dir_size(full_p)
                        var = tk.BooleanVar(value=True)
                        project_items.append({
                            "id": f"scratch_{entry}",
                            "name": f"📁 {entry}",
                            "desc": "Scratch Projesi",
                            "group": "projects",
                            "type": "scratch_project",
                            "folder_name": entry,
                            "path": full_p,
                            "size": p_size,
                            "var": var
                        })
                    else:
                        loose_files.append(full_p)

                if loose_files:
                    total_loose_size = sum(os.path.getsize(f) for f in loose_files if os.path.isfile(f))
                    var = tk.BooleanVar(value=True)
                    project_items.append({
                        "id": "scratch_loose_files",
                        "name": f"📄 Tekil Dosyalar & Scriptler ({len(loose_files)} dosya)",
                        "desc": "Scratch ana dizinindeki bağımsız dosyalar",
                        "group": "projects",
                        "type": "scratch_loose_files",
                        "files": loose_files,
                        "path": scratch_dir,
                        "size": total_loose_size,
                        "var": var
                    })
            except Exception as e:
                messagebox.showerror("Hata", f"Scratch klasörü taranırken hata: {e}")

        for cpath in self.custom_project_folders:
            if os.path.exists(cpath):
                folder_name = os.path.basename(cpath)
                p_size = get_dir_size(cpath)
                var = tk.BooleanVar(value=True)
                project_items.append({
                    "id": f"custom_{folder_name}",
                    "name": f"📦 [Özel Proje] {folder_name}",
                    "desc": cpath,
                    "group": "projects",
                    "type": "custom_project",
                    "folder_name": folder_name,
                    "path": cpath,
                    "size": p_size,
                    "var": var
                })

        # 2. Sistem & Yapılandırma Grubu
        system_items = []

        if os.path.exists(config_dir):
            c_size = get_dir_size(config_dir)
            system_items.append({
                "id": "config",
                "name": "⚙️ Antigravity Yapılandırmaları",
                "desc": "Özel Eklentiler (Plugins), Beceriler (Skills), Kurallar (Rules), MCP",
                "group": "system",
                "type": "config",
                "path": config_dir,
                "size": c_size,
                "var": tk.BooleanVar(value=True)
            })

        # Konuşma Veritabanları (conversations/*.db + conversation_summaries.db)
        conv_exists = os.path.exists(conv_dir)
        sum_exists = os.path.exists(summaries_db)
        if conv_exists or sum_exists:
            conv_size = get_dir_size(conv_dir) if conv_exists else 0
            sum_size = os.path.getsize(summaries_db) if sum_exists else 0
            db_count = len([f for f in os.listdir(conv_dir) if f.lower().endswith(".db")]) if conv_exists else 0
            system_items.append({
                "id": "conversations_pack",
                "name": f"💬 Konuşma Veritabanları ({db_count} konuşma)",
                "desc": "Sohbet geçmişi (.db) ve konuşma listesi (conversation_summaries.db - İçe aktarmada birleştirilir)",
                "group": "system",
                "type": "conversations_pack",
                "conv_path": conv_dir if conv_exists else "",
                "summaries_path": summaries_db if sum_exists else "",
                "path": agy_root,
                "size": conv_size + sum_size,
                "var": tk.BooleanVar(value=True)
            })

        if os.path.exists(brain_dir):
            b_size = get_dir_size(brain_dir)
            system_items.append({
                "id": "brain",
                "name": "🧠 Üretilen Dokümanlar ve Transkriptler (Brain)",
                "desc": "Oturum transkriptleri (transcript.jsonl), planlar ve artifact dosyaları",
                "group": "system",
                "type": "brain",
                "path": brain_dir,
                "size": b_size,
                "var": tk.BooleanVar(value=True)
            })

        if os.path.exists(annotations_dir):
            a_size = get_dir_size(annotations_dir)
            if a_size > 0:
                system_items.append({
                    "id": "annotations",
                    "name": "📝 Konuşma Notları (Annotations)",
                    "desc": "Konuşmalara eklenen kullanıcı notları ve işaretler",
                    "group": "system",
                    "type": "annotations",
                    "path": annotations_dir,
                    "size": a_size,
                    "var": tk.BooleanVar(value=True)
                })

        if os.path.exists(knowledge_dir):
            k_size = get_dir_size(knowledge_dir)
            if k_size > 0:
                system_items.append({
                    "id": "knowledge",
                    "name": "📚 Öğrenilmiş Bilgi Tabanı (Knowledge)",
                    "desc": "Antigravity tarafından çıkarılmış kalıcı bilgi öğeleri",
                    "group": "system",
                    "type": "knowledge",
                    "path": knowledge_dir,
                    "size": k_size,
                    "var": tk.BooleanVar(value=True)
                })

        self.export_items = project_items + system_items

        if not self.export_items:
            lbl = ttk.Label(self.exp_scroll_frame, text="Belirtilen yolda aktarılacak herhangi bir Antigravity verisi bulunamadı.", foreground="#dc2626")
            lbl.pack(anchor="w", padx=10, pady=10)
        else:
            self._render_group_ui(
                parent=self.exp_scroll_frame,
                group_title="📁 PROJELER VE ÇALIŞMA DOSYALARI",
                group_id="projects",
                items=project_items,
                is_export=True
            )
            self._render_group_ui(
                parent=self.exp_scroll_frame,
                group_title="⚙️ ANTİGRAVİTY SİSTEM & YAPILANDIRMA VERİLERİ",
                group_id="system",
                items=system_items,
                is_export=True
            )

        self._update_export_summary()
        self.exp_status_lbl.config(text=f"Tarama tamamlandı: {len(project_items)} proje, {len(system_items)} sistem verisi bulundu.")

    def _render_group_ui(self, parent, group_title, group_id, items, is_export=True):
        if not items:
            return

        total_group_size = sum(it["size"] for it in items)

        group_header = tk.Frame(parent, bg="#e2e8f0", bd=1, relief="solid")
        group_header.pack(fill="x", padx=4, pady=(10, 2))

        title_lbl = tk.Label(group_header, text=group_title, font=("Segoe UI", 10, "bold"), fg="#1e293b", bg="#e2e8f0")
        title_lbl.pack(side="left", padx=8, pady=4)

        info_lbl = tk.Label(group_header, text=f"({len(items)} öğe - {format_size(total_group_size)})", font=("Segoe UI", 9), fg="#475569", bg="#e2e8f0")
        info_lbl.pack(side="left", padx=4)

        btn_box = tk.Frame(group_header, bg="#e2e8f0")
        btn_box.pack(side="right", padx=6)

        cb_cmd = lambda val: (self._set_group_export(group_id, val) if is_export else self._set_group_import(group_id, val))

        b1 = tk.Button(btn_box, text="Tümünü Seç", font=("Segoe UI", 8), bg="#ffffff", relief="groove", command=lambda: cb_cmd(True))
        b1.pack(side="left", padx=2, pady=2)

        b2 = tk.Button(btn_box, text="Kaldır", font=("Segoe UI", 8), bg="#ffffff", relief="groove", command=lambda: cb_cmd(False))
        b2.pack(side="left", padx=2, pady=2)

        items_card = tk.Frame(parent, bg="#ffffff", bd=1, relief="groove")
        items_card.pack(fill="x", padx=4, pady=(0, 4))

        for idx, item in enumerate(items):
            row_bg = "#ffffff" if idx % 2 == 0 else "#f8fafc"
            row_frame = tk.Frame(items_card, bg=row_bg)
            row_frame.pack(fill="x", padx=4, pady=2)

            update_cmd = self._update_export_summary if is_export else self._update_import_summary
            cb = ttk.Checkbutton(row_frame, variable=item["var"], command=update_cmd)
            cb.pack(side="left", padx=(6, 2))

            text_frame = tk.Frame(row_frame, bg=row_bg)
            text_frame.pack(side="left", fill="x", expand=True)

            name_lbl = tk.Label(text_frame, text=item["name"], font=("Segoe UI", 9, "bold"), fg="#0f172a", bg=row_bg, anchor="w")
            name_lbl.pack(anchor="w")

            if item.get("desc"):
                desc_lbl = tk.Label(text_frame, text=item["desc"], font=("Segoe UI", 8), fg="#64748b", bg=row_bg, anchor="w")
                desc_lbl.pack(anchor="w")

            size_lbl = tk.Label(row_frame, text=format_size(item["size"]), font=("Segoe UI", 9, "bold"), fg="#334155", bg=row_bg)
            size_lbl.pack(side="right", padx=10)

    def start_export(self):
        selected_items = [i for i in self.export_items if i["var"].get()]
        if not selected_items:
            messagebox.showwarning("Seçim Yok", "Lütfen aktarmak için en az bir öğe seçin!")
            return

        dest_dir = self.exp_dest_var.get().strip()
        if not dest_dir:
            messagebox.showwarning("Hedef Eksik", "Lütfen bir hedef yedek klasörü belirtin!")
            return

        self.btn_start_export.config(state="disabled")
        self.exp_progress.config(mode="indeterminate")
        self.exp_progress.start(10)
        self.exp_status_lbl.config(text="Yedekleme başlatılıyor...")

        threading.Thread(target=self._run_export_thread, args=(selected_items, dest_dir), daemon=True).start()

    def _run_export_thread(self, selected_items, dest_dir):
        try:
            os.makedirs(dest_dir, exist_ok=True)
            manifest_items = []

            for idx, item in enumerate(selected_items, 1):
                self._update_status(f"Kopyalanıyor ({idx}/{len(selected_items)}): {item['name']}...")
                itype = item["type"]

                if itype in ("config", "brain", "annotations", "knowledge"):
                    target_sub = os.path.join(dest_dir, itype)
                    safe_copy_tree(item["path"], target_sub)
                    manifest_items.append({
                        "id": item["id"],
                        "type": itype,
                        "group": "system",
                        "name": item["name"],
                        "desc": item.get("desc", ""),
                        "rel_path": itype,
                        "size": item["size"]
                    })

                elif itype == "conversations_pack":
                    pack_dir = os.path.join(dest_dir, "conversations_data")
                    os.makedirs(pack_dir, exist_ok=True)
                    if item.get("conv_path") and os.path.exists(item["conv_path"]):
                        safe_copy_tree(item["conv_path"], os.path.join(pack_dir, "conversations"))
                    if item.get("summaries_path") and os.path.exists(item["summaries_path"]):
                        backup_sqlite_db(item["summaries_path"], os.path.join(pack_dir, "conversation_summaries.db"))
                    manifest_items.append({
                        "id": item["id"],
                        "type": "conversations_pack",
                        "group": "system",
                        "name": item["name"],
                        "desc": item.get("desc", ""),
                        "rel_path": "conversations_data",
                        "size": item["size"]
                    })

                elif itype == "scratch_project":
                    target_sub = os.path.join(dest_dir, "projects", item["folder_name"])
                    safe_copy_tree(item["path"], target_sub)
                    manifest_items.append({
                        "id": item["id"],
                        "type": itype,
                        "group": "projects",
                        "folder_name": item["folder_name"],
                        "name": item["name"],
                        "desc": item.get("desc", ""),
                        "rel_path": f"projects/{item['folder_name']}",
                        "size": item["size"]
                    })

                elif itype == "custom_project":
                    target_sub = os.path.join(dest_dir, "custom_projects", item["folder_name"])
                    safe_copy_tree(item["path"], target_sub)
                    manifest_items.append({
                        "id": item["id"],
                        "type": itype,
                        "group": "projects",
                        "folder_name": item["folder_name"],
                        "name": item["name"],
                        "desc": item.get("desc", ""),
                        "rel_path": f"custom_projects/{item['folder_name']}",
                        "size": item["size"]
                    })

                elif itype == "scratch_loose_files":
                    target_sub = os.path.join(dest_dir, "scratch_files")
                    os.makedirs(target_sub, exist_ok=True)
                    for fpath in item["files"]:
                        if os.path.isfile(fpath):
                            shutil.copy2(fpath, os.path.join(target_sub, os.path.basename(fpath)))
                    manifest_items.append({
                        "id": item["id"],
                        "type": itype,
                        "group": "projects",
                        "name": item["name"],
                        "desc": item.get("desc", ""),
                        "rel_path": "scratch_files",
                        "size": item["size"]
                    })

            manifest = {
                "manifest_version": "1.2",
                "tool": "Antigravity Migrator",
                "created_at": datetime.datetime.now().isoformat(),
                "created_by_user": os.getenv("USERNAME", "unknown"),
                "items": manifest_items
            }
            manifest_file = os.path.join(dest_dir, "backup_manifest.json")
            with open(manifest_file, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)

            self._update_status("Uygulamanın bir kopyası yedek klasörüne ekleniyor...")
            self_exe_path = sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__)
            dest_exe_name = "AntigravityMigrator.exe" if self_exe_path.lower().endswith(".exe") else "AntigravityMigrator.py"
            dest_self_path = os.path.join(dest_dir, dest_exe_name)
            try:
                if os.path.abspath(self_exe_path) != os.path.abspath(dest_self_path):
                    shutil.copy2(self_exe_path, dest_self_path)
            except Exception:
                pass

            self.after(0, lambda: self._on_export_success(dest_dir, len(manifest_items)))

        except Exception as e:
            self.after(0, lambda: self._on_export_error(str(e)))

    def _update_status(self, text):
        self.after(0, lambda: self.exp_status_lbl.config(text=text))

    def _on_export_success(self, dest_dir, count):
        self.exp_progress.stop()
        self.exp_progress.config(mode="determinate", value=100)
        self.btn_start_export.config(state="normal")
        self.exp_status_lbl.config(text="✅ Yedekleme başarıyla tamamlandı!")

        msg = (
            f"Tebrikler! Seçilen {count} öğe başarıyla yedeklendi.\n\n"
            f"Hedef Klasör:\n{dest_dir}\n\n"
            f"ℹ️ Programın kendisi de yedek klasörünün içine kopyalandı. "
            f"Bu klasörü yeni bilgisayara taşıyıp içindeki programı çalıştırdığınızda yedekler otomatik algılanacaktır.\n\n"
            f"Klasörü şimdi açmak ister misiniz?"
        )
        if messagebox.askyesno("Yedekleme Başarılı", msg):
            try:
                os.startfile(dest_dir)
            except Exception:
                pass

    def _on_export_error(self, err_msg):
        self.exp_progress.stop()
        self.btn_start_export.config(state="normal")
        self.exp_status_lbl.config(text="❌ Hata oluştu!")
        messagebox.showerror("Yedekleme Hatası", f"İşlem sırasında bir hata meydana geldi:\n\n{err_msg}")

    # =========================================================================
    # İÇE AKTAR (IMPORT / GERİ YÜKLE) SEKMESİ
    # =========================================================================
    def _build_import_tab(self):
        action_frame = ttk.Frame(self.import_tab, padding=8)
        action_frame.pack(side="bottom", fill="x", padx=10, pady=(2, 6))

        self.imp_progress = ttk.Progressbar(action_frame, mode="determinate")
        self.imp_progress.pack(fill="x", pady=(0, 6))

        bot_row = ttk.Frame(action_frame)
        bot_row.pack(fill="x")

        self.imp_status_lbl = ttk.Label(bot_row, text="Yedek klasörü seçildiğinde geri yükleme yapılabilir.", style="Status.TLabel")
        self.imp_status_lbl.pack(side="left", padx=2, fill="x", expand=True)

        self.btn_start_import = ttk.Button(bot_row, text="📥 İçe Aktarmayı Başlat", style="Action.TButton", command=self.start_import)
        self.btn_start_import.pack(side="right", padx=2)

        paths_group = ttk.LabelFrame(self.import_tab, text=" Kaynak Yedek ve Hedef Konumlar ", padding=8)
        paths_group.pack(side="top", fill="x", padx=10, pady=5)

        lbl1 = ttk.Label(paths_group, text="Yedek Klasörü (Kaynak):")
        lbl1.grid(row=0, column=0, sticky="w", pady=2)
        self.imp_src_var = tk.StringVar(value="")
        ent1 = ttk.Entry(paths_group, textvariable=self.imp_src_var, width=54)
        ent1.grid(row=0, column=1, padx=6, pady=2, sticky="we")
        btn1 = ttk.Button(paths_group, text="Gözat...", command=self._browse_imp_src)
        btn1.grid(row=0, column=2, padx=2, pady=2)

        lbl2 = ttk.Label(paths_group, text="Hedef Antigravity (.gemini):")
        lbl2.grid(row=1, column=0, sticky="w", pady=2)
        self.imp_dest_var = tk.StringVar(value=self.default_gemini_path)
        ent2 = ttk.Entry(paths_group, textvariable=self.imp_dest_var, width=54)
        ent2.grid(row=1, column=1, padx=6, pady=2, sticky="we")
        btn2 = ttk.Button(paths_group, text="Gözat...", command=self._browse_imp_dest)
        btn2.grid(row=1, column=2, padx=2, pady=2)

        paths_group.columnconfigure(1, weight=1)

        btn_scan = ttk.Button(paths_group, text="🔍 Yedek İçeriğini İncele / Tara", command=self.scan_import_items)
        btn_scan.grid(row=2, column=1, sticky="w", pady=(6, 2))

        items_group = ttk.LabelFrame(self.import_tab, text=" İçe Aktarılacak Öğeler (Gruplara Göre Seçim Yapın) ", padding=6)
        items_group.pack(side="top", fill="both", expand=True, padx=10, pady=4)

        sel_btn_frame = ttk.Frame(items_group)
        sel_btn_frame.pack(fill="x", pady=(0, 4))

        ttk.Button(sel_btn_frame, text="Tümünü Seç", command=lambda: self._set_all_import(True)).pack(side="left", padx=2)
        ttk.Button(sel_btn_frame, text="Tümünü Kaldır", command=lambda: self._set_all_import(False)).pack(side="left", padx=2)

        self.imp_summary_lbl = ttk.Label(sel_btn_frame, text="Yedek klasörü seçip tarama yapın.", font=("Segoe UI", 9, "italic"))
        self.imp_summary_lbl.pack(side="right", padx=6)

        canvas_container = ttk.Frame(items_group)
        canvas_container.pack(fill="both", expand=True)

        self.imp_canvas = tk.Canvas(canvas_container, borderwidth=0, highlightthickness=0, bg="#f8fafc")
        scrollbar = ttk.Scrollbar(canvas_container, orient="vertical", command=self.imp_canvas.yview)
        self.imp_scroll_frame = ttk.Frame(self.imp_canvas)

        self.imp_scroll_frame.bind(
            "<Configure>",
            lambda e: self.imp_canvas.configure(scrollregion=self.imp_canvas.bbox("all"))
        )

        self.imp_canvas_window = self.imp_canvas.create_window((0, 0), window=self.imp_scroll_frame, anchor="nw")
        self.imp_canvas.configure(yscrollcommand=scrollbar.set)

        self.imp_canvas.bind('<Configure>', lambda e: self.imp_canvas.itemconfig(self.imp_canvas_window, width=e.width))
        self._bind_mousewheel(self.imp_canvas, self.imp_scroll_frame)

        self.imp_canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def _browse_imp_src(self):
        d = filedialog.askdirectory(title="Yedek Klasörünü Seçin")
        if d:
            self.imp_src_var.set(d)
            self.scan_import_items()

    def _browse_imp_dest(self):
        d = filedialog.askdirectory(title="Hedef .gemini Klasörünü Seçin", initialdir=self.imp_dest_var.get())
        if d:
            self.imp_dest_var.set(d)

    def _set_all_import(self, val):
        for item in self.import_items:
            item["var"].set(val)
        self._update_import_summary()

    def _set_group_import(self, group_name, val):
        for item in self.import_items:
            if item.get("group") == group_name:
                item["var"].set(val)
        self._update_import_summary()

    def _update_import_summary(self):
        selected = [i for i in self.import_items if i["var"].get()]
        total_size = sum(i["size"] for i in selected)
        self.imp_summary_lbl.config(
            text=f"Seçilen: {len(selected)} / {len(self.import_items)} öğe ({format_size(total_size)})"
        )

    def scan_import_items(self):
        for widget in self.imp_scroll_frame.winfo_children():
            widget.destroy()
        self.import_items.clear()

        src_dir = self.imp_src_var.get().strip()
        if not src_dir or not os.path.exists(src_dir):
            lbl = ttk.Label(self.imp_scroll_frame, text="Lütfen geçerli bir yedek klasörü seçin.", foreground="#dc2626")
            lbl.pack(anchor="w", padx=10, pady=10)
            return

        manifest_path = os.path.join(src_dir, "backup_manifest.json")
        project_items = []
        system_items = []

        if os.path.exists(manifest_path):
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
                items = manifest.get("items", [])
                for it in items:
                    it_path = os.path.join(src_dir, it["rel_path"])
                    if os.path.exists(it_path):
                        var = tk.BooleanVar(value=True)
                        group = it.get("group")
                        if not group:
                            group = "system" if it.get("type") in ("config", "brain", "conversations_pack", "annotations", "knowledge") else "projects"

                        item_obj = {
                            "id": it.get("id"),
                            "name": it.get("name", os.path.basename(it_path)),
                            "desc": it.get("desc", ""),
                            "group": group,
                            "type": it.get("type"),
                            "folder_name": it.get("folder_name"),
                            "rel_path": it.get("rel_path"),
                            "full_path": it_path,
                            "size": it.get("size", get_dir_size(it_path)),
                            "var": var
                        }
                        if group == "projects":
                            project_items.append(item_obj)
                        else:
                            system_items.append(item_obj)
            except Exception as e:
                messagebox.showerror("Hata", f"Yedek manifest dosyası okunamadı: {e}")
                return
        else:
            for entry in os.listdir(src_dir):
                full_p = os.path.join(src_dir, entry)
                if entry.lower() in ("backup_manifest.json", "antigravitymigrator.exe", "antigravitymigrator.py"):
                    continue
                if os.path.isdir(full_p):
                    sz = get_dir_size(full_p)
                    var = tk.BooleanVar(value=True)
                    low = entry.lower()
                    sys_types = ("config", "brain", "conversations_data", "annotations", "knowledge")
                    group = "system" if low in sys_types else "projects"
                    itype = "conversations_pack" if low == "conversations_data" else (low if low in sys_types else "scratch_project")
                    item_obj = {
                        "id": entry,
                        "name": f"📁 {entry}",
                        "desc": "Yedek Klasörü",
                        "group": group,
                        "type": itype,
                        "folder_name": entry,
                        "rel_path": entry,
                        "full_path": full_p,
                        "size": sz,
                        "var": var
                    }
                    if group == "projects":
                        project_items.append(item_obj)
                    else:
                        system_items.append(item_obj)

        self.import_items = project_items + system_items

        if not self.import_items:
            lbl = ttk.Label(self.imp_scroll_frame, text="Bu klasörde geri yüklenecek bir yedek içeriği bulunamadı.", foreground="#dc2626")
            lbl.pack(anchor="w", padx=10, pady=10)
        else:
            self._render_group_ui(
                parent=self.imp_scroll_frame,
                group_title="📁 YEDEKLENMİŞ PROJELER",
                group_id="projects",
                items=project_items,
                is_export=False
            )
            self._render_group_ui(
                parent=self.imp_scroll_frame,
                group_title="⚙️ YEDEKLENMİŞ SİSTEM & YAPILANDIRMA VERİLERİ",
                group_id="system",
                items=system_items,
                is_export=False
            )

        self._update_import_summary()
        self.imp_status_lbl.config(text=f"Yedek incelendi: {len(project_items)} proje, {len(system_items)} sistem verisi bulundu.")

    def start_import(self):
        selected_items = [i for i in self.import_items if i["var"].get()]
        if not selected_items:
            messagebox.showwarning("Seçim Yok", "Lütfen içe aktarmak için en az bir öğe seçin!")
            return

        dest_gemini = self.imp_dest_var.get().strip()
        if not dest_gemini:
            messagebox.showwarning("Hedef Eksik", "Lütfen Antigravity (.gemini) hedef konumunu belirtin!")
            return

        has_conv = any(i.get("type") == "conversations_pack" for i in selected_items)
        if has_conv and is_antigravity_running():
            ans = messagebox.askyesno(
                "Antigravity Açık Görünüyor",
                "Konuşma veritabanlarını (conversations / conversation_summaries.db) güvenle birleştirebilmek için "
                "Antigravity uygulamasının kapalı olması gerekir.\n\n"
                "Lütfen Antigravity'yi kapatıp devam edin. Yine de devam etmek istiyor musunuz?"
            )
            if not ans:
                return

        self.btn_start_import.config(state="disabled")
        self.imp_progress.config(mode="indeterminate")
        self.imp_progress.start(10)
        self.imp_status_lbl.config(text="İçe aktarma başlatılıyor...")

        threading.Thread(target=self._run_import_thread, args=(selected_items, dest_gemini), daemon=True).start()

    def _run_import_thread(self, selected_items, dest_gemini):
        try:
            agy_dest = os.path.join(dest_gemini, "antigravity")
            target_scratch = os.path.join(agy_dest, "scratch")
            target_brain = os.path.join(agy_dest, "brain")
            target_config = os.path.join(dest_gemini, "config")
            target_conv = os.path.join(agy_dest, "conversations")
            target_summaries_db = os.path.join(agy_dest, "conversation_summaries.db")
            target_annotations = os.path.join(agy_dest, "annotations")
            target_knowledge = os.path.join(agy_dest, "knowledge")

            merge_info = None

            for idx, item in enumerate(selected_items, 1):
                self._update_imp_status(f"Aktarılıyor ({idx}/{len(selected_items)}): {item['name']}...")
                itype = item.get("type")

                if itype == "config":
                    safe_copy_tree(item["full_path"], target_config)
                elif itype == "brain":
                    safe_copy_tree(item["full_path"], target_brain)
                elif itype == "annotations":
                    safe_copy_tree(item["full_path"], target_annotations)
                elif itype == "knowledge":
                    safe_copy_tree(item["full_path"], target_knowledge)
                elif itype == "conversations_pack":
                    src_conv_dir = os.path.join(item["full_path"], "conversations")
                    if os.path.exists(src_conv_dir):
                        # Mevcut konuşmaları ezmeden sadece eksik olanları / yeni .db dosyalarını kopyala
                        safe_copy_tree(src_conv_dir, target_conv, skip_existing=True)
                    src_sum_db = os.path.join(item["full_path"], "conversation_summaries.db")
                    if os.path.exists(src_sum_db):
                        self._update_imp_status("Konuşma veritabanları SQLite ile birleştiriliyor...")
                        merge_info = merge_conversation_summaries_db(src_sum_db, target_summaries_db)
                elif itype in ("scratch_project", "custom_project"):
                    folder_name = item.get("folder_name", os.path.basename(item["full_path"]))
                    target_project_dir = os.path.join(target_scratch, folder_name)
                    safe_copy_tree(item["full_path"], target_project_dir)
                elif itype == "scratch_loose_files":
                    os.makedirs(target_scratch, exist_ok=True)
                    for fn in os.listdir(item["full_path"]):
                        sf = os.path.join(item["full_path"], fn)
                        if os.path.isfile(sf):
                            shutil.copy2(sf, os.path.join(target_scratch, fn))
                else:
                    folder_name = os.path.basename(item["full_path"])
                    target_project_dir = os.path.join(target_scratch, folder_name)
                    safe_copy_tree(item["full_path"], target_project_dir)

            self.after(0, lambda: self._on_import_success(dest_gemini, len(selected_items), merge_info))

        except Exception as e:
            self.after(0, lambda: self._on_import_error(str(e)))

    def _update_imp_status(self, text):
        self.after(0, lambda: self.imp_status_lbl.config(text=text))

    def _on_import_success(self, dest_gemini, count, merge_info=None):
        self.imp_progress.stop()
        self.imp_progress.config(mode="determinate", value=100)
        self.btn_start_import.config(state="normal")
        self.imp_status_lbl.config(text="✅ İçe aktarma başarıyla tamamlandı!")

        merge_msg = ""
        if merge_info is not None:
            added, total = merge_info
            merge_msg = f"\n\n💬 Konuşma Veritabanı Birleştirme:\n• Yeni eklenen konuşma: {added}\n• Toplam konuşma sayısı: {total}"

        msg = (
            f"Harika! Seçtiğiniz {count} öğe başarıyla bu bilgisayara aktarıldı.\n\n"
            f"Hedef Dizin: {dest_gemini}"
            f"{merge_msg}\n\n"
            f"Artık Antigravity'yi başlatıp tüm eski ve yeni konuşmalarınızla çalışmalarınıza devam edebilirsiniz!"
        )
        messagebox.showinfo("İçe Aktarma Tamamlandı", msg)

    def _on_import_error(self, err_msg):
        self.imp_progress.stop()
        self.btn_start_import.config(state="normal")
        self.imp_status_lbl.config(text="❌ Hata oluştu!")
        messagebox.showerror("İçe Aktarma Hatası", f"İşlem sırasında bir hata meydana geldi:\n\n{err_msg}")

    # =========================================================================
    # OTOMATİK ALGILAMA (AUTO-DETECT)
    # =========================================================================
    def check_auto_detect_backup(self):
        current_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        manifest_here = os.path.join(current_dir, "backup_manifest.json")

        detected_backup_dir = None
        if os.path.exists(manifest_here):
            detected_backup_dir = current_dir
        else:
            for entry in os.listdir(current_dir):
                sub = os.path.join(current_dir, entry)
                if os.path.isdir(sub) and os.path.exists(os.path.join(sub, "backup_manifest.json")):
                    detected_backup_dir = sub
                    break

        if detected_backup_dir:
            msg = (
                "🔍 Bu klasörde daha önceden yedeklenmiş Antigravity projeleri ve ayarları tespit edildi!\n\n"
                f"Yedek Konumu: {detected_backup_dir}\n\n"
                "Bu yedekleri otomatik olarak bu bilgisayara aktarmak istiyor musunuz?"
            )
            if messagebox.askyesno("Yedek Tespit Edildi", msg):
                self.notebook.select(self.import_tab)
                self.imp_src_var.set(detected_backup_dir)
                self.scan_import_items()
        else:
            self.scan_export_items()


if __name__ == "__main__":
    app = AntigravityMigratorApp()
    app.mainloop()
