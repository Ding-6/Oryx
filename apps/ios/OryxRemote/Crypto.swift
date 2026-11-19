import CryptoKit
import Foundation

enum RemoteError: LocalizedError {
    case badCode
    case crypto
    case badMessage

    var errorDescription: String? {
        switch self {
        case .badCode: return "配对码不能为空"
        case .crypto: return "加密失败"
        case .badMessage: return "消息格式不正确"
        }
    }
}

/// 与 PC 端 core/oryx/remote/protocol.py 一一对应。
/// 密钥：HKDF-SHA256(ikm = SHA256(归一化配对码), salt="oryx-remote", info="oryx-remote-v1", 32B)
/// 消息：AES-GCM，combined = nonce(12) + 密文 + tag(16)
enum RemoteCrypto {
    static let kindControl: UInt8 = 0x01
    static let kindFrame: UInt8 = 0x02

    static func normalize(_ code: String) -> String {
        code.uppercased().filter { $0.isLetter || $0.isNumber }
    }

    static func deriveKey(code: String) throws -> SymmetricKey {
        let normalized = normalize(code)
        guard !normalized.isEmpty else { throw RemoteError.badCode }
        let digest = SHA256.hash(data: Data(normalized.utf8))
        return HKDF<SHA256>.deriveKey(
            inputKeyMaterial: SymmetricKey(data: Data(digest)),
            salt: Data("oryx-remote".utf8),
            info: Data("oryx-remote-v1".utf8),
            outputByteCount: 32
        )
    }

    static func seal(_ plaintext: Data, key: SymmetricKey) throws -> Data {
        let sealed = try AES.GCM.seal(plaintext, using: key)
        guard let combined = sealed.combined else { throw RemoteError.crypto }
        return combined
    }

    static func open(_ blob: Data, key: SymmetricKey) throws -> Data {
        let box = try AES.GCM.SealedBox(combined: blob)
        return try AES.GCM.open(box, using: key)
    }

    static func control(_ object: [String: Any], key: SymmetricKey) throws -> Data {
        var payload = Data([kindControl])
        payload.append(try JSONSerialization.data(withJSONObject: object))
        return try seal(payload, key: key)
    }

    /// 解出一条消息：kind 为 1 时返回 JSON 字典，为 2 时返回 JPEG 数据。
    static func unpack(_ blob: Data, key: SymmetricKey) throws -> (UInt8, Any) {
        let plaintext = try open(blob, key: key)
        guard let kind = plaintext.first else { throw RemoteError.badMessage }
        let body = plaintext.dropFirst()
        switch kind {
        case kindControl:
            let object = try JSONSerialization.jsonObject(with: Data(body))
            guard let dict = object as? [String: Any] else { throw RemoteError.badMessage }
            return (kind, dict)
        case kindFrame:
            return (kind, Data(body))
        default:
            throw RemoteError.badMessage
        }
    }
}
