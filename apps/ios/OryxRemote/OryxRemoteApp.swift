import SwiftUI

@main
@MainActor
struct OryxRemoteApp: App {
    @StateObject private var session = RemoteSession()
    @State private var showingViewer = false

    var body: some Scene {
        WindowGroup {
            if showingViewer {
                NavigationStack {
                    ViewerView(session: session) {
                        session.disconnect()
                        showingViewer = false
                    }
                }
            } else {
                PairingView(session: session) {
                    showingViewer = true
                }
            }
        }
    }
}
