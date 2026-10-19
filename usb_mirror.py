"""Trình phát riêng: phản chiếu màn hình iPhone qua cáp USB bằng giao thức QuickTime screen capture
(cách QuickTime trên Mac và 3uAirPlayer làm; giao thức theo quicktime_video_hack), không cần app 3uAirPlayer.
USB: driver filter libusb-win32 (libusb0) mà 3uAirPlayer đã gắn lên iPhone (UpperFilters của thiết bị Apple).
Giải mã H.264 bằng FFmpeg (PyAV). Điều khiển: chuột Bluetooth (bumble_mouse) khi chạy với --ble.
Chuột trái: chạm/kéo. Lăn chuột: cuộn. Đóng cửa sổ: trả iPhone về chế độ USB thường (go-ios/WDA dùng lại được).
Chạy:  python usb_mirror.py [--ble usb:8087:0033] [--height 800]
"""
import argparse
import struct
import sys
import threading
import time
import tkinter as tk

import av
import usb.backend.libusb0
import usb.core
import usb.util
from PIL import ImageTk

sys.stdout.reconfigure(encoding="utf-8")  # console Windows mặc định cp1252 không in được tiếng Việt

APPLE_VID = 0x05AC
QT_SUBCLASS = 0x2A  # interface QuickTime: class 0xFF, subclass 0x2A


def M(s):
    """Mã 4 ký tự -> số; ghi little-endian ra dây thành chuỗi đảo ngược (vd 'ping' -> b'gnip'), như QVH."""
    return int.from_bytes(s.encode("latin1"), "big")


def el(magic, payload):
    """Một phần tử: [độ dài u32][mã u32][dữ liệu]."""
    return struct.pack("<II", 8 + len(payload), M(magic)) + payload


def elements(b):
    """Tách chuỗi phần tử -> [(mã, dữ liệu)]."""
    out, i = [], 0
    while i + 8 <= len(b):
        n, m = struct.unpack_from("<II", b, i)
        if n < 8 or i + n > len(b):
            break
        out.append((m.to_bytes(4, "big").decode("latin1"), b[i + 8:i + n]))
        i += n
    return out


def ser_value(v):
    if isinstance(v, bool):
        return el("bulv", bytes([v]))
    if isinstance(v, float):
        return el("nmbv", b"\x06" + struct.pack("<d", v))
    if isinstance(v, int):
        return el("nmbv", b"\x03" + struct.pack("<I", v))
    if isinstance(v, str):
        return el("strv", v.encode())
    if isinstance(v, bytes):
        return el("datv", v)
    return ser_dict(v)


def ser_dict(d):
    return el("dict", b"".join(el("keyv", el("strk", k.encode()) + ser_value(v)) for k, v in d.items()))


def find_index_value(b, key):
    """Tìm giá trị của khoá số `key` (idxk) ở mọi tầng của dict/extn/fdsc/sbuf."""
    for magic, p in elements(b):
        if magic == "keyv":
            kids = elements(p)
            if len(kids) == 2 and kids[0][0] == "idxk" and struct.unpack_from("<H", kids[0][1])[0] == key:
                return kids[1]
            found = find_index_value(p, key)
        elif magic in ("sbuf", "fdsc", "extn", "dict", "keyv", "idxk"):
            found = find_index_value(p, key)
        else:
            continue
        if found:
            return found
    return None


def parse_sbuf(payload):
    """CMSampleBuffer -> (dữ liệu mẫu 'sdat', (codec, avcC/hvcC) hoặc None, thời điểm 'opts' (value, scale) hoặc None)."""
    sdat, fmt, opts = None, None, None
    for magic, p in elements(payload):
        if magic != "sbuf":
            continue
        for m2, p2 in elements(p):
            if m2 == "sdat":
                sdat = p2
            elif m2 == "opts":
                opts = struct.unpack_from("<QI", p2)
            elif m2 == "fdsc":
                codec = next((struct.unpack_from("<I", x)[0] for mm, x in elements(p2) if mm == "codc"), 0)
                cfg = find_index_value(el("fdsc", p2), 105)  # khoá 105 trong extensions = avcC/hvcC
                if cfg and cfg[0] == "datv":
                    fmt = (codec.to_bytes(4, "big").decode("latin1"), cfg[1])
    return sdat, fmt, opts


