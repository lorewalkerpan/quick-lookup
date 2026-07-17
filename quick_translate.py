"""Quick Lookup: a lightweight, open-source selection dictionary for Windows.

Select English text or double-click a word to show a floating Chinese
translation. For single words it also shows pronunciation, definitions and
examples from a configurable dictionary provider.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from collections import OrderedDict
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import queue
import re
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
import urllib.parse
import urllib.request
import sys
import winreg

from pynput import keyboard, mouse
import pythoncom
from win32com.client import Dispatch

__version__ = "0.3.3"

POPUP_SECONDS = 7
COPY_TIMEOUT_SECONDS = 0.7
SELECTION_SETTLE_SECONDS = 0.18
DOUBLE_CLICK_SETTLE_SECONDS = 0.12
DOUBLE_CLICK_RADIUS = 20
DRAG_SELECTION_DISTANCE = 5
MAX_TEXT_LENGTH = 180
CACHE_SIZE = 200
SOURCE_DIR = Path(__file__).resolve().parent
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", SOURCE_DIR))
USER_DATA_DIR = (Path(os.environ.get("LOCALAPPDATA", SOURCE_DIR)) / "QuickLookup") if getattr(sys, "frozen", False) else SOURCE_DIR
CONFIG_FILE = RESOURCE_DIR / "quick_lookup_config.json"
LOCAL_CONFIG_FILE = USER_DATA_DIR / "quick_lookup_config.local.json"
LOCAL_DICTIONARY_FILE = RESOURCE_DIR / "offline_dictionary.json"
THEMES_FILE = RESOURCE_DIR / "themes.json"
LOG_FILE = USER_DATA_DIR / "quick_translate.log"

DEFAULT_CONFIG = {
    "popup_position": "selection_right",
    "translation_mode": "api",
    "theme": "dark",
    "theme_overrides": {},
    "font_family": "Microsoft YaHei UI",
    "font_size": 11,
    "speech_rate": 175,
    "run_at_startup": False,
}
COLOR_KEYS = ("popup_background", "title_text_color", "translation_text_color", "definition_text_color", "secondary_text_color", "muted_text_color")

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
CF_UNICODETEXT = 13
VK_CONTROL = 0x11
VK_C = 0x43
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
ERROR_ALREADY_EXISTS = 183


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG), ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t),
    ]


class INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("data", INPUTUNION)]


kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = wintypes.LPVOID
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.restype = wintypes.BOOL
kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT


@dataclass
class DictionaryEntry:
    word: str
    chinese: str
    ipa: str = ""
    part_of_speech: str = ""
    definitions: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    provider: str = "翻译"
    notice: str = ""


class SpeechService:
    """A small background queue around Windows SAPI; no network request is made."""
    def __init__(self, rate: int) -> None:
        self.rate = rate
        self.requests: queue.Queue[str | None] = queue.Queue()
        self.worker = threading.Thread(target=self.run, name="quick-lookup-speech", daemon=True)
        self.worker.start()

    def speak(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        # Keep only the newest request when the button is clicked repeatedly.
        while True:
            try:
                self.requests.get_nowait()
            except queue.Empty:
                break
        self.requests.put(text)
        log("speech queued")

    def run(self) -> None:
        voice = None
        try:
            pythoncom.CoInitialize()
            voice = Dispatch("SAPI.SpVoice")
            while True:
                text = self.requests.get()
                if text is None:
                    return
                # SVSFlagsAsync (1) + SVSFPurgeBeforeSpeak (2): a new click
                # immediately interrupts the previous utterance and starts over.
                voice.Rate = max(-10, min(10, round((self.rate - 175) / 12.5)))
                voice.Speak(text, 3)
        except Exception as error:
            log(f"speech engine failed: {type(error).__name__}: {error}")
        finally:
            if voice is not None:
                try:
                    voice.Speak("", 3)
                except Exception:
                    pass
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass

    def stop(self) -> None:
        self.requests.put(None)


def log(message: str) -> None:
    """A small local diagnostic log; never write selected text to it."""
    try:
        USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as file:
            file.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    except OSError:
        pass


def load_themes() -> dict[str, dict[str, str]]:
    try:
        themes = json.loads(THEMES_FILE.read_text(encoding="utf-8"))
        if isinstance(themes, dict):
            return {
                name: values for name, values in themes.items()
                if isinstance(name, str) and isinstance(values, dict)
                and all(isinstance(values.get(key), str) and re.fullmatch(r"#[0-9a-fA-F]{6}", values[key]) for key in COLOR_KEYS)
            }
    except (OSError, json.JSONDecodeError) as error:
        log(f"theme load failed: {type(error).__name__}")
    return {}


def load_config() -> dict[str, object]:
    config = DEFAULT_CONFIG.copy()
    themes = load_themes()
    for config_path in (CONFIG_FILE, LOCAL_CONFIG_FILE):
        try:
            saved = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                for key, value in saved.items():
                    if key in config and isinstance(value, type(config[key])):
                        config[key] = value
        except (OSError, json.JSONDecodeError):
            pass
    if config["popup_position"] not in {"selection_right", "center"}:
        config["popup_position"] = DEFAULT_CONFIG["popup_position"]
    if config["translation_mode"] not in {"api", "smart", "exact", "word_by_word"}:
        config["translation_mode"] = DEFAULT_CONFIG["translation_mode"]
    if config["theme"] not in themes:
        config["theme"] = "dark" if "dark" in themes else next(iter(themes), "")
    config.update(themes.get(config["theme"], {}))
    overrides = config["theme_overrides"] if isinstance(config["theme_overrides"], dict) else {}
    config["theme_overrides"] = overrides
    for color_key, color_value in overrides.items():
        if color_key in COLOR_KEYS and isinstance(color_value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", color_value):
            config[color_key] = color_value
    if not 8 <= config["font_size"] <= 24:
        config["font_size"] = DEFAULT_CONFIG["font_size"]
    if not 100 <= config["speech_rate"] <= 250:
        config["speech_rate"] = DEFAULT_CONFIG["speech_rate"]
    return config


def save_config(config: dict[str, object]) -> None:
    try:
        USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        persisted = {key: value for key, value in config.items() if key not in COLOR_KEYS}
        LOCAL_CONFIG_FILE.write_text(json.dumps(persisted, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as error:
        log(f"could not save config: {error}")


def startup_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}"'
    executable = Path(sys.executable).resolve()
    if executable.name.lower() == "python.exe":
        pythonw = executable.with_name("pythonw.exe")
        if pythonw.exists():
            executable = pythonw
    return f'"{executable}" "{Path(__file__).resolve()}"'


def set_run_at_startup(enabled: bool) -> None:
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, "QuickLookup", 0, winreg.REG_SZ, startup_command())
        else:
            try:
                winreg.DeleteValue(key, "QuickLookup")
            except FileNotFoundError:
                pass


def is_run_at_startup_enabled() -> bool:
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, "QuickLookup")
        return isinstance(value, str) and bool(value)
    except FileNotFoundError:
        return False


def clipboard_text() -> str | None:
    for _ in range(8):
        if user32.OpenClipboard(None):
            try:
                handle = user32.GetClipboardData(CF_UNICODETEXT)
                if not handle:
                    return None
                pointer = kernel32.GlobalLock(handle)
                if not pointer:
                    return None
                try:
                    return ctypes.wstring_at(pointer)
                finally:
                    kernel32.GlobalUnlock(handle)
            finally:
                user32.CloseClipboard()
        time.sleep(0.02)
    return None


def send_copy_shortcut() -> None:
    inputs = (INPUT * 4)(
        INPUT(INPUT_KEYBOARD, INPUTUNION(ki=KEYBDINPUT(VK_CONTROL, 0, 0, 0, 0))),
        INPUT(INPUT_KEYBOARD, INPUTUNION(ki=KEYBDINPUT(VK_C, 0, 0, 0, 0))),
        INPUT(INPUT_KEYBOARD, INPUTUNION(ki=KEYBDINPUT(VK_C, 0, KEYEVENTF_KEYUP, 0, 0))),
        INPUT(INPUT_KEYBOARD, INPUTUNION(ki=KEYBDINPUT(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0, 0))),
    )
    if user32.SendInput(4, inputs, ctypes.sizeof(INPUT)) != 4:
        raise ctypes.WinError(ctypes.get_last_error())


def normalize_english(value: str) -> str | None:
    value = " ".join(value.split()).strip()
    if len(value) > MAX_TEXT_LENGTH:
        return None
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9 '\u2019\-.,;:!?()]{0,179}", value):
        return value
    return None


def load_local_dictionary() -> dict[str, dict[str, object]]:
    """Load the editable bundled dictionary. No network is ever used."""
    try:
        data = json.loads(LOCAL_DICTIONARY_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(key).lower(): value for key, value in data.items() if isinstance(value, dict)}
    except (OSError, json.JSONDecodeError) as error:
        log(f"local dictionary load failed: {type(error).__name__}")
    return {}


def request_json(url: str) -> object:
    request = urllib.request.Request(url, headers={"User-Agent": "QuickLookup/0.2"})
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def api_translate_to_chinese(source: str) -> str:
    query = urllib.parse.urlencode({"client": "gtx", "sl": "en", "tl": "zh-CN", "dt": "t", "q": source})
    data = request_json("https://translate.googleapis.com/translate_a/single?" + query)
    return "".join(part[0] for part in data[0] if part and part[0]).strip()  # type: ignore[index]


def api_dictionary(word: str) -> tuple[str, str, list[str], list[str]]:
    data = request_json("https://api.dictionaryapi.dev/api/v2/entries/en/" + urllib.parse.quote(word))
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        raise ValueError("unexpected dictionary response")
    item = data[0]
    ipa = item.get("phonetic", "") if isinstance(item.get("phonetic"), str) else ""
    if not ipa:
        for phonetic in item.get("phonetics", []):
            if isinstance(phonetic, dict) and isinstance(phonetic.get("text"), str):
                ipa = phonetic["text"]
                break
    part, definitions, examples = "", [], []
    for meaning in item.get("meanings", [])[:2]:
        if not isinstance(meaning, dict):
            continue
        if not part and isinstance(meaning.get("partOfSpeech"), str):
            part = meaning["partOfSpeech"]
        for definition in meaning.get("definitions", [])[:2]:
            if not isinstance(definition, dict):
                continue
            if isinstance(definition.get("definition"), str):
                definitions.append(definition["definition"])
            if isinstance(definition.get("example"), str):
                examples.append(definition["example"])
    return ipa, part, definitions[:3], examples[:1]


class QuickLookupApp:
    def __init__(self) -> None:
        self.mutex = kernel32.CreateMutexW(None, False, "Local\\QuickLookupSelectionPopup")
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            raise RuntimeError("Quick Lookup 已在运行")
        self.themes = load_themes()
        self.config = load_config()
        self.local_dictionary = load_local_dictionary()
        self.speech = SpeechService(int(self.config["speech_rate"]))
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.report_callback_exception = self.report_tk_error
        self.trigger_queue: queue.Queue[tuple[float, int, int, str]] = queue.Queue()
        self.ui_queue: queue.Queue[tuple] = queue.Queue()
        self.pending: list[tuple[float, int, int, str]] = []
        self.cache: OrderedDict[str, DictionaryEntry] = OrderedDict()
        self.popup: tk.Toplevel | None = None
        self.settings_window: tk.Toplevel | None = None
        self.hide_job: str | None = None
        self.mouse_down: tuple[int, int, float] | None = None
        self.last_left_down: tuple[int, int, float] | None = None
        self.request_id, self.running = 0, True
        self.mouse_listener = mouse.Listener(on_click=self.on_click)
        self.hotkeys = keyboard.GlobalHotKeys({
            "<ctrl>+<alt>+q": self.request_quit,
            "<ctrl>+<alt>+p": self.request_position_cycle,
            "<ctrl>+<alt>+s": self.request_settings,
        })
        self.mouse_listener.start()
        self.hotkeys.start()
        self.root.after(20, self.process_queues)
        self.root.after(250, self.show_startup_notice)
        if self.config["run_at_startup"]:
            try:
                set_run_at_startup(True)
            except OSError as error:
                log(f"startup registration failed: {error}")
        log("started: listeners and popup loop are active")

    def report_tk_error(self, exc_type, exc_value, _traceback) -> None:
        log(f"Tk error: {exc_type.__name__}: {exc_value}")

    def on_click(self, x: int, y: int, button: mouse.Button, pressed: bool) -> None:
        if button != mouse.Button.left:
            return
        now = time.monotonic()
        if pressed:
            if self.last_left_down:
                old_x, old_y, old_time = self.last_left_down
                if now - old_time < 0.60 and abs(x - old_x) < DOUBLE_CLICK_RADIUS and abs(y - old_y) < DOUBLE_CLICK_RADIUS:
                    self.trigger_queue.put((now + DOUBLE_CLICK_SETTLE_SECONDS, x, y, "double-click"))
            self.last_left_down, self.mouse_down = (x, y, now), (x, y, now)
        elif self.mouse_down:
            down_x, down_y, _ = self.mouse_down
            self.mouse_down = None
            if abs(x - down_x) >= DRAG_SELECTION_DISTANCE or abs(y - down_y) >= DRAG_SELECTION_DISTANCE:
                self.trigger_queue.put((now + SELECTION_SETTLE_SECONDS, x, y, "selection"))

    def request_quit(self) -> None:
        self.ui_queue.put(("quit",))

    def request_position_cycle(self) -> None:
        self.ui_queue.put(("cycle_position",))

    def request_settings(self) -> None:
        self.ui_queue.put(("settings",))

    def process_queues(self) -> None:
        now = time.monotonic()
        while True:
            try:
                self.pending.append(self.trigger_queue.get_nowait())
            except queue.Empty:
                break
        keep: list[tuple[float, int, int, str]] = []
        for due, x, y, reason in self.pending:
            if due > now:
                keep.append((due, x, y, reason))
            else:
                self.start_lookup(x, y, reason)
        self.pending = keep
        while True:
            try:
                event = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            if event[0] == "loading":
                _, request_id, x, y, source = event
                if request_id == self.request_id:
                    self.show_popup(x, y, DictionaryEntry(source, "正在查询…", provider="Quick Lookup"))
            elif event[0] == "result":
                _, request_id, x, y, entry = event
                if request_id == self.request_id:
                    self.show_popup(x, y, entry)
                    self.hide_job = self.root.after(int(POPUP_SECONDS * 1000), self.hide_popup)
            elif event[0] == "cycle_position":
                self.cycle_position()
            elif event[0] == "settings":
                self.show_settings()
            elif event[0] == "quit":
                self.quit()
                return
        if self.running:
            self.root.after(20, self.process_queues)

    def start_lookup(self, x: int, y: int, reason: str) -> None:
        self.request_id += 1
        request_id = self.request_id
        threading.Thread(target=self.copy_and_lookup, args=(request_id, x, y, reason), daemon=True).start()

    def copy_and_lookup(self, request_id: int, x: int, y: int, reason: str) -> None:
        try:
            previous_clipboard = user32.GetClipboardSequenceNumber()
            send_copy_shortcut()
            deadline = time.monotonic() + COPY_TIMEOUT_SECONDS
            while time.monotonic() < deadline and user32.GetClipboardSequenceNumber() == previous_clipboard:
                time.sleep(0.02)
            # Never reuse a prior clipboard value.  If Ctrl+C did not produce a
            # new clipboard sequence, the foreground drag/click was not a text
            # selection and showing the old translation would be misleading.
            if user32.GetClipboardSequenceNumber() == previous_clipboard:
                log(f"{reason}: ignored because no fresh text was copied")
                return
            source = normalize_english(clipboard_text() or "")
            if not source:
                return
            self.ui_queue.put(("loading", request_id, x, y, source))
            entry = self.lookup(source)
            self.ui_queue.put(("result", request_id, x, y, entry))
            log(f"{reason}: lookup completed")
        except Exception as error:
            log(f"{reason}: lookup error: {type(error).__name__}: {error}")

    def lookup(self, source: str) -> DictionaryEntry:
        key = f"{self.config['translation_mode']}:{source.lower()}"
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        normalized = source.lower().strip(".,;:!?()")
        raw = self.local_dictionary.get(normalized)
        mode = self.config["translation_mode"]
        if mode == "api":
            entry = self.api_lookup(source, normalized, raw)
        elif raw and mode != "word_by_word":
            entry = self.entry_from_raw(source, raw)
        elif raw and " " not in normalized:
            entry = self.entry_from_raw(source, raw)
        elif mode in {"smart", "word_by_word"}:
            parts = [self.local_dictionary.get(part.lower()) for part in normalized.split()]
            if len(parts) > 1 and all(parts):
                chinese = " ".join(str(part.get("zh", "")) for part in parts if part)
                entry = DictionaryEntry(source, chinese, provider="本地词库（逐词）")
            else:
                entry = self.not_found_entry(source)
        else:
            entry = self.not_found_entry(source)
        self.cache[key] = entry
        if len(self.cache) > CACHE_SIZE:
            self.cache.popitem(last=False)
        return entry

    def api_lookup(self, source: str, normalized: str, local_raw: dict[str, object] | None) -> DictionaryEntry:
        """Default online mode; falls back to local data if a service is unavailable."""
        try:
            entry = DictionaryEntry(source, api_translate_to_chinese(source), provider="在线翻译 API")
            if " " not in normalized and re.fullmatch(r"[A-Za-z'-]+", normalized):
                try:
                    entry.ipa, entry.part_of_speech, entry.definitions, entry.examples = api_dictionary(normalized)
                    entry.provider = "在线翻译 API + 词典 API"
                except Exception as error:
                    log(f"online dictionary failed: {type(error).__name__}")
                    entry.notice = "词典释义暂不可用"
            return entry
        except Exception as error:
            log(f"online translation failed: {type(error).__name__}")
            if local_raw:
                fallback = self.entry_from_raw(source, local_raw)
                fallback.notice = "在线服务不可用，已切换本地词库"
                return fallback
            fallback = self.not_found_entry(source)
            fallback.notice = "在线服务不可用，且本地词库未收录"
            return fallback

    @staticmethod
    def entry_from_raw(source: str, raw: dict[str, object]) -> DictionaryEntry:
        definitions = raw.get("definitions", [])
        examples = raw.get("examples", [])
        return DictionaryEntry(
            word=source,
            chinese=str(raw.get("zh", "")),
            ipa=str(raw.get("ipa", "")),
            part_of_speech=str(raw.get("part_of_speech", "")),
            definitions=[str(item) for item in definitions if isinstance(item, str)][:3] if isinstance(definitions, list) else [],
            examples=[str(item) for item in examples if isinstance(item, str)][:1] if isinstance(examples, list) else [],
            provider="本地词库（离线）",
        )

    @staticmethod
    def not_found_entry(source: str) -> DictionaryEntry:
        return DictionaryEntry(
            source,
            "本地词库未收录",
            provider="离线模式",
            notice="可在 offline_dictionary.json 添加这个单词或短语",
        )

    def show_popup(self, x: int, y: int, entry: DictionaryEntry) -> None:
        self.hide_popup()
        popup = tk.Toplevel(self.root)
        self.popup = popup
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        popup.configure(bg=self.config["popup_background"])
        frame = tk.Frame(popup, bg=self.config["popup_background"], padx=14, pady=11)
        frame.pack()
        title = entry.word + (f"   /{entry.ipa}/" if entry.ipa else "")
        self.add_label(frame, title, self.config["title_text_color"], self.config["font_size"] + 1, "bold")
        if entry.part_of_speech:
            self.add_label(frame, entry.part_of_speech, self.config["translation_text_color"], self.config["font_size"] - 2)
        self.add_label(frame, entry.chinese, self.config["translation_text_color"], self.config["font_size"])
        for definition in entry.definitions[:3]:
            self.add_label(frame, "• " + definition, self.config["definition_text_color"], self.config["font_size"] - 1)
        for example in entry.examples[:1]:
            self.add_label(frame, "例：" + example, self.config["secondary_text_color"], self.config["font_size"] - 2)
        if entry.notice:
            self.add_label(frame, entry.notice, self.config["secondary_text_color"], self.config["font_size"] - 2)
        tk.Button(
            frame, text="🔊 朗读", command=lambda: self.speech.speak(entry.word),
            bg=self.config["translation_text_color"], fg=self.config["popup_background"],
            activebackground=self.config["title_text_color"], relief="flat", padx=11, pady=3,
            font=(self.config["font_family"], max(8, self.config["font_size"] - 2)),
        ).pack(anchor="w", pady=(2, 5))
        self.add_label(frame, f"{entry.provider} · Ctrl+Alt+P 位置 · Ctrl+Alt+S 设置", self.config["muted_text_color"], self.config["font_size"] - 3)
        popup.update_idletasks()
        width, height = popup.winfo_width(), popup.winfo_height()
        screen_width, screen_height = popup.winfo_screenwidth(), popup.winfo_screenheight()
        if self.config["popup_position"] == "center":
            left, top = (screen_width - width) // 2, (screen_height - height) // 2
        else:
            left = max(0, min(x + 14, screen_width - width - 8))
            top = max(0, min(y + 20, screen_height - height - 8))
        popup.geometry(f"+{left}+{top}")
        popup.lift()

    def add_label(self, parent: tk.Widget, text: str, color: str, size: int, weight: str = "normal") -> None:
        tk.Label(parent, text=text, bg=self.config["popup_background"], fg=color, font=(self.config["font_family"], max(8, size), weight),
                 justify="left", anchor="w", wraplength=430).pack(anchor="w", pady=(0, 3))

    def hide_popup(self) -> None:
        if self.hide_job:
            self.root.after_cancel(self.hide_job)
            self.hide_job = None
        if self.popup:
            self.popup.destroy()
            self.popup = None

    def cycle_position(self) -> None:
        self.config["popup_position"] = "center" if self.config["popup_position"] == "selection_right" else "selection_right"
        save_config(self.config)
        position = "屏幕居中" if self.config["popup_position"] == "center" else "划词右侧"
        self.show_popup(0, 0, DictionaryEntry("浮窗位置", position, provider="Quick Lookup"))
        self.hide_job = self.root.after(2500, self.hide_popup)

    def show_startup_settings_legacy(self) -> None:
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.lift()
            self.settings_window.focus_force()
            return
        window = tk.Toplevel(self.root)
        self.settings_window = window
        window.title("Quick Lookup 设置")
        window.attributes("-topmost", True)
        window.configure(bg=self.config["popup_background"])
        window.resizable(False, False)
        frame = tk.Frame(window, bg=self.config["popup_background"], padx=20, pady=16)
        frame.pack()
        self.add_label(frame, "Quick Lookup 设置", self.config["title_text_color"], self.config["font_size"] + 1, "bold")
        startup_enabled = tk.BooleanVar(value=is_run_at_startup_enabled())
        tk.Checkbutton(
            frame,
            text="开机时自动启动 Quick Lookup",
            variable=startup_enabled,
            bg=self.config["popup_background"],
            fg=self.config["definition_text_color"],
            activebackground=self.config["popup_background"],
            activeforeground=self.config["title_text_color"],
            selectcolor=self.config["popup_background"],
            font=(self.config["font_family"], self.config["font_size"]),
        ).pack(anchor="w", pady=(10, 12))

        def save_settings() -> None:
            enabled = startup_enabled.get()
            try:
                set_run_at_startup(enabled)
                self.config["run_at_startup"] = enabled
                save_config(self.config)
                messagebox.showinfo("Quick Lookup", "已启用开机启动" if enabled else "已关闭开机启动", parent=window)
                window.destroy()
            except OSError as error:
                messagebox.showerror("Quick Lookup", f"无法更新开机启动设置：{error}", parent=window)

        button = tk.Button(
            frame, text="保存", command=save_settings,
            bg=self.config["translation_text_color"], fg=self.config["popup_background"],
            activebackground=self.config["title_text_color"], relief="flat", padx=16, pady=5,
        )
        button.pack(anchor="e")
        window.protocol("WM_DELETE_WINDOW", window.destroy)

    def show_settings(self) -> None:
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.lift()
            self.settings_window.focus_force()
            return

        window = tk.Toplevel(self.root)
        self.settings_window = window
        window.title("Quick Lookup 设置")
        window.attributes("-topmost", True)
        window.configure(bg=self.config["popup_background"])
        window.resizable(False, False)
        frame = tk.Frame(window, bg=self.config["popup_background"], padx=20, pady=16)
        frame.pack()

        title = tk.Label(
            frame, text="Quick Lookup 设置", bg=self.config["popup_background"], fg=self.config["title_text_color"],
            font=(self.config["font_family"], self.config["font_size"] + 2, "bold"), anchor="w",
        )
        title.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))

        position_var = tk.StringVar(value=self.config["popup_position"])
        mode_var = tk.StringVar(value=self.config["translation_mode"])
        theme_var = tk.StringVar(value=self.config["theme"])
        font_var = tk.StringVar(value=self.config["font_family"])
        font_size_var = tk.StringVar(value=str(self.config["font_size"]))
        speech_rate_var = tk.StringVar(value=str(self.config["speech_rate"]))
        startup_var = tk.BooleanVar(value=is_run_at_startup_enabled())
        color_vars = {key: tk.StringVar(value=self.config[key]) for key in COLOR_KEYS}

        def label(row: int, text: str) -> None:
            tk.Label(
                frame, text=text, bg=self.config["popup_background"], fg=self.config["definition_text_color"],
                font=(self.config["font_family"], self.config["font_size"]), anchor="w",
            ).grid(row=row, column=0, sticky="w", padx=(0, 16), pady=4)

        def combo(row: int, variable: tk.StringVar, values: tuple[str, ...]) -> ttk.Combobox:
            control = ttk.Combobox(frame, textvariable=variable, values=values, state="readonly", width=25)
            control.grid(row=row, column=1, sticky="ew", pady=4)
            return control

        label(1, "浮窗位置")
        combo(1, position_var, ("selection_right", "center"))
        label(2, "翻译模式")
        combo(2, mode_var, ("api", "smart", "exact", "word_by_word"))
        label(3, "主题")
        theme_control = combo(3, theme_var, tuple(self.themes) or ("dark",))
        label(4, "字体")
        font_control = ttk.Combobox(frame, textvariable=font_var, values=("Microsoft YaHei UI", "Segoe UI", "Consolas", "Arial"), width=25)
        font_control.grid(row=4, column=1, sticky="ew", pady=4)
        label(5, "字号")
        tk.Spinbox(frame, from_=8, to=24, textvariable=font_size_var, width=8).grid(row=5, column=1, sticky="w", pady=4)
        label(6, "朗读速度")
        tk.Spinbox(frame, from_=100, to=250, increment=5, textvariable=speech_rate_var, width=8).grid(row=6, column=1, sticky="w", pady=4)

        tk.Checkbutton(
            frame, text="开机时自动启动 Quick Lookup", variable=startup_var,
            bg=self.config["popup_background"], fg=self.config["definition_text_color"],
            activebackground=self.config["popup_background"], activeforeground=self.config["title_text_color"],
            selectcolor=self.config["popup_background"], font=(self.config["font_family"], self.config["font_size"]),
        ).grid(row=7, column=0, columnspan=2, sticky="w", pady=(8, 10))

        tk.Label(
            frame, text="颜色（#RRGGBB）", bg=self.config["popup_background"], fg=self.config["title_text_color"],
            font=(self.config["font_family"], self.config["font_size"], "bold"), anchor="w",
        ).grid(row=8, column=0, columnspan=2, sticky="w", pady=(2, 4))
        color_labels = {
            "popup_background": "浮窗背景", "title_text_color": "标题文字", "translation_text_color": "译文文字",
            "definition_text_color": "释义文字", "secondary_text_color": "示例/提示", "muted_text_color": "页脚文字",
        }
        for offset, key in enumerate(COLOR_KEYS, start=9):
            label(offset, color_labels[key])
            tk.Entry(frame, textvariable=color_vars[key], width=28).grid(row=offset, column=1, sticky="ew", pady=3)

        def apply_theme(_event: object = None) -> None:
            selected = self.themes.get(theme_var.get())
            if selected:
                for key in COLOR_KEYS:
                    color_vars[key].set(selected[key])

        theme_control.bind("<<ComboboxSelected>>", apply_theme)

        def save_settings() -> None:
            try:
                selected_theme = theme_var.get()
                if selected_theme not in self.themes:
                    raise ValueError("请选择有效主题")
                selected_size = int(font_size_var.get())
                if not 8 <= selected_size <= 24:
                    raise ValueError("字号应在 8 到 24 之间")
                selected_speech_rate = int(speech_rate_var.get())
                if not 100 <= selected_speech_rate <= 250:
                    raise ValueError("朗读速度应在 100 到 250 之间")
                selected_colors = {key: color_vars[key].get().upper() for key in COLOR_KEYS}
                if not all(re.fullmatch(r"#[0-9A-F]{6}", value) for value in selected_colors.values()):
                    raise ValueError("颜色必须是 #RRGGBB 格式")
                theme_colors = self.themes[selected_theme]
                self.config.update({
                    "popup_position": position_var.get(), "translation_mode": mode_var.get(),
                    "theme": selected_theme, "font_family": font_var.get().strip() or "Microsoft YaHei UI",
                    "font_size": selected_size, "speech_rate": selected_speech_rate, "run_at_startup": startup_var.get(),
                    "theme_overrides": {key: value for key, value in selected_colors.items() if value != theme_colors[key].upper()},
                })
                self.config.update(selected_colors)
                self.speech.rate = selected_speech_rate
                set_run_at_startup(startup_var.get())
                save_config(self.config)
                messagebox.showinfo("Quick Lookup", "设置已保存", parent=window)
                window.destroy()
            except (OSError, ValueError) as error:
                messagebox.showerror("Quick Lookup", f"无法保存设置：{error}", parent=window)

        buttons = tk.Frame(frame, bg=self.config["popup_background"])
        buttons.grid(row=15, column=0, columnspan=2, sticky="e", pady=(14, 0))
        tk.Button(buttons, text="取消", command=window.destroy, relief="flat", padx=14, pady=5).pack(side="right", padx=(8, 0))
        tk.Button(
            buttons, text="保存", command=save_settings, bg=self.config["translation_text_color"],
            fg=self.config["popup_background"], activebackground=self.config["title_text_color"], relief="flat", padx=16, pady=5,
        ).pack(side="right")
        window.protocol("WM_DELETE_WINDOW", window.destroy)

    def show_startup_notice(self) -> None:
        class Point(ctypes.Structure):
            _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]
        point = Point()
        user32.GetCursorPos(ctypes.byref(point))
        self.show_popup(point.x, point.y, DictionaryEntry("Quick Lookup 已启动", "划选英语自动查询", provider="Ctrl+Alt+P 位置 · Ctrl+Alt+S 设置 · Ctrl+Alt+Q 退出"))
        self.hide_job = self.root.after(2500, self.hide_popup)

    def quit(self) -> None:
        self.running = False
        self.hide_popup()
        self.mouse_listener.stop()
        self.hotkeys.stop()
        self.speech.stop()
        kernel32.CloseHandle(self.mutex)
        self.root.quit()
        log("stopped")

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    try:
        QuickLookupApp().run()
    except Exception as error:
        log(f"fatal error: {error}")
        if "已在运行" not in str(error):
            raise


if __name__ == "__main__":
    main()
