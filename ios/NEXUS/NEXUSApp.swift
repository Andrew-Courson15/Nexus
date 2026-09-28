import SwiftUI

/// NEXUS for iPhone: your NEXUS (running on your computer) as an app.
///
/// The first time, you give it your computer's private NEXUS address (shown as a QR code in
/// NEXUS on the computer: Settings → Your phone). After that it opens straight into NEXUS, and
/// when the computer is off it shows the copy saved on this phone.
@main
struct NEXUSApp: App {
    @AppStorage("serverURL") private var serverURL = ""

    var body: some Scene {
        WindowGroup {
            if let url = URL(string: serverURL), !serverURL.isEmpty {
                NexusView(server: url, onChangeServer: { serverURL = "" })
                    .ignoresSafeArea(edges: .bottom)
                    .id(serverURL)  // a new address starts a fresh web view
            } else {
                SetupView { serverURL = $0.absoluteString }
            }
        }
    }
}
