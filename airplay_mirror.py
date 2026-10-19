"""Máy thu phản chiếu màn hình AirPlay (AirPlay Screen Mirroring Receiver).
Chạy được:
  1. Qua Wi-Fi / LAN (PC cắm Ethernet hoặc Wi-Fi cùng router với iPhone).
  2. Qua dây USB: iPhone bật Personal Hotspot qua USB, Windows nhận mạng 172.20.10.x.

Giao thức AirPlay:
  - mDNS (Bonjour / dnssd.dll): công bố dịch vụ _airplay._tcp và _raop._tcp trên cổng 7000.
  - RTSP/HTTP trên cổng 7000: /info, /pair-setup, /pair-verify, /fp-setup, SETUP, RECORD, v.v.
  - FairPlay: giải mã ekey bằng playfair.dll (biên dịch từ UxPlay lib/playfair).
  - Luồng hình TCP: gói 128-byte header, loại 1 = avcC (SPS/PPS), loại 0 = khung hình mã hoá AES-128-CTR.
  - Giải mã H.264/HEVC bằng Decoder trong usb_mirror.py (PyAV).
  - Điều khiển bằng chuột Bluetooth (BleInput trong iphone_mirror.py) khi chạy với --ble.

Chạy:  python airplay_mirror.py [--ble usb:8087:0033] [--height 800] [--self-test]
"""
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import os
import plistlib
import select
import socket
import struct
import sys
import threading
import time
import tkinter as tk
import uuid

from cryptography.hazmat.primitives.asymmetric import ed25519, x25519
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
import av
from PIL import ImageTk

from usb_mirror import Decoder

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
DLL_PATH = os.path.join(HERE, "playfair.dll")

# 4 bảng phản hồi chuẩn cho FairPlay fp-setup mode 0..3 (theo UxPlay fairplay_playfair.c)
FP_REPLY_MESSAGES = [
    bytes([0x46,0x50,0x4c,0x59,0x03,0x01,0x02,0x00,0x00,0x00,0x00,0x82,0x02,0x00,0x0f,0x9f,0x3f,0x9e,0x0a,0x25,0x21,0xdb,0xdf,0x31,0x2a,0xb2,0xbf,0xb2,0x9e,0x8d,0x23,0x2b,0x63,0x76,0xa8,0xc8,0x18,0x70,0x1d,0x22,0xae,0x93,0xd8,0x27,0x37,0xfe,0xaf,0x9d,0xb4,0xfd,0xf4,0x1c,0x2d,0xba,0x9d,0x1f,0x49,0xca,0xaa,0xbf,0x65,0x91,0xac,0x1f,0x7b,0xc6,0xf7,0xe0,0x66,0x3d,0x21,0xaf,0xe0,0x15,0x65,0x95,0x3e,0xab,0x81,0xf4,0x18,0xce,0xed,0x09,0x5a,0xdb,0x7c,0x3d,0x0e,0x25,0x49,0x09,0xa7,0x98,0x31,0xd4,0x9c,0x39,0x82,0x97,0x34,0x34,0xfa,0xcb,0x42,0xc6,0x3a,0x1c,0xd9,0x11,0xa6,0xfe,0x94,0x1a,0x8a,0x6d,0x4a,0x74,0x3b,0x46,0xc3,0xa7,0x64,0x9e,0x44,0xc7,0x89,0x55,0xe4,0x9d,0x81,0x55,0x00,0x95,0x49,0xc4,0xe2,0xf7,0xa3,0xf6,0xd5,0xba]),
    bytes([0x46,0x50,0x4c,0x59,0x03,0x01,0x02,0x00,0x00,0x00,0x00,0x82,0x02,0x01,0xcf,0x32,0xa2,0x57,0x14,0xb2,0x52,0x4f,0x8a,0xa0,0xad,0x7a,0xf1,0x64,0xe3,0x7b,0xcf,0x44,0x24,0xe2,0x00,0x04,0x7e,0xfc,0x0a,0xd6,0x7a,0xfc,0xd9,0x5d,0xed,0x1c,0x27,0x30,0xbb,0x59,0x1b,0x96,0x2e,0xd6,0x3a,0x9c,0x4d,0xed,0x88,0xba,0x8f,0xc7,0x8d,0xe6,0x4d,0x91,0xcc,0xfd,0x5c,0x7b,0x56,0xda,0x88,0xe3,0x1f,0x5c,0xce,0xaf,0xc7,0x43,0x19,0x95,0xa0,0x16,0x65,0xa5,0x4e,0x19,0x39,0xd2,0x5b,0x94,0xdb,0x64,0xb9,0xe4,0x5d,0x8d,0x06,0x3e,0x1e,0x6a,0xf0,0x7e,0x96,0x56,0x16,0x2b,0x0e,0xfa,0x40,0x42,0x75,0xea,0x5a,0x44,0xd9,0x59,0x1c,0x72,0x56,0xb9,0xfb,0xe6,0x51,0x38,0x98,0xb8,0x02,0x27,0x72,0x19,0x88,0x57,0x16,0x50,0x94,0x2a,0xd9,0x46,0x68,0x8a]),
    bytes([0x46,0x50,0x4c,0x59,0x03,0x01,0x02,0x00,0x00,0x00,0x00,0x82,0x02,0x02,0xc1,0x69,0xa3,0x52,0xee,0xed,0x35,0xb1,0x8c,0xdd,0x9c,0x58,0xd6,0x4f,0x16,0xc1,0x51,0x9a,0x89,0xeb,0x53,0x17,0xbd,0x0d,0x43,0x36,0xcd,0x68,0xf6,0x38,0xff,0x9d,0x01,0x6a,0x5b,0x52,0xb7,0xfa,0x92,0x16,0xb2,0xb6,0x54,0x82,0xc7,0x84,0x44,0x11,0x81,0x21,0xa2,0xc7,0xfe,0xd8,0x3d,0xb7,0x11,0x9e,0x91,0x82,0xaa,0xd7,0xd1,0x8c,0x70,0x63,0xe2,0xa4,0x57,0x55,0x59,0x10,0xaf,0x9e,0x0e,0xfc,0x76,0x34,0x7d,0x16,0x40,0x43,0x80,0x7f,0x58,0x1e,0xe4,0xfb,0xe4,0x2c,0xa9,0xde,0xdc,0x1b,0x5e,0xb2,0xa3,0xaa,0x3d,0x2e,0xcd,0x59,0xe7,0xee,0xe7,0x0b,0x36,0x29,0xf2,0x2a,0xfd,0x16,0x1d,0x87,0x73,0x53,0xdd,0xb9,0x9a,0xdc,0x8e,0x07,0x00,0x6e,0x56,0xf8,0x50,0xce]),
    bytes([0x46,0x50,0x4c,0x59,0x03,0x01,0x02,0x00,0x00,0x00,0x00,0x82,0x02,0x03,0x90,0x01,0xe1,0x72,0x7e,0x0f,0x57,0xf9,0xf5,0x88,0x0d,0xb1,0x04,0xa6,0x25,0x7a,0x23,0xf5,0xcf,0xff,0x1a,0xbb,0xe1,0xe9,0x30,0x45,0x25,0x1a,0xfb,0x97,0xeb,0x9f,0xc0,0x01,0x1e,0xbe,0x0f,0x3a,0x81,0xdf,0x5b,0x69,0x1d,0x76,0xac,0xb2,0xf7,0xa5,0xc7,0x08,0xe3,0xd3,0x28,0xf5,0x6b,0xb3,0x9d,0xbd,0xe5,0xf2,0x9c,0x8a,0x17,0xf4,0x81,0x48,0x7e,0x3a,0xe8,0x63,0xc6,0x78,0x32,0x54,0x22,0xe6,0xf7,0x8e,0x16,0x6d,0x18,0xaa,0x7f,0xd6,0x36,0x25,0x8b,0xce,0x28,0x72,0x6f,0x66,0x1f,0x73,0x88,0x93,0xce,0x44,0x31,0x1e,0x4b,0xe6,0xc0,0x53,0x51,0x93,0xe5,0xef,0x72,0xe8,0x68,0x62,0x33,0x72,0x9c,0x22,0x7d,0x82,0x0c,0x99,0x94,0x45,0xd8,0x92,0x46,0xc8,0xc3,0x59])
]

