"""Chuột Bluetooth LE (HID over GATT) cho iPhone, chạy bằng Bumble trên card Bluetooth Intel đã đổi sang WinUSB.

Chuột vị trí TUYỆT ĐỐI: gửi toạ độ theo tỉ lệ màn hình (0..1), iPhone đặt con trỏ AssistiveTouch đúng chỗ (đã thử).
Bumble tự lo ghép đôi nên không vướng dịch vụ ghép đôi của Windows (sự kiện 3501, 0x8007048F, khi dùng WinRT),
và lưu khoá ghép đôi ra bumble_mouse_keys.json để iPhone tự kết nối lại.
Cần: card đã đổi driver sang WinUSB bằng Zadig (Windows mất Bluetooth trong lúc này), firmware Intel đã tải
bằng `bumble-intel-fw-download`. iPhone: bật AssistiveTouch, vào Cài đặt > Bluetooth, chọn "Mirror Mouse".
Chạy thử:  python bumble_mouse.py [usb:0]
"""
import asyncio
import os
import struct
import sys

from bumble import data_types
from bumble.core import AdvertisingData
from bumble.device import Device
from bumble.drivers import intel
from bumble.gatt import (
    GATT_BATTERY_LEVEL_CHARACTERISTIC, GATT_BATTERY_SERVICE, GATT_DEVICE_INFORMATION_SERVICE,
    GATT_HID_CONTROL_POINT_CHARACTERISTIC, GATT_HID_INFORMATION_CHARACTERISTIC, GATT_HUMAN_INTERFACE_DEVICE_SERVICE,
    GATT_MANUFACTURER_NAME_STRING_CHARACTERISTIC, GATT_PROTOCOL_MODE_CHARACTERISTIC, GATT_REPORT_CHARACTERISTIC,
    GATT_REPORT_MAP_CHARACTERISTIC, GATT_REPORT_REFERENCE_DESCRIPTOR, Characteristic, Descriptor, Service)
from bumble.hci import HCI_ACL_DATA_PACKET, Address
from bumble.keys import JsonKeyStore
from bumble.pairing import PairingConfig, PairingDelegate
from bumble.transport import open_transport
from bumble.transport.usb import EventPacketSplitter

sys.stdout.reconfigure(encoding="utf-8")  # console Windows mặc định cp1252 không in được tiếng Việt

# shortcut: Bumble 0.0.236 chỉ nạp firmware cho biến thể Intel 0x17/0x19/0x1C; AX211 của máy này là 0x18 ("Solar"),
# Linux btintel nạp nó cùng quy trình TLV như 0x19 nên báo nó là 0x19. Gỡ khi Bumble thêm 0x18 vào danh sách.
_read_device_info = intel.Driver.read_device_info


async def _read_device_info_ax211(self):
    info = await _read_device_info(self)
    hw = info.get(intel.ValueType.HARDWARE_INFO)
    if hw and hw.variant == 0x18:
        info[intel.ValueType.HARDWARE_INFO] = intel.HardwareInfo(hw.platform, intel.HardwareVariant.GARFIELD_PEAK)
    return info


intel.Driver.read_device_info = _read_device_info_ax211

NAME = "Mirror Mouse"  # gói quảng bá tối đa 31 byte: tên dài hơn sẽ bị controller từ chối (0x12)
# Địa chỉ random static cố định để bản ghép đôi trên iPhone dùng lại được. Đổi REPORT_MAP thì phải đổi địa chỉ này
# (và xoá file khoá) vì iPhone nhớ Report Map cũ của thiết bị đã ghép đôi.
ADDRESS = Address("F2:C3:A1:5E:42:33")
KEYS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bumble_mouse_keys.json")