# ---- các gói trả lời (đều có 4 byte độ dài ở đầu, như QVH) ----
def rply_clock(corr, clock_ref):
    return struct.pack("<IIQIQ", 28, M("rply"), corr, 0, clock_ref)


def rply_empty(corr, extra):
    return struct.pack("<IIQI", 20 + len(extra), M("rply"), corr, 0) + extra


def asyn(clock_ref, kind, payload=b""):
    return struct.pack("<IIQI", 20 + len(payload), M("asyn"), clock_ref, M(kind)) + payload


PING = struct.pack("<IIQ", 16, M("ping"), 0x0000000100000000)
HPD1 = ser_dict({"Valeria": True, "HEVCDecoderSupports444": True,
                 "DisplaySize": {"Width": 1920.0, "Height": 1200.0}})
ASBD = struct.pack("<dIIIIIIII", 48000.0, M("lpcm"), 12, 4, 1, 4, 2, 16, 0) + struct.pack("<dd", 48000.0, 48000.0)
HPA1 = ser_dict({"BufferAheadInterval": 0.07300000000000001, "deviceUID": "Valeria", "ScreenLatency": 0.04,
                 "formats": ASBD, "EDIDAC3Support": 0, "deviceName": "Valeria"})


class QtSession:
    """Máy trạng thái giao thức (theo messageprocessor.go của QVH): trả lời đồng bộ, đòi khung hình, nhận FEED."""

    def __init__(self, write, on_sample):
        self.write, self.on_sample = write, on_sample
        self.need, self.audio_ref, self.clock0 = None, 0, time.perf_counter()
        self.audio_clock0, self.eat_first, self.eat_last = time.perf_counter(), None, None
        self.released, self.seen = threading.Event(), set()

    def now_ns(self, t0):
        return int((time.perf_counter() - t0) * 1e9)

    def handle(self, d):
        magic, kind = struct.unpack_from("<I", d)[0], struct.unpack_from("<I", d, 12)[0] if len(d) >= 16 else 0
        name = d[:4][::-1].decode("latin1") + (":" + d[12:16][::-1].decode("latin1") if len(d) >= 16 else "")
        if name not in self.seen:  # in loại gói mới gặp lần đầu, để biết giao thức đang tới bước nào
            self.seen.add(name)
            print("iPhone gửi:", name, flush=True)
        if magic == M("ping"):
            self.write(PING)
        elif magic == M("sync"):
            self.sync(kind, struct.unpack_from("<Q", d, 4)[0], struct.unpack_from("<Q", d, 16)[0], d[24:])
        elif magic == M("asyn"):
            self.asyn(kind, d[16:])

    def sync(self, kind, clock_ref, corr, p):
        if kind == M("cwpa"):
            self.audio_ref = struct.unpack_from("<Q", p)[0]
            self.audio_clock0 = time.perf_counter()
            self.write(asyn(1, "hpd1", HPD1))
            self.write(asyn(1, "hpd1", HPD1))
            self.write(rply_clock(corr, self.audio_ref + 1000))
            self.write(asyn(self.audio_ref, "hpa1", HPA1))
        elif kind == M("cvrp"):
            ref = struct.unpack_from("<Q", p)[0]
            self.need = asyn(ref, "need")
            self.write(self.need)
            self.write(rply_clock(corr, ref + 0x1000AF))
        elif kind == M("clok"):
            self.clock0 = time.perf_counter()
            self.write(rply_clock(corr, clock_ref + 0x10000))
        elif kind == M("time"):
            self.write(struct.pack("<IIQI", 44, M("rply"), corr, 0) + struct.pack("<QIIQ", self.now_ns(self.clock0), 10**9, 1, 0))
        elif kind == M("afmt"):
            self.write(rply_empty(corr, ser_dict({"Error": 0})))
        elif kind == M("skew"):
            self.write(rply_empty(corr, struct.pack("<d", self.skew())))
        elif kind == M("stop"):
            self.write(rply_empty(corr, b"\0" * 4))
        elif kind == M("go! "):
            self.write(rply_empty(corr, b"\0" * 4))

    def skew(self):
        """Tốc độ đồng hồ âm thanh của iPhone so với máy tính (QVH CalculateSkew); thiếu dữ liệu thì báo danh định."""
        if not (self.eat_first and self.eat_last) or self.eat_last[0] == self.eat_first[0]:
            return 48000.0
        (dev0, scale, loc0), (dev1, _, loc1) = self.eat_first, self.eat_last
        return scale * ((loc1 - loc0) / 1e9 * scale) / (dev1 - dev0)

    def asyn(self, kind, p):
        if kind == M("feed"):
            sdat, fmt, _ = parse_sbuf(p)
            self.on_sample(sdat, fmt)
            if self.need:
                self.write(self.need)
        elif kind == M("eat!"):
            _, _, opts = parse_sbuf(p)
            if opts:
                mark = (opts[0], opts[1], self.now_ns(self.audio_clock0))
                self.eat_first = self.eat_first or mark
                self.eat_last = mark
        elif kind == M("rels"):
            self.released.set()

    def close(self):
        self.write(asyn(self.audio_ref, "hpa0"))
        self.write(asyn(1, "hpd0"))
        self.released.wait(3)


