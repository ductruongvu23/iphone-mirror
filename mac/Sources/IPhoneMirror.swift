// iPhone Mirror cho macOS: phản chiếu màn hình iPhone qua cáp USB, giao diện kính lỏng theo bản mẫu Figma.
// Hình lấy giống QuickTime: bật CoreMediaIO "screen capture devices" -> iPhone cắm cáp hiện thành thiết bị AVFoundation
// (video + âm thanh), không cần AirPlay (macOS đã có AirPlay Receiver riêng chiếm cổng 7000).
// Kính: .glassEffect gốc của Apple trên macOS 26 (như callstack/liquid-glass), máy cũ dùng .ultraThinMaterial.
// shortcut: chưa có chuột Bluetooth điều khiển iPhone trên Mac (CoreBluetooth không cho quảng bá HID), cần dongle riêng cho Bumble.
import AVFoundation
import CoreMediaIO
import SwiftUI

@main
struct IPhoneMirrorApp: App {
    var body: some Scene {
        WindowGroup("iPhone Mirror") {
            ContentView().frame(minWidth: 980, minHeight: 680)
        }
        .windowStyle(.hiddenTitleBar)
    }
}

/// Cho phép iPhone/iPad cắm cáp hiện thành thiết bị quay màn hình (mặc định macOS ẩn đi, chỉ QuickTime bật).
func allowScreenCaptureDevices() {
    var prop = CMIOObjectPropertyAddress(
        mSelector: CMIOObjectPropertySelector(kCMIOHardwarePropertyAllowScreenCaptureDevices),
        mScope: CMIOObjectPropertyScope(kCMIOObjectPropertyScopeGlobal),
        mElement: CMIOObjectPropertyElement(kCMIOObjectPropertyElementMain))
    var allow: UInt32 = 1
    CMIOObjectSetPropertyData(CMIOObjectID(kCMIOObjectSystemObject), &prop, 0, nil, UInt32(MemoryLayout<UInt32>.size), &allow)
}

final class Mirror: NSObject, ObservableObject {
    @Published var devices: [AVCaptureDevice] = []
    @Published var current: AVCaptureDevice?
    @Published var size = CGSize(width: 1242, height: 2688)
    @Published var muted = false { didSet { audio.volume = muted ? 0 : volume } }
    @Published var volume: Float = 1 { didSet { audio.volume = muted ? 0 : volume } }
    @Published var denied = false
    let session = AVCaptureSession()
    private let audio = AVCaptureAudioPreviewOutput()
    private let queue = DispatchQueue(label: "capture")

    override init() {
        super.init()
        allowScreenCaptureDevices()
        if session.canAddOutput(audio) { session.addOutput(audio) }
        audio.volume = 1
        let nc = NotificationCenter.default
        for name in [NSNotification.Name.AVCaptureDeviceWasConnected, .AVCaptureDeviceWasDisconnected] {
            nc.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in self?.refresh() }
        }
        AVCaptureDevice.requestAccess(for: .video) { ok in
            DispatchQueue.main.async { self.denied = !ok; self.refresh() }
        }
    }

    /// iPhone sau khi bật screen capture hiện dưới dạng thiết bị "muxed" (hình + tiếng).
    func refresh() {
        devices = AVCaptureDevice.DiscoverySession(deviceTypes: [.external], mediaType: .muxed, position: .unspecified).devices
        if let c = current, devices.contains(c) { return }
        if let first = devices.first { start(first) } else { stop() }
    }

    func start(_ device: AVCaptureDevice) {
        current = device
        let dims = CMVideoFormatDescriptionGetDimensions(device.activeFormat.formatDescription)
        if dims.width > 0 { size = CGSize(width: CGFloat(dims.width), height: CGFloat(dims.height)) }
        queue.async { [session] in
            session.beginConfiguration()
            session.inputs.forEach { session.removeInput($0) }
            if let input = try? AVCaptureDeviceInput(device: device), session.canAddInput(input) { session.addInput(input) }
            session.commitConfiguration()
            if !session.isRunning { session.startRunning() }
        }
    }

    func stop() {
        current = nil
        queue.async { [session] in session.stopRunning() }
    }
}

