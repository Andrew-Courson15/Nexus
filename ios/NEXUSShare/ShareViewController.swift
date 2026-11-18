import SwiftUI
import UIKit
import UniformTypeIdentifiers

/// "Share → NEXUS": send photos, PDFs, voice memos, links and text from other apps (Photos, Notes,
/// Safari, Files …) into NEXUS, with an optional note. Your words go in exactly as typed.
///
/// The extension can't read the NEXUS app's settings (that needs a paid developer account's App
/// Group), so the first time it asks for your NEXUS address itself, and the passcode if NEXUS has
/// one; both are remembered.
@objc(ShareViewController)
final class ShareViewController: UIViewController {
    override func viewDidLoad() {
        super.viewDidLoad()
        let items = (extensionContext?.inputItems as? [NSExtensionItem]) ?? []
        let providers = items.flatMap { $0.attachments ?? [] }
        let model = ShareModel(
            providers: providers,
            finish: { [weak self] in self?.extensionContext?.completeRequest(returningItems: nil, completionHandler: nil) },
            cancel: { [weak self] in
                self?.extensionContext?.cancelRequest(withError: NSError(domain: NSCocoaErrorDomain, code: NSUserCancelledError))
            })
        let host = UIHostingController(rootView: ShareView(model: model))
        addChild(host)
        host.view.frame = view.bounds
        host.view.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        view.addSubview(host.view)
        host.didMove(toParent: self)
        Task { await model.load() }
    }
}

struct SharedFile: Identifiable {
    let id = UUID()
    let name: String
    let mime: String
    let data: Data
    let preview: UIImage?
}

enum ShareError: LocalizedError {
    case locked
    case message(String)

    var errorDescription: String? {
        switch self {
        case .locked: return "NEXUS asks for its passcode."
        case .message(let text): return text
        }
    }
}

@MainActor
final class ShareModel: ObservableObject {
    @Published var files: [SharedFile] = []
    @Published var text = ""
    @Published var address = UserDefaults.standard.string(forKey: "serverURL") ?? ""
    @Published var passcode = ""
    @Published var needsPasscode = false
    @Published var loading = true
    @Published var sending = false
    @Published var done = false
    @Published var problem: String?

    let providers: [NSItemProvider]
    let finish: () -> Void
    let cancel: () -> Void

    init(providers: [NSItemProvider], finish: @escaping () -> Void, cancel: @escaping () -> Void) {
        self.providers = providers
        self.finish = finish
        self.cancel = cancel
    }

    // MARK: What was shared

    /// Pictures, PDFs and audio are sent as files (exactly as they are); links and text go in the note.
    func load() async {
        var notes: [String] = []
        for provider in providers {
            if let type = Self.fileType(provider) {
                guard let data = await Self.data(provider, type) else { continue }
                let uti = UTType(type)
                let ext = uti?.preferredFilenameExtension ?? "bin"
                let base = provider.suggestedName ?? "shared"
                let name = base.lowercased().hasSuffix("." + ext.lowercased()) ? base : "\(base).\(ext)"
                let preview = uti?.conforms(to: .image) == true
                    ? UIImage(data: data)?.preparingThumbnail(of: CGSize(width: 180, height: 180)) : nil
                files.append(SharedFile(name: name, mime: uti?.preferredMIMEType ?? "application/octet-stream",
                                        data: data, preview: preview))
            } else if provider.hasItemConformingToTypeIdentifier(UTType.url.identifier) {
                if let link = await Self.string(provider, UTType.url.identifier) { notes.append(link) }
            } else if provider.hasItemConformingToTypeIdentifier(UTType.plainText.identifier) {
                if let words = await Self.string(provider, UTType.plainText.identifier) { notes.append(words) }
            }
        }
        text = notes.joined(separator: "\n")
        loading = false
        if files.isEmpty && text.isEmpty { problem = "There's nothing here NEXUS can keep (pictures, PDFs, audio, links or text)." }
    }

    static func fileType(_ provider: NSItemProvider) -> String? {
        let wanted: [UTType] = [.image, .pdf, .audio]
        for id in provider.registeredTypeIdentifiers {
            guard let type = UTType(id) else { continue }
            if wanted.contains(where: { type.conforms(to: $0) }) { return id }
        }
        return nil
    }

    static func data(_ provider: NSItemProvider, _ type: String) async -> Data? {
        await withCheckedContinuation { continuation in
            _ = provider.loadDataRepresentation(forTypeIdentifier: type) { data, _ in
                continuation.resume(returning: data)
            }
        }
    }

    static func string(_ provider: NSItemProvider, _ type: String) async -> String? {
        await withCheckedContinuation { continuation in
            provider.loadItem(forTypeIdentifier: type, options: nil) { item, _ in
                if let url = item as? URL {
                    continuation.resume(returning: url.absoluteString)
                } else if let words = item as? String {
                    continuation.resume(returning: words)
                } else if let data = item as? Data {
                    continuation.resume(returning: String(data: data, encoding: .utf8))
                } else {
                    continuation.resume(returning: nil)
                }
            }
        }
    }

    // MARK: Sending