def split_messages(buf):
    """Tách các gói [độ dài u32][...] hoàn chỉnh khỏi luồng USB -> (danh sách gói không kèm độ dài, phần dư)."""
    out = []
    while len(buf) >= 4:
        n = struct.unpack_from("<I", buf)[0]
        if n < 4 or len(buf) < n:
            break
        out.append(buf[4:n])
        buf = buf[n:]
    return out, buf


class UsbLink:
    """Mở iPhone qua libusb0, bật cấu hình QuickTime, đọc/ghi endpoint bulk của interface QuickTime."""

    def __init__(self):
        self.be = usb.backend.libusb0.get_backend()
        if self.be is None:
            raise SystemExit("Không thấy libusb0.dll (driver libusb-win32). Cần cài driver USB của 3uAirPlayer trước.")
        self.dev = self.find()
        active = self.active()
        qt = self.qt_config()
        # Cấu hình USB thường để trả về khi thoát; nếu lần chạy trước bị ngắt giữa chừng thì iPhone còn kẹt ở cấu hình QuickTime
        self.orig_cfg = active if not qt or active != qt[0].bConfigurationValue else 4
        if not qt:
            try:
                self.dev.ctrl_transfer(0x40, 0x52, 0, 2, b"")  # bật cấu hình QuickTime ẩn; iPhone sẽ kết nối lại
            except usb.core.USBError:
                pass  # QVH: lệnh này hay báo lỗi nhưng vẫn có tác dụng
            qt = self.wait_for_qt()
            if not qt:
                raise SystemExit("iPhone không bật được chế độ QuickTime (thử rút ra cắm lại, mở khoá iPhone)")
        # Phải làm liền tay: iPhone chỉ chờ ~2 giây cho máy tính bắt đầu phiên QuickTime rồi tự ngắt khỏi bus USB
        # (nhật ký PnP: "surprise removed"). Không mở lại handle sau set_configuration (libusb-win32 nhớ cấu hình theo handle).
        cfg, intf = qt
        print(f"Chế độ QuickTime: cấu hình {cfg.bConfigurationValue}, interface {intf.bInterfaceNumber}", flush=True)
        self.dev.set_configuration(cfg.bConfigurationValue)
        usb.util.claim_interface(self.dev, intf.bInterfaceNumber)
        self.intf = intf.bInterfaceNumber
        eps = {usb.util.endpoint_direction(e.bEndpointAddress): e.bEndpointAddress for e in intf}
        self.ep_in, self.ep_out = eps[usb.util.ENDPOINT_IN], eps[usb.util.ENDPOINT_OUT]
        for ep in (self.ep_in, self.ep_out):
            self.dev.clear_halt(ep)
        self.lock = threading.Lock()
        print(f"Đã nhận interface QuickTime (in {self.ep_in:#x}, out {self.ep_out:#x}), chờ iPhone gửi ping…", flush=True)

    def active(self):
        try:
            return self.dev.get_active_configuration().bConfigurationValue
        except usb.core.USBError:
            return None

    def wait_for_qt(self, seconds=10):
        """Dò lại iPhone mỗi 0,1 giây sau lệnh kích hoạt, tới khi thấy cấu hình QuickTime."""
        for _ in range(int(seconds * 10)):
            time.sleep(0.1)
            try:
                self.dev = self.find()
                qt = self.qt_config()
            except (SystemExit, usb.core.USBError):
                continue
            if qt:
                return qt
        return None

    def find(self):
        dev = usb.core.find(idVendor=APPLE_VID, backend=self.be, custom_match=lambda d: 0x1290 <= d.idProduct <= 0x12AF)
        if dev is None:
            raise SystemExit("Không thấy iPhone qua libusb0 (cắm cáp? 3uAirPlayer đang phát hình thì tắt nó đi)")
        return dev

    def qt_config(self):
        for cfg in self.dev:
            for intf in cfg:
                if intf.bInterfaceClass == 0xFF and intf.bInterfaceSubClass == QT_SUBCLASS:
                    return cfg, intf
        return None

    def write(self, data):
        with self.lock:
            try:
                self.dev.write(self.ep_out, data, timeout=2000)
            except usb.core.USBError as ex:
                raise usb.core.USBError(f"ghi gói {data[4:8][::-1]!r}/{data[16:20][::-1]!r} lỗi: {ex}") from ex

    def read(self):
        try:
            return bytes(self.dev.read(self.ep_in, 1 << 20, timeout=1000))
        except usb.core.USBError as ex:
            # libusb0 trên Windows báo hết giờ chờ bằng chữ ("timeout error" / "aborted"), không phải USBTimeoutError
            if isinstance(ex, usb.core.USBTimeoutError) or "timeout" in str(ex) or "aborted" in str(ex):
                return b""
            raise

    def close(self):
        try:
            usb.util.release_interface(self.dev, self.intf)
            self.dev.ctrl_transfer(0x40, 0x52, 0, 0, b"")  # tắt chế độ QuickTime
            self.dev.set_configuration(self.orig_cfg)
        except usb.core.USBError as ex:
            print("Trả iPhone về chế độ USB thường bị lỗi (rút ra cắm lại là được):", ex, flush=True)


