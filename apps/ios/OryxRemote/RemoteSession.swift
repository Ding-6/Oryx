import Foundation
import SwiftUI

/// 与 PC 端的 WebSocket 会话：收帧解密成图片，把触摸加密后发回去。
@MainActor
final class RemoteSession: ObservableObject {
    enum State: Equatable {
        case idle
        case connecting
        case connected
        case failed(String)
    }

    @Published private(set) var state: State = .idle
    @Published private(set) var frame: UIImage?
    @Published private(set) var framesPerSecond: Double = 0
    @Published private(set) var peerReady = false

    private var task: URLSessionWebSocketTask?
    private var key: SymmetricKey?
    private var receiveLoop: Task<Void, Never>?
    private var frameTimestamps: [Date] = []

    func connect(relay: String, code: String) {
        disconnect()
        guard let key = try? RemoteCrypto.deriveKey(code: code) else {
            state = .failed("配对码无效")
            return
        }
        self.key = key

        var components = URLComponents(string: relay.trimmingCharacters(in: .whitespaces))
        let normalized = RemoteCrypto.normalize(code)
        // 允许用户只填域名：默认走 wss:// 的 /ws 路径
        if components?.scheme == nil {
            components = URLComponents(string: "wss://" + relay.trimmingCharacters(in: .whitespaces))
        }
        let basePath = components?.path ?? ""
        components?.path = basePath.isEmpty ? "/ws" : basePath
        components?.queryItems = [
            URLQueryItem(name: "role", value: "client"),
            URLQueryItem(name: "room", value: normalized),
        ]
        guard let url = components?.url else {
            state = .failed("中转地址不合法")
            return
        }

        state = .connecting
        let task = URLSession.shared.webSocketTask(with: url)
        self.task = task
        task.resume()

        receiveLoop = Task { [weak self] in
            await self?.sendControl(["t": "hello", "v": 1, "role": "client"])
            await self?.sendControl(["t": "ready", "v": 1])
            await self?.receiveMessages()
        }
    }

    func disconnect() {
        receiveLoop?.cancel()
        receiveLoop = nil
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        key = nil
        peerReady = false
    }

    // MARK: - 发送

    private func sendControl(_ object: [String: Any]) async {
        guard let key, let task else { return }
        guard let data = try? RemoteCrypto.control(object, key: key) else { return }
        try? await task.send(.data(data))
    }

    func sendTap(x: Double, y: Double) {
        Task { await sendInput(["kind": "tap", "x": x, "y": y]) }
    }

    func sendSwipe(x1: Double, y1: Double, x2: Double, y2: Double) {
        Task {
            await sendInput([
                "kind": "swipe", "x1": x1, "y1": y1, "x2": x2, "y2": y2, "duration_ms": 260,
            ])
        }
    }

    func sendWheel(delta: Int) {
        Task { await sendInput(["kind": "wheel", "delta": delta]) }
    }

    private func sendInput(_ fields: [String: Any]) async {
        var message: [String: Any] = ["t": "input", "v": 1]
        message.merge(fields) { _, new in new }
        await sendControl(message)
    }

    // MARK: - 接收

    private func receiveMessages() async {
        guard let task else { return }
        while !Task.isCancelled {
            do {
                let message = try await task.receive()
                guard let key else { return }
                switch message {
                case .data(let data):
                    handle(data, key: key)
                case .string:
                    break
                @unknown default:
                    break
                }
            } catch {
                if !Task.isCancelled {
                    state = .failed(error.localizedDescription)
                }
                return
            }
        }
    }

    private func handle(_ data: Data, key: SymmetricKey) {
        guard let (kind, payload) = try? RemoteCrypto.unpack(data, key: key) else { return }
        if kind == RemoteCrypto.kindFrame, let jpeg = payload as? Data, let image = UIImage(data: jpeg) {
            frame = image
            recordFrame()
        } else if kind == RemoteCrypto.kindControl, let dict = payload as? [String: Any] {
            if dict["t"] as? String == "meta" {
                peerReady = true
                state = .connected
            }
        }
    }

    private func recordFrame() {
        let now = Date()
        frameTimestamps.append(now)
        frameTimestamps.removeAll { now.timeIntervalSince($0) > 2 }
        framesPerSecond = Double(frameTimestamps.count) / 2.0
    }
}
