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
import urllib.parse
import urllib.request

from pynput import keyboard, mouse

__version__ = "0.2.0"

POPUP_SECONDS = 7
COPY_TIMEOUT_SECONDS = 0.7
SELECTION_SETTLE_SECONDS = 0.18
DOUBLE_CLICK_SETTLE_SECONDS = 0.12
DOUBLE_CLICK_RADIUS = 20
DRAG_SELECTION_DISTANCE = 5
MAX_TEXT_LENGTH = 180
CACHE_SIZE = 200
ROOT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = ROOT_DIR / "quick_lookup_config.json"
LOG_FILE = ROOT_DIR / "quick_translate.log"

DEFAULT_CONFIG = {
    "popup_position": "selection_right",
    "dictionary_provider": "free",
    "language": "en-gb",
    "fixed_position": "center",
}

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


def log(message: str) -> None:
    """A small local diagnostic log; never write selected text to it."""
    try:
        with LOG_FILE.open("a", encoding="utf-8") as file:
            file.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    except OSError:
        pass


def load_config() -> dict[str, str]:
    config = DEFAULT_CONFIG.copy()
    try:
        saved = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if isinstance(saved, dict):
            config.update({key: value for key, value in saved.items() if key in config and isinstance(value, str)})
    except (OSError, json.JSONDecodeError):
        pass
    if config["popup_position"] not in {"selection_right", "center"}:
        config["popup_position"] = DEFAULT_CONFIG["popup_position"]
    if config["dictionary_provider"] not in {"free", "oxford"}:
        config["dictionary_provider"] = DEFAULT_CONFIG["dictionary_provider"]
    return config


def save_config(config: dict[str, str]) -> None:
    try:
        CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as error:
        log(f"could not save config: {error}")


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


def request_json(url: str, headers: dict[str, str] | None = None) -> object:
    request = urllib.request.Request(url, headers={"User-Agent": "QuickLookup/0.2", **(headers or {})})
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def translate_to_chinese(source: str) -> str:
    query = urllib.parse.urlencode({"client": "gtx", "sl": "en", "tl": "zh-CN", "dt": "t", "q": source})
    try:
        data = request_json("https://translate.googleapis.com/translate_a/single?" + query)
        return "".join(part[0] for part in data[0] if part and part[0]).strip()  # type: ignore[index]
    except Exception as error:
        log(f"translation request failed: {type(error).__name__}")
        return "翻译服务暂不可用"


def free_dictionary(word: str) -> tuple[str, str, list[str], list[str]]:
    """Return IPA, part of speech, short definitions and examples from dictionaryapi.dev."""
    data = request_json("https://api.dictionaryapi.dev/api/v2/entries/en/" + urllib.parse.quote(word))
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        raise ValueError("unexpected free dictionary response")
    entry = data[0]
    ipa = entry.get("phonetic", "") if isinstance(entry.get("phonetic"), str) else ""
    if not ipa:
        for phonetic in entry.get("phonetics", []):
            if isinstance(phonetic, dict) and isinstance(phonetic.get("text"), str):
                ipa = phonetic["text"]
                break
    part, definitions, examples = "", [], []
    for meaning in entry.get("meanings", [])[:2]:
        if not isinstance(meaning, dict):
            continue
        if not part and isinstance(meaning.get("partOfSpeech"), str):
            part = meaning["partOfSpeech"]
        for definition in meaning.get("definitions", [])[:2]:
            if not isinstance(definition, dict):
                continue
            text = definition.get("definition")
            if isinstance(text, str):
                definitions.append(text)
            example = definition.get("example")
            if isinstance(example, str):
                examples.append(example)
    return ipa, part, definitions[:3], examples[:1]


def oxford_dictionary(word: str, language: str) -> tuple[str, str, list[str], list[str]]:
    """Read licensed Oxford API content when the user supplies their own credentials."""
    app_id, app_key = os.getenv("OXFORD_APP_ID"), os.getenv("OXFORD_APP_KEY")
    if not app_id or not app_key:
        raise RuntimeError("未设置 OXFORD_APP_ID 和 OXFORD_APP_KEY")
    base_url = os.getenv("OXFORD_API_BASE_URL", "https://od-api.oxforddictionaries.com/api/v2").rstrip("/")
    query = urllib.parse.urlencode({"q": word, "fields": "definitions,pronunciations,examples"})
    data = request_json(f"{base_url}/words/{language}?{query}", {"app_id": app_id, "app_key": app_key})
    if not isinstance(data, dict):
        raise ValueError("unexpected Oxford response")
    ipa, part, definitions, examples = "", "", [], []
    for result in data.get("results", [])[:1]:
        if not isinstance(result, dict):
            continue
        for lexical in result.get("lexicalEntries", [])[:2]:
            if not isinstance(lexical, dict):
                continue
            category = lexical.get("lexicalCategory", {})
            if not part and isinstance(category, dict) and isinstance(category.get("text"), str):
                part = category["text"]
            for pronunciation in lexical.get("pronunciations", []):
                if not ipa and isinstance(pronunciation, dict) and isinstance(pronunciation.get("phoneticSpelling"), str):
                    ipa = pronunciation["phoneticSpelling"]
            for entry in lexical.get("entries", [])[:1]:
                if not isinstance(entry, dict):
                    continue
                for pronunciation in entry.get("pronunciations", []):
                    if not ipa and isinstance(pronunciation, dict) and isinstance(pronunciation.get("phoneticSpelling"), str):
                        ipa = pronunciation["phoneticSpelling"]
                for sense in entry.get("senses", [])[:3]:
                    if not isinstance(sense, dict):
                        continue
                    for definition in sense.get("definitions", [])[:2]:
                        if isinstance(definition, str):
                            definitions.append(definition)
                    for example in sense.get("examples", [])[:1]:
                        if isinstance(example, dict) and isinstance(example.get("text"), str):
                            examples.append(example["text"])
    return ipa, part, definitions[:3], examples[:1]


