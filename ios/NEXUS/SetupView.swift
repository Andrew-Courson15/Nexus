import SwiftUI

/// First run (or "Change NEXUS address"): where is your NEXUS?
struct SetupView: View {
    let onConnected: (URL) -> Void

    @State private var address = ""
    @State private var checking = false
    @State private var problem: String?
    @State private var scanning = false

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Text("NEXUS runs on your computer. This app opens it from anywhere, privately, through Tailscale.")
                        .font(.callout)
                }
                Section("Your NEXUS address") {
                    TextField("https://your-computer.tail1234.ts.net", text: $address)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .submitLabel(.go)
                        .onSubmit(connect)
                    if ScannerView.isAvailable {
                        Button("Scan the QR code instead") { scanning = true }
                    }
                    Button(action: connect) {
                        HStack {
                            Text("Connect")
                            if checking { Spacer(); ProgressView() }
                        }
                    }
                    .disabled(address.trimmingCharacters(in: .whitespaces).isEmpty || checking)
                    if let problem {
                        Text(problem).foregroundStyle(.red).font(.callout)
                    }
                }
                Section("Where to find it") {
                    Text("1. On your computer, open NEXUS → Settings → Your phone, and press Turn on phone access.")
                    Text("2. Install Tailscale on this iPhone from the App Store and sign in with the same account as the computer.")
                    Text("3. Scan the QR code NEXUS shows, or type the address under it.")
                }
                .font(.callout)
            }
            .navigationTitle("NEXUS")
            .sheet(isPresented: $scanning) {
                ScannerView { code in
                    scanning = false
                    address = code
                    connect()
                }
                .ignoresSafeArea()
            }
        }
    }

    private func connect() {
        guard let url = Self.normalize(address) else {
            problem = "That doesn't look like an address. It usually starts with https:// and ends in .ts.net"
            return
        }
        checking = true
        problem = nil
        Task {
            let result = await Self.check(url)
            checking = false
            switch result {
            case .success: onConnected(url)
            case .failure(let message): problem = message
            }
        }
    }

    /// "gaming-pc.tail1234.ts.net" → "https://gaming-pc.tail1234.ts.net" (no path).
    static func normalize(_ text: String) -> URL? {
        var t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if t.isEmpty { return nil }
        if !t.contains("://") { t = "https://" + t }
        guard var parts = URLComponents(string: t), let host = parts.host, !host.isEmpty,
              parts.scheme == "https" || parts.scheme == "http" else { return nil }
        parts.path = ""
        parts.query = nil
        parts.fragment = nil
        return parts.url
    }

    enum CheckResult { case success, failure(String) }

    /// Is NEXUS answering there? (Its /api/ping says {"ok": true, "version": …}.)
    static func check(_ url: URL) async -> CheckResult {
        var request = URLRequest(url: url.appendingPathComponent("api/ping"), timeoutInterval: 10)
        request.cachePolicy = .reloadIgnoringLocalCacheData
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            let status = (response as? HTTPURLResponse)?.statusCode ?? 0
            if status == 200, let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any], json["ok"] as? Bool == true {
                return .success
            }
            if status == 502 || status == 503 || status == 504 {
                return .failure("Your computer answered, but NEXUS isn't running on it. Start NEXUS there and try again.")
            }
            return .failure("Something answered at that address, but it isn't NEXUS (status \(status)).")
        } catch {
            return .failure("Couldn't reach it. Is the computer on, with NEXUS running, and is Tailscale on here and there?")
        }
    }
}