_playfair_dll = None


def load_playfair():
    global _playfair_dll
    if _playfair_dll is None:
        if not os.path.exists(DLL_PATH):
            raise FileNotFoundError(f"Không tìm thấy {DLL_PATH}. Cần build playfair.dll trước.")
        _playfair_dll = ctypes.CDLL(DLL_PATH)
    return _playfair_dll


def playfair_decrypt_key(msg3_bytes, ekey_bytes):
    """Giải mã 72 byte ekey bằng 164 byte msg3 -> khoá AES 16 byte."""
    dll = load_playfair()
    out = (ctypes.c_uint8 * 16)()
    m = (ctypes.c_uint8 * 164).from_buffer_copy(msg3_bytes)
    e = (ctypes.c_uint8 * 72).from_buffer_copy(ekey_bytes)
    dll.playfair_decrypt(m, e, out)
    return bytes(out)


def get_mac_address():
    """Lấy địa chỉ MAC của máy dạng chữ thường (aa:bb:cc:dd:ee:ff)."""
    node = uuid.getnode()
    return ":".join(f"{(node >> i) & 0xff:02x}" for i in (40, 32, 24, 16, 8, 0))


def get_local_ips():
    """Lấy danh sách các IP IPv4 đang hoạt động (LAN, Wi-Fi, Personal Hotspot USB 172.20.10.x)."""
    ips = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and not ip.startswith("169.254.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue).IPAddress"],
            capture_output=True, text=True, timeout=2,
            creationflags=0x08000000
        ).stdout
        for line in out.splitlines():
            ip = line.strip()
            if ip and not ip.startswith("127.") and not ip.startswith("169.254.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    return ips


def protect_pc_internet():
    """Đảm bảo Windows không bao giờ dùng 4G của iPhone để đi Internet.
    Tự động đặt InterfaceMetric của card 'Apple Mobile Device Ethernet' (172.20.10.*)
    lên 1000 (mức ưu tiên thấp nhất), để mạng LAN Ethernet của PC luôn ưu tiên 100%.
    """
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-NetIPAddress -IPAddress '172.20.10.*' -ErrorAction SilentlyContinue | ForEach-Object { Set-NetIPInterface -InterfaceIndex $_.InterfaceIndex -InterfaceMetric 1000 -ErrorAction SilentlyContinue }"],
            capture_output=True, timeout=2, creationflags=0x08000000
        )
    except Exception:
        pass


def build_txt_record(items):
    """Tạo TXT record theo chuẩn DNS-SD: mỗi mục gồm 1 byte độ dài tiếp theo là chuỗi key=value."""
    chunks = []
    for k, v in items:
        entry = f"{k}={v}".encode("latin1")
        if len(entry) > 255:
            entry = entry[:255]
        chunks.append(bytes([len(entry)]) + entry)
    return b"".join(chunks)


DNSSD_REPLY_CB = ctypes.WINFUNCTYPE(
    None,
    ctypes.c_void_p,  # sdRef
    ctypes.c_uint32,  # flags
    ctypes.c_int32,   # errorCode
    ctypes.c_char_p,  # name
    ctypes.c_char_p,  # regtype
    ctypes.c_char_p,  # domain
    ctypes.c_void_p   # context
)


class BonjourRegistrar:
    """Đăng ký dịch vụ AirPlay và RAOP qua Bonjour (dnssd.dll)."""

    def __init__(self, name, port, mac, ed_pub_hex):
        self.dnssd = ctypes.cdll.LoadLibrary("dnssd.dll")
        self.airplay_ref = ctypes.c_void_p()
        self.raop_ref = ctypes.c_void_p()
        self.running = True

        self._cb = DNSSD_REPLY_CB(self._on_register)

        # flags=0x4 (bit 2: AudioCableAttached, bit 7: 0 => không yêu cầu mật khẩu)
        # pw=false: không dùng mật khẩu
        # features=0x527FFEE6: Chuẩn UxPlay (tắt bit 27 Legacy Pairing để không bắt mã PIN, bật đầy đủ bit tương thích Screen Mirroring cho iOS)
        airplay_txt = [
            ("deviceid", mac.lower()),
            ("features", "0x527FFEE6,0x0"),
            ("flags", "0x4"),
            ("model", "AppleTV3,2"),
            ("pk", ed_pub_hex),
            ("pi", "2e388006-13ba-4041-9a67-25dd4a43d536"),
            ("pw", "false"),
            ("srcvers", "220.68"),
            ("vv", "2"),
        ]
        self.airplay_txt_bytes = build_txt_record(airplay_txt)

        raop_txt = [
            ("ch", "2"),
            ("cn", "0,1,2,3"),
            ("da", "true"),
            ("et", "0,3,5"),
            ("vv", "2"),
            ("ft", "0x527FFEE6,0x0"),
            ("am", "AppleTV3,2"),
            ("md", "0,1,2"),
            ("rhd", "5.6.0.0"),
            ("pw", "false"),
            ("sf", "0x4"),
            ("sr", "44100"),
            ("ss", "16"),
            ("sv", "false"),
            ("tp", "UDP"),
            ("txtvers", "1"),
            ("vs", "220.68"),
            ("vn", "65537"),
            ("pk", ed_pub_hex),
        ]
        self.raop_txt_bytes = build_txt_record(raop_txt)

        # Đăng ký _airplay._tcp
        err1 = self.dnssd.DNSServiceRegister(
            ctypes.byref(self.airplay_ref),
            0, 0,
            name.encode("utf-8"),
            b"_airplay._tcp",
            None, None,
            socket.htons(port),
            len(self.airplay_txt_bytes),
            self.airplay_txt_bytes,
            self._cb, None
        )
        if err1 != 0:
            print(f"Cảnh báo: Đăng ký Bonjour _airplay._tcp lỗi {err1}", flush=True)

        # Đăng ký _raop._tcp với tên AABBCCDDEEFF@TênMáy
        mac_compact = mac.replace(":", "").upper()
        raop_name = f"{mac_compact}@{name}"
        err2 = self.dnssd.DNSServiceRegister(
            ctypes.byref(self.raop_ref),
            0, 0,
            raop_name.encode("utf-8"),
            b"_raop._tcp",
            None, None,
            socket.htons(port),
            len(self.raop_txt_bytes),
            self.raop_txt_bytes,
            self._cb, None
        )
        if err2 != 0:
            print(f"Cảnh báo: Đăng ký Bonjour _raop._tcp lỗi {err2}", flush=True)

        threading.Thread(target=self._pump_loop, daemon=True).start()

    def _on_register(self, sdRef, flags, errorCode, name, regtype, domain, context):
        if errorCode == 0:
            r = regtype.decode("latin1") if regtype else ""
            n = name.decode("utf-8", errors="ignore") if name else ""
            print(f"Bonjour đã xác nhận công bố dịch vụ: {n} ({r})", flush=True)
        else:
            print(f"Bonjour lỗi đăng ký: {errorCode}", flush=True)

    def _pump_loop(self):
        """Lắng nghe socket IPC của Bonjour để hoàn tất và duy trì đăng ký dịch vụ."""
        while self.running:
            fds = []
            for ref in (self.airplay_ref, self.raop_ref):
                if ref.value:
                    try:
                        fd = self.dnssd.DNSServiceRefSockFD(ref)
                        if fd > 0:
                            fds.append((fd, ref))
                    except Exception:
                        pass
            if not fds:
                time.sleep(0.2)
                continue
            try:
                r, _, _ = select.select([fd for fd, _ in fds], [], [], 0.5)
                for fd, ref in fds:
                    if fd in r and self.running and ref.value:
                        self.dnssd.DNSServiceProcessResult(ref)
            except Exception:
                pass

    def close(self):
        self.running = False
        if self.airplay_ref.value:
            self.dnssd.DNSServiceRefDeallocate(self.airplay_ref)
            self.airplay_ref = ctypes.c_void_p()
        if self.raop_ref.value:
            self.dnssd.DNSServiceRefDeallocate(self.raop_ref)
            self.raop_ref = ctypes.c_void_p()