/// Lớp hiển thị video không sao chép khung hình (AVCaptureVideoPreviewLayer vẽ thẳng bằng GPU).
struct PreviewView: NSViewRepresentable {
    let session: AVCaptureSession
    let radius: CGFloat

    func makeNSView(context: Context) -> NSView {
        let view = NSView()
        let layer = AVCaptureVideoPreviewLayer(session: session)
        layer.videoGravity = .resizeAspectFill
        layer.masksToBounds = true
        view.layer = layer
        view.wantsLayer = true
        return view
    }

    func updateNSView(_ view: NSView, context: Context) {
        view.layer?.cornerRadius = radius
    }
}

extension View {
    @ViewBuilder func glass(_ radius: CGFloat) -> some View {
        #if compiler(>=6.2)
        if #available(macOS 26, *) {
            self.glassEffect(.regular, in: .rect(cornerRadius: radius))
        } else {
            self.background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: radius))
        }
        #else
        self.background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: radius))
        #endif
    }
}

struct PanelSection<Content: View>: View {
    let title: String
    @ViewBuilder let content: Content
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title.uppercased()).font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 10) { content }
                .padding(14)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(.white.opacity(0.06), in: RoundedRectangle(cornerRadius: 20))
                .overlay(RoundedRectangle(cornerRadius: 20).stroke(.white.opacity(0.1)))
        }
    }
}

struct ContentView: View {
    @StateObject private var m = Mirror()
    @State private var sidebar = true
    private var playing: Bool { m.current != nil }

    var body: some View {
        ZStack {
            // nền than chì tối như bản Figma
            LinearGradient(colors: [Color(red: 0.06, green: 0.07, blue: 0.09), Color(red: 0.13, green: 0.16, blue: 0.23)],
                           startPoint: .topLeading, endPoint: .bottomTrailing).ignoresSafeArea()
            Circle().fill(Color(red: 0.23, green: 0.35, blue: 0.59).opacity(0.35)).frame(width: 700).blur(radius: 160).offset(x: -380, y: -260)
            Circle().fill(Color(red: 0.24, green: 0.23, blue: 0.39).opacity(0.35)).frame(width: 600).blur(radius: 160).offset(x: 420, y: 300)

            VStack(spacing: 20) {
                topBar
                HStack(spacing: 24) {
                    stage.frame(maxWidth: .infinity, maxHeight: .infinity)
                    if sidebar { panel.frame(width: 340).transition(.move(edge: .trailing).combined(with: .opacity)) }
                }
            }
            .padding(24)
        }
        .preferredColorScheme(.dark)
        .animation(.smooth, value: sidebar)
        .animation(.smooth, value: playing)
    }

