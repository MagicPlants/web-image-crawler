#!/usr/bin/env python3
"""
gui.py - Desktop GUI for the Web Image Crawler & Archive Manager.
Features:
- Recursive multi-level crawling with depth and domain controls
- Headless browser rendering option
- Archive Manager tracking all ZIP archives
- Direct in-memory image thumbnail preview without unpacking
- Native Windows File Explorer integration and batch extraction
"""

from datetime import datetime
import io
import os
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext
from urllib.parse import urlparse

from PIL import Image, ImageTk

import archive_store
import crawler


def format_bytes(num_bytes: int) -> str:
    """Formats bytes into human readable KB / MB."""
    if num_bytes < 1024:
        return f"{num_bytes} B"
    elif num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    else:
        return f"{num_bytes / (1024 * 1024):.2f} MB"


class ImageScraperApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Web Image Crawler & Archive Manager by magicplants.net")
        self.geometry("1060x740")
        self.minsize(900, 600)

        # Apply system styling
        self.style = ttk.Style(self)
        try:
            self.style.theme_use("vista" if "vista" in self.style.theme_names() else "clam")
        except Exception:
            pass

        self.crawler_thread = None
        self.cancel_event = threading.Event()
        self.current_preview_image = None  # Reference to avoid garbage collection

        self.create_widgets()
        self.refresh_archives_list()

    def create_widgets(self):
        # Main Notebook Tabs
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # Tab 1: Crawler & Archiver
        self.tab_crawler = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.tab_crawler, text="  📥  Web Crawler & Downloader  ")

        # Tab 2: Archive Manager
        self.tab_manager = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.tab_manager, text="  🗃️  Archive Manager & Viewer  ")

        self.setup_crawler_tab()
        self.setup_manager_tab()

    # =========================================================================
    # TAB 1: CRAWLER & DOWNLOADER
    # =========================================================================
    def setup_crawler_tab(self):
        # Target Settings
        frame_target = ttk.LabelFrame(self.tab_crawler, text="Target Page & Output", padding=10)
        frame_target.pack(fill=tk.X, pady=(0, 10))

        # URL Input
        ttk.Label(frame_target, text="Starting Web Page URL:").grid(row=0, column=0, sticky=tk.W, pady=4)
        self.entry_url = ttk.Entry(frame_target, width=65)
        self.entry_url.grid(row=0, column=1, sticky=tk.EW, padx=8, pady=4)
        self.entry_url.insert(0, "https://")
        self.entry_url.bind("<KeyRelease>", self.on_url_changed)

        # Output Zip Location
        ttk.Label(frame_target, text="Save Archive To:").grid(row=1, column=0, sticky=tk.W, pady=4)
        self.entry_output = ttk.Entry(frame_target, width=65)
        self.entry_output.grid(row=1, column=1, sticky=tk.EW, padx=8, pady=4)

        default_zip = os.path.join(archive_store.DEFAULT_ARCHIVES_DIR, "images.zip")
        self.entry_output.insert(0, default_zip)

        btn_browse = ttk.Button(frame_target, text="Browse...", command=self.browse_output_zip)
        btn_browse.grid(row=1, column=2, padx=4, pady=4)

        frame_target.columnconfigure(1, weight=1)

        # Crawl Settings
        frame_opts = ttk.LabelFrame(self.tab_crawler, text="Crawl Depth & Scraping Options", padding=10)
        frame_opts.pack(fill=tk.X, pady=(0, 10))

        # Depth Spinbox
        ttk.Label(frame_opts, text="Crawl Depth (Worm X levels deep):").grid(row=0, column=0, sticky=tk.W, pady=4)
        self.spin_depth = ttk.Spinbox(frame_opts, from_=0, to=10, width=5)
        self.spin_depth.set(0)
        self.spin_depth.grid(row=0, column=1, sticky=tk.W, padx=8, pady=4)

        ttk.Label(frame_opts, text="(0 = Only starting page, 1 = Page + direct links, 2 = 2 levels deep)").grid(
            row=0, column=2, sticky=tk.W, padx=4, pady=4
        )

        # Max pages limit
        ttk.Label(frame_opts, text="Max Pages Cap:").grid(row=1, column=0, sticky=tk.W, pady=4)
        self.spin_max_pages = ttk.Spinbox(frame_opts, from_=1, to=500, width=5)
        self.spin_max_pages.set(25)
        self.spin_max_pages.grid(row=1, column=1, sticky=tk.W, padx=8, pady=4)

        ttk.Label(frame_opts, text="Prevents runaway crawls on large sites").grid(row=1, column=2, sticky=tk.W, padx=4, pady=4)

        # Options Checkboxes
        opts_box = ttk.Frame(frame_opts)
        opts_box.grid(row=2, column=0, columnspan=3, sticky=tk.W, pady=6)

        self.var_same_domain = tk.BooleanVar(value=True)
        self.chk_same_domain = ttk.Checkbutton(opts_box, text="Stay within same domain", variable=self.var_same_domain)
        self.chk_same_domain.pack(side=tk.LEFT, padx=(0, 15))

        self.var_browser = tk.BooleanVar(value=False)
        self.chk_browser = ttk.Checkbutton(
            opts_box, text="Use Headless Browser (for dynamic JS/lazy loaders)", variable=self.var_browser
        )
        self.chk_browser.pack(side=tk.LEFT, padx=(0, 15))

        # Min size filter
        ttk.Label(opts_box, text="Min image bytes:").pack(side=tk.LEFT, padx=(10, 4))
        self.spin_min_size = ttk.Spinbox(opts_box, from_=50, to=50000, increment=50, width=6)
        self.spin_min_size.set(200)
        self.spin_min_size.pack(side=tk.LEFT)

        # Action Buttons & Status Banner
        frame_action = ttk.Frame(self.tab_crawler)
        frame_action.pack(fill=tk.X, pady=(0, 8))

        self.btn_start = ttk.Button(frame_action, text=" ▶  Start Crawl & Archive ", command=self.start_crawl)
        self.btn_start.pack(side=tk.LEFT, padx=(0, 8))

        self.btn_stop = ttk.Button(frame_action, text=" ⏹  Stop ", command=self.stop_crawl, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=(0, 12))

        self.btn_view_manager = ttk.Button(
            frame_action, text=" 📂 View Archive in Manager ", command=self.jump_to_manager, state=tk.DISABLED
        )
        self.btn_view_manager.pack(side=tk.LEFT)

        self.lbl_status = ttk.Label(frame_action, text="Ready", font=("Segoe UI", 9, "bold"))
        self.lbl_status.pack(side=tk.RIGHT, padx=5)

        # Progress bar
        self.progressbar = ttk.Progressbar(self.tab_crawler, orient="horizontal", mode="indeterminate")
        self.progressbar.pack(fill=tk.X, pady=(0, 8))

        # Live Console Output
        frame_log = ttk.LabelFrame(self.tab_crawler, text="Live Progress Log", padding=6)
        frame_log.pack(fill=tk.BOTH, expand=True)

        self.txt_log = scrolledtext.ScrolledText(
            frame_log, wrap=tk.WORD, height=12, font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4", insertbackground="white"
        )
        self.txt_log.pack(fill=tk.BOTH, expand=True)

        # Text color tags
        self.txt_log.tag_config("info", foreground="#569cd6")
        self.txt_log.tag_config("success", foreground="#4ec9b0")
        self.txt_log.tag_config("saved", foreground="#6a9955")
        self.txt_log.tag_config("warning", foreground="#ce9178")
        self.txt_log.tag_config("error", foreground="#f44747")

    def on_url_changed(self, event=None):
        url = self.entry_url.get().strip()
        parsed = urlparse(url)
        if parsed.netloc:
            slug = re.sub(r"[^a-zA-Z0-9_\-]", "_", parsed.netloc)
            date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{slug}_{date_str}.zip"
            target_path = os.path.join(archive_store.DEFAULT_ARCHIVES_DIR, filename)
            self.entry_output.delete(0, tk.END)
            self.entry_output.insert(0, target_path)

    def browse_output_zip(self):
        f = filedialog.asksaveasfilename(
            defaultextension=".zip",
            filetypes=[("ZIP Archive", "*.zip")],
            initialdir=archive_store.DEFAULT_ARCHIVES_DIR,
        )
        if f:
            self.entry_output.delete(0, tk.END)
            self.entry_output.insert(0, f)

    def log_message(self, message: str):
        def _append():
            tag = "info"
            if message.startswith("[+]"):
                tag = "saved"
            elif message.startswith("[OK]"):
                tag = "success"
            elif message.startswith("[!]"):
                tag = "warning"
            elif "error" in message.lower() or "failed" in message.lower():
                tag = "error"

            self.txt_log.insert(tk.END, message + "\n", tag)
            self.txt_log.see(tk.END)

        self.after(0, _append)

    def start_crawl(self):
        url = self.entry_url.get().strip()
        if not url or url == "https://":
            messagebox.showerror("Missing URL", "Please enter a valid starting web page URL.")
            return

        if not url.startswith(("http://", "https://")):
            url = "https://" + url
            self.entry_url.delete(0, tk.END)
            self.entry_url.insert(0, url)

        output_zip = self.entry_output.get().strip()
        if not output_zip:
            output_zip = os.path.join(archive_store.DEFAULT_ARCHIVES_DIR, "images.zip")

        try:
            depth = int(self.spin_depth.get())
            max_pages = int(self.spin_max_pages.get())
            min_size = int(self.spin_min_size.get())
        except ValueError:
            messagebox.showerror("Invalid Input", "Depth, Max Pages, and Min Size must be valid numbers.")
            return

        same_domain = self.var_same_domain.get()
        use_browser = self.var_browser.get()

        self.txt_log.delete("1.0", tk.END)
        self.btn_start.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        self.btn_view_manager.config(state=tk.DISABLED)
        self.lbl_status.config(text="Crawling in progress...")
        self.progressbar.start(10)
        self.cancel_event.clear()

        # Run crawler in background thread
        def run_thread():
            summary = crawler.crawl_and_archive(
                start_url=url,
                output_zip=output_zip,
                max_depth=depth,
                max_pages=max_pages,
                same_domain=same_domain,
                use_browser=use_browser,
                min_bytes=min_size,
                callback=self.handle_crawler_callback,
                cancel_event=self.cancel_event,
            )

            # Record in archive store if images were saved
            if summary.get("total_images", 0) > 0:
                rec = archive_store.add_record(
                    filepath=summary["output_zip"],
                    source_url=summary["start_url"],
                    depth=summary["max_depth"],
                    image_count=summary["total_images"],
                    pages_crawled=summary["pages_crawled"],
                    file_size=summary["file_size"],
                )
                self.last_saved_record_id = rec["id"]
                self.after(0, lambda: self.btn_view_manager.config(state=tk.NORMAL))
                self.after(0, self.refresh_archives_list)

            self.after(0, self.finish_crawl)

        self.crawler_thread = threading.Thread(target=run_thread, daemon=True)
        self.crawler_thread.start()

    def handle_crawler_callback(self, data: dict):
        msg_type = data.get("type")
        if msg_type == "log":
            self.log_message(data.get("message", ""))
        elif msg_type == "progress":
            page = data.get("page_num", 0)
            max_p = data.get("max_pages", 0)
            saved = data.get("total_saved", 0)
            depth = data.get("depth", 0)
            status_text = f"Page {page}/{max_p} (Depth {depth}) | {saved} images saved"
            self.after(0, lambda: self.lbl_status.config(text=status_text))

    def stop_crawl(self):
        if self.crawler_thread and self.crawler_thread.is_alive():
            self.log_message("[!] Stopping crawl, finalizing archive...")
            self.cancel_event.set()
            self.btn_stop.config(state=tk.DISABLED)

    def finish_crawl(self):
        self.progressbar.stop()
        self.btn_start.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)
        self.lbl_status.config(text="Crawl completed.")

    def jump_to_manager(self):
        self.notebook.select(self.tab_manager)
        if hasattr(self, "last_saved_record_id"):
            for item in self.tree_archives.get_children():
                if self.tree_archives.item(item, "tags") == (self.last_saved_record_id,):
                    self.tree_archives.selection_set(item)
                    self.tree_archives.see(item)
                    self.on_archive_selected()
                    break

    # =========================================================================
    # TAB 2: ARCHIVE MANAGER & VIEWER
    # =========================================================================
    def setup_manager_tab(self):
        # Manager Toolbar
        toolbar = ttk.Frame(self.tab_manager)
        toolbar.pack(fill=tk.X, pady=(0, 8))

        btn_refresh = ttk.Button(toolbar, text=" 🔄 Refresh / Scan Folder ", command=self.refresh_archives_list)
        btn_refresh.pack(side=tk.LEFT, padx=(0, 6))

        btn_open_folder = ttk.Button(toolbar, text=" 📁 Open in Explorer ", command=self.open_selected_archive_explorer)
        btn_open_folder.pack(side=tk.LEFT, padx=(0, 6))

        btn_extract = ttk.Button(toolbar, text=" 📤 Extract All... ", command=self.extract_selected_archive)
        btn_extract.pack(side=tk.LEFT, padx=(0, 6))

        btn_delete = ttk.Button(toolbar, text=" 🗑 Delete Archive ", command=self.delete_selected_archive)
        btn_delete.pack(side=tk.LEFT, padx=(0, 6))

        self.lbl_archive_count = ttk.Label(toolbar, text="0 archives tracked", font=("Segoe UI", 9))
        self.lbl_archive_count.pack(side=tk.RIGHT, padx=5)

        # Upper Table: Tracked Archives
        frame_upper = ttk.LabelFrame(self.tab_manager, text="Saved Archives", padding=6)
        frame_upper.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        cols = ("name", "url", "depth", "images", "size", "created")
        self.tree_archives = ttk.Treeview(frame_upper, columns=cols, show="headings", height=7, selectmode="browse")

        self.tree_archives.heading("name", text="Archive File")
        self.tree_archives.heading("url", text="Source URL")
        self.tree_archives.heading("depth", text="Depth")
        self.tree_archives.heading("images", text="Images")
        self.tree_archives.heading("size", text="Size")
        self.tree_archives.heading("created", text="Created")

        self.tree_archives.column("name", width=220, anchor=tk.W)
        self.tree_archives.column("url", width=240, anchor=tk.W)
        self.tree_archives.column("depth", width=50, anchor=tk.CENTER)
        self.tree_archives.column("images", width=65, anchor=tk.CENTER)
        self.tree_archives.column("size", width=80, anchor=tk.E)
        self.tree_archives.column("created", width=130, anchor=tk.CENTER)

        scroll_archives = ttk.Scrollbar(frame_upper, orient=tk.VERTICAL, command=self.tree_archives.yview)
        self.tree_archives.configure(yscrollcommand=scroll_archives.set)

        self.tree_archives.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll_archives.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree_archives.bind("<<TreeviewSelect>>", self.on_archive_selected)
        self.tree_archives.bind("<Double-1>", lambda e: self.open_selected_archive_explorer())

        # Lower Split View: Internal Files List + Image Previewer
        paned = ttk.PanedWindow(self.tab_manager, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        # Left: Files inside selected Zip
        frame_files = ttk.LabelFrame(paned, text="Archive Contents", padding=6)
        paned.add(frame_files, weight=3)

        file_cols = ("inner_path", "size")
        self.tree_files = ttk.Treeview(frame_files, columns=file_cols, show="headings", selectmode="browse")
        self.tree_files.heading("inner_path", text="Image Path inside Archive")
        self.tree_files.heading("size", text="Size")
        self.tree_files.column("inner_path", width=300, anchor=tk.W)
        self.tree_files.column("size", width=80, anchor=tk.E)

        scroll_files = ttk.Scrollbar(frame_files, orient=tk.VERTICAL, command=self.tree_files.yview)
        self.tree_files.configure(yscrollcommand=scroll_files.set)

        self.tree_files.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll_files.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree_files.bind("<<TreeviewSelect>>", self.on_file_selected)
        self.tree_files.bind("<Double-1>", lambda e: self.open_selected_image_externally())

        # Right: Live Image Thumbnail Preview
        frame_preview = ttk.LabelFrame(paned, text="Image Preview", padding=6)
        paned.add(frame_preview, weight=2)

        self.lbl_preview_info = ttk.Label(frame_preview, text="Select an image to preview", anchor=tk.CENTER)
        self.lbl_preview_info.pack(fill=tk.X, pady=(0, 4))

        self.lbl_image_canvas = ttk.Label(frame_preview, anchor=tk.CENTER)
        self.lbl_image_canvas.pack(fill=tk.BOTH, expand=True, pady=4)

        btn_open_img = ttk.Button(frame_preview, text=" 🔍 Open in Default Photo Viewer ", command=self.open_selected_image_externally)
        btn_open_img.pack(fill=tk.X, pady=(4, 0))

    def refresh_archives_list(self):
        # Scan archives folder for new files
        records = archive_store.scan_and_sync_folder()

        self.tree_archives.delete(*self.tree_archives.get_children())
        for r in records:
            size_str = format_bytes(r.get("file_size", 0))
            self.tree_archives.insert(
                "",
                tk.END,
                values=(
                    r.get("name"),
                    r.get("source_url"),
                    r.get("depth", 0),
                    r.get("image_count", 0),
                    size_str,
                    r.get("created_at"),
                ),
                tags=(r.get("id"),),
            )

        self.lbl_archive_count.config(text=f"{len(records)} archive(s) tracked")

    def get_selected_archive_record(self) -> dict | None:
        sel = self.tree_archives.selection()
        if not sel:
            return None
        rec_id = self.tree_archives.item(sel[0], "tags")[0]
        records = archive_store.load_registry()
        for r in records:
            if r.get("id") == rec_id:
                return r
        return None

    def on_archive_selected(self, event=None):
        rec = self.get_selected_archive_record()
        self.tree_files.delete(*self.tree_files.get_children())
        self.clear_preview()

        if not rec:
            return

        zip_path = rec.get("filepath", "")
        if not os.path.exists(zip_path):
            self.lbl_preview_info.config(text=f"File not found on disk: {os.path.basename(zip_path)}")
            return

        items = archive_store.inspect_archive(zip_path)
        for it in items:
            self.tree_files.insert(
                "",
                tk.END,
                values=(it["filename"], format_bytes(it["file_size"])),
                tags=(it["filename"],),
            )

        if items:
            first_id = self.tree_files.get_children()[0]
            self.tree_files.selection_set(first_id)
            self.on_file_selected()

    def on_file_selected(self, event=None):
        sel_archive = self.get_selected_archive_record()
        sel_file = self.tree_files.selection()
        if not sel_archive or not sel_file:
            return

        zip_path = sel_archive.get("filepath", "")
        inner_filename = self.tree_files.item(sel_file[0], "tags")[0]

        img_bytes = archive_store.read_image_from_zip(zip_path, inner_filename)
        if not img_bytes:
            self.lbl_preview_info.config(text="Could not load image bytes.")
            return

        # Render preview using Pillow
        try:
            pil_img = Image.open(io.BytesIO(img_bytes))
            orig_w, orig_h = pil_img.size
            fmt = pil_img.format or os.path.splitext(inner_filename)[1].upper().strip(".")

            self.lbl_preview_info.config(
                text=f"{os.path.basename(inner_filename)} | {orig_w}x{orig_h} | {fmt} ({format_bytes(len(img_bytes))})"
            )

            # Fit into preview window (e.g. 280 x 240)
            preview_w, preview_h = 280, 240
            pil_img.thumbnail((preview_w, preview_h), Image.Resampling.LANCZOS)

            self.current_preview_image = ImageTk.PhotoImage(pil_img)
            self.lbl_image_canvas.config(image=self.current_preview_image, text="")

        except Exception as e:
            # Vector SVGs or unsupported formats
            self.lbl_preview_info.config(text=f"{os.path.basename(inner_filename)} ({format_bytes(len(img_bytes))})")
            self.lbl_image_canvas.config(
                image="", text="[ Vector SVG / Preview not supported ]\nDouble click to open externally"
            )

    def clear_preview(self):
        self.lbl_preview_info.config(text="Select an image to preview")
        self.lbl_image_canvas.config(image="", text="")
        self.current_preview_image = None

    def open_selected_archive_explorer(self):
        rec = self.get_selected_archive_record()
        if not rec:
            messagebox.showinfo("Select Archive", "Please select an archive first.")
            return

        path = rec.get("filepath", "")
        if not os.path.exists(path):
            messagebox.showerror("Not Found", f"Archive file does not exist on disk:\n{path}")
            return

        # Highlight file in Windows Explorer
        try:
            subprocess.run(["explorer", f"/select,{os.path.normpath(path)}"])
        except Exception as e:
            os.startfile(os.path.dirname(path))

    def extract_selected_archive(self):
        rec = self.get_selected_archive_record()
        if not rec:
            messagebox.showinfo("Select Archive", "Please select an archive first.")
            return

        zip_path = rec.get("filepath", "")
        if not os.path.exists(zip_path):
            messagebox.showerror("Not Found", "Archive file does not exist on disk.")
            return

        target_dir = filedialog.askdirectory(title="Select Extraction Destination Folder")
        if not target_dir:
            return

        try:
            archive_store.extract_archive(zip_path, target_dir)
            if messagebox.askyesno("Success", f"Archive extracted successfully to:\n{target_dir}\n\nOpen folder now?"):
                os.startfile(target_dir)
        except Exception as e:
            messagebox.showerror("Extraction Error", f"Failed to extract archive: {e}")

    def delete_selected_archive(self):
        rec = self.get_selected_archive_record()
        if not rec:
            messagebox.showinfo("Select Archive", "Please select an archive to delete.")
            return

        name = rec.get("name", "Archive")
        if messagebox.askyesno("Confirm Delete", f"Are you sure you want to delete '{name}' and remove it from disk?"):
            archive_store.delete_record(rec["id"], delete_file=True)
            self.refresh_archives_list()
            self.tree_files.delete(*self.tree_files.get_children())
            self.clear_preview()

    def open_selected_image_externally(self):
        sel_archive = self.get_selected_archive_record()
        sel_file = self.tree_files.selection()
        if not sel_archive or not sel_file:
            return

        zip_path = sel_archive.get("filepath", "")
        inner_filename = self.tree_files.item(sel_file[0], "tags")[0]

        img_bytes = archive_store.read_image_from_zip(zip_path, inner_filename)
        if not img_bytes:
            return

        # Write to temp file and launch default viewer
        _, ext = os.path.splitext(inner_filename)
        if not ext:
            ext = ".png"
        temp_file = os.path.join(tempfile.gettempdir(), f"preview_{int(time.time())}{ext}")
        with open(temp_file, "wb") as f:
            f.write(img_bytes)

        try:
            os.startfile(temp_file)
        except Exception as e:
            messagebox.showerror("Error", f"Could not launch viewer: {e}")


def main():
    app = ImageScraperApp()
    app.mainloop()


if __name__ == "__main__":
    main()
