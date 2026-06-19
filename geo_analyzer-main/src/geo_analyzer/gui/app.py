from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

from geo_analyzer.core.models import LocationInput
from geo_analyzer.pipeline.analysis_pipeline import run_analysis
from geo_analyzer.pipeline.comparison_pipeline import ComparisonResult, run_location_comparison

APP_BG = "#EEF2F7"
WINDOW_BG = "#F8FAFC"
SIDEBAR_BG = "#101827"
SIDEBAR_CARD = "#172235"
CARD_BG = "#FFFFFF"
CARD_ALT = "#F4F7FB"
TEXT = "#111827"
MUTED = "#64748B"
PRIMARY = "#1F78FF"
PRIMARY_DARK = "#155BD6"
SUCCESS = "#36D399"
WARNING = "#F59E0B"
LOG_BG = "#111827"
LOG_TEXT = "#B7C3D4"

SINGLE_DEFAULT_ADDRESS = "Ижевск, Пушкинская 277"
COMPARE_DEFAULT_A = "Ижевск, Пушкинская 277"
COMPARE_DEFAULT_B = "Ижевск, Красная 131"

SINGLE_STEPS = ["Геокодирование", "POI и изохроны", "Доступность", "Антидрайверы", "Excel и карты"]
COMPARISON_STEPS = ["Локация A", "Локация B", "Сравнение", "Excel", "Готово"]


def _app_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _env_path() -> Path:
    return _app_root() / ".env"


def _read_env_value(key: str) -> str:
    value = os.environ.get(key, "")
    env_path = _env_path()
    if not env_path.exists():
        return value
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                name, raw = stripped.split("=", 1)
                if name.strip() == key:
                    return raw.strip().strip('"').strip("'")
    except Exception:
        return value
    return value


