import CryptoKit
import Foundation
import Network
import QuartzCore

/// Đường hình UDP mã hoá (xem "Đường hình UDP" trong remote_host.cpp). Khoá AES-256 của phiên nhận bên trong TLS;
/// mỗi gói AES-256-GCM với nonce = hướng + số thứ tự (không lặp), chống phát lại, trả thử thách để PC chỉ gửi hình tới
/// đúng máy này. Khung dựng từ mảnh dữ liệu và mảnh dư Reed-Solomon nên rớt vài gói vẫn không phải chờ gửi lại.
/// UDP bị chặn (chưa mở cổng UDP trên modem...) thì PC vẫn gửi hình qua TCP như cũ.
/// Gói: [loại 1][0 1][mã phiên 4][số thứ tự 8] | bản mã | nhãn 16.
final class UDPVideo {
    enum Kind: UInt8 { case hello = 1, ack, video, feedback }
    static let hdr = 14, tagSize = 16, vhdr = 14, shard = 1200, kmax = 200
    static let down: UInt32 = 1, up: UInt32 = 2  // hướng trong nonce: PC→iPhone, iPhone→PC

    private let key: SymmetricKey
    private let sid: UInt32
    private let conn: NWConnection
    private let queue = DispatchQueue(label: "vn.vdt.remotedesk.udp")
    private var seq: UInt64 = 0
    private var replay = ReplayWindow()
    private var challenge: UInt64 = 0
    private var timer: DispatchSourceTimer?
    private var ticks = 0
    private var lastVideo = 0.0
    private let assembler = FrameAssembler()
    var onFrame: ((Data) -> Void)?
    var onNeedKey: (() -> Void)?

    /// Đang nhận hình qua UDP (có gói hình trong 2 giây qua).
    var active: Bool { queue.sync { CACurrentMediaTime() - lastVideo < 2 } }

    init?(address: String, key: Data, sid: UInt32) {
        guard key.count == 32, let (h, p) = Self.split(address), let port = NWEndpoint.Port(rawValue: p) else { return nil }
        self.key = SymmetricKey(data: key)
        self.sid = sid
        conn = NWConnection(host: NWEndpoint.Host(h), port: port, using: .udp)
    }

    /// "1.2.3.4:8443", "[fe80::1]:8443", "nha.duckdns.org:8443" → (máy, cổng). UDP dùng cùng địa chỉ và số cổng với TCP.
    static func split(_ a: String) -> (String, UInt16)? {
        guard let i = a.lastIndex(of: ":"), let p = UInt16(a[a.index(after: i)...]) else { return nil }
        var h = String(a[..<i])
        if h.hasPrefix("["), h.hasSuffix("]") { h = String(h.dropFirst().dropLast()) }
        return h.isEmpty ? nil : (h, p)
    }

    func start() {
        conn.start(queue: queue)
        let t = DispatchSource.makeTimerSource(queue: queue)
        t.schedule(deadline: .now(), repeating: .milliseconds(250))
        t.setEventHandler { [weak self] in self?.tick() }
        timer = t
        t.resume()
        receive()
    }

    func stop() {
        queue.async {
            self.timer?.cancel()
            self.timer = nil
            self.conn.cancel()
        }
    }

    /// PC quay về gửi hình qua TCP: bỏ các khung UDP dở dang, lần sau giải lại từ khung khoá.
    func reset() { queue.async { self.assembler.reset() } }

    private func tick() {
        ticks += 1
        let flowing = CACurrentMediaTime() - lastVideo < 1
        // chưa có hình: chào (hoặc trả thử thách) mỗi 250 ms; có hình rồi: báo số liệu mỗi 500 ms, cũng giữ cổng NAT mở
        if !flowing || ticks % 2 == 0 { sendFeedback(challenge == 0 && !flowing ? .hello : .feedback) }
        if assembler.waitingKey(now: CACurrentMediaTime()) { onNeedKey?() }
    }

    private func sendFeedback(_ kind: Kind) {
        var b = Data(capacity: 24)
        b.appendBE(challenge, 8)
        b.appendBE(UInt64(assembler.received), 4)
        b.appendBE(UInt64(assembler.fixed), 4)
        b.appendBE(UInt64(assembler.lost), 4)
        b.appendBE(UInt64(assembler.last), 4)
        seq += 1
        conn.send(content: Self.seal(key: key, kind: kind.rawValue, sid: sid, seq: seq, dir: Self.up, body: b),
                  completion: .contentProcessed { _ in })
    }

