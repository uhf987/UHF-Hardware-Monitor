"""
UHF Hardware Monitor - ComfyUI'den bağımsız, küçük Windows sistem monitörü.

Gösterdikleri: CPU %, CPU sıcaklığı, RAM, GPU %, VRAM, GPU sıcaklığı, Disk

- CPU/RAM/Disk  : psutil
- NVIDIA GPU    : pynvml (nvidia-ml-py)
- CPU sıcaklığı : LibreHardwareMonitorLib.dll (pythonnet ile doğrudan yüklenir,
                  LibreHardwareMonitor uygulamasının açık olması GEREKMEZ).
                  Sürücü yüklemek için YÖNETİCİ olarak çalışmalıdır.

Gerekenler:
  pip install psutil nvidia-ml-py pythonnet
  LibreHardwareMonitorLib.dll (+ yanındaki diğer .dll'ler) bu dosyanın yanında.

Kullanım: sol tık sürükle, sağ tık menü (Her zaman üstte / Çıkış).
"""
import ctypes
import json
import os
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
import tkinter as tk
from tkinter import messagebox

import psutil

try:
    import pynvml

    pynvml.nvmlInit()
    GPU_HANDLE = pynvml.nvmlDeviceGetHandleByIndex(0)
    HAS_NVML = True
except Exception:
    HAS_NVML = False

# Windows'ta yüksek DPI'de yazılar bulanık çıkmasın (Tk penceresinden önce çağrılmalı)
if os.name == "nt":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

# ---------------- Ayarlar ----------------
APP_NAME = "UHF Hardware Monitor"
DLL_NAME = "LibreHardwareMonitorLib.dll"
AUTO_ELEVATE = True  # yönetici değilse UAC ile kendini yeniden başlat
DISK_PATH = "C:\\"
REFRESH_MS = 1000
COMPACT_LABEL_PT = 7   # görev çubuğu modunda üstteki küçük başlık yazısı (pt)
COMPACT_VALUE_PT = 9   # alttaki değer yazısı (pt)
TRAY_RESERVE = 360     # görev çubuğu modunda sağ taraftan bırakılan boşluk (saat/tepsi için, px)
CONFIG_FILE = os.path.join(os.path.expanduser("~"), "uhf_hardware_monitor.json")


def detect_light_taskbar():
    """Windows görev çubuğu açık temada mı? (koyu tema varsayılan)"""
    try:
        import winreg
        key = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            return winreg.QueryValueEx(k, "SystemUsesLightTheme")[0] == 1
    except Exception:
        return False


if detect_light_taskbar():
    BG, TRACK, FG, DIM, ACCENT = "#f3f3f3", "#d0d4d9", "#1b1b1b", "#5f6368", "#2f5fd0"
    LEVELS = ("#2e7d32", "#b26a00", "#c62828")
else:
    BG, TRACK, FG, DIM, ACCENT = "#1e2327", "#3a4148", "#d8dee4", "#8b949e", "#7aa2f7"
    LEVELS = ("#4caf50", "#ffb300", "#e53935")
FONT = ("Segoe UI", 9)
BAR_W, BAR_H = 110, 12
LOG_FILE = os.path.join(os.path.expanduser("~"), "uhf_hardware_monitor_error.log")
# Kaynaklar winget paket kimliği konusunda çeliştiği için ikisi de sırayla denenir
WINGET_IDS = ("namazso.PawnIO", "PawnIO.PawnIO")
PAWNIO_SITE = "https://pawnio.eu"


# ---------------- Yönetici yardımcıları ----------------
def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin():
    """Kendini UAC isteğiyle yeniden başlatır. Başarılıysa True döner."""
    try:
        if getattr(sys, "frozen", False):
            exe, args = sys.executable, sys.argv[1:]
        else:
            exe, args = sys.executable, sys.argv
        params = " ".join(f'"{a}"' for a in args)
        return ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1) > 32
    except Exception:
        return False


