"""Bộ tạo biểu tượng ứng dụng iPhone Mirror theo phong cách Apple iOS 27 Liquid Glass.
Khớp chuẩn bản mẫu Figma:
  - Khung squircle siêu hình học iOS chuẩn mực (superellipse n=4.8).
  - Nền gradient chuyển từ xanh #4FACFE sang tím #7B3FD1.
  - Phủ lớp kính bóng nhẹ Liquid Glass (vệt sáng trắng phản quang phía trên, viền sáng mảnh inner rim).
  - Hình thiết bị iPhone màu trắng ở giữa (Dynamic Island, Home bar) kèm sóng AirPlay phát sóng.
  - Xuất ra:
      + icons/app_icon_1024.png (1024x1024 RGBA)
      + icons/app_icon.ico (đa kích thước: 16, 24, 32, 48, 64, 128, 256)

Chạy:  python make_icon.py [--self-test]
"""
import argparse
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
ICONS_DIR = os.path.join(HERE, "icons")
PNG_1024_PATH = os.path.join(ICONS_DIR, "app_icon_1024.png")
ICO_PATH = os.path.join(ICONS_DIR, "app_icon.ico")


def draw_squircle_mask(size, radius, exponent=4.8):
    """Tạo mask Squircle / Superellipse chuẩn Apple với chuyển tiếp anti-aliasing mềm mại."""
    cx, cy = size / 2.0, size / 2.0
    y, x = np.ogrid[:size, :size]
    dx = np.abs(x - cx) / radius
    dy = np.abs(y - cy) / radius
    dist = dx**exponent + dy**exponent
    # Anti-aliased transition boundary
    mask = np.clip((1.0 - dist) * 160.0, 0.0, 1.0)
    return mask, dist