def recv_exact(sock, n):
    """Đọc chính xác n byte từ socket; trả về bytes hoặc None nếu ngắt kết nối."""
    buf = bytearray()
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except (socket.timeout, OSError):
            return None
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


class AirPlaySession:
    """Phiên kết nối của một iPhone: lưu trữ khoá ghép đôi, FairPlay và luồng hình."""

    def __init__(self, client_ip, server_ed_priv, server_ed_pub):
        self.client_ip = client_ip
        self.server_ed_priv = server_ed_priv
        self.server_ed_pub = server_ed_pub

        self.client_x_pub = None
        self.client_ed_pub = None
        self.server_x_priv = None
        self.server_x_pub = None
        self.ecdh_secret = None

        self.fp_keymsg = None
        self.aeskey = None

        self.video_cipher = None
        self.video_sock = None
        self.video_port = 0

        # Cổng UDP cho timing và audio
        self.timing_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.timing_sock.bind(("0.0.0.0", 0))
        self.timing_port = self.timing_sock.getsockname()[1]

        self.audio_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.audio_sock.bind(("0.0.0.0", 0))
        self.audio_port = self.audio_sock.getsockname()[1]

        self.ctrl_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.ctrl_sock.bind(("0.0.0.0", 0))
        self.ctrl_port = self.ctrl_sock.getsockname()[1]

        self.aesiv = None
        self.audio_active = False
        self.audio_ct = 8
        self.audio_spf = 480
        self.seen_audio_seqnums = set()

    def close(self):
        self.audio_active = False
        for s in (self.video_sock, self.timing_sock, self.audio_sock, self.ctrl_sock):
            if s:
                try:
                    s.close()
                except OSError:
                    pass


class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", wintypes.WORD),
        ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD),
        ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD),
        ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD)
    ]


class WAVEHDR(ctypes.Structure):
    pass

WAVEHDR._fields_ = [
    ("lpData", ctypes.c_char_p),
    ("dwBufferLength", wintypes.DWORD),
    ("dwBytesRecorded", wintypes.DWORD),
    ("dwUser", ctypes.c_void_p),
    ("dwFlags", wintypes.DWORD),
    ("dwLoops", wintypes.DWORD),
    ("lpNext", ctypes.c_void_p),
    ("reserved", ctypes.c_void_p),
]


class WaveOutPlayer:
    """Trình phát âm thanh PCM 44.1kHz 16-bit stereo qua Windows Multimedia API (winmm.dll)."""

    def __init__(self, sample_rate=44100, channels=2):
        self.sample_rate = sample_rate
        self.channels = channels
        self.winmm = ctypes.windll.winmm
        self.h_waveout = wintypes.HANDLE()
        self.wfx = WAVEFORMATEX()
        self.wfx.wFormatTag = 1  # WAVE_FORMAT_PCM
        self.wfx.nChannels = channels
        self.wfx.nSamplesPerSec = sample_rate
        self.wfx.wBitsPerSample = 16
        self.wfx.nBlockAlign = channels * 2
        self.wfx.nAvgBytesPerSec = sample_rate * self.wfx.nBlockAlign
        self.wfx.cbSize = 0

        res = self.winmm.waveOutOpen(ctypes.byref(self.h_waveout), -1, ctypes.byref(self.wfx), 0, 0, 0)
        self.opened = (res == 0)
        self.lock = threading.Lock()
        self.headers = []
        self.volume = 1.0
        self.running = True
        threading.Thread(target=self._cleaner, daemon=True).start()

    def set_volume(self, vol):
        self.volume = max(0.0, min(1.0, float(vol)))
        if self.opened:
            v16 = int(self.volume * 65535)
            dw_vol = (v16 << 16) | v16
            self.winmm.waveOutSetVolume(self.h_waveout, dw_vol)

    def write(self, pcm_data):
        if not self.opened or not pcm_data:
            return
        buf = ctypes.create_string_buffer(pcm_data)
        hdr = WAVEHDR()
        hdr.lpData = ctypes.cast(buf, ctypes.c_char_p)
        hdr.dwBufferLength = len(pcm_data)
        with self.lock:
            if self.winmm.waveOutPrepareHeader(self.h_waveout, ctypes.byref(hdr), ctypes.sizeof(hdr)) == 0:
                self.winmm.waveOutWrite(self.h_waveout, ctypes.byref(hdr), ctypes.sizeof(hdr))
                self.headers.append((hdr, buf))

    def _cleaner(self):
        while self.running:
            time.sleep(0.05)
            with self.lock:
                rem = []
                for hdr, buf in self.headers:
                    if hdr.dwFlags & 1:  # WHDR_DONE
                        self.winmm.waveOutUnprepareHeader(self.h_waveout, ctypes.byref(hdr), ctypes.sizeof(hdr))
                    else:
                        rem.append((hdr, buf))
                self.headers = rem

    def close(self):
        self.running = False
        if self.opened:
            self.winmm.waveOutReset(self.h_waveout)
            with self.lock:
                for hdr, buf in self.headers:
                    self.winmm.waveOutUnprepareHeader(self.h_waveout, ctypes.byref(hdr), ctypes.sizeof(hdr))
                self.headers.clear()
            self.winmm.waveOutClose(self.h_waveout)
            self.opened = False


class AirPlayAudioDecoder:
    """Giải mã âm thanh AirPlay (AAC-ELD hoặc ALAC) thành PCM 44.1kHz 16-bit stereo bằng PyAV."""

    def __init__(self):
        self.aac_ctx = None
        self.alac_ctx = None
        self.resampler = av.AudioResampler(format="s16", layout="stereo", rate=44100)

    def _init_aac(self):
        ctx = av.CodecContext.create("aac", "r")
        ctx.extradata = bytes.fromhex("f8e85000")
        ctx.open()
        return ctx

    def _init_alac(self):
        ctx = av.CodecContext.create("alac", "r")
        ctx.extradata = bytes.fromhex("00000024616c616300000000000001600010280a0e0200ff00000000000000000000ac44")
        ctx.open()
        return ctx

    def decode(self, payload, ct):
        try:
            if ct == 8:  # AAC-ELD
                if self.aac_ctx is None:
                    self.aac_ctx = self._init_aac()
                ctx = self.aac_ctx
            elif ct == 2:  # ALAC
                if self.alac_ctx is None:
                    self.alac_ctx = self._init_alac()
                ctx = self.alac_ctx
            else:
                return None

            packet = av.Packet(payload)
            frames = ctx.decode(packet)
            chunks = []
            for f in frames:
                for rf in self.resampler.resample(f):
                    chunks.append(bytes(rf.planes[0]))
            return b"".join(chunks) if chunks else None
        except Exception:
            return None


