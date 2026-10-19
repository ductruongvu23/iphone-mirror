"""Giao diện kính lỏng (Liquid Glass) kiểu iOS 27 cho app Tk, vẽ bằng numpy + Pillow.

Khúc xạ theo thuật toán của shuding/liquid-glass (MIT, https://github.com/shuding/liquid-glass):
SDF hình chữ nhật bo góc -> bản đồ dịch chuyển, nền phía sau bị bẻ cong mạnh dần về mép kính.
Phần backdrop-filter của bản web (blur, saturate, contrast, brightness, box-shadow) làm lại bằng Pillow,
thêm viền sáng phía nguồn sáng như hiệu ứng Glass của Figma.
Bản mẫu: Figma uHDGDHJ1xb1KSqPnirkCTT (khung 5:2 "Đang phát", 6:59 "Chờ kết nối").

Nền và các tấm kính là tĩnh: chỉ vẽ lại khi đổi kích thước cửa sổ (`GlassUI.resize`).
`GlassUI.render(state)` vẽ chữ/nút theo trạng thái lên bản sao nền; video do app dán đè vào `GlassUI.screen`.
Chạy thử:  python liquid_glass.py --self-test   (xuất glass_preview_*.png để xem)
"""
import functools
import math
import sys
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from scipy import ndimage

WHITE = (255, 255, 255)
GREEN, BLUE, ORANGE, RED = (52, 199, 89), (10, 132, 255), (255, 159, 10), (255, 69, 58)
FONT_FILES = {"regular": "segoeui.ttf", "semibold": "seguisb.ttf", "bold": "segoeuib.ttf", "icons": "SegoeIcons.ttf"}
# Segoe Fluent Icons
IC_WIFI, IC_USB, IC_MOUSE, IC_INFO, IC_VOL, IC_VOL0, IC_CAST, IC_PHONE = (
    "", "", "", "", "", "", "", "")
SIDE_W, MARGIN, TOP_Y, TOP_H = 380, 24, 20, 64


# ---------- hình học: SDF, mặt nạ khử răng cưa ----------
def sdf_rrect(w, h, r, m=0):
    """Khoảng cách có dấu (px) tới mép hình chữ nhật bo góc w×h, âm ở bên trong; lưới có thêm lề m."""
    y, x = np.mgrid[-m:h + m, -m:w + m].astype(np.float32) + 0.5
    qx = np.abs(x - w / 2) - (w / 2 - r)
    qy = np.abs(y - h / 2) - (h / 2 - r)
    return np.hypot(np.maximum(qx, 0), np.maximum(qy, 0)) + np.minimum(np.maximum(qx, qy), 0) - r


def rr_mask(w, h, r, opacity=1.0):
    return Image.fromarray((np.clip(0.5 - sdf_rrect(w, h, min(r, w / 2, h / 2)), 0, 1) * 255 * opacity).astype(np.uint8), "L")


def _box(box):
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    return x0, y0, max(1, x1 - x0), max(1, y1 - y0)


def fill_rr(img, box, r, color, opacity=1.0):
    x0, y0, w, h = _box(box)
    img.paste(Image.new("RGB", (w, h), color), (x0, y0), rr_mask(w, h, r, opacity))


def stroke_rr(img, box, r, color, opacity=1.0, width=1.0):
    x0, y0, w, h = _box(box)
    d = sdf_rrect(w, h, min(r, w / 2, h / 2))
    a = np.clip(0.5 - d, 0, 1) - np.clip(0.5 - d - width, 0, 1)
    img.paste(Image.new("RGB", (w, h), color), (x0, y0), Image.fromarray((a * 255 * opacity).astype(np.uint8), "L"))


