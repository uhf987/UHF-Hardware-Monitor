"""
UHF Hardware Monitor - ComfyUI'den bağımsız, küçük Windows sistem monitörü.

Gösterdikleri: CPU % + sıcaklık, RAM, GPU % + sıcaklık, VRAM, Disk

Ekranın sağ altında (saatin hemen üstünde) arka plansız, yarı saydam ve
TIKLANAMAZ bir gösterge olarak durur; fare tıklamaları altındaki pencereye geçer.

Kısayollar (her yerde çalışır):
  Ctrl+Alt+F10 : düzenleme modu aç/kapat (sürükleyip taşı, sağ tık menü)
  Ctrl+Alt+F12 : programı kapat

- CPU/RAM/Disk  : psutil
- NVIDIA GPU    : pynvml (nvidia-ml-py)
- CPU sıcaklığı : LibreHardwareMonitorLib.dll (pythonnet ile doğrudan yüklenir,
                  LibreHardwareMonitor uygulamasının açık olması GEREKMEZ).
                  Sürücü yüklemek için YÖNETİCİ olarak çalışmalıdır.

Gerekenler:
  pip install psutil nvidia-ml-py pythonnet
  LibreHardwareMonitorLib.dll (+ yanındaki diğer .dll'ler) bu dosyanın yanında.
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
import tkinter.font as tkfont
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
CONFIG_FILE = os.path.join(os.path.expanduser("~"), "uhf_hardware_monitor.json")
LOG_FILE = os.path.join(os.path.expanduser("~"), "uhf_hardware_monitor_error.log")
DEBUG_FILE = os.path.join(os.path.expanduser("~"), "uhf_hardware_monitor_debug.log")
# Kaynaklar winget paket kimliği konusunda çeliştiği için ikisi de sırayla denenir
WINGET_IDS = ("namazso.PawnIO", "PawnIO.PawnIO")
PAWNIO_SITE = "https://pawnio.eu"

# --- Görünüm ---
OVERLAY_ALPHA = 0.75          # 0.0 (görünmez) - 1.0 (tam opak); düşürdükçe daha silik olur
OVERLAY_PT = 10               # yazı boyutu (pt)
BAR_W, BAR_H = 70, 8          # renkli çubuğun boyutu (px, 96 DPI'a göre)
OVERLAY_MARGIN_X = 0          # ekranın sağ kenarından boşluk (px); büyüttükçe sola kayar
OVERLAY_MARGIN_Y = 0          # görev çubuğunun üstünden boşluk (px); büyüttükçe yukarı çıkar
OVERLAY_ROWS = ("cpu", "ram", "gpu", "vram", "disk")  # göstermek istemediğini listeden sil
POS_KEY = "overlay_pos_v3"   # config içindeki konum anahtarı
OV_KEY = "#010101"            # şeffaf yapılan renk anahtarı (yazılarda kullanılmaz)
OV_LABEL = "#c3cad2"
OV_TRACK = "#2b3138"
OV_LEVELS = ("#5fd068", "#ffc233", "#ff5a52")  # yeşil, sarı, kırmızı

# --- Kısayollar: hepsi Ctrl+Alt+<tuş> ---
HOTKEY_EDIT_VK, HOTKEY_EDIT_TEXT = 0x79, "Ctrl+Alt+F10"   # 0x79 = F10
HOTKEY_QUIT_VK, HOTKEY_QUIT_TEXT = 0x7B, "Ctrl+Alt+F12"   # 0x7B = F12


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
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020  # fare olaylarını alttaki pencereye geçirir


def ov_level_color(pct):
    if pct is None:
        return OV_LABEL
    ok, warn, bad = OV_LEVELS
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


def work_area():
    """Görev çubuğu hariç çalışma alanı (sol, üst, sağ, alt) ya da None."""
    try:
        from ctypes import wintypes
        rect = wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):  # SPI_GETWORKAREA
            return rect.left, rect.top, rect.right, rect.bottom
    except Exception:
        pass
    return None


def corner_anchor():
    """(sağ, alt): ekranın sağ alt köşesi, görev çubuğunun (saatin) hemen üstü.
    Önce görev çubuğunun gerçek konumuna bakar, olmazsa çalışma alanını, o da olmazsa ekran boyutunu kullanır."""
    user32 = ctypes.windll.user32
    right, bottom = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)  # ana ekran
    wa = work_area()
    if wa:
        right, bottom = wa[2], wa[3]
    try:
        from ctypes import wintypes
        hwnd = user32.FindWindowW("Shell_TrayWnd", None)
        if hwnd:
            r = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            if (r.right - r.left) >= (r.bottom - r.top) and r.top > user32.GetSystemMetrics(1) // 2:
                bottom = r.top  # alt kenardaki yatay görev çubuğunun üst çizgisi
    except Exception:
        pass
    return right, bottom


def window_rect(hwnd):
    """Pencerenin gerçek ekran konumu (sol, üst, sağ, alt) ya da None."""
    try:
        from ctypes import wintypes
        r = wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
        return r.left, r.top, r.right, r.bottom
    except Exception:
        return None


def get_hwnd(widget):
    """Tk kök penceresinin gerçek HWND'si."""
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    return user32.GetAncestor(widget.winfo_id(), 2)  # GA_ROOT


