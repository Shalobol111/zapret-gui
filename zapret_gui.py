#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ZapretGUI v1.0.0
Графический интерфейс для zapret-discord-youtube
Обёртка над существующими .bat файлами — не модифицирует их содержимое.
"""

import os
import sys
import json
import logging
import subprocess
import threading
import time
import glob
import re
import ctypes
import tempfile
import shutil
import zipfile
import webbrowser
import urllib.request
import urllib.error
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Tuple

import customtkinter as ctk
import psutil

# ─── Опциональные зависимости ───────────────────────────────────
try:
    import pystray
    from PIL import Image, ImageDraw
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False


# ════════════════════════════════════════════════════════════════
# Константы
# ════════════════════════════════════════════════════════════════

APP_NAME    = "ZapretGUI"
APP_VERSION = "1.0.0"
WINWS_EXE   = "winws.exe"
SERVICE_BAT = "service.bat"
SETTINGS_FILE = "gui_settings.json"

# Флаг, скрывающий консольное окно дочерних процессов. Критично для сборки
# в .exe без консоли (--windowed): иначе при каждом фоновом вызове powershell
# мелькало бы чёрное окно. CREATE_NO_WINDOW есть в Python 3.7+ на Windows.
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def app_base_dir() -> str:
    """Папка приложения: рядом с .exe (в сборке) или со скриптом (при запуске
    из исходников). Сюда кладём gui_settings.json и gui.log."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def resource_path(rel: str) -> str:
    """Путь к ресурсу, вшитому в сборку PyInstaller (или рядом со скриптом)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def is_admin() -> bool:
    """True, если процесс запущен с правами администратора."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False
LOG_FILE      = "gui.log"

# URL для проверки обновлений через GitHub API (для ссылки на .zip-ассет)
GITHUB_API_URL = "https://api.github.com/repos/Flowseal/zapret-discord-youtube/releases/latest"
# Файл с актуальной версией zapret — тот же источник, что использует service.bat
ZAPRET_VERSION_URL = "https://raw.githubusercontent.com/Flowseal/zapret-discord-youtube/main/.service/version.txt"
# Страница релизов (ручное скачивание, если авто-обновление недоступно)
ZAPRET_RELEASES_URL = "https://github.com/Flowseal/zapret-discord-youtube/releases/latest"

DEFAULT_SETTINGS: Dict = {
    "program_path":      "",
    "last_strategy":     "",
    "ipset_filter":      True,
    "game_filter":       False,
    "autostart":         False,
    "minimize_to_tray":  True,
    "theme":             "dark",
    "check_updates":     True,
    "window_width":      900,
    "window_height":     700,
}

# Номера пунктов меню реального service.bat (передаём в run_service_action):
# 1=Install, 2=Remove, 3=Status, 8=Update Hosts, 10=Diagnostics.

# Возможные расположения конфиг-файлов относительно корня программы
CONFIG_FILE_PATTERNS: Dict[str, List[str]] = {
    "Пользовательские домены": [
        "lists/list-general-user.txt",
        "list-general-user.txt",
    ],
    "Общий список доменов": [
        "lists/list-general.txt",
        "list-general.txt",
    ],
    "Список исключений": [
        "lists/list-exclude.txt",
        "list-exclude.txt",
    ],
    "IP-адреса (ipset)": [
        "lists/ipset-all.txt",
        "ipset-all.txt",
    ],
    "Hosts": ["hosts"],
}

# ════════════════════════════════════════════════════════════════
# Цветовая палитра
# Кортеж = (светлая_тема, тёмная_тема). CustomTkinter сам выбирает
# нужный элемент по текущему режиму и переключает его «на лету».
# ════════════════════════════════════════════════════════════════

ACCENT        = ("#2563eb", "#4c8dff")   # основной синий
ACCENT_HOVER  = ("#1d4ed8", "#3b7ae4")
GREEN         = ("#16a34a", "#2ea043")
GREEN_HOVER   = ("#15803d", "#268839")
RED           = ("#dc2626", "#da3633")
RED_HOVER     = ("#b91c1c", "#b62324")
INFO          = ("#0e7490", "#2b7d9e")
INFO_HOVER    = ("#0c5e74", "#246a87")
NEUTRAL       = ("#dde3ec", "#2b313b")   # вторичная (серая) кнопка
NEUTRAL_HOVER = ("#cbd5e1", "#363d49")

WINDOW_BG  = ("#e9edf3", "#0f1116")
HEADER_BG  = ("#ffffff", "#15181f")
CARD_BG    = ("#ffffff", "#1a1e26")
CARD2_BG   = ("#f4f6fa", "#14171d")   # вложенная/информационная панель
PILL_BG    = ("#eef2f8", "#222833")
BORDER_COL = ("#e2e7f0", "#2a303a")   # тонкая граница карточек

TXT_NORMAL = ("#1f2328", "#e6edf3")
TXT_DIM    = ("#5b6573", "#9aa4b2")
TXT_FAINT  = ("#8a929e", "#727b88")

# Сегментированные переключатели: «дорожка» должна быть отчётливо видна на
# карточке, а невыбранные сегменты — иметь читаемый тёмный текст (в светлой
# теме они раньше сливались с фоном).
SEG_TRACK = ("#d3dbe8", "#21262e")   # фон-дорожка
SEG_UNSEL = ("#d3dbe8", "#21262e")   # невыбранный сегмент = цвет дорожки
SEG_HOVER = ("#c0cbdb", "#2c333c")
SEG_TEXT  = ("#283142", "#e6edf3")   # текст сегментов

# Консоли (tk.Text / tk.Scrollbar — обычные tkinter-виджеты): цвета
# переключаем вручную через _apply_console_theme().
CONSOLE_BG  = ("#f6f8fa", "#0d1117")
CONSOLE_FG  = ("#24292f", "#c9d1d9")
CONSOLE_SEL = ("#add6ff", "#264f78")
CONSOLE_CUR = ("#1f2328", "#e6edf3")

# Подсветка строк диагностики/тестов в журнале (светлая, тёмная)
JOURNAL_COLORS = {
    "ok":    ("#1a7f37", "#3fb950"),
    "warn":  ("#9a6700", "#d29922"),
    "err":   ("#cf222e", "#f85149"),
    "head":  ("#0969da", "#58a6ff"),
    "dim":   ("#6e7781", "#8b949e"),
    "plain": ("#24292f", "#c9d1d9"),
}

# Цвета уровней логирования в журнале (светлая, тёмная)
LOG_LEVEL_COLORS = {
    "DEBUG":    ("#8a929e", "#6e7681"),
    "INFO":     ("#57606a", "#9aa4b2"),
    "WARNING":  ("#9a6700", "#d29922"),
    "ERROR":    ("#cf222e", "#f85149"),
    "CRITICAL": ("#cf222e", "#ff6b6b"),
}


def pick(tup) -> str:
    """Возвращает элемент палитры для текущего режима оформления."""
    return tup[1] if ctk.get_appearance_mode() == "Dark" else tup[0]


# ════════════════════════════════════════════════════════════════
# Логирование
# ════════════════════════════════════════════════════════════════

log = logging.getLogger(APP_NAME)


def setup_logging(log_path: str) -> None:
    log.setLevel(logging.DEBUG)
    if log.handlers:
        return  # уже настроено

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    try:
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        log.addHandler(fh)
    except Exception:
        pass

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    log.addHandler(ch)


# ════════════════════════════════════════════════════════════════
# SettingsManager
# ════════════════════════════════════════════════════════════════

class SettingsManager:
    def __init__(self, path: str):
        self.path = path
        self.data: Dict = DEFAULT_SETTINGS.copy()
        self._load()

    def _load(self) -> None:
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    self.data.update(json.load(f))
        except Exception as e:
            log.error(f"Ошибка загрузки настроек: {e}")

    def save(self) -> None:
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.error(f"Ошибка сохранения настроек: {e}")

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def set(self, key: str, value) -> None:
        self.data[key] = value
        self.save()

    def export_to(self, path: str) -> bool:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            log.error(f"Ошибка экспорта: {e}")
            return False

    def import_from(self, path: str) -> bool:
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.data.update(json.load(f))
            self.save()
            return True
        except Exception as e:
            log.error(f"Ошибка импорта: {e}")
            return False

    def reset(self) -> None:
        self.data = DEFAULT_SETTINGS.copy()
        self.save()


# ════════════════════════════════════════════════════════════════
# StrategyDetector — динамическое обнаружение стратегий
# ════════════════════════════════════════════════════════════════

class StrategyDetector:
    def __init__(self, program_path: str):
        self.program_path = program_path

    def get_strategies(self) -> List[Dict]:
        """Сканирует папку и возвращает список general*.bat стратегий."""
        result = []
        if not self.program_path or not os.path.isdir(self.program_path):
            return result

        pattern = os.path.join(self.program_path, "general*.bat")
        for bat in sorted(glob.glob(pattern)):
            name = os.path.basename(bat)
            result.append({
                "file":    bat,
                "name":    name,
                "display": self._display_name(name),
            })

        log.info(f"Обнаружено стратегий: {len(result)}")
        return result

    @staticmethod
    def _display_name(filename: str) -> str:
        name = filename.removesuffix(".bat").replace("_", " ")
        return " ".join(p.capitalize() for p in name.split())

    def get_install_index(self, filename: str) -> Optional[int]:
        """
        Возвращает 1-based индекс стратегии в списке, который показывает
        service.bat при установке службы. service.bat перечисляет все *.bat
        кроме service*.bat, отсортированные «натуральной» сортировкой
        (числа дополняются нулями) — точно воспроизводим это здесь, чтобы
        передать корректный номер.
        """
        if not self.program_path or not os.path.isdir(self.program_path):
            return None

        bats = [
            os.path.basename(p)
            for p in glob.glob(os.path.join(self.program_path, "*.bat"))
            if not os.path.basename(p).lower().startswith("service")
        ]

        def natural_key(n: str) -> str:
            return re.sub(r"\d+", lambda m: m.group().zfill(8), n).lower()

        bats.sort(key=natural_key)
        for i, b in enumerate(bats, start=1):
            if b.lower() == filename.lower():
                return i
        return None

    def get_config_files(self) -> Dict[str, Optional[str]]:
        """Возвращает словарь {метка: абсолютный_путь | None}."""
        result: Dict[str, Optional[str]] = {}
        for label, patterns in CONFIG_FILE_PATTERNS.items():
            found = None
            for rel in patterns:
                full = os.path.join(self.program_path, rel)
                if os.path.exists(full):
                    found = full
                    break
            result[label] = found
        return result


# ════════════════════════════════════════════════════════════════
# ProcessManager
# ════════════════════════════════════════════════════════════════