    func send() {
        guard let server = Self.normalize(address) else {
            problem = "Type your NEXUS address, like https://your-computer.tail1234.ts.net"
            return
        }
        let note = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if files.isEmpty && note.isEmpty {
            problem = "Type your idea first."
            return
        }
        sending = true
        problem = nil
        Task {
            do {
                if !passcode.isEmpty { try await unlock(server) }
                if files.isEmpty {
                    try await capture(server, note)
                } else {
                    for (index, file) in files.enumerated() {
                        try await upload(server, file, note: index == 0 ? note : "")
                    }
                }
                UserDefaults.standard.set(server.absoluteString, forKey: "serverURL")
                done = true
                sending = false
                try? await Task.sleep(nanoseconds: 800_000_000)
                finish()
            } catch ShareError.locked {
                needsPasscode = true
                problem = ShareError.locked.errorDescription
                sending = false
            } catch {
                problem = error.localizedDescription
                sending = false
            }
        }
    }

    private func unlock(_ server: URL) async throws {
        var req = URLRequest(url: server.appendingPathComponent("api/lock/unlock"), timeoutInterval: 20)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: ["passcode": passcode])
        do {
            _ = try await perform(req)
        } catch ShareError.locked {
            throw ShareError.message("That passcode isn't right.")
        }
        passcode = ""
        needsPasscode = false
    }

    private func capture(_ server: URL, _ words: String) async throws {
        var req = URLRequest(url: server.appendingPathComponent("api/entries"), timeoutInterval: 30)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: ["original_text": words])
        _ = try await perform(req)
    }

    private func upload(_ server: URL, _ file: SharedFile, note: String) async throws {
        let boundary = "nexus-\(UUID().uuidString)"
        var body = Data()
        func add(_ string: String) { body.append(Data(string.utf8)) }
        if !note.isEmpty {
            add("--\(boundary)\r\nContent-Disposition: form-data; name=\"note\"\r\n\r\n\(note)\r\n")
        }
        let name = file.name.replacingOccurrences(of: "\"", with: "")
        add("--\(boundary)\r\nContent-Disposition: form-data; name=\"file\"; filename=\"\(name)\"\r\n")
        add("Content-Type: \(file.mime)\r\n\r\n")
        body.append(file.data)
        add("\r\n--\(boundary)--\r\n")
        var req = URLRequest(url: server.appendingPathComponent("api/uploads"), timeoutInterval: 180)
        req.httpMethod = "POST"
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        req.httpBody = body
        _ = try await perform(req)
    }

    private func perform(_ req: URLRequest) async throws -> Data {
        let result: (Data, URLResponse)
        do {
            result = try await URLSession.shared.data(for: req)
        } catch {
            throw ShareError.message("Couldn't reach NEXUS. Is the computer on with NEXUS running, and is Tailscale on here and there?")
        }
        let (data, response) = result
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        if status == 401 { throw ShareError.locked }
        if status == 502 || status == 503 || status == 504 {
            throw ShareError.message("Your computer answered, but NEXUS isn't running on it. Start NEXUS there and try again.")
        }
        guard (200..<300).contains(status) else {
            let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            throw ShareError.message((json?["detail"] as? String) ?? "NEXUS answered with an error (\(status)).")
        }
        return data
    }

    /// "gaming-pc.tail1234.ts.net" → "https://gaming-pc.tail1234.ts.net" (same as the app's setup screen).
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
}

struct ShareView: View {
    @ObservedObject var model: ShareModel

    var body: some View {
        NavigationStack {
            Form {
                if model.loading {
                    Section { ProgressView("Getting it ready…") }
                } else {
                    if !model.files.isEmpty {
                        Section {
                            ScrollView(.horizontal, showsIndicators: false) {
                                HStack(spacing: 8) {
                                    ForEach(model.files) { file in
                                        if let preview = file.preview {
                                            Image(uiImage: preview).resizable().scaledToFill()
                                                .frame(width: 72, height: 72).clipShape(RoundedRectangle(cornerRadius: 8))
                                        } else {
                                            Label(file.name, systemImage: "doc").font(.footnote).lineLimit(1)
                                        }
                                    }
                                }
                            }
                            Text(model.files.count == 1 ? "1 file, kept exactly as it is." : "\(model.files.count) files, kept exactly as they are.")
                                .font(.footnote).foregroundStyle(.secondary)
                        }
                    }
                    Section(model.files.isEmpty ? "Your idea" : "Your note (optional)") {
                        TextField(model.files.isEmpty ? "What's the idea?" : "What is it, or why keep it?", text: $model.text, axis: .vertical)
                            .lineLimit(3...8)
                    }
                    Section("Your NEXUS address") {
                        TextField("https://your-computer.tail1234.ts.net", text: $model.address)
                            .keyboardType(.URL)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                        if model.needsPasscode {
                            SecureField("Passcode", text: $model.passcode)
                        }
                    }
                    if let problem = model.problem {
                        Section { Text(problem).foregroundStyle(.red) }
                    }
                    if model.done {
                        Section { Label("Sent to NEXUS", systemImage: "checkmark.circle.fill").foregroundStyle(.green) }
                    }
                }
            }
            .navigationTitle("Send to NEXUS")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { model.cancel() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    if model.sending {
                        ProgressView()
                    } else {
                        Button("Send") { model.send() }.disabled(model.loading || model.done)
                    }
                }
            }
        }
    }
}