    private func receive() {
        conn.receiveMessage { [weak self] data, _, _, error in
            guard let self, self.timer != nil else { return }  // đã dừng
            if let data { self.handle(data) }
            if error == nil { self.receive() } else { self.queue.asyncAfter(deadline: .now() + 0.2) { self.receive() } }
        }
    }

    private func handle(_ raw: Data) {
        let p = Data(raw)  // chỉ số bắt đầu từ 0
        guard p.count > Self.hdr + Self.tagSize, p.be(2, 4) == UInt64(sid) else { return }  // gói lạ: bỏ trước khi giải mã
        let s = p.be(6, 8)
        guard replay.check(s), let (kind, body) = Self.open(key: key, dir: Self.down, packet: p) else { return }
        replay.accept(s)
        switch Kind(rawValue: kind) {
        case .ack:  // thử thách của PC: trả ngay để hình chuyển sang UDP
            if body.count >= 8 {
                challenge = body.be(0, 8)
                sendFeedback(.feedback)
            }
        case .video:
            let now = CACurrentMediaTime()
            if now - lastVideo > 1 { assembler.reset() }  // PC vừa gửi qua TCP một lúc: giải lại từ khung khoá
            lastVideo = now
            assembler.add(body, now: lastVideo, deliver: { onFrame?($0) }, needKey: { onNeedKey?() })
        default:
            break
        }
    }

    static func nonce(_ dir: UInt32, _ seq: UInt64) -> AES.GCM.Nonce {
        var n = Data(capacity: 12)
        n.appendBE(UInt64(dir), 4)
        n.appendBE(seq, 8)
        return try! AES.GCM.Nonce(data: n)  // luôn đủ 12 byte
    }

    static func seal(key: SymmetricKey, kind: UInt8, sid: UInt32, seq: UInt64, dir: UInt32, body: Data) -> Data {
        var h = Data([kind, 0])
        h.appendBE(UInt64(sid), 4)
        h.appendBE(seq, 8)
        guard let box = try? AES.GCM.seal(body, using: key, nonce: nonce(dir, seq), authenticating: h) else { return Data() }
        return h + box.ciphertext + box.tag
    }

    /// Mở và xác thực một gói (phần đầu là AAD). Sai khoá, sai hướng, bị sửa: nil.
    static func open(key: SymmetricKey, dir: UInt32, packet: Data) -> (UInt8, Data)? {
        let p = Data(packet)
        guard p.count > hdr + tagSize,
              let box = try? AES.GCM.SealedBox(nonce: nonce(dir, p.be(6, 8)), ciphertext: p[hdr..<(p.count - tagSize)],
                                               tag: p[(p.count - tagSize)...]),
              let body = try? AES.GCM.open(box, using: key, authenticating: p[0..<hdr]) else { return nil }
        return (p[0], body)
    }
}

/// Cửa sổ chống phát lại 64 gói như IPsec. Chỉ ghi nhận gói đã xác thực.
struct ReplayWindow {
    private var top: UInt64 = 0, bits: UInt64 = 0
    func check(_ s: UInt64) -> Bool { s != 0 && (s > top || (top - s < 64 && bits >> (top - s) & 1 == 0)) }
    mutating func accept(_ s: UInt64) {
        if s > top {
            bits = s - top >= 64 ? 1 : bits << (s - top) | 1
            top = s
        } else {
            bits |= 1 << (top - s)
        }
    }
}

/// Dựng khung từ các mảnh và giao đúng thứ tự. Khung không dựng được (mất nhiều hơn số mảnh dư) thì bỏ, xin khung khoá,
/// và bỏ luôn các khung sau cho tới khung khoá để không hiện hình vỡ.
final class FrameAssembler {
    private struct Block {
        var k = 0, m = 0
        var shards: [Int: Data] = [:]
        var data: Data?
    }
    private struct Pending {
        var len = 0, count = 0, done = 0
        var blocks: [Int: Block] = [:]
        var whole: Data?
    }
    private var frames: [UInt32: Pending] = [:]
    private var next: UInt32?  // khung kế phải giao
    private var gapFor: UInt32?, gapStart = 0.0
    private var waitKey = true, askedAt = 0.0
    private(set) var received = 0, fixed = 0, lost = 0
    private(set) var last: UInt32 = 0