    var topBar: some View {
        HStack(spacing: 12) {
            Image(systemName: "iphone").font(.title3).frame(width: 40, height: 40)
                .background(LinearGradient(colors: [Color(red: 0.31, green: 0.67, blue: 1), Color(red: 0.48, green: 0.25, blue: 0.82)],
                                           startPoint: .top, endPoint: .bottom), in: RoundedRectangle(cornerRadius: 11))
            VStack(alignment: .leading, spacing: 2) {
                Text("iPhone Mirror").font(.headline)
                Text("Phản chiếu qua cáp USB").font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            HStack(spacing: 8) {
                Circle().fill(playing ? .green : .orange).frame(width: 8, height: 8)
                Text(playing ? "Đang phát qua cáp USB" : "Đang chờ iPhone").font(.callout.weight(.semibold))
            }
            .padding(.horizontal, 14).padding(.vertical, 8).glass(18)
            Spacer()
            Button(sidebar ? "Ẩn bảng điều khiển" : "Hiện bảng điều khiển") { sidebar.toggle() }
                .buttonStyle(.plain).padding(.horizontal, 16).padding(.vertical, 9).glass(20)
                .keyboardShortcut(.escape, modifiers: [])
        }
        .padding(.horizontal, 14).frame(height: 64).glass(32)
    }

    @ViewBuilder var stage: some View {
        if playing {
            GeometryReader { geo in
                let h = geo.size.height, w = h * m.size.width / max(m.size.height, 1)
                let fitW = min(w, geo.size.width), fitH = fitW * m.size.height / max(m.size.width, 1)
                PreviewView(session: m.session, radius: min(fitW, fitH) * 0.075)
                    .frame(width: fitW, height: fitH)
                    .padding(6)
                    .background(Color.black, in: RoundedRectangle(cornerRadius: min(fitW, fitH) * 0.075 + 6))
                    .overlay(RoundedRectangle(cornerRadius: min(fitW, fitH) * 0.075 + 6).stroke(.white.opacity(0.35), lineWidth: 2))
                    .shadow(color: .black.opacity(0.45), radius: 40, y: 24)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        } else {
            VStack(spacing: 18) {
                Image(systemName: "cable.connector").font(.system(size: 34)).frame(width: 76, height: 76).glass(38)
                Text("Sẵn sàng phản chiếu").font(.largeTitle.weight(.semibold))
                Text(m.denied ? "Hãy cho phép iPhone Mirror dùng Camera trong Cài đặt hệ thống › Quyền riêng tư & Bảo mật."
                              : "Cắm iPhone vào máy Mac bằng cáp, mở khoá và chọn “Tin cậy” nếu được hỏi.")
                    .foregroundStyle(.secondary).multilineTextAlignment(.center)
                VStack(alignment: .leading, spacing: 12) {
                    ForEach(Array(["Cắm cáp USB giữa iPhone và Mac", "Mở khoá iPhone, chọn “Tin cậy”", "Hình hiện tự động sau vài giây"].enumerated()), id: \.0) { i, s in
                        HStack(spacing: 12) {
                            Text("\(i + 1)").font(.callout.weight(.semibold)).frame(width: 28, height: 28).background(.white.opacity(0.2), in: Circle())
                            Text(s)
                        }
                    }
                }
                .padding(18).frame(maxWidth: .infinity, alignment: .leading)
                .background(.white.opacity(0.06), in: RoundedRectangle(cornerRadius: 22))
            }
            .padding(36).frame(maxWidth: 560).glass(36)
        }
    }

    var panel: some View {
        VStack(alignment: .leading, spacing: 16) {
            PanelSection(title: "Kết nối") {
                Label(m.current?.localizedName ?? "Chưa có iPhone", systemImage: playing ? "cable.connector" : "iphone.slash")
                    .font(.headline)
                HStack(spacing: 8) {
                    stat(playing ? "\(Int(m.size.width))×\(Int(m.size.height))" : "–", "độ phân giải")
                    stat(playing ? "Gốc" : "–", "chất lượng")
                }
            }
            PanelSection(title: "Thiết bị") {
                if m.devices.isEmpty {
                    Text("Chưa thấy iPhone qua cáp").foregroundStyle(.secondary)
                } else {
                    Picker("iPhone", selection: Binding(get: { m.current?.uniqueID ?? "" },
                                                        set: { id in if let d = m.devices.first(where: { $0.uniqueID == id }) { m.start(d) } })) {
                        ForEach(m.devices, id: \.uniqueID) { Text($0.localizedName).tag($0.uniqueID) }
                    }
                    .labelsHidden()
                }
                Button("Tìm lại thiết bị") { m.refresh() }.buttonStyle(.link)
            }
            PanelSection(title: "Âm thanh") {
                Picker("Loa", selection: $m.muted) {
                    Text("Loa Mac").tag(false)
                    Text("Tắt tiếng").tag(true)
                }
                .pickerStyle(.segmented).labelsHidden()
                HStack {
                    Image(systemName: "speaker.fill").foregroundStyle(.secondary)
                    Slider(value: $m.volume, in: 0...1).disabled(m.muted)
                    Image(systemName: "speaker.wave.3.fill").foregroundStyle(.secondary)
                }
            }
            PanelSection(title: "Chuột Bluetooth") {
                Label("Bản Mac chưa hỗ trợ điều khiển bằng chuột ảo. Dùng bản Windows để điều khiển iPhone.", systemImage: "info.circle")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
        }
        .padding(20).frame(maxHeight: .infinity).glass(32)
    }

    func stat(_ value: String, _ label: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(value).font(.title3.weight(.semibold)).lineLimit(1).minimumScaleFactor(0.6)
            Text(label).font(.caption).foregroundStyle(.secondary)
        }
        .padding(12).frame(maxWidth: .infinity, alignment: .leading)
        .background(.white.opacity(0.08), in: RoundedRectangle(cornerRadius: 16))
    }
}
