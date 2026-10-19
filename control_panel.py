"""iPhone Mirror — một cửa sổ: máy thu AirPlay (Wi-Fi / cáp USB qua Personal Hotspot) + chuột Bluetooth BLE + bảng điều khiển.
Giao diện kính lỏng iOS 27 theo bản mẫu Figma (uHDGDHJ1xb1KSqPnirkCTT), vẽ bằng liquid_glass.GlassUI.
Bản giao diện cũ của Gemini: control_panel_gemini_v1.py.

Chuột trái trên màn hình iPhone: chạm / kéo.  Lăn chuột: cuộn.  Chuột phải: nút phụ AssistiveTouch.  Esc: ẩn/hiện bảng điều khiển.
Chạy:  python control_panel.py [--self-test]
"""
import argparse
import ctypes
import ctypes.wintypes
import json
import os
import subprocess
import sys
import threading
import time
import tkinter as tk

from PIL import Image, ImageTk

import bt_driver
from airplay_mirror import AirPlayServer, AirPlayDecoder, get_local_ips, protect_pc_internet
from liquid_glass import GlassUI, GREEN, ORANGE

HERE = os.path.dirname(os.path.abspath(__file__))
IOS = os.path.join(HERE, "ios.exe")
BLE = "usb:8087:0033"
KEYS = os.path.join(HERE, "bumble_mouse_keys.json")  # có file này = "Mirror Mouse" đã ghép đôi với iPhone
ICONS_DIR = os.path.join(HERE, "icons")
PNG_1024_PATH = os.path.join(ICONS_DIR, "app_icon_1024.png")
ICO_PATH = os.path.join(ICONS_DIR, "app_icon.ico")
BG = "#0f1218"
# Độ phân giải gốc (rộng × cao, điểm ảnh) theo ProductType
MODELS = {
    "iPhone10,3": ("iPhone X", 1125, 2436), "iPhone11,2": ("iPhone XS", 1125, 2436),
    "iPhone11,6": ("iPhone XS Max", 1242, 2688), "iPhone11,8": ("iPhone XR", 828, 1792),
    "iPhone12,1": ("iPhone 11", 828, 1792), "iPhone12,3": ("iPhone 11 Pro", 1125, 2436),
    "iPhone12,5": ("iPhone 11 Pro Max", 1242, 2688), "iPhone12,8": ("iPhone SE 2", 750, 1334),
    "iPhone13,1": ("iPhone 12 mini", 1080, 2340), "iPhone13,2": ("iPhone 12", 1170, 2532),
    "iPhone13,3": ("iPhone 12 Pro", 1170, 2532), "iPhone13,4": ("iPhone 12 Pro Max", 1284, 2778),
    "iPhone14,4": ("iPhone 13 mini", 1080, 2340), "iPhone14,5": ("iPhone 13", 1170, 2532),
    "iPhone14,2": ("iPhone 13 Pro", 1170, 2532), "iPhone14,3": ("iPhone 13 Pro Max", 1284, 2778),
    "iPhone14,6": ("iPhone SE 3", 750, 1334), "iPhone14,7": ("iPhone 14", 1170, 2532),
    "iPhone14,8": ("iPhone 14 Plus", 1284, 2778), "iPhone15,2": ("iPhone 14 Pro", 1179, 2556),
    "iPhone15,3": ("iPhone 14 Pro Max", 1290, 2796), "iPhone15,4": ("iPhone 15", 1179, 2556),
    "iPhone15,5": ("iPhone 15 Plus", 1290, 2796), "iPhone16,1": ("iPhone 15 Pro", 1179, 2556),
    "iPhone16,2": ("iPhone 15 Pro Max", 1290, 2796), "iPhone17,3": ("iPhone 16", 1179, 2556),
    "iPhone17,4": ("iPhone 16 Plus", 1290, 2796), "iPhone17,1": ("iPhone 16 Pro", 1206, 2622),
    "iPhone17,2": ("iPhone 16 Pro Max", 1320, 2868), "iPhone17,5": ("iPhone 16e", 1170, 2532),
}
DEFAULT_MODEL = "iPhone12,5"
RES_LONG = {"720": 1280, "1080": 1920}  # cạnh dài khung khai cho iPhone; "max" = cạnh dài gốc của máy

# Đặt AppUserModelID để Windows Taskbar hiển thị đúng icon ứng dụng thay vì icon Python mặc định
try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("apple.iphonemirror.studio.27")
except Exception:
    pass