class Decoder:
    """Giải mã H.264/HEVC dạng AVCC bằng FFmpeg; giữ khung hình mới nhất."""

    def __init__(self):
        self.ctx, self.cfg, self.latest, self.frames = None, None, None, 0

    def feed(self, sdat, fmt):
        if fmt and fmt != self.cfg:
            self.cfg = fmt
            self.ctx = av.CodecContext.create("hevc" if fmt[0] in ("hvc1", "hev1") else "h264", "r")
            self.ctx.extradata = fmt[1]
            self.ctx.thread_type = "SLICE"  # không dùng frame threading: nó giữ lại vài khung, tăng độ trễ
        if self.ctx and sdat:
            for frame in self.ctx.decode(av.Packet(sdat)):
                self.latest = frame
                self.frames += 1


class Player:
    def __init__(self, root, height, ble):
        self.root, self.height, self.dw, self.dh = root, height, 0, 0
        self.dec, self.photo, self.running = Decoder(), None, True
        self.canvas = tk.Canvas(root, width=round(height * 0.462), height=height, bg="black", highlightthickness=0)
        self.canvas.pack()
        self.img = self.canvas.create_image(0, 0, anchor="nw")
        self.status = self.canvas.create_text(10, 10, anchor="nw", fill="#9f9", text="Đang kết nối iPhone qua USB…")
        self.ble = None
        if ble:
            from iphone_mirror import BleInput
            self.ble = BleInput(ble)
            f = lambda e: dict(fx=e.x / max(1, self.dw), fy=e.y / max(1, self.dh))
            self.canvas.bind("<Motion>", lambda e: self.ble.update(**f(e)))
            self.canvas.bind("<ButtonPress-1>", lambda e: self.ble.update(buttons=1, **f(e)))
            self.canvas.bind("<ButtonRelease-1>", lambda e: self.ble.update(buttons=0, **f(e)))
            self.canvas.bind("<MouseWheel>", lambda e: self.ble.update(wheel=2 * round(e.delta / 120), **f(e)))
        threading.Thread(target=self.usb_loop, daemon=True).start()
        self.t_fps, self.n_fps = time.perf_counter(), 0
        self.refresh()

    def usb_loop(self):
        for attempt in range(1, 4):
            try:
                self.link = UsbLink()
                self.session = QtSession(self.link.write, self.dec.feed)
                buf = b""
                while self.running:
                    msgs, buf = split_messages(buf + self.link.read())
                    for m in msgs:
                        self.session.handle(m)
                return
            except (SystemExit, usb.core.USBError) as ex:
                print(f"Lỗi USB (lần {attempt}):", ex, flush=True)
                if self.dec.frames or attempt == 3:  # đã có hình rồi mới lỗi thì báo luôn, không thử lại
                    self.root.after(0, self.canvas.itemconfig, self.status, {"text": f"Lỗi: {ex}", "fill": "#f88"})
                    return
                time.sleep(1.5)  # thường do iPhone vừa kết nối lại với Windows; chờ rồi thử lại

    def refresh(self):
        frame, self.dec.latest = self.dec.latest, None
        if frame is not None:
            if not self.dw:  # kích thước theo tỉ lệ video thật
                self.dh = self.height
                self.dw = round(self.height * frame.width / frame.height)
                self.canvas.config(width=self.dw, height=self.dh)
            self.photo = ImageTk.PhotoImage(frame.to_image(width=self.dw, height=self.dh))
            self.canvas.itemconfig(self.img, image=self.photo)
            self.n_fps += 1
        if time.perf_counter() - self.t_fps >= 1:
            self.canvas.itemconfig(self.status, text=f"{self.n_fps} khung/s" if self.dw else "Đang chờ hình từ iPhone…")
            if self.dw:
                print(f"{self.n_fps} khung/s (giải mã {self.dec.frames})", flush=True)
            self.t_fps, self.n_fps = time.perf_counter(), 0
        self.root.after(8, self.refresh)

    def close(self):
        self.running = False
        if getattr(self, "session", None):
            try:
                self.session.close()
            except usb.core.USBError:
                pass  # iPhone chưa mở phiên (chưa gửi ping) thì gửi hpa0 sẽ hết giờ; vẫn phải trả iPhone về USB thường
            self.link.close()
        if self.ble:
            self.ble.close()