def nal_chain_ok(buf):
    """Dữ liệu là chuỗi NAL [độ dài 4 byte BE][NAL] khớp đúng hết gói, mỗi NAL có bit forbidden = 0."""
    off = 0
    while off + 5 <= len(buf):
        n = int.from_bytes(buf[off:off + 4], "big")
        if n <= 0 or off + 4 + n > len(buf) or buf[off + 4] & 0x80:
            return False
        off += 4 + n
    return off == len(buf)


def pick_video_cipher(candidates, first_payload):
    """Chọn khoá giải mã đúng bằng gói hình đầu tiên; trả về bộ giải mã đã đi qua gói đó (giữ đúng trạng thái CTR)."""
    for i, (key, iv) in enumerate(candidates):
        if nal_chain_ok(AirPlayStreamDecryptor(key, iv).decrypt(first_payload)):
            print(f"Luồng hình: dùng khoá ứng viên {i}", flush=True)
            return AirPlayStreamDecryptor(key, iv)
    return None


class AirPlayStreamDecryptor:
    """Bộ giải mã luồng video AirPlay theo chuẩn mirror_buffer.c (UxPlay).
    Quản lý keystream AES-CTR block 16 byte có lưu phần dư (og) giữa các gói.
    """

    def __init__(self, key, iv):
        # AES-CTR của cryptography tự giữ keystream dư giữa các lần update -> giống mirror_buffer.c, nhanh hơn XOR từng byte
        self.ctr = Cipher(algorithms.AES(key), modes.CTR(iv)).decryptor()

    def decrypt(self, data):
        return self.ctr.update(data)

    def _decrypt_slow(self, data):
        out = bytearray(len(data))
        pos = 0

        # 1. Dùng phần thừa của block 16-byte từ gói trước (nếu có)
        if self.og:
            use_len = min(len(self.og), len(data))
            for i in range(use_len):
                out[i] = data[i] ^ self.og[i]
            self.og = self.og[use_len:]
            pos = use_len

        # 2. Giải mã các block 16 byte trọn vẹn
        full_blocks = (len(data) - pos) // 16
        for _ in range(full_blocks):
            keystream = self.cipher.update(self.counter.to_bytes(16, "big"))
            self.counter += 1
            for j in range(16):
                out[pos + j] = data[pos + j] ^ keystream[j]
            pos += 16

        # 3. Xử lý phần dư cuối cùng (< 16 byte)
        rem = len(data) - pos
        if rem > 0:
            keystream = self.cipher.update(self.counter.to_bytes(16, "big"))
            self.counter += 1
            for j in range(rem):
                out[pos + j] = data[pos + j] ^ keystream[j]
            self.og = keystream[rem:]

        return bytes(out)


