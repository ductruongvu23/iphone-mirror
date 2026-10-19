"""Phát màn hình iPhone lên Windows qua cáp USB và điều khiển bằng chuột/bàn phím.

Đường đi: cáp USB -> usbmuxd (Apple Mobile Device Service) -> go-ios (ios.exe) chuyển cổng
-> WebDriverAgent (WDA) chạy trên iPhone: cổng 9100 = luồng MJPEG, cổng 8100 = API chạm/vuốt/gõ phím.

Chuột trái: chạm / giữ / vuốt.  Chuột phải: nút Home.  Gõ phím: gửi vào ô đang focus trên iPhone.
Chạy:  python iphone_mirror.py [--bundle <bundle id của WDA>] [--height 800] [--ble usb:8087:0033]

--ble: điều khiển bằng chuột Bluetooth (bumble_mouse.py) thay cho lệnh chạm của WDA. Con trỏ AssistiveTouch bám theo
chuột trên cửa sổ, bấm/kéo/cuộn gần như tức thì. WDA vẫn dùng cho hình, gõ phím và nút Home.
"""
import argparse
import asyncio
import atexit
import collections
import io
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.request

from PIL import Image, ImageTk

sys.stdout.reconfigure(encoding="utf-8")  # console Windows mặc định cp1252 không in được tiếng Việt
HERE = os.path.dirname(os.path.abspath(__file__))
IOS = os.path.join(HERE, "ios.exe") if os.path.exists(os.path.join(HERE, "ios.exe")) else "ios"
WDA = "http://127.0.0.1:8100"
MJPEG = "http://127.0.0.1:9100"


def http(method, url, body=None, timeout=30):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def wda_up():
    try:
        http("GET", f"{WDA}/status", timeout=2)
        return True
    except OSError:
        return False


def start_go_ios(bundle):
    """Mở tunnel (iOS 17+), mount Developer Disk Image, chạy WDA và chuyển cổng 8100/9100 về máy tính."""
    procs = []

    def bg(*args):
        log = open(os.path.join(HERE, f"ios-{args[0]}-{len(procs)}.log"), "w")
        procs.append(subprocess.Popen([IOS, *args], stdout=log, stderr=subprocess.STDOUT))

    atexit.register(lambda: [p.terminate() for p in procs])
    bg("tunnel", "start", "--userspace")  # iOS < 17 không cần, lỗi thì bỏ qua
    time.sleep(3)
    subprocess.run([IOS, "image", "auto"], cwd=HERE)
    bg("runwda", f"--bundleid={bundle}", f"--testrunnerbundleid={bundle}", "--xctestconfig=WebDriverAgentRunner.xctest")
    bg("forward", "8100", "8100")
    bg("forward", "9100", "9100")
    for _ in range(60):
        if wda_up():
            return
        time.sleep(1)
    raise SystemExit("WDA không phản hồi sau 60s. Xem các file ios-*.log cạnh script.")


def split_jpegs(buf):
    """Tách JPEG hoàn chỉnh mới nhất khỏi luồng MJPEG. Trả về (frame hoặc None, phần dư)."""
    frame = None
    while True:
        s = buf.find(b"\xff\xd8")
        if s < 0:
            return frame, buf[-1:]  # giữ byte cuối phòng khi \xff\xd8 bị cắt giữa 2 lần đọc
        e = buf.find(b"\xff\xd9", s + 2)
        if e < 0:
            return frame, buf[s:]
        frame, buf = buf[s:e + 2], buf[e + 2:]


def gesture(start, end, hold_s, move_s):
    """W3C touch action: đặt ngón tay, giữ hold_s giây, (kéo tới end trong move_s giây), nhấc lên."""
    acts = [{"type": "pointerMove", "duration": 0, "x": start[0], "y": start[1]},
            {"type": "pointerDown", "button": 0},
            {"type": "pause", "duration": max(50, int(hold_s * 1000))}]
    if end:
        # shortcut: kéo theo đường thẳng start->end, bỏ qua đường cong ở giữa; cần vẽ chữ/cử chỉ phức tạp thì gửi thêm điểm
        acts.append({"type": "pointerMove", "duration": max(50, int(move_s * 1000)), "x": end[0], "y": end[1]})
    acts.append({"type": "pointerUp", "button": 0})
    return {"actions": [{"type": "pointer", "id": "finger1", "parameters": {"pointerType": "touch"}, "actions": acts}]}


def queue_input(q, state, buttons=None, fx=None, fy=None, wheel=0):
    """Xếp trạng thái chuột vào hàng đợi gửi; trả về trạng thái mới (nút, fx, fy).
    Vị trí di chuyển thuần được gộp (chỉ giữ vị trí mới nhất); nhấn/nhả nút và cuộn thì luôn giữ để không mất cú bấm."""
    b, x, y = state
    state = (b if buttons is None else buttons, x if fx is None else fx, y if fy is None else fy)
    movable = buttons is None and not wheel
    if movable and q and q[-1][4]:
        q[-1] = (*state, 0, True)
    else:
        q.append((*state, wheel, movable))
    return state