class QuickLookupApp:
    def __init__(self) -> None:
        self.mutex = kernel32.CreateMutexW(None, False, "Local\\QuickLookupSelectionPopup")
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            raise RuntimeError("Quick Lookup 已在运行")
        self.config = load_config()
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.report_callback_exception = self.report_tk_error
        self.trigger_queue: queue.Queue[tuple[float, int, int, str]] = queue.Queue()
        self.ui_queue: queue.Queue[tuple] = queue.Queue()
        self.pending: list[tuple[float, int, int, str]] = []
        self.cache: OrderedDict[str, DictionaryEntry] = OrderedDict()
        self.popup: tk.Toplevel | None = None
        self.hide_job: str | None = None
        self.mouse_down: tuple[int, int, float] | None = None
        self.last_left_down: tuple[int, int, float] | None = None
        self.request_id, self.running = 0, True
        self.mouse_listener = mouse.Listener(on_click=self.on_click)
        self.hotkeys = keyboard.GlobalHotKeys({
            "<ctrl>+<alt>+q": self.request_quit,
            "<ctrl>+<alt>+p": self.request_position_cycle,
        })
        self.mouse_listener.start()
        self.hotkeys.start()
        self.root.after(20, self.process_queues)
        self.root.after(250, self.show_startup_notice)
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
        key = f"{self.config['dictionary_provider']}:{source.lower()}"
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        entry = DictionaryEntry(word=source, chinese=translate_to_chinese(source))
        if " " not in source and re.fullmatch(r"[A-Za-z'-]+", source):
            try:
                if self.config["dictionary_provider"] == "oxford":
                    ipa, part, definitions, examples = oxford_dictionary(source, self.config["language"])
                    entry.provider = "Oxford Dictionaries API"
                else:
                    ipa, part, definitions, examples = free_dictionary(source)
                    entry.provider = "Free Dictionary"
                entry.ipa, entry.part_of_speech = ipa, part
                entry.definitions, entry.examples = definitions, examples
            except Exception as error:
                entry.provider = "翻译"
                entry.notice = "词典释义暂不可用"
                log(f"dictionary lookup failed: {type(error).__name__}")
        else:
            entry.provider = "短语翻译"
        self.cache[key] = entry
        if len(self.cache) > CACHE_SIZE:
            self.cache.popitem(last=False)
        return entry

    def show_popup(self, x: int, y: int, entry: DictionaryEntry) -> None:
        self.hide_popup()
        popup = tk.Toplevel(self.root)
        self.popup = popup
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        popup.configure(bg="#202124")
        frame = tk.Frame(popup, bg="#202124", padx=14, pady=11)
        frame.pack()
        title = entry.word + (f"   /{entry.ipa}/" if entry.ipa else "")
        self.add_label(frame, title, "#ffffff", 12, "bold")
        if entry.part_of_speech:
            self.add_label(frame, entry.part_of_speech, "#a9c7ff", 9)
        self.add_label(frame, entry.chinese, "#b9d4ff", 11)
        for definition in entry.definitions[:3]:
            self.add_label(frame, "• " + definition, "#e8eaed", 10)
        for example in entry.examples[:1]:
            self.add_label(frame, "例：" + example, "#aeb4bc", 9)
        if entry.notice:
            self.add_label(frame, entry.notice, "#ffcf70", 9)
        self.add_label(frame, f"{entry.provider} · Ctrl+Alt+P 切换位置", "#7f8792", 8)
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

    @staticmethod
    def add_label(parent: tk.Widget, text: str, color: str, size: int, weight: str = "normal") -> None:
        tk.Label(parent, text=text, bg="#202124", fg=color, font=("Microsoft YaHei UI", size, weight),
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

    def show_startup_notice(self) -> None:
        class Point(ctypes.Structure):
            _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]
        point = Point()
        user32.GetCursorPos(ctypes.byref(point))
        self.show_popup(point.x, point.y, DictionaryEntry("Quick Lookup 已启动", "划选英语自动查询", provider="Ctrl+Alt+P 切换位置 · Ctrl+Alt+Q 退出"))
        self.hide_job = self.root.after(2500, self.hide_popup)

    def quit(self) -> None:
        self.running = False
        self.hide_popup()
        self.mouse_listener.stop()
        self.hotkeys.stop()
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