class AirPlayServer:
    """Máy chủ AirPlay RTSP/HTTP trên cổng 7000."""

    def __init__(self, port, on_frame, on_config, on_connect=None, audio_mode="pc"):
        self.port = port
        self.on_frame = on_frame
        self.on_config = on_config
        self.on_connect = on_connect
        self.audio_mode = audio_mode  # "pc", "iphone", "mute"
        self.audio_volume = 1.0
        self.running = True

        protect_pc_internet()

        self.audio_decoder = AirPlayAudioDecoder()
        self.audio_player = WaveOutPlayer()

        self.mac = get_mac_address()
        self.name = socket.gethostname()

        # Tạo khoá Ed25519 cho AirPlay pairing
        self.ed_priv = ed25519.Ed25519PrivateKey.generate()
        self.ed_pub = self.ed_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        self.ed_pub_hex = self.ed_pub.hex()

        self.sessions = {}
        self.lock = threading.Lock()
        self.display_size = (1920, 1080)  # khung màn hình khai cho iPhone ở GET /info; iPhone thu hình cho vừa khung này
        self.client_model = None  # ProductType iPhone gửi trong SETUP (vd. iPhone12,5)

        # Mở socket TCP cổng 7000
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind(("0.0.0.0", port))
        self.server_sock.listen(5)

        # Đăng ký mDNS
        self.bonjour = BonjourRegistrar(self.name, port, self.mac, self.ed_pub_hex)

        threading.Thread(target=self._accept_loop, daemon=True).start()

    def get_session(self, client_ip):
        with self.lock:
            if client_ip not in self.sessions:
                self.sessions[client_ip] = AirPlaySession(client_ip, self.ed_priv, self.ed_pub)
            return self.sessions[client_ip]

    def set_audio_mode(self, mode):
        self.audio_mode = mode
        if mode == "mute" and self.audio_player:
            self.audio_player.set_volume(0.0)
        elif mode == "pc" and self.audio_player:
            self.audio_player.set_volume(self.audio_volume)

    def set_volume(self, vol):
        self.audio_volume = max(0.0, min(1.0, float(vol)))
        if self.audio_mode == "pc" and self.audio_player:
            self.audio_player.set_volume(self.audio_volume)

    def _accept_loop(self):
        while self.running:
            try:
                conn, addr = self.server_sock.accept()
                threading.Thread(target=self._handle_client, args=(conn, addr), daemon=True).start()
            except OSError:
                break

    def _handle_client(self, conn, addr):
        client_ip = addr[0]
        session = self.get_session(client_ip)
        rfile = conn.makefile("rb")

        try:
            while self.running:
                line = rfile.readline()
                if not line:
                    break
                parts = line.decode("latin1").strip().split(" ", 2)
                if len(parts) < 3:
                    break
                method, url, version = parts[0], parts[1], parts[2]

                headers = {}
                while True:
                    hline = rfile.readline()
                    if not hline or hline in (b"\r\n", b"\n"):
                        break
                    hparts = hline.decode("latin1").strip().split(":", 1)
                    if len(hparts) == 2:
                        headers[hparts[0].strip().lower()] = hparts[1].strip()

                content_len = int(headers.get("content-length", 0))
                body = rfile.read(content_len) if content_len > 0 else b""

                resp_code, resp_reason, resp_hdrs, resp_body = self._dispatch(session, method, url, version, headers, body)

                resp_hdrs["Server"] = "AirTunes/220.68"
                if "cseq" in headers:
                    resp_hdrs["CSeq"] = headers["cseq"]
                resp_hdrs["Content-Length"] = str(len(resp_body))

                out_lines = [f"{version} {resp_code} {resp_reason}\r\n".encode("latin1")]
                for k, v in resp_hdrs.items():
                    out_lines.append(f"{k}: {v}\r\n".encode("latin1"))
                out_lines.append(b"\r\n")
                if resp_body:
                    out_lines.append(resp_body)
                conn.sendall(b"".join(out_lines))

                if resp_hdrs.get("Connection") == "close":
                    break
        except (socket.error, OSError):
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _dispatch(self, session, method, url, version, headers, body):
        path = url.split("?")[0]
        if path.startswith("rtsp://"):
            path = "/" + "/".join(path.split("/")[3:])

        if method == "OPTIONS":
            return 200, "OK", {"Public": "SETUP, RECORD, FLUSH, TEARDOWN, OPTIONS, GET_PARAMETER, SET_PARAMETER"}, b""

        if method == "GET" and "/info" in url:
            return self._handle_info(session, url, headers, body)

        if method == "POST" and path == "/pair-setup":
            return 200, "OK", {"Content-Type": "application/octet-stream"}, session.server_ed_pub

        if method == "POST" and path == "/pair-verify":
            return self._handle_pair_verify(session, body)

        if method == "POST" and path == "/fp-setup":
            return self._handle_fp_setup(session, body)

        if method == "SETUP":
            return self._handle_setup(session, body)

        if method == "RECORD":
            if self.on_connect:
                self.on_connect(session.client_ip)
            return 200, "OK", {"Audio-Latency": "0", "Audio-Jack-Status": "connected; type=analog"}, b""

        if method == "GET_PARAMETER":
            if b"volume" in body:
                return 200, "OK", {"Content-Type": "text/parameters"}, b"volume: 0.0\r\n"
            return 200, "OK", {}, b""

        # Từ chối các lệnh stream video media riêng lẻ (play/scrub/playback-info) để buộc iOS/TikTok phát video trên màn hình rồi mirror
        MEDIA_PATHS = (
            "/play", "/scrub", "/rate", "/stop", "/playback-info",
            "/action", "/slideshows", "/photo", "/stream.xml"
        )
        if any(path.startswith(p) for p in MEDIA_PATHS):
            return 405, "Method Not Allowed", {}, b""

        if method in ("SET_PARAMETER", "FLUSH", "POST"):
            return 200, "OK", {}, b""

        if method == "TEARDOWN":
            return 200, "OK", {"Connection": "close"}, b""

        return 200, "OK", {}, b""

    def _handle_info(self, session, url, headers, body):
        qualifier = None
        if body and body.startswith(b"bplist00"):
            try:
                req_plist = plistlib.loads(body)
                qualifier = req_plist.get("qualifier")
            except Exception:
                pass

        if qualifier == ["txtAirPlay"]:
            data = plistlib.dumps({"txtAirPlay": self.bonjour.airplay_txt_bytes}, fmt=plistlib.FMT_BINARY)
            return 200, "OK", {"Content-Type": "application/x-apple-binary-plist"}, data
        if qualifier == ["txtRAOP"]:
            data = plistlib.dumps({"txtRAOP": self.bonjour.raop_txt_bytes}, fmt=plistlib.FMT_BINARY)
            return 200, "OK", {"Content-Type": "application/x-apple-binary-plist"}, data

        info = {
            "deviceID": self.mac,
            "macAddress": self.mac,
            "name": self.name,
            "model": "AppleTV3,2",
            "pk": self.ed_pub,
            "pi": "2e388006-13ba-4041-9a67-25dd4a43d536",
            "vv": 2,
            "features": 0x527FFEE6,
            "statusFlags": 68,
            "sourceVersion": "220.68",
            "keepAliveLowPower": True,
            "keepAliveSendStatsAsBody": True,
            "initialVolume": 0.0,
            "displays": [{
                "uuid": "e0ff8a27-6738-3d56-8a16-cc53aacee925",
                "widthPhysical": 0,
                "heightPhysical": 0,
                "width": self.display_size[0],
                "height": self.display_size[1],
                "widthPixels": self.display_size[0],
                "heightPixels": self.display_size[1],
                "rotation": False,
                "refreshRate": 1.0 / 60.0,
                "maxFPS": 60,
                "overscanned": False,
                "features": 14,
            }],
            "audioLatencies": [
                {"type": 100, "audioType": "default", "inputLatencyMicros": 0, "outputLatencyMicros": False},
                {"type": 101, "audioType": "default", "inputLatencyMicros": 0, "outputLatencyMicros": False},
            ],
            "audioFormats": [
                {"type": 100, "audioInputFormats": 0x3FFFFFC, "audioOutputFormats": 0x3FFFFFC},
                {"type": 101, "audioInputFormats": 0x3FFFFFC, "audioOutputFormats": 0x3FFFFFC},
            ]
        }
        data = plistlib.dumps(info, fmt=plistlib.FMT_BINARY)
        return 200, "OK", {"Content-Type": "application/x-apple-binary-plist"}, data

    def _handle_pair_verify(self, session, body):
        if len(body) < 4:
            return 400, "Bad Request", {}, b""

        flag = body[0]
        if flag == 1:
            # Bước 1 của pair-verify: nhận client X25519 PK (32 byte) và client Ed25519 PK (32 byte)
            session.client_x_pub = body[4:36]
            session.client_ed_pub = body[36:68]

            session.server_x_priv = x25519.X25519PrivateKey.generate()
            session.server_x_pub = session.server_x_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

            client_x = x25519.X25519PublicKey.from_public_bytes(session.client_x_pub)
            session.ecdh_secret = session.server_x_priv.exchange(client_x)

            sig_msg = session.server_x_pub + session.client_x_pub
            sig = session.server_ed_priv.sign(sig_msg)

            key = hashlib.sha512(b"Pair-Verify-AES-Key" + session.ecdh_secret).digest()[:16]
            iv = hashlib.sha512(b"Pair-Verify-AES-IV" + session.ecdh_secret).digest()[:16]

            aes_enc = Cipher(algorithms.AES(key), modes.CTR(iv)).encryptor()
            enc_sig = aes_enc.update(sig)

            resp = session.server_x_pub + enc_sig
            return 200, "OK", {"Content-Type": "application/octet-stream"}, resp

        if flag == 0:
            # Bước 2 của pair-verify: nhận chữ ký client đã mã hoá
            key = hashlib.sha512(b"Pair-Verify-AES-Key" + session.ecdh_secret).digest()[:16]
            iv = hashlib.sha512(b"Pair-Verify-AES-IV" + session.ecdh_secret).digest()[:16]

            aes_dec = Cipher(algorithms.AES(key), modes.CTR(iv)).decryptor()
            aes_dec.update(b"\x00" * 64)  # Bỏ qua 64 byte đầu của dòng CTR
            client_sig = aes_dec.update(body[4:68])

            client_ed = ed25519.Ed25519PublicKey.from_public_bytes(session.client_ed_pub)
            client_ed.verify(client_sig, session.client_x_pub + session.server_x_pub)
            return 200, "OK", {"Content-Type": "application/octet-stream"}, b""

        return 400, "Bad Request", {}, b""

    def _handle_fp_setup(self, session, body):
        if len(body) == 16:
            mode = body[14]
            if 0 <= mode < len(FP_REPLY_MESSAGES):
                return 200, "OK", {"Content-Type": "application/octet-stream"}, FP_REPLY_MESSAGES[mode]
            return 501, "Not Implemented", {}, b""

        if len(body) == 164:
            session.fp_keymsg = body
            resp = bytes([0x46, 0x50, 0x4c, 0x59, 0x03, 0x01, 0x04, 0x00, 0x00, 0x00, 0x00, 0x14]) + body[144:164]
            return 200, "OK", {"Content-Type": "application/octet-stream"}, resp

        return 400, "Bad Request", {}, b""

    def _handle_setup(self, session, body):
        req = plistlib.loads(body)

        # SETUP lần 1: giải ekey ra aeskey, lưu eiv, trả về timingPort
        if "ekey" in req and "eiv" in req:
            self.client_model = req.get("model") or self.client_model
            ekey = req["ekey"]
            session.aesiv = req["eiv"]
            raw_key = playfair_decrypt_key(session.fp_keymsg, ekey)
            session.raw_aeskey = raw_key
            if session.ecdh_secret:
                session.hashed_aeskey = hashlib.sha512(raw_key + session.ecdh_secret).digest()[:16]
            else:
                session.hashed_aeskey = raw_key
            session.aeskey = session.hashed_aeskey

            resp_dict = {
                "eventPort": 0,
                "timingPort": session.timing_port
            }
            data = plistlib.dumps(resp_dict, fmt=plistlib.FMT_BINARY)
            return 200, "OK", {"Content-Type": "application/x-apple-binary-plist"}, data

        # SETUP lần 2: cấu hình streams (luồng hình type 110, luồng âm thanh type 96)
        if "streams" in req:
            res_streams = []
            for st in req["streams"]:
                stype = st.get("type")
                if stype == 110:
                    cid = st.get("streamConnectionID", 0)
                    self._start_video_stream(session, cid)
                    res_streams.append({"type": 110, "dataPort": session.video_port})
                elif stype == 96:
                    ct = st.get("ct", 8)
                    spf = st.get("spf", 480)
                    remote_ctrl_port = st.get("controlPort", 0)
                    if self.audio_mode != "iphone":
                        self._start_audio_stream(session, ct, spf, remote_ctrl_port)
                        res_streams.append({
                            "type": 96,
                            "dataPort": session.audio_port,
                            "controlPort": session.ctrl_port
                        })

            data = plistlib.dumps({"streams": res_streams}, fmt=plistlib.FMT_BINARY)
            return 200, "OK", {"Content-Type": "application/x-apple-binary-plist"}, data

        return 400, "Bad Request", {}, b""

    def _start_video_stream(self, session, cid):
        # Tính khoá AES và IV cho luồng hình từ streamConnectionID
        # Khoá ứng viên: aeskey đã/không băm với ecdh_secret × streamConnectionID có dấu/không dấu (UxPlay in %PRIu64).
        # Gói hình đầu tiên sẽ chọn ra khoá đúng (xem pick_video_cipher).
        keys = [session.aeskey] + [k for k in (getattr(session, "raw_aeskey", None),) if k and k != session.aeskey]
        cids = [cid & 0xFFFFFFFFFFFFFFFF] + ([cid] if cid < 0 else [])
        session.video_candidates = [(hashlib.sha512(f"AirPlayStreamKey{c}".encode() + k).digest()[:16],
                                     hashlib.sha512(f"AirPlayStreamIV{c}".encode() + k).digest()[:16]) for k in keys for c in cids]
        session.video_cipher = None
        print(f"Luồng hình: streamConnectionID={cid}, {len(session.video_candidates)} khoá ứng viên", flush=True)

        if session.video_sock:
            try:
                session.video_sock.close()
            except OSError:
                pass

        vsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        vsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        vsock.bind(("0.0.0.0", 0))
        vsock.listen(1)
        session.video_port = vsock.getsockname()[1]
        session.video_sock = vsock

        threading.Thread(target=self._video_worker, args=(session, vsock), daemon=True).start()

    def _video_worker(self, session, vsock):
        try:
            vsock.settimeout(15.0)
            conn, addr = vsock.accept()
            conn.settimeout(None)  # màn hình iPhone đứng yên thì không có gói nào: không được coi im lặng là mất kết nối
            print(f"iPhone đã nối cổng luồng hình TCP: {addr}", flush=True)

            sps_pps_header = b""
            prepend_sps_pps = False
            counts, why = {}, "máy chủ dừng"

            while self.running:
                hdr = recv_exact(conn, 128)
                if not hdr:
                    why = "iPhone đóng kết nối"
                    break
                payload_size = struct.unpack_from("<I", hdr, 0)[0]
                packet_type = hdr[4]
                counts[packet_type] = counts.get(packet_type, 0) + 1
                if counts[packet_type] == 1:
                    print(f"Luồng hình: gói đầu tiên loại {packet_type}, {payload_size} byte", flush=True)

                if payload_size > 0:
                    payload = recv_exact(conn, payload_size)
                    if not payload:
                        break
                    if packet_type == 1:
                        # Gói SPS/PPS theo định dạng AirPlay
                        try:
                            sps_len = struct.unpack_from(">H", payload, 6)[0]
                            sps = payload[8:8 + sps_len]
                            pps_len = struct.unpack_from(">H", payload, sps_len + 9)[0]
                            pps = payload[sps_len + 11:sps_len + 11 + pps_len]
                            sps_pps_header = b"\x00\x00\x00\x01" + sps + b"\x00\x00\x00\x01" + pps
                            prepend_sps_pps = True
                            if self.on_frame:
                                self.on_frame(sps_pps_header)
                        except Exception:
                            pass
                    elif packet_type == 0:
                        # Gói khung hình H.264, giải mã AES-CTR
                        if session.video_cipher is None:
                            session.video_cipher = pick_video_cipher(session.video_candidates, payload)
                            if session.video_cipher is None:
                                print("Luồng hình: không khoá ứng viên nào giải mã được gói đầu tiên", flush=True)
                                break
                        decrypted = session.video_cipher.decrypt(payload)
                        # Đổi các độ dài NAL 4 byte BE thành start code Annex B \x00\x00\x00\x01
                        nalus = bytearray(decrypted)
                        offset = 0
                        while offset + 4 <= len(nalus):
                            nc_len = struct.unpack_from(">I", nalus, offset)[0]
                            if nc_len <= 0 or offset + 4 + nc_len > len(nalus):
                                break
                            nalus[offset:offset+4] = b"\x00\x00\x00\x01"
                            offset += 4 + nc_len

                        frame_chunk = bytes(nalus)
                        if counts[0] <= 2:
                            print(f"Luồng hình: NAL đã giải mã hợp lệ tới byte {offset}/{len(nalus)}, đầu = {bytes(nalus[:6]).hex()}", flush=True)
                        if prepend_sps_pps and sps_pps_header:
                            frame_chunk = sps_pps_header + frame_chunk
                            prepend_sps_pps = False

                        if self.on_frame:
                            try:
                                self.on_frame(frame_chunk)
                            except Exception:
                                pass
            conn.close()
            print(f"Luồng hình dừng ({why}); số gói theo loại: {counts}", flush=True)
        except Exception as ex:
            if self.running:
                print("Luồng hình kết thúc:", ex, flush=True)

    def _start_audio_stream(self, session, ct, spf, remote_ctrl_port=0):
        session.audio_active = True
        session.audio_ct = ct
        session.audio_spf = spf
        session.remote_ctrl_port = remote_ctrl_port
        session.seen_audio_seqnums = set()
        session.audio_key_verified = False
        session.audio_aeskey = getattr(session, "aeskey", session.raw_aeskey if hasattr(session, "raw_aeskey") else None)

        # Gửi gói UDP đục lỗ Windows Firewall (NAT traversal / state pinhole) tới iPhone
        if remote_ctrl_port > 0 and session.client_ip:
            try:
                session.audio_sock.sendto(b"\x00", (session.client_ip, remote_ctrl_port))
                session.ctrl_sock.sendto(b"\x00", (session.client_ip, remote_ctrl_port))
            except Exception:
                pass

        threading.Thread(target=self._audio_worker, args=(session,), daemon=True).start()
        threading.Thread(target=self._ctrl_worker, args=(session,), daemon=True).start()

    def _ctrl_worker(self, session):
        """Tiêu thụ các gói RTCP đồng bộ thời gian (0x54) và yêu cầu gửi lại từ iPhone."""
        sock = session.ctrl_sock
        sock.settimeout(1.0)
        while self.running and getattr(session, "audio_active", False):
            try:
                data, addr = sock.recvfrom(2048)
            except (socket.timeout, OSError):
                continue
            if not data:
                continue

    def _audio_worker(self, session):
        sock = session.audio_sock
        sock.settimeout(1.0)
        packet_count = 0
        while self.running and getattr(session, "audio_active", False):
            try:
                data, addr = sock.recvfrom(4096)
            except (socket.timeout, OSError):
                continue
            if len(data) < 12:
                continue

            seqnum = struct.unpack_from(">H", data, 2)[0]
            if seqnum in session.seen_audio_seqnums:
                continue
            session.seen_audio_seqnums.add(seqnum)
            if len(session.seen_audio_seqnums) > 1000:
                session.seen_audio_seqnums.clear()

            payload = data[12:]
            # Bỏ qua gói rỗng lúc bắt đầu stream (AAC-ELD marker 0x00 0x68 0x34 0x00)
            if not payload or (len(payload) == 4 and payload == b"\x00\x68\x34\x00"):
                continue
            # Bỏ qua format header rỗng của ALAC lúc mới khởi tạo
            if session.audio_ct == 2 and len(payload) == 32:
                continue

            if self.audio_mode == "mute":
                continue

            # Tự động xác thực và chọn khoá giải mã đúng giữa raw_aeskey và hashed_aeskey
            if not getattr(session, "audio_key_verified", False) and getattr(session, "aesiv", None):
                candidates = []
                if hasattr(session, "raw_aeskey") and session.raw_aeskey:
                    candidates.append(session.raw_aeskey)
                if hasattr(session, "hashed_aeskey") and session.hashed_aeskey and session.hashed_aeskey not in candidates:
                    candidates.append(session.hashed_aeskey)
                if hasattr(session, "aeskey") and session.aeskey and session.aeskey not in candidates:
                    candidates.append(session.aeskey)

                for cand in candidates:
                    try:
                        c = Cipher(algorithms.AES(cand), modes.CBC(session.aesiv)).decryptor()
                        test_dec = c.update(payload[:16])
                        if test_dec and test_dec[0] in (0x8c, 0x8d, 0x8e, 0x80, 0x81, 0x82, 0x20):
                            session.audio_aeskey = cand
                            session.audio_key_verified = True
                            print(f"Xác thực thành công khoá âm thanh AirPlay (loại {session.audio_ct}, byte magic: 0x{test_dec[0]:02x})", flush=True)
                            break
                    except Exception:
                        pass
                if not getattr(session, "audio_key_verified", False):
                    session.audio_aeskey = session.raw_aeskey if hasattr(session, "raw_aeskey") else session.aeskey

            key = getattr(session, "audio_aeskey", session.aeskey)
            if key and getattr(session, "aesiv", None):
                enc_len = (len(payload) // 16) * 16
                cipher = Cipher(algorithms.AES(key), modes.CBC(session.aesiv)).decryptor()
                decrypted = cipher.update(payload[:enc_len]) + payload[enc_len:]
            else:
                decrypted = payload

            if self.audio_mode == "pc" and self.audio_player:
                pcm = self.audio_decoder.decode(decrypted, session.audio_ct)
                if pcm:
                    self.audio_player.write(pcm)
                    packet_count += 1
                    if packet_count == 1:
                        print(f"Đang nhận và phát âm thanh AirPlay ra Loa máy tính PC (định dạng ct={session.audio_ct})...", flush=True)

    def close(self):
        self.running = False
        try:
            self.server_sock.close()
        except OSError:
            pass
        self.bonjour.close()
        if hasattr(self, "audio_player") and self.audio_player:
            self.audio_player.close()
        with self.lock:
            for s in self.sessions.values():
                s.close()


class AirPlayDecoder:
    """Giải mã luồng H.264 Annex B bằng PyAV."""

    def __init__(self):
        self.ctx = av.CodecContext.create("h264", "r")
        self.ctx.thread_type = "SLICE"
        self.latest = None
        self.frames = 0

    def feed(self, data, _unused_cfg=None):
        if not data:
            return
        try:
            for frame in self.ctx.decode(av.Packet(data)):
                self.latest = frame
                self.frames += 1
                if self.frames <= 3:
                    print(f"Đã giải mã khung hình {self.frames}: {frame.width}x{frame.height}", flush=True)
        except Exception:
            pass


class AirPlayPlayer:
    """Hiển thị khung hình AirPlay lên Tkinter và điều khiển bằng chuột Bluetooth BLE (bumble_mouse)."""

    def __init__(self, root, height, ble, audio_mode="pc"):
        self.root, self.height, self.dw, self.dh = root, height, 0, 0
        self.frame_size = (0, 0)
        self.dec, self.photo, self.running = AirPlayDecoder(), None, True
        self.canvas = tk.Canvas(root, width=round(height * 0.462), height=height, bg="black", highlightthickness=0)
        self.canvas.pack()
        self.img = self.canvas.create_image(0, 0, anchor="nw")
        self.status = self.canvas.create_text(10, 10, anchor="nw", fill="#9f9",
                                                text="Đang chờ iPhone kết nối AirPlay…")
        self.status_msg = "Đang chờ iPhone kết nối AirPlay…"
        self.ble = None
        if ble:
            from iphone_mirror import BleInput
            self.ble = BleInput(ble)
            f = lambda e: dict(fx=e.x / max(1, self.dw), fy=e.y / max(1, self.dh))
            self.canvas.bind("<Motion>", lambda e: self.ble.update(**f(e)))
            self.canvas.bind("<ButtonPress-1>", lambda e: self.ble.update(buttons=1, **f(e)))
            self.canvas.bind("<ButtonRelease-1>", lambda e: self.ble.update(buttons=0, **f(e)))
            self.canvas.bind("<MouseWheel>", lambda e: self.ble.update(wheel=2 * round(e.delta / 120), **f(e)))

        self.server = AirPlayServer(
            port=7000,
            on_frame=self.dec.feed,
            on_config=None,
            on_connect=self._on_client_connected,
            audio_mode=audio_mode
        )

        self.is_wired = False
        self.t_fps, self.n_fps = time.perf_counter(), 0
        self.refresh()

    def _on_client_connected(self, client_ip):
        self.is_wired = client_ip.startswith("172.20.10.")
        mode_str = "Cáp USB" if self.is_wired else "Wi-Fi"
        self.status_msg = f"iPhone ({client_ip} qua {mode_str}) đã kết nối AirPlay"
        self.root.after(0, self._update_ui_mode, mode_str)

    def _update_ui_mode(self, mode_str):
        self.root.title(f"iPhone (AirPlay - {mode_str})")
        self.canvas.itemconfig(self.status, text=self.status_msg)

    def refresh(self):
        frame, self.dec.latest = self.dec.latest, None
        if frame is not None:
            if not self.dw or self.frame_size != (frame.width, frame.height):
                self.frame_size = (frame.width, frame.height)
                self.dh = self.height
                self.dw = round(self.height * frame.width / frame.height)
                self.canvas.config(width=self.dw, height=self.dh)
            self.photo = ImageTk.PhotoImage(frame.to_image(width=self.dw, height=self.dh))
            self.canvas.itemconfig(self.img, image=self.photo)
            self.n_fps += 1

        if time.perf_counter() - self.t_fps >= 1:
            if self.dw:
                tag = " [Cáp USB]" if self.is_wired else " [Wi-Fi]"
                self.canvas.itemconfig(self.status, text=f"{self.n_fps} khung/s{tag}")
                print(f"{self.n_fps} khung/s{tag} (giải mã {self.dec.frames})", flush=True)
            else:
                self.canvas.itemconfig(self.status, text=self.status_msg)
            self.t_fps, self.n_fps = time.perf_counter(), 0

        if self.running:
            self.root.after(8, self.refresh)

    def close(self):
        self.running = False
        self.server.close()
        if self.ble:
            self.ble.close()


def self_test():
    """Kiểm tra toàn bộ thuật toán và thành phần mà không cần bật GUI / phần cứng."""
    print("--- Chạy self-test airplay_mirror.py ---", flush=True)

    # 1. Kiểm tra playfair.dll
    msg3 = bytes(range(164))
    ekey = bytes(range(72))
    key_out = playfair_decrypt_key(msg3, ekey)
    assert len(key_out) == 16
    assert key_out.hex() == "0f62f98ab1aec34681b0e5fa0e2ae0a2"
    print("1. playfair.dll giải mã ekey: OK")

    # 2. Kiểm tra định dạng TXT record DNS-SD
    txt_data = build_txt_record([("model", "AppleTV3,2"), ("vv", "2")])
    assert txt_data[0] == len(b"model=AppleTV3,2")
    assert txt_data[1:1 + len(b"model=AppleTV3,2")] == b"model=AppleTV3,2"
    print("2. Định dạng DNS-SD TXT record: OK")

    # 3. Kiểm tra bắt tay Pair-Verify (Ed25519 + X25519 + AES-128-CTR)
    s_ed_priv = ed25519.Ed25519PrivateKey.generate()
    s_ed_pub = s_ed_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    c_ed_priv = ed25519.Ed25519PrivateKey.generate()
    c_ed_pub = c_ed_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    c_x_priv = x25519.X25519PrivateKey.generate()
    c_x_pub = c_x_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

    # Client gửi step 1
    req1 = bytes([1, 0, 0, 0]) + c_x_pub + c_ed_pub
    # Server xử lý step 1
    s_x_priv = x25519.X25519PrivateKey.generate()
    s_x_pub = s_x_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    sec = s_x_priv.exchange(x25519.X25519PublicKey.from_public_bytes(c_x_pub))
    sig = s_ed_priv.sign(s_x_pub + c_x_pub)
    k = hashlib.sha512(b"Pair-Verify-AES-Key" + sec).digest()[:16]
    iv = hashlib.sha512(b"Pair-Verify-AES-IV" + sec).digest()[:16]
    enc_sig = Cipher(algorithms.AES(k), modes.CTR(iv)).encryptor().update(sig)
    resp1 = s_x_pub + enc_sig

    # Client xác minh step 1
    c_sec = c_x_priv.exchange(x25519.X25519PublicKey.from_public_bytes(s_x_pub))
    assert c_sec == sec
    c_k = hashlib.sha512(b"Pair-Verify-AES-Key" + c_sec).digest()[:16]
    c_iv = hashlib.sha512(b"Pair-Verify-AES-IV" + c_sec).digest()[:16]
    c_cipher = Cipher(algorithms.AES(c_k), modes.CTR(c_iv)).encryptor()
    dec_server_sig = c_cipher.update(enc_sig)
    s_ed_priv.public_key().verify(dec_server_sig, s_x_pub + c_x_pub)

    # Client gửi step 2
    c_sig = c_ed_priv.sign(c_x_pub + s_x_pub)
    enc_c_sig = c_cipher.update(c_sig)
    req2 = bytes([0, 0, 0, 0]) + enc_c_sig

    # Server xác minh step 2
    s_dec = Cipher(algorithms.AES(k), modes.CTR(iv)).decryptor()
    s_dec.update(b"\x00" * 64)
    dec_c_sig = s_dec.update(req2[4:])
    c_ed_priv.public_key().verify(dec_c_sig, c_x_pub + s_x_pub)
    print("3. Bắt tay Pair-Verify hai chiều: OK")

    # 4. Kiểm tra dẫn xuất khoá giải mã luồng hình AES-128-CTR
    stream_id = 1234567890
    vkey = hashlib.sha512(f"AirPlayStreamKey{stream_id}".encode() + key_out).digest()[:16]
    viv = hashlib.sha512(f"AirPlayStreamIV{stream_id}".encode() + key_out).digest()[:16]
    enc = Cipher(algorithms.AES(vkey), modes.CTR(viv)).encryptor()
    dec = Cipher(algorithms.AES(vkey), modes.CTR(viv)).decryptor()
    plain = b"Test Video NAL Unit Payload 123456789"
    cipher_text = enc.update(plain)
    assert dec.update(cipher_text) == plain
    print("4. Dẫn xuất khoá và giải mã liên tục AES-128-CTR: OK")

    # 5. Kiểm tra plistlib binary plist
    info = {"deviceID": "00:11:22:33:44:55", "features": 0x5A7FFEE6}
    bp = plistlib.dumps(info, fmt=plistlib.FMT_BINARY)
    assert bp.startswith(b"bplist00")
    info2 = plistlib.loads(bp)
    assert info2["deviceID"] == info["deviceID"]
    print("5. Nhị phân bplist serialization: OK")

    # 6. Kiểm tra cấu hình Decoder avcC
    dec = Decoder()
    avcc = bytes([1, 0x64, 0, 0x28, 0xFF, 0xE1, 0, 2, 0x67, 0x64, 1, 0, 1, 0x68])
    dec.feed(None, ("avc1", avcc))
    assert dec.ctx is not None
    print("6. Cấu hình PyAV Decoder avcC: OK")

    # 7. Kiểm tra cấu hình AirPlayAudioDecoder (AAC-ELD và ALAC)
    adec = AirPlayAudioDecoder()
    assert adec._init_aac() is not None
    assert adec._init_alac() is not None
    print("7. Khởi tạo PyAV Audio Decoder (AAC-ELD + ALAC): OK")

    # 8. Kiểm tra WaveOutPlayer PCM
    wout = WaveOutPlayer()
    assert wout.opened
    wout.set_volume(0.8)
    wout.write(bytes(4410 * 4))  # 0.1s silence
    time.sleep(0.12)
    wout.close()
    print("8. Windows Multimedia PCM WaveOutPlayer: OK")

    print("=== SELF-TEST TẤT CẢ ĐỀU ĐẠT! ===")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Máy thu AirPlay Screen Mirroring riêng")
    ap.add_argument("--ble", metavar="TRANSPORT", help="điều khiển bằng chuột Bluetooth Bumble, vd. usb:8087:0033")
    ap.add_argument("--height", type=int, default=800, help="chiều cao cửa sổ hiển thị (mặc định: 800)")
    ap.add_argument("--audio", choices=["pc", "iphone", "mute"], default="pc", help="đầu ra âm thanh (pc/iphone/mute, mặc định: pc)")
    ap.add_argument("--self-test", action="store_true", help="chạy kiểm tra tự động các thành phần")
    args = ap.parse_args()

    if args.self_test:
        self_test()
        sys.exit(0)

    local_ips = get_local_ips()
    print("Khởi động máy thu AirPlay:", flush=True)
    print(f" - Địa chỉ IP máy tính: {', '.join(local_ips)}", flush=True)
    if any(ip.startswith("172.20.10.") for ip in local_ips):
        print("   (Phát hiện mạng USB Personal Hotspot: 172.20.10.x)", flush=True)
    print(" - Đang chờ iPhone kết nối AirPlay (Trung tâm điều khiển > Phản chiếu màn hình)...", flush=True)
    print(f" - Tùy chọn đầu ra âm thanh: {args.audio}", flush=True)
    if args.ble:
        print(f" - Điều khiển chuột Bluetooth qua Bumble ({args.ble})", flush=True)

    root = tk.Tk()
    root.title("iPhone (AirPlay)")
    root.resizable(False, False)
    player = AirPlayPlayer(root, args.height, args.ble, audio_mode=args.audio)

    def on_closing():
        player.close()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing)
    try:
        root.mainloop()
    finally:
        player.close()
