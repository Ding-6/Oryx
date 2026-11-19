import SwiftUI

struct PairingView: View {
    @ObservedObject var session: RemoteSession
    let onConnected: () -> Void

    @AppStorage("oryx.relay") private var relay: String = ""
    @State private var code: String = ""

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("wss://relay.example.com", text: $relay)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                } header: {
                    Text("中转地址")
                } footer: {
                    Text("留空路径时默认连接 /ws。也可以只填域名，自动补 wss://。")
                }

                Section {
                    TextField("配对码", text: $code)
                        .textInputAutocapitalization(.characters)
                        .autocorrectionDisabled()
                        .font(.system(.title2, design: .monospaced))
                        .onChange(of: code) { newValue in
                            code = String(RemoteCrypto.normalize(newValue).prefix(12))
                        }
                } header: {
                    Text("配对码")
                } footer: {
                    Text("在电脑端打开 Oryx，点「远程」，屏幕上会显示 6–8 位配对码。")
                }

                if case .failed(let message) = session.state {
                    Section {
                        Text(message).foregroundStyle(.red)
                    }
                }

                Section {
                    Button {
                        session.connect(relay: relay, code: code)
                    } label: {
                        HStack {
                            Spacer()
                            if session.state == .connecting {
                                ProgressView().padding(.trailing, 6)
                                Text("连接中…")
                            } else {
                                Text("连接")
                            }
                            Spacer()
                        }
                    }
                    .disabled(code.isEmpty || relay.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
            .navigationTitle("连接电脑")
            .onChange(of: session.state) { newValue in
                if newValue == .connected { onConnected() }
            }
        }
    }
}
