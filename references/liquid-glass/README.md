# Nguồn tham khảo kính lỏng (Liquid Glass)

| Thư mục | Repo | Dùng để |
|---|---|---|
| `shuding/` | https://github.com/shuding/liquid-glass (MIT) | Thuật toán khúc xạ: SDF hình chữ nhật bo góc thành bản đồ dịch chuyển. Đã port sang numpy trong `liquid_glass.py`. |
| `rdev-react/` | https://github.com/rdev/liquid-glass-react | Chuỗi bộ lọc SVG: tán sắc R/G/B, blur + saturate 140%, bóng đổ. Đã port trong `liquid_glass.py`. |
| `callstack-native/` | https://github.com/callstack/liquid-glass (MIT) | Bọc hiệu ứng kính gốc của Apple (UIGlassEffect, iOS 26+). Mẫu cho **bản Mac**: dùng kính gốc NSGlassEffectView (macOS 26) thay cho bản vẽ bằng numpy. |

Bản Windows (Tk) dùng `liquid_glass.py` vì Windows không có hiệu ứng kính gốc.