class BleInput:
    """Chuột Bluetooth (bumble_mouse) chạy trong luồng asyncio riêng; luồng Tk gọi update()."""

    def __init__(self, transport):
        import bumble_mouse
        self.mouse, self.q, self.state, self.pressed = bumble_mouse.BumbleMouse(), collections.deque(), (0, 0.5, 0.5), 0
        self.loop, self.wake = asyncio.new_event_loop(), asyncio.Event()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        fut = asyncio.run_coroutine_threadsafe(self.run(transport), self.loop)
        fut.add_done_callback(lambda f: f.cancelled() or not f.exception() or print("Lỗi chuột Bluetooth:", repr(f.exception()), flush=True))

    def close(self):
        # Reset card khi thoát: nếu không, card vẫn phát sóng và iPhone kết nối vào lúc lần chạy sau đang khởi động.
        from bumble.hci import HCI_Reset_Command
        if getattr(self.mouse, "device", None):
            try:
                asyncio.run_coroutine_threadsafe(self.mouse.device.host.send_command(HCI_Reset_Command()), self.loop).result(2)
            except Exception as ex:  # thoát app không được hỏng vì card
                print("Không reset được card Bluetooth:", repr(ex), flush=True)

    async def run(self, transport):
        await self.mouse.start(transport)
        print('Chuột Bluetooth đang chờ iPhone kết nối (lần đầu: Cài đặt > Bluetooth > "Mirror Mouse").', flush=True)
        was = False
        while True:
            try:
                await asyncio.wait_for(self.wake.wait(), 1)
            except asyncio.TimeoutError:
                pass
            self.wake.clear()
            if self.mouse.connected != was:
                was = self.mouse.connected
                print("Chuột Bluetooth:", "đã kết nối iPhone" if was else "mất kết nối", flush=True)
            while self.q:
                b, fx, fy, w, _ = self.q.popleft()
                if not was:
                    continue
                if self.pressed and not b:  # iOS bỏ qua cú bấm quá ngắn (bấm/nhả trong vài ms)
                    await asyncio.sleep(max(0.0, 0.05 - (time.monotonic() - self.pressed)))
                self.pressed = time.monotonic() if b and not self.pressed else (self.pressed if b else 0)
                await self.mouse.send(b, fx, fy, w)

    def update(self, **kw):
        self.loop.call_soon_threadsafe(self._apply, kw)

    def _apply(self, kw):
        self.state = queue_input(self.q, self.state, **kw)
        self.wake.set()


class App:
    def __init__(self, root, height, ble=None):
        s = http("POST", f"{WDA}/session", {"capabilities": {}})
        self.sid = s.get("sessionId") or s["value"]["sessionId"]
        size = http("GET", f"{WDA}/session/{self.sid}/window/size")["value"]
        self.pw, self.ph = size["width"], size["height"]  # đơn vị point của iOS, không phải pixel
        http("POST", f"{WDA}/session/{self.sid}/appium/settings", {"settings": {
            "mjpegServerFramerate": 30, "mjpegScalingFactor": 50, "mjpegServerScreenshotQuality": 30}})
        # shortcut: chỉ dọc (portrait); app xoay ngang sẽ bị méo và lệch toạ độ
        self.dh, self.dw = height, round(height * self.pw / self.ph)
        self.canvas = tk.Canvas(root, width=self.dw, height=self.dh, bg="black", highlightthickness=0)
        self.canvas.pack()
        self.img_id = self.canvas.create_image(0, 0, anchor="nw")
        self.latest, self.photo, self.down, self.moved_at = None, None, None, None
        self.q = queue.Queue()
        self.ble = BleInput(ble) if ble else None
        if ble:
            f = lambda e: dict(fx=e.x / self.dw, fy=e.y / self.dh)  # vị trí theo tỉ lệ màn hình iPhone
            self.canvas.bind("<Motion>", lambda e: self.ble.update(**f(e)))
            self.canvas.bind("<ButtonPress-1>", lambda e: self.ble.update(buttons=1, **f(e)))
            self.canvas.bind("<ButtonRelease-1>", lambda e: self.ble.update(buttons=0, **f(e)))
            self.canvas.bind("<MouseWheel>", lambda e: self.ble.update(wheel=2 * round(e.delta / 120), **f(e)))
        else:
            self.canvas.bind("<ButtonPress-1>", self.press)
            self.canvas.bind("<B1-Motion>", self.motion)
            self.canvas.bind("<ButtonRelease-1>", self.release)
        self.canvas.bind("<Button-3>", lambda e: self.q.put(("home", None)))
        root.bind("<Key>", self.key)
        threading.Thread(target=self.read_stream, daemon=True).start()
        threading.Thread(target=self.worker, daemon=True).start()
        self.root = root
        self.refresh()

    def read_stream(self):
        while True:
            try:
                with urllib.request.urlopen(MJPEG, timeout=10) as r:
                    buf = b""
                    while chunk := r.read1(65536):
                        frame, buf = split_jpegs(buf + chunk)
                        if frame:
                            self.latest = Image.open(io.BytesIO(frame)).convert("RGB").resize((self.dw, self.dh))
            except OSError as ex:
                print("Mất luồng hình, thử lại:", ex)
                time.sleep(1)

    def refresh(self):
        if self.latest is not None:
            self.photo = ImageTk.PhotoImage(self.latest)
            self.latest = None
            self.canvas.itemconfig(self.img_id, image=self.photo)
        self.root.after(15, self.refresh)

    def pt(self, x, y):
        return (round(min(max(x, 0), self.dw - 1) * self.pw / self.dw),
                round(min(max(y, 0), self.dh - 1) * self.ph / self.dh))

    def press(self, e):
        self.down, self.moved_at = (e.x, e.y, time.time()), None

    def motion(self, e):
        if self.moved_at is None and abs(e.x - self.down[0]) + abs(e.y - self.down[1]) > 8:
            self.moved_at = time.time()

    def release(self, e):
        x0, y0, t0 = self.down
        t1 = time.time()
        tm = self.moved_at or t1
        end = self.pt(e.x, e.y) if self.moved_at else None
        self.q.put(("touch", gesture(self.pt(x0, y0), end, tm - t0, t1 - tm)))

    def key(self, e):
        ch = {"BackSpace": "\b", "Return": "\n"}.get(e.keysym, e.char)
        if ch and (ch.isprintable() or ch in "\b\n"):
            self.q.put(("key", ch))

    def worker(self):
        while True:
            kind, val = self.q.get()
            while kind == "key" and self.q.queue and self.q.queue[0][0] == "key":
                val += self.q.get()[1]  # gộp phím gõ nhanh thành 1 request
            try:
                if kind == "touch":
                    http("POST", f"{WDA}/session/{self.sid}/actions", val)
                elif kind == "key":
                    http("POST", f"{WDA}/session/{self.sid}/wda/keys", {"value": list(val)})
                elif kind == "home":
                    http("POST", f"{WDA}/wda/homescreen", {})
            except OSError as ex:
                print("Lỗi WDA:", ex)