def drop_shadow(img, box, r, blur=24, dy=8, alpha=0.18):
    x0, y0, w, h = _box(box)
    m = blur + abs(dy)
    mask = Image.new("L", (w + 2 * m, h + 2 * m), 0)
    mask.paste(rr_mask(w, h, r), (m, m + dy))
    mask = mask.filter(ImageFilter.GaussianBlur(blur / 2))
    region = (x0 - m, y0 - m, x0 + w + m, y0 + h + m)
    dark = Image.new("RGB", mask.size, (0, 0, 0))
    img.paste(dark, region[:2], mask.point(lambda v: int(v * alpha)))


# ---------- nền và kính ----------
def wallpaper(w, h):
    """Nền than chì tối + vài quầng màu dịu (giống bản Figma sau khi giảm màu)."""
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    t = np.clip((x / w + y / h) / 2, 0, 1)[..., None]
    stops = [(0, (15, 18, 24)), (0.55, (22, 27, 38)), (1, (34, 42, 58))]
    img = np.zeros((h, w, 3), np.float32)
    for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
        k = np.clip((t - p0) / (p1 - p0), 0, 1)
        seg = (t >= p0) & (t <= p1)
        img = np.where(seg, np.array(c0) * (1 - k) + np.array(c1) * k, img)
    sx, sy = w / 1440, h / 900
    for cx, cy, d, color, o in [(220, 160, 760, (58, 90, 150), 0.40), (1260, 740, 640, (61, 58, 99), 0.35), (640, 740, 520, (42, 95, 110), 0.30)]:
        rr = ((x - cx * sx) ** 2 + (y - cy * sy) ** 2) / (d * (sx + sy) / 2 / 2) ** 2
        a = (o * np.exp(-rr * 1.6))[..., None]
        img = img * (1 - a) + np.array(color, np.float32) * a
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB")


EFFECTS = {"regular": (12, 1.0), "clear": (4, 0.45)}  # kiểu kính của callstack/liquid-glass: (độ mờ px, hệ số lớp trắng)


def glass(img, box, r, fill=0.14, effect="regular", depth=14, aberration=2.0, saturation=1.4, tint=None, shadow=True):
    """Tấm kính lỏng.
    - khúc xạ mép: SDF -> bản đồ dịch chuyển (shuding/liquid-glass)
    - tán sắc: kênh R/G/B lệch 1 : 1+0.05ab : 1+0.1ab, nền blur + saturate(140%), bóng 0 12px 40px (rdev/liquid-glass-react)
    - effect "regular"/"clear" và tint=(màu, độ phủ) (callstack/liquid-glass)"""
    if shadow:
        drop_shadow(img, box, r, blur=40, dy=12, alpha=0.25)
    frost, veil = EFFECTS[effect]
    x0, y0, w, h = _box(box)
    m = depth + 2
    src = img.crop((x0 - m, y0 - m, x0 + w + m, y0 + h + m))
    a = np.asarray(src.filter(ImageFilter.GaussianBlur(frost / 2)), np.float32)
    d = sdf_rrect(w, h, r, m)
    gy, gx = np.gradient(d)
    n = np.hypot(gx, gy) + 1e-6
    nx, ny = gx / n, gy / n  # pháp tuyến hướng ra ngoài
    t = np.clip(1 + d / depth, 0, 1)  # 0 ở lòng kính, 1 ở mép
    disp = depth * 0.9 * t * t  # dịch chuyển mạnh dần về mép, như smoothStep của bản gốc
    yy, xx = np.mgrid[0:h + 2 * m, 0:w + 2 * m].astype(np.float32)
    # lấy mẫu lệch vào trong (nền như bị thấu kính kéo vào); mỗi kênh màu lệch một chút -> viền cầu vồng ở mép
    out = np.stack([ndimage.map_coordinates(a[..., c], [yy - ny * disp * s, xx - nx * disp * s], order=1, mode="nearest")
                    for c, s in enumerate((1, 1 + 0.05 * aberration, 1 + 0.1 * aberration))], -1)
    mean = out.mean(axis=2, keepdims=True)
    out = mean + (out - mean) * saturation
    out = (out - 128) * 1.05 + 132  # contrast + brightness nhẹ
    if tint:
        color, o = tint
        out = out * (1 - o) + np.array(color, np.float32) * o
    fill *= veil
    out = out * (1 - fill) + 255 * fill
    facing = np.clip(-(nx + ny) * 0.7071, 0, 1)  # mép quay về nguồn sáng trên-trái
    back = np.clip((nx + ny) * 0.7071, 0, 1)
    rim = np.clip(1 - np.abs(d + 0.75), 0, 1) * (0.22 + 0.55 * facing + 0.18 * back)
    glow = np.where(d < 0, np.exp(np.minimum(d, 0) / 6) * 0.06, 0)
    out = out + (255 - out) * (rim + glow)[..., None]
    cov = np.clip(0.5 - d, 0, 1)[..., None]
    base = np.asarray(src, np.float32)
    res = base * (1 - cov) + out * cov
    img.paste(Image.fromarray(np.clip(res, 0, 255).astype(np.uint8), "RGB"), (x0 - m, y0 - m))