def render_liquid_glass_icon(size_hi=2048, size_out=1024):
    """Vẽ icon độ phân giải cao 2048x2048 rồi downsample mượt về 1024x1024."""
    S = size_hi
    cx, cy = S / 2.0, S / 2.0
    pad = int(S * 0.04)  # Margin 4% để lại chỗ cho shadow
    R = (S - 2 * pad) / 2.0

    # 1. TÍNH TOÁN TOẠ ĐỘ VÀ SQUIRCLE MASK
    mask_sq, dist = draw_squircle_mask(S, R, exponent=4.8)
    y, x = np.ogrid[:S, :S]

    # 2. NỀN GRADIENT XANH #4FACFE -> TÍM #7B3FD1
    # Vector chiếu chéo 135 độ (từ góc trên-trái xuống dưới-phải)
    t = ((x - (cx - R * 0.72)) + (y - (cy - R * 0.72))) / (2.85 * R)
    t = np.clip(t, 0.0, 1.0)
    # Smoothstep curve để màu chuyển tiếp rực rỡ và hài hòa
    t_smooth = t * t * (3.0 - 2.0 * t)

    c_start = np.array([79, 172, 254], dtype=np.float32)   # #4FACFE (Xanh Cyan)
    c_mid   = np.array([102, 114, 238], dtype=np.float32)  # Chàm điện trung gian
    c_end   = np.array([123, 63, 209], dtype=np.float32)   # #7B3FD1 (Tím Neon)

    bg_rgb = np.zeros((S, S, 3), dtype=np.float32)
    m1 = t_smooth < 0.5
    t1 = t_smooth[m1] * 2.0
    bg_rgb[m1] = (1.0 - t1[:, None]) * c_start + t1[:, None] * c_mid
    m2 = ~m1
    t2 = (t_smooth[m2] - 0.5) * 2.0
    bg_rgb[m2] = (1.0 - t2[:, None]) * c_mid + t2[:, None] * c_end

    # 3. QUẦNG SÁNG AMBIENT NHẸ GÓC TRÊN-TRÁI
    d_light = np.sqrt((x - (cx - R * 0.45))**2 + (y - (cy - R * 0.55))**2) / (R * 1.15)
    ambient_glow = np.clip(1.0 - d_light, 0.0, 1.0)**2 * 36.0
    bg_rgb = np.clip(bg_rgb + ambient_glow[:, :, None], 0.0, 255.0)

    # 4. VIỀN SÁNG PHẢN CHIẾU KÍNH (INNER RIM LIGHT)
    # Viền mảnh 1-2% ôm sát mép squircle tạo hiệu ứng vát mép kính cong 3D
    rim_intensity = np.clip(1.0 - np.abs(dist - 0.978) / 0.022, 0.0, 1.0)
    # Phần trên sáng mạnh (bắt sáng từ trên), phần dưới dịu hơn
    rim_alpha = rim_intensity * (0.65 - 0.35 * ((y - (cy - R)) / (2.0 * R)))
    rim_alpha = np.clip(rim_alpha, 0.0, 1.0)
    bg_rgb = bg_rgb * (1.0 - rim_alpha[:, :, None]) + 255.0 * rim_alpha[:, :, None]

    # 5. LỚP KÍNH BÓNG PHÍA TRÊN (TOP SPECULAR GLOSS & SHEEN)
    # Vệt elip cong mờ mềm ở nửa trên icon
    el_dx = (x - cx) / (R * 0.88)
    el_dy = (y - (cy - R * 0.32)) / (R * 0.62)
    el_dist = el_dx**2 + el_dy**2
    gloss_mask = np.clip(1.0 - el_dist, 0.0, 1.0)
    fade_y = np.clip(1.0 - (y - (cy - R * 0.8)) / (R * 1.1), 0.0, 1.0)
    top_gloss = (gloss_mask**1.8) * fade_y * 0.34

    # Dải phản quang chéo siêu mờ (Liquid Glass Sheen)
    sheen_t = np.abs((x - cx) + (y - cy) * 0.55 + R * 0.18) / (R * 0.42)
    diagonal_sheen = np.clip(1.0 - sheen_t, 0.0, 1.0)**2 * 0.14 * np.clip(1.0 - y / (cy + R * 0.25), 0.0, 1.0)

    specular = np.clip(top_gloss + diagonal_sheen, 0.0, 0.48)
    bg_rgb = bg_rgb * (1.0 - specular[:, :, None]) + 255.0 * specular[:, :, None]

    # Tạo ảnh nền Squircle RGBA
    base_rgba = np.dstack([bg_rgb.astype(np.uint8), (mask_sq * 255).astype(np.uint8)])
    base_img = Image.fromarray(base_rgba, mode="RGBA")

    # 6. VẼ BIỂU TƯỢNG IPHONE & SÓNG AIRPLAY
    phone_layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(phone_layer)

    # Thông số hình iPhone
    pw = int(S * 0.37)          # 757 px
    ph = int(S * 0.63)          # 1290 px
    px = int(cx)
    py = int(cy + S * 0.012)    # Hơi lùi xuống để cân đối specular
    pr = int(pw * 0.20)         # 151 px bo góc ngoài
    bw = int(pw * 0.052)        # 39 px độ dày viền thân máy

    x1, y1 = px - pw // 2, py - ph // 2
    x2, y2 = px + pw // 2, py + ph // 2

    # Bóng đổ nhẹ sau lưng iPhone (Ambient Depth)
    shadow_img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow_img)
    sd.rounded_rectangle([x1, y1 + 16, x2, y2 + 16], radius=pr, fill=(15, 8, 32, 90))
    shadow_img = shadow_img.filter(ImageFilter.GaussianBlur(18))

    # Viền thân máy iPhone: Màu trắng tinh sắc nét
    d.rounded_rectangle([x1, y1, x2, y2], radius=pr, fill=(255, 255, 255, 250))

    # Màn hình bên trong (Lùi vào khoảng cách viền bw)
    sx1, sy1 = x1 + bw, y1 + bw
    sx2, sy2 = x2 - bw, y2 - bw
    sr = max(10, pr - bw)

    # Nền màn hình kính OLED mờ (trong suốt vừa phải để gradient lấp lánh xuyên qua)
    d.rounded_rectangle([sx1, sy1, sx2, sy2], radius=sr, fill=(18, 14, 38, 115))

    # Cụm Dynamic Island ở đỉnh trên màn hình
    iw = int(pw * 0.24)         # 181 px
    ih = int(pw * 0.062)        # 46 px
    iy = sy1 + int(ph * 0.024)
    ir = ih // 2
    ix1, ix2 = px - iw // 2, px + iw // 2
    d.rounded_rectangle([ix1, iy, ix2, iy + ih], radius=ir, fill=(12, 10, 18, 255),
                        outline=(255, 255, 255, 210), width=3)
    # Chấm ống kính camera cảm biến ngọc bích
    cam_r = ih // 4
    cam_x = ix2 - int(iw * 0.22)
    cam_y = iy + ih // 2
    d.ellipse([cam_x - cam_r, cam_y - cam_r, cam_x + cam_r, cam_y + cam_r],
              fill=(16, 185, 129, 220))

    # Thanh Home Bar ở đáy màn hình
    hw = int(pw * 0.32)         # 242 px
    hh = int(pw * 0.018)        # 13 px
    hy = sy2 - int(ph * 0.032)
    d.rounded_rectangle([px - hw // 2, hy, px + hw // 2, hy + hh],
                        radius=hh // 2, fill=(255, 255, 255, 240))

    # 7. BIỂU TƯỢNG AIRPLAY & SÓNG TÍN HIỆU Ở TRUNG TÂM MÀN HÌNH
    air_cy = py + int(ph * 0.028)

    # Tam giác AirPlay chính thức: Hướng lên trên
    tw = int(pw * 0.24)         # 181 px đáy
    th = int(pw * 0.19)         # 143 px cao
    tip_y = air_cy - th // 2
    bot_y = air_cy + th // 2
    tri_points = [
        (px, tip_y),
        (px - tw // 2, bot_y),
        (px + tw // 2, bot_y)
    ]
    d.polygon(tri_points, fill=(255, 255, 255, 255))

    # Các cung sóng AirPlay đồng tâm phát ra phía trên đỉnh tam giác
    wave_stroke = int(pw * 0.028)  # 21 px
    wave_center_y = tip_y + 12

    # Cung sóng 1 (Nhỏ)
    r1 = int(pw * 0.17)
    d.arc([px - r1, wave_center_y - r1, px + r1, wave_center_y + r1],
          start=216, end=324, fill=(255, 255, 255, 255), width=wave_stroke)

    # Cung sóng 2 (Lớn)
    r2 = int(pw * 0.26)
    d.arc([px - r2, wave_center_y - r2, px + r2, wave_center_y + r2],
          start=222, end=318, fill=(255, 255, 255, 255), width=wave_stroke)

    # 8. HÒA TRỘN TỔNG THỂ & BÓNG ĐỔ SQUIRCLE
    # Ghép bóng iPhone vào nền
    composed = Image.alpha_composite(base_img, shadow_img)
    # Ghép biểu tượng iPhone vào nền
    composed = Image.alpha_composite(composed, phone_layer)

    # Bóng đổ mềm mại cho toàn bộ Squircle (Soft Contact Shadow)
    shadow_sq = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    # Dùng mask squircle làm bóng đổ
    shadow_alpha = (mask_sq * 75).astype(np.uint8)
    shadow_rgb = np.zeros((S, S, 3), dtype=np.uint8)
    shadow_rgb[:, :] = [24, 16, 48]
    shadow_sq_img = Image.fromarray(np.dstack([shadow_rgb, shadow_alpha]), mode="RGBA")
    # Offset y = +28px và Gaussian blur
    shadow_sq_img = shadow_sq_img.filter(ImageFilter.GaussianBlur(24))
    
    # Tạo canvas kết quả và ghép
    final_canvas = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    # Dán bóng đổ với dịch chuyển nhẹ
    final_canvas.paste(shadow_sq_img, (0, int(S * 0.012)), shadow_sq_img)
    final_canvas.paste(composed, (0, 0), composed)

    # Resize chất lượng cao LANCZOS về kích thước đầu ra 1024x1024
    icon_1024 = final_canvas.resize((size_out, size_out), Image.Resampling.LANCZOS)
    return icon_1024


def export_icons(force=True):
    """Tạo thư mục icons/ và xuất các file PNG 1024x1024 & ICO đa kích thước."""
    os.makedirs(ICONS_DIR, exist_ok=True)
    print("Đang vẽ biểu tượng Liquid Glass iOS 27 (2048x2048 supersampling)...", flush=True)
    icon_1024 = render_liquid_glass_icon(size_hi=2048, size_out=1024)

    # 1. Lưu file PNG 1024x1024
    icon_1024.save(PNG_1024_PATH, format="PNG", optimize=True)
    print(f"✔ Đã tạo {PNG_1024_PATH} (1024x1024 RGBA)", flush=True)

    # 2. Lưu file ICO đa kích thước (16, 24, 32, 48, 64, 128, 256)
    ico_sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    icon_1024.save(ICO_PATH, format="ICO", sizes=ico_sizes)
    print(f"✔ Đã tạo {ICO_PATH} (kích thước: {', '.join(str(s[0]) for s in ico_sizes)})", flush=True)

    return PNG_1024_PATH, ICO_PATH


def self_test():
    """Kiểm tra tính toàn vẹn và chất lượng của bộ tạo icon."""
    print("--- Chạy self-test make_icon.py ---", flush=True)
    png_path, ico_path = export_icons(force=True)

    # 1. Kiểm tra file PNG
    assert os.path.exists(png_path), f"Không tìm thấy {png_path}"
    with Image.open(png_path) as img:
        assert img.size == (1024, 1024), f"Kích thước PNG không đúng: {img.size}"
        assert img.mode == "RGBA", f"Mode PNG không đúng: {img.mode}"
    print("1. File PNG 1024x1024 RGBA: OK")

    # 2. Kiểm tra file ICO
    assert os.path.exists(ico_path), f"Không tìm thấy {ico_path}"
    ico_size = os.path.getsize(ico_path)
    assert ico_size > 5000, f"File ICO quá nhỏ ({ico_size} bytes)"
    with Image.open(ico_path) as img:
        assert img.format == "ICO", f"Định dạng không phải ICO: {img.format}"
    print(f"2. File ICO đa kích thước ({ico_size} bytes): OK")

    print("=== SELF-TEST MAKE_ICON THÀNH CÔNG 100%! ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tạo icon iPhone Mirror phong cách iOS 27 Liquid Glass")
    parser.add_argument("--self-test", action="store_true", help="chạy kiểm tra tự động thành phần")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        sys.exit(0)

    export_icons()
