#!/bin/bash
# Build iPhone Mirror.app (universal arm64 + x86_64, macOS 14+). Chạy trên máy Mac có Xcode, hoặc GitHub Actions.
set -euo pipefail
cd "$(dirname "$0")"
APP="build/iPhone Mirror.app"
rm -rf build
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" build/AppIcon.iconset

for arch in arm64 x86_64; do
  swiftc -O -swift-version 5 -parse-as-library -target "$arch-apple-macos14.0" Sources/*.swift -o "build/iPhoneMirror-$arch"
done
lipo -create build/iPhoneMirror-arm64 build/iPhoneMirror-x86_64 -output "$APP/Contents/MacOS/iPhoneMirror"

for s in 16 32 128 256 512; do
  sips -z $s $s ../icons/app_icon_1024.png --out "build/AppIcon.iconset/icon_${s}x${s}.png" >/dev/null
  sips -z $((s * 2)) $((s * 2)) ../icons/app_icon_1024.png --out "build/AppIcon.iconset/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns build/AppIcon.iconset -o "$APP/Contents/Resources/AppIcon.icns"
cp Info.plist "$APP/Contents/"

codesign --force --deep -s - "$APP"  # ký ad-hoc: chưa có chứng chỉ Developer ID
codesign --verify "$APP"
lipo -archs "$APP/Contents/MacOS/iPhoneMirror"
ditto -c -k --keepParent "$APP" build/iPhoneMirror-mac.zip
echo "Xong: mac/build/iPhoneMirror-mac.zip"