def self_test():
    a, b = b"\xff\xd8AAA\xff\xd9", b"\xff\xd8BBB\xff\xd9"
    assert split_jpegs(b"--x\r\n" + a + b"\r\n--x\r\n" + b[:4]) == (a, b[:4])
    assert split_jpegs(b[:4] + b[4:] + b"junk\xff") == (b, b"\xff")
    f, rest = split_jpegs(b"hdr\xff")
    assert f is None and split_jpegs(rest + b"\xd8Z\xff\xd9")[0] == b"\xff\xd8Z\xff\xd9"
    tap = gesture((10, 20), None, 0.01, 0)["actions"][0]["actions"]
    assert [x["type"] for x in tap] == ["pointerMove", "pointerDown", "pause", "pointerUp"] and tap[2]["duration"] == 50
    swipe = gesture((10, 20), (10, 400), 0.02, 0.3)["actions"][0]["actions"]
    assert swipe[3] == {"type": "pointerMove", "duration": 300, "x": 10, "y": 400}
    q, st = collections.deque(), (0, 0.5, 0.5)
    st = queue_input(q, st, fx=0.1, fy=0.1)
    st = queue_input(q, st, fx=0.2, fy=0.2)  # gộp với vị trí trước
    st = queue_input(q, st, buttons=1, fx=0.2, fy=0.2)  # nhấn: luôn giữ
    st = queue_input(q, st, fx=0.3, fy=0.3)
    st = queue_input(q, st, buttons=0, fx=0.3, fy=0.3)  # nhả: luôn giữ
    st = queue_input(q, st, wheel=-2)
    assert [i[:4] for i in q] == [(0, 0.2, 0.2, 0), (1, 0.2, 0.2, 0), (1, 0.3, 0.3, 0), (0, 0.3, 0.3, 0), (0, 0.3, 0.3, -2)], q
    print("self-test OK")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", default="com.facebook.WebDriverAgentRunner.xctrunner", help="bundle id của WDA trên iPhone (xem bằng: ios apps)")
    ap.add_argument("--height", type=int, default=800, help="chiều cao cửa sổ (pixel)")
    ap.add_argument("--ble", metavar="TRANSPORT", help="điều khiển bằng chuột Bluetooth Bumble, vd. usb:8087:0033")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        self_test()
        raise SystemExit
    if not wda_up():
        start_go_ios(args.bundle)
    root = tk.Tk()
    root.title("iPhone")
    root.resizable(False, False)
    app = App(root, args.height, args.ble)
    root.mainloop()
    if app.ble:
        app.ble.close()  # gọi khi luồng còn chạy; trong atexit luồng asyncio đã dừng nên reset bị hết giờ