    func reset() {
        frames.removeAll()
        next = nil
        waitKey = true
    }

    /// Chờ khung khoá quá 0,5 giây thì xin lại.
    func waitingKey(now: Double) -> Bool {
        guard waitKey, next != nil, now - askedAt > 0.5 else { return false }
        askedAt = now
        return true
    }

    func add(_ b: Data, now: Double, deliver: (Data) -> Void, needKey: () -> Void) {
        guard b.count == UDPVideo.vhdr + UDPVideo.shard else { return }
        received += 1
        let s = b.startIndex
        let f = UInt32(b.be(0, 4)), len = Int(b.be(4, 4))
        let bi = Int(b[s + 8]), nb = Int(b[s + 9]), i = Int(b[s + 10]), k = Int(b[s + 11]), m = Int(b[s + 12])
        let per = UDPVideo.kmax * UDPVideo.shard
        guard nb > 0, bi < nb, k > 0, k <= UDPVideo.kmax, i < k + m, len > bi * per, len <= nb * per else { return }
        if next == nil { next = f }
        guard let n = next, f >= n else { return }  // khung đã giao hoặc đã bỏ
        var p = frames[f] ?? Pending(len: len, count: nb)
        var blk = p.blocks[bi] ?? Block(k: k, m: m)
        guard p.len == len, p.count == nb, p.whole == nil, blk.data == nil, blk.k == k, blk.m == m else { return }
        blk.shards[i] = b.subdata(in: (s + UDPVideo.vhdr)..<b.endIndex)
        if blk.shards.count >= k, let r = ReedSolomon.recover(&blk.shards, k: k, m: m) {
            fixed += r
            var d = Data(capacity: k * UDPVideo.shard)
            for j in 0..<k { d.append(blk.shards[j]!) }
            blk.data = d.prefix(min(per, len - bi * per))
            blk.shards.removeAll()
            p.done += 1
        }
        p.blocks[bi] = blk
        if p.done == nb {
            var whole = Data(capacity: len)
            for x in 0..<nb { whole.append(p.blocks[x]!.data!) }
            p.whole = whole
            p.blocks.removeAll()
        }
        frames[f] = p
        flush(now: now, newest: f, deliver: deliver, needKey: needKey)
    }

    /// Giao các khung đủ theo thứ tự. Khung kế còn thiếu mà đã có khung mới hơn 3 khung, hoặc thiếu quá 80 ms: coi như mất.
    private func flush(now: Double, newest: UInt32, deliver: (Data) -> Void, needKey: () -> Void) {
        while let n = next {
            if let w = frames[n]?.whole {
                frames[n] = nil
                next = n &+ 1
                last = n
                if waitKey {
                    guard w.first.map({ $0 & 1 == 1 }) == true else { continue }  // chưa tới khung khoá: bỏ
                    waitKey = false
                }
                deliver(w)
                continue
            }
            guard newest > n else { return }
            if gapFor != n { gapFor = n; gapStart = now }
            guard newest - n > 3 || now - gapStart > 0.08 else { return }
            frames[n] = nil
            lost += 1
            next = n &+ 1
            if !waitKey {
                waitKey = true
                askedAt = now
                needKey()
            }
        }
    }
}

/// Reed-Solomon kiểu Cauchy trên GF(256), đa thức 0x11D, giống bản PC: mảnh dư i = Σ_j c(i,j)·mảnh j với
/// c(i,j) = 1/((K+i) XOR j). Thiếu e mảnh dữ liệu thì chỉ cần giải hệ e×e từ e mảnh dư.
enum ReedSolomon {
    private static let gf: (exp: [UInt8], log: [Int]) = {
        var e = [UInt8](repeating: 0, count: 512), l = [Int](repeating: 0, count: 256), x = 1
        for i in 0..<255 {
            e[i] = UInt8(x)
            e[i + 255] = UInt8(x)
            l[x] = i
            x <<= 1
            if x & 0x100 != 0 { x ^= 0x11D }
        }
        return (e, l)
    }()
    private static let table: [UInt8] = {  // bảng nhân 256×256
        var t = [UInt8](repeating: 0, count: 65536)
        for a in 1..<256 { for b in 1..<256 { t[a << 8 | b] = gf.exp[gf.log[a] + gf.log[b]] } }
        return t
    }()