def _open_in_os(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(str(path))
    if sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def _safe_path(value: Any) -> Path | None:
    if value is None:
        return None
    text = str(value).strip()
    return Path(text) if text and text.lower() != "none" else None


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


class GeoAnalyzerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Geo Analyzer")
        self.geometry("1240x780")
        self.minsize(1120, 700)
        self.configure(bg=APP_BG)

        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._is_running = False
        self._result_dir: Path | None = None
        self._report_path: Path | None = None
        self._summary_path: Path | None = None

        self.mode_var = tk.StringVar(value="single")
        self.address_var = tk.StringVar(value=SINGLE_DEFAULT_ADDRESS)
        self.compare_a_var = tk.StringVar(value=COMPARE_DEFAULT_A)
        self.compare_b_var = tk.StringVar(value=COMPARE_DEFAULT_B)
        self.status_var = tk.StringVar(value="Ожидание")
        self.result_hint_var = tk.StringVar(value="Отчёт ещё не создан")
        self.progress_var = tk.DoubleVar(value=0)
        self.metric_access_var = tk.StringVar(value="—")
        self.metric_poi_var = tk.StringVar(value="—")
        self.metric_anti_var = tk.StringVar(value="—")
        self.metric_transport_var = tk.StringVar(value="—")

        self._step_widgets: list[tuple[tk.Label, tk.Label, tk.Label]] = []
        self._sidebar_labels: dict[str, tk.Frame] = {}

        self._build_styles()
        self._build_ui()
        self._apply_mode()
        self.after(120, self._poll_queue)

    def _build_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Title.TLabel", background=WINDOW_BG, foreground=TEXT, font=("Segoe UI", 24, "bold"))
        style.configure("Subtitle.TLabel", background=WINDOW_BG, foreground=MUTED, font=("Segoe UI", 10))
        style.configure("CardTitle.TLabel", background=CARD_BG, foreground=TEXT, font=("Segoe UI", 13, "bold"))
        style.configure("CardText.TLabel", background=CARD_BG, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("Muted.TLabel", background=CARD_BG, foreground=MUTED, font=("Segoe UI", 9))
        style.configure("MetricValue.TLabel", background=CARD_ALT, foreground=TEXT, font=("Segoe UI", 18, "bold"))
        style.configure("MetricTitle.TLabel", background=CARD_ALT, foreground=MUTED, font=("Segoe UI", 9))
        style.configure("Primary.TButton", font=("Segoe UI", 10, "bold"), padding=(18, 10), foreground="#FFFFFF", background=PRIMARY)
        style.map("Primary.TButton", background=[("active", PRIMARY_DARK), ("disabled", "#9DB8E8")])
        style.configure("TButton", font=("Segoe UI", 10), padding=(14, 9))
        style.configure("Horizontal.TProgressbar", troughcolor="#E6ECF5", background=PRIMARY)
        style.configure("TEntry", padding=(10, 8))

    def _build_ui(self) -> None:
        outer = tk.Frame(self, bg=APP_BG)
        outer.pack(fill=tk.BOTH, expand=True, padx=22, pady=22)
        outer.grid_columnconfigure(0, weight=1)
        outer.grid_rowconfigure(0, weight=1)

        app = tk.Frame(outer, bg=WINDOW_BG, highlightthickness=1, highlightbackground="#DDE4EF")
        app.grid(row=0, column=0, sticky="nsew")
        app.grid_columnconfigure(1, weight=1)
        app.grid_rowconfigure(0, weight=1)

        self._build_sidebar(app)
        self._build_content(app)

    def _build_sidebar(self, parent: tk.Misc) -> None:
        side = tk.Frame(parent, bg=SIDEBAR_BG, width=270)
        side.grid(row=0, column=0, sticky="ns")
        side.grid_propagate(False)

        tk.Label(side, text="GEO", bg=SIDEBAR_BG, fg="#FFFFFF", font=("Segoe UI", 20, "bold")).place(x=28, y=34)
        tk.Label(side, text="ANALYZER", bg=SIDEBAR_BG, fg="#7DD3FC", font=("Segoe UI", 20, "bold")).place(x=92, y=34)
        tk.Label(side, text="Windows Desktop", bg=SIDEBAR_BG, fg="#8EA0B7", font=("Segoe UI", 9)).place(x=31, y=72)

        self._sidebar_item(side, "●", "Анализ локации", 130, "single")
        self._sidebar_item(side, "◇", "Сравнение двух локаций", 188, "comparison")
        self._sidebar_item(side, "⚙", "Настройки API", 246, None, self._show_settings)

        api_card = tk.Frame(side, bg=SIDEBAR_CARD)
        api_card.place(x=26, y=590, width=218, height=120)
        tk.Label(api_card, text="Статус API", bg=SIDEBAR_CARD, fg="#FFFFFF", font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=18, pady=(16, 8))

        loaded = bool(_read_env_value("DGIS_API_KEY"))
        dot = tk.Canvas(api_card, width=16, height=16, bg=SIDEBAR_CARD, highlightthickness=0)
        dot.create_oval(2, 2, 14, 14, fill=SUCCESS if loaded else WARNING, outline=SUCCESS if loaded else WARNING)
        dot.place(x=18, y=54)
        tk.Label(api_card, text="2GIS ключ найден" if loaded else "ключ не задан", bg=SIDEBAR_CARD, fg="#CBD5E1", font=("Segoe UI", 9)).place(x=42, y=52)
        tk.Label(api_card, text="Кеш: стандартный", bg=SIDEBAR_CARD, fg="#8EA0B7", font=("Segoe UI", 9)).place(x=18, y=82)

    def _sidebar_item(self, parent: tk.Misc, icon: str, text: str, y: int, mode: str | None, command: Any | None = None) -> None:
        frame = tk.Frame(parent, bg=SIDEBAR_BG, cursor="hand2")
        frame.place(x=20, y=y, width=230, height=46)
        tk.Label(frame, text=icon, bg=SIDEBAR_BG, fg="#718096", font=("Segoe UI", 12, "bold")).place(x=14, y=11)
        tk.Label(frame, text=text, bg=SIDEBAR_BG, fg="#B7C3D4", font=("Segoe UI", 10)).place(x=44, y=11)

        def click(_event: Any = None) -> None:
            if command:
                command()
            elif mode:
                self.mode_var.set(mode)
                self._apply_mode()

        frame.bind("<Button-1>", click)
        for child in frame.winfo_children():
            child.bind("<Button-1>", click)

        if mode:
            self._sidebar_labels[mode] = frame

    def _build_content(self, parent: tk.Misc) -> None:
        content = tk.Frame(parent, bg=WINDOW_BG)
        content.grid(row=0, column=1, sticky="nsew", padx=34, pady=28)
        content.grid_columnconfigure(0, weight=1)
        content.grid_rowconfigure(2, weight=1)

        header = tk.Frame(content, bg=WINDOW_BG)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        ttk.Label(header, text="Анализ локации", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="Введите адрес, запустите расчёт и получите Excel-отчёт с картами и выводами.",
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Button(header, text="Настройки", command=self._show_settings).grid(row=0, column=1, rowspan=2, sticky="e")

        self._build_input_card(content)
        self._build_body(content)
        self._build_log(content)

    def _build_input_card(self, parent: tk.Misc) -> None:
        card = self._card(parent)
        card.grid(row=1, column=0, sticky="ew", pady=(24, 18))
        card.grid_columnconfigure(0, weight=1)

        pills = tk.Frame(card, bg=CARD_BG)
        pills.grid(row=0, column=0, sticky="w", padx=24, pady=(20, 10))

        self.single_pill = self._mode_pill(pills, "Одна локация", "single")
        self.single_pill.pack(side=tk.LEFT)

        self.comparison_pill = self._mode_pill(pills, "Сравнить две локации", "comparison")
        self.comparison_pill.pack(side=tk.LEFT, padx=(10, 0))

        self.start_button = ttk.Button(card, text="Запустить анализ", command=self._start, style="Primary.TButton")
        self.start_button.grid(row=0, column=1, sticky="e", padx=24, pady=(20, 10))

        self.single_form = tk.Frame(card, bg=CARD_BG)
        self.single_form.grid(row=1, column=0, columnspan=2, sticky="ew", padx=24, pady=(0, 24))
        self.single_form.grid_columnconfigure(0, weight=1)
        ttk.Label(self.single_form, text="Адрес для анализа", style="CardText.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.single_entry = ttk.Entry(self.single_form, textvariable=self.address_var, font=("Segoe UI", 11))
        self.single_entry.grid(row=1, column=0, sticky="ew")

        self.compare_form = tk.Frame(card, bg=CARD_BG)
        self.compare_form.grid(row=2, column=0, columnspan=2, sticky="ew", padx=24, pady=(0, 24))
        self.compare_form.grid_columnconfigure(0, weight=1)
        self.compare_form.grid_columnconfigure(1, weight=1)
        ttk.Label(self.compare_form, text="Локация A", style="CardText.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 6), padx=(0, 10))
        ttk.Label(self.compare_form, text="Локация B", style="CardText.TLabel").grid(row=0, column=1, sticky="w", pady=(0, 6), padx=(10, 0))

        self.a_entry = ttk.Entry(self.compare_form, textvariable=self.compare_a_var, font=("Segoe UI", 11))
        self.b_entry = ttk.Entry(self.compare_form, textvariable=self.compare_b_var, font=("Segoe UI", 11))
        self.a_entry.grid(row=1, column=0, sticky="ew", padx=(0, 10))
        self.b_entry.grid(row=1, column=1, sticky="ew", padx=(10, 0))

        for entry in [self.single_entry, self.a_entry, self.b_entry]:
            entry.bind("<Return>", lambda _event: self._start())

    def _build_body(self, parent: tk.Misc) -> None:
        body = tk.Frame(parent, bg=WINDOW_BG)
        body.grid(row=2, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=1, uniform="body")
        body.grid_columnconfigure(1, weight=1, uniform="body")

        left = self._card(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        ttk.Label(left, text="Ход анализа", style="CardTitle.TLabel").pack(anchor="w", padx=24, pady=(22, 10))
        ttk.Label(left, textvariable=self.status_var, style="Muted.TLabel", wraplength=430).pack(anchor="w", padx=24)
        ttk.Progressbar(left, variable=self.progress_var, maximum=100).pack(fill=tk.X, padx=24, pady=(16, 22))

        self.steps_frame = tk.Frame(left, bg=CARD_BG)
        self.steps_frame.pack(fill=tk.X, padx=24, pady=(0, 16))

        buttons = tk.Frame(left, bg=CARD_BG)
        buttons.pack(fill=tk.X, padx=24, pady=(4, 24))
        self.open_report_button = ttk.Button(buttons, text="Открыть отчёт", command=self._open_report, state=tk.DISABLED)
        self.open_report_button.pack(side=tk.LEFT)
        self.open_folder_button = ttk.Button(buttons, text="Папка результатов", command=self._open_folder, state=tk.DISABLED)
        self.open_folder_button.pack(side=tk.LEFT, padx=(8, 0))
        self.summary_button = ttk.Button(buttons, text="Summary", command=self._open_summary, state=tk.DISABLED)
        self.summary_button.pack(side=tk.LEFT, padx=(8, 0))

        right = self._card(body)
        right.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        right.grid_columnconfigure(0, weight=1)
        right.grid_columnconfigure(1, weight=1)
        ttk.Label(right, text="Краткие результаты", style="CardTitle.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", padx=24, pady=(22, 10))
        ttk.Label(right, textvariable=self.result_hint_var, style="Muted.TLabel", wraplength=440).grid(row=1, column=0, columnspan=2, sticky="w", padx=24, pady=(0, 16))

        self._metric_card(right, 2, 0, "Доступность", self.metric_access_var, "общий уровень")
        self._metric_card(right, 2, 1, "POI в зоне", self.metric_poi_var, "объектов найдено")
        self._metric_card(right, 3, 0, "Антидрайверы", self.metric_anti_var, "факторы среды")
        self._metric_card(right, 3, 1, "Транспорт", self.metric_transport_var, "остановки и центр")

    def _build_log(self, parent: tk.Misc) -> None:
        log_card = tk.Frame(parent, bg=LOG_BG)
        log_card.grid(row=3, column=0, sticky="ew", pady=(18, 0))
        log_card.grid_columnconfigure(0, weight=1)
        tk.Label(log_card, text="Лог выполнения", bg=LOG_BG, fg="#FFFFFF", font=("Segoe UI", 11, "bold")).grid(row=0, column=0, sticky="w", padx=24, pady=(16, 4))
        self.log = tk.Text(
            log_card,
            wrap=tk.WORD,
            height=7,
            bg=LOG_BG,
            fg=LOG_TEXT,
            insertbackground="#FFFFFF",
            relief=tk.FLAT,
            font=("Consolas", 10),
        )
        self.log.grid(row=1, column=0, sticky="ew", padx=24, pady=(0, 18))
        self._log("Готов к запуску.")

    def _card(self, parent: tk.Misc) -> tk.Frame:
        return tk.Frame(parent, bg=CARD_BG, highlightbackground="#DDE4EF", highlightthickness=1, bd=0)

    def _mode_pill(self, parent: tk.Misc, text: str, mode: str) -> tk.Label:
        label = tk.Label(parent, text=text, padx=18, pady=9, cursor="hand2", font=("Segoe UI", 10, "bold"))
        label.bind("<Button-1>", lambda _event: self._set_mode(mode))
        return label

    def _metric_card(self, parent: tk.Misc, row: int, col: int, title: str, variable: tk.StringVar, desc: str) -> None:
        frame = tk.Frame(parent, bg=CARD_ALT)
        frame.grid(row=row, column=col, sticky="nsew", padx=(24 if col == 0 else 10, 24 if col == 1 else 10), pady=8)
        ttk.Label(frame, text=title, style="MetricTitle.TLabel").pack(anchor="w", padx=18, pady=(14, 0))
        ttk.Label(frame, textvariable=variable, style="MetricValue.TLabel").pack(anchor="w", padx=18, pady=(2, 0))
        tk.Label(frame, text=desc, bg=CARD_ALT, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w", padx=18, pady=(0, 14))

    def _set_mode(self, mode: str) -> None:
        if not self._is_running:
            self.mode_var.set(mode)
            self._apply_mode()

    def _apply_mode(self) -> None:
        if self.mode_var.get() == "comparison":
            self.single_form.grid_remove()
            self.compare_form.grid()
            self._render_steps(COMPARISON_STEPS)
            self.status_var.set("Режим сравнения двух локаций")
            self.result_hint_var.set("Сравнение сохранит Excel, summary и изображения.")
            self.single_pill.configure(bg=CARD_ALT, fg="#334155")
            self.comparison_pill.configure(bg=PRIMARY, fg="#FFFFFF")
        else:
            self.compare_form.grid_remove()
            self.single_form.grid()
            self._render_steps(SINGLE_STEPS)
            self.status_var.set("Ожидание")
            self.result_hint_var.set("Отчёт ещё не создан")
            self.single_pill.configure(bg=PRIMARY, fg="#FFFFFF")
            self.comparison_pill.configure(bg=CARD_ALT, fg="#334155")

        self._paint_sidebar()

    def _paint_sidebar(self) -> None:
        active = self.mode_var.get()
        for mode, frame in self._sidebar_labels.items():
            bg = PRIMARY if mode == active else SIDEBAR_BG
            frame.configure(bg=bg)
            children = frame.winfo_children()
            for child in children:
                child.configure(bg=bg)
            if children:
                children[0].configure(fg="#FFFFFF" if mode == active else "#718096")
            if len(children) > 1:
                children[1].configure(fg="#FFFFFF" if mode == active else "#B7C3D4")

    def _render_steps(self, steps: list[str]) -> None:
        for child in self.steps_frame.winfo_children():
            child.destroy()
        self._step_widgets.clear()
        for index, step in enumerate(steps, start=1):
            row = tk.Frame(self.steps_frame, bg=CARD_BG)
            row.pack(fill=tk.X, pady=5)
            num = tk.Label(row, text=str(index), width=3, bg="#E6ECF5", fg=MUTED, font=("Segoe UI", 9, "bold"))
            num.pack(side=tk.LEFT)
            title = tk.Label(row, text=step, bg=CARD_BG, fg=TEXT, font=("Segoe UI", 10, "bold"))
            title.pack(side=tk.LEFT, padx=(12, 0))
            state = tk.Label(row, text="ожидание", bg=CARD_BG, fg=MUTED, font=("Segoe UI", 9))
            state.pack(side=tk.RIGHT)
            self._step_widgets.append((num, title, state))

    def _update_steps(self, step: int, total: int) -> None:
        if not self._step_widgets:
            return
        active = max(1, min(len(self._step_widgets), int(round((step / max(total, 1)) * len(self._step_widgets)))))
        for index, (num, _title, state) in enumerate(self._step_widgets, start=1):
            if index < active:
                num.configure(bg=SUCCESS, fg="#FFFFFF")
                state.configure(text="готово", fg=SUCCESS)
            elif index == active:
                num.configure(bg=PRIMARY, fg="#FFFFFF")
                state.configure(text="в работе", fg=PRIMARY)
            else:
                num.configure(bg="#E6ECF5", fg=MUTED)
                state.configure(text="ожидание", fg=MUTED)

    def _start(self) -> None:
        if self._is_running:
            return
        if self.mode_var.get() == "single" and not self.address_var.get().strip():
            messagebox.showerror("Geo Analyzer", "Введите адрес.")
            return
        if self.mode_var.get() == "comparison" and (not self.compare_a_var.get().strip() or not self.compare_b_var.get().strip()):
            messagebox.showerror("Geo Analyzer", "Введите обе локации.")
            return

        self._reset_result_state()
        self._set_running(True)
        self._log("")
        self._log("Запуск анализа...")
        target = self._run_comparison if self.mode_var.get() == "comparison" else self._run_single
        threading.Thread(target=target, daemon=True).start()

    def _run_single(self) -> None:
        try:
            result = run_analysis(LocationInput(address=self.address_var.get().strip()), progress_callback=self._progress)
            self._queue.put(("done_single", result))
        except Exception as exc:
            self._queue.put(("error", exc))

    def _run_comparison(self) -> None:
        try:
            result = run_location_comparison(
                LocationInput(address=self.compare_a_var.get().strip()),
                LocationInput(address=self.compare_b_var.get().strip()),
                progress_callback=self._progress,
            )
            self._queue.put(("done_comparison", result))
        except Exception as exc:
            self._queue.put(("error", exc))

    def _progress(self, *args: Any, **kwargs: Any) -> None:
        step = kwargs.get("step") if kwargs else args[0] if len(args) > 0 else 0
        total = kwargs.get("total") if kwargs else args[1] if len(args) > 1 else 1
        msg = kwargs.get("message") if kwargs else args[2] if len(args) > 2 else str(args[0]) if args else ""
        self._queue.put(("progress", (int(step or 0), int(total or 1), str(msg))))

    def _poll_queue(self) -> None:
        try:
            while True:
                event, payload = self._queue.get_nowait()
                if event == "progress":
                    step, total, msg = payload
                    self.progress_var.set(step / max(total, 1) * 100)
                    self.status_var.set(msg)
                    self._update_steps(step, total)
                    self._log(f"[{step}/{total}] {msg}")
                elif event == "done_single":
                    self._handle_single_result(payload)
                    self._finish("Анализ завершён.")
                elif event == "done_comparison":
                    self._handle_comparison_result(payload)
                    self._finish("Сравнение завершено.")
                elif event == "error":
                    self._set_running(False)
                    self.status_var.set("Ошибка")
                    self.result_hint_var.set("Анализ не завершён. Подробности в логе.")
                    self._log(str(payload))
                    messagebox.showerror("Geo Analyzer", str(payload))
        except queue.Empty:
            pass
        self.after(120, self._poll_queue)

    def _handle_single_result(self, result: dict[str, Any]) -> None:
        self._result_dir = _safe_path(result.get("result_dir"))
        self._report_path = _safe_path(result.get("report_path"))
        self._summary_path = _safe_path(result.get("summary_path"))
        self.result_hint_var.set("Excel-отчёт и summary сформированы." if self._report_path else "Анализ завершён.")
        self.metric_poi_var.set(str(self._count_poi(result.get("poi_details_by_iso"))))
        self.metric_access_var.set(self._format_score(self._first_score(result.get("quality_scores") or {}, ["Итоговая оценка", "Итог", "quality_score", "score"])))
        self.metric_transport_var.set(self._format_score(self._first_score(result.get("accessibility_snapshot") or {}, ["transport_score", "Транспортная доступность", "transport"])))
        self.metric_anti_var.set(self._format_anti(result.get("anti_driver_summary")))

    def _handle_comparison_result(self, result: ComparisonResult) -> None:
        self._result_dir = result.result_dir
        self._report_path = result.comparison_path
        self._summary_path = result.summary_path
        self.result_hint_var.set("Сравнение двух локаций сформировано.")
        self.metric_access_var.set("готово")
        self.metric_poi_var.set("2")
        self.metric_anti_var.set("сравнено")
        self.metric_transport_var.set("готово")

    def _finish(self, message: str) -> None:
        self.progress_var.set(100)
        self.status_var.set(message)
        self._update_steps(999, 999)
        self._log(message)
        self._set_running(False)
        self.open_folder_button.configure(state=tk.NORMAL if self._result_dir else tk.DISABLED)
        self.open_report_button.configure(state=tk.NORMAL if self._report_path else tk.DISABLED)
        self.summary_button.configure(state=tk.NORMAL if self._summary_path else tk.DISABLED)

    def _reset_result_state(self) -> None:
        self._result_dir = self._report_path = self._summary_path = None
        self.progress_var.set(0)
        self.status_var.set("Запуск...")
        self.result_hint_var.set("Анализ выполняется")
        for var in [self.metric_access_var, self.metric_poi_var, self.metric_anti_var, self.metric_transport_var]:
            var.set("—")
        self.open_folder_button.configure(state=tk.DISABLED)
        self.open_report_button.configure(state=tk.DISABLED)
        self.summary_button.configure(state=tk.DISABLED)
        self._update_steps(0, 1)

    def _set_running(self, running: bool) -> None:
        self._is_running = running
        self.start_button.configure(state=tk.DISABLED if running else tk.NORMAL)
        state = tk.DISABLED if running else tk.NORMAL
        for entry in [self.single_entry, self.a_entry, self.b_entry]:
            entry.configure(state=state)

    def _log(self, message: str) -> None:
        self.log.insert(tk.END, message + "\n")
        self.log.see(tk.END)

    def _open_folder(self) -> None:
        if not self._result_dir:
            messagebox.showinfo("Geo Analyzer", "????? ?????????? ??? ?? ???????. ??????? ????????? ??????.")
            return

        if not self._result_dir.exists():
            messagebox.showerror("Geo Analyzer", f"????? ?????????? ?? ???????:\n{self._result_dir}")
            return

        self._open_path(self._result_dir)

    def _open_report(self) -> None:
        self._open_path(self._report_path)

    def _open_summary(self) -> None:
        self._open_path(self._summary_path)

    def _open_path(self, path: Path | None) -> None:
        if not path:
            return
        try:
            _open_in_os(path)
        except Exception as exc:
            messagebox.showerror("Geo Analyzer", str(exc))

    def _show_settings(self) -> None:
        key_loaded = bool(_read_env_value("DGIS_API_KEY"))
        messagebox.showinfo(
            "Настройки API",
            f"Файл настроек: {_env_path()}\n\n"
            f"DGIS_API_KEY: {'найден' if key_loaded else 'не найден'}\n\n"
            "Для изменения ключа откройте .env и укажите DGIS_API_KEY=ваш_ключ",
        )

    def _format_score(self, value: Any) -> str:
        number = _safe_float(value)
        return f"{number:.1f} / 10" if number is not None else "—"

    def _first_score(self, data: Any, keys: list[str]) -> Any:
        if isinstance(data, dict):
            for key in keys:
                if key in data:
                    return data[key]
            for value in data.values():
                nested = self._first_score(value, keys)
                if nested is not None:
                    return nested
        return None

    def _count_poi(self, data: Any) -> int:
        try:
            if data is None:
                return 0
            if hasattr(data, "__len__") and not isinstance(data, dict):
                return int(len(data))
            if isinstance(data, dict):
                return sum(len(v) for v in data.values() if hasattr(v, "__len__"))
        except Exception:
            return 0
        return 0

    def _format_anti(self, anti: Any) -> str:
        if anti is None:
            return "—"
        if isinstance(anti, str):
            return anti[:24]
        count = self._count_poi(anti)
        return str(count) if count else "—"


def main() -> int:
    app = GeoAnalyzerApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
