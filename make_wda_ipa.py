"""Tạo WDA.ipa từ bản WebDriverAgent build sẵn (chưa ký) của Appium, để ký và cài bằng Sideloadly."""
import io
import sys
import urllib.request
import zipfile

sys.stdout.reconfigure(encoding="utf-8")  # console Windows mặc định cp1252 không in được tiếng Việt
URL = "https://github.com/appium/WebDriverAgent/releases/download/v16.15.3/WebDriverAgentRunner-Runner.zip"

src = zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(sys.argv[1] if len(sys.argv) > 1 else URL).read()))
with zipfile.ZipFile("WDA.ipa", "w", zipfile.ZIP_DEFLATED) as ipa:
    for info in src.infolist():
        data = src.read(info)
        info.filename = "Payload/" + info.filename  # IPA = zip chứa Payload/<tên>.app; giữ nguyên quyền thực thi của file
        ipa.writestr(info, data)
print("Đã tạo WDA.ipa")