# ---------------- PawnIO sürücüsü ----------------
def pawnio_installed():
    """PawnIO kayıt defterinde (veya varsayılan klasörde) var mı? Windows dışında True döner."""
    try:
        import winreg
    except ImportError:
        return True
    key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO"
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path, 0, winreg.KEY_READ | view):
                return True
        except OSError:
            continue
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    return os.path.isfile(os.path.join(pf, "PawnIO", "PawnIOLib.dll"))


def install_pawnio():
    """winget ile PawnIO'yu sessizce kurar. (başarılı_mı, hata_mesajı) döner."""
    last_err = "Bilinmeyen hata"
    for pid in WINGET_IDS:
        try:
            subprocess.run(
                ["winget", "install", "--id", pid, "-e", "--silent",
                 "--accept-package-agreements", "--accept-source-agreements"],
                stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace",
                timeout=600, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except FileNotFoundError:
            return False, "winget bulunamadı"
        except Exception as ex:
            last_err = str(ex)
            continue
        if pawnio_installed():  # çıkış koduna değil, gerçek sonuca bak
            return True, ""
        last_err = f"winget '{pid}' paketini kuramadı"
    return False, last_err


# ---------------- CPU sıcaklığı (LibreHardwareMonitorLib) ----------------
def candidate_dirs():
    """DLL'lerin bulunabileceği klasörler (exe yanı, _internal, script yanı)."""
    cands = []
    if getattr(sys, "frozen", False):
        cands += [os.path.dirname(sys.executable), getattr(sys, "_MEIPASS", None)]
    cands.append(os.path.dirname(os.path.abspath(__file__)))
    out = []
    for d in cands:
        if d and d not in out and os.path.isdir(d):
            out.append(d)
    return out


def find_dll_dir():
    for d in candidate_dirs():
        if os.path.isfile(os.path.join(d, DLL_NAME)):
            return d
    return None


def pick_cpu_temp(temps):
    """[(sensör_adı, değer)] içinden en anlamlı CPU sıcaklığını seçer."""
    if not temps:
        return None
    for key in ("package", "tctl", "tdie", "core max", "average"):
        for name, val in temps:
            if key in name.lower():
                return val
    return max(v for _, v in temps)


class CpuTempReader(threading.Thread):
    """LHM kütüphanesini arka plan thread'inde açar ve sıcaklığı periyodik okur."""

    def __init__(self):
        super().__init__(daemon=True)
        self.temp = None
        self.status = "Okunuyor…"
        self.stop_event = threading.Event()
        self.resolve_log = []

    def stop(self):
        self.stop_event.set()
        if self.is_alive():
            self.join(timeout=3)

    def run(self):
        comp = None
        try:
            if not is_admin():
                self.status = "Yönetici gerek."
                return
            dll_dir = find_dll_dir()
            if dll_dir is None:
                self.status = "DLL yok"
                return
            try:
                import clr  # pythonnet
            except Exception:
                self.status = "pythonnet yok"
                return

            # .NET bağımlı DLL'leri (System.Memory vb.) varsayılan olarak sadece
            # exe'nin klasöründe arar. Bulamazsa bu işleyici bizim klasörlere bakar.
            from System import AppDomain, ResolveEventHandler
            from System.Reflection import Assembly

            search_dirs = candidate_dirs()

            def _on_resolve(sender, args):
                try:
                    simple = str(args.Name).split(",")[0]
                    for d in search_dirs:
                        p = os.path.join(d, simple + ".dll")
                        if os.path.isfile(p):
                            self.resolve_log.append(f"BULUNDU  {args.Name} -> {p}")
                            return Assembly.LoadFrom(p)
                    self.resolve_log.append(f"BULUNAMADI {args.Name}")
                except Exception as ex:
                    self.resolve_log.append(f"HATA {args.Name}: {ex}")
                return None

            self._resolver = ResolveEventHandler(_on_resolve)  # GC silmesin diye sakla
            AppDomain.CurrentDomain.add_AssemblyResolve(self._resolver)

            for d in search_dirs:
                if d not in sys.path:
                    sys.path.append(d)
            clr.AddReference("LibreHardwareMonitorLib")
            from LibreHardwareMonitor.Hardware import Computer, HardwareType, SensorType

            comp = Computer()
            comp.IsCpuEnabled = True
            comp.Open()

            while not self.stop_event.is_set():
                temps = []
                for hw in comp.Hardware:
                    if hw.HardwareType == HardwareType.Cpu:
                        hw.Update()
                        for s in hw.Sensors:
                            if s.SensorType == SensorType.Temperature and s.Value is not None and float(s.Value) > 0:
                                temps.append((str(s.Name), float(s.Value)))
                self.temp = pick_cpu_temp(temps)
                self.status = "ok" if self.temp is not None else "PawnIO yok mu?"
                self.stop_event.wait(1.0)
        except Exception:
            self.temp = None
            self.status = "Hata (log)"
            try:
                with open(LOG_FILE, "a", encoding="utf-8") as f:
                    f.write(time.strftime("[%Y-%m-%d %H:%M:%S]\n") + traceback.format_exc() + "\n")
                    f.write("--- TEŞHİS ---\n")
                    f.write(f"python={sys.version.split()[0]} frozen={getattr(sys, 'frozen', False)}\n")
                    f.write(f"exe={sys.executable}\n")
                    for d in candidate_dirs():
                        try:
                            dlls = sorted(n for n in os.listdir(d) if n.lower().endswith(".dll"))
                        except Exception:
                            dlls = []
                        has_mem = os.path.isfile(os.path.join(d, "System.Memory.dll"))
                        f.write(f"klasör: {d} | dll sayısı: {len(dlls)} | System.Memory.dll: {has_mem}\n")
                        sysd = [n for n in dlls if n.lower().startswith("system.")]
                        f.write(f"  System.* : {', '.join(sysd) or '(yok)'}\n")
                    for line in self.resolve_log:
                        f.write(f"  resolve: {line}\n")
                    f.write("\n")
            except Exception:
                pass
        finally:
            if comp is not None:
                try:
                    comp.Close()  # sürücüyü düzgünce bırak
                except Exception:
                    pass


# ---------------- Arayüz ----------------
def level_color(pct):
    if pct is None:
        return DIM
    ok, warn, bad = LEVELS
    return ok if pct < 60 else warn if pct < 85 else bad


def worst(*vals):
    vals = [v for v in vals if v is not None]
    return max(vals) if vals else None


def load_config():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
    except Exception:
        pass


def taskbar_rect():
    """Görev çubuğunun ekran koordinatları (sol, üst, sağ, alt) ya da None."""
    try:
        from ctypes import wintypes
        hwnd = ctypes.windll.user32.FindWindowW("Shell_TrayWnd", None)
        if not hwnd:
            return None
        rect = wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
        return rect.left, rect.top, rect.right, rect.bottom
    except Exception:
        return None


class Row:
    """Tam görünümdeki çubuklu satır."""

    def __init__(self, parent, label, scale=1.0):
        self.bar_w, self.bar_h = int(BAR_W * scale), int(BAR_H * scale)
        f = tk.Frame(parent, bg=BG)
        f.pack(fill="x", padx=8, pady=2)
        tk.Label(f, text=label, width=6, anchor="w", fg=FG, bg=BG, font=FONT).pack(side="left")
        self.canvas = tk.Canvas(f, width=self.bar_w, height=self.bar_h, bg=TRACK, highlightthickness=0)
        self.canvas.pack(side="left", padx=4)
        self.bar = self.canvas.create_rectangle(0, 0, 0, self.bar_h, width=0, fill=TRACK)
        self.value = tk.Label(f, text="—", width=16, anchor="e", fg=FG, bg=BG, font=FONT)
        self.value.pack(side="left")

    def set(self, pct, text="—"):
        if pct is None:
            self.canvas.coords(self.bar, 0, 0, 0, self.bar_h)
            self.value.config(text=text)
            return
        pct = max(0, min(100, pct))
        self.canvas.coords(self.bar, 0, 0, self.bar_w * pct / 100, self.bar_h)
        self.canvas.itemconfig(self.bar, fill=level_color(pct))
        self.value.config(text=text)


class Col:
    """Görev çubuğu şeridindeki sütun: üstte küçük başlık, altta renkli değer."""

    def __init__(self, parent, title, width):
        f = tk.Frame(parent, bg=BG)
        f.pack(side="left", padx=6)
        tk.Label(f, text=title, fg=DIM, bg=BG, pady=0,
                 font=("Segoe UI", COMPACT_LABEL_PT)).pack(anchor="w")
        self.val = tk.Label(f, text="—", width=width, anchor="w", fg=DIM, bg=BG, pady=0,
                            font=("Segoe UI", COMPACT_VALUE_PT, "bold"))
        self.val.pack(anchor="w")

    def set(self, text, pct):
        self.val.config(text=text, fg=level_color(pct))


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.configure(bg=BG)
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.scale = self.root.winfo_fpixels("1i") / 96.0

        self.cfg = load_config()
        self.compact = self.cfg.get("mode") == "compact"
        self.topmost = tk.BooleanVar(value=True)
        self.compact_var = tk.BooleanVar(value=self.compact)
        self._moved = False

        # --- Tam görünüm ---
        self.full = tk.Frame(self.root, bg=BG)
        tk.Label(self.full, text=APP_NAME, fg=ACCENT, bg=BG,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=8, pady=(6, 0))
        self.rows = {k: Row(self.full, k, self.scale) for k in
                     ("CPU", "CPU °C", "RAM", "GPU", "VRAM", "GPU °C", "Disk")}
        tk.Frame(self.full, bg=BG, height=4).pack()

        # --- Görev çubuğu şeridi ---
        self.strip = tk.Frame(self.root, bg=BG)
        self.cols = {"cpu": Col(self.strip, "CPU", 9), "ram": Col(self.strip, "RAM", 8)}
        if HAS_NVML:  # NVIDIA kart yoksa GPU/VRAM sütunları yer kaplamasın
            self.cols["gpu"] = Col(self.strip, "GPU", 9)
            self.cols["vram"] = Col(self.strip, "VRAM", 8)
        self.cols["disk"] = Col(self.strip, "DİSK", 4)

        # --- CPU sıcaklığı okuyucu / PawnIO ---
        self.cpu_temp = CpuTempReader()
        self.install_result = None  # kurulum thread'inden ana thread'e sonuç
        if os.name == "nt" and is_admin() and not pawnio_installed():
            self.cpu_temp.status = "PawnIO kurulu değil"
            self.root.after(500, self.offer_pawnio)  # pencere çizildikten sonra sor
        else:
            self.cpu_temp.start()

        # --- Menü ---
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_checkbutton(label="Görev çubuğu modu", variable=self.compact_var,
                                  command=self.toggle_mode)
        self.menu.add_checkbutton(label="Her zaman üstte", variable=self.topmost,
                                  command=lambda: self.root.attributes("-topmost", self.topmost.get()))
        self.menu.add_command(label="Konumu sıfırla", command=self.reset_position)
        self.menu.add_command(label="PawnIO sürücüsünü kur…", command=self.menu_install_pawnio)
        self.menu.add_separator()
        self.menu.add_command(label="Çıkış", command=self.quit)

        self.root.bind_all("<ButtonPress-1>", self._drag_start)
        self.root.bind_all("<B1-Motion>", self._drag_move)
        self.root.bind_all("<ButtonRelease-1>", self._drag_end)
        self.root.bind_all("<Button-3>", lambda e: self.menu.tk_popup(e.x_root, e.y_root))

        self.apply_mode()
        psutil.cpu_percent()  # ilk çağrı referans noktası
        self.update()
        self._keep_on_top()

    # ----- Görünüm modu / konum -----
    def _mode_key(self):
        return "compact" if self.compact else "full"

    def default_pos(self):
        self.root.update_idletasks()
        w, h = self.root.winfo_reqwidth(), self.root.winfo_reqheight()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        if not self.compact:
            return 40, 40
        reserve = int(TRAY_RESERVE * self.scale)
        rect = taskbar_rect()
        if rect:
            left, top, right, bottom = rect
            if (right - left) >= (bottom - top):  # yatay görev çubuğu: dikeyde ortala
                return right - w - reserve, top + max(0, ((bottom - top) - h) // 2)
            return left + max(0, ((right - left) - w) // 2), bottom - h - int(200 * self.scale)
        return sw - w - reserve, sh - h - 4

    def apply_mode(self):
        self.full.pack_forget()
        self.strip.pack_forget()
        if self.compact:
            self.strip.pack(padx=4, pady=2)
            self.root.attributes("-alpha", 1.0)
        else:
            self.full.pack()
            self.root.attributes("-alpha", 0.92)
        self.root.update_idletasks()
        w, h = self.root.winfo_reqwidth(), self.root.winfo_reqheight()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        pos = self.cfg.get("pos", {}).get(self._mode_key())
        x, y = pos if pos else self.default_pos()
        x, y = max(0, min(int(x), sw - w)), max(0, min(int(y), sh - h))  # ekran dışında kalmasın
        self.root.geometry(f"+{x}+{y}")
        self.raise_window()

    def toggle_mode(self):
        self.compact = self.compact_var.get()
        self.cfg["mode"] = self._mode_key()
        save_config(self.cfg)
        self.apply_mode()

    def reset_position(self):
        self.cfg.get("pos", {}).pop(self._mode_key(), None)
        save_config(self.cfg)
        self.apply_mode()

    def raise_window(self):
        """Görev çubuğu öne çıktığında şeridi tekrar en üste al (odağı çalmadan)."""
        try:
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
            user32.GetAncestor.restype = wintypes.HWND
            user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                            ctypes.c_int, ctypes.c_int, wintypes.UINT]
            hwnd = user32.GetAncestor(self.root.winfo_id(), 2)  # GA_ROOT
            # HWND_TOPMOST=-1; SWP_NOSIZE|SWP_NOMOVE|SWP_NOACTIVATE
            user32.SetWindowPos(hwnd, wintypes.HWND(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
        except Exception:
            self.root.attributes("-topmost", True)

    def _keep_on_top(self):
        if self.compact or self.topmost.get():
            self.raise_window()
        self.root.after(1000, self._keep_on_top)

    def quit(self):
        self.cpu_temp.stop()
        self.root.destroy()

    # ----- PawnIO kurulum akışı -----
    def _ask(self, title, text):
        self.root.attributes("-topmost", False)  # diyalog arkada kalmasın
        try:
            return messagebox.askyesno(title, text, parent=self.root)
        finally:
            self.root.attributes("-topmost", self.compact or self.topmost.get())

    def offer_pawnio(self):
        ok = self._ask(
            f"{APP_NAME} - PawnIO gerekli",
            "CPU sıcaklığını okumak için PawnIO sürücüsü gerekiyor ve bu bilgisayarda kurulu değil.\n\n"
            "Şimdi winget ile otomatik kurulsun mu?")
        if not ok:
            self.cpu_temp.status = "PawnIO yok"
            return
        self.cpu_temp.status = "PawnIO kuruluyor…"

        def worker():
            self.install_result = install_pawnio()

        threading.Thread(target=worker, daemon=True).start()

    def menu_install_pawnio(self):
        if os.name != "nt" or pawnio_installed():
            messagebox.showinfo(APP_NAME, "PawnIO zaten kurulu.", parent=self.root)
        elif not is_admin():
            messagebox.showwarning(APP_NAME, "Kurulum için programı yönetici olarak çalıştırmalısın.",
                                   parent=self.root)
        else:
            self.offer_pawnio()

    def _finish_install(self, ok, msg):
        if ok:
            self.cpu_temp.status = "Okunuyor…"
            if self.cpu_temp.ident is None:  # henüz başlamadıysa başlat
                self.cpu_temp.start()
        else:
            self.cpu_temp.status = "PawnIO kurulamadı"
            if self._ask(f"{APP_NAME} - Kurulum başarısız",
                         f"PawnIO otomatik kurulamadı ({msg}).\n\n"
                         f"{PAWNIO_SITE} sayfasını açıp elle kurmak ister misin?"):
                webbrowser.open(PAWNIO_SITE)

    # ----- Sürükleme (konum modlara göre ayrı kaydedilir) -----
    def _drag_start(self, e):
        self._moved = False
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()

    def _drag_move(self, e):
        self._moved = True
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _drag_end(self, e):
        if self._moved:
            self.cfg.setdefault("pos", {})[self._mode_key()] = [self.root.winfo_x(), self.root.winfo_y()]
            save_config(self.cfg)
            self._moved = False

    # ----- Veri toplama ve çizim -----
    def collect(self):
        raw = {"cpu": psutil.cpu_percent(), "cpu_t": self.cpu_temp.temp,
               "gpu": None, "gpu_t": None, "vram_used": None, "vram_total": None, "vram_pct": None}
        vm = psutil.virtual_memory()
        raw.update(ram_pct=vm.percent, ram_used=vm.used / 2**30, ram_total=vm.total / 2**30)
        try:
            raw["disk"] = psutil.disk_usage(DISK_PATH).percent
        except Exception:
            raw["disk"] = None
        if HAS_NVML:
            try:
                mem = pynvml.nvmlDeviceGetMemoryInfo(GPU_HANDLE)
                raw["gpu"] = pynvml.nvmlDeviceGetUtilizationRates(GPU_HANDLE).gpu
                raw["gpu_t"] = pynvml.nvmlDeviceGetTemperature(GPU_HANDLE, pynvml.NVML_TEMPERATURE_GPU)
                raw.update(vram_used=mem.used / 2**30, vram_total=mem.total / 2**30,
                           vram_pct=mem.used / mem.total * 100)
            except Exception:
                pass
        return raw

    def render_full(self, d):
        r = self.rows
        r["CPU"].set(d["cpu"], f"{d['cpu']:.0f}%")
        t = d["cpu_t"]
        if t is not None:
            r["CPU °C"].set(t, f"{t:.0f} °C")
        else:
            st = self.cpu_temp.status
            r["CPU °C"].set(None, st if st != "ok" else "—")
        r["RAM"].set(d["ram_pct"], f"{d['ram_used']:.1f}/{d['ram_total']:.0f} GB")
        if d["disk"] is not None:
            r["Disk"].set(d["disk"], f"{d['disk']:.0f}%")
        else:
            r["Disk"].set(None)
        if d["gpu"] is not None:
            r["GPU"].set(d["gpu"], f"{d['gpu']}%")
            r["VRAM"].set(d["vram_pct"], f"{d['vram_used']:.1f}/{d['vram_total']:.0f} GB")
            r["GPU °C"].set(d["gpu_t"], f"{d['gpu_t']} °C")
        else:
            for k in ("GPU", "VRAM", "GPU °C"):
                r[k].set(None)

    def render_compact(self, d):
        c = self.cols
        t = d["cpu_t"]
        c["cpu"].set(f"{d['cpu']:.0f}%" + (f" {t:.0f}°" if t is not None else ""), worst(d["cpu"], t))
        c["ram"].set(f"{d['ram_used']:.1f}/{d['ram_total']:.0f}G", d["ram_pct"])
        if "gpu" in c:
            if d["gpu"] is not None:
                c["gpu"].set(f"{d['gpu']}% {d['gpu_t']}°", worst(d["gpu"], d["gpu_t"]))
                c["vram"].set(f"{d['vram_used']:.1f}/{d['vram_total']:.0f}G", d["vram_pct"])
            else:
                c["gpu"].set("—", None)
                c["vram"].set("—", None)
        if d["disk"] is not None:
            c["disk"].set(f"{d['disk']:.0f}%", d["disk"])
        else:
            c["disk"].set("—", None)

    def update(self):
        if self.install_result is not None:
            ok, msg = self.install_result
            self.install_result = None
            self._finish_install(ok, msg)

        data = self.collect()
        if self.compact:
            self.render_compact(data)
        else:
            self.render_full(data)

        self.root.after(REFRESH_MS, self.update)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    if AUTO_ELEVATE and os.name == "nt" and not is_admin():
        if relaunch_as_admin():
            sys.exit(0)  # yükseltilmiş kopya açıldı, bu kopya kapanır
    App().run()