# Chuột: 3 nút, X/Y tuyệt đối 16 bit (0..32767), cuộn tương đối 1 byte có dấu. Report ID 1.
REPORT_MAP = bytes([
    0x05, 0x01, 0x09, 0x02, 0xA1, 0x01, 0x85, 0x01, 0x09, 0x01, 0xA1, 0x00,
    0x05, 0x09, 0x19, 0x01, 0x29, 0x03, 0x15, 0x00, 0x25, 0x01, 0x95, 0x03, 0x75, 0x01, 0x81, 0x02,
    0x95, 0x01, 0x75, 0x05, 0x81, 0x03,
    0x05, 0x01, 0x09, 0x30, 0x09, 0x31, 0x16, 0x00, 0x00, 0x26, 0xFF, 0x7F, 0x75, 0x10, 0x95, 0x02, 0x81, 0x02,
    0x09, 0x38, 0x15, 0x81, 0x25, 0x7F, 0x75, 0x08, 0x95, 0x01, 0x81, 0x06,
    0xC0, 0xC0])


def mouse_report(buttons, fx, fy, wheel=0):
    """fx, fy: vị trí theo tỉ lệ chiều ngang/dọc màn hình iPhone (0..1)."""
    pos = lambda f: round(min(max(f, 0.0), 1.0) * 32767)
    return struct.pack("<BHHb", buttons & 0x07, pos(fx), pos(fy), max(-127, min(127, int(wheel))))


class AutoAccept(PairingDelegate):
    """Máy tính tự đồng ý ghép đôi; in mã để người dùng đối chiếu với mã trên iPhone trước khi bấm Pair."""

    def __init__(self):
        super().__init__(io_capability=PairingDelegate.IoCapability.DISPLAY_OUTPUT_AND_YES_NO_INPUT)

    async def compare_numbers(self, number, digits):
        print(f"Mã ghép đôi: {number:0{digits}d}  (phải trùng mã trên iPhone)", flush=True)
        return True

    async def confirm(self, auto=False):
        return True