def set_exstyle(hwnd, add=0, remove=0):
    """Pencerenin genişletilmiş stilini değiştirir (tıklanamazlık vb.)."""
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    get = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
    set_ = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
    get.argtypes = [wintypes.HWND, ctypes.c_int]
    get.restype = ctypes.c_ssize_t
    set_.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    set_.restype = ctypes.c_ssize_t
    style = get(hwnd, -20)  # GWL_EXSTYLE
    set_(hwnd, -20, (style | add) & ~remove)
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, wintypes.UINT]
    # SWP_NOMOVE|SWP_NOSIZE|SWP_NOZORDER|SWP_NOACTIVATE|SWP_FRAMECHANGED
    user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0002 | 0x0001 | 0x0004 | 0x0010 | 0x0020)


def hotkey_down(vk):
    """Ctrl+Alt+<vk> şu an basılı mı? (pencere odakta olmasa da çalışır)"""
    try:
        gs = ctypes.windll.user32.GetAsyncKeyState
        return all(gs(k) & 0x8000 for k in (0x11, 0x12, vk))
    except Exception:
        return False


class Overlay:
    """Arka plansız gösterge: başlık | renkli çubuk | değer. Boşluklar tamamen şeffaf."""

    TITLES = {"cpu": "CPU", "ram": "RAM", "gpu": "GPU", "vram": "VRAM", "disk": "DİSK"}
    HINT = f"Sürükle  •  {HOTKEY_EDIT_TEXT}: kilitle\n{HOTKEY_QUIT_TEXT}: kapat"

    def __init__(self, parent, keys, scale):
        self.keys = list(keys)
        self.font = tkfont.Font(family="Segoe UI", size=OVERLAY_PT, weight="bold")
        hint_font = tkfont.Font(family="Segoe UI", size=8)
        pad, gap = int(5 * scale), int(10 * scale)
        self.bar_w, self.bar_h = int(BAR_W * scale), int(BAR_H * scale)
        line_h = self.font.metrics("linespace") + int(3 * scale)
        label_w = max(self.font.measure(self.TITLES[k]) for k in self.keys)
        value_w = max(self.font.measure("100%  100°C"), self.font.measure("000.0/000 GB"))
        hint_lines = self.HINT.split("\n")
        hint_h = hint_font.metrics("linespace") * len(hint_lines) + int(8 * scale)  # üstte, kilitliyken şeffaf
        w = max(pad * 2 + label_w + gap + self.bar_w + gap + value_w,
                max(hint_font.measure(s) for s in hint_lines) + pad * 2)
        h = hint_h + pad * 2 + line_h * len(self.keys)

        self.canvas = tk.Canvas(parent, width=w, height=h, bg=OV_KEY, bd=0, highlightthickness=0)
        c = self.canvas
        # Düzenleme modunda görünen panel + ipucu (kilitliyken gizli = tamamen şeffaf)
        self.panel = c.create_rectangle(0, 0, w - 1, h - 1, fill="#1e2327", outline="#7aa2f7",
                                        state="hidden")
        self.hint = c.create_text(w / 2, hint_h / 2, text=self.HINT, fill="#8b949e", font=hint_font,
                                  justify="center", state="hidden")
        self.bar_x = pad + label_w + gap
        self.rows = {}
        for i, k in enumerate(self.keys):
            y = hint_h + pad + i * line_h + line_h / 2
            self._text(pad, y, "w", self.TITLES[k], OV_LABEL)
            top, bot = y - self.bar_h / 2, y + self.bar_h / 2
            c.create_rectangle(self.bar_x, top, self.bar_x + self.bar_w, bot,
                               fill=OV_TRACK, outline="#000000")
            fill = c.create_rectangle(self.bar_x + 1, top + 1, self.bar_x + 1, bot - 1,
                                      width=0, fill=OV_LABEL)
            ids = self._text(w - pad, y, "e", "—", OV_LABEL)
            self.rows[k] = (ids, fill, top + 1, bot - 1)

    def _text(self, x, y, anchor, text, fill):
        c = self.canvas
        ids = [c.create_text(x + dx, y + dy, text=text, anchor=anchor, font=self.font, fill="#000000")
               for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1))]  # siyah kontür
        ids.append(c.create_text(x, y, text=text, anchor=anchor, font=self.font, fill=fill))
        return ids

    def set(self, key, text, bar_pct, color_pct):
        """text: sağdaki değer, bar_pct: çubuğun dolulığu (%), color_pct: renk seviyesini belirleyen değer."""
        row = self.rows.get(key)
        if not row:
            return
        ids, fill, y0, y1 = row
        color = ov_level_color(color_pct)
        for i in ids[:-1]:
            self.canvas.itemconfig(i, text=text)
        self.canvas.itemconfig(ids[-1], text=text, fill=color)
        pct = max(0, min(100, bar_pct)) if bar_pct is not None else 0
        x0 = self.bar_x + 1
        self.canvas.coords(fill, x0, y0, x0 + (self.bar_w - 2) * pct / 100, y1)
        self.canvas.itemconfig(fill, fill=color)

    def set_edit(self, on):
        state = "normal" if on else "hidden"
        self.canvas.itemconfig(self.panel, state=state)
        self.canvas.itemconfig(self.hint, state=state)


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.configure(bg=OV_KEY)
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.scale = self.root.winfo_fpixels("1i") / 96.0

        self.cfg = load_config()
        self.edit = False  # True: taşınabilir + menü açılır, False: tıklanamaz
        self._moved = False
        self._hk_prev = {"edit": False, "quit": False}
        self._menu_open = False  # menü açıkken pencereyi öne alma
        self._target = None
        # Düzenleme modunda varsayılan: normal pencere gibi davran (menü üstte kalsın)
        self.edit_topmost = tk.BooleanVar(value=bool(self.cfg.get("edit_topmost", False)))

        keys = [k for k in OVERLAY_ROWS if HAS_NVML or k not in ("gpu", "vram")]
        self.overlay = Overlay(self.root, keys, self.scale)
        self.overlay.canvas.pack()

        # --- CPU sıcaklığı okuyucu / PawnIO ---
        self.cpu_temp = CpuTempReader()
        self.install_result = None  # kurulum thread'inden ana thread'e sonuç
        if os.name == "nt" and is_admin() and not pawnio_installed():
            self.cpu_temp.status = "PawnIO kurulu değil"
            self.root.after(500, self.offer_pawnio)  # pencere çizildikten sonra sor
        else:
            self.cpu_temp.start()

        # --- Menü (yalnızca düzenleme modunda ulaşılır) ---
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label=f"Kilitle / tıklanamaz yap  ({HOTKEY_EDIT_TEXT})",
                              command=self.toggle_edit)
        self.menu.add_checkbutton(label="Düzenlerken her zaman üstte tut", variable=self.edit_topmost,
                                  command=self.toggle_edit_topmost)
        self.menu.add_command(label="Konumu sıfırla", command=self.reset_position)
        self.menu.add_command(label="PawnIO sürücüsünü kur…", command=self.menu_install_pawnio)
        self.menu.add_separator()
        self.menu.add_command(label=f"Çıkış  ({HOTKEY_QUIT_TEXT})", command=self.quit)

        c = self.overlay.canvas
        c.bind("<ButtonPress-1>", self._drag_start)
        c.bind("<B1-Motion>", self._drag_move)
        c.bind("<ButtonRelease-1>", self._drag_end)
        c.bind("<Button-3>", self._popup_menu)

        self.apply_overlay()
        self.root.after(300, self.place)    # pencere çizildikten sonra tekrar yerleştir
        self.root.after(1500, self.place)
        psutil.cpu_percent()  # ilk çağrı referans noktası
        self.update()
        self._keep_on_top()
        self._poll_hotkeys()

    # ----- Konum / görünüm -----
    def default_pos(self, w, h):
        """Sağ alt köşe: görev çubuğunun (saatin) hemen üstü, köşeye tam hizalı."""
        mx, my = int(OVERLAY_MARGIN_X * self.scale), int(OVERLAY_MARGIN_Y * self.scale)
        try:
            right, bottom = corner_anchor()
        except Exception:
            right = self.root.winfo_screenwidth()
            bottom = self.root.winfo_screenheight() - int(48 * self.scale)
        return right - w - mx, bottom - h - my

    def apply_overlay(self):
        self.root.attributes("-transparentcolor", OV_KEY)  # arka plan tamamen şeffaf
        self.overlay.set_edit(self.edit)
        self.root.attributes("-alpha", 0.95 if self.edit else OVERLAY_ALPHA)
        self.place()
        self._apply_clickthrough()
        self.raise_window()

    def _target_xy(self, w, h):
        """Pencerenin olması gereken konum: kayıtlı konum varsa o, yoksa sağ alt köşe."""
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        pos = self.cfg.get(POS_KEY)
        x, y = pos if pos else self.default_pos(w, h)
        return max(0, min(int(x), sw - w)), max(0, min(int(y), sh - h))  # ekran dışında kalmasın

    def place(self):
        """Pencereyi konumlandırır (varsayılan: sağ alt köşe, görev çubuğunun hemen üstü)."""
        self.root.update_idletasks()
        w, h = self.root.winfo_reqwidth(), self.root.winfo_reqheight()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        pos = self.cfg.get(POS_KEY)
        x, y = self._target_xy(w, h)
        self._target = (x, y, w, h)
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        try:  # Tk'ya güvenmeden doğrudan Win32 ile de yerleştir
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                            ctypes.c_int, ctypes.c_int, wintypes.UINT]
            user32.SetWindowPos(get_hwnd(self.root), wintypes.HWND(-1 if self._want_topmost() else -2),
                                x, y, w, h, 0x0010)  # TOPMOST/NOTOPMOST, NOACTIVATE
        except Exception:
            pass
        self._dbg = getattr(self, "_dbg", 0) + 1
        try:  # teşhis kaydı (konum sorunu olursa buna bakılır); dosya şişmesin diye sınırlı
            if self._dbg > 40:
                return
            with open(DEBUG_FILE, "a", encoding="utf-8") as f:
                f.write(time.strftime("[%H:%M:%S] ") + f"ekran={sw}x{sh} pencere={w}x{h} "
                        f"olcek={self.scale:.2f} calisma_alani={work_area()} koseler={corner_anchor()} "
                        f"kayitli={pos} -> hedef x={x} y={y} | gercek={window_rect(get_hwnd(self.root))}\n")
        except Exception:
            pass

    def _apply_clickthrough(self):
        """Kilitliyken pencere fare olaylarını görmez (tıklar alttakine geçer)."""
        if os.name != "nt":
            return
        try:
            hwnd = get_hwnd(self.root)
            if self.edit:
                set_exstyle(hwnd, remove=WS_EX_TRANSPARENT)
            else:
                set_exstyle(hwnd, add=WS_EX_LAYERED | WS_EX_TRANSPARENT)
        except Exception:
            pass

    def toggle_edit(self):
        self.edit = not self.edit
        self.apply_overlay()

    def reset_position(self):
        self.cfg.pop(POS_KEY, None)
        save_config(self.cfg)
        self.apply_overlay()

    def _want_topmost(self):
        """Kilitliyken her zaman üstte; düzenlerken yalnızca seçenek açıksa."""
        return (not self.edit) or self.edit_topmost.get()

    def raise_window(self):
        """Pencerenin z-sırasını duruma göre ayarlar (odağı çalmadan)."""
        top = self._want_topmost()
        try:
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            hwnd = get_hwnd(self.root)
            user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                            ctypes.c_int, ctypes.c_int, wintypes.UINT]
            # HWND_TOPMOST=-1 / HWND_NOTOPMOST=-2; SWP_NOSIZE|SWP_NOMOVE|SWP_NOACTIVATE
            user32.SetWindowPos(hwnd, wintypes.HWND(-1 if top else -2), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
        except Exception:
            self.root.attributes("-topmost", top)

    def _keep_on_top(self):
        # Menü açıkken veya düzenlerken (seçenek kapalıysa) öne alma; menü arkada kalmasın
        if self._want_topmost() and not self._menu_open:
            self.raise_window()
        # Kilitliyken pencere hedef konumda değilse (Windows/Tk kaydırdıysa, görev çubuğu geç
        # yüklendiyse, çözünürlük değiştiyse) kendini yeniden doğru yere alır.
        if not self.edit and not self._menu_open:
            try:
                w, h = self.root.winfo_reqwidth(), self.root.winfo_reqheight()
                x, y = self._target_xy(w, h)
                rect = window_rect(get_hwnd(self.root))
                if rect is None or abs(rect[0] - x) > 2 or abs(rect[1] - y) > 2 or (x, y, w, h) != self._target:
                    self.place()
            except Exception:
                pass
        self.root.after(1000, self._keep_on_top)

    def toggle_edit_topmost(self):
        self.cfg["edit_topmost"] = bool(self.edit_topmost.get())
        save_config(self.cfg)
        self.raise_window()

    def _poll_hotkeys(self):
        for name, vk in (("edit", HOTKEY_EDIT_VK), ("quit", HOTKEY_QUIT_VK)):
            down = hotkey_down(vk)
            if down and not self._hk_prev[name]:  # sadece basılma anında tetikle
                if name == "quit":
                    self.quit()
                    return
                self.toggle_edit()
            self._hk_prev[name] = down
        self.root.after(80, self._poll_hotkeys)

    def quit(self):
        self.cpu_temp.stop()
        self.root.destroy()

    def _popup_menu(self, e):
        self._menu_open = True
        try:
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.menu.grab_release()
            self._menu_open = False

    # ----- PawnIO kurulum akışı -----
    def _ask(self, title, text):
        self.root.attributes("-topmost", False)  # diyalog arkada kalmasın
        try:
            return messagebox.askyesno(title, text, parent=self.root)
        finally:
            self.raise_window()

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

    # ----- Sürükleme (yalnızca düzenleme modunda olay alır) -----
    def _drag_start(self, e):
        self._moved = False
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()

    def _drag_move(self, e):
        self._moved = True
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _drag_end(self, e):
        if self._moved:
            self.cfg[POS_KEY] = [self.root.winfo_x(), self.root.winfo_y()]
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

    def render(self, d):
        o, t = self.overlay, d["cpu_t"]
        # Çubuk kullanım yüzdesini gösterir; renk, yüzde ve sıcaklıktan kötü olana göre seçilir.
        o.set("cpu", f"{d['cpu']:.0f}%" + (f"  {t:.0f}°C" if t is not None else ""),
              d["cpu"], worst(d["cpu"], t))
        o.set("ram", f"{d['ram_used']:.1f}/{d['ram_total']:.0f} GB", d["ram_pct"], d["ram_pct"])
        if d["gpu"] is not None:
            o.set("gpu", f"{d['gpu']}%  {d['gpu_t']}°C", d["gpu"], worst(d["gpu"], d["gpu_t"]))
            o.set("vram", f"{d['vram_used']:.1f}/{d['vram_total']:.0f} GB", d["vram_pct"], d["vram_pct"])
        else:
            o.set("gpu", "—", None, None)
            o.set("vram", "—", None, None)
        if d["disk"] is not None:
            o.set("disk", f"{d['disk']:.0f}%", d["disk"], d["disk"])
        else:
            o.set("disk", "—", None, None)

    def update(self):
        if self.install_result is not None:
            ok, msg = self.install_result
            self.install_result = None
            self._finish_install(ok, msg)
        self.render(self.collect())
        self.root.after(REFRESH_MS, self.update)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    if AUTO_ELEVATE and os.name == "nt" and not is_admin():
        if relaunch_as_admin():
            sys.exit(0)  # yükseltilmiş kopya açıldı, bu kopya kapanır
    App().run()
