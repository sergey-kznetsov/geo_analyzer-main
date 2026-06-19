from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
import traceback
import zipfile
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

import tkinter as tk

from geo_analyzer.core.models import LocationInput
from geo_analyzer.core.settings import get_settings
from geo_analyzer.pipeline.comparison_pipeline import ComparisonResult, run_location_comparison
from geo_analyzer.pipeline.stable_runner import run_analysis

APP_BG = "#F4F7FB"
CARD_BG = "#FFFFFF"
TEXT = "#172033"
MUTED = "#6B7280"
BORDER = "#DDE4EF"
PRIMARY = "#2F6FED"
PRIMARY_DARK = "#1F55C9"
SUCCESS = "#1F9D55"
ERROR = "#D64545"
WARNING = "#B7791F"
STEP_BG = "#F8FAFD"
STEP_ACTIVE_BG = "#EAF1FF"
STEP_DONE_BG = "#EAF7EF"
STEP_ERROR_BG = "#FDECEC"


def _app_root() -> Path:
    """Корень приложения для хранения локального .env.

    Функция оставлена на уровне модуля специально: её используют юнит-тесты
    и GUI-логика работы с ключом 2GIS.
    """
    return Path(__file__).resolve().parents[3]


def _env_path() -> Path:
    return _app_root() / ".env"


def _read_env_value(key: str) -> str:
    env_path = _env_path()
    if not env_path.exists():
        return os.environ.get(key, "")

    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            name, value = stripped.split("=", 1)
            if name.strip() == key:
                return value.strip().strip('"').strip("'")
    except Exception:
        return os.environ.get(key, "")

    return os.environ.get(key, "")


def _write_env_value(key: str, value: str) -> None:
    env_path = _env_path()
    env_path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    found = False

    if env_path.exists():
        try:
            lines = env_path.read_text(encoding="utf-8").splitlines()
        except Exception:
            lines = []

    output: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            output.append(line)
            continue

        name, _old_value = stripped.split("=", 1)
        if name.strip() == key:
            output.append(f"{key}={value}")
            found = True
        else:
            output.append(line)

    if not found:
        output.append(f"{key}={value}")

    env_path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    os.environ[key] = value


SINGLE_STEPS = ["Геокодирование через 2GIS", "Построение контекста", "Загрузка POI через 2GIS Places", "Классификация POI", "Построение изохрон через 2GIS", "Привязка POI к изохронам", "Автомобильная доступность через 2GIS Routing", "Расчёт доступности и сетевых метрик", "Расчёт антидрайверов по категориям 2GIS", "Расчёт метрик и benchmark", "Визуализация", "Экспорт Excel и summary", "Сохранение meta.json"]
COMPARISON_STEPS = ["Анализ Локации A", "Анализ Локации B", "Расчёт сравнения", "Экспорт comparison.xlsx", "Сравнение готово"]


def _open_in_os(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(str(path))
    if sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]
        return
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
        return
    subprocess.Popen(["xdg-open", str(path)])


