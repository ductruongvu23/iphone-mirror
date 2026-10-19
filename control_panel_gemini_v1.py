"""Giao diện điều khiển hợp nhất iPhone Studio — Chuẩn Apple iOS & iPadOS Design Language.
Thiết kế theo UI Kit chính thức của Apple (iOS / iPadOS):
  - Phong cách Inset Grouped Settings: Tối giản, thanh lịch, sang trọng và chuẩn mực Apple.
  - Bảng màu iOS Dark Mode: System Background (#000000), Secondary/Tertiary Grouped (#1c1c1e, #2c2c2e).
  - Apple SF Rounded Accent Icons (Xanh Blue #0a84ff, Xanh lá #30d158, Cam #ff9f0a, Tím #5e5ce6).
  - Native iOS Segmented Control & Control Center Volume Slider.
  - Mockup iPhone viền mỏng Dynamic Island thanh lịch mang phong cách Apple Setup Assistant.
  - Trình phát AirPlay 60 FPS độc lập (Cáp USB Hotspot 172.20.10.x & Wi-Fi).
  - Chuột ảo Bluetooth HID tuyệt đối (Bumble trên card Intel AX211, driver WinUSB).
  - Hỗ trợ phím tắt ESC để thu gọn/mở rộng sidebar.

Chạy:  python control_panel.py [--self-test]
"""
import argparse
import ctypes
from ctypes import wintypes
import json
import os
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk

import bt_driver
from airplay_mirror import (
    AirPlayServer,
    AirPlayDecoder,
    get_local_ips,
    protect_pc_internet
)

HERE = os.path.dirname(os.path.abspath(__file__))
IOS = os.path.join(HERE, "ios.exe")
BLE = "usb:8087:0033"
ICONS_DIR = os.path.join(HERE, "icons")
PNG_1024_PATH = os.path.join(ICONS_DIR, "app_icon_1024.png")
ICO_PATH = os.path.join(ICONS_DIR, "app_icon.ico")

# Đặt AppUserModelID để Windows Taskbar hiển thị đúng icon ứng dụng thay vì icon Python mặc định
try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("apple.iphonemirror.studio.27")
except Exception:
    pass

# =========================================================================
# BẢNG MÀU CHUẨN APPLE iOS & iPadOS (DARK MODE)
# =========================================================================
IOS_BG_BASE = "#000000"           # System Background (Đen tuyền OLED)
IOS_BG_SECONDARY = "#1c1c1e"      # Secondary System Background (Sidebar / Inset Group)
IOS_BG_TERTIARY = "#2c2c2e"       # Tertiary System Background (Card / Row)
IOS_BG_ELEVATED = "#3a3a3c"       # Elevated / Active state
IOS_SEPARATOR = "#38383a"         # Đường phân cách System Separator

# Apple System Colors
IOS_BLUE = "#0a84ff"              # System Blue
IOS_BLUE_HOVER = "#0071e3"
IOS_GREEN = "#30d158"             # System Green (Active / Success)
IOS_ORANGE = "#ff9f0a"            # System Orange (Audio / Warning)
IOS_RED = "#ff453a"               # System Red
IOS_PURPLE = "#af52de"            # System Purple
IOS_INDIGO = "#5e5ce6"            # System Indigo

# Typography
IOS_LABEL_PRIMARY = "#ffffff"     # White 100%
IOS_LABEL_SECONDARY = "#8e8e93"   # System Gray (Subheadline)
IOS_LABEL_TERTIARY = "#636366"    # Darker Gray (Footnote)


def apply_dark_titlebar(window):
    """Bật chế độ Dark Mode cho thanh tiêu đề Windows 11/10."""
    try:
        window.update_idletasks()
        hwnd = ctypes.windll.user32.FindWindowW(None, window.title())
        if hwnd:
            # DWMWA_USE_IMMERSIVE_DARK_MODE = 20
            val = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(val), ctypes.sizeof(val))
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


