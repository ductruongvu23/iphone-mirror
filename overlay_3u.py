"""Lớp điều khiển đè lên cửa sổ video của 3uAirPlayer (phản chiếu qua cáp, ~40-50 khung/giây).
Cửa sổ gần như trong suốt nên vẫn thấy video bên dưới; chuột trên lớp này đi qua chuột Bluetooth (bumble_mouse).
Chuột trái: chạm / kéo.  Lăn chuột: cuộn.  Esc: thoát.
Cần: card Bluetooth ở chế độ WinUSB, iPhone đã ghép đôi "Mirror Mouse", 3uAirPlayer đang Start Casting.
Chạy:  python overlay_3u.py [usb:8087:0033]
"""
import ctypes
import ctypes.wintypes as W
import sys
import tkinter as tk

from iphone_mirror import BleInput

ctypes.windll.shcore.SetProcessDpiAwareness(2)  # toạ độ thật của màn hình, khớp GetWindowRect của 3uAirPlayer
u = ctypes.windll.user32
TITLE_BAR = 36  # shortcut: chiều cao thanh tiêu đề cửa sổ video 3uAirPlayer đo ở màn hình 100%; màn hình phóng to thì phải đo lại
ASPECT = 1242 / 2688  # shortcut: chỉ màn hình dọc của iPhone này; xoay ngang thì lớp điều khiển lệch


def windows_of(exe_name):
    """[(hwnd, (l, t, r, b))] các cửa sổ đang hiện của tiến trình có tên file exe_name."""
    out = []

    @ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
    def cb(h, _):
        if u.IsWindowVisible(h) and not u.IsIconic(h):
            pid = W.DWORD()
            u.GetWindowThreadProcessId(h, ctypes.byref(pid))
            hp = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION: đọc được cả tiến trình chạy Admin
            name = ctypes.create_unicode_buffer(260)
            size = W.DWORD(260)
            if hp and ctypes.windll.kernel32.QueryFullProcessImageNameW(hp, 0, name, ctypes.byref(size)) and \
                    name.value.lower().endswith(exe_name):
                r = W.RECT()
                u.GetWindowRect(h, ctypes.byref(r))
                out.append((h, (r.left, r.top, r.right, r.bottom)))
            if hp:
                ctypes.windll.kernel32.CloseHandle(hp)
        return True
    u.EnumWindows(cb, 0)
    return out


def find_video_window(wins):
    """Cửa sổ video là cửa sổ có thanh công cụ hẹp (<100px) dính ở mép phải."""
    for h, (l, t, r, b) in wins:
        if any(abs(tl - r) <= 2 and tr - tl < 100 and tb - tt > 200 for _, (tl, tt, tr, tb) in wins):
            return h, (l, t, r, b)
    return None


def video_box(rect):
    """Vùng hình iPhone trong cửa sổ video: dưới thanh tiêu đề, giữ tỉ lệ màn hình, căn giữa."""
    l, t, r, b = rect
    cl, ct, cw, ch = l, t + TITLE_BAR, r - l, b - t - TITLE_BAR
    vw, vh = (ch * ASPECT, ch) if cw / ch > ASPECT else (cw, cw / ASPECT)
    x, y = round(cl + (cw - vw) / 2), round(ct + (ch - vh) / 2)
    return x, y, round(vw), round(vh)


class Overlay:
    def __init__(self, root, transport):
        self.root, self.box = root, None
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", 0.01)  # gần như vô hình nhưng vẫn nhận chuột
        self.ble = BleInput(transport)
        f = lambda e: dict(fx=e.x / max(1, self.box[2]), fy=e.y / max(1, self.box[3]))
        root.bind("<Motion>", lambda e: self.ble.update(**f(e)))
        root.bind("<ButtonPress-1>", lambda e: self.ble.update(buttons=1, **f(e)))
        root.bind("<ButtonRelease-1>", lambda e: self.ble.update(buttons=0, **f(e)))
        root.bind("<MouseWheel>", lambda e: self.ble.update(wheel=2 * round(e.delta / 120), **f(e)))
        root.bind("<Escape>", lambda e: root.destroy())
        self.follow()

    def follow(self):
        found = find_video_window(windows_of("3uairplayer.exe"))
        box = video_box(found[1]) if found else None
        if box != self.box:
            self.box = box
            if box:
                x, y, w, h = box
                self.root.geometry(f"{w}x{h}+{x}+{y}")
                self.root.deiconify()
            else:
                self.root.withdraw()  # 3uAirPlayer chưa phát hình hoặc đang thu nhỏ
        self.root.after(200, self.follow)


def self_test():
    wins = [(1, (-1673, 291, -673, 971)), (2, (-1756, 83, -956, 683)), (3, (-956, 113, -878, 789))]
    assert find_video_window(wins) == (2, (-1756, 83, -956, 683))
    assert video_box((-1756, 83, -956, 683)) == (-1486, 119, 261, 564)  # khớp vùng đo trên ảnh chụp
    assert find_video_window(wins[:1]) is None
    print("self-test OK")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        self_test()
        raise SystemExit
    root = tk.Tk()
    root.title("iPhone Mirror overlay")
    app = Overlay(root, sys.argv[1] if len(sys.argv) > 1 else "usb:8087:0033")
    root.mainloop()
    app.ble.close()
