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

# ---------------- Ayarlar ----------------
APP_NAME = "UHF Hardware Monitor"
DLL_NAME = "LibreHardwareMonitorLib.dll"
AUTO_ELEVATE = True  # yönetici değilse UAC ile kendini yeniden başlat
DISK_PATH = "C:\\"
REFRESH_MS = 1000
BG, TRACK, FG = "#1e2327", "#3a4148", "#d8dee4"
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
        return TRACK
    return "#4caf50" if pct < 60 else "#ffb300" if pct < 85 else "#e53935"


class Row:
    def __init__(self, parent, label):
        f = tk.Frame(parent, bg=BG)
        f.pack(fill="x", padx=8, pady=2)
        tk.Label(f, text=label, width=6, anchor="w", fg=FG, bg=BG, font=FONT).pack(side="left")
        self.canvas = tk.Canvas(f, width=BAR_W, height=BAR_H, bg=TRACK, highlightthickness=0)
        self.canvas.pack(side="left", padx=4)
        self.bar = self.canvas.create_rectangle(0, 0, 0, BAR_H, width=0, fill=TRACK)
        self.value = tk.Label(f, text="—", width=16, anchor="e", fg=FG, bg=BG, font=FONT)
        self.value.pack(side="left")

    def set(self, pct, text="—"):
        if pct is None:
            self.canvas.coords(self.bar, 0, 0, 0, BAR_H)
            self.value.config(text=text)
            return
        pct = max(0, min(100, pct))
        self.canvas.coords(self.bar, 0, 0, BAR_W * pct / 100, BAR_H)
        self.canvas.itemconfig(self.bar, fill=level_color(pct))
        self.value.config(text=text)


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.configure(bg=BG)
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.92)
        self.root.geometry("+40+40")

        tk.Label(self.root, text=APP_NAME, fg="#7aa2f7", bg=BG,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=8, pady=(6, 0))

        self.rows = {k: Row(self.root, k) for k in
                     ("CPU", "CPU °C", "RAM", "GPU", "VRAM", "GPU °C", "Disk")}
        tk.Frame(self.root, bg=BG, height=4).pack()

        self.cpu_temp = CpuTempReader()
        self.install_result = None  # kurulum thread'inden ana thread'e sonuç
        self.topmost = tk.BooleanVar(value=True)
        if os.name == "nt" and is_admin() and not pawnio_installed():
            self.cpu_temp.status = "PawnIO kurulu değil"
            self.root.after(500, self.offer_pawnio)  # pencere çizildikten sonra sor
        else:
            self.cpu_temp.start()

        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_checkbutton(label="Her zaman üstte", variable=self.topmost,
                                  command=lambda: self.root.attributes("-topmost", self.topmost.get()))
        self.menu.add_command(label="PawnIO sürücüsünü kur…", command=self.menu_install_pawnio)
        self.menu.add_separator()
        self.menu.add_command(label="Çıkış", command=self.quit)

        self.root.bind_all("<ButtonPress-1>", self._drag_start)
        self.root.bind_all("<B1-Motion>", self._drag_move)
        self.root.bind_all("<Button-3>", lambda e: self.menu.tk_popup(e.x_root, e.y_root))

        psutil.cpu_percent()  # ilk çağrı referans noktası
        self.update()

    def quit(self):
        self.cpu_temp.stop()
        self.root.destroy()

    # ----- PawnIO kurulum akışı -----
    def _ask(self, title, text):
        self.root.attributes("-topmost", False)  # diyalog arkada kalmasın
        try:
            return messagebox.askyesno(title, text, parent=self.root)
        finally:
            self.root.attributes("-topmost", self.topmost.get())

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

    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()

    def _drag_move(self, e):
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def update(self):
        if self.install_result is not None:
            ok, msg = self.install_result
            self.install_result = None
            self._finish_install(ok, msg)

        r = self.rows
        cpu = psutil.cpu_percent()
        r["CPU"].set(cpu, f"{cpu:.0f}%")

        t = self.cpu_temp.temp
        if t is not None:
            r["CPU °C"].set(t, f"{t:.0f} °C")
        else:
            r["CPU °C"].set(None, self.cpu_temp.status if self.cpu_temp.status != "ok" else "—")

        vm = psutil.virtual_memory()
        r["RAM"].set(vm.percent, f"{vm.used / 2**30:.1f}/{vm.total / 2**30:.0f} GB")

        try:
            du = psutil.disk_usage(DISK_PATH)
            r["Disk"].set(du.percent, f"{du.percent:.0f}%")
        except Exception:
            r["Disk"].set(None)

        if HAS_NVML:
            try:
                util = pynvml.nvmlDeviceGetUtilizationRates(GPU_HANDLE).gpu
                mem = pynvml.nvmlDeviceGetMemoryInfo(GPU_HANDLE)
                temp = pynvml.nvmlDeviceGetTemperature(GPU_HANDLE, pynvml.NVML_TEMPERATURE_GPU)
                r["GPU"].set(util, f"{util}%")
                r["VRAM"].set(mem.used / mem.total * 100,
                              f"{mem.used / 2**30:.1f}/{mem.total / 2**30:.0f} GB")
                r["GPU °C"].set(temp, f"{temp} °C")
            except Exception:
                for k in ("GPU", "VRAM", "GPU °C"):
                    r[k].set(None)
        else:
            for k in ("GPU", "VRAM", "GPU °C"):
                r[k].set(None)

        self.root.after(REFRESH_MS, self.update)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    if AUTO_ELEVATE and os.name == "nt" and not is_admin():
        if relaunch_as_admin():
            sys.exit(0)  # yükseltilmiş kopya açıldı, bu kopya kapanır
    App().run()