# ---------- chữ và biểu tượng ----------
@functools.lru_cache(maxsize=64)
def font(style, size):
    return ImageFont.truetype(FONT_FILES[style], size)


def text_w(s, size, style="regular"):
    return font(style, size).getlength(s)


def text(img, xy, s, size, style="regular", color=WHITE, o=1.0, anchor="la"):
    """Vẽ chữ có độ mờ lên ảnh RGB; trả về độ rộng."""
    f = font(style, size)
    l, t, r, b = f.getbbox(s, anchor=anchor)
    if r <= l or b <= t:
        return 0
    mask = Image.new("L", (r - l, b - t), 0)
    ImageDraw.Draw(mask).text((-l, -t), s, font=f, fill=int(255 * o), anchor=anchor)
    img.paste(Image.new("RGB", mask.size, color), (int(xy[0]) + l, int(xy[1]) + t), mask)
    return r - l


def wrap(s, width, size, style="regular"):
    lines, cur = [], ""
    for word in s.split():
        if cur and text_w(cur + " " + word, size, style) > width:
            lines.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    return lines + ([cur] if cur else [])


def bubble(img, cx, cy, d, glyph, color, o=0.28, size=None):
    fill_rr(img, (cx - d / 2, cy - d / 2, cx + d / 2, cy + d / 2), d / 2, color, o)
    text(img, (cx, cy), glyph, size or round(d * 0.45), "icons", anchor="mm")