class ProgressRing(tk.Canvas):
    def __init__(self, master: tk.Misc, size: int = 116, thickness: int = 12) -> None:
        super().__init__(master, width=size, height=size, bg=CARD_BG, highlightthickness=0, bd=0)
        pad = thickness // 2 + 4
        self.coords_box = (pad, pad, size - pad, size - pad)
        self.create_oval(*self.coords_box, outline="#E2E8F0", width=thickness)
        self.arc = self.create_arc(*self.coords_box, start=90, extent=0, outline=PRIMARY, width=thickness, style=tk.ARC)
        self.text = self.create_text(size // 2, size // 2, text="0%", fill=TEXT, font=("Segoe UI", 21, "bold"))

    def set_value(self, percent: int) -> None:
        value = max(0, min(100, int(percent)))
        self.itemconfigure(self.arc, extent=-360 * value / 100)
        self.itemconfigure(self.text, text=f"{value}%")


class GeoAnalyzerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.settings = get_settings()
        self.title("Geo Analyzer")
        self.geometry("1260x780")
        self.minsize(1180, 720)
        self.configure(bg=APP_BG)
        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._is_running = False
        self._result_dir: Path | None = None
        self._report_path: Path | None = None
        self._summary_path: Path | None = None
        self._started_at: float | None = None
        self._step_widgets: list[dict[str, Any]] = []
        self._build_styles()
        self._build_ui()
        self._apply_mode()
        self._reset_status()
        self.after(150, self._poll_queue)

    def _build_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background=APP_BG)
        style.configure("Card.TLabel", background=CARD_BG, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("Muted.TLabel", background=CARD_BG, foreground=MUTED, font=("Segoe UI", 9))
        style.configure("Title.TLabel", background=APP_BG, foreground=TEXT, font=("Segoe UI", 22, "bold"))
        style.configure("Subtitle.TLabel", background=APP_BG, foreground=MUTED, font=("Segoe UI", 10))
        style.configure("CardTitle.TLabel", background=CARD_BG, foreground=TEXT, font=("Segoe UI", 12, "bold"))
        style.configure("Success.TLabel", background=CARD_BG, foreground=SUCCESS, font=("Segoe UI", 10, "bold"))
        style.configure("Error.TLabel", background=CARD_BG, foreground=ERROR, font=("Segoe UI", 10, "bold"))
        style.configure("Warning.TLabel", background=CARD_BG, foreground=WARNING, font=("Segoe UI", 10, "bold"))
        style.configure("TEntry", fieldbackground="#FFFFFF", foreground=TEXT, padding=8)
        style.configure("Horizontal.TProgressbar", troughcolor="#E2E8F0", background=PRIMARY, thickness=10)
        style.configure("TButton", font=("Segoe UI", 10), padding=(14, 8))
        style.configure("Primary.TButton", font=("Segoe UI", 10, "bold"), foreground="#FFFFFF", background=PRIMARY, padding=(16, 9))
        style.map("Primary.TButton", background=[("active", PRIMARY_DARK), ("disabled", "#A8B7D8")], foreground=[("disabled", "#EEF2FF")])
        style.configure("Soft.TButton", background="#EEF2F7")
        style.configure("TCheckbutton", background=CARD_BG, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("TRadiobutton", background=CARD_BG, foreground=TEXT, font=("Segoe UI", 10))

    def _make_card(self, parent: tk.Misc) -> tk.Frame:
        return tk.Frame(parent, bg=CARD_BG, highlightbackground=BORDER, highlightcolor=BORDER, highlightthickness=1, bd=0)

    def _build_ui(self) -> None:
        root = tk.Frame(self, bg=APP_BG)
        root.pack(fill=tk.BOTH, expand=True, padx=22, pady=18)
        root.grid_columnconfigure(0, weight=1)
        root.grid_rowconfigure(2, weight=1)
        header = tk.Frame(root, bg=APP_BG)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        title_block = tk.Frame(header, bg=APP_BG)
        title_block.grid(row=0, column=0, sticky="w")
        ttk.Label(title_block, text="Geo Analyzer", style="Title.TLabel").pack(anchor="w")
        ttk.Label(title_block, text="Анализ локации, инфраструктуры, доступности и городского окружения", style="Subtitle.TLabel").pack(anchor="w", pady=(2, 0))
        ttk.Button(header, text="Настройки", command=self._show_settings, style="Soft.TButton").grid(row=0, column=1, sticky="e")

        input_card = self._make_card(root)
        input_card.grid(row=1, column=0, sticky="ew", pady=(16, 16))
        input_card.grid_columnconfigure(1, weight=1)
        input_card.grid_columnconfigure(3, weight=1)
        self.mode_var = tk.StringVar(value="single")
        mode_row = tk.Frame(input_card, bg=CARD_BG)
        mode_row.grid(row=0, column=0, columnspan=5, sticky="w", padx=18, pady=(16, 6))
        ttk.Radiobutton(mode_row, text="Один адрес", value="single", variable=self.mode_var, command=self._apply_mode).pack(side=tk.LEFT)
        ttk.Radiobutton(mode_row, text="Сравнить две локации", value="comparison", variable=self.mode_var, command=self._apply_mode).pack(side=tk.LEFT, padx=(18, 0))
        self.single_label = ttk.Label(input_card, text="Адрес", style="Card.TLabel")
        self.single_label.grid(row=1, column=0, sticky="w", padx=(18, 10), pady=(10, 10))
        self.address_var = tk.StringVar(value="Ижевск, Пушкинская 277")
        self.address_entry = ttk.Entry(input_card, textvariable=self.address_var)
        self.address_entry.grid(row=1, column=1, columnspan=3, sticky="ew", pady=(10, 10))
        self.address_entry.bind("<Return>", lambda _event: self._start())
        self.compare_a_label = ttk.Label(input_card, text="Локация A", style="Card.TLabel")
        self.compare_a_var = tk.StringVar(value="Ижевск, Пушкинская 277")
        self.compare_a_entry = ttk.Entry(input_card, textvariable=self.compare_a_var)
        self.compare_a_entry.bind("<Return>", lambda _event: self._start())
        self.compare_b_label = ttk.Label(input_card, text="Локация B", style="Card.TLabel")
        self.compare_b_var = tk.StringVar(value="Ижевск, Красная 131")
        self.compare_b_entry = ttk.Entry(input_card, textvariable=self.compare_b_var)
        self.compare_b_entry.bind("<Return>", lambda _event: self._start())
        self._register_entry_clipboard(self.address_entry)
        self._register_entry_clipboard(self.compare_a_entry)
        self._register_entry_clipboard(self.compare_b_entry)
        self.start_button = ttk.Button(input_card, text="Начать анализ", command=self._start, style="Primary.TButton")
        self.start_button.grid(row=1, column=4, sticky="e", padx=(14, 18), pady=(10, 10))
        options_row = tk.Frame(input_card, bg=CARD_BG)
        options_row.grid(row=3, column=1, columnspan=4, sticky="w", padx=(0, 18), pady=(0, 16))
        self.refresh_benchmark_var = tk.BooleanVar(value=False)
        self.refresh_benchmark_check = ttk.Checkbutton(options_row, text="Обновить benchmark города", variable=self.refresh_benchmark_var)
        self.refresh_benchmark_check.pack(side=tk.LEFT)
        ttk.Label(options_row, text="Пересобирает benchmark и обновляет данные 2GIS для текущего запуска.", style="Muted.TLabel").pack(side=tk.LEFT, padx=(12, 0))

        body = tk.Frame(root, bg=APP_BG)
        body.grid(row=2, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)
        left = self._make_card(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 9))
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(1, weight=1)
        status_header = tk.Frame(left, bg=CARD_BG)
        status_header.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 10))
        status_header.grid_columnconfigure(1, weight=1)
        self.progress_ring = ProgressRing(status_header, size=116, thickness=12)
        self.progress_ring.grid(row=0, column=0, rowspan=4, sticky="w", padx=(0, 16))
        ttk.Label(status_header, text="Статус", style="CardTitle.TLabel").grid(row=0, column=1, sticky="w")
        self.status_var = tk.StringVar(value="Ожидание запуска")
        ttk.Label(status_header, textvariable=self.status_var, style="Card.TLabel").grid(row=1, column=1, sticky="w", pady=(8, 0))
        self.progress = ttk.Progressbar(status_header, mode="determinate", maximum=100)
        self.progress.grid(row=2, column=1, sticky="ew", pady=(10, 5))
        self.time_var = tk.StringVar(value="Время: —")
        ttk.Label(status_header, textvariable=self.time_var, style="Muted.TLabel").grid(row=3, column=1, sticky="w")
        steps_wrap = tk.Frame(left, bg=CARD_BG)
        steps_wrap.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 16))
        steps_wrap.grid_columnconfigure(0, weight=1)
        steps_wrap.grid_rowconfigure(0, weight=1)
        self.steps_canvas = tk.Canvas(steps_wrap, bg=CARD_BG, highlightthickness=0, bd=0)
        self.steps_scrollbar = ttk.Scrollbar(steps_wrap, orient="vertical", command=self.steps_canvas.yview)
        self.steps_frame = tk.Frame(self.steps_canvas, bg=CARD_BG)
        self.steps_frame.bind("<Configure>", lambda _event: self.steps_canvas.configure(scrollregion=self.steps_canvas.bbox("all")))
        self.steps_window = self.steps_canvas.create_window((0, 0), window=self.steps_frame, anchor="nw")
        self.steps_canvas.configure(yscrollcommand=self.steps_scrollbar.set)
        self.steps_canvas.bind("<Configure>", lambda event: self.steps_canvas.itemconfigure(self.steps_window, width=event.width))
        self.steps_canvas.grid(row=0, column=0, sticky="nsew")
        self.steps_scrollbar.grid(row=0, column=1, sticky="ns")

        right = self._make_card(body)
        right.grid(row=0, column=1, sticky="nsew", padx=(9, 0))
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(2, weight=1)
        ttk.Label(right, text="Результаты", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w", padx=16, pady=(16, 8))
        self.result_label = ttk.Label(right, text="Результат ещё не сформирован", style="Muted.TLabel")
        self.result_label.grid(row=1, column=0, sticky="w", padx=16, pady=(0, 8))
        metrics_frame = tk.Frame(right, bg=CARD_BG)
        metrics_frame.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 12))
        metrics_frame.grid_columnconfigure(0, weight=1)
        metrics_frame.grid_rowconfigure(0, weight=1)
        self.metrics_text = tk.Text(metrics_frame, wrap=tk.WORD, bg="#F8FAFD", fg=TEXT, relief=tk.FLAT, padx=12, pady=12, height=18, font=("Segoe UI", 10), highlightbackground=BORDER, highlightcolor=BORDER, highlightthickness=1)
        self.metrics_text.grid(row=0, column=0, sticky="nsew")
        self.metrics_text.configure(state=tk.DISABLED)
        self.result_folder_var = tk.StringVar(value="Папка: —")
        ttk.Label(right, textvariable=self.result_folder_var, style="Muted.TLabel").grid(row=3, column=0, sticky="w", padx=16, pady=(0, 10))
        buttons = tk.Frame(right, bg=CARD_BG)
        buttons.grid(row=4, column=0, sticky="ew", padx=16, pady=(0, 16))
        for i in range(4):
            buttons.grid_columnconfigure(i, weight=1)
        self.open_folder_button = ttk.Button(buttons, text="Открыть папку", command=self._open_result_folder)
        self.open_folder_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.open_report_button = ttk.Button(buttons, text="Открыть Excel", command=self._open_report)
        self.open_report_button.grid(row=0, column=1, sticky="ew", padx=6)
        self.summary_button = ttk.Button(buttons, text="Показать summary", command=self._show_summary)
        self.summary_button.grid(row=0, column=2, sticky="ew", padx=6)
        self.zip_button = ttk.Button(buttons, text="Создать ZIP", command=self._create_zip)
        self.zip_button.grid(row=0, column=3, sticky="ew", padx=(6, 0))
    def _register_entry_clipboard(self, entry: ttk.Entry) -> None:
        entry.bind("<<Paste>>", lambda event, widget=entry: self._paste_into_entry(widget))
        entry.bind("<Control-v>", lambda event, widget=entry: self._paste_into_entry(widget))
        entry.bind("<Control-V>", lambda event, widget=entry: self._paste_into_entry(widget))
        entry.bind("<Shift-Insert>", lambda event, widget=entry: self._paste_into_entry(widget))
        entry.bind("<Control-a>", lambda event, widget=entry: self._select_entry_all(widget))
        entry.bind("<Control-A>", lambda event, widget=entry: self._select_entry_all(widget))
        entry.bind("<Button-3>", lambda event, widget=entry: self._show_entry_context_menu(event, widget))

    def _paste_into_entry(self, entry: ttk.Entry) -> str:
        try:
            value = self.clipboard_get()
        except tk.TclError:
            return "break"
        try:
            if entry.selection_present():
                entry.delete("sel.first", "sel.last")
        except tk.TclError:
            pass
        entry.insert(tk.INSERT, value)
        return "break"

    @staticmethod
    def _select_entry_all(entry: ttk.Entry) -> str:
        entry.selection_range(0, tk.END)
        entry.icursor(tk.END)
        return "break"

    def _show_entry_context_menu(self, event: tk.Event, entry: ttk.Entry) -> str:
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Вставить", command=lambda: self._paste_into_entry(entry))
        menu.add_command(label="Копировать", command=lambda: entry.event_generate("<<Copy>>"))
        menu.add_command(label="Вырезать", command=lambda: entry.event_generate("<<Cut>>"))
        menu.add_separator()
        menu.add_command(label="Выделить всё", command=lambda: self._select_entry_all(entry))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    def _apply_mode(self) -> None:
        mode = self.mode_var.get()
        self.single_label.grid_remove(); self.address_entry.grid_remove(); self.compare_a_label.grid_remove(); self.compare_a_entry.grid_remove(); self.compare_b_label.grid_remove(); self.compare_b_entry.grid_remove()
        if mode == "comparison":
            self.start_button.configure(text="Сравнить локации")
            self.compare_a_label.grid(row=1, column=0, sticky="w", padx=(18, 10), pady=(10, 8))
            self.compare_a_entry.grid(row=1, column=1, columnspan=3, sticky="ew", pady=(10, 8))
            self.compare_b_label.grid(row=2, column=0, sticky="w", padx=(18, 10), pady=(0, 10))
            self.compare_b_entry.grid(row=2, column=1, columnspan=3, sticky="ew", pady=(0, 10))
            self.start_button.grid(row=1, column=4, rowspan=2, sticky="e", padx=(14, 18), pady=(10, 10))
            self._render_steps(COMPARISON_STEPS)
        else:
            self.single_label.configure(text="Адрес")
            self.start_button.configure(text="Начать анализ")
            self.single_label.grid(row=1, column=0, sticky="w", padx=(18, 10), pady=(10, 10))
            self.address_entry.grid(row=1, column=1, columnspan=3, sticky="ew", pady=(10, 10))
            self.start_button.grid(row=1, column=4, sticky="e", padx=(14, 18), pady=(10, 10))
            self._render_steps(SINGLE_STEPS)

    def _render_steps(self, steps: list[str]) -> None:
        for child in self.steps_frame.winfo_children():
            child.destroy()
        self._step_widgets = []
        for index, label in enumerate(steps, start=1):
            item = tk.Frame(self.steps_frame, bg=STEP_BG, highlightbackground=BORDER, highlightthickness=1, bd=0)
            item.grid(row=index - 1, column=0, sticky="ew", pady=(0, 7))
            item.grid_columnconfigure(1, weight=1)
            indicator = tk.Label(item, text=str(index), width=3, bg=STEP_BG, fg=MUTED, font=("Segoe UI", 9, "bold"))
            indicator.grid(row=0, column=0, sticky="w", padx=(10, 4), pady=8)
            text_label = tk.Label(item, text=label, bg=STEP_BG, fg=TEXT, anchor="w", font=("Segoe UI", 9))
            text_label.grid(row=0, column=1, sticky="ew", padx=(0, 10), pady=8)
            self._step_widgets.append({"frame": item, "indicator": indicator, "label": text_label})

    def _reset_status(self) -> None:
        self._result_dir = None; self._report_path = None; self._summary_path = None; self._started_at = None
        self.progress["value"] = 0; self.progress_ring.set_value(0)
        self.status_var.set("Ожидание запуска"); self.time_var.set("Время: —")
        self.result_label.configure(text="Результат ещё не сформирован", style="Muted.TLabel")
        self.result_folder_var.set("Папка: —")
        self._set_metrics_text("После запуска здесь появятся краткие метрики и ссылки на результат.")
        self.open_folder_button.configure(state=tk.DISABLED); self.open_report_button.configure(state=tk.DISABLED); self.summary_button.configure(state=tk.DISABLED); self.zip_button.configure(state=tk.DISABLED)
        for item in self._step_widgets:
            self._paint_step(item, status="pending")

    def _paint_step(self, item: dict[str, Any], status: str) -> None:
        bg = STEP_BG; fg = MUTED; indicator_text = item["indicator"].cget("text")
        if status == "active": bg = STEP_ACTIVE_BG; fg = PRIMARY; indicator_text = "•"
        elif status == "done": bg = STEP_DONE_BG; fg = SUCCESS; indicator_text = "✓"
        elif status == "error": bg = STEP_ERROR_BG; fg = ERROR; indicator_text = "!"
        elif status == "pending":
            try: int(indicator_text)
            except ValueError: indicator_text = ""
        item["frame"].configure(bg=bg); item["indicator"].configure(bg=bg, fg=fg, text=indicator_text); item["label"].configure(bg=bg, fg=TEXT)

    def _set_metrics_text(self, text: str) -> None:
        self.metrics_text.configure(state=tk.NORMAL); self.metrics_text.delete("1.0", tk.END); self.metrics_text.insert(tk.END, text); self.metrics_text.configure(state=tk.DISABLED)

    def _set_buttons_running(self, running: bool) -> None:
        self._is_running = running
        self.start_button.configure(state=tk.DISABLED if running else tk.NORMAL)
        self.refresh_benchmark_check.configure(state=tk.DISABLED if running else tk.NORMAL)
        if running:
            self.open_folder_button.configure(state=tk.DISABLED); self.open_report_button.configure(state=tk.DISABLED); self.summary_button.configure(state=tk.DISABLED); self.zip_button.configure(state=tk.DISABLED)

    def _start(self) -> None:
        if self._is_running:
            return
        mode = self.mode_var.get()
        if mode == "comparison": self._start_comparison()
        else: self._start_analysis()

    def _start_analysis(self) -> None:
        address = self.address_var.get().strip()
        if not address:
            messagebox.showerror("Geo Analyzer", "Введите адрес."); return
        self._begin_run("Анализ запущен", "Выполняется анализ", "Анализ выполняется. После завершения появятся краткие метрики.")
        threading.Thread(target=self._analysis_worker, args=(LocationInput(address=address),), daemon=True).start()

    def _start_comparison(self) -> None:
        address_a = self.compare_a_var.get().strip(); address_b = self.compare_b_var.get().strip()
        if not address_a or not address_b:
            messagebox.showerror("Geo Analyzer", "Введите адреса для Локации A и Локации B."); return
        self._begin_run("Сравнение запущено", "Выполняется сравнение", "Сравнение выполняется. Будут сформированы отчёты по двум локациям и comparison.xlsx.")
        threading.Thread(target=self._comparison_worker, args=(LocationInput(address=address_a), LocationInput(address=address_b)), daemon=True).start()

    def _begin_run(self, status: str, label: str, metrics: str) -> None:
        self._apply_mode(); self._reset_status(); self._set_buttons_running(True); self._started_at = time.perf_counter()
        self.status_var.set(status); self.result_label.configure(text=label, style="Warning.TLabel"); self._set_metrics_text(metrics)
        os.environ["GEO_ANALYZER_REFRESH_CITY_BENCHMARK"] = "1" if self.refresh_benchmark_var.get() else "0"

    def _analysis_worker(self, location_input: LocationInput) -> None:
        try:
            self._queue.put(("success_single", run_analysis(location_input, progress_callback=self._progress_callback)))
        except Exception:
            error_text = traceback.format_exc(); self._write_error_log(error_text); self._queue.put(("error", error_text))

    def _comparison_worker(self, location_a: LocationInput, location_b: LocationInput) -> None:
        try:
            self._queue.put(("success_comparison", run_location_comparison(location_a, location_b, progress_callback=self._progress_callback)))
        except Exception:
            error_text = traceback.format_exc(); self._write_error_log(error_text); self._queue.put(("error", error_text))

    def _progress_callback(self, *args: Any, **kwargs: Any) -> None:
        payload: dict[str, Any] = {}
        if args and isinstance(args[0], dict): payload.update(args[0])
        elif len(args) >= 3: payload.update({"step": args[0], "total": args[1], "message": args[2]})
        elif len(args) >= 2: payload.update({"step": args[0], "message": args[1]})
        elif len(args) == 1: payload.update({"message": args[0]})
        payload.update(kwargs); self._queue.put(("progress", payload))

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "progress": self._handle_progress(payload)
                elif kind == "success_single": self._finish_single_success(payload)
                elif kind == "success_comparison": self._finish_comparison_success(payload)
                elif kind == "error": self._finish_error(str(payload))
        except queue.Empty:
            pass
        self.after(150, self._poll_queue)

    def _handle_progress(self, payload: Any) -> None:
        data = payload if isinstance(payload, dict) else {"message": str(payload)}
        self.status_var.set(str(data.get("message") or "Выполнение"))
        try: step = int(data.get("step") or 0)
        except Exception: step = 0
        try: total = int(data.get("total") or len(self._step_widgets) or 1)
        except Exception: total = len(self._step_widgets) or 1
        if step <= 0: return
        percent = round(max(0, min(1, step / max(total, 1))) * 100)
        self.progress["value"] = percent; self.progress_ring.set_value(percent)
        for index, item in enumerate(self._step_widgets, start=1):
            if index < step: self._paint_step(item, status="done")
            elif index == step: self._paint_step(item, status="active" if percent < 100 else "done")
            else: self._paint_step(item, status="pending")
        if self._started_at is not None:
            self.time_var.set(f"Время: {time.perf_counter() - self._started_at:.1f} сек")

    def _finish_single_success(self, payload: Any) -> None:
        data = payload if isinstance(payload, dict) else {}
        self._set_buttons_running(False); self._mark_success("Анализ завершён", "Анализ успешно завершён")
        self._result_dir = self._to_path(data.get("result_dir")); self._report_path = self._to_path(data.get("report_path")); self._summary_path = self._to_path(data.get("summary_path"))
        self.result_folder_var.set(f"Папка: {self._result_dir}"); self._enable_result_buttons()
        meta = data.get("meta", {}) if isinstance(data.get("meta"), dict) else {}
        quality_scores = self._normalize_quality_scores(data.get("quality_scores")); accessibility_rows = self._normalize_rows(data.get("accessibility_snapshot"))
        poi_count = int(data.get("poi_count", 0) or self._safe_len(data.get("pois")) or 0)
        self._set_metrics_text(self._build_quick_metrics_text(meta, quality_scores, accessibility_rows, poi_count) or "Краткие метрики не сформированы.")

    def _finish_comparison_success(self, payload: Any) -> None:
        result = payload if isinstance(payload, ComparisonResult) else None
        self._set_buttons_running(False)
        if result is None:
            self._finish_error("Некорректный результат сравнения."); return
        self._mark_success("Сравнение завершено", "Сравнение успешно завершено")
        self._result_dir = result.result_dir; self._report_path = result.comparison_path; self._summary_path = result.summary_path
        self.result_folder_var.set(f"Папка: {self._result_dir}"); self._enable_result_buttons()
        meta_a = result.location_a_result.get("meta", {}) if isinstance(result.location_a_result, dict) else {}
        meta_b = result.location_b_result.get("meta", {}) if isinstance(result.location_b_result, dict) else {}
        lines = ["Сравнение локаций завершено.", f"Локация A: {meta_a.get('resolved_address') or self.compare_a_var.get().strip()}", f"Локация B: {meta_b.get('resolved_address') or self.compare_b_var.get().strip()}", f"Абсолютный победитель: {result.winner_absolute}"]
        if result.winner_benchmark: lines.append(f"Победитель относительно benchmark города: {result.winner_benchmark}")
        if result.comparison_path: lines.append(f"Excel: {result.comparison_path}")
        if result.summary_path: lines.append(f"Summary: {result.summary_path}")
        if result.scores_chart_path: lines.append(f"График: {result.scores_chart_path}")
        if result.map_path: lines.append(f"Карта: {result.map_path}")
        self._set_metrics_text("\n".join(lines))

    def _mark_success(self, status: str, label: str) -> None:
        self.progress["value"] = 100; self.progress_ring.set_value(100); self.status_var.set(status); self.result_label.configure(text=label, style="Success.TLabel"); self._finish_elapsed()
        for item in self._step_widgets: self._paint_step(item, status="done")

    def _finish_elapsed(self) -> None:
        if self._started_at is not None: self.time_var.set(f"Время: {time.perf_counter() - self._started_at:.1f} сек")

    def _enable_result_buttons(self) -> None:
        self.open_folder_button.configure(state=tk.NORMAL); self.open_report_button.configure(state=tk.NORMAL); self.zip_button.configure(state=tk.NORMAL); self.summary_button.configure(state=tk.NORMAL)

    def _finish_error(self, error_text: str) -> None:
        self._set_buttons_running(False); self.status_var.set("Ошибка выполнения"); self.result_label.configure(text="Выполнение завершилось ошибкой", style="Error.TLabel"); self.progress_ring.set_value(int(self.progress["value"] or 0))
        if self._step_widgets: self._paint_step(self._step_widgets[0], status="error")
        log_path = self._log_dir() / "gui_last_error.txt"
        self._set_metrics_text(f"Ошибка выполнения. Подробный лог сохранён в:\n{log_path}\n\n{error_text[-2500:]}")
        messagebox.showerror("Geo Analyzer", f"Выполнение завершилось ошибкой. Подробный лог сохранён в:\n{log_path}")

    def _normalize_quality_scores(self, value: Any) -> dict[str, float]:
        result: dict[str, float] = {}
        if value is None: return result
        if hasattr(value, "to_dict"):
            try: records = value.to_dict(orient="records")
            except Exception: records = []
            for row in records:
                if not isinstance(row, dict): continue
                name = row.get("Метрика") or row.get("metric") or row.get("name"); score = row.get("Оценка_из_10") or row.get("score") or row.get("value")
                if name is None: continue
                try: result[str(name)] = float(score)
                except Exception: continue
            return result
        if isinstance(value, dict):
            for key, item in value.items():
                try:
                    score = item.get("score") or item.get("value") or item.get("Оценка_из_10") if isinstance(item, dict) else item
                    result[str(key)] = float(score)
                except Exception: continue
        return result

    def _normalize_rows(self, value: Any) -> list[dict[str, Any]]:
        if isinstance(value, list): return [item for item in value if isinstance(item, dict)]
        if hasattr(value, "to_dict"):
            try: return [item for item in value.to_dict(orient="records") if isinstance(item, dict)]
            except Exception: return []
        if isinstance(value, dict): return [value]
        return []

    def _build_quick_metrics_text(self, meta: dict[str, Any], quality_scores: dict[str, float], accessibility_rows: list[dict[str, Any]], poi_count: int) -> str:
        lines: list[str] = []
        lines.append(f"Адрес: {meta.get('resolved_address') or meta.get('source_label') or '—'}")
        if meta.get("city"): lines.append(f"Город: {meta.get('city')}")
        if quality_scores:
            avg_score = sum(quality_scores.values()) / len(quality_scores); best_name, best_value = max(quality_scores.items(), key=lambda x: x[1]); worst_name, worst_value = min(quality_scores.items(), key=lambda x: x[1])
            lines.append(f"Средняя оценка среды: {avg_score:.1f} / 10"); lines.append(f"Сильная метрика: {best_name} — {best_value:.1f}"); lines.append(f"Слабая метрика: {worst_name} — {worst_value:.1f}")
        lines.append(f"Всего POI в зоне анализа: {poi_count}")
        return "\n".join(lines)

    def _open_path(self, path: Path | None) -> None:
        try:
            if path is None: raise FileNotFoundError("Путь не задан")
            _open_in_os(path)
        except Exception:
            messagebox.showerror("Ошибка", "Файл или папка не найдены.")

    def _open_result_folder(self) -> None: self._open_path(self._result_dir)
    def _open_report(self) -> None: self._open_path(self._report_path)

    def _show_summary(self) -> None:
        if not self._summary_path or not self._summary_path.exists(): messagebox.showerror("Ошибка", "Summary не найден."); return
        try: text = self._summary_path.read_text(encoding="utf-8")
        except Exception as exc: messagebox.showerror("Ошибка", str(exc)); return
        window = tk.Toplevel(self); window.title("Summary"); window.geometry("820x560"); window.configure(bg=APP_BG)
        box = tk.Text(window, wrap=tk.WORD, bg=CARD_BG, fg=TEXT, relief=tk.FLAT, padx=14, pady=14, font=("Segoe UI", 10), highlightbackground=BORDER, highlightcolor=BORDER, highlightthickness=1)
        box.pack(fill=tk.BOTH, expand=True, padx=16, pady=16); box.insert(tk.END, text); box.configure(state=tk.DISABLED)

    def _build_table(self, parent: tk.Misc, rows: list[dict[str, Any]]) -> None:
        if not rows: return
        frame = tk.Frame(parent, bg=APP_BG); frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=(4, 12))
        columns = list(rows[0].keys()); tree = ttk.Treeview(frame, columns=columns, show="headings", height=10)
        for name in columns: tree.heading(name, text=name.replace("_", " ")); tree.column(name, width=150, anchor="w", stretch=False)
        for row in rows: tree.insert("", tk.END, values=["" if row.get(name) is None else row.get(name, "") for name in columns])
        y_scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview); x_scroll = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set); tree.grid(row=0, column=0, sticky="nsew"); y_scroll.grid(row=0, column=1, sticky="ns"); x_scroll.grid(row=1, column=0, sticky="ew")
        frame.grid_rowconfigure(0, weight=1); frame.grid_columnconfigure(0, weight=1)

    def _create_zip(self) -> None:
        if not self._result_dir or not self._result_dir.exists(): messagebox.showerror("Ошибка", "Папка результата не найдена."); return
        target = filedialog.asksaveasfilename(defaultextension=".zip", filetypes=[("ZIP archive", "*.zip")], initialfile=f"{self._result_dir.name}.zip")
        if not target: return
        zip_path = Path(target)
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in self._result_dir.rglob("*"): archive.write(path, arcname=path.relative_to(self._result_dir.parent))
        messagebox.showinfo("ZIP создан", str(zip_path))

    def _show_settings(self) -> None:
        key_state = "найден"
        if not self.settings.dgis_api_key: key_state = "не найден"
        elif self.settings.is_frozen: key_state = "встроен в сборку"
        text = f"Папка приложения:\n{self.settings.app_dir}\n\nПапка результатов:\n{self.settings.output_dir}\n\nПапка кеша:\n{self.settings.cache_dir}\n\nПапка benchmark:\n{self.settings.benchmark_dir}\n\nФайл конфигурации:\n{self.settings.config_path or 'не найден, используется fallback'}\n\nКлюч 2GIS: {key_state}"
        messagebox.showinfo("Настройки", text)

    def _log_dir(self) -> Path:
        try: path = self.settings.logs_dir
        except Exception: path = Path.cwd() / "logs"
        path.mkdir(parents=True, exist_ok=True); return path

    def _write_error_log(self, error_text: str) -> None:
        (self._log_dir() / "gui_last_error.txt").write_text(error_text, encoding="utf-8")

    @staticmethod
    def _to_path(value: Any) -> Path | None:
        if value is None: return None
        if isinstance(value, Path): return value
        try: return Path(str(value))
        except Exception: return None

    @staticmethod
    def _safe_len(value: Any) -> int:
        try: return len(value)
        except Exception: return 0

    @staticmethod
    def _find_latest_file(folder: Path, pattern: str) -> Path | None:
        try:
            files = [path for path in folder.glob(pattern) if path.is_file()]
            return max(files, key=lambda path: path.stat().st_mtime) if files else None
        except Exception: return None


def main() -> int:
    app = GeoAnalyzerApp(); app.mainloop(); return 0


if __name__ == "__main__":
    raise SystemExit(main())