def self_test():
    assert M("ping").to_bytes(4, "little") == b"gnip"
    d = ser_dict({"Error": 0})
    assert elements(d)[0][0] == "dict" and len(d) == 42  # dict(8) + keyv(8) + strk "Error"(13) + nmbv u32(13)
    msgs, rest = split_messages(PING + PING[:6])
    assert msgs == [PING[4:]] and rest == PING[:6]
    assert len(rply_clock(1, 2)) == 28 and len(rply_empty(1, b"\0" * 4)) == 24 and len(rply_empty(1, struct.pack("<d", 1.0))) == 28
    assert len(ASBD) == 56 and len(asyn(1, "need")) == 20
    # sbuf giả: fdsc (codec avc1, extensions{49:{105: avcC}}) + sdat
    avcc = bytes([1, 0x64, 0, 0x28, 0xFF, 0xE1, 0, 2, 0x67, 0x64, 1, 0, 1, 0x68])
    idx = lambda k, v: el("keyv", el("idxk", struct.pack("<H", k)) + v)
    fdsc = el("fdsc", el("mdia", struct.pack("<I", M("vide"))) + el("vdim", struct.pack("<II", 1170, 2532))
              + el("codc", struct.pack("<I", M("avc1"))) + el("extn", idx(49, el("dict", idx(105, el("datv", avcc))))))
    sbuf = el("sbuf", el("opts", struct.pack("<QIIQ", 5, 600, 1, 0)) + fdsc + el("sdat", b"\0\0\0\x02\x65\x88"))
    assert parse_sbuf(sbuf) == (b"\0\0\0\x02\x65\x88", ("avc1", avcc), (5, 600))
    # máy trạng thái: ping -> ping; cvrp -> need + reply; feed -> on_sample + need
    sent, got = [], []
    s = QtSession(sent.append, lambda a, b: got.append((a, b)))
    s.handle(PING[4:])
    cvrp = struct.pack("<IQI", M("sync"), 1, M("cvrp")) + struct.pack("<QQ", 7, 0xABC) + ser_dict({})
    s.handle(cvrp)
    assert sent[0] == PING and sent[1] == asyn(0xABC, "need") and sent[2] == rply_clock(7, 0xABC + 0x1000AF)
    s.handle(struct.pack("<IQI", M("asyn"), 1, M("feed")) + sbuf)
    assert got == [(b"\0\0\0\x02\x65\x88", ("avc1", avcc))] and sent[-1] == asyn(0xABC, "need")
    print("self-test OK")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ble", metavar="TRANSPORT", help="điều khiển bằng chuột Bluetooth Bumble, vd. usb:8087:0033")
    ap.add_argument("--height", type=int, default=800)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        self_test()
        raise SystemExit
    root = tk.Tk()
    root.title("iPhone (USB)")
    root.resizable(False, False)
    player = Player(root, args.height, args.ble)
    root.mainloop()
    player.close()
