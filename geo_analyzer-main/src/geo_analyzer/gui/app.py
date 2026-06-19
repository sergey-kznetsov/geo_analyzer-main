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
from geo_analyzer.core.settings import get_settings
from geo_analyzer.pipeline.analysis_pipeline import run_analysis
from geo_analyzer.pipeline.comparison_pipeline import ComparisonResult, run_location_comparison

APP_BG = "#F4F7FB"
CARD_BG = "#FFFFFF"
TEXT = "#172033"
MUTED = "#6B7280"
PRIMARY = "#2F6FED"

SINGLE_STEPS = [
    "Геокодирование через 2GIS",
    "Построение контекста",
    "Загрузка POI через 2GIS Places",
    "Классификация POI",
    "Построение изохрон через 2GIS",
    "Привязка POI к изохронам",
    "Автомобильная доступность через 2GIS Routing",
    "Расчёт доступности и сетевых метрик",
    "Расчёт антидрайверов",
    "Расчёт метрик и benchmark",
    "Визуализация",
    "Экспорт Excel и summary",
    "Сохранение meta.json",
]
COMPARISON_STEPS = ["Анализ Локации A", "Анализ Локации B", "Расчёт сравнения", "Экспорт comparison.xlsx", "Сравнение готово"]


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
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            name, raw = stripped.split("=", 1)
            if name.strip() == key:
                return raw.strip().strip('"').strip("'")
    except Exception:
        return value
    return value


def _write_env_value(key: str, value: str) -> None:
    env_path = _env_path()
    env_path.parent.mkdir(parents=True, exist_ok=True)
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    output: list[str] = []
    found = False
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            output.append(line)
            continue
        name, _old = stripped.split("=", 1)
        if name.strip() == key:
            output.append(f"{key}={value}")
            found = True
        else:
            output.append(line)
    if not found:
        output.append(f"{key}={value}")
    env_path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    os.environ[key] = value