    static func mul(_ a: UInt8, _ b: UInt8) -> UInt8 { table[Int(a) << 8 | Int(b)] }
    static func inv(_ a: UInt8) -> UInt8 { gf.exp[255 - gf.log[Int(a)]] }
    static func coef(_ i: Int, _ j: Int, _ k: Int) -> UInt8 { inv(UInt8((k + i) ^ j)) }

    /// dst ^= c·src, từng byte.
    static func mulAdd<B: ContiguousBytes>(_ dst: inout [UInt8], _ src: B, _ c: UInt8) {
        let base = Int(c) << 8
        table.withUnsafeBufferPointer { t in
            src.withUnsafeBytes { s in
                dst.withUnsafeMutableBufferPointer { d in
                    for i in 0..<min(d.count, s.count) { d[i] ^= t[base | Int(s[i])] }
                }
            }
        }
    }

    /// m mảnh dư cho các mảnh dữ liệu cùng độ dài (bản PC tự làm; ở đây để self-test).
    static func parity(_ data: [Data], m: Int) -> [Data] {
        (0..<m).map { i in
            var p = [UInt8](repeating: 0, count: data.first?.count ?? 0)
            for (j, d) in data.enumerated() { mulAdd(&p, d, coef(i, j, data.count)) }
            return Data(p)
        }
    }

    /// Dựng lại các mảnh dữ liệu còn thiếu (chỉ số 0..<k) từ mảnh dư (k..<k+m). Trả số mảnh đã dựng, nil nếu thiếu quá m.
    static func recover(_ sh: inout [Int: Data], k: Int, m: Int) -> Int? {
        let missing = (0..<k).filter { sh[$0] == nil }
        if missing.isEmpty { return 0 }
        let par = Array(sh.keys.filter { $0 >= k && $0 < k + m }.sorted().prefix(missing.count))
        guard par.count == missing.count, let size = sh.values.first?.count else { return nil }
        var syn: [[UInt8]] = []
        for r in par {  // vế phải: mảnh dư trừ phần của các mảnh dữ liệu đang có
            var s = [UInt8](sh[r]!)
            for j in 0..<k { if let d = sh[j] { mulAdd(&s, d, coef(r - k, j, k)) } }
            syn.append(s)
        }
        guard let ai = invert(par.map { r in missing.map { coef(r - k, $0, k) } }) else { return nil }
        for (t, q) in missing.enumerated() {
            var d = [UInt8](repeating: 0, count: size)
            for r in 0..<syn.count where ai[t][r] != 0 { mulAdd(&d, syn[r], ai[t][r]) }
            sh[q] = Data(d)
        }
        return missing.count
    }

    /// Nghịch đảo ma trận vuông trên GF(256) (Gauss-Jordan).
    static func invert(_ m: [[UInt8]]) -> [[UInt8]]? {
        let n = m.count
        var a = m.enumerated().map { q, r in r + (0..<n).map { UInt8($0 == q ? 1 : 0) } }
        for col in 0..<n {
            guard let piv = (col..<n).first(where: { a[$0][col] != 0 }) else { return nil }
            a.swapAt(col, piv)
            let f = inv(a[col][col])
            a[col] = a[col].map { mul(f, $0) }
            for r in 0..<n where r != col && a[r][col] != 0 {
                let g = a[r][col]
                for c in 0..<(2 * n) { a[r][c] ^= mul(g, a[col][c]) }
            }
        }
        return a.map { Array($0[n...]) }
    }
}

extension Data {
    /// Số nguyên big-endian n byte tại vị trí off (tính từ đầu Data).
    func be(_ off: Int, _ n: Int) -> UInt64 {
        var v: UInt64 = 0
        for i in 0..<n { v = v << 8 | UInt64(self[startIndex + off + i]) }
        return v
    }

    mutating func appendBE(_ v: UInt64, _ n: Int) {
        for i in stride(from: n - 1, through: 0, by: -1) { append(UInt8(truncatingIfNeeded: v >> (8 * UInt64(i)))) }
    }
}