# ---------- toàn bộ giao diện ----------
class GlassUI:
    def __init__(self, w=1440, h=900, app_icon=None, sidebar=True):
        self.app_icon = app_icon.convert("RGBA").resize((40, 40), Image.LANCZOS) if app_icon else None
        self.sidebar = sidebar
        self.hits, self.volume_track = [], (0, 1)  # có sẵn trước lần vẽ đầu (vẽ ở luồng nền)
        self.resize(w, h)

    def resize(self, w, h, sidebar=None):
        if sidebar is not None:
            self.sidebar = sidebar
        self.size = (w, h)
        self._layout()
        base = wallpaper(w, h)
        glass(base, self.top, 32)
        if self.side:
            glass(base, self.side, 32, fill=0.12)
            for g in (self.g1, self.g2, self.g3, self.g4):
                fill_rr(base, g, 22, WHITE, 0.08)
                stroke_rr(base, g, 22, WHITE, 0.12)
            for tile in self.tiles:
                fill_rr(base, tile, 16, WHITE, 0.10)
        self.base = base
        # bản có iPhone (đang phát) và bản có thẻ hướng dẫn (chờ kết nối)
        self.base_phone = base.copy()
        p, k = self.phone, self.k
        drop_shadow(self.base_phone, p, 58 * k, blur=60, dy=24, alpha=0.45)
        fill_rr(self.base_phone, p, 58 * k, (11, 11, 15))
        stroke_rr(self.base_phone, p, 58 * k, (199, 199, 204), 0.8, 2.5 * k)
        fill_rr(self.base_phone, self.screen, self.screen_r, (0, 0, 0))
        for box, key, val in self.chips:
            glass(self.base_phone, box, 16, fill=0.14, effect="clear", depth=8)
            x = text(self.base_phone, (box[0] + 14, box[1] + 8), key, 12, "semibold")
            text(self.base_phone, (box[0] + 20 + x, box[1] + 8), val, 12, o=0.75)
        self.base_wait = base.copy()
        glass(self.base_wait, self.card, 36, fill=0.13)
        self.screen_mask = rr_mask(self.screen[2] - self.screen[0], self.screen[3] - self.screen[1], self.screen_r)

    def _layout(self):
        W, H = self.size
        M = MARGIN
        self.top = (M, TOP_Y, W - M, TOP_Y + TOP_H)
        self.side = (W - M - SIDE_W, 108, W - M, H - M) if self.sidebar else None
        self.stage = (M, 108, (self.side[0] - M) if self.side else W - M, H - M)
        sx0, sy0, sx1, sy1 = self.stage
        ph = min(sy1 - sy0 - 56, 1000)
        pw = round(ph * 335 / 712)
        self.k = k = ph / 712
        px, py = round((sx0 + sx1 - pw) / 2), sy0 + max(0, (sy1 - sy0 - 56 - ph) // 2)
        self.phone = (px, py, px + pw, py + ph)
        inset = max(4, round(6 * k))
        self.screen = (px + inset, py + inset, px + pw - inset, py + ph - inset)
        self.screen_r = round(52 * k)
        chips = [("Chuột trái", "Chạm & kéo"), ("Lăn chuột", "Cuộn"), ("Chuột phải", "Nút phụ")]
        widths = [text_w(a, 12, "semibold") + text_w(b, 12) + 34 for a, b in chips]
        x = (sx0 + sx1 - sum(widths) - 10 * (len(chips) - 1)) / 2
        self.chips = []
        for (a, b), cw in zip(chips, widths):
            self.chips.append(((x, py + ph + 18, x + cw, py + ph + 50), a, b))
            x += cw + 10
        cw = min(600, sx1 - sx0 - 48)
        self.card = (round((sx0 + sx1 - cw) / 2), round(sy0 + (sy1 - sy0 - 600) / 2), round((sx0 + sx1 + cw) / 2), round(sy0 + (sy1 - sy0 - 600) / 2) + 520)
        if self.side:
            gx0, gx1 = self.side[0] + 20, self.side[2] - 20
            y = self.side[1] + 20
            self.label1, y = y, y + 24
            self.g1, y = (gx0, y, gx1, y + 152), y + 168
            self.label2, y = y, y + 24
            self.g2, y = (gx0, y, gx1, y + 150), y + 166
            self.label3, y = y, y + 24
            self.g3, y = (gx0, y, gx1, y + 100), y + 116
            self.label4, y = y, y + 24
            self.g4 = (gx0, y, gx1, y + 82)
            self.tip = (gx0, self.side[3] - 20 - 64, gx1, self.side[3] - 20)
            tw = (gx1 - gx0 - 28 - 16) / 3
            ty = self.g1[1] + 78
            self.tiles = [(gx0 + 14 + i * (tw + 8), ty, gx0 + 14 + i * (tw + 8) + tw, ty + 60) for i in range(3)]

    def render(self, st):
        """Vẽ giao diện theo trạng thái `st` (dict). Trả về ảnh RGB; vùng nút bấm lưu trong self.hits."""
        playing = st.get("playing", False)
        img = (self.base_phone if playing else self.base_wait).copy()
        self.hits = []
        self._top(img, st)
        if self.side:
            self._sidebar(img, st)
        if not playing:
            self._card(img, st)
        return img

    def hit(self, x, y):
        for name, (x0, y0, x1, y1) in reversed(self.hits):
            if x0 <= x <= x1 and y0 <= y <= y1:
                return name
        return None

    def _pill_button(self, img, x1, label, name, color=None):
        w = text_w(label, 13, "semibold") + 36
        box = (x1 - w, TOP_Y + 12, x1, TOP_Y + 52)
        fill_rr(img, box, 20, color or WHITE, 0.75 if color else 0.18)
        stroke_rr(img, box, 20, WHITE, 0.35)
        text(img, ((box[0] + box[2]) / 2, TOP_Y + 32), label, 13, "semibold", anchor="mm")
        self.hits.append((name, box))
        return box[0]

    def _top(self, img, st):
        x0, y0, x1, y1 = self.top
        if self.app_icon:
            img.paste(self.app_icon, (x0 + 14, y0 + 12), self.app_icon)
        else:
            fill_rr(img, (x0 + 14, y0 + 12, x0 + 54, y0 + 52), 12, (79, 172, 254))
            text(img, (x0 + 34, y0 + 32), IC_PHONE, 20, "icons", anchor="mm")
        text(img, (x0 + 66, y0 + 12), "iPhone Mirror", 16, "semibold")
        text(img, (x0 + 66, y0 + 35), "AirPlay · Chuột Bluetooth", 12, o=0.7)
        label, color = st.get("status", ("Đang chờ iPhone", ORANGE))
        w = text_w(label, 13, "semibold") + 46
        cx = self.size[0] / 2
        box = (cx - w / 2, y0 + 14, cx + w / 2, y0 + 50)
        fill_rr(img, box, 18, WHITE, 0.12)
        stroke_rr(img, box, 18, WHITE, 0.25)
        fill_rr(img, (box[0] + 13, y0 + 28, box[0] + 21, y0 + 36), 4, color)
        text(img, (box[0] + 30, y0 + 32), label, 13, "semibold", anchor="lm")
        x = self._pill_button(img, x1 - 12, "Ngắt kết nối", "disconnect", RED) if playing_or(st) else x1 - 4
        self._pill_button(img, x - 8, "Ẩn bảng điều khiển" if self.sidebar else "Hiện bảng điều khiển", "toggle_sidebar")

    def _sidebar(self, img, st):
        gx0, gx1 = self.g1[0], self.g1[2]
        for y, s in [(self.label1, "KẾT NỐI"), (self.label2, "CHUỘT BLUETOOTH"), (self.label3, "ÂM THANH"), (self.label4, "ĐỘ PHÂN GIẢI")]:
            text(img, (gx0, y), s, 11, "semibold", o=0.6)
        # Kết nối
        x, y = gx0 + 14, self.g1[1] + 14
        usb = st.get("via_usb", False)
        bubble(img, x + 20, y + 20, 40, IC_USB if usb else IC_WIFI, GREEN if st.get("playing") else BLUE)
        text(img, (x + 52, y - 1), st.get("link_title", "Chưa có iPhone kết nối"), 15, "semibold")
        for i, line in enumerate(st.get("link_lines", [])[:2]):
            text(img, (x + 52, y + 22 + 18 * i), line, 12, o=0.7)
        for tile, (val, lab) in zip(self.tiles, st.get("stats", [("–", "khung/giây"), ("–", "độ phân giải"), ("–", "mã hoá")])):
            size = 20 if text_w(val, 20, "semibold") <= tile[2] - tile[0] - 20 else 14
            text(img, (tile[0] + 12, tile[1] + 10), val, size, "semibold")
            text(img, (tile[0] + 12, tile[1] + 38), lab, 11, o=0.7)
        # Chuột Bluetooth
        y = self.g2[1] + 14
        bubble(img, x + 20, y + 20, 40, IC_MOUSE, BLUE)
        text(img, (x + 52, y + 1), "Chuột ảo cho iPhone", 14, "semibold")
        text(img, (x + 52, y + 22), st.get("ble_sub", "Windows tạm tắt Bluetooth khi bật"), 11, o=0.65)
        on = st.get("ble_on", False)
        tb = (gx1 - 14 - 51, y + 5, gx1 - 14, y + 36)
        fill_rr(img, tb, 15.5, GREEN if on else WHITE, 1 if on else 0.18)
        kx = tb[2] - 29 if on else tb[0] + 2
        drop_shadow(img, (kx, tb[1] + 2, kx + 27, tb[1] + 29), 13.5, blur=6, dy=2, alpha=0.2)
        fill_rr(img, (kx, tb[1] + 2, kx + 27, tb[1] + 29), 13.5, WHITE)
        self.hits.append(("ble_toggle", tb))
        for dy in (52, 96):
            fill_rr(img, (x, y + dy, gx1 - 14, y + dy + 1), 0, WHITE, 0.12)
        text(img, (x, y + 64), st.get("mouse_name", "Mirror Mouse"), 14)
        ok_text, ok = st.get("paired", ("Chưa ghép đôi", False))
        text(img, (gx1 - 14, y + 64), ok_text, 13, "semibold", color=(48, 209, 88) if ok else ORANGE, anchor="ra")
        text(img, (x, y + 109), IC_INFO, 13, "icons", o=0.65)
        text(img, (x + 20, y + 107), st.get("ble_hint", "Bật AssistiveTouch trên iPhone để thấy con trỏ chuột."), 11, o=0.65)
        # Âm thanh
        y = self.g3[1] + 14
        seg = (x, y, gx1 - 14, y + 38)
        fill_rr(img, seg, 12, WHITE, 0.1)
        iw = (seg[2] - seg[0] - 6) / 3
        for i, (key, label) in enumerate([("pc", "Loa PC"), ("iphone", "Loa iPhone"), ("mute", "Tắt tiếng")]):
            ib = (seg[0] + 3 + i * iw, y + 3, seg[0] + 3 + (i + 1) * iw, y + 35)
            sel = st.get("audio", "pc") == key
            if sel:
                fill_rr(img, ib, 9, WHITE, 0.28)
            text(img, ((ib[0] + ib[2]) / 2, y + 19), label, 12, "semibold" if sel else "regular", o=1 if sel else 0.75, anchor="mm")
            self.hits.append(("audio_" + key, ib))
        y += 52
        vol = max(0, min(100, st.get("volume", 70)))
        text(img, (x, y + 10), IC_VOL0, 13, "icons", anchor="lm")
        t0, t1 = x + 26, gx1 - 14 - 72
        fill_rr(img, (t0, y + 8, t1, y + 12), 2, WHITE, 0.2)
        kx = t0 + (t1 - t0) * vol / 100
        fill_rr(img, (t0, y + 8, kx, y + 12), 2, WHITE)
        drop_shadow(img, (kx - 10, y, kx + 10, y + 20), 10, blur=6, dy=2, alpha=0.25)
        fill_rr(img, (kx - 10, y, kx + 10, y + 20), 10, WHITE)
        text(img, (t1 + 12, y + 10), IC_VOL, 15, "icons", anchor="lm")
        text(img, (gx1 - 14, y + 10), f"{vol}%", 12, o=0.8, anchor="rm")
        self.volume_track = (t0, t1)
        self.hits.append(("volume", (t0 - 10, y - 4, t1 + 10, y + 24)))
        # Độ phân giải: mức chất lượng + đời iPhone (độ phân giải gốc)
        y = self.g4[1] + 12
        seg = (x, y, gx1 - 14, y + 34)
        fill_rr(img, seg, 12, WHITE, 0.1)
        iw = (seg[2] - seg[0] - 6) / 3
        for i, (key, label) in enumerate([("720", "720p"), ("1080", "1080p"), ("max", "Gốc")]):
            ib = (seg[0] + 3 + i * iw, y + 3, seg[0] + 3 + (i + 1) * iw, y + 31)
            sel = st.get("res", "max") == key
            if sel:
                fill_rr(img, ib, 9, WHITE, 0.28)
            text(img, ((ib[0] + ib[2]) / 2, y + 17), label, 12, "semibold" if sel else "regular", o=1 if sel else 0.75, anchor="mm")
            self.hits.append(("res_" + key, ib))
        y += 44
        text(img, (x, y), st.get("model_line", "iPhone 11 Pro Max · 1242×2688"), 12, o=0.85)
        text(img, (gx1 - 14, y), "Đổi máy ›", 12, "semibold", color=BLUE, anchor="ra")
        self.hits.append(("model_menu", (x, y - 4, gx1 - 14, y + 20)))
        # Mẹo / thông báo
        msg = st.get("message") or "Mẹo: bật Điểm truy cập cá nhân trên iPhone rồi cắm cáp — AirPlay sẽ chạy qua dây, mượt và ổn định hơn Wi‑Fi."
        if self.tip[1] < self.g4[3] + 8:  # cửa sổ thấp: bỏ ô mẹo cho khỏi đè
            return
        fill_rr(img, self.tip, 22, GREEN, 0.14)
        stroke_rr(img, self.tip, 22, WHITE, 0.12)
        for i, line in enumerate(wrap(msg, self.tip[2] - self.tip[0] - 28, 11)[:3]):
            text(img, (self.tip[0] + 14, self.tip[1] + 12 + 15 * i), line, 11, o=0.85)

    def _card(self, img, st):
        x0, y0, x1, y1 = self.card
        cx = (x0 + x1) / 2
        fill_rr(img, (cx - 38, y0 + 34, cx + 38, y0 + 110), 38, WHITE, 0.18)
        stroke_rr(img, (cx - 38, y0 + 34, cx + 38, y0 + 110), 38, WHITE, 0.4)
        text(img, (cx, y0 + 72), IC_CAST, 32, "icons", anchor="mm")
        text(img, (cx, y0 + 124), "Sẵn sàng phản chiếu", 30, "semibold", anchor="ma")
        text(img, (cx, y0 + 172), st.get("receiver_line", "Máy thu AirPlay “iPhone Mirror” đang chờ"), 14, o=0.75, anchor="ma")
        sb = (x0 + 28, y0 + 210, x1 - 28, y0 + 350)
        fill_rr(img, sb, 22, WHITE, 0.08)
        stroke_rr(img, sb, 22, WHITE, 0.12)
        for i, s in enumerate(["Mở Trung tâm điều khiển trên iPhone", "Chạm “Phản chiếu màn hình”", f"Chọn “{st.get('receiver_name', 'iPhone Mirror')}” trong danh sách"]):
            yy = sb[1] + 18 + i * 40
            fill_rr(img, (sb[0] + 18, yy, sb[0] + 46, yy + 28), 14, WHITE, 0.2)
            text(img, (sb[0] + 32, yy + 14), str(i + 1), 13, "semibold", anchor="mm")
            text(img, (sb[0] + 58, yy + 14), s, 14, anchor="lm")
        tw = (x1 - x0 - 56 - 12) / 2
        for i, (glyph, title, body, color, rec) in enumerate([
                (IC_WIFI, "Qua Wi‑Fi", "iPhone và máy tính dùng chung một mạng.", BLUE, False),
                (IC_USB, "Qua cáp USB", "Bật Điểm truy cập cá nhân rồi cắm cáp. Mượt và ổn định hơn.", GREEN, True)]):
            tb = (x0 + 28 + i * (tw + 12), y0 + 372, x0 + 28 + i * (tw + 12) + tw, y0 + 488)
            fill_rr(img, tb, 22, color, 0.18 if rec else 0.1)
            stroke_rr(img, tb, 22, color if rec else WHITE, 0.6 if rec else 0.15)
            bubble(img, tb[0] + 34, tb[1] + 34, 36, glyph, color, 0.35, 17)
            tx = text(img, (tb[0] + 62, tb[1] + 34), title, 15, "semibold", anchor="lm")
            if rec:
                bx = tb[0] + 70 + tx
                fill_rr(img, (bx, tb[1] + 24, bx + text_w("Khuyên dùng", 10, "semibold") + 16, tb[1] + 44), 10, color, 0.9)
                text(img, (bx + 8, tb[1] + 34), "Khuyên dùng", 10, "semibold", anchor="lm")
            for j, line in enumerate(wrap(body, tw - 32, 12)[:2]):
                text(img, (tb[0] + 16, tb[1] + 64 + 18 * j), line, 12, o=0.75)
        ble, ok = st.get("ble_pill", ("Chuột Bluetooth: chưa bật", False))
        w = text_w(ble, 13, "semibold") + 46
        pb = (cx - w / 2, y1 + 20, cx + w / 2, y1 + 56)
        fill_rr(img, pb, 18, WHITE, 0.12)
        stroke_rr(img, pb, 18, WHITE, 0.25)
        fill_rr(img, (pb[0] + 13, pb[1] + 14, pb[0] + 21, pb[1] + 22), 4, GREEN if ok else ORANGE)
        text(img, (pb[0] + 30, pb[1] + 18), ble, 13, "semibold", anchor="lm")

    def paste_video(self, img, frame_img):
        """Dán khung hình (PIL, đúng kích thước màn hình) vào vùng màn hình iPhone, bo góc."""
        img.paste(frame_img, self.screen[:2], self.screen_mask)


def playing_or(st):
    return st.get("playing", False)


DEMO = {
    "playing": True, "status": ("Đang phát qua cáp USB", GREEN), "via_usb": True,
    "link_title": "Đang phát qua cáp USB", "link_lines": ["AirPlay · 172.20.10.2", "iPhone 11 Pro Max · iOS 27"],
    "stats": [("58", "khung/giây"), ("1242×2688", "độ phân giải"), ("H.264", "mã hoá")],
    "ble_on": True, "paired": ("Đã ghép đôi", True), "audio": "pc", "volume": 70,
}


def self_test():
    d = sdf_rrect(100, 60, 20)
    assert d[30, 50] < 0 and d[0, 0] > 0 and abs(d[30, 0]) <= 1
    m = np.asarray(rr_mask(100, 60, 20))
    assert m[30, 50] == 255 and m[0, 0] == 0
    assert wrap("một hai ba bốn năm", 40, 12) and text_w("Kính", 14) > 0
    t = time.perf_counter()
    ui = GlassUI(1440, 900)
    t_base = time.perf_counter() - t
    t = time.perf_counter()
    play = ui.render(DEMO)
    t_render = time.perf_counter() - t
    assert ui.hit(ui.side[2] - 50, ui.g2[1] + 30) == "ble_toggle"
    assert ui.hit(ui.g3[0] + 30, ui.g3[1] + 30) == "audio_pc"
    ui.paste_video(play, Image.new("RGB", (ui.screen[2] - ui.screen[0], ui.screen[3] - ui.screen[1]), (40, 60, 120)))
    play.save("glass_preview_playing.png")
    ui.render({"receiver_line": "Máy thu AirPlay “iPhone Mirror” đang chờ trên mạng 192.168.0.101",
               "ble_pill": ("Chuột Bluetooth: Mirror Mouse đã ghép đôi · sẵn sàng", True)}).save("glass_preview_waiting.png")
    print(f"self-test OK (nền+kính {t_base * 1000:.0f} ms, vẽ trạng thái {t_render * 1000:.0f} ms)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if "--self-test" in sys.argv:
        self_test()