def _open_in_os(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(str(path))
    if sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


class GeoAnalyzerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.settings = get_settings()
        self.title("Geo Analyzer")
        self.geometry("1040x680")
        self.minsize(900, 620)
        self.configure(bg=APP_BG)
        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._is_running = False
        self._result_dir: Path | None = None
        self._report_path: Path | None = None
        self._summary_path: Path | None = None
        self._build_styles()
        self._build_ui()
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
        style.configure("TButton", font=("Segoe UI", 10), padding=(14, 8))
        style.configure("Primary.TButton", font=("Segoe UI", 10, "bold"), foreground="#FFFFFF", background=PRIMARY, padding=(16, 9))
        style.configure("TRadiobutton", background=CARD_BG, foreground=TEXT, font=("Segoe UI", 10))

    def _card(self, parent: tk.Misc) -> tk.Frame:
        return tk.Frame(parent, bg=CARD_BG, highlightbackground="#DDE4EF", highlightthickness=1, bd=0)

    def _build_ui(self) -> None:
        root = tk.Frame(self, bg=APP_BG)
        root.pack(fill=tk.BOTH, expand=True, padx=22, pady=18)
        root.grid_columnconfigure(0, weight=1)
        root.grid_rowconfigure(2, weight=1)
        ttk.Label(root, text="Geo Analyzer", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(root, text="Анализ локации и сравнение двух адресов", style="Subtitle.TLabel").grid(row=0, column=0, sticky="w", pady=(34, 0))
        ttk.Button(root, text="Настройки", command=self._show_settings).grid(row=0, column=1, sticky="e")

        input_card = self._card(root)
        input_card.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(16, 16))
        input_card.grid_columnconfigure(1, weight=1)
        input_card.grid_columnconfigure(3, weight=1)
        self.mode_var = tk.StringVar(value="single")
        ttk.Radiobutton(input_card, text="Один адрес", value="single", variable=self.mode_var, command=self._apply_mode).grid(row=0, column=0, padx=18, pady=(16, 6), sticky="w")
        ttk.Radiobutton(input_card, text="Сравнить две локации", value="comparison", variable=self.mode_var, command=self._apply_mode).grid(row=0, column=1, padx=8, pady=(16, 6), sticky="w")
        self.address_var = tk.StringVar(value="Ижевск, Пушкинская 277")
        self.compare_a_var = tk.StringVar(value="Ижевск, Пушкинская 277")
        self.compare_b_var = tk.StringVar(value="Ижевск, Красная 131")
        self.single_label = ttk.Label(input_card, text="Адрес", style="Card.TLabel")
        self.single_entry = ttk.Entry(input_card, textvariable=self.address_var)
        self.a_label = ttk.Label(input_card, text="Локация A", style="Card.TLabel")
        self.a_entry = ttk.Entry(input_card, textvariable=self.compare_a_var)
        self.b_label = ttk.Label(input_card, text="Локация B", style="Card.TLabel")
        self.b_entry = ttk.Entry(input_card, textvariable=self.compare_b_var)
        self.start_button = ttk.Button(input_card, text="Начать анализ", command=self._start, style="Primary.TButton")
        self.start_button.grid(row=1, column=4, padx=18, pady=12, sticky="e")
        for entry in [self.single_entry, self.a_entry, self.b_entry]:
            entry.bind("<Return>", lambda _event: self._start())
        self._apply_mode()

        body = tk.Frame(root, bg=APP_BG)
        body.grid(row=2, column=0, columnspan=2, sticky="nsew")
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)
        left = self._card(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 9))
        right = self._card(body)
        right.grid(row=0, column=1, sticky="nsew", padx=(9, 0))
        self.status_var = tk.StringVar(value="Ожидание")
        self.progress_var = tk.DoubleVar(value=0)
        ttk.Label(left, text="Статус", style="Card.TLabel").pack(anchor="w", padx=16, pady=(16, 4))
        ttk.Label(left, textvariable=self.status_var, style="Card.TLabel", wraplength=430).pack(anchor="w", padx=16)
        ttk.Progressbar(left, variable=self.progress_var, maximum=100).pack(fill=tk.X, padx=16, pady=14)
        buttons = tk.Frame(left, bg=CARD_BG)
        buttons.pack(fill=tk.X, padx=16, pady=(0, 16))
        self.open_folder_button = ttk.Button(buttons, text="Открыть папку", command=self._open_folder, state=tk.DISABLED)
        self.open_folder_button.pack(side=tk.LEFT)
        self.open_report_button = ttk.Button(buttons, text="Открыть отчёт", command=self._open_report, state=tk.DISABLED)
        self.open_report_button.pack(side=tk.LEFT, padx=(8, 0))
        self.summary_button = ttk.Button(buttons, text="Открыть summary", command=self._open_summary, state=tk.DISABLED)
        self.summary_button.pack(side=tk.LEFT, padx=(8, 0))
        ttk.Label(right, text="Лог", style="Card.TLabel").pack(anchor="w", padx=16, pady=(16, 4))
        self.log = tk.Text(right, wrap=tk.WORD, height=20, font=("Consolas", 10))
        self.log.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 16))
        self._log("Готов к запуску.")

    def _apply_mode(self) -> None:
        for widget in [self.single_label, self.single_entry, self.a_label, self.a_entry, self.b_label, self.b_entry]:
            widget.grid_forget()
        if self.mode_var.get() == "comparison":
            self.a_label.grid(row=1, column=0, padx=(18, 10), pady=12, sticky="w")
            self.a_entry.grid(row=1, column=1, padx=(0, 10), pady=12, sticky="ew")
            self.b_label.grid(row=1, column=2, padx=(8, 10), pady=12, sticky="w")
            self.b_entry.grid(row=1, column=3, padx=(0, 10), pady=12, sticky="ew")
        else:
            self.single_label.grid(row=1, column=0, padx=(18, 10), pady=12, sticky="w")
            self.single_entry.grid(row=1, column=1, columnspan=3, padx=(0, 10), pady=12, sticky="ew")

    def _start(self) -> None:
        if self._is_running:
            return
        mode = self.mode_var.get()
        if mode == "single" and not self.address_var.get().strip():
            messagebox.showerror("Geo Analyzer", "Введите адрес.")
            return
        if mode == "comparison" and (not self.compare_a_var.get().strip() or not self.compare_b_var.get().strip()):
            messagebox.showerror("Geo Analyzer", "Введите обе локации.")
            return
        self._set_running(True)
        self._result_dir = self._report_path = self._summary_path = None
        self._log("")
        self._log("Запуск...")
        target = self._run_comparison if mode == "comparison" else self._run_single
        threading.Thread(target=target, daemon=True).start()

    def _run_single(self) -> None:
        try:
            result = run_analysis(LocationInput(address=self.address_var.get().strip()), progress_callback=self._progress)
            self._queue.put(("done_single", result))
        except Exception as exc:
            self._queue.put(("error", exc))

    def _run_comparison(self) -> None:
        try:
            result = run_location_comparison(LocationInput(address=self.compare_a_var.get().strip()), LocationInput(address=self.compare_b_var.get().strip()), progress_callback=self._progress)
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
                    self._log(f"[{step}/{total}] {msg}")
                elif event == "done_single":
                    self._result_dir = Path(payload.get("result_dir"))
                    self._report_path = Path(payload.get("report_path"))
                    self._summary_path = Path(payload.get("summary_path"))
                    self._finish("Анализ завершён.")
                elif event == "done_comparison":
                    result: ComparisonResult = payload
                    self._result_dir = result.result_dir
                    self._report_path = result.comparison_path
                    self._summary_path = result.summary_path
                    self._finish("Сравнение завершено.")
                elif event == "error":
                    self._set_running(False)
                    self.status_var.set("Ошибка")
                    self._log(str(payload))
                    messagebox.showerror("Geo Analyzer", str(payload))
        except queue.Empty:
            pass
        self.after(150, self._poll_queue)

    def _finish(self, message: str) -> None:
        self.progress_var.set(100)
        self.status_var.set(message)
        self._log(message)
        self._set_running(False)
        self.open_folder_button.configure(state=tk.NORMAL if self._result_dir else tk.DISABLED)
        self.open_report_button.configure(state=tk.NORMAL if self._report_path else tk.DISABLED)
        self.summary_button.configure(state=tk.NORMAL if self._summary_path else tk.DISABLED)

    def _set_running(self, running: bool) -> None:
        self._is_running = running
        self.start_button.configure(state=tk.DISABLED if running else tk.NORMAL)

    def _log(self, message: str) -> None:
        self.log.insert(tk.END, message + "\n")
        self.log.see(tk.END)

    def _open_folder(self) -> None:
        if self._result_dir:
            _open_in_os(self._result_dir)

    def _open_report(self) -> None:
        if self._report_path:
            _open_in_os(self._report_path)

    def _open_summary(self) -> None:
        if self._summary_path:
            _open_in_os(self._summary_path)

    def _show_settings(self) -> None:
        window = tk.Toplevel(self)
        window.title("Настройки")
        window.geometry("620x180")
        frame = ttk.Frame(window, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text="DGIS_API_KEY").pack(anchor="w")
        key_var = tk.StringVar(value=_read_env_value("DGIS_API_KEY"))
        ttk.Entry(frame, textvariable=key_var, show="*").pack(fill=tk.X, pady=(6, 12))
        def save() -> None:
            _write_env_value("DGIS_API_KEY", key_var.get().strip())
            messagebox.showinfo("Geo Analyzer", "Ключ сохранён.")
            window.destroy()
        ttk.Button(frame, text="Сохранить", command=save).pack(anchor="e")


def main() -> int:
    app = GeoAnalyzerApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