class IOSButton(tk.Canvas):
    """Nút bấm chuẩn phong cách Apple iOS (Filled hoặc Tinted) với góc bo cong mượt mà."""

    def __init__(self, parent, text, command=None, style="filled", width=140, height=36,
                 bg_color=None, fg_color=None, font=("Segoe UI", 9, "bold"), **kw):
        super().__init__(parent, width=width, height=height, bg=parent["bg"],
                         highlightthickness=0, cursor="hand2", **kw)
        self.command = command
        self.text = text
        self.style = style  # "filled", "tinted", "secondary"
        self.btn_w = width
        self.btn_h = height
        self.font = font
        self.is_enabled = True
        self.is_hovered = False

        if style == "filled":
            self.idle_bg = bg_color or IOS_BLUE
            self.hover_bg = IOS_BLUE_HOVER
            self.text_color = fg_color or "#ffffff"
        elif style == "tinted":
            self.idle_bg = bg_color or "#2c2c2e"
            self.hover_bg = "#3a3a3c"
            self.text_color = fg_color or IOS_BLUE
        else:
            self.idle_bg = bg_color or "#2c2c2e"
            self.hover_bg = "#3a3a3c"
            self.text_color = fg_color or "#ffffff"

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)

        self._redraw()

    def _draw_pill(self, x1, y1, x2, y2, r, fill, outline=""):
        points = [
            x1 + r, y1,
            x2 - r, y1,
            x2, y1,
            x2, y1 + r,
            x2, y2 - r,
            x2, y2,
            x2 - r, y2,
            x1 + r, y2,
            x1, y2,
            x1, y2 - r,
            x1, y1 + r,
            x1, y1
        ]
        return self.create_polygon(points, smooth=True, fill=fill, outline=outline)

    def _redraw(self):
        self.delete("all")
        w, h = self.btn_w, self.btn_h
        r = min(10, h // 2)

        if not self.is_enabled:
            fill = "#242426"
            txt_color = IOS_LABEL_TERTIARY
            self.configure(cursor="arrow")
        else:
            fill = self.hover_bg if self.is_hovered else self.idle_bg
            txt_color = self.text_color
            self.configure(cursor="hand2")

        self._draw_pill(2, 2, w - 2, h - 2, r, fill=fill)
        self.create_text(w // 2, h // 2, text=self.text, font=self.font, fill=txt_color)

    def _on_enter(self, _e):
        if self.is_enabled:
            self.is_hovered = True
            self._redraw()

    def _on_leave(self, _e):
        if self.is_enabled:
            self.is_hovered = False
            self._redraw()

    def _on_click(self, _e):
        if self.is_enabled and self.command:
            self.command()

    def set_enabled(self, enabled):
        self.is_enabled = enabled
        self._redraw()

    def set_text(self, text):
        self.text = text
        self._redraw()

    def set_colors(self, idle_bg, hover_bg, text_color):
        self.idle_bg = idle_bg
        self.hover_bg = hover_bg
        self.text_color = text_color
        self._redraw()


class IOSSegmentedControl(tk.Frame):
    """Bộ chọn Segmented Control chuẩn Apple iOS: Các tab nằm gọn trong thanh viên thuốc."""

    def __init__(self, parent, options, on_change=None, initial="pc", **kw):
        super().__init__(parent, bg="#2c2c2e", padx=3, pady=3, **kw)
        self.options = options  # [(val, label), ...]
        self.on_change = on_change
        self.selected = initial
        self.buttons = {}

        for val, label in options:
            btn = tk.Label(self, text=label, font=("Segoe UI", 8, "bold"),
                           padx=10, pady=5, cursor="hand2")
            btn.pack(side="left", expand=True, fill="x", padx=1)
            btn.bind("<Button-1>", lambda _e, v=val: self._select(v))
            self.buttons[val] = btn

        self._update_styles()

    def _select(self, val):
        if self.selected != val:
            self.selected = val
            self._update_styles()
            if self.on_change:
                self.on_change(val)

    def _update_styles(self):
        for val, btn in self.buttons.items():
            if val == self.selected:
                # Tab được chọn phong cách iOS (nổi bật với nền sáng hơn)
                btn.configure(bg="#636366", fg="#ffffff")
            else:
                btn.configure(bg="#2c2c2e", fg=IOS_LABEL_SECONDARY)

    def set_selected(self, val):
        self.selected = val
        self._update_styles()


class UnifiedMirrorApp:
    """Ứng dụng hợp nhất: Bảng điều khiển Studio + Máy thu AirPlay 60 FPS + Chuột BLE chuẩn Apple HIG."""

    def __init__(self, root):
        self.root = root
        self.root.title("iPhone Studio — Apple iOS Design")
        self.root.geometry("1100x860")
        self.root.minsize(800, 560)
        self.root.configure(bg=IOS_BG_BASE)

        self.sidebar_visible = True
        self.mode = None
        self.hotspot_ip = None
        self.usb_dev = None
        self.ble = None
        self.is_wired = False
        self.last_client_ip = None

        # Thiết lập giải mã video AirPlay
        self.dec = AirPlayDecoder()
        self.photo = None
        self.frame_size = (0, 0)
        self.dw = 0
        self.dh = 0
        self.offset_x = 0
        self.offset_y = 0
        self.t_fps = time.perf_counter()
        self.n_fps = 0
        self.current_fps = 0

        # Cài đặt âm thanh (mặc định: Loa máy tính)
        self.audio_mode = "pc"  # "pc", "iphone", "mute"
        self.audio_volume = 1.0

        # Khởi động máy chủ AirPlay tích hợp
        self.server = AirPlayServer(
            port=7000,
            on_frame=self.dec.feed,
            on_config=None,
            on_connect=self._on_airplay_connect,
            audio_mode=self.audio_mode
        )

        self._apply_app_icon()
        self._build_ui()
        apply_dark_titlebar(self.root)

        # Polling trạng thái thiết bị & USB
        self.refresh_devices()

        # Vòng lặp cập nhật khung hình video
        self.running = True
        self._render_loop()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.bind("<Escape>", lambda _e: self.toggle_sidebar())

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

    def _build_ui(self):
        # Container chính phân chia Sidebar (Trái) và Viewport màn hình (Phải)
        self.main_container = tk.Frame(self.root, bg=IOS_BG_BASE)
        self.main_container.pack(fill="both", expand=True)

        # --- 1. SIDEBAR ĐIỀU KHIỂN (PHONG CÁCH APPLE INSET GROUPED) ---
        self.sidebar = tk.Frame(self.main_container, bg=IOS_BG_SECONDARY, width=360, padx=16, pady=16)
        self.sidebar.pack(side="left", fill="y", expand=False)
        self.sidebar.pack_propagate(False)

        # Header Sidebar dạng Navigation Bar
        head_row = tk.Frame(self.sidebar, bg=IOS_BG_SECONDARY)
        head_row.pack(fill="x", pady=(0, 6))

        # Icon ứng dụng Liquid Glass squircle ở đầu header (khớp bản mẫu Figma)
        if os.path.exists(PNG_1024_PATH):
            try:
                icon_pil = Image.open(PNG_1024_PATH).resize((38, 38), Image.Resampling.LANCZOS)
                self.header_icon_img = ImageTk.PhotoImage(icon_pil)
                lbl_icon = tk.Label(head_row, image=self.header_icon_img, bg=IOS_BG_SECONDARY)
                lbl_icon.pack(side="left", padx=(0, 10))
            except Exception:
                pass

        logo_box = tk.Frame(head_row, bg=IOS_BG_SECONDARY)
        logo_box.pack(side="left")

        tk.Label(logo_box, text="iPhone Studio", font=("Segoe UI", 13, "bold"),
                 bg=IOS_BG_SECONDARY, fg=IOS_LABEL_PRIMARY).pack(anchor="w")

        tk.Label(logo_box, text="Apple Ecosystem Mirror & Control", font=("Segoe UI", 7),
                 bg=IOS_BG_SECONDARY, fg=IOS_LABEL_SECONDARY).pack(anchor="w")

        self.btn_hide_sidebar = IOSButton(head_row, text="◀ Thu gọn", command=self.toggle_sidebar,
                                          style="secondary", width=86, height=28,
                                          font=("Segoe UI", 8, "bold"))
        self.btn_hide_sidebar.pack(side="right")

        # Đường kẻ phân cách System Separator
        sep1 = tk.Frame(self.sidebar, bg=IOS_SEPARATOR, height=1)
        sep1.pack(fill="x", pady=(8, 14))

        # --- NHÓM 1: KẾT NỐI IPHONE (INSET GROUPED ROW) ---
        card_conn = tk.Frame(self.sidebar, bg=IOS_BG_TERTIARY, padx=14, pady=12)
        card_conn.pack(fill="x", pady=(0, 12))

        row_conn_h = tk.Frame(card_conn, bg=IOS_BG_TERTIARY)
        row_conn_h.pack(fill="x")

        # Icon vuông bo tròn màu xanh dương chuẩn Apple
        icon_conn = tk.Label(row_conn_h, text="📶", font=("Segoe UI", 10),
                             bg=IOS_BLUE, fg="#ffffff", padx=5, pady=2)
        icon_conn.pack(side="left")

        tk.Label(row_conn_h, text="  KẾT NỐI AIRPLAY", font=("Segoe UI", 8, "bold"),
                 bg=IOS_BG_TERTIARY, fg=IOS_LABEL_PRIMARY).pack(side="left")

        self.badge_conn = tk.Label(row_conn_h, text="SẴN SÀNG", font=("Segoe UI", 7, "bold"),
                                   bg="#3a3a3c", fg=IOS_ORANGE, padx=6, pady=2)
        self.badge_conn.pack(side="right")

        self.lbl_conn_status = tk.Label(card_conn, text="Đang chờ tín hiệu AirPlay…",
                                        font=("Segoe UI", 9, "bold"), bg=IOS_BG_TERTIARY,
                                        fg=IOS_ORANGE, anchor="w", wraplength=310, justify="left")
        self.lbl_conn_status.pack(fill="x", pady=(8, 2))

        self.lbl_conn_sub = tk.Label(card_conn, text="Dịch vụ Bonjour đã sẵn sàng kết nối.",
                                     font=("Segoe UI", 8), bg=IOS_BG_TERTIARY,
                                     fg=IOS_LABEL_SECONDARY, anchor="w", wraplength=310, justify="left")
        self.lbl_conn_sub.pack(fill="x")

        # --- NHÓM 2: CHUỘT ẢO BLUETOOTH BLE (INSET GROUPED ROW) ---
        card_bt = tk.Frame(self.sidebar, bg=IOS_BG_TERTIARY, padx=14, pady=12)
        card_bt.pack(fill="x", pady=(0, 12))

        row_bt_h = tk.Frame(card_bt, bg=IOS_BG_TERTIARY)
        row_bt_h.pack(fill="x")

        icon_bt = tk.Label(row_bt_h, text="🖱", font=("Segoe UI", 10),
                           bg=IOS_INDIGO, fg="#ffffff", padx=5, pady=2)
        icon_bt.pack(side="left")

        tk.Label(row_bt_h, text="  CHUỘT ẢO BLUETOOTH (AX211)", font=("Segoe UI", 8, "bold"),
                 bg=IOS_BG_TERTIARY, fg=IOS_LABEL_PRIMARY).pack(side="left")

        self.lbl_bt_status = tk.Label(card_bt, text="● Đang kiểm tra driver…", font=("Segoe UI", 9, "bold"),
                                      bg=IOS_BG_TERTIARY, fg=IOS_ORANGE, anchor="w")
        self.lbl_bt_status.pack(fill="x", pady=(8, 8))

        row_bt_btns = tk.Frame(card_bt, bg=IOS_BG_TERTIARY)
        row_bt_btns.pack(fill="x", pady=(0, 6))

        self.btn_mouse = IOSButton(row_bt_btns, text="Dùng chuột iPhone", command=lambda: self.switch_bt("winusb"),
                                   style="filled", width=150, height=34, font=("Segoe UI", 8, "bold"))
        self.btn_mouse.pack(side="left", expand=True, fill="x", padx=(0, 4))

        self.btn_win = IOSButton(row_bt_btns, text="Trả Bluetooth PC", command=lambda: self.switch_bt("intel"),
                                 style="tinted", width=150, height=34, font=("Segoe UI", 8, "bold"))
        self.btn_win.pack(side="left", expand=True, fill="x", padx=(4, 0))

        tk.Label(card_bt, text="* Cần bật 'Cài đặt > Trợ năng > Cảm ứng > AssistiveTouch' để hiện con trỏ.",
                 font=("Segoe UI", 7), bg=IOS_BG_TERTIARY, fg=IOS_LABEL_SECONDARY, anchor="w").pack(fill="x")

        # --- NHÓM 3: TÙY CHỌN ĐẦU RA ÂM THANH (INSET GROUPED ROW) ---
        card_audio = tk.Frame(self.sidebar, bg=IOS_BG_TERTIARY, padx=14, pady=12)
        card_audio.pack(fill="x", pady=(0, 12))

        row_audio_h = tk.Frame(card_audio, bg=IOS_BG_TERTIARY)
        row_audio_h.pack(fill="x")

        icon_audio = tk.Label(row_audio_h, text="🔊", font=("Segoe UI", 10),
                              bg=IOS_ORANGE, fg="#ffffff", padx=5, pady=2)
        icon_audio.pack(side="left")

        tk.Label(row_audio_h, text="  ĐẦU RA ÂM THANH", font=("Segoe UI", 8, "bold"),
                 bg=IOS_BG_TERTIARY, fg=IOS_LABEL_PRIMARY).pack(side="left")

        # Segmented Control chuẩn Apple
        self.audio_seg = IOSSegmentedControl(
            card_audio,
            options=[("pc", "Loa PC"), ("iphone", "Loa iPhone"), ("mute", "Tắt tiếng")],
            on_change=self.select_audio_mode,
            initial=self.audio_mode
        )
        self.audio_seg.pack(fill="x", pady=(10, 8))

        # Thanh trượt âm lượng phong cách iOS
        self.row_vol = tk.Frame(card_audio, bg=IOS_BG_TERTIARY)
        self.row_vol.pack(fill="x", pady=(4, 0))

        self.lbl_vol_title = tk.Label(self.row_vol, text="Âm lượng Loa PC: 100%", font=("Segoe UI", 8),
                                      bg=IOS_BG_TERTIARY, fg=IOS_LABEL_PRIMARY)
        self.lbl_vol_title.pack(anchor="w")

        self.scale_vol = ttk.Scale(self.row_vol, from_=0.0, to=1.0, value=1.0,
                                   command=self._on_vol_change)
        self.scale_vol.pack(fill="x", pady=(3, 0))

        # --- NHÓM 4: THÔNG SỐ TRUYỀN PHÁT (INSET GROUPED ROW) ---
        card_stats = tk.Frame(self.sidebar, bg=IOS_BG_TERTIARY, padx=14, pady=12)
        card_stats.pack(fill="x", pady=(0, 12))

        row_stat_h = tk.Frame(card_stats, bg=IOS_BG_TERTIARY)
        row_stat_h.pack(fill="x")

        icon_stats = tk.Label(row_stat_h, text="⚡", font=("Segoe UI", 10),
                              bg=IOS_PURPLE, fg="#ffffff", padx=5, pady=2)
        icon_stats.pack(side="left")

        tk.Label(row_stat_h, text="  THÔNG SỐ PHÁT HÌNH", font=("Segoe UI", 8, "bold"),
                 bg=IOS_BG_TERTIARY, fg=IOS_LABEL_PRIMARY).pack(side="left")

        self.lbl_stat_fps = tk.Label(card_stats, text="Tốc độ: 0 FPS", font=("Segoe UI", 9, "bold"),
                                     bg=IOS_BG_TERTIARY, fg=IOS_LABEL_PRIMARY, anchor="w")
        self.lbl_stat_fps.pack(fill="x", pady=(6, 2))

        self.lbl_stat_res = tk.Label(card_stats, text="Độ phân giải: Đang chờ tín hiệu",
                                     font=("Segoe UI", 8), bg=IOS_BG_TERTIARY, fg=IOS_LABEL_SECONDARY, anchor="w")
        self.lbl_stat_res.pack(fill="x")

        # Footer Sidebar
        footer = tk.Frame(self.sidebar, bg=IOS_BG_SECONDARY)
        footer.pack(side="bottom", fill="x")

        self.lbl_msg = tk.Label(footer, text="", font=("Segoe UI", 8), bg=IOS_BG_SECONDARY,
                                fg=IOS_LABEL_SECONDARY, wraplength=320, justify="left")
        self.lbl_msg.pack(fill="x", pady=(0, 4))

        # --- 2. VÙNG VIEWPORT HIỂN THỊ MÀN HÌNH IPHONE (BÊN PHẢI) ---
        self.view_container = tk.Frame(self.main_container, bg=IOS_BG_BASE)
        self.view_container.pack(side="right", fill="both", expand=True)

        # Nút nổi bật khi thu gọn sidebar
        self.btn_show_sidebar = IOSButton(self.view_container, text="▶ Mở Bảng Điều Khiển",
                                          command=self.toggle_sidebar, style="filled",
                                          width=165, height=36, font=("Segoe UI", 9, "bold"))

        # Canvas hiển thị với nền đen tuyền OLED
        self.canvas = tk.Canvas(self.view_container, bg=IOS_BG_BASE, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self.canvas_img = self.canvas.create_image(0, 0, anchor="nw")

        # Tương tác chuột Bluetooth BLE trên Canvas
        self.canvas.bind("<Motion>", self._on_mouse_move)
        self.canvas.bind("<ButtonPress-1>", self._on_mouse_down)
        self.canvas.bind("<ButtonRelease-1>", self._on_mouse_up)
        self.canvas.bind("<MouseWheel>", self._on_mouse_wheel)
        self.canvas.bind("<Configure>", self._on_canvas_resize)

        # Vẽ màn hình chờ khi khởi động
        self._draw_waiting_screen()

    def toggle_sidebar(self):
        """Bật / tắt thanh điều khiển bên trái để phóng to vùng màn hình iPhone."""
        if self.sidebar_visible:
            self.sidebar.pack_forget()
            self.sidebar_visible = False
            self.btn_show_sidebar.place(x=16, y=16)
        else:
            self.btn_show_sidebar.place_forget()
            self.sidebar.pack(side="left", fill="y", expand=False, before=self.view_container)
            self.sidebar_visible = True
        self.root.update_idletasks()
        self._on_canvas_resize(None)

    def _on_airplay_connect(self, client_ip):
        """iPhone bắt đầu phiên AirPlay."""
        self.last_client_ip = client_ip
        self.is_wired = client_ip.startswith("172.20.10.")
        self.root.after(0, self._handle_client_connected, client_ip)

    def _handle_client_connected(self, client_ip):
        tag = "Cáp USB" if self.is_wired else "Wi-Fi"
        self.root.title(f"iPhone Studio — AirPlay [{tag}]")
        self.lbl_stat_res.config(text=f"Thiết bị: {client_ip} ({tag})", fg=IOS_GREEN)
        self.badge_conn.config(text=f"ĐÃ KẾT NỐI [{tag.upper()}]", bg="#1e3a29", fg=IOS_GREEN)

    def select_audio_mode(self, mode):
        """Chuyển đổi tùy chọn đầu ra âm thanh."""
        self.audio_mode = mode
        self.server.set_audio_mode(mode)
        self.audio_seg.set_selected(mode)

        if mode == "pc":
            self.row_vol.pack(fill="x", pady=(4, 0))
            self.lbl_msg.config(text="🔊 Âm thanh iPhone được phát qua Loa PC.")
        elif mode == "iphone":
            self.row_vol.pack_forget()
            self.lbl_msg.config(text="📱 Âm thanh phát trên Loa iPhone. (AirPlay chỉ chiếu hình ảnh)")
        else:
            self.row_vol.pack_forget()
            self.lbl_msg.config(text="🔇 Đã tắt tiếng.")

    def _on_vol_change(self, val):
        vol = float(val)
        self.audio_volume = vol
        self.server.set_volume(vol)
        self.lbl_vol_title.config(text=f"Âm lượng Loa PC: {int(vol * 100)}%")

    def refresh_devices(self):
        """Kiểm tra Bluetooth, iPhone qua cáp và mạng Hotspot trong luồng nền."""
        threading.Thread(target=self._poll_status, daemon=True).start()

    def _poll_status(self):
        mode = bt_driver.status()
        hotspot_ip = check_hotspot_ip()
        dev = iphone_usb()
        if hotspot_ip:
            protect_pc_internet()
        self.root.after(0, self._update_device_ui, mode, hotspot_ip, dev)

    def _update_device_ui(self, mode, hotspot_ip, dev):
        self.mode = mode
        self.hotspot_ip = hotspot_ip
        self.usb_dev = dev

        # Cập nhật trạng thái Bluetooth
        if mode == "winusb":
            self.lbl_bt_status.config(text="● Chuột ảo BLE (WinUSB): BẬT", fg=IOS_GREEN)
            self.btn_mouse.set_enabled(False)
            self.btn_win.set_enabled(True)
            if self.ble is None:
                try:
                    from iphone_mirror import BleInput
                    self.ble = BleInput(BLE)
                except Exception as ex:
                    print("Không mở được chuột BLE:", ex, flush=True)
        elif mode == "intel":
            self.lbl_bt_status.config(text="● Bluetooth PC (Intel): BẬT", fg=IOS_BLUE)
            self.btn_mouse.set_enabled(True)
            self.btn_win.set_enabled(False)
            if self.ble is not None:
                self.ble.close()
                self.ble = None
        else:
            self.lbl_bt_status.config(text=f"● Không thấy card: {mode}", fg=IOS_RED)
            self.btn_mouse.set_enabled(False)
            self.btn_win.set_enabled(False)

        # Cập nhật trạng thái Kết nối iPhone
        if hotspot_ip:
            self.lbl_conn_status.config(text=f"⚡ Cáp USB + Hotspot ({hotspot_ip})", fg=IOS_GREEN)
            self.lbl_conn_sub.config(
                text="AirPlay qua dây cực mượt 60 FPS (Đã bật bảo vệ, không tốn 4G máy tính).",
                fg=IOS_LABEL_PRIMARY
            )
            self.badge_conn.config(text="USB HOTSPOT", bg="#1e3a29", fg=IOS_GREEN)
        elif dev:
            self.lbl_conn_status.config(text=f"✔ Cáp USB: {dev.get('ProductType', 'iPhone')}", fg=IOS_BLUE)
            self.lbl_conn_sub.config(
                text="Mẹo: Bật 'Điểm truy cập cá nhân' trên iPhone để AirPlay tự động chạy qua cáp USB!",
                fg=IOS_ORANGE
            )
            self.badge_conn.config(text="USB CABLE", bg="#15273d", fg=IOS_BLUE)
        else:
            local_ips = get_local_ips()
            lan_ip = local_ips[0] if local_ips else "192.168.0.x"
            self.lbl_conn_status.config(text=f"📡 Sẵn sàng qua Wi-Fi ({lan_ip})", fg=IOS_ORANGE)
            self.lbl_conn_sub.config(
                text="Cắm cáp USB + bật Điểm truy cập cá nhân để đường truyền nét và mượt hơn.",
                fg=IOS_LABEL_SECONDARY
            )
            self.badge_conn.config(text="WI-FI READY", bg="#3a3a3c", fg=IOS_ORANGE)

        if self.running:
            self.root.after(2500, self.refresh_devices)

    def switch_bt(self, target_mode):
        """Chuyển đổi driver card Bluetooth AX211."""
        if self.ble:
            self.ble.close()
            self.ble = None
        self.lbl_msg.config(text=f"Đang đổi driver sang {target_mode} (Windows sẽ hỏi quyền Admin)...")
        threading.Thread(target=lambda: [bt_driver.switch(target_mode), self.root.after(1000, self.refresh_devices)], daemon=True).start()

    # --- TÍNH TOÁN VÀ ĐIỀU KHIỂN CHUỘT BLE TRÊN MÀN HÌNH ---
    def _canvas_to_iphone_coords(self, x, y):
        """Chuyển đổi toạ độ click trên Canvas thành toạ độ tỉ lệ chuẩn (0.0 đến 1.0) của iPhone."""
        if self.dw <= 0 or self.dh <= 0:
            return None, None
        fx = (x - self.offset_x) / self.dw
        fy = (y - self.offset_y) / self.dh
        if 0.0 <= fx <= 1.0 and 0.0 <= fy <= 1.0:
            return fx, fy
        return None, None

    def _on_mouse_move(self, event):
        if self.ble and self.mode == "winusb":
            fx, fy = self._canvas_to_iphone_coords(event.x, event.y)
            if fx is not None:
                self.ble.update(fx=fx, fy=fy)

    def _on_mouse_down(self, event):
        if self.ble and self.mode == "winusb":
            fx, fy = self._canvas_to_iphone_coords(event.x, event.y)
            if fx is not None:
                self.ble.update(buttons=1, fx=fx, fy=fy)

    def _on_mouse_up(self, event):
        if self.ble and self.mode == "winusb":
            fx, fy = self._canvas_to_iphone_coords(event.x, event.y)
            if fx is not None:
                self.ble.update(buttons=0, fx=fx, fy=fy)

    def _on_mouse_wheel(self, event):
        if self.ble and self.mode == "winusb":
            fx, fy = self._canvas_to_iphone_coords(event.x, event.y)
            if fx is not None:
                delta = 2 * round(event.delta / 120)
                self.ble.update(wheel=delta, fx=fx, fy=fy)

    def _on_canvas_resize(self, _event):
        """Tính toán lại kích thước hiển thị khi thay đổi kích thước cửa sổ."""
        if self.dec.latest is None and self.dec.frames == 0:
            self._draw_waiting_screen()

    def _draw_waiting_screen(self):
        """Vẽ màn hình chờ chuẩn Apple AirPlay & iPhone Mockup thanh lịch, tối giản."""
        self.canvas.delete("all")
        cw = max(100, self.canvas.winfo_width())
        ch = max(100, self.canvas.winfo_height())
        if cw <= 1 or ch <= 1:
            cw, ch = 740, 840

        cx, cy = cw // 2, ch // 2

        # 1. Khung mô phỏng iPhone tỷ lệ chuẩn Apple
        box_w, box_h = min(360, cw - 60), min(580, ch - 60)
        x1, y1 = cx - box_w // 2, cy - box_h // 2
        x2, y2 = cx + box_w // 2, cy + box_h // 2

        # Viền thân máy iPhone Space Gray bo tròn thanh lịch
        phone_points = [
            x1, y1 + 36,
            x1 + 36, y1,
            x2 - 36, y1,
            x2, y1 + 36,
            x2, y2 - 36,
            x2 - 36, y2,
            x1 + 36, y2,
            x1, y2 - 36
        ]
        self.canvas.create_polygon(phone_points, smooth=True, fill="#1c1c1e", outline="#2c2c2e", width=2)

        # Màn hình bên trong (Đen OLED)
        inner_m = 5
        screen_points = [
            x1 + inner_m, y1 + 32,
            x1 + 32, y1 + inner_m,
            x2 - 32, y1 + inner_m,
            x2 - inner_m, y1 + 32,
            x2 - inner_m, y2 - 32,
            x2 - 32, y2 - inner_m,
            x1 + 32, y2 - inner_m,
            x1 + inner_m, y2 - 32
        ]
        self.canvas.create_polygon(screen_points, smooth=True, fill="#000000", outline="")

        # Dynamic Island chuẩn Apple
        island_w, island_h = 86, 20
        iy1 = y1 + 14
        self.canvas.create_rectangle(cx - island_w // 2, iy1, cx + island_w // 2, iy1 + island_h,
                                    fill="#000000", outline="#2c2c2e", width=1)
        # Chấm camera cảm biến
        self.canvas.create_oval(cx + 24, iy1 + 5, cx + 34, iy1 + 15, fill="#1c1c1e", outline="")

        # 2. Biểu tượng AirPlay chuẩn Apple
        # Màn hình chữ nhật
        ap_y = cy - 130
        self.canvas.create_rectangle(cx - 24, ap_y - 18, cx + 24, ap_y + 12,
                                    outline=IOS_BLUE, width=2)
        # Tam giác phía dưới lồng vào
        self.canvas.create_polygon([cx - 14, ap_y + 18, cx + 14, ap_y + 18, cx, ap_y + 2],
                                   fill=IOS_BLUE, outline="")

        # Tiêu đề AirPlay
        self.canvas.create_text(cx, cy - 75, text="AirPlay",
                                font=("Segoe UI", 16, "bold"), fill=IOS_LABEL_PRIMARY)

        self.canvas.create_text(cx, cy - 48, text="Sẵn sàng phản chiếu màn hình",
                                font=("Segoe UI", 9), fill=IOS_LABEL_SECONDARY)

        # Hộp hiển thị tên máy tính phong cách Apple Card
        name_w = box_w - 60
        n_x1, n_y1 = cx - name_w // 2, cy - 25
        n_x2, n_y2 = cx + name_w // 2, cy + 18
        self.canvas.create_rectangle(n_x1, n_y1, n_x2, n_y2, fill="#1c1c1e", outline="#2c2c2e", width=1)

        self.canvas.create_text(cx, cy - 4, text=socket.gethostname(),
                                font=("Segoe UI", 10, "bold"), fill=IOS_BLUE)

        # 3 bước kết nối đơn giản, sạch sẽ
        steps = [
            ("1", "Vuốt góc trên bên phải màn hình để mở Control Center"),
            ("2", "Chạm vào biểu tượng 'Phản chiếu màn hình'"),
            ("3", f"Chọn '{socket.gethostname()}' để kết nối ngay")
        ]
        sy = cy + 50
        for num, text in steps:
            # Chấm tròn xám tối
            self.canvas.create_oval(cx - 120, sy - 9, cx - 102, sy + 9, fill="#2c2c2e", outline="")
            self.canvas.create_text(cx - 111, sy, text=num, font=("Segoe UI", 8, "bold"), fill=IOS_LABEL_PRIMARY)
            self.canvas.create_text(cx - 90, sy, text=text, font=("Segoe UI", 8),
                                    fill=IOS_LABEL_SECONDARY, anchor="w")
            sy += 36

        # Badge thông tin kết nối
        tip = "⚡ Cáp USB + Hotspot: 60 FPS • Không độ trễ • Đã bảo vệ 4G"
        self.canvas.create_text(cx, cy + 165, text=tip, font=("Segoe UI", 8), fill=IOS_GREEN)

        self.canvas_img = self.canvas.create_image(0, 0, anchor="nw")

    def _render_loop(self):
        """Vòng lặp giải mã và hiển thị khung hình mượt mà 60 FPS."""
        frame, self.dec.latest = self.dec.latest, None
        if frame is not None:
            cw = max(100, self.canvas.winfo_width())
            ch = max(100, self.canvas.winfo_height())

            fw, fh = frame.width, frame.height
            if self.frame_size != (fw, fh) or self.canvas.find_withtag("video_frame") == ():
                self.canvas.delete("all")
                self.frame_size = (fw, fh)
                self.lbl_stat_res.config(text=f"Độ phân giải: {fw} x {fh}")

            # Tính toán tỉ lệ giữ nguyên Aspect Ratio và căn giữa
            margin = 12
            aw, ah = max(50, cw - margin * 2), max(50, ch - margin * 2)
            scale = min(aw / fw, ah / fh)
            self.dw = max(1, int(fw * scale))
            self.dh = max(1, int(fh * scale))
            self.offset_x = (cw - self.dw) // 2
            self.offset_y = (ch - self.dh) // 2

            # Chuyển đổi khung hình sang ảnh Tkinter
            img = frame.to_image(width=self.dw, height=self.dh)
            self.photo = ImageTk.PhotoImage(img)

            if not self.canvas.find_withtag("video_frame"):
                self.canvas_img = self.canvas.create_image(self.offset_x, self.offset_y,
                                                           image=self.photo, anchor="nw",
                                                           tags="video_frame")
            else:
                self.canvas.coords(self.canvas_img, self.offset_x, self.offset_y)
                self.canvas.itemconfig(self.canvas_img, image=self.photo)

            self.n_fps += 1

        # Cập nhật FPS mỗi giây
        now = time.perf_counter()
        if now - self.t_fps >= 1.0:
            self.current_fps = self.n_fps
            tag = " [Cáp USB]" if self.is_wired else (" [Wi-Fi]" if self.dec.frames > 0 else "")
            if self.dec.frames > 0:
                self.lbl_stat_fps.config(text=f"Tốc độ: {self.current_fps} FPS{tag}", fg=IOS_GREEN)
            else:
                self.lbl_stat_fps.config(text="Tốc độ: Đang chờ...", fg=IOS_LABEL_SECONDARY)
            self.t_fps, self.n_fps = now, 0

        if self.running:
            self.root.after(10, self._render_loop)

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
    """Kiểm tra tính toàn vẹn của ứng dụng control_panel.py chuẩn Apple iOS HIG."""
    print("--- Chạy self-test control_panel.py (Apple iOS Design) ---", flush=True)

    # 1. Kiểm tra phát hiện Hotspot IP
    ip = check_hotspot_ip()
    print(f"1. Kiểm tra Hotspot IP: {ip if ip else 'Chưa cắm Hotspot (OK)'}")

    # 2. Kiểm tra hàm iphone_usb
    usb = iphone_usb()
    print(f"2. Kiểm tra iPhone USB: {usb.get('ProductType') if usb else 'Chưa thấy thiết bị USB (OK)'}")

    # 3. Kiểm tra import AirPlay server & decoder
    server_dec = AirPlayDecoder()
    assert server_dec is not None
    print("3. Tích hợp AirPlayDecoder: OK")

    # 4. Kiểm tra giao diện headless
    root = tk.Tk()
    root.withdraw()
    app = UnifiedMirrorApp(root)
    assert app.server is not None
    app.select_audio_mode("pc")
    assert app.audio_mode == "pc"
    app.select_audio_mode("iphone")
    assert app.audio_mode == "iphone"
    # 5. Kiểm tra biểu tượng Liquid Glass (PNG 1024x1024 và ICO đa kích thước)
    assert os.path.exists(PNG_1024_PATH), f"Không tìm thấy {PNG_1024_PATH}"
    assert os.path.exists(ICO_PATH), f"Không tìm thấy {ICO_PATH}"
    print("5. Tích hợp Biểu tượng Liquid Glass (PNG 1024 & ICO): OK")

    app.on_close()
    print("=== SELF-TEST APPLE iOS DESIGN THÀNH CÔNG 100%! ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="iPhone Studio — Apple iOS & iPadOS Design")
    parser.add_argument("--self-test", action="store_true", help="chạy kiểm tra tự động thành phần")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        sys.exit(0)

    root = tk.Tk()
    UnifiedMirrorApp(root)
    root.mainloop()