def apply_dark_titlebar(window):
    """Bật chế độ Dark Mode cho thanh tiêu đề Windows 11/10."""
    try:
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        val = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(val), ctypes.sizeof(val))  # DWMWA_USE_IMMERSIVE_DARK_MODE
    except Exception:
        pass


def iphone_usb():
    """Kiểm tra iPhone kết nối qua cáp USB bằng ios.exe."""
    try:
        out = subprocess.run([IOS, "list", "--details"], capture_output=True, text=True, timeout=5,
                             creationflags=bt_driver.NO_WINDOW).stdout
        devs = json.loads(out.strip().splitlines()[-1]).get("deviceList", [])
        return next((d for d in devs if d.get("ConnectionType") == "USB"), None)
    except Exception:
        return None


def check_hotspot_ip():
    """Kiểm tra IP mạng Personal Hotspot qua USB (172.20.10.x)."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue).IPAddress"],
            capture_output=True, text=True, timeout=2, creationflags=bt_driver.NO_WINDOW
        ).stdout
        for line in out.splitlines():
            ip = line.strip()
            if ip.startswith("172.20.10."):
                return ip
    except Exception:
        pass
    return None


def fit(fw, fh, box):
    """Khung (x0, y0, w, h) giữ tỉ lệ fw:fh, căn giữa trong box."""
    x0, y0, x1, y1 = box
    s = min((x1 - x0) / fw, (y1 - y0) / fh)
    w, h = max(1, int(fw * s)), max(1, int(fh * s))
    return x0 + (x1 - x0 - w) // 2, y0 + (y1 - y0 - h) // 2, w, h


class UnifiedMirrorApp:
    """Máy thu AirPlay + chuột BLE + bảng điều khiển kính lỏng trong một cửa sổ."""

    def __init__(self, root):
        self.root = root
        root.title("iPhone Mirror")
        wa = ctypes.wintypes.RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(wa), 0)  # SPI_GETWORKAREA: màn hình trừ taskbar
        aw, ah = wa.right - wa.left, wa.bottom - wa.top
        w, h = min(1440, aw - 40), min(900, ah - 50)  # 50: thanh tiêu đề + lề
        root.geometry(f"{w}x{h}+{wa.left + (aw - w) // 2}+{wa.top + max(0, (ah - h - 40) // 2)}")
        root.minsize(1100, 800)
        root.configure(bg=BG)

        self.mode = self.hotspot_ip = self.usb_dev = self.ble = None
        self.lan_ip = None
        self.is_wired = False
        self.last_client_ip = None
        self.message = None

        # Giải mã video AirPlay
        self.dec = AirPlayDecoder()
        self.frame_size = None
        self.video_rect = None  # (x, y, w, h) vùng video thật trên canvas, để đổi toạ độ chuột
        self.last_frame_t = 0.0
        self.t_fps, self.n_fps, self.current_fps = time.perf_counter(), 0, 0

        # Âm thanh (mặc định: Loa máy tính)
        self.audio_mode = "pc"  # "pc", "iphone", "mute"
        self.audio_volume = 1.0
        self.res = "max"  # "720", "1080", "max"
        self.model = None  # ProductType người dùng chọn; None = tự nhận

        self.server = AirPlayServer(port=7000, on_frame=self.dec.feed, on_config=None,
                                    on_connect=self._on_airplay_connect, audio_mode=self.audio_mode)

        self.apply_resolution(quiet=True)
        self._apply_app_icon()
        self.icon_img = Image.open(PNG_1024_PATH) if os.path.exists(PNG_1024_PATH) else None
        self.ui = GlassUI(w, h, app_icon=self.icon_img)  # nền + kính tĩnh, ~1.3 giây
        self._rebuild_job = None
        self._redraw_pending = False
        self.dragging_volume = False

        self.canvas = tk.Canvas(root, highlightthickness=0, bg=BG)
        self.canvas.pack(fill="both", expand=True)
        self.chrome_item = self.canvas.create_image(0, 0, anchor="nw")
        self.video_item = self.canvas.create_image(0, 0, anchor="nw", state="hidden")
        c = self.canvas
        c.bind("<Configure>", self._on_resize)
        c.bind("<Motion>", self._on_mouse_move)
        c.bind("<B1-Motion>", self._on_drag)
        c.bind("<ButtonPress-1>", self._on_mouse_down)
        c.bind("<ButtonRelease-1>", self._on_mouse_up)
        c.bind("<ButtonPress-3>", lambda e: self._ble_button(e, 2))
        c.bind("<ButtonRelease-3>", lambda e: self._ble_button(e, 0))
        c.bind("<MouseWheel>", self._on_mouse_wheel)
        apply_dark_titlebar(root)

        self.redraw()
        self.refresh_devices()
        self.running = True
        self._render_loop()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.bind("<Escape>", lambda _e: self.toggle_sidebar())

    def _apply_app_icon(self):
        """Gắn biểu tượng Liquid Glass vào cửa sổ Tkinter và thanh Taskbar."""
        if not os.path.exists(ICO_PATH) or not os.path.exists(PNG_1024_PATH):
            try:
                import make_icon
                make_icon.export_icons()
            except Exception:
                pass
        try:
            if os.path.exists(ICO_PATH):
                self.root.iconbitmap(default=ICO_PATH)
        except Exception:
            pass
        try:
            if os.path.exists(PNG_1024_PATH):
                self._app_icon_photo = ImageTk.PhotoImage(file=PNG_1024_PATH)
                self.root.iconphoto(True, self._app_icon_photo)
        except Exception:
            pass

    # ---------- trạng thái -> giao diện ----------
    def playing(self):
        return self.dec.frames > 0 and time.perf_counter() - self.last_frame_t < 3

    def state(self):
        playing = self.playing()
        name, mw, mh = MODELS[self.current_model()]
        st = {"playing": playing, "audio": self.audio_mode, "volume": round(self.audio_volume * 100),
              "message": self.message, "receiver_name": self.server.name, "res": self.res,
              "model_line": f"{name} · {mw}×{mh}" + ("" if self.model else " (tự nhận)")}
        dev = self.usb_dev or {}
        device = f"{dev.get('ProductType', 'iPhone')} · iOS {dev['ProductVersion']}" if dev.get("ProductVersion") else "iPhone"
        if playing:
            via = "cáp USB" if self.is_wired else "Wi‑Fi"
            fw, fh = self.frame_size or (0, 0)
            st.update(status=(f"Đang phát qua {via}", GREEN), via_usb=self.is_wired, link_title=f"Đang phát qua {via}",
                      link_lines=[f"AirPlay · {self.last_client_ip}", device],
                      stats=[(str(self.current_fps), "khung/giây"), (f"{fw}×{fh}", "độ phân giải"), ("H.264", "mã hoá")])
        else:
            ip = self.hotspot_ip or self.lan_ip or "…"
            st.update(status=("Đang chờ iPhone", ORANGE), via_usb=bool(self.hotspot_ip),
                      link_title="Sẵn sàng qua cáp USB" if self.hotspot_ip else "Sẵn sàng qua Wi‑Fi",
                      link_lines=[f"Máy thu: {self.server.name}", f"IP {ip}" + (" · iPhone đang cắm cáp" if dev else "")],
                      receiver_line=f"Máy thu AirPlay “{self.server.name}” đang chờ trên mạng {ip}")
        ble_on = self.mode == "winusb"
        paired = os.path.exists(KEYS)
        st.update(ble_on=ble_on, paired=("Đã ghép đôi", True) if paired else ("Chưa ghép đôi", False),
                  ble_sub={"winusb": "Đang dùng card AX211 · Bluetooth Windows tạm tắt",
                           "intel": "Đang tắt · card đang dùng cho Windows",
                           None: "Đang kiểm tra card Bluetooth…"}.get(self.mode, "Không thấy card Bluetooth"),
                  ble_pill=("Chuột Bluetooth: Mirror Mouse đã ghép đôi · sẵn sàng" if ble_on and paired else
                            "Chuột Bluetooth: đang tắt" if not ble_on else "Chuột Bluetooth: chưa ghép đôi", ble_on and paired))
        return st

    def redraw(self):
        """Gom các yêu cầu vẽ lại trong cùng một vòng sự kiện."""
        if not self._redraw_pending:
            self._redraw_pending = True
            self.root.after_idle(self._redraw_now)

    def _redraw_now(self, sync=False):
        """Vẽ giao diện ở luồng nền (~40 ms) để video không khựng; bỏ qua nếu trạng thái không đổi."""
        self._redraw_pending = False
        st, ui = self.state(), self.ui
        key = (repr(st), id(ui))
        if key == getattr(self, "_last_key", None):
            return
        if getattr(self, "_rendering", False):  # đang vẽ dở: vẽ lại khi xong
            self._redraw_pending = True
            self.root.after(20, self._redraw_now)
            return
        self._rendering, self._last_key = True, key

        def work():
            chrome = ui.render(st)
            if sync:
                self._apply_chrome(chrome, ui, st)
            else:
                self.root.after(0, self._apply_chrome, chrome, ui, st)
        work() if sync else threading.Thread(target=work, daemon=True).start()

    def _apply_chrome(self, chrome, ui, st):
        self._rendering = False
        if ui is not self.ui:
            return self.redraw()
        self.chrome = chrome
        self.screen_bg = chrome.crop(ui.screen)  # nền sau màn hình iPhone: lấp các góc bo của video
        photo = getattr(self, "chrome_photo", None)
        if photo and (photo.width(), photo.height()) == chrome.size:
            photo.paste(chrome)  # dùng lại ảnh Tk: rẻ hơn tạo mới
        else:
            self.chrome_photo = ImageTk.PhotoImage(chrome)
            self.canvas.itemconfig(self.chrome_item, image=self.chrome_photo)
        self.canvas.itemconfig(self.video_item, state="normal" if st["playing"] else "hidden")

    def _on_resize(self, e):
        if (e.width, e.height) != self.ui.size and e.width > 100:
            if self._rebuild_job:
                self.root.after_cancel(self._rebuild_job)
            self._rebuild_job = self.root.after(250, self._rebuild, e.width, e.height, self.ui.sidebar)

    def _rebuild(self, w, h, sidebar):
        """Vẽ lại nền kính ở luồng nền (~1 giây) rồi đổi sang, để cửa sổ không bị đứng."""
        def work():
            ui = GlassUI(w, h, app_icon=self.icon_img, sidebar=sidebar)
            self.root.after(0, self._swap_ui, ui)
        threading.Thread(target=work, daemon=True).start()

    def _swap_ui(self, ui):
        self.ui = ui
        self.redraw()

    def toggle_sidebar(self):
        self._rebuild(*self.ui.size, not self.ui.sidebar)

    # ---------- sự kiện AirPlay / thiết bị ----------
    def _on_airplay_connect(self, client_ip):
        """iPhone bắt đầu phiên AirPlay (gọi từ luồng máy chủ)."""
        self.last_client_ip = client_ip
        self.is_wired = client_ip.startswith("172.20.10.")
        self.message = None
        self.root.after(0, self.redraw)

    def disconnect(self):
        """Đóng mọi phiên AirPlay đang mở; máy thu vẫn chờ kết nối mới."""
        with self.server.lock:
            for s in list(self.server.sessions.values()):
                s.close()
            self.server.sessions.clear()
        self.last_frame_t = 0
        self.message = "Đã ngắt kết nối. Mở lại Phản chiếu màn hình trên iPhone để phát tiếp."
        self.redraw()

    def select_audio_mode(self, mode):
        self.audio_mode = mode
        self.server.set_audio_mode(mode)
        self.message = {"pc": "Âm thanh iPhone phát qua loa máy tính.",
                        "iphone": "Âm thanh phát trên loa iPhone (AirPlay chỉ chiếu hình).",
                        "mute": "Đã tắt tiếng."}[mode]
        self.redraw()

    def set_volume(self, vol):
        self.audio_volume = max(0.0, min(1.0, vol))
        self.server.set_volume(self.audio_volume)
        self.redraw()

    def current_model(self):
        for m in (self.model, self.server.client_model, (self.usb_dev or {}).get("ProductType")):
            if m in MODELS:
                return m
        return DEFAULT_MODEL

    def apply_resolution(self, quiet=False):
        """Khai cho iPhone một khung vuông theo cạnh dài: iPhone dọc hay ngang đều được độ nét tối đa của mức đã chọn."""
        long_side = RES_LONG.get(self.res) or MODELS[self.current_model()][2]
        if self.server.display_size != (long_side, long_side):
            self.server.display_size = (long_side, long_side)
            if not quiet:
                self.message = f"Độ phân giải mới (cạnh dài {long_side}px): tắt rồi bật lại Phản chiếu màn hình trên iPhone để áp dụng."
        self.redraw() if not quiet else None

    def model_menu(self, x, y):
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_radiobutton(label="Tự nhận", command=lambda: self.set_model(None))
        for pt, (name, w, h) in MODELS.items():
            menu.add_radiobutton(label=f"{name}  ({w}×{h})", command=lambda pt=pt: self.set_model(pt))
        menu.tk_popup(self.root.winfo_rootx() + int(x), self.root.winfo_rooty() + int(y))

    def set_model(self, pt):
        self.model = pt
        self.apply_resolution()

    def refresh_devices(self):
        """Kiểm tra Bluetooth, iPhone qua cáp và mạng Hotspot trong luồng nền."""
        threading.Thread(target=self._poll_status, daemon=True).start()

    def _poll_status(self):
        mode = bt_driver.status()
        hotspot_ip = check_hotspot_ip()
        dev = iphone_usb()
        ips = [ip for ip in get_local_ips() if not ip.startswith(("169.254.", "100."))]
        if hotspot_ip:
            protect_pc_internet()
        self.root.after(0, self._update_device_ui, mode, hotspot_ip, dev, ips[0] if ips else None)

    def _update_device_ui(self, mode, hotspot_ip, dev, lan_ip):
        if not self.running:
            return
        self.mode, self.hotspot_ip, self.usb_dev, self.lan_ip = mode, hotspot_ip, dev, lan_ip
        self.apply_resolution(quiet=True)  # đời máy tự nhận có thể vừa đổi
        if mode == "winusb" and self.ble is None:
            try:
                from iphone_mirror import BleInput
                self.ble = BleInput(BLE)
            except Exception as ex:
                self.message = f"Không mở được chuột BLE: {ex}"
        elif mode != "winusb" and self.ble is not None:
            self.ble.close()
            self.ble = None
        self.redraw()
        self.root.after(2500, self.refresh_devices)

    def switch_bt(self, target_mode):
        """Chuyển driver card Bluetooth AX211 (Windows hỏi quyền Admin)."""
        if self.ble:
            self.ble.close()
            self.ble = None
        self.message = f"Đang đổi card Bluetooth sang {'chuột iPhone' if target_mode == 'winusb' else 'Windows'} — bấm Yes khi Windows hỏi quyền Admin."
        self.redraw()
        threading.Thread(target=lambda: [bt_driver.switch(target_mode), self.root.after(1000, self.refresh_devices)], daemon=True).start()

    # ---------- chuột: nút giao diện trước, còn lại gửi sang iPhone ----------
    def _iphone_xy(self, e):
        if not (self.playing() and self.video_rect):
            return None
        x, y, w, h = self.video_rect
        fx, fy = (e.x - x) / w, (e.y - y) / h
        return (fx, fy) if 0 <= fx <= 1 and 0 <= fy <= 1 else None

    def _ble_send(self, e, **kw):
        if self.ble and self.mode == "winusb":
            p = self._iphone_xy(e)
            if p:
                self.ble.update(fx=p[0], fy=p[1], **kw)

    def _on_mouse_move(self, e):
        self.canvas.config(cursor="hand2" if self.ui.hit(e.x, e.y) else "")
        self._ble_send(e)

    def _on_drag(self, e):
        if self.dragging_volume:
            self._volume_from_x(e.x)
        else:
            self._ble_send(e)

    def _on_mouse_down(self, e):
        name = self.ui.hit(e.x, e.y)
        if name is None:
            self._ble_send(e, buttons=1)
        elif name == "volume":
            self.dragging_volume = True
            self._volume_from_x(e.x)
        elif name == "toggle_sidebar":
            self.toggle_sidebar()
        elif name == "disconnect":
            self.disconnect()
        elif name == "ble_toggle":
            if self.mode in ("winusb", "intel"):
                self.switch_bt("intel" if self.mode == "winusb" else "winusb")
        elif name.startswith("audio_"):
            self.select_audio_mode(name[6:])
        elif name.startswith("res_"):
            self.res = name[4:]
            self.apply_resolution()
        elif name == "model_menu":
            self.model_menu(e.x, e.y)

    def _on_mouse_up(self, e):
        if self.dragging_volume:
            self.dragging_volume = False
        else:
            self._ble_send(e, buttons=0)

    def _ble_button(self, e, buttons):
        if self.ui.hit(e.x, e.y) is None:
            self._ble_send(e, buttons=buttons)

    def _on_mouse_wheel(self, e):
        self._ble_send(e, wheel=2 * round(e.delta / 120))

    def _volume_from_x(self, x):
        t0, t1 = self.ui.volume_track
        self.set_volume((x - t0) / (t1 - t0))

    # ---------- video ----------
    def _render_loop(self):
        frame, self.dec.latest = self.dec.latest, None
        if frame is not None:
            was_playing = self.playing()
            self.last_frame_t = time.perf_counter()
            if (frame.width, frame.height) != self.frame_size or not was_playing:
                self.frame_size = (frame.width, frame.height)
                self.redraw()
            sx0, sy0, sx1, sy1 = self.ui.screen
            x, y, w, h = fit(frame.width, frame.height, self.ui.screen)  # iPhone xoay ngang thì có viền đen
            self.video_rect = (x, y, w, h)
            img = frame.to_image(width=w, height=h)
            size = (sx1 - sx0, sy1 - sy0)
            bg = getattr(self, "screen_bg", None)
            out = bg.copy() if bg is not None and bg.size == size else Image.new("RGB", size)
            if (w, h) != size:  # iPhone xoay ngang: nền đen trong màn hình
                out.paste((0, 0, 0), (0, 0, size[0], size[1]), self.ui.screen_mask)
            out.paste(img, (x - sx0, y - sy0), self.ui.screen_mask.crop((x - sx0, y - sy0, x - sx0 + w, y - sy0 + h)))
            photo = getattr(self, "video_photo", None)
            if photo and (photo.width(), photo.height()) == size:
                photo.paste(out)  # dùng lại ảnh Tk thay vì tạo mới mỗi khung
            else:
                self.video_photo = ImageTk.PhotoImage(out)
                self.canvas.itemconfig(self.video_item, image=self.video_photo)
                self.canvas.coords(self.video_item, sx0, sy0)
            self.n_fps += 1
        now = time.perf_counter()
        if now - self.t_fps >= 1.0:
            self.current_fps, self.t_fps, self.n_fps = self.n_fps, now, 0
            self.redraw()  # cập nhật FPS, và chuyển về màn hình chờ khi iPhone ngừng phát
        if self.running:
            self.root.after(5, self._render_loop)

    def on_close(self):
        """Dọn dẹp và thoát ứng dụng sạch sẽ."""
        self.running = False
        try:
            self.server.close()
        except Exception:
            pass
        if self.ble:
            try:
                self.ble.close()
            except Exception:
                pass
        self.root.destroy()


def self_test():
    print("--- self-test control_panel.py ---", flush=True)
    _, _, w, h = fit(1242, 2688, (0, 0, 323, 700))
    assert w == 323 and 699 <= h <= 700
    assert fit(2688, 1242, (0, 0, 323, 700))[1] > 0  # iPhone xoay ngang: căn giữa theo chiều dọc
    root = tk.Tk()
    root.withdraw()
    app = UnifiedMirrorApp(root)
    root.update()
    app._last_key, app._rendering = None, False
    app._redraw_now(sync=True)
    ui = app.ui
    hit = lambda name: next(((b[0] + b[2]) / 2 for n, b in ui.hits if n == name), None)
    for mode in ("iphone", "mute", "pc"):
        box = next(b for n, b in ui.hits if n == "audio_" + mode)
        app._on_mouse_down(type("E", (), {"x": (box[0] + box[2]) / 2, "y": (box[1] + box[3]) / 2})())
        assert app.audio_mode == mode, mode
    t0, t1 = ui.volume_track
    vb = next(b for n, b in ui.hits if n == "volume")
    app._on_mouse_down(type("E", (), {"x": t0 + (t1 - t0) * 0.25, "y": (vb[1] + vb[3]) / 2})())
    app._on_mouse_up(None)
    assert abs(app.audio_volume - 0.25) < 0.02 and hit("toggle_sidebar")
    app.res = "1080"; app.apply_resolution()
    assert app.server.display_size == (1920, 1920)
    app.set_model("iPhone17,2"); app.res = "max"; app.apply_resolution()
    assert app.server.display_size == (2868, 2868) and "16 Pro Max" in app.state()["model_line"]
    st = app.state()
    assert st["status"][1] == ORANGE and not st["playing"]
    assert os.path.exists(PNG_1024_PATH) and os.path.exists(ICO_PATH)
    app.on_close()
    print("self-test OK")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="iPhone Mirror — AirPlay + chuột Bluetooth, giao diện kính lỏng")
    parser.add_argument("--self-test", action="store_true", help="chạy kiểm tra tự động")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        sys.exit(0)
    root = tk.Tk()
    UnifiedMirrorApp(root)
    root.mainloop()
