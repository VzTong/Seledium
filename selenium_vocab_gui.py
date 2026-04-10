from __future__ import annotations

import csv
import json
import os
import queue
import sys
import threading
import traceback
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from selenium_vocab_tool import TopicResult, scrape_vocabulary


# =============================================================================
# Ghi chú tổng quan file
# - File này quản lý toàn bộ giao diện Tkinter của ứng dụng.
# - Chia làm 2 tab chính:
#   1) Tab "Cào dữ liệu": cấu hình và chạy scraper.
#   2) Tab "Xem CSV": đọc all_vocabulary.csv hoặc all_topics.json để xem dữ liệu.
# - Luồng chạy scraper sử dụng thread nền + queue để UI luôn mượt.
# =============================================================================


def app_base_dir() -> Path:
    """Trả về thư mục gốc thực tế của app.

    - Khi chạy file .py: dùng thư mục chứa file mã nguồn.
    - Khi chạy file .exe (PyInstaller one-file): dùng thư mục chứa file .exe.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


class PlaceholderEntry(tk.Entry):
    """Ô nhập có placeholder tiếng mờ khi trống.

    Dùng cho các ô quan trọng như URL đầu vào để người dùng dễ nhận biết.
    """

    def __init__(self, master: tk.Misc, placeholder: str, **kwargs) -> None:
        """Khởi tạo ô nhập placeholder với style tối và caret sáng."""
        super().__init__(master, **kwargs)
        self.placeholder = placeholder
        self._is_placeholder = False
        self._normal_fg = "#e8eeff"
        self._placeholder_fg = "#94a3b8"

        self.configure(
            bg="#0f1630",
            fg=self._normal_fg,
            insertbackground="#9ff3ff",
            insertwidth=2,
            insertontime=500,
            insertofftime=300,
            relief="flat",
            highlightthickness=1,
            highlightbackground="#2a355c",
            highlightcolor="#4cc9f0",
            font=("Segoe UI", 10),
        )

        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)
        self._show_placeholder()

    def _show_placeholder(self) -> None:
        """Hiển thị text placeholder nếu ô hiện tại đang rỗng."""
        if self.get().strip():
            return
        self._is_placeholder = True
        self.configure(fg=self._placeholder_fg)
        self.delete(0, "end")
        self.insert(0, self.placeholder)

    def _hide_placeholder(self) -> None:
        """Ẩn placeholder khi người dùng bắt đầu nhập."""
        if not self._is_placeholder:
            return
        self._is_placeholder = False
        self.configure(fg=self._normal_fg)
        self.delete(0, "end")

    def _on_focus_in(self, _event: tk.Event) -> None:
        """Khi focus vào ô nhập: bỏ placeholder để gõ thật."""
        self._hide_placeholder()

    def _on_focus_out(self, _event: tk.Event) -> None:
        """Khi mất focus: nếu rỗng thì hiện lại placeholder."""
        if not self.get().strip():
            self._show_placeholder()

    def get_value(self) -> str:
        """Lấy giá trị thực của ô nhập (không trả về placeholder)."""
        if self._is_placeholder:
            return ""
        return self.get().strip()


class BrightEntry(tk.Entry):
    """Ô nhập chuẩn của app với caret sáng, dễ nhìn trên nền tối."""

    def __init__(self, master: tk.Misc, textvariable: tk.Variable | None = None, **kwargs) -> None:
        super().__init__(master, textvariable=textvariable, **kwargs)
        self.configure(
            bg="#0f1630",
            fg="#e8eeff",
            insertbackground="#9ff3ff",
            insertwidth=2,
            insertontime=500,
            insertofftime=300,
            relief="flat",
            highlightthickness=1,
            highlightbackground="#2a355c",
            highlightcolor="#4cc9f0",
            font=("Segoe UI", 10),
        )


class VocabularyScraperApp(tk.Tk):
    """Cửa sổ chính của ứng dụng Seledium Galaxy Scraper."""

    def __init__(self) -> None:
        """Khởi tạo state, giao diện và vòng poll queue."""
        super().__init__()
        self.title("Seledium Galaxy Scraper")
        self.geometry("1000x700")
        self.resizable(True, True)
        self.minsize(1000, 700)
        self.option_add("*insertOffTime", 300)
        self.option_add("*insertOnTime", 500)

        icon_path = app_base_dir() / "app_icon.ico"
        if icon_path.exists():
            try:
                self.iconbitmap(default=str(icon_path))
            except tk.TclError:
                pass

        # Queue giao tiếp giữa worker thread và UI thread.
        self.queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.worker_thread: threading.Thread | None = None

        # Kết quả mới nhất trả về từ scraper để render preview.
        self.latest_results: list[TopicResult] = []
        self.log_file_path: Path | None = None

        # Dữ liệu đã normalize từ CSV/JSON để lọc và hiển thị.
        self.csv_rows: list[dict[str, str]] = []
        self.current_csv_path: Path | None = None

        self.total_topics = 0
        self.done_topics = 0
        self.ok_topics = 0
        self.skip_topics = 0
        self.include_phonetic_var = tk.BooleanVar(value=True)
        self.include_example_var = tk.BooleanVar(value=True)
        self.include_note_definition_all_var = tk.BooleanVar(value=False)

        self._build_style()
        self._build_ui()
        self._center_window()
        self.after(120, self._poll_queue)

    def _center_window(self) -> None:
        """Canh cửa sổ vào giữa màn hình sau khi UI được tạo xong."""
        self.update_idletasks()
        width = self.winfo_width() or 1000
        height = self.winfo_height() or 700
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _build_style(self) -> None:
        """Khai báo toàn bộ style ttk dùng trong app (theme tối)."""
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        # Bộ màu chủ đạo để dễ tinh chỉnh giao diện tập trung một chỗ.
        bg = "#0b1020"
        card = "#151c33"
        edge = "#2a355c"
        text = "#e6ecff"
        muted = "#9fb0e8"

        self.configure(background=bg)

        style.configure("App.TFrame", background=bg)
        style.configure("Card.TLabelframe", background=card, foreground=text, borderwidth=1, relief="solid")
        style.configure("Card.TLabelframe.Label", background=card, foreground="#c9d7ff", font=("Segoe UI", 10, "bold"))
        style.configure("Title.TLabel", background=bg, foreground=text, font=("Segoe UI", 22, "bold"))
        style.configure("Sub.TLabel", background=bg, foreground=muted, font=("Segoe UI", 10))
        style.configure("Card.TLabel", background=card, foreground="#dce6ff", font=("Segoe UI", 10))

        style.configure("App.TEntry", fieldbackground="#0f1630", foreground="#e8eeff", bordercolor=edge, padding=7)
        style.configure("App.TCheckbutton", background=card, foreground="#dce6ff", font=("Segoe UI", 10))
        style.map("App.TCheckbutton", background=[("active", card)], foreground=[("active", "#f0f5ff")])

        style.configure("App.TButton", background="#3341a8", foreground="#f5f7ff", padding=(12, 7), font=("Segoe UI", 10, "bold"))
        style.map("App.TButton", background=[("active", "#4253cf")])
        style.configure("Start.TButton", background="#0f766e", foreground="#effffd", padding=(14, 8), font=("Segoe UI", 10, "bold"))
        style.map("Start.TButton", background=[("active", "#109587")])

        style.configure("App.Horizontal.TProgressbar", troughcolor="#1b264a", background="#22d3ee")

        style.configure("App.TNotebook", background=bg, borderwidth=0)
        style.configure("App.TNotebook.Tab", background="#1d2650", foreground="#cbd6ff", padding=(16, 8), font=("Segoe UI", 10, "bold"))
        style.map("App.TNotebook.Tab", background=[("selected", "#3341a8")], foreground=[("selected", "#ffffff")])

        style.configure("App.Treeview", background="#0f1630", fieldbackground="#0f1630", foreground="#e8eeff", rowheight=26, borderwidth=0)
        style.configure("App.Treeview.Heading", background="#202c57", foreground="#d7e2ff", font=("Segoe UI", 10, "bold"))
        style.map("App.Treeview", background=[("selected", "#3341a8")], foreground=[("selected", "#ffffff")])

        style.configure("App.TCombobox", fieldbackground="#0f1630", foreground="#e8eeff")

    def _build_ui(self) -> None:
        """Dựng layout root và 2 tab chính: cào dữ liệu + xem CSV."""
        root = ttk.Frame(self, style="App.TFrame", padding=14)
        root.pack(fill="both", expand=True)

        ttk.Label(root, text="Seledium Galaxy Scraper", style="Title.TLabel").pack(anchor="w")
        ttk.Label(root, text="Cào dữ liệu và xem CSV rõ ràng theo từng topic", style="Sub.TLabel").pack(anchor="w", pady=(2, 12))

        self.main_tabs = ttk.Notebook(root, style="App.TNotebook")
        self.main_tabs.pack(fill="both", expand=True)

        self.scrape_tab = ttk.Frame(self.main_tabs, style="App.TFrame")
        self.csv_tab = ttk.Frame(self.main_tabs, style="App.TFrame")
        self.main_tabs.add(self.scrape_tab, text="Cào dữ liệu")
        self.main_tabs.add(self.csv_tab, text="Xem CSV")

        self._build_scrape_tab()
        self._build_csv_tab()

    def _build_scrape_tab(self) -> None:
        """Dựng tab cấu hình và chạy scraper."""
        form = ttk.LabelFrame(self.scrape_tab, text="Thiết lập", style="Card.TLabelframe", padding=12)
        form.pack(fill="x", padx=2, pady=(2, 10))

        # Dùng biến row để xếp các control theo dạng lưới tuần tự.
        row = 0
        ttk.Label(form, text="Link gốc", style="Card.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 10), pady=6)
        self.url_entry = PlaceholderEntry(form, placeholder="https://example.com/vocabulary")
        self.url_entry.grid(row=row, column=1, columnspan=3, sticky="ew", pady=6)
        row += 1

        ttk.Label(form, text="Thư mục output", style="Card.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 10), pady=6)
        self.output_var = tk.StringVar(value=str(app_base_dir() / "output"))
        BrightEntry(form, textvariable=self.output_var).grid(row=row, column=1, sticky="ew", pady=6)
        ttk.Button(form, text="Chọn", style="App.TButton", command=self._choose_output).grid(row=row, column=2, padx=(8, 0), pady=6)
        ttk.Button(form, text="Mở thư mục", style="App.TButton", command=self._open_output_folder).grid(row=row, column=3, padx=(8, 0), pady=6)
        row += 1

        self.headless_var = tk.BooleanVar(value=True)
        self.resume_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(form, text="Chạy ẩn (headless)", style="App.TCheckbutton", variable=self.headless_var).grid(row=row, column=1, sticky="w", pady=4)
        ttk.Checkbutton(form, text="Resume từ lần chạy trước", style="App.TCheckbutton", variable=self.resume_var).grid(row=row, column=2, sticky="w", pady=4)
        row += 1

        ttk.Checkbutton(form, text="Lưu phiên âm", style="App.TCheckbutton", variable=self.include_phonetic_var).grid(row=row, column=1, sticky="w", pady=4)
        ttk.Checkbutton(form, text="Lưu ví dụ", style="App.TCheckbutton", variable=self.include_example_var).grid(row=row, column=2, sticky="w", pady=4)
        row += 1

        # Mặc định chỉ giữ note/definition cho từ trùng chữ (đa nghĩa) để output gọn hơn.
        ttk.Checkbutton(
            form,
            text="Lưu note/definition cho tất cả từ",
            style="App.TCheckbutton",
            variable=self.include_note_definition_all_var,
        ).grid(row=row, column=1, columnspan=2, sticky="w", pady=4)
        row += 1

        ttk.Label(form, text="CSS selector từ vựng", style="Card.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 10), pady=6)
        self.vocab_selector_var = tk.StringVar(value="table td, li, .word, .vocab")
        BrightEntry(form, textvariable=self.vocab_selector_var).grid(row=row, column=1, columnspan=3, sticky="ew", pady=6)
        row += 1

        ttk.Label(form, text="ChromeDriver path", style="Card.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 10), pady=6)
        self.driver_path_var = tk.StringVar()
        BrightEntry(form, textvariable=self.driver_path_var).grid(row=row, column=1, sticky="ew", pady=6)
        ttk.Button(form, text="Chọn driver", style="App.TButton", command=self._choose_driver).grid(row=row, column=2, padx=(8, 0), pady=6)

        form.columnconfigure(1, weight=1)

        # Action bar: nút chạy + tiến trình.
        action = ttk.Frame(self.scrape_tab, style="App.TFrame")
        action.pack(fill="x", pady=(0, 8))

        self.start_button = ttk.Button(action, text="Bắt đầu cào", style="Start.TButton", command=self._start_scrape)
        self.start_button.pack(side="left", padx=(0, 8))
        ttk.Button(action, text="Mở log", style="App.TButton", command=self._open_current_log).pack(side="left", padx=(0, 8))
        ttk.Button(action, text="Sang tab CSV", style="App.TButton", command=lambda: self.main_tabs.select(self.csv_tab)).pack(side="left")

        self.progress = ttk.Progressbar(action, style="App.Horizontal.TProgressbar", mode="determinate", maximum=100)
        self.progress.pack(side="left", fill="x", expand=True, padx=(14, 8))

        self.progress_text_var = tk.StringVar(value="0%")
        ttk.Label(action, textvariable=self.progress_text_var, style="Sub.TLabel").pack(side="left")

        self.stats_var = tk.StringVar(value="Processed: 0 | OK: 0 | Skip: 0")
        ttk.Label(self.scrape_tab, textvariable=self.stats_var, style="Sub.TLabel").pack(anchor="w", pady=(0, 8))

        # Khu vực dưới: bên trái preview kết quả, bên phải log runtime.
        content = ttk.Panedwindow(self.scrape_tab, orient="horizontal")
        content.pack(fill="both", expand=True)

        preview_frame = ttk.LabelFrame(content, text="Kết quả phiên chạy", style="Card.TLabelframe", padding=10)
        log_frame = ttk.LabelFrame(content, text="Log", style="Card.TLabelframe", padding=10)
        content.add(preview_frame, weight=2)
        content.add(log_frame, weight=3)

        self.topic_tree = ttk.Treeview(preview_frame, style="App.Treeview", columns=("topic", "words"), show="headings", height=8)
        self.topic_tree.heading("topic", text="Topic")
        self.topic_tree.heading("words", text="Số từ")
        self.topic_tree.column("topic", width=360)
        self.topic_tree.column("words", width=80, anchor="center")
        self.topic_tree.pack(fill="x")
        self.topic_tree.bind("<<TreeviewSelect>>", self._on_topic_selected)

        self.preview_entries = ttk.Treeview(
            preview_frame,
            style="App.Treeview",
            columns=("order", "word", "phonetic", "example", "parts_of_speech", "definition_en"),
            show="headings",
            height=9,
        )
        self.preview_entries.heading("order", text="#")
        self.preview_entries.heading("word", text="Word")
        self.preview_entries.heading("phonetic", text="Phonetic")
        self.preview_entries.heading("example", text="Example")
        self.preview_entries.heading("parts_of_speech", text="Parts of Speech")
        self.preview_entries.heading("definition_en", text="Definition (EN)")
        self.preview_entries.column("order", width=44, anchor="center")
        self.preview_entries.column("word", width=140)
        self.preview_entries.column("phonetic", width=120)
        self.preview_entries.column("example", width=260)
        self.preview_entries.column("parts_of_speech", width=110)
        self.preview_entries.column("definition_en", width=300)
        self.preview_entries.pack(fill="both", expand=True, pady=(8, 0))

        self.log_text = tk.Text(
            log_frame,
            wrap="word",
            bg="#0f1630",
            fg="#e8eeff",
            insertbackground="#7dd3fc",
            relief="flat",
            font=("Consolas", 10),
            padx=10,
            pady=10,
        )
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        log_scroll.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=log_scroll.set)

        self.log_text.tag_configure("ok", foreground="#4ade80")
        self.log_text.tag_configure("warn", foreground="#fb923c")
        self.log_text.tag_configure("error", foreground="#f87171")
        self.log_text.tag_configure("save", foreground="#38bdf8")
        self.log_text.tag_configure("info", foreground="#a5b4fc")

        self.status_var = tk.StringVar(value="Sẵn sàng")
        ttk.Label(self.scrape_tab, textvariable=self.status_var, style="Sub.TLabel").pack(anchor="w", pady=(8, 0))

    def _build_csv_tab(self) -> None:
        """Dựng tab xem dữ liệu CSV/JSON theo topic."""
        top = ttk.LabelFrame(self.csv_tab, text="CSV Viewer", style="Card.TLabelframe", padding=12)
        top.pack(fill="x", padx=2, pady=(2, 10))

        self.csv_path_var = tk.StringVar(value="Chưa chọn file CSV")
        self.csv_topic_filter_var = tk.StringVar(value="Tất cả")
        self.csv_search_var = tk.StringVar(value="")

        # Hàng điều khiển: nạp file, lọc topic, tìm kiếm từ khóa.
        controls = ttk.Frame(top, style="App.TFrame")
        controls.pack(fill="x")

        ttk.Button(controls, text="CSV tổng mới nhất", style="App.TButton", command=self._load_latest_all_csv).grid(row=0, column=0, padx=(0, 14), pady=4)
        ttk.Button(controls, text="JSON tổng mới nhất", style="App.TButton", command=self._load_latest_all_json).grid(row=0, column=2, padx=(0, 14), pady=4)
        ttk.Button(controls, text="Mở output", style="App.TButton", command=self._open_output_folder).grid(row=0, column=1, padx=(0, 14), pady=4)

        ttk.Label(controls, text="Topic", style="Sub.TLabel").grid(row=0, column=5, padx=(0, 6), pady=4, sticky="w")
        self.csv_topic_combo = ttk.Combobox(controls, textvariable=self.csv_topic_filter_var, values=["Tất cả"], state="readonly", width=30, style="App.TCombobox")
        self.csv_topic_combo.grid(row=0, column=6, padx=(0, 12), pady=4, sticky="w")
        self.csv_topic_combo.bind("<<ComboboxSelected>>", lambda _e: self._apply_csv_filters())

        ttk.Label(controls, text="Tìm", style="Sub.TLabel").grid(row=0, column=7, padx=(0, 6), pady=4, sticky="w")
        search = BrightEntry(controls, textvariable=self.csv_search_var)
        search.grid(row=0, column=8, padx=(0, 8), pady=4, sticky="ew")
        search.bind("<KeyRelease>", lambda _e: self._apply_csv_filters())

        ttk.Button(controls, text="Làm mới", style="App.TButton", command=self._apply_csv_filters).grid(row=0, column=9, pady=4)
        controls.columnconfigure(8, weight=1)

        ttk.Label(top, textvariable=self.csv_path_var, style="Sub.TLabel").pack(anchor="w", pady=(8, 0))

        # Thân tab: cây topic bên trái, bảng chi tiết bên phải.
        body = ttk.Panedwindow(self.csv_tab, orient="horizontal")
        body.pack(fill="both", expand=True)

        left = ttk.LabelFrame(body, text="Danh sách topic", style="Card.TLabelframe", padding=10)
        right = ttk.LabelFrame(body, text="Chi tiết từ vựng", style="Card.TLabelframe", padding=10)
        body.add(left, weight=1)
        body.add(right, weight=4)

        self.csv_topic_tree = ttk.Treeview(left, style="App.Treeview", columns=("topic", "count"), show="headings")
        self.csv_topic_tree.heading("topic", text="Topic")
        self.csv_topic_tree.heading("count", text="Số từ")
        self.csv_topic_tree.column("topic", width=260)
        self.csv_topic_tree.column("count", width=70, anchor="center")
        self.csv_topic_tree.pack(side="left", fill="both", expand=True)
        self.csv_topic_tree.bind("<<TreeviewSelect>>", self._on_csv_topic_selected)

        topic_scroll = ttk.Scrollbar(left, orient="vertical", command=self.csv_topic_tree.yview)
        topic_scroll.pack(side="right", fill="y")
        self.csv_topic_tree.configure(yscrollcommand=topic_scroll.set)

        table_wrap = ttk.Frame(right, style="App.TFrame")
        table_wrap.pack(fill="both", expand=True)

        self.csv_tree = ttk.Treeview(
            table_wrap,
            style="App.Treeview",
            columns=("order", "phonetic", "example", "note", "definition_en"),
            show="tree headings",
        )
        self.csv_tree.heading("#0", text="Word / Topic")
        self.csv_tree.heading("order", text="#")
        self.csv_tree.heading("phonetic", text="Phonetic")
        self.csv_tree.heading("example", text="Example")
        self.csv_tree.heading("note", text="Note")
        self.csv_tree.heading("definition_en", text="Definition (EN)")
        self.csv_tree.column("#0", width=280)
        self.csv_tree.column("order", width=52, anchor="center")
        self.csv_tree.column("phonetic", width=140)
        self.csv_tree.column("example", width=280)
        self.csv_tree.column("note", width=120)
        self.csv_tree.column("definition_en", width=360)
        self.csv_tree.pack(side="left", fill="both", expand=True)

        y_scroll = ttk.Scrollbar(table_wrap, orient="vertical", command=self.csv_tree.yview)
        y_scroll.pack(side="right", fill="y")
        self.csv_tree.configure(yscrollcommand=y_scroll.set)

        x_scroll = ttk.Scrollbar(right, orient="horizontal", command=self.csv_tree.xview)
        x_scroll.pack(fill="x", pady=(8, 0))
        self.csv_tree.configure(xscrollcommand=x_scroll.set)

        self.csv_summary_var = tk.StringVar(value="0 dòng")
        ttk.Label(self.csv_tab, textvariable=self.csv_summary_var, style="Sub.TLabel").pack(anchor="w", pady=(8, 0))

    def _choose_output(self) -> None:
        """Mở dialog chọn thư mục output."""
        selected = filedialog.askdirectory(title="Chọn thư mục output")
        if selected:
            self.output_var.set(selected)

    def _open_output_folder(self) -> None:
        """Mở nhanh thư mục output bằng Explorer."""
        folder = Path(self.output_var.get().strip()).expanduser().resolve()
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(folder)

    def _choose_driver(self) -> None:
        """Chọn đường dẫn chromedriver.exe khi cần chạy offline."""
        selected = filedialog.askopenfilename(
            title="Chọn chromedriver.exe",
            filetypes=[("Chrome Driver", "chromedriver.exe"), ("Executable", "*.exe"), ("All files", "*.*")],
        )
        if selected:
            self.driver_path_var.set(selected)

    def _open_current_log(self) -> None:
        """Mở file log của phiên chạy hiện tại (nếu có)."""
        if self.log_file_path and self.log_file_path.exists():
            os.startfile(str(self.log_file_path))
        else:
            messagebox.showinfo("Log", "Chưa có file log của phiên hiện tại.")

    def _find_latest_file(self, filename: str) -> Path | None:
        """Tìm file mới nhất theo tên trong các nhánh output phổ biến."""
        roots: list[Path] = []
        manual = self.output_var.get().strip()
        if manual:
            roots.append(Path(manual).expanduser().resolve())
        roots.append((Path.cwd() / "output").resolve())
        roots.append((app_base_dir() / "output").resolve())

        seen: set[str] = set()
        uniq: list[Path] = []
        for root in roots:
            key = str(root).lower()
            if key in seen:
                continue
            seen.add(key)
            uniq.append(root)

        candidates: list[Path] = []
        for root in uniq:
            if root.exists():
                candidates.extend(root.glob(f"**/{filename}"))

        if not candidates:
            return None
        return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0]

    def _load_latest_all_csv(self) -> None:
        """Nạp file all_vocabulary.csv mới nhất và đẩy lên viewer."""
        selected = self._find_latest_file("all_vocabulary.csv")
        if not selected:
            messagebox.showinfo("CSV", "Không tìm thấy all_vocabulary.csv trong output.")
            return
        output_anchor = next((p for p in selected.parents if p.name.lower() == "output"), selected.parent)
        self.output_var.set(str(output_anchor))
        self._load_csv(selected)

    def _load_latest_all_json(self) -> None:
        """Nạp file all_topics.json mới nhất và đẩy lên viewer."""
        selected = self._find_latest_file("all_topics.json")
        if not selected:
            messagebox.showinfo("JSON", "Không tìm thấy all_topics.json trong output.")
            return
        output_anchor = next((p for p in selected.parents if p.name.lower() == "output"), selected.parent)
        self.output_var.set(str(output_anchor))
        self._load_json(selected)

    def _load_csv(self, path: Path) -> None:
        """Đọc CSV, normalize về cấu trúc chung để hiển thị."""
        try:
            with path.open("r", newline="", encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
        except Exception as exc:
            messagebox.showerror("CSV", f"Không thể đọc CSV:\n{exc}")
            return

        # Chuẩn hóa dữ liệu để các nguồn CSV cũ/mới đều hiển thị được.
        normalized: list[dict[str, str]] = []
        fallback_topic = path.stem
        for row in rows:
            topic = (row.get("topic_title") or "").strip() or (row.get("topic") or "").strip() or fallback_topic
            order = (row.get("order") or "").strip()
            word = (row.get("word") or "").strip() or (row.get("vocabulary") or "").strip()
            phonetic = (row.get("phonetic") or "").strip()
            example = (row.get("example") or "").strip()
            note = (row.get("note") or "").strip()
            definition_en = (row.get("definition_en") or "").strip()
            if not word:
                continue
            normalized.append({
                "topic": topic,
                "order": order,
                "word": word,
                "phonetic": phonetic,
                "example": example,
                "note": note,
                "definition_en": definition_en,
            })

        self.current_csv_path = path
        self.csv_rows = normalized
        self.csv_path_var.set(str(path))

        topics = sorted({r["topic"] for r in normalized if r.get("topic")})
        self.csv_topic_combo.configure(values=["Tất cả"] + topics)
        self.csv_topic_filter_var.set("Tất cả")

        self._refresh_csv_topic_tree(normalized)
        self._apply_csv_filters()
        self.main_tabs.select(self.csv_tab)
        self._log(f"INFO: CSV Viewer tải {len(normalized)} dòng từ {path.name}")

    def _load_json(self, path: Path) -> None:
        """Đọc JSON (all_topics.json) và chuyển sang dạng rows cho bảng."""
        try:
            with path.open("r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception as exc:
            messagebox.showerror("JSON", f"Không thể đọc JSON:\n{exc}")
            return

        # Chuẩn hóa JSON về chung format với CSV để tái sử dụng bộ lọc.
        normalized: list[dict[str, str]] = []
        if isinstance(payload, list):
            for topic_item in payload:
                if not isinstance(topic_item, dict):
                    continue
                topic = str(topic_item.get("topic_title", "")).strip() or "(không tên topic)"
                entries = topic_item.get("entries", [])
                if isinstance(entries, list) and entries:
                    for idx, row in enumerate(entries, start=1):
                        if not isinstance(row, dict):
                            continue
                        word = str(row.get("word", "")).strip()
                        if not word:
                            continue
                        normalized.append(
                            {
                                "topic": topic,
                                "order": str(row.get("order", idx)).strip(),
                                "word": word,
                                "phonetic": str(row.get("phonetic", "")).strip(),
                                "example": str(row.get("example", "")).strip(),
                                "note": str(row.get("note", "")).strip(),
                                "definition_en": str(row.get("definition_en", "")).strip(),
                            }
                        )
                else:
                    vocab = topic_item.get("vocabulary", [])
                    if isinstance(vocab, list):
                        for idx, word in enumerate(vocab, start=1):
                            text = str(word).strip()
                            if not text:
                                continue
                            normalized.append(
                                {
                                    "topic": topic,
                                    "order": str(idx),
                                    "word": text,
                                    "phonetic": "",
                                    "example": "",
                                    "note": "",
                                    "definition_en": "",
                                }
                            )

        self.current_csv_path = path
        self.csv_rows = normalized
        self.csv_path_var.set(str(path))

        topics = sorted({r["topic"] for r in normalized if r.get("topic")})
        self.csv_topic_combo.configure(values=["Tất cả"] + topics)
        self.csv_topic_filter_var.set("Tất cả")

        self._refresh_csv_topic_tree(normalized)
        self._apply_csv_filters()
        self.main_tabs.select(self.csv_tab)
        self._log(f"INFO: JSON Viewer tải {len(normalized)} dòng từ {path.name}")

    def _refresh_csv_topic_tree(self, rows: list[dict[str, str]]) -> None:
        """Render danh sách topic + số lượng từ ở panel bên trái."""
        counts: dict[str, int] = {}
        for row in rows:
            topic = row.get("topic", "")
            if not topic:
                continue
            counts[topic] = counts.get(topic, 0) + 1

        for iid in self.csv_topic_tree.get_children():
            self.csv_topic_tree.delete(iid)

        for idx, (topic, count) in enumerate(sorted(counts.items(), key=lambda x: x[0].lower())):
            self.csv_topic_tree.insert("", "end", iid=f"topic-{idx}", values=(topic, count))

    def _on_csv_topic_selected(self, _event: object) -> None:
        """Khi chọn topic ở cây trái thì lọc bảng chi tiết theo topic đó."""
        selected = self.csv_topic_tree.selection()
        if not selected:
            return
        values = self.csv_topic_tree.item(selected[0], "values")
        if not values:
            return
        self.csv_topic_filter_var.set(str(values[0]))
        self._apply_csv_filters()

    def _apply_csv_filters(self) -> None:
        """Áp dụng filter theo topic + từ khóa và render dạng cây topic -> word."""
        topic_filter = self.csv_topic_filter_var.get().strip()
        keyword = self.csv_search_var.get().strip().lower()

        filtered: list[dict[str, str]] = []
        for row in self.csv_rows:
            if topic_filter and topic_filter != "Tất cả" and row.get("topic", "") != topic_filter:
                continue
            if keyword:
                hay = " ".join(
                    [
                        row.get("topic", ""),
                        row.get("word", ""),
                        row.get("phonetic", ""),
                        row.get("example", ""),
                        row.get("note", ""),
                        row.get("definition_en", ""),
                    ]
                ).lower()
                if keyword not in hay:
                    continue
            filtered.append(row)

        for iid in self.csv_tree.get_children():
            self.csv_tree.delete(iid)

        # Gom nhóm theo topic để hiển thị parent/child rõ ràng.
        grouped: dict[str, list[dict[str, str]]] = {}
        for row in filtered:
            topic = row.get("topic", "(không tên topic)")
            grouped.setdefault(topic, []).append(row)

        topic_seq = sorted(grouped.keys(), key=lambda x: x.lower())
        for t_index, topic in enumerate(topic_seq):
            topic_rows = grouped[topic]
            parent_id = f"topic-parent-{t_index}"
            self.csv_tree.insert(
                "",
                "end",
                iid=parent_id,
                text=f"{topic} ({len(topic_rows)} từ)",
                values=("", "", "", "", ""),
                open=True,
            )
            topic_rows = sorted(topic_rows, key=lambda r: int((r.get("order") or "0") or "0"))
            for w_index, row in enumerate(topic_rows):
                self.csv_tree.insert(
                    parent_id,
                    "end",
                    iid=f"word-{t_index}-{w_index}",
                    text=row.get("word", ""),
                    values=(
                        row.get("order", ""),
                        row.get("phonetic", ""),
                        row.get("example", ""),
                        row.get("note", ""),
                        row.get("definition_en", ""),
                    ),
                )

        self.csv_summary_var.set(f"Hiển thị {len(filtered)}/{len(self.csv_rows)} dòng")

    def _set_busy(self, busy: bool) -> None:
        """Khóa/mở nút chạy để tránh bấm nhiều lần khi đang crawl."""
        self.start_button.configure(state="disabled" if busy else "normal")

    def _set_progress(self, done: int, total: int) -> None:
        """Cập nhật progress bar và % hoàn thành."""
        if total <= 0:
            self.progress.configure(maximum=100, value=0)
            self.progress_text_var.set("0%")
            return
        self.progress.configure(maximum=total, value=done)
        self.progress_text_var.set(f"{int(done * 100 / total)}%")

    def _update_stats(self) -> None:
        """Cập nhật thống kê runtime (Processed / OK / Skip)."""
        self.stats_var.set(f"Processed: {self.done_topics} | OK: {self.ok_topics} | Skip: {self.skip_topics}")

    def _parse_selectors(self, raw: str) -> list[str] | None:
        """Tách chuỗi selector (phân tách bằng dấu phẩy) thành list."""
        items = [s.strip() for s in raw.split(",") if s.strip()]
        return items or None

    def _start_scrape(self) -> None:
        """Validate input, reset UI state và khởi chạy worker thread."""
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showinfo("Đang chạy", "Vui lòng chờ phiên hiện tại hoàn tất.")
            return

        start_url = self.url_entry.get_value()
        if not start_url:
            messagebox.showerror("Thiếu link", "Vui lòng nhập link gốc.")
            return

        output_dir = Path(self.output_var.get().strip()).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        logs_dir = output_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        self.log_file_path = logs_dir / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

        self.log_text.delete("1.0", "end")
        self.latest_results = []
        self._refresh_preview([])

        self.total_topics = 0
        self.done_topics = 0
        self.ok_topics = 0
        self.skip_topics = 0
        self._update_stats()
        self._set_busy(True)
        self._set_progress(0, 1)
        self.status_var.set("Đang chạy...")

        # Chạy scraper ở thread nền để giao diện không bị treo.
        self.worker_thread = threading.Thread(
            target=self._run_scraper,
            kwargs={
                "start_url": start_url,
                "output_dir": output_dir,
                "headless": bool(self.headless_var.get()),
                "vocab_selectors": self._parse_selectors(self.vocab_selector_var.get()),
                "driver_path": self.driver_path_var.get().strip() or None,
                "resume": bool(self.resume_var.get()),
                "include_phonetic": bool(self.include_phonetic_var.get()),
                "include_example": bool(self.include_example_var.get()),
                "include_note_definition_all": bool(self.include_note_definition_all_var.get()),
            },
            daemon=True,
        )
        self.worker_thread.start()

    def _run_scraper(
        self,
        start_url: str,
        output_dir: Path,
        headless: bool,
        vocab_selectors: list[str] | None,
        driver_path: str | None,
        resume: bool,
        include_phonetic: bool,
        include_example: bool,
        include_note_definition_all: bool,
    ) -> None:
        """Luồng nền gọi scrape_vocabulary và gửi event về queue."""
        try:
            self.queue.put(("log", "INFO: Thread crawler đã khởi động"))
            self.queue.put(
                (
                    "log",
                    f"INFO: Config headless={headless}, resume={resume}, phonetic={include_phonetic}, example={include_example}, note_all={include_note_definition_all}, output={output_dir}",
                )
            )

            results = scrape_vocabulary(
                start_url=start_url,
                output_dir=output_dir,
                headless=headless,
                vocab_selectors=vocab_selectors,
                logger=lambda msg: self.queue.put(("log", msg)),
                progress_callback=lambda done, total: self.queue.put(("progress", (done, total))),
                driver_path=driver_path,
                resume=resume,
                include_phonetic=include_phonetic,
                include_example=include_example,
                include_note_definition_all=include_note_definition_all,
            )

            total_words = sum(len(r.vocabulary) for r in results)
            self.queue.put(("results", results))
            self.queue.put(("done", f"Hoàn tất: {len(results)} topic, {total_words} từ"))
        except Exception as exc:
            detail = traceback.format_exc()
            self.queue.put(("log", f"TRACEBACK:\n{detail}"))
            self.queue.put(("error", f"Lỗi: {exc.__class__.__name__}: {exc}"))

    def _refresh_preview(self, results: list[TopicResult]) -> None:
        """Render lại bảng topic ở tab chạy sau mỗi lần có kết quả mới."""
        for iid in self.topic_tree.get_children():
            self.topic_tree.delete(iid)
        for iid in self.preview_entries.get_children():
            self.preview_entries.delete(iid)

        for idx, item in enumerate(results):
            self.topic_tree.insert("", "end", iid=str(idx), values=(item.topic_title, len(item.vocabulary)))

    def _on_topic_selected(self, _event: object) -> None:
        """Hiển thị danh sách entry chi tiết khi người dùng chọn 1 topic."""
        selected = self.topic_tree.selection()
        if not selected:
            return

        idx = int(selected[0])
        if idx >= len(self.latest_results):
            return

        topic = self.latest_results[idx]
        for iid in self.preview_entries.get_children():
            self.preview_entries.delete(iid)

        if topic.entries:
            rows = sorted(topic.entries, key=lambda x: int((x.get("order") or "0") or "0"))
            for ridx, row in enumerate(rows):
                self.preview_entries.insert(
                    "",
                    "end",
                    iid=f"pre-{ridx}",
                    values=(
                        row.get("order", ""),
                        row.get("word", ""),
                        row.get("phonetic", ""),
                        row.get("example", ""),
                        row.get("note", ""),
                        row.get("definition_en", ""),
                    ),
                )
        else:
            for ridx, word in enumerate(topic.vocabulary, start=1):
                self.preview_entries.insert("", "end", iid=f"pre-{ridx}", values=(str(ridx), word, "", "", "", ""))

    def _log(self, message: str) -> None:
        """Ghi log ra UI + file log, đồng thời tô màu theo mức độ."""
        tag = ""
        upper = message.upper()
        if upper.startswith("OK:"):
            tag = "ok"
        elif upper.startswith("WARN:"):
            tag = "warn"
        elif upper.startswith("ERROR:") or upper.startswith("TRACEBACK"):
            tag = "error"
        elif upper.startswith("SAVE:"):
            tag = "save"
        elif upper.startswith("INFO:") or "TÌM THẤY" in upper:
            tag = "info"

        self.log_text.insert("end", message + "\n", tag)
        self.log_text.see("end")

        if self.log_file_path:
            with self.log_file_path.open("a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {message}\n")

    def _poll_queue(self) -> None:
        """Vòng lặp nhận message từ worker và cập nhật UI tương ứng."""
        try:
            while True:
                kind, payload = self.queue.get_nowait()

                if kind == "log":
                    msg = str(payload)
                    self._log(msg)
                    if msg.startswith("OK:"):
                        self.ok_topics += 1
                        self._update_stats()
                    elif msg.startswith("SKIP:"):
                        self.skip_topics += 1
                        self._update_stats()

                elif kind == "progress":
                    done, total = payload
                    self.done_topics = int(done)
                    self.total_topics = int(total)
                    self._set_progress(self.done_topics, self.total_topics)
                    self._update_stats()
                    self.status_var.set(f"Tiến trình: {self.done_topics}/{self.total_topics}")

                elif kind == "results":
                    self.latest_results = list(payload)
                    self._refresh_preview(self.latest_results)

                elif kind == "done":
                    self._log(str(payload))
                    self.status_var.set(str(payload))
                    self.progress.configure(value=self.progress.cget("maximum"))
                    self._set_busy(False)
                    messagebox.showinfo("Hoàn tất", str(payload))

                    # Tự tải CSV tổng mới nhất để xem luôn.
                    self._load_latest_all_csv()

                elif kind == "error":
                    self._log(str(payload))
                    self.status_var.set("Có lỗi")
                    self._set_busy(False)
                    messagebox.showerror("Lỗi", str(payload))

        except queue.Empty:
            pass
        finally:
            self.after(120, self._poll_queue)


def main() -> None:
    app = VocabularyScraperApp()
    app.mainloop()


if __name__ == "__main__":
    main()
