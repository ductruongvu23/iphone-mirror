# iPhone Mirror

Hiển thị và điều khiển iPhone trên máy tính, giao diện kính lỏng kiểu iOS 27.

| | Windows | macOS |
|---|---|---|
| Hình | Máy thu AirPlay tự viết (`airplay_mirror.py`), qua Wi‑Fi hoặc cáp USB (Điểm truy cập cá nhân) | Qua cáp USB, iPhone hiện như trong QuickTime (`mac/`) |
| Điều khiển | Chuột Bluetooth ảo (Bumble trên card Intel AX211, `bumble_mouse.py`) | Chưa có |
| Giao diện | `control_panel.py` + `liquid_glass.py` | SwiftUI, kính `.glassEffect` gốc trên macOS 26 |

## Windows
```
python control_panel.py
```
Cần Python 3.14, `av`, `Pillow`, `numpy`, `scipy`, `cryptography`, `bumble`. Dịch vụ Bonjour phải đang chạy.
`playfair.dll` build từ `UxPlay/lib/playfair` bằng gcc (xem GEMINI.md).

## macOS
Tải bản build trong tab **Actions** › lần chạy mới nhất › Artifacts › `iPhoneMirror-mac`, giải nén.
App chưa ký Developer ID, nên lần đầu mở: chuột phải › Mở, hoặc chạy `xattr -dr com.apple.quarantine "iPhone Mirror.app"`.
Tự build trên Mac: `bash mac/build.sh`.

Chi tiết kỹ thuật và tiến độ: [GEMINI.md](GEMINI.md).