class ProcessManager:
    def __init__(self, program_path: str):
        self.program_path = program_path
        self._active_proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()

    def is_winws_running(self) -> bool:
        for proc in psutil.process_iter(["name"]):
            try:
                if proc.info["name"] and proc.info["name"].lower() == WINWS_EXE.lower():
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return False

    def run_strategy(self, bat_file: str, on_exit=None) -> bool:
        if not os.path.exists(bat_file):
            log.error(f"Файл не найден: {bat_file}")
            return False

        log.info(f"Запуск стратегии: {bat_file}")

        def _worker():
            try:
                proc = subprocess.Popen(
                    bat_file,
                    cwd=self.program_path,
                    creationflags=(
                        subprocess.CREATE_NEW_CONSOLE
                        | subprocess.CREATE_NEW_PROCESS_GROUP
                    ),
                    shell=True,
                )
                with self._lock:
                    self._active_proc = proc
                proc.wait()
                log.info("Стратегия завершилась")
            except Exception as e:
                log.error(f"Ошибка запуска стратегии: {e}")
            finally:
                if on_exit:
                    on_exit()

        threading.Thread(target=_worker, daemon=True).start()
        return True

    def stop_winws(self) -> bool:
        stopped = False
        for proc in psutil.process_iter(["name", "pid"]):
            try:
                if proc.info["name"] and proc.info["name"].lower() == WINWS_EXE.lower():
                    proc.terminate()
                    log.info(f"Завершён winws.exe (PID {proc.pid})")
                    stopped = True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        with self._lock:
            if self._active_proc and self._active_proc.poll() is None:
                try:
                    self._active_proc.terminate()
                    stopped = True
                except Exception:
                    pass
                self._active_proc = None

        return stopped

    @staticmethod
    def _clean_console(s: str) -> str:
        """Убирает управляющие символы (form feed от `cls` и т.п.) и
        повторяющийся баннер меню service.bat — оставляем только вывод
        самого действия."""
        s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", s)
        # Вырезаем блоки меню (от заголовка до строки «0. Exit»)
        s = re.sub(r"\s*ZAPRET SERVICE MANAGER.*?0\.\s*Exit\s*", "\n",
                   s, flags=re.S)
        # Схлопываем лишние пустые строки
        s = re.sub(r"\n{3,}", "\n\n", s)
        return s.strip()

    def run_service_action(
        self, service_bat: str, menu_choice: str,
        install_index: Optional[int] = None, timeout: int = 150,
    ) -> Tuple[int, str]:
        """
        Выполняет один пункт меню service.bat с правами администратора.

        Меню service.bat читает выбор через `set /p`, но `set /p` НЕ читает
        перенаправленный из файла stdin, если до этого выполнялся `chcp`
        (а service.bat вызывает chcp в каждом помощнике меню) — известный баг
        cmd. Поэтому stdin не используем вовсе: создаём ПРАВЛЕНУЮ копию
        service.bat рядом с оригиналом (важно для %~dp0), где
          • выбор пункта меню зашит жёстко (и скрипт выходит после первого
            прохода, иначе экшен вернулся бы в меню и зациклился);
          • индекс файла при установке зашит;
          • подтверждения в диагностике автоотвечены «N»;
          • интерактивные `pause` обезврежены.
        Запускаем копию (без stdin), вывод перехватываем в файл.

        Возвращает (код, текст). 0 — успех, -2 — таймаут, -3 — UAC отклонён.
        """
        if not os.path.exists(service_bat):
            return -1, f"Файл не найден: {service_bat}"

        script_dir = os.path.dirname(service_bat) or "."
        # ВАЖНО: имя обязано начинаться на "service" — иначе service.bat
        # включит эту копию в список стратегий (он отбрасывает только
        # 'service*'), индексы поедут, и установка распарсит сам патч-файл.
        # Тот же фильтр 'service*' использует и StrategyDetector.get_install_index,
        # поэтому индексы у GUI и у service.bat останутся согласованными.
        patched = os.path.join(script_dir, "service_zgui_run.bat")
        tmp = tempfile.mkdtemp(prefix="zgui_svc_")
        out_path  = os.path.join(tmp, "output.txt")
        wrap_path = os.path.join(tmp, "wrapper.bat")
        try:
            with open(service_bat, "r", encoding="utf-8-sig", errors="replace") as f:
                body = f.read()

            # 1) Жёсткий выбор пункта меню + выход после первого прохода
            guard = (
                "if defined ZGUI_DONE exit /b 0\r\n"
                'set "ZGUI_DONE=1"\r\n'
                f'set "menu_choice={menu_choice}"'
            )
            body = re.sub(r"(?im)^[ \t]*set /p menu_choice=.*$",
                          guard, body, count=1)
            # 2) Индекс файла при установке службы
            if install_index is not None:
                body = re.sub(r'set /p "choice=Input file index[^"]*"',
                              f'set "choice={install_index}"', body)
            # 3) Подтверждения (диагностика) — отвечаем N
            body = re.sub(r'set /p "CHOICE=[^"]*"', 'set "CHOICE=N"', body)
            # 4) Обезвреживаем паузы
            body = re.sub(r"(?im)^[ \t]*pause[ \t]*$", "rem pause", body)

            with open(patched, "w", encoding="utf-8-sig") as f:
                f.write(body)
            open(out_path, "w").close()
            with open(wrap_path, "w", encoding="utf-8") as f:
                f.write(
                    "@echo off\r\n"
                    f'pushd "{script_dir}"\r\n'
                    f'call "{patched}" admin > "{out_path}" 2>&1\r\n'
                    "popd\r\n"
                )

            used_runas = not is_admin()
            if used_runas:
                ps = (
                    f"Start-Process -FilePath '{wrap_path}' "
                    "-Verb RunAs -Wait -WindowStyle Hidden"
                )
                r = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", ps],
                    capture_output=True, text=True, timeout=timeout,
                    creationflags=CREATE_NO_WINDOW,
                )
                denied = r.returncode != 0
            else:
                # Уже админ — напрямую, без пайпов (фоновые процессы
                # service.bat держали бы пайп открытым). Вывод — в файл.
                subprocess.run(
                    ["cmd", "/c", wrap_path],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=timeout, creationflags=CREATE_NO_WINDOW,
                )
                denied = False

            with open(out_path, "r", encoding="utf-8", errors="replace") as f:
                output = self._clean_console(f.read())

            if denied and not output.strip():
                return -3, (
                    "Не удалось получить права администратора "
                    "(запрос UAC отклонён или не подтверждён)."
                )
            return 0, output
        except subprocess.TimeoutExpired:
            return -2, "Превышено время ожидания — операция не завершилась."
        except Exception as e:
            log.error(f"run_service_action: {e}")
            return -1, str(e)
        finally:
            for p in (out_path, wrap_path, patched):
                try:
                    os.remove(p)
                except Exception:
                    pass
            try:
                os.rmdir(tmp)
            except Exception:
                pass

    def run_test_script(
        self, script_path: str, choices: List[str],
        on_text, on_done, timeout: int = 3600
    ) -> None:
        """
        Запускает оригинальный 'utils/test zapret.ps1' с правами админа,
        управляя интерактивными вопросами через stdin (тип теста, режим,
        выбор конфигов) и транслируя вывод в реальном времени.

        on_text(chunk)         — вызывается для каждой новой порции вывода;
        on_done(ok, err, full) — по завершении (full — весь текст вывода).

        Работа идёт в отдельных потоках, метод возвращается сразу.
        """
        if not os.path.exists(script_path):
            on_done(False, f"Файл не найден: {script_path}", "")
            return

        tmp = tempfile.mkdtemp(prefix="zgui_test_")
        in_path   = os.path.join(tmp, "input.txt")
        out_path  = os.path.join(tmp, "output.txt")
        wrap_path = os.path.join(tmp, "wrapper.bat")

        # Готовим «тихую» копию скрипта РЯДОМ с оригиналом (тот же каталог
        # критичен — скрипт опирается на $PSScriptRoot). Удаляем интерактивные
        # паузы [System.Console]::ReadKey(): при перенаправленном из файла stdin
        # они либо бросают исключение, либо подвешивают процесс в самом конце,
        # из-за чего кнопка навсегда застревала в «Тестирование…».
        script_dir = os.path.dirname(script_path) or "."
        run_script = os.path.join(script_dir, "_zgui_runtest.ps1")
        target_script = script_path
        try:
            with open(script_path, "r", encoding="utf-8-sig", errors="replace") as f:
                body = f.read()
            body = re.sub(
                r"\[void\]\s*\[System\.Console\]::ReadKey\([^)]*\)",
                "# [pause removed by ZapretGUI]", body)
            body = re.sub(
                r"\[System\.Console\]::ReadKey\([^)]*\)", "$null", body)
            body = re.sub(
                r'(?im)^[ \t]*Write-Host[ \t]+"Press any key[^"]*".*$', "", body)
            with open(run_script, "w", encoding="utf-8-sig") as f:
                f.write(body)
            target_script = run_script
        except Exception as e:
            log.warning(f"Не удалось подготовить копию теста, использую оригинал: {e}")
            run_script = None

        with open(in_path, "w", encoding="utf-8") as f:
            f.write("\n".join(choices) + "\n")
        open(out_path, "w").close()
        with open(wrap_path, "w", encoding="utf-8") as f:
            f.write(
                "@echo off\r\n"
                "chcp 65001 >nul\r\n"
                f'cd /d "{self.program_path}"\r\n'
                f'powershell -NoProfile -ExecutionPolicy Bypass '
                f'-File "{target_script}" < "{in_path}" > "{out_path}" 2>&1\r\n'
            )

        done = threading.Event()

        def _poller():
            pos = 0
            while not done.is_set():
                try:
                    with open(out_path, "r", encoding="utf-8", errors="replace") as f:
                        f.seek(pos)
                        chunk = f.read()
                        pos = f.tell()
                    if chunk:
                        on_text(chunk)
                except Exception:
                    pass
                time.sleep(0.4)

        def _runner():
            ok, err = True, ""
            try:
                used_runas = not is_admin()
                if used_runas:
                    ps = (
                        f"Start-Process -FilePath '{wrap_path}' "
                        "-Verb RunAs -Wait -WindowStyle Hidden"
                    )
                    r = subprocess.run(
                        ["powershell", "-NoProfile", "-Command", ps],
                        capture_output=True, text=True, timeout=timeout,
                        creationflags=CREATE_NO_WINDOW,
                    )
                else:
                    # Уже админ — напрямую, без пайпов (см. комментарий в
                    # run_service_menu): фоновые процессы теста держали бы
                    # пайп открытым. Вывод идёт в output.txt.
                    r = subprocess.run(
                        ["cmd", "/c", wrap_path],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        timeout=timeout, creationflags=CREATE_NO_WINDOW,
                    )
                if used_runas and r.returncode != 0:
                    try:
                        empty = os.path.getsize(out_path) == 0
                    except Exception:
                        empty = True
                    if empty:
                        ok = False
                        err = ("Не удалось запустить тесты "
                               "(UAC отклонён?).\n" + (r.stderr or ""))
            except subprocess.TimeoutExpired:
                ok, err = False, "Превышено время ожидания тестов."
            except Exception as e:
                ok, err = False, str(e)
            finally:
                done.set()
                time.sleep(0.6)  # дать поллеру дочитать остаток
                full = ""
                try:
                    with open(out_path, "r", encoding="utf-8", errors="replace") as f:
                        full = f.read()
                except Exception:
                    pass
                paths = [in_path, out_path, wrap_path]
                if run_script:
                    paths.append(run_script)
                for p in paths:
                    try:
                        os.remove(p)
                    except Exception:
                        pass
                try:
                    os.rmdir(tmp)
                except Exception:
                    pass
                on_done(ok, err, full)

        threading.Thread(target=_poller, daemon=True).start()
        threading.Thread(target=_runner, daemon=True).start()


# ════════════════════════════════════════════════════════════════
# Версии zapret — определение и сравнение
# ════════════════════════════════════════════════════════════════

def parse_zapret_version(v: str):
    """Преобразует строку версии (например '1.9.9c') в сравнимый ключ
    вида ((1, 9, 9), 'c'). Возвращает None, если разобрать не удалось.
    Поддерживает буквенные суффиксы — сравнение не строковое."""
    if not v:
        return None
    v = v.strip().lstrip("vV")
    if not v:
        return None
    nums, suffix = [], ""
    for token in v.split("."):
        m = re.match(r"^(\d+)([A-Za-z]*)$", token)
        if not m:
            return None
        nums.append(int(m.group(1)))
        if m.group(2):
            suffix = m.group(2).lower()
    return (tuple(nums), suffix)


