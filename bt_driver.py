"""Bật/tắt chế độ chuột iPhone cho card Bluetooth Intel AX211 (USB 8087:0033) bằng cách đổi driver:
  winusb : Bumble (chuột iPhone) dùng card; Windows mất Bluetooth (tai nghe, loa...)
  intel  : trả card cho Windows, Bluetooth của máy dùng bình thường
Cả hai gói driver đã nằm sẵn trong kho driver của Windows (WinUSB do Zadig tạo, Intel gốc), nên chỉ cần ép card dùng gói nào.
Chạy:  python bt_driver.py status | winusb | intel     (winusb/intel cần quyền Admin: tự bật hộp thoại UAC)
"""
import ctypes
import os
import re
import subprocess
import sys

HWID = r"USB\VID_8087&PID_0033"
# (Provider Name, Class Name, chữ có trong Original Name) của gói driver cho từng chế độ, theo `pnputil /enum-drivers`.
# Zadig (libwdi) có thể đã tạo gói cho thiết bị khác nên lọc thêm theo tên; gói Intel gốc tên là ibtusb.inf.
PACKAGES = {"winusb": ("libwdi", "USBDevice", "bluetooth"), "intel": ("Intel Corporation", "Bluetooth", "")}
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW: không bật cửa sổ console khi gọi từ giao diện


def pnputil(*args):
    return subprocess.run(["pnputil", *args], capture_output=True, text=True, creationflags=NO_WINDOW).stdout


def parse_blocks(text):
    """Tách output 'Khoá: giá trị' của pnputil thành danh sách dict (mỗi khối cách nhau bởi dòng trống)."""
    blocks, cur = [], {}
    for line in text.splitlines():
        m = re.match(r"\s*([^:]+?):\s+(.*)", line)
        if m:
            cur[m.group(1)] = m.group(2).strip()
        elif cur:
            blocks.append(cur)
            cur = {}
    return blocks + ([cur] if cur else [])


def find_inf(mode, drivers_text=None):
    provider, cls, name = PACKAGES[mode]
    for b in parse_blocks(drivers_text if drivers_text is not None else pnputil("/enum-drivers")):
        if b.get("Provider Name") == provider and b.get("Class Name") == cls and name in b.get("Original Name", "").lower():
            return b["Published Name"]
    return None


def status(devices_text=None):
    """'winusb', 'intel', 'missing' (không thấy card) hoặc tên gói lạ."""
    blocks = parse_blocks(devices_text if devices_text is not None else pnputil("/enum-devices", "/deviceid", HWID))
    inf = blocks[0].get("Driver Name") if blocks else None
    if not inf:
        return "missing"
    for mode in PACKAGES:
        if inf == find_inf(mode):
            return mode
    return inf


def is_admin():
    return bool(ctypes.windll.shell32.IsUserAnAdmin())


def switch(mode):
    """Ép card dùng gói driver của `mode`. Không có quyền Admin thì mở lại chính script này qua UAC rồi trả về ngay."""
    if not is_admin():
        args = f'"{os.path.abspath(__file__)}" {mode}'
        ok = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, args, None, 0) > 32
        return "đang chờ UAC" if ok else "bị huỷ"
    inf = find_inf(mode)
    if not inf:
        raise SystemExit(f"Không thấy gói driver cho chế độ {mode} trong kho driver")
    reboot = ctypes.c_int(0)
    INSTALLFLAG_FORCE = 1
    ok = ctypes.windll.newdev.UpdateDriverForPlugAndPlayDevicesW(
        None, HWID, os.path.join(os.environ["WINDIR"], "INF", inf), INSTALLFLAG_FORCE, ctypes.byref(reboot))
    if not ok:
        raise SystemExit(f"Đổi driver thất bại, mã lỗi {ctypes.GetLastError()}")
    return f"đã chuyển sang {mode} ({inf})" + (", cần khởi động lại máy" if reboot.value else "")


def self_test():
    drivers = """
Published Name:     oem45.inf
Original Name:      intel(r)_wireless_bluetooth(r).inf
Provider Name:      libwdi
Class Name:         USBDevice

Published Name:     oem2.inf
Original Name:      ipf_acpi.inf
Provider Name:      Intel

Published Name:     oem8.inf
Original Name:      ibtusb.inf
Provider Name:      Intel Corporation
Class Name:         Bluetooth
"""
    assert find_inf("winusb", drivers) == "oem45.inf" and find_inf("intel", drivers) == "oem8.inf"
    assert parse_blocks("Instance ID:   USB\\X\nDriver Name:   oem45.inf\n")[0]["Driver Name"] == "oem45.inf"
    assert status("") == "missing"
    print("self-test OK")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "--self-test":
        self_test()
    elif cmd == "status":
        print(status())
    elif cmd in PACKAGES:
        print(switch(cmd))
    else:
        raise SystemExit(__doc__)