class BumbleMouse:
    async def start(self, transport="usb:0"):
        self.transport = await open_transport(transport) if isinstance(transport, str) else transport
        d = self.device = Device.with_hci(NAME, ADDRESS, self.transport.source, self.transport.sink)
        d.keystore = JsonKeyStore.from_device(d, KEYS)
        # Khai địa chỉ định danh là địa chỉ random đang phát sóng: mặc định Bumble khai địa chỉ PUBLIC của card (trùng
        # máy tính Windows đã ghép đôi với iPhone) và iPhone ngắt kết nối ngay sau bước trao địa chỉ (reason 19).
        d.pairing_config_factory = lambda conn: PairingConfig(
            sc=True, mitm=True, bonding=True, delegate=AutoAccept(), identity_address_type=PairingConfig.AddressType.RANDOM)
        enc = Characteristic.READ_REQUIRES_ENCRYPTION  # iPhone bỏ qua chuột chưa ghép đôi; đọc Report Map sẽ kích ghép đôi
        self.report = Characteristic(
            GATT_REPORT_CHARACTERISTIC, Characteristic.Properties.READ | Characteristic.Properties.NOTIFY, enc, bytes(6),
            [Descriptor(GATT_REPORT_REFERENCE_DESCRIPTOR, Descriptor.READABLE, bytes([0x01, 0x01]))])  # ID 1, Input
        self.subscribers = set()
        self.report.on("subscription", lambda conn, notify, indicate: (
            self.subscribers.add(conn) if notify else self.subscribers.discard(conn)))
        d.add_services([
            Service(GATT_HUMAN_INTERFACE_DEVICE_SERVICE, [
                Characteristic(GATT_PROTOCOL_MODE_CHARACTERISTIC, Characteristic.Properties.READ, enc, bytes([0x01])),
                Characteristic(GATT_HID_INFORMATION_CHARACTERISTIC, Characteristic.Properties.READ, enc,
                               bytes([0x11, 0x01, 0x00, 0x02])),  # HID 1.11, NormallyConnectable
                Characteristic(GATT_HID_CONTROL_POINT_CHARACTERISTIC, Characteristic.WRITE_WITHOUT_RESPONSE,
                               Characteristic.WRITEABLE, bytes([0x00])),
                Characteristic(GATT_REPORT_MAP_CHARACTERISTIC, Characteristic.Properties.READ, enc, REPORT_MAP),
                self.report]),
            Service(GATT_DEVICE_INFORMATION_SERVICE, [
                Characteristic(GATT_MANUFACTURER_NAME_STRING_CHARACTERISTIC, Characteristic.Properties.READ,
                               Characteristic.READABLE, b"iphone_mirror")]),
            Service(GATT_BATTERY_SERVICE, [
                Characteristic(GATT_BATTERY_LEVEL_CHARACTERISTIC, Characteristic.Properties.READ,
                               Characteristic.READABLE, bytes([100]))]),
        ])
        adv = bytes(AdvertisingData([
            data_types.Flags(AdvertisingData.Flags.LE_GENERAL_DISCOVERABLE_MODE | AdvertisingData.Flags.BR_EDR_NOT_SUPPORTED),
            data_types.CompleteLocalName(NAME),
            data_types.IncompleteListOf16BitServiceUUIDs([GATT_HUMAN_INTERFACE_DEVICE_SERVICE]),
            data_types.Appearance(data_types.Appearance.Category.HUMAN_INTERFACE_DEVICE,
                                  data_types.Appearance.HumanInterfaceDeviceSubcategory.MOUSE)]))
        # shortcut (cùng bản vá AX211 ở trên): ở bootloader, card trả lời lệnh nạp firmware trên kênh ACL nhưng theo
        # khung gói event (0E 04 01 09 FC 00); bộ tách gói ACL đọc nhầm độ dài thành 0x0901 và chờ mãi. Trong lúc
        # power_on (chưa có kết nối nên chưa có ACL thật) thì tách kênh ACL theo khung event, xong trả lại như cũ.
        src = self.transport.source
        acl = getattr(src, "splitters", {}).get(HCI_ACL_DATA_PACKET)
        if acl:
            src.splitters[HCI_ACL_DATA_PACKET] = EventPacketSplitter(lambda p: src.queue_packet(HCI_ACL_DATA_PACKET, p))
        try:
            await d.power_on()  # Bumble tự nạp firmware Intel ở bước này nếu card đang ở bootloader
        finally:
            if acl:
                src.splitters[HCI_ACL_DATA_PACKET] = acl
        await d.start_advertising(auto_restart=True, advertising_data=adv)

    @property
    def connected(self):
        return bool(self.subscribers)

    async def send(self, buttons, fx, fy, wheel=0):
        await self.device.notify_subscribers(self.report, mouse_report(buttons, fx, fy, wheel))


async def demo():
    m = BumbleMouse()
    await m.start(sys.argv[1] if len(sys.argv) > 1 else "usb:0")
    print(f'Đang phát sóng "{NAME}". Trên iPhone: bật AssistiveTouch, vào Cài đặt > Bluetooth, chọn "{NAME}".', flush=True)
    while not m.connected:
        await asyncio.sleep(0.5)
    print("iPhone đã kết nối. Con trỏ sẽ đi qua 4 góc của hình vuông giữa màn hình.", flush=True)
    for fx, fy in ((0.25, 0.25), (0.75, 0.25), (0.75, 0.75), (0.25, 0.75), (0.5, 0.5)):
        await m.send(0, fx, fy)
        await asyncio.sleep(0.7)
    print("Xong. Giữ kết nối thêm 120s để thử.", flush=True)
    await asyncio.sleep(120)


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        assert mouse_report(1, 0.5, 1.0, -1) == bytes([1, 0x00, 0x40, 0xFF, 0x7F, 0xFF])  # 16384, 32767, -1
        assert mouse_report(0, -3, 7, 500) == bytes([0, 0, 0, 0xFF, 0x7F, 127])  # kẹp ngoài khoảng
        print("self-test OK")
    else:
        asyncio.run(demo())