def compare_zapret_versions(a: str, b: str) -> Optional[int]:
    """Сравнивает версии: -1 (a<b), 0 (равны), 1 (a>b).
    Возвращает None, если хотя бы одну версию не удалось разобрать —
    вызывающая сторона НЕ должна считать версию устаревшей в этом случае."""
    ka, kb = parse_zapret_version(a), parse_zapret_version(b)
    if ka is None or kb is None:
        return None
    return (ka > kb) - (ka < kb)


def read_local_zapret_version(program_path: str) -> Optional[str]:
    """Читает LOCAL_VERSION из service.bat установленной сборки zapret.
    Это источник правды об установленной версии (как и сам service.bat)."""
    if not program_path:
        return None
    sb = os.path.join(program_path, SERVICE_BAT)
    if not os.path.isfile(sb):
        return None
    try:
        with open(sb, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = re.search(r'set\s+"?LOCAL_VERSION=([^"\r\n]+)"?', line, re.I)
                if m:
                    return m.group(1).strip()
    except Exception as e:
        log.warning(f"Чтение версии zapret: {e}")
    return None


# ════════════════════════════════════════════════════════════════
# UpdateChecker — сравнивает установленную версию zapret с актуальной
# ════════════════════════════════════════════════════════════════

class UpdateChecker:
    """Проверяет версию установленного zapret против актуальной в репозитории.
    Никогда не считает версию устаревшей при сетевой ошибке или нечитаемой
    локальной версии — в таких случаях отдаёт статус 'error'/'unknown'.

    Результат — dict со статусом:
      'current'  — установлена последняя версия
      'outdated' — доступна новая (в res['download_url'] — ссылка на .zip)
      'unknown'  — не удалось определить/сравнить версии (не предлагаем обновление)
      'error'    — сетевая ошибка (в res['error'] — текст)
    """

    def __init__(self, program_path: str, callback=None):
        self.program_path = program_path
        self.callback = callback

    def check_async(self) -> None:
        threading.Thread(target=self._check, daemon=True).start()

    def fetch_latest_version(self) -> Optional[str]:
        """Скачивает строку актуальной версии из version.txt."""
        req = urllib.request.Request(
            ZAPRET_VERSION_URL,
            headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}",
                     "Cache-Control": "no-cache"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read().decode("utf-8", "replace").strip()

    def fetch_download_url(self) -> Optional[str]:
        """Возвращает прямую ссылку на .zip-ассет последнего релиза."""
        req = urllib.request.Request(
            GITHUB_API_URL,
            headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        for asset in data.get("assets", []):
            name = asset.get("name", "")
            if name.lower().endswith(".zip"):
                return asset.get("browser_download_url")
        # запасной вариант — zip всего репозитория
        return data.get("zipball_url")

    def _emit(self, result: dict) -> None:
        if self.callback:
            self.callback(result)

    def _check(self) -> None:
        local = read_local_zapret_version(self.program_path)
        try:
            latest = self.fetch_latest_version()
        except Exception as e:
            log.warning(f"Проверка версии zapret: {e}")
            self._emit({"status": "error", "local": local,
                        "latest": None, "error": str(e)})
            return

        if not latest:
            self._emit({"status": "error", "local": local,
                        "latest": None, "error": "пустой ответ сервера"})
            return

        # Не можем прочитать локальную версию — НЕ считаем устаревшей.
        if not local:
            self._emit({"status": "unknown", "local": None, "latest": latest})
            return

        cmp = compare_zapret_versions(local, latest)
        if cmp is None:
            self._emit({"status": "unknown", "local": local, "latest": latest})
        elif cmp >= 0:
            self._emit({"status": "current", "local": local, "latest": latest})
        else:
            # Устарела — заранее получаем ссылку на скачивание
            download = None
            try:
                download = self.fetch_download_url()
            except Exception as e:
                log.warning(f"Ссылка на загрузку: {e}")
            self._emit({"status": "outdated", "local": local,
                        "latest": latest, "download_url": download})


# ════════════════════════════════════════════════════════════════
# ZapretUpdater — скачивает архив релиза и заменяет файлы установки
# ════════════════════════════════════════════════════════════════

class ZapretUpdater:
    """Скачивает .zip последнего релиза и аккуратно заменяет файлы
    установленной сборки zapret. Пользовательские списки не затираются."""

    # Файлы, которые НЕ перезаписываем (правки пользователя)
    KEEP_FILES = ("list-general-user.txt",)
    # Службы zapret, держащие winws.exe и драйвер WinDivert
    SERVICE_NAMES = ("zapret", "WinDivert", "WinDivert14")

    def __init__(self, program_path: str, download_url: str):
        self.program_path = program_path
        self.download_url = download_url
        self._pending_old: List[str] = []   # переименованные занятые файлы (.zgui_old)
        self._restart_zapret = False        # перезапустить службу zapret после обновления

    def run(self, progress=None) -> Tuple[bool, str]:
        """Выполняет обновление. progress(text) — колбэк прогресса.
        Возвращает (успех, сообщение). Любая ошибка возвращается как (False, …)."""
        def say(msg: str) -> None:
            log.info(msg)
            if progress:
                progress(msg)

        if not self.program_path or not os.path.isdir(self.program_path):
            return False, "Путь к zapret не задан или не существует."
        if not os.access(self.program_path, os.W_OK):
            return False, "Нет прав на запись в папку zapret."
        if not self.download_url:
            return False, "Не получена ссылка на скачивание обновления."

        tmpdir = tempfile.mkdtemp(prefix="zapret_upd_")
        zip_path = os.path.join(tmpdir, "zapret_update.zip")
        try:
            # 1. Скачивание
            say("Скачивание архива обновления…")
            try:
                req = urllib.request.Request(
                    self.download_url,
                    headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"},
                )
                with urllib.request.urlopen(req, timeout=120) as resp, \
                        open(zip_path, "wb") as f:
                    shutil.copyfileobj(resp, f)
            except Exception as e:
                return False, f"Не удалось скачать файл: {e}"

            if not os.path.isfile(zip_path) or os.path.getsize(zip_path) < 1024:
                return False, "Скачанный архив повреждён (слишком мал)."

            # 2. Распаковка + проверка целостности
            say("Проверка и распаковка архива…")
            extract_dir = os.path.join(tmpdir, "extracted")
            try:
                with zipfile.ZipFile(zip_path) as z:
                    bad = z.testzip()
                    if bad is not None:
                        return False, f"Архив повреждён (битый файл: {bad})."
                    z.extractall(extract_dir)
            except zipfile.BadZipFile:
                return False, "Архив повреждён (некорректный ZIP)."
            except Exception as e:
                return False, f"Не удалось распаковать архив: {e}"

            # 3. Находим корень сборки (GitHub кладёт всё в подпапку)
            src_root = self._find_source_root(extract_dir)
            if src_root is None:
                return False, "В архиве не найдена сборка zapret (нет service.bat)."

            # 4. Освобождаем файлы: останавливаем службы и процесс winws.exe
            say("Остановка служб zapret и WinDivert…")
            self._restart_zapret = self._service_running("zapret")
            self._stop_services()
            self._kill_winws()
            time.sleep(1.0)  # даём ОС закрыть хендлы

            # 5. Копируем файлы поверх установки (с запасным переименованием)
            say("Замена файлов установки…")
            try:
                self._copy_tree(src_root, self.program_path)
            except PermissionError as e:
                return False, (
                    "Не удалось заменить занятый файл. Закройте запущенный zapret "
                    f"и повторите. Подробности: {e}")
            except Exception as e:
                return False, f"Не удалось заменить файлы: {e}"

            # 6. Перезапускаем службу zapret, если она работала до обновления
            if self._restart_zapret:
                say("Перезапуск службы zapret…")
                self._start_service("zapret")

            self._cleanup_old_files()
            new_ver = read_local_zapret_version(self.program_path) or "?"
            return True, f"zapret успешно обновлён до версии {new_ver}."
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    @staticmethod
    def _find_source_root(extract_dir: str) -> Optional[str]:
        """Ищет папку, содержащую service.bat."""
        if os.path.isfile(os.path.join(extract_dir, SERVICE_BAT)):
            return extract_dir
        try:
            for entry in os.listdir(extract_dir):
                sub = os.path.join(extract_dir, entry)
                if os.path.isdir(sub) and \
                        os.path.isfile(os.path.join(sub, SERVICE_BAT)):
                    return sub
        except OSError:
            pass
        for root, _dirs, files in os.walk(extract_dir):
            if SERVICE_BAT in files:
                return root
        return None

    @staticmethod
    def _kill_winws() -> None:
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", WINWS_EXE],
                capture_output=True, creationflags=CREATE_NO_WINDOW,
            )
        except Exception:
            pass

    @staticmethod
    def _service_running(name: str) -> bool:
        """True, если служба существует и находится в состоянии RUNNING."""
        try:
            r = subprocess.run(
                ["sc", "query", name], capture_output=True,
                creationflags=CREATE_NO_WINDOW,
            )
            out = r.stdout.decode("utf-8", "replace") + r.stderr.decode("utf-8", "replace")
            return "RUNNING" in out.upper()
        except Exception:
            return False

    def _stop_services(self) -> None:
        """Останавливает службы zapret/WinDivert, чтобы освободить файлы."""
        for name in self.SERVICE_NAMES:
            for cmd in (["net", "stop", name], ["sc", "stop", name]):
                try:
                    subprocess.run(cmd, capture_output=True,
                                   creationflags=CREATE_NO_WINDOW, timeout=30)
                except Exception:
                    pass

    @staticmethod
    def _start_service(name: str) -> None:
        try:
            subprocess.run(["net", "start", name], capture_output=True,
                           creationflags=CREATE_NO_WINDOW, timeout=30)
        except Exception:
            pass

    def _cleanup_old_files(self) -> None:
        """Удаляет временные .zgui_old, оставшиеся от занятых файлов."""
        for old in self._pending_old:
            try:
                os.remove(old)
            except OSError:
                pass  # ещё занят — ОС удалит при следующем запуске/перезагрузке
        self._pending_old.clear()

    def _safe_copy(self, src_file: str, dst_file: str) -> None:
        """Копирует файл поверх dst_file. Если файл занят (WinError 32),
        переименовывает старый в .zgui_old (это работает для занятых файлов
        на Windows) и копирует новый на освободившееся имя."""
        try:
            shutil.copy2(src_file, dst_file)
            return
        except (PermissionError, OSError):
            if not os.path.exists(dst_file):
                raise  # дело не в занятости — пробрасываем ошибку
            old = dst_file + ".zgui_old"
            try:
                if os.path.exists(old):
                    os.remove(old)
            except OSError:
                pass
            # переименование занятого файла на Windows допустимо
            os.replace(dst_file, old)
            self._pending_old.append(old)
            shutil.copy2(src_file, dst_file)

    def _copy_tree(self, src: str, dst: str) -> None:
        """Копирует дерево src поверх dst, сохраняя пользовательские списки."""
        for root, _dirs, files in os.walk(src):
            rel = os.path.relpath(root, src)
            target = dst if rel == "." else os.path.join(dst, rel)
            os.makedirs(target, exist_ok=True)
            for name in files:
                dst_file = os.path.join(target, name)
                if name in self.KEEP_FILES and os.path.isfile(dst_file):
                    continue  # не затираем пользовательские домены
                self._safe_copy(os.path.join(root, name), dst_file)


# ════════════════════════════════════════════════════════════════
# AutostartManager
# ════════════════════════════════════════════════════════════════

class AutostartManager:
    # Реестровый Run не работает для exe с requireAdministrator-манифестом:
    # при старте Windows показывает UAC-запрос, который при автологине игнорируется.
    # Планировщик задач с /rl highest запускает приложение с правами администратора
    # без UAC-запроса — единственный надёжный способ для elevated-приложений.
    _TASK = "ZapretGUI_Autostart"

    @classmethod
    def is_enabled(cls) -> bool:
        try:
            r = subprocess.run(
                ["schtasks", "/query", "/tn", cls._TASK],
                capture_output=True, creationflags=CREATE_NO_WINDOW,
            )
            return r.returncode == 0
        except Exception:
            return False

    @classmethod
    def enable(cls, exe_path: str) -> bool:
        try:
            # Удаляем старое задание если есть
            subprocess.run(
                ["schtasks", "/delete", "/tn", cls._TASK, "/f"],
                capture_output=True, creationflags=CREATE_NO_WINDOW,
            )
            r = subprocess.run([
                "schtasks", "/create",
                "/tn", cls._TASK,
                "/tr", f'"{exe_path}"',
                "/sc", "onlogon",
                "/rl", "highest",   # запуск с правами администратора без UAC-запроса
                "/f",
            ], capture_output=True, creationflags=CREATE_NO_WINDOW)
            if r.returncode != 0:
                err = r.stderr.decode(errors="replace").strip()
                log.error(f"schtasks /create вернул {r.returncode}: {err}")
                return False
            return True
        except Exception as e:
            log.error(f"Автозапуск (включение): {e}")
            return False

    @classmethod
    def disable(cls) -> bool:
        try:
            subprocess.run(
                ["schtasks", "/delete", "/tn", cls._TASK, "/f"],
                capture_output=True, creationflags=CREATE_NO_WINDOW,
            )
            return True
        except Exception as e:
            log.error(f"Автозапуск (выключение): {e}")
            return False
        return True


# ════════════════════════════════════════════════════════════════
# Главное окно приложения
# ════════════════════════════════════════════════════════════════

class ZapretGUI(ctk.CTk):
    def __init__(self):
        # Настройки — первым делом (рядом с .exe / скриптом)
        self.settings = SettingsManager(os.path.join(app_base_dir(), SETTINGS_FILE))

        log_path = os.path.join(app_base_dir(), LOG_FILE)
        setup_logging(log_path)
        log.info(f"══ {APP_NAME} v{APP_VERSION} запущен ══")
        log.info("Права администратора: " + ("да" if is_admin() else "нет"))

        prog_path = self.settings.get("program_path", "")
        self.proc_mgr = ProcessManager(prog_path)
        self.detector = StrategyDetector(prog_path)
        self.strategies: List[Dict] = []
        self._config_map: Dict[str, str] = {}
        self._poll_id: Optional[str] = None
        self._tray = None
        self._update_download_url: Optional[str] = None  # ссылка на .zip обновления
        # (виджет, роль) для tk.Text-консолей — перекрашиваются при смене темы
        self._consoles: List[Tuple[tk.Text, str]] = []

        ctk.set_appearance_mode(self.settings.get("theme", "dark"))
        ctk.set_default_color_theme("blue")

        super().__init__()
        self.configure(fg_color=WINDOW_BG)

        # Иконка окна (если вшита в сборку / лежит рядом)
        try:
            ico = resource_path("icon.ico")
            if os.path.exists(ico):
                self.iconbitmap(ico)
        except Exception:
            pass

        w = self.settings.get("window_width", 940)
        h = self.settings.get("window_height", 720)
        self.title(f"{APP_NAME} v{APP_VERSION}")
        self.geometry(f"{w}x{h}")
        self.minsize(780, 600)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self._apply_console_theme()
        self._refresh_strategies()
        self._refresh_config_files()
        self._start_poll()

        if self.settings.get("check_updates", True):
            UpdateChecker(
                prog_path,
                callback=lambda res: self.after(0, self._on_update_result, res, False),
            ).check_async()

        if TRAY_AVAILABLE:
            self._setup_tray()

    # ──────────────────────── UI Building ─────────────────────────

    def _build_ui(self) -> None:
        self._build_header()

        self.tabs = ctk.CTkTabview(
            self, corner_radius=12,
            fg_color=CARD_BG, border_width=1, border_color=BORDER_COL,
            segmented_button_fg_color=SEG_TRACK,
            segmented_button_selected_color=ACCENT,
            segmented_button_selected_hover_color=ACCENT_HOVER,
            segmented_button_unselected_color=SEG_UNSEL,
            segmented_button_unselected_hover_color=SEG_HOVER,
            text_color=SEG_TEXT,
            anchor="w",
            command=self._on_tab_change,
        )
        self.tabs.pack(fill="both", expand=True, padx=14, pady=(2, 6))
        try:
            self.tabs._segmented_button.configure(font=("Segoe UI", 13))
        except Exception:
            pass

        for name in ["Управление", "Службы", "Конфигурация",
                     "Диагностика", "Тесты", "Настройки"]:
            self.tabs.add(name)

        self._build_manage(self.tabs.tab("Управление"))
        self._build_services(self.tabs.tab("Службы"))
        self._build_config(self.tabs.tab("Конфигурация"))
        self._build_diagnostics(self.tabs.tab("Диагностика"))
        self._build_tests(self.tabs.tab("Тесты"))
        self._build_settings(self.tabs.tab("Настройки"))

        self._build_log()

    def _build_header(self) -> None:
        hdr = ctk.CTkFrame(self, height=66, corner_radius=12, fg_color=HEADER_BG,
                           border_width=1, border_color=BORDER_COL)
        hdr.pack(fill="x", padx=14, pady=(12, 6))
        hdr.pack_propagate(False)

        # Логотип-плашка
        badge = ctk.CTkFrame(hdr, width=44, height=44, corner_radius=12, fg_color=ACCENT)
        badge.pack(side="left", padx=(14, 10), pady=11)
        badge.pack_propagate(False)
        ctk.CTkLabel(
            badge, text="⚡", font=("Segoe UI", 22), text_color="#ffffff",
        ).pack(expand=True)

        titles = ctk.CTkFrame(hdr, fg_color="transparent")
        titles.pack(side="left", pady=12)
        ctk.CTkLabel(
            titles, text=APP_NAME, font=("Segoe UI Semibold", 17),
            text_color=TXT_NORMAL,
        ).pack(anchor="w")
        ctk.CTkLabel(
            titles, text="Обход блокировок Discord / YouTube",
            font=("Segoe UI", 11), text_color=TXT_DIM,
        ).pack(anchor="w")

        # Индикатор прав администратора
        adm_ok = is_admin()
        adm = ctk.CTkFrame(
            hdr, corner_radius=22, height=38,
            fg_color=(("#e6f4ea", "#1b3a26") if adm_ok else PILL_BG),
        )
        adm.pack(side="right", padx=(0, 10), pady=14)
        adm.pack_propagate(False)
        ctk.CTkLabel(
            adm, text=("🛡 Админ" if adm_ok else "🛡 Без прав"),
            font=("Segoe UI Semibold", 12), anchor="center",
            text_color=(GREEN if adm_ok else TXT_DIM),
        ).pack(expand=True, fill="both", padx=14)

        # Статус-индикатор
        pill = ctk.CTkFrame(hdr, corner_radius=22, fg_color=PILL_BG, height=38)
        pill.pack(side="right", padx=16, pady=14)
        pill.pack_propagate(False)

        self._dot = ctk.CTkLabel(
            pill, text="●", font=("Segoe UI", 16),
            text_color=TXT_FAINT, width=20,
        )
        self._dot.pack(side="left", padx=(14, 4))

        self._hdr_status = ctk.CTkLabel(
            pill, text="Не активен", anchor="w",
            font=("Segoe UI Semibold", 12), text_color=TXT_DIM, width=96,
        )
        self._hdr_status.pack(side="left", padx=(0, 16))

    # ── Вкладка «Управление» ──────────────────────────────────────

    def _build_manage(self, parent) -> None:
        parent.grid_columnconfigure(0, weight=1)

        # Выбор стратегии
        sf = self._card(parent)
        sf.grid(row=0, column=0, sticky="ew", padx=14, pady=(16, 8))
        sf.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            sf, text="Стратегия обхода", font=("Segoe UI Semibold", 13),
            text_color=TXT_NORMAL,
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=16, pady=(14, 2))

        self._strat_var = tk.StringVar()
        self._strat_combo = ctk.CTkOptionMenu(
            sf, variable=self._strat_var, values=["—"],
            height=38, font=("Segoe UI", 12),
            fg_color=CARD2_BG, button_color=ACCENT, button_hover_color=ACCENT_HOVER,
            text_color=TXT_NORMAL, text_color_disabled=TXT_DIM,
            command=lambda v: self.settings.set("last_strategy", v),
        )
        self._strat_combo.grid(row=1, column=1, padx=8, pady=(2, 16), sticky="ew")
        ctk.CTkLabel(sf, text="Профиль:", font=("Segoe UI", 12), text_color=TXT_DIM).grid(
            row=1, column=0, padx=(16, 0), pady=(2, 16),
        )
        ctk.CTkButton(
            sf, text="⟳", width=40, height=38, corner_radius=8,
            fg_color=NEUTRAL, hover_color=NEUTRAL_HOVER, text_color=TXT_NORMAL,
            command=self._refresh_strategies,
        ).grid(row=1, column=2, padx=(4, 16), pady=(2, 16))

        # Кнопки управления
        bf = ctk.CTkFrame(parent, fg_color="transparent")
        bf.grid(row=1, column=0, sticky="ew", padx=14, pady=4)
        bf.grid_columnconfigure((0, 1), weight=1)

        self._start_btn = ctk.CTkButton(
            bf, text="▶   Запустить",
            font=("Segoe UI Semibold", 15), height=54, corner_radius=10,
            fg_color=GREEN, hover_color=GREEN_HOVER,
            command=self._start_strategy,
        )
        self._start_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self._stop_btn = ctk.CTkButton(
            bf, text="■   Остановить",
            font=("Segoe UI Semibold", 15), height=54, corner_radius=10,
            fg_color=RED, hover_color=RED_HOVER,
            command=self._stop_strategy,
            state="disabled",
        )
        self._stop_btn.grid(row=0, column=1, sticky="ew", padx=(6, 0))

        # Информационная строка
        inf = ctk.CTkFrame(parent, fg_color=CARD2_BG, corner_radius=10)
        inf.grid(row=2, column=0, sticky="ew", padx=14, pady=8)

        self._info_label = ctk.CTkLabel(
            inf, text="Выберите стратегию и нажмите «Запустить».",
            font=("Segoe UI", 12), text_color=TXT_DIM, wraplength=760, justify="left",
        )
        self._info_label.pack(anchor="w", padx=16, pady=14)

        # Дополнительные опции
        of = self._card(parent)
        of.grid(row=3, column=0, sticky="ew", padx=14, pady=(8, 14))

        ctk.CTkLabel(
            of, text="Дополнительно", font=("Segoe UI Semibold", 13),
            text_color=TXT_NORMAL,
        ).pack(anchor="w", padx=16, pady=(12, 4))
        row = ctk.CTkFrame(of, fg_color="transparent")
        row.pack(anchor="w", padx=12, pady=(0, 12))

        self._ipset_var = tk.BooleanVar(value=self.settings.get("ipset_filter", True))
        ctk.CTkCheckBox(
            row, text="IPSet фильтр", variable=self._ipset_var, font=("Segoe UI", 12),
            command=lambda: self.settings.set("ipset_filter", self._ipset_var.get()),
        ).pack(side="left", padx=8)

        self._game_var = tk.BooleanVar(value=self.settings.get("game_filter", False))
        ctk.CTkCheckBox(
            row, text="Game Filter", variable=self._game_var, font=("Segoe UI", 12),
            command=lambda: self.settings.set("game_filter", self._game_var.get()),
        ).pack(side="left", padx=8)

    # ── Вспомогательные конструкторы UI ───────────────────────────

    @staticmethod
    def _card(parent):
        """Карточка-контейнер с фоном и скруглением."""
        return ctk.CTkFrame(parent, fg_color=CARD_BG, corner_radius=12,
                            border_width=1, border_color=BORDER_COL)

    def _console(self, parent, *, font_size=11, wrap_mode="word"):
        """Создаёт карточку-консоль (tk.Text + CTk-скроллбар) и регистрирует
        её для перекраски при смене темы. Возвращает frame-обёртку; сам
        текстовый виджет доступен как `frame.text`."""
        wrap = ctk.CTkFrame(parent, fg_color=CARD2_BG, corner_radius=10)
        txt = tk.Text(
            wrap, font=("Consolas", font_size), state="disabled",
            relief="flat", borderwidth=0, wrap=wrap_mode,
            padx=10, pady=8, highlightthickness=0,
            bg=pick(CONSOLE_BG), fg=pick(CONSOLE_FG),
            insertbackground=pick(CONSOLE_CUR),
            selectbackground=pick(CONSOLE_SEL), selectforeground=pick(CONSOLE_FG),
        )
        sb = ctk.CTkScrollbar(wrap, command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y", padx=(0, 4), pady=4)
        txt.pack(fill="both", expand=True, padx=(6, 0), pady=6)
        # Перекрашиваем при каждом появлении виджета на экране (переключение вкладок)
        txt.bind("<Map>", lambda _e: self.after(0, self._apply_console_theme))
        self._consoles.append((txt, "console"))
        wrap.text = txt
        return wrap

    # ── Вкладка «Службы» ─────────────────────────────────────────

    def _build_services(self, parent) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(2, weight=1)

        bf = ctk.CTkFrame(parent, fg_color="transparent")
        bf.grid(row=0, column=0, sticky="ew", padx=14, pady=(16, 8))
        for i in range(4):
            bf.grid_columnconfigure(i, weight=1)

        buttons = [
            ("⊕  Установить службу", GREEN,   GREEN_HOVER,   None,        self._install_service),
            ("⊖  Удалить службы",    RED,     RED_HOVER,     None,        self._remove_services),
            ("◦  Статус служб",      NEUTRAL, NEUTRAL_HOVER, TXT_NORMAL,  self._service_status),
            ("↻  Обновить hosts",    INFO,    INFO_HOVER,    None,        self._update_hosts),
        ]
        for i, (text, fg, hv, tc, cmd) in enumerate(buttons):
            kw = {"text_color": tc} if tc else {}
            ctk.CTkButton(
                bf, text=text, height=46, corner_radius=10,
                font=("Segoe UI", 12), fg_color=fg, hover_color=hv,
                command=cmd, **kw,
            ).grid(row=0, column=i, sticky="ew", padx=4)

        ctk.CTkLabel(
            parent, text="Вывод операций", font=("Segoe UI Semibold", 12),
            text_color=TXT_DIM,
        ).grid(row=1, column=0, sticky="w", padx=18, pady=(4, 2))

        cons = self._console(parent)
        cons.grid(row=2, column=0, sticky="nsew", padx=14, pady=(0, 14))
        self._svc_out = cons.text

    # ── Вкладка «Конфигурация» ────────────────────────────────────

    def _build_config(self, parent) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(3, weight=1)

        # Выбор файла + действия
        sf = self._card(parent)
        sf.grid(row=0, column=0, sticky="ew", padx=14, pady=(16, 6))

        top = ctk.CTkFrame(sf, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(12, 6))
        ctk.CTkLabel(top, text="Файл:", font=("Segoe UI", 12), text_color=TXT_DIM).pack(
            side="left", padx=(4, 8),
        )
        self._cfg_var = tk.StringVar()
        self._cfg_combo = ctk.CTkComboBox(
            top, variable=self._cfg_var, values=[], width=280, height=36, border_width=0,
            fg_color=CARD2_BG, button_color=ACCENT, button_hover_color=ACCENT_HOVER,
            command=self._on_config_selected,
        )
        self._cfg_combo.pack(side="left", padx=4)
        ctk.CTkButton(
            top, text="⟳ Список", width=110, height=36, corner_radius=8,
            fg_color=NEUTRAL, hover_color=NEUTRAL_HOVER, text_color=TXT_NORMAL,
            command=self._refresh_config_files,
        ).pack(side="left", padx=4)

        af = ctk.CTkFrame(sf, fg_color="transparent")
        af.pack(fill="x", padx=12, pady=(0, 12))
        self._cfg_save_btn = ctk.CTkButton(
            af, text="💾  Сохранить", width=140, height=36, corner_radius=8,
            fg_color=GREEN, hover_color=GREEN_HOVER,
            command=self._save_config, state="disabled",
        )
        self._cfg_save_btn.pack(side="left", padx=4)
        ctk.CTkButton(
            af, text="📋  Вставить из буфера", width=190, height=36, corner_radius=8,
            fg_color=INFO, hover_color=INFO_HOVER,
            command=self._paste_clipboard,
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            af, text="↺  Перечитать", width=140, height=36, corner_radius=8,
            fg_color=NEUTRAL, hover_color=NEUTRAL_HOVER, text_color=TXT_NORMAL,
            command=lambda: self._on_config_selected(self._cfg_var.get()),
        ).pack(side="left", padx=4)

        self._cfg_info = ctk.CTkLabel(
            parent, text="", font=("Segoe UI", 10), text_color=TXT_FAINT,
        )
        self._cfg_info.grid(row=2, column=0, sticky="w", padx=18, pady=(4, 2))

        # Полноценный редактируемый текст (весь файл)
        pf = ctk.CTkFrame(parent, fg_color=CARD2_BG, corner_radius=10)
        pf.grid(row=3, column=0, sticky="nsew", padx=14, pady=(0, 14))

        self._cfg_editor = tk.Text(
            pf, font=("Consolas", 12), undo=True, relief="flat",
            borderwidth=0, wrap="none", padx=10, pady=8, highlightthickness=0,
            bg=pick(CONSOLE_BG), fg=pick(CONSOLE_FG),
            insertbackground=pick(CONSOLE_CUR),
            selectbackground=pick(CONSOLE_SEL), selectforeground=pick(CONSOLE_FG),
        )
        vsb = ctk.CTkScrollbar(pf, orientation="vertical",   command=self._cfg_editor.yview)
        hsb = ctk.CTkScrollbar(pf, orientation="horizontal", command=self._cfg_editor.xview)
        self._cfg_editor.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side="right",  fill="y", padx=(0, 4), pady=4)
        hsb.pack(side="bottom", fill="x", padx=6, pady=(0, 4))
        self._cfg_editor.pack(fill="both", expand=True, padx=(6, 0), pady=(6, 0))
        self._cfg_editor.bind("<Map>", lambda _e: self.after(0, self._apply_console_theme))
        self._consoles.append((self._cfg_editor, "editor"))

        # Ctrl+S — быстрое сохранение
        self._cfg_editor.bind("<Control-s>", lambda e: (self._save_config(), "break"))

    # ── Вкладка «Диагностика» ─────────────────────────────────────

    def _build_diagnostics(self, parent) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(2, weight=1)

        bf = ctk.CTkFrame(parent, fg_color="transparent")
        bf.grid(row=0, column=0, sticky="ew", padx=14, pady=(16, 8))

        ctk.CTkButton(
            bf, text="▶  Диагностика", height=44, width=170, corner_radius=10,
            font=("Segoe UI", 12), fg_color=ACCENT, hover_color=ACCENT_HOVER,
            command=self._run_diag,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            bf, text="✓  Secure DNS", height=44, corner_radius=10,
            font=("Segoe UI", 12), fg_color=NEUTRAL, hover_color=NEUTRAL_HOVER,
            text_color=TXT_NORMAL, command=self._check_secure_dns,
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            bf, text="⊡  Очистить", height=44, width=110, corner_radius=10,
            font=("Segoe UI", 12), fg_color=NEUTRAL, hover_color=NEUTRAL_HOVER,
            text_color=TXT_NORMAL, command=lambda: self._clear_text(self._diag_out),
        ).pack(side="right")

        ctk.CTkLabel(
            parent, text="Результаты", font=("Segoe UI Semibold", 12), text_color=TXT_DIM,
        ).grid(row=1, column=0, sticky="w", padx=18, pady=(4, 2))

        cons = self._console(parent)
        cons.grid(row=2, column=0, sticky="nsew", padx=14, pady=(0, 14))
        self._diag_out = cons.text

    # ── Вкладка «Тесты» ───────────────────────────────────────────

    def _build_tests(self, parent) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(3, weight=1)

        # Тип теста
        tf = self._card(parent)
        tf.grid(row=0, column=0, sticky="ew", padx=14, pady=(16, 6))
        ctk.CTkLabel(
            tf, text="Тип теста:", font=("Segoe UI", 12), text_color=TXT_DIM,
        ).pack(side="left", padx=(16, 8), pady=12)
        self._test_type = tk.StringVar(value="Стандартный (HTTP/Ping)")
        ctk.CTkSegmentedButton(
            tf, values=["Стандартный (HTTP/Ping)", "DPI checkers (TCP 16-20)"],
            variable=self._test_type, font=("Segoe UI", 12), corner_radius=8,
            selected_color=ACCENT, selected_hover_color=ACCENT_HOVER,
            unselected_color=SEG_UNSEL, unselected_hover_color=SEG_HOVER,
            fg_color=SEG_TRACK, text_color=SEG_TEXT,
        ).pack(side="left", padx=6, pady=12)

        # Управление запуском и выбором
        cf = ctk.CTkFrame(parent, fg_color="transparent")
        cf.grid(row=1, column=0, sticky="ew", padx=14, pady=4)

        self._test_run_btn = ctk.CTkButton(
            cf, text="▶  Запустить тесты", width=190, height=40, corner_radius=10,
            font=("Segoe UI Semibold", 13), fg_color=GREEN, hover_color=GREEN_HOVER,
            command=self._run_tests,
        )
        self._test_run_btn.pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            cf, text="Выбрать все", width=110, height=40, corner_radius=8,
            fg_color=NEUTRAL, hover_color=NEUTRAL_HOVER, text_color=TXT_NORMAL,
            command=lambda: self._test_select_all(True),
        ).pack(side="left", padx=3)
        ctk.CTkButton(
            cf, text="Снять все", width=100, height=40, corner_radius=8,
            fg_color=NEUTRAL, hover_color=NEUTRAL_HOVER, text_color=TXT_NORMAL,
            command=lambda: self._test_select_all(False),
        ).pack(side="left", padx=3)
        ctk.CTkButton(
            cf, text="⟳", width=40, height=40, corner_radius=8,
            fg_color=NEUTRAL, hover_color=NEUTRAL_HOVER, text_color=TXT_NORMAL,
            command=self._refresh_test_list,
        ).pack(side="left", padx=3)

        ctk.CTkLabel(
            parent, justify="left", font=("Segoe UI", 10), text_color=TXT_FAINT,
            text="Отметьте стратегии для проверки — ход и результаты тестов "
                 "транслируются в журнал внизу.\nНужны права администратора; "
                 "служба zapret должна быть удалена (вкладка «Службы»).",
        ).grid(row=2, column=0, sticky="w", padx=18, pady=(6, 2))

        self._test_scroll = ctk.CTkScrollableFrame(
            parent, label_text="Стратегии", fg_color=CARD2_BG,
            label_fg_color=CARD_BG, label_text_color=TXT_DIM,
        )
        self._test_scroll.grid(row=3, column=0, sticky="nsew", padx=14, pady=(4, 14))
        self._test_checks: Dict[str, tk.BooleanVar] = {}

    def _refresh_test_list(self) -> None:
        if not hasattr(self, "_test_scroll"):
            return
        for w in self._test_scroll.winfo_children():
            w.destroy()
        self._test_checks = {}
        if not self.strategies:
            ctk.CTkLabel(
                self._test_scroll, text="Стратегии не найдены — укажите путь к программе.",
                text_color=TXT_FAINT,
            ).pack(anchor="w", padx=8, pady=6)
            return
        for s in self.strategies:
            v = tk.BooleanVar(value=True)
            self._test_checks[s["name"]] = v
            ctk.CTkCheckBox(
                self._test_scroll, text=s["display"], variable=v,
                font=("Segoe UI", 12),
            ).pack(anchor="w", padx=8, pady=2)

    def _test_select_all(self, state: bool) -> None:
        for v in self._test_checks.values():
            v.set(state)

    def _run_tests(self) -> None:
        path = self.settings.get("program_path", "")
        if not path:
            messagebox.showerror("Ошибка", "Укажите путь к программе в настройках.")
            return

        script = os.path.join(path, "utils", "test zapret.ps1")
        if not os.path.exists(script):
            script = self._find_in_tree(path, "test zapret.ps1", max_depth=2)
        if not script:
            messagebox.showerror(
                "Ошибка",
                "Не найден файл тестов «utils/test zapret.ps1» в папке программы.",
            )
            return

        selected = [n for n, v in self._test_checks.items() if v.get()]
        if not selected:
            messagebox.showwarning("Тесты", "Отметьте хотя бы одну стратегию.")
            return

        test_type = "1" if self._test_type.get().startswith("Стандарт") else "2"

        if len(selected) == len(self.strategies):
            choices = [test_type, "1"]          # режим «все конфиги»
            sel_desc = f"все стратегии ({len(selected)})"
        else:
            idxs = sorted(
                i for i in (self.detector.get_install_index(n) for n in selected)
                if i is not None
            )
            if not idxs:
                messagebox.showerror(
                    "Ошибка", "Не удалось определить номера выбранных стратегий.")
                return
            choices = [test_type, "2", ",".join(str(i) for i in idxs)]
            sel_desc = f"{len(idxs)} стратег. (№ {', '.join(str(i) for i in idxs)})"

        uac_note = ("" if is_admin()
                    else "Появится запрос прав администратора (UAC).\n")
        if not messagebox.askyesno(
            "Запуск тестов",
            f"Тип: {self._test_type.get()}\n"
            f"Объём: {sel_desc}\n\n"
            "Тесты могут занять несколько минут. " + uac_note + "\nПродолжить?",
        ):
            return

        self._test_run_btn.configure(state="disabled", text="⏳  Тестирование…")
        wait_note = ("Подтвердите запрос UAC и ожидайте"
                     if not is_admin() else "Ожидайте")
        self._journal_write(
            "\n╔════════════════ ЗАПУСК ТЕСТОВ ════════════════╗\n"
            f"  Тип: {self._test_type.get()}\n"
            f"  Объём: {sel_desc}\n"
            f"  {wait_note} — вывод появится ниже.\n\n"
        )
        log.info(f"Запуск тестов: choices={choices}")

        self.proc_mgr.run_test_script(
            script, choices,
            on_text=lambda c: self.after(0, self._test_stream, c),
            on_done=lambda ok, err, full: self.after(0, self._test_done, ok, err, full),
            timeout=3600,
        )

    # Шаблоны строк-шума от ReadKey/$Host.UI.RawUI.ReadKey() в конце PS-скрипта
    _TEST_NOISE = (
        "ReadKey", "Cannot read keys", "System.Console",
        "console input has been redirected",
        "InvalidOperationException", "ParentContainsErrorRecordException",
        "CategoryInfo", "FullyQualifiedErrorId",
        "Script interrupted",
        "+ +",          # PS error detail prefix
    )

    def _test_stream(self, chunk: str) -> None:
        lines = [
            ln for ln in chunk.splitlines(keepends=True)
            if not any(noise in ln for noise in self._TEST_NOISE)
        ]
        if lines:
            self._journal_write("".join(lines))

    def _test_done(self, ok: bool, err: str, full: str) -> None:
        self._test_run_btn.configure(state="normal", text="▶  Запустить тесты")
        if not ok:
            self._journal_write(f"\n[ERROR] {err}\n")
            log.error(f"Тесты не выполнены: {err.splitlines()[0] if err else ''}")
            return
        self._render_test_summary(full)
        log.info("Тесты завершены")

    def _render_test_summary(self, full: str) -> None:
        """Извлекает блок ANALYTICS и лучшую стратегию, рисует сводку."""
        analytics: List[str] = []
        best: Optional[str] = None
        in_block = False
        for ln in full.splitlines():
            s = ln.strip()
            if s.startswith("=== ANALYTICS"):
                in_block = True
                continue
            if s.startswith("Best config") or s.startswith("Best strategy"):
                best = s.split(":", 1)[1].strip() if ":" in s else None
                in_block = False
                continue
            if in_block and s and ":" in s:
                analytics.append(s)

        bar = "═" * 64
        out = ["", bar, "          📊  РЕЗУЛЬТАТЫ ТЕСТОВ", bar]
        if analytics:
            out.extend("  " + a for a in analytics)
        else:
            out.append("  (детальная аналитика недоступна — см. вывод выше)")
        out.append("─" * 64)
        if best:
            out.append(f"Лучшая стратегия:  {best}")
        out.append(bar + "\n")
        self._journal_write("\n".join(out) + "\n")

    # ── Вкладка «Настройки» ───────────────────────────────────────

    def _build_settings(self, parent) -> None:
        scroll = ctk.CTkScrollableFrame(parent, fg_color=CARD_BG)
        scroll.pack(fill="both", expand=True, padx=6, pady=6)
        scroll.grid_columnconfigure(1, weight=1)
        r = 0

        # ── Путь к программе
        r = self._section(scroll, "Путь к программе zapret", r)

        pf = ctk.CTkFrame(scroll, fg_color="transparent")
        pf.grid(row=r, column=0, columnspan=2, sticky="ew", padx=16, pady=(2, 10))
        pf.grid_columnconfigure(0, weight=1)
        r += 1

        self._path_var = tk.StringVar(value=self.settings.get("program_path", ""))
        ctk.CTkEntry(
            pf, textvariable=self._path_var, font=("Segoe UI", 11), height=38,
            fg_color=CARD2_BG, border_width=0,
            placeholder_text="Укажите путь к папке zapret…",
        ).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ctk.CTkButton(
            pf, text="Обзор…", width=90, height=38, corner_radius=8,
            fg_color=NEUTRAL, hover_color=NEUTRAL_HOVER, text_color=TXT_NORMAL,
            command=self._browse,
        ).grid(row=0, column=1)
        ctk.CTkButton(
            pf, text="✓ Применить", width=120, height=38, corner_radius=8,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, command=self._apply_path,
        ).grid(row=0, column=2, padx=(6, 0))

        # ── Обновление zapret
        r = self._section(scroll, "Обновление zapret", r)

        self._upd_status = ctk.CTkLabel(
            scroll, text="Нажмите «Проверить обновление», чтобы сравнить версии.",
            font=("Segoe UI", 12), text_color=TXT_DIM,
            wraplength=720, justify="left", anchor="w",
        )
        self._upd_status.grid(row=r, column=0, columnspan=2, sticky="w",
                              padx=22, pady=(2, 6))
        r += 1

        uf = ctk.CTkFrame(scroll, fg_color="transparent")
        uf.grid(row=r, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 10))
        r += 1

        self._upd_check_btn = ctk.CTkButton(
            uf, text="🔍  Проверить обновление", height=38, corner_radius=8,
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            command=self._check_zapret_update,
        )
        self._upd_check_btn.pack(side="left", padx=4)

        self._upd_apply_btn = ctk.CTkButton(
            uf, text="⬇  Обновить zapret", height=38, corner_radius=8,
            fg_color=GREEN, hover_color=GREEN_HOVER,
            command=self._apply_zapret_update, state="disabled",
        )
        self._upd_apply_btn.pack(side="left", padx=4)

        # ── Внешний вид
        r = self._section(scroll, "Внешний вид", r)
        ctk.CTkLabel(scroll, text="Тема оформления:", text_color=TXT_NORMAL).grid(
            row=r, column=0, sticky="w", padx=22, pady=8,
        )
        self._theme_var = tk.StringVar(value=self.settings.get("theme", "dark"))
        ctk.CTkSegmentedButton(
            scroll, values=["dark", "light", "system"],
            variable=self._theme_var, command=self._change_theme, corner_radius=8,
            selected_color=ACCENT, selected_hover_color=ACCENT_HOVER,
            unselected_color=SEG_UNSEL, unselected_hover_color=SEG_HOVER,
            fg_color=SEG_TRACK, text_color=SEG_TEXT,
        ).grid(row=r, column=1, sticky="w", padx=10, pady=8)
        r += 1

        # ── Поведение
        r = self._section(scroll, "Поведение", r)

        self._autostart_var = tk.BooleanVar(value=self.settings.get("autostart", False))
        ctk.CTkCheckBox(
            scroll, text="Автозапуск при старте Windows",
            variable=self._autostart_var, command=self._toggle_autostart,
        ).grid(row=r, column=0, columnspan=2, sticky="w", padx=22, pady=6)
        r += 1

        self._tray_var = tk.BooleanVar(value=self.settings.get("minimize_to_tray", True))
        ctk.CTkCheckBox(
            scroll, text="Сворачивать в системный трей при закрытии",
            variable=self._tray_var,
            command=lambda: self.settings.set("minimize_to_tray", self._tray_var.get()),
        ).grid(row=r, column=0, columnspan=2, sticky="w", padx=22, pady=6)
        r += 1

        self._upd_var = tk.BooleanVar(value=self.settings.get("check_updates", True))
        ctk.CTkCheckBox(
            scroll, text="Проверять обновления при запуске",
            variable=self._upd_var,
            command=lambda: self.settings.set("check_updates", self._upd_var.get()),
        ).grid(row=r, column=0, columnspan=2, sticky="w", padx=22, pady=6)
        r += 1

        # ── Экспорт / Импорт
        r = self._section(scroll, "Экспорт / Импорт настроек", r)

        ef = ctk.CTkFrame(scroll, fg_color="transparent")
        ef.grid(row=r, column=0, columnspan=2, sticky="ew", padx=16, pady=(2, 10))
        r += 1

        ctk.CTkButton(
            ef, text="⬆  Экспорт", height=38, corner_radius=8,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, command=self._export,
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            ef, text="⬇  Импорт", height=38, corner_radius=8, command=self._import_cfg,
            fg_color=NEUTRAL, hover_color=NEUTRAL_HOVER, text_color=TXT_NORMAL,
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            ef, text="↺  Сбросить всё", height=38, corner_radius=8,
            fg_color=RED, hover_color=RED_HOVER, command=self._reset_cfg,
        ).pack(side="left", padx=16)

    @staticmethod
    def _section(parent, title: str, row: int) -> int:
        ctk.CTkLabel(
            parent, text=title.upper(),
            font=("Segoe UI Semibold", 11), text_color=TXT_FAINT,
        ).grid(row=row, column=0, columnspan=2, sticky="w", padx=12, pady=(18, 2))
        return row + 1

    # ── Журнал операций ───────────────────────────────────────────

    def _build_log(self) -> None:
        lf = ctk.CTkFrame(self, height=215, corner_radius=12, fg_color=CARD_BG,
                          border_width=1, border_color=BORDER_COL)
        lf.pack(fill="x", padx=14, pady=(0, 12))
        lf.pack_propagate(False)

        hdr = ctk.CTkFrame(lf, fg_color="transparent")
        hdr.pack(fill="x", padx=4, pady=(2, 0))
        ctk.CTkLabel(
            hdr, text="📋  Журнал", font=("Segoe UI Semibold", 12), text_color=TXT_DIM,
        ).pack(side="left", padx=10, pady=6)
        ctk.CTkButton(
            hdr, text="Очистить", width=84, height=26, corner_radius=8,
            fg_color=NEUTRAL, hover_color=NEUTRAL_HOVER, text_color=TXT_NORMAL,
            font=("Segoe UI", 11),
            command=lambda: self._clear_text(self._log_txt),
        ).pack(side="right", padx=8, pady=4)

        body = ctk.CTkFrame(lf, fg_color=CARD2_BG, corner_radius=10)
        body.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self._log_txt = tk.Text(
            body, font=("Consolas", 10), state="disabled", relief="flat",
            borderwidth=0, highlightthickness=0, padx=10, pady=6,
            bg=pick(CONSOLE_BG), fg=pick(CONSOLE_FG),
            insertbackground=pick(CONSOLE_CUR),
            selectbackground=pick(CONSOLE_SEL), selectforeground=pick(CONSOLE_FG),
        )
        sb = ctk.CTkScrollbar(body, command=self._log_txt.yview)
        self._log_txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y", padx=(0, 4), pady=4)
        self._log_txt.pack(fill="both", expand=True, padx=(6, 0), pady=4)
        self._log_txt.bind("<Map>", lambda _e: self.after(0, self._apply_console_theme))
        self._consoles.append((self._log_txt, "log"))

        self._attach_log_handler()

    def _attach_log_handler(self) -> None:
        app = self

        class _GH(logging.Handler):
            def emit(self, record):
                msg  = self.format(record) + "\n"
                lvl  = record.levelname
                app.after(0, self._write, msg, lvl)

            def _write(self, msg, lvl):
                w = app._log_txt
                w.configure(state="normal")
                col = pick(LOG_LEVEL_COLORS.get(lvl, TXT_DIM))
                w.tag_config(lvl, foreground=col)
                w.insert("end", msg, lvl)
                w.see("end")
                w.configure(state="disabled")

        h = _GH()
        h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
        log.addHandler(h)

    @staticmethod
    def _classify_line(line: str) -> str:
        """Определяет цветовой тег строки по её содержимому."""
        s = line.strip()
        if not s:
            return "plain"
        low = s.lower()
        if s[0] in "=–—-#╔╚║╠═┌└│├📊":
            return "head"
        if any(k in low for k in (
                "[error]", "[x]", "fail", ":ssl", "blocked",
                "not found", "not running", "error", "отклон")):
            return "err"
        if any(k in low for k in (
                "[warn", "unsup", "[?]", "warning", "timeout", "likely")):
            return "warn"
        if any(k in low for k in (
                "[ok]", "passed", ":ok", "status=ok", "is running",
                "best config", "best strategy", "лучшая", " ok:")):
            return "ok"
        if low.startswith("[info]"):
            return "dim"
        return "plain"

    def _journal_write(self, text: str) -> None:
        """Пишет текст в журнал с построчной цветовой подсветкой."""
        w = self._log_txt
        w.configure(state="normal")
        for line in text.splitlines(keepends=True):
            tag = self._classify_line(line)
            w.tag_config(tag, foreground=pick(JOURNAL_COLORS[tag]))
            w.insert("end", line, tag)
        w.see("end")
        w.configure(state="disabled")

    def _apply_console_theme(self) -> None:
        """Перекрашивает все tk.Text-консоли и теги под текущую тему."""
        bg, fg = pick(CONSOLE_BG), pick(CONSOLE_FG)
        sel, cur = pick(CONSOLE_SEL), pick(CONSOLE_CUR)
        for w, _role in self._consoles:
            try:
                w.configure(
                    bg=bg, fg=fg, insertbackground=cur,
                    selectbackground=sel, selectforeground=fg,
                )
                # обновляем уже расставленные цветовые теги (журнал)
                for tag, pair in JOURNAL_COLORS.items():
                    if tag in w.tag_names():
                        w.tag_config(tag, foreground=pick(pair))
                for lvl, pair in LOG_LEVEL_COLORS.items():
                    if lvl in w.tag_names():
                        w.tag_config(lvl, foreground=pick(pair))
            except tk.TclError:
                pass

    def _on_tab_change(self, tab_name: str = "") -> None:
        # Вызываем немедленно (до рендера) и после flush очереди (после рендера),
        # чтобы tk.Text не мерцал ни в тёмной, ни в светлой теме.
        self._apply_console_theme()
        self.after(0, self._apply_console_theme)

    # ──────────────────── Управление стратегиями ──────────────────

    def _refresh_strategies(self) -> None:
        self.strategies = self.detector.get_strategies()
        if not self.strategies:
            self._strat_combo.configure(values=["Стратегии не найдены"])
            self._strat_var.set("Стратегии не найдены")
            log.warning("Файлы general*.bat не найдены")
            self._refresh_test_list()
            return

        vals = [s["display"] for s in self.strategies]
        self._strat_combo.configure(values=vals)
        last = self.settings.get("last_strategy", "")
        self._strat_var.set(last if last in vals else vals[0])
        log.info(f"Стратегии: {vals}")
        self._refresh_test_list()

    def _selected_strategy(self) -> Optional[Dict]:
        disp = self._strat_var.get()
        return next((s for s in self.strategies if s["display"] == disp), None)

    def _start_strategy(self) -> None:
        s = self._selected_strategy()
        if not s:
            messagebox.showwarning("Предупреждение", "Выберите стратегию из списка.")
            return

        if self.proc_mgr.is_winws_running():
            if not messagebox.askyesno(
                "winws.exe уже работает",
                "winws.exe запущен. Остановить и перезапустить?",
            ):
                return
            self.proc_mgr.stop_winws()
            time.sleep(0.8)

        self._info_label.configure(text=f"Запуск: {s['display']}…")
        log.info(f"Запуск: {s['name']}")
        self.proc_mgr.run_strategy(s["file"], on_exit=lambda: self.after(0, self._update_status))
        self.after(1500, self._update_status)

    def _stop_strategy(self) -> None:
        if self.proc_mgr.stop_winws():
            self._info_label.configure(text="Остановлено.")
            log.info("winws.exe остановлен")
        else:
            messagebox.showinfo("Статус", "winws.exe не запущен.")

    # ──────────────────────── Службы ──────────────────────────────

    def _service_bat_path(self) -> Optional[str]:
        path = self.settings.get("program_path", "")
        if not path:
            messagebox.showerror("Ошибка", "Укажите путь к программе в настройках.")
            return None
        bat = os.path.join(path, SERVICE_BAT)
        if not os.path.exists(bat):
            messagebox.showerror("Ошибка", f"Файл не найден:\n{bat}")
            return None
        return bat

    def _install_service(self) -> None:
        s = self._selected_strategy()
        if not s:
            messagebox.showwarning("Предупреждение", "Выберите стратегию.")
            return

        idx = self.detector.get_install_index(s["name"])
        if idx is None:
            messagebox.showerror(
                "Ошибка",
                "Не удалось определить номер стратегии в меню service.bat.",
            )
            return

        uac_note = ("" if is_admin()
                    else "\n\nПоявится запрос прав администратора (UAC) — подтвердите его.")
        if not messagebox.askyesno(
            "Установка службы",
            f"Установить «{s['display']}» как службу автозапуска?" + uac_note,
        ):
            return
        self._exec_service(
            "1", self._svc_append,
            f"Установка службы: {s['display']} (пункт меню 1 → файл №{idx})",
            install_index=idx,
        )

    def _remove_services(self) -> None:
        if messagebox.askyesno("Удаление", "Удалить все установленные службы?"):
            self._exec_service("2", self._svc_append, "Удаление служб")

    def _service_status(self) -> None:
        self._exec_service("3", self._svc_append, "Проверка статуса служб")

    def _update_hosts(self) -> None:
        self._exec_service(
            "8", self._svc_append, "Обновление hosts-файла", timeout=180,
        )

    def _exec_service(
        self, menu_choice: str, append_fn, busy_msg: str,
        install_index: Optional[int] = None, timeout: int = 150,
    ) -> None:
        """Запускает один пункт меню service.bat в фоне и выводит результат."""
        bat = self._service_bat_path()
        if not bat:
            return

        note = ("выполняется…" if is_admin()
                else "подтвердите запрос UAC…")
        append_fn(
            f"\n[{datetime.now():%H:%M:%S}] ── {busy_msg} ──\n"
            f"Запуск с правами администратора, {note}\n\n"
        )
        log.info(f"service.bat: {busy_msg} (пункт {menu_choice}, "
                 f"индекс={install_index})")

        def _worker():
            code, out = self.proc_mgr.run_service_action(
                bat, menu_choice, install_index=install_index, timeout=timeout)
            self.after(0, append_fn, (out.strip() or "(нет вывода)") + "\n")
            self.after(0, self._update_status)
            log.info(f"service.bat завершён: код {code}")

        threading.Thread(target=_worker, daemon=True).start()

    def _svc_append(self, text: str) -> None:
        self._svc_out.configure(state="normal")
        self._svc_out.insert("end", text)
        self._svc_out.see("end")
        self._svc_out.configure(state="disabled")

    # ──────────────────── Конфигурация ────────────────────────────

    def _refresh_config_files(self) -> None:
        if not self.detector.program_path:
            return
        cfg = self.detector.get_config_files()
        self._config_map = {k: v for k, v in cfg.items() if v}

        if not self._config_map:
            self._cfg_combo.configure(values=["Файлы не найдены"])
            self._cfg_var.set("Файлы не найдены")
            return

        vals = list(self._config_map.keys())
        self._cfg_combo.configure(values=vals)
        self._cfg_var.set(vals[0])
        self._on_config_selected(vals[0])

    def _on_config_selected(self, value: str) -> None:
        """Загружает весь файл в редактируемое поле."""
        path = self._config_map.get(value)
        if not path:
            self._cfg_editor.delete("1.0", "end")
            self._cfg_save_btn.configure(state="disabled")
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            self._cfg_editor.delete("1.0", "end")
            self._cfg_editor.insert("1.0", content)
            self._cfg_editor.edit_reset()        # сброс истории undo
            self._cfg_editor.edit_modified(False)
            sz = os.path.getsize(path)
            lines = content.count("\n") + 1
            self._cfg_info.configure(
                text=f"{path}   ·   {sz:,} байт   ·   {lines} строк   ·   "
                     "редактируйте прямо здесь и нажмите «Сохранить» (Ctrl+S)"
            )
            self._cfg_save_btn.configure(state="normal")
        except Exception as e:
            log.error(f"Загрузка конфига: {e}")
            messagebox.showerror("Ошибка", f"Не удалось открыть файл:\n{e}")

    def _save_config(self) -> None:
        path = self._config_map.get(self._cfg_var.get())
        if not path:
            return
        try:
            content = self._cfg_editor.get("1.0", "end-1c")
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            self._cfg_editor.edit_modified(False)
            log.info(f"Файл сохранён: {path}")
            self._cfg_info.configure(text=f"{path}   ·   сохранено ✓")
        except Exception as e:
            log.error(f"Сохранение конфига: {e}")
            messagebox.showerror("Ошибка", f"Не удалось сохранить:\n{e}")

    def _paste_clipboard(self) -> None:
        """Вставляет текст из буфера обмена в позицию курсора редактора."""
        try:
            text = self.clipboard_get()
        except tk.TclError:
            messagebox.showinfo("Буфер обмена", "Буфер обмена пуст или содержит не текст.")
            return
        if not text:
            return
        # Если есть выделение — заменяем его, иначе вставляем по курсору
        try:
            self._cfg_editor.delete(tk.SEL_FIRST, tk.SEL_LAST)
        except tk.TclError:
            pass
        self._cfg_editor.insert(tk.INSERT, text)
        self._cfg_editor.see(tk.INSERT)
        self._cfg_save_btn.configure(state="normal")

    # ──────────────────── Диагностика ─────────────────────────────

    def _run_diag(self) -> None:
        # Пункт 10 «Run Diagnostics» в service.bat. На вопросы про удаление
        # конфликтующих служб и очистку кэша Discord отвечаем «N» автоматически.
        self._exec_service(
            "10", self._diag_append,
            "Диагностика (Run Diagnostics)", timeout=240,
        )

    def _check_secure_dns(self) -> None:
        self._diag_append(f"\n[{datetime.now():%H:%M:%S}] Проверка DoH/Secure DNS…\n")

        def _worker():
            try:
                r = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     "Get-DnsClientDoHServerAddress | Format-List"],
                    capture_output=True, text=True, timeout=15,
                    creationflags=CREATE_NO_WINDOW,
                )
                out = r.stdout.strip() or "DoH-адреса не настроены"
            except Exception as e:
                out = f"Ошибка: {e}"
            self.after(0, self._diag_append, out + "\n")

        threading.Thread(target=_worker, daemon=True).start()

    def _diag_append(self, text: str) -> None:
        self._diag_out.configure(state="normal")
        self._diag_out.insert("end", text)
        self._diag_out.see("end")
        self._diag_out.configure(state="disabled")
        # Дублируем вывод диагностики в журнал с подсветкой
        self._journal_write(text)

    # ──────────────────── Настройки ───────────────────────────────

    def _browse(self) -> None:
        p = filedialog.askdirectory(title="Выберите папку zapret")
        if p:
            self._path_var.set(p)
            self._apply_path()

    @staticmethod
    def _find_in_tree(root: str, name: str, max_depth: int = 2) -> Optional[str]:
        """Ищет файл в корне, затем в типичных подпапках (bin) и неглубоко."""
        direct = os.path.join(root, name)
        if os.path.exists(direct):
            return direct
        for sub in ("bin", "zapret", "win", "x86_64"):
            p = os.path.join(root, sub, name)
            if os.path.exists(p):
                return p
        # Неглубокий рекурсивный поиск как запасной вариант
        name_l = name.lower()
        for dirpath, dirs, files in os.walk(root):
            depth = dirpath[len(root):].count(os.sep)
            if any(f.lower() == name_l for f in files):
                return os.path.join(dirpath, name)
            if depth >= max_depth:
                dirs[:] = []
        return None

    def _apply_path(self) -> None:
        path = self._path_var.get().strip()
        if not path:
            messagebox.showwarning("Предупреждение", "Укажите путь.")
            return
        if not os.path.isdir(path):
            messagebox.showerror("Ошибка", f"Папка не существует:\n{path}")
            return

        warns = []
        if not self._find_in_tree(path, WINWS_EXE):
            warns.append(f"• {WINWS_EXE} не найден (искали и в подпапке bin)")
        if not self._find_in_tree(path, SERVICE_BAT, max_depth=1):
            warns.append(f"• {SERVICE_BAT} не найден")

        if warns and not messagebox.askyesno(
            "Предупреждение",
            "В папке не найдены файлы:\n" + "\n".join(warns) + "\n\nПродолжить?",
        ):
            return

        self.settings.set("program_path", path)
        self.proc_mgr.program_path = path
        self.detector.program_path = path
        self._refresh_strategies()
        self._refresh_config_files()
        log.info(f"Путь установлен: {path}")
        messagebox.showinfo("Готово", f"Путь к программе:\n{path}")

    def _change_theme(self, value: str) -> None:
        ctk.set_appearance_mode(value)
        self.settings.set("theme", value)
        # CTk-виджеты переключаются сами; tk.Text-консоли красим вручную
        self.after(60, self._apply_console_theme)
        self.after(60, self._update_status)

    def _toggle_autostart(self) -> None:
        if self._autostart_var.get():
            if not AutostartManager.enable(sys.executable):
                self._autostart_var.set(False)
                messagebox.showerror("Ошибка", "Не удалось добавить в автозапуск.")
                return
        else:
            AutostartManager.disable()
        self.settings.set("autostart", self._autostart_var.get())

    def _export(self) -> None:
        p = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
            title="Экспорт настроек",
        )
        if p and self.settings.export_to(p):
            messagebox.showinfo("Экспорт", f"Настройки сохранены:\n{p}")

    def _import_cfg(self) -> None:
        p = filedialog.askopenfilename(
            filetypes=[("JSON", "*.json")],
            title="Импорт настроек",
        )
        if p and self.settings.import_from(p):
            messagebox.showinfo("Импорт", "Настройки импортированы. Перезапустите приложение.")

    def _reset_cfg(self) -> None:
        if messagebox.askyesno("Сброс", "Сбросить все настройки к значениям по умолчанию?"):
            self.settings.reset()
            messagebox.showinfo("Сброс", "Настройки сброшены. Перезапустите приложение.")

    # ──────────────────── Статус-поллинг ─────────────────────────

    def _start_poll(self) -> None:
        self._update_status()
        self._poll_id = self.after(3000, self._start_poll)

    def _update_status(self) -> None:
        running = self.proc_mgr.is_winws_running()
        if running:
            self._dot.configure(text_color=pick(GREEN))
            self._hdr_status.configure(text="Активен", text_color=pick(GREEN))
            self._start_btn.configure(state="disabled")
            self._stop_btn.configure(state="normal")
        else:
            self._dot.configure(text_color=pick(TXT_FAINT))
            self._hdr_status.configure(text="Не активен", text_color=pick(TXT_DIM))
            self._start_btn.configure(state="normal")
            self._stop_btn.configure(state="disabled")

    # ──────────────────── Системный трей ──────────────────────────

    def _setup_tray(self) -> None:
        try:
            img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            d.ellipse([4, 4, 60, 60], fill=(45, 125, 58))
            d.text((18, 16), "Z", fill="white")

            menu = pystray.Menu(
                pystray.MenuItem("Открыть", lambda: self.after(0, self._show_window), default=True),
                pystray.MenuItem("Запустить", lambda: self.after(0, self._start_strategy)),
                pystray.MenuItem("Остановить", lambda: self.after(0, self._stop_strategy)),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Выход", lambda: self.after(0, self._quit)),
            )
            self._tray = pystray.Icon(APP_NAME, img, APP_NAME, menu)
            threading.Thread(target=self._tray.run, daemon=True).start()
        except Exception as e:
            log.warning(f"Трей недоступен: {e}")

    def _show_window(self) -> None:
        self.deiconify()
        self.lift()
        self.focus_force()

    # ──────────────────── Утилиты ─────────────────────────────────

    @staticmethod
    def _clear_text(widget: tk.Text) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.configure(state="disabled")

    # ──────────────────── Обновление zapret ───────────────────────

    def _set_update_status(self, text: str, color) -> None:
        try:
            self._upd_status.configure(text=text, text_color=color)
        except Exception:
            pass

    def _check_zapret_update(self) -> None:
        """Ручная проверка версии zapret (кнопка в настройках)."""
        path = self.settings.get("program_path", "")
        if not path:
            messagebox.showwarning(
                "Путь не задан", "Сначала укажите путь к папке zapret.")
            return
        self._upd_check_btn.configure(state="disabled", text="Проверка…")
        self._set_update_status("Проверка обновлений…", TXT_DIM)
        UpdateChecker(
            path,
            callback=lambda res: self.after(0, self._on_update_result, res, True),
        ).check_async()

    def _on_update_result(self, res: dict, manual: bool = False) -> None:
        """Обрабатывает результат проверки версии zapret.
        manual=True — вызвано кнопкой (показываем диалоги для всех статусов)."""
        try:
            self._upd_check_btn.configure(
                state="normal", text="🔍  Проверить обновление")
        except Exception:
            pass

        status = res.get("status")
        local  = res.get("local")  or "неизвестно"
        latest = res.get("latest") or "неизвестно"

        if status == "current":
            self._update_download_url = None
            self._upd_apply_btn.configure(state="disabled")
            msg = f"У вас уже установлена последняя версия zapret ({local})."
            self._set_update_status("✔  " + msg, GREEN)
            log.info(msg)
            if manual:
                messagebox.showinfo("Обновление не требуется", msg)

        elif status == "outdated":
            self._update_download_url = res.get("download_url")
            can_apply = bool(self._update_download_url)
            self._upd_apply_btn.configure(
                state="normal" if can_apply else "disabled")
            msg = f"Доступна новая версия zapret: {latest}  (установлена: {local})"
            self._set_update_status("⬆  " + msg, ACCENT)
            log.info(f"Доступно обновление zapret: {local} → {latest}")
            tail = ("\n\nНажмите «Обновить zapret», чтобы установить."
                    if can_apply else
                    "\n\nАвто-загрузка недоступна — обновите вручную со страницы релизов.")
            messagebox.showinfo("Доступно обновление", msg + tail)

        elif status == "unknown":
            self._update_download_url = None
            self._upd_apply_btn.configure(state="disabled")
            msg = (f"Не удалось точно определить версию "
                   f"(локальная: {local}, доступная: {latest}). "
                   f"Обновление не предлагается.")
            self._set_update_status("ℹ  " + msg, TXT_DIM)
            log.warning("Версию zapret определить не удалось — обновление не предлагается")
            if manual:
                messagebox.showinfo("Проверка обновления", msg)

        else:  # error
            self._update_download_url = None
            self._upd_apply_btn.configure(state="disabled")
            err = res.get("error", "неизвестная ошибка")
            msg = f"Не удалось проверить обновления: {err}"
            self._set_update_status("✖  " + msg, RED)
            log.warning(msg)
            if manual:
                messagebox.showerror("Ошибка проверки", msg)

    def _apply_zapret_update(self) -> None:
        """Скачивает и устанавливает обновление zapret."""
        path = self.settings.get("program_path", "")
        url  = self._update_download_url
        if not path or not url:
            messagebox.showwarning(
                "Обновление недоступно",
                "Сначала выполните проверку обновления.")
            return
        if not messagebox.askyesno(
            "Обновление zapret",
            "Сейчас будет скачана и установлена новая версия zapret.\n"
            "Активные процессы winws.exe будут остановлены, "
            "пользовательские списки доменов сохранятся.\n\n"
            "Продолжить?",
        ):
            return

        self._upd_apply_btn.configure(state="disabled", text="Обновление…")
        self._upd_check_btn.configure(state="disabled")
        self._set_update_status("Запуск обновления…", ACCENT)

        def work():
            ok, msg = ZapretUpdater(path, url).run(
                progress=lambda m: self.after(0, self._set_update_status, m, ACCENT)
            )
            self.after(0, self._finish_update, ok, msg)
        threading.Thread(target=work, daemon=True).start()

    def _finish_update(self, ok: bool, msg: str) -> None:
        self._upd_check_btn.configure(state="normal")
        self._upd_apply_btn.configure(state="disabled", text="⬇  Обновить zapret")
        if ok:
            self._update_download_url = None
            self._set_update_status("✔  " + msg, GREEN)
            log.info(msg)
            messagebox.showinfo("Готово", msg)
            self._refresh_strategies()
            self._refresh_config_files()
        else:
            self._set_update_status("✖  " + msg, RED)
            log.error(msg)
            messagebox.showerror("Ошибка обновления", msg)

    def _on_close(self) -> None:
        if self.settings.get("minimize_to_tray", True) and TRAY_AVAILABLE and self._tray:
            self.withdraw()
            log.info("Свёрнуто в трей")
        else:
            self._quit()

    def _quit(self) -> None:
        log.info("Завершение работы")
        self.settings.set("window_width",  self.winfo_width())
        self.settings.set("window_height", self.winfo_height())

        if self._poll_id:
            self.after_cancel(self._poll_id)
        if self._tray:
            try:
                self._tray.stop()
            except Exception:
                pass
        self.destroy()


# ════════════════════════════════════════════════════════════════
# Entry point
# ════════════════════════════════════════════════════════════════

def main() -> None:
    if sys.version_info < (3, 8):
        print("Требуется Python 3.8 или новее.")
        sys.exit(1)

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    app = ZapretGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
