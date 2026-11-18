import CryptoKit
import Foundation
import WebKit

/// Serves the NEXUS app inside the iPhone app at nexus://localhost/ and passes its requests to
/// the NEXUS on your computer (over Tailscale).
///
/// Because the pages come through here, the app always opens, even when the computer is off:
/// - the app itself ("/" and "/static/…"): from the computer when it answers within a few
///   seconds (and saved on the phone), otherwise the copy saved on the phone;
/// - picture thumbnails: saved on the phone the first time they're shown, then used from there;
/// - full pictures: from the computer, or the saved thumbnail when it can't be reached;
/// - everything else (/api/…): passed straight to the computer. When it can't be reached the
///   app gets a 502, which it knows means "use the copy saved on this phone".
final class NexusSchemeHandler: NSObject, WKURLSchemeHandler {
    static let scheme = "nexus"
    static let origin = URL(string: "nexus://localhost/")!

    let server: URL
    private let session: URLSession
    private let cache = DiskCache()
    private var running: [ObjectIdentifier: URLSessionTask] = [:]  // only touched on the main thread
    private var active = Set<ObjectIdentifier>()
    private var saves = 0

    init(server: URL) {
        self.server = server
        let config = URLSessionConfiguration.default
        config.httpCookieStorage = .shared  // the passcode's "this device is unlocked" cookie
        config.httpShouldSetCookies = true
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        config.timeoutIntervalForRequest = 300
        config.waitsForConnectivity = false
        session = URLSession(configuration: config)
    }

    // MARK: WKURLSchemeHandler

    func webView(_ webView: WKWebView, start task: WKURLSchemeTask) {
        let id = ObjectIdentifier(task)
        active.insert(id)
        guard let url = task.request.url else { return fail(task, status: 400) }
        let path = url.path.isEmpty ? "/" : url.path
        let query = url.query.map { "?" + $0 } ?? ""

        if path == "/__native/status" {
            return respondJSON(task, ["server": server.absoluteString, "thumbnails": cache.count(prefix: "thumb:"),
                                      "app": Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? ""])
        }
        if path == "/" || path.hasPrefix("/static/") || path == "/manifest.webmanifest" {
            return shell(task, path: path, query: query)
        }
        if let id = Self.mediaID(path, kind: "thumb") {
            return thumbnail(task, id: id, path: path, query: query)
        }
        if let id = Self.mediaID(path, kind: "preview") {
            return preview(task, id: id, path: path, query: query)
        }
        passThrough(task, path: path, query: query)
    }

    func webView(_ webView: WKWebView, stop task: WKURLSchemeTask) {
        let id = ObjectIdentifier(task)
        active.remove(id)
        running.removeValue(forKey: id)?.cancel()
    }

    // MARK: kinds of request

    /// The app itself: the computer's copy when it answers quickly, otherwise the saved one.
    private func shell(_ task: WKURLSchemeTask, path: String, query: String) {
        let key = "shell:" + path  // one saved copy per file (the newest version wins)
        var request = URLRequest(url: target(path, query), timeoutInterval: path == "/" ? 4 : 8)
        request.cachePolicy = .reloadIgnoringLocalCacheData
        fetch(task, request) { [weak self] result in
            guard let self else { return }
            if case .success(let (response, data)) = result, response.statusCode == 200 {
                self.cache.put(key, data: data, contentType: response.value(forHTTPHeaderField: "Content-Type"))
                self.respond(task, status: 200, data: data, contentType: response.value(forHTTPHeaderField: "Content-Type"),
                             headers: ["Cache-Control": "no-store"])
            } else if let saved = self.cache.get(key) {
                self.respond(task, status: 200, data: saved.data, contentType: saved.contentType)
            } else if path == "/", let page = Bundle.main.url(forResource: "offline", withExtension: "html"),
                      let data = try? Data(contentsOf: page) {
                self.respond(task, status: 200, data: data, contentType: "text/html; charset=utf-8")
            } else {
                self.fail(task, status: 502)
            }
        }
    }

    /// Thumbnails: the copy saved on this phone first (they never change: the address includes
    /// the file's fingerprint), otherwise from the computer, saved for next time.
    private func thumbnail(_ task: WKURLSchemeTask, id: String, path: String, query: String) {
        let key = "thumb:\(id)"  // an upload never changes, so neither does its thumbnail
        if let saved = cache.get(key) {
            return respond(task, status: 200, data: saved.data, contentType: saved.contentType,
                           headers: ["Cache-Control": "max-age=31536000"])
        }
        fetch(task, URLRequest(url: target(path, query), timeoutInterval: 15)) { [weak self] result in
            guard let self else { return }
            if case .success(let (response, data)) = result, response.statusCode == 200 {
                self.cache.put(key, data: data, contentType: response.value(forHTTPHeaderField: "Content-Type"))
                self.saves += 1
                if self.saves % 100 == 0 { self.cache.trim(prefix: "thumb:", keep: 3000) }
                self.respond(task, status: 200, data: data, contentType: response.value(forHTTPHeaderField: "Content-Type"))
            } else if case .success(let (response, data)) = result {
                self.respond(task, status: response.statusCode, data: data, contentType: response.value(forHTTPHeaderField: "Content-Type"))
            } else {
                self.fail(task, status: 502)
            }
        }
    }

    /// Full pictures: from the computer; when it can't be reached, the saved thumbnail.
    private func preview(_ task: WKURLSchemeTask, id: String, path: String, query: String) {
        fetch(task, URLRequest(url: target(path, query), timeoutInterval: 20)) { [weak self] result in
            guard let self else { return }
            if case .success(let (response, data)) = result, response.statusCode < 500 {
                self.respond(task, status: response.statusCode, data: data,
                             contentType: response.value(forHTTPHeaderField: "Content-Type"))
            } else if let saved = self.cache.get("thumb:\(id)") {
                self.respond(task, status: 200, data: saved.data, contentType: saved.contentType)
            } else {
                self.fail(task, status: 502)
            }
        }
    }

    /// Everything else goes to the computer as it is (method, headers, body).
    private func passThrough(_ task: WKURLSchemeTask, path: String, query: String) {
        var request = URLRequest(url: target(path, query), timeoutInterval: 300)
        request.httpMethod = task.request.httpMethod ?? "GET"
        for (name, value) in task.request.allHTTPHeaderFields ?? [:]
        where !["host", "origin", "referer", "cookie", "content-length"].contains(name.lowercased()) {
            request.setValue(value, forHTTPHeaderField: name)
        }
        request.httpBody = Self.body(of: task.request)
        fetch(task, request) { [weak self] result in
            guard let self else { return }
            switch result {
            case .success(let (response, data)):
                var headers: [String: String] = [:]
                for (name, value) in response.allHeaderFields {
                    guard let name = name as? String, let value = value as? String else { continue }
                    if !["content-encoding", "content-length", "transfer-encoding", "set-cookie"].contains(name.lowercased()) {
                        headers[name] = value
                    }
                }
                self.respond(task, status: response.statusCode, data: data, contentType: nil, headers: headers)
            case .failure:
                self.fail(task, status: 502)  // the app treats this as "NEXUS isn't reachable"
            }
        }
    }

    // MARK: plumbing

    private func target(_ path: String, _ query: String) -> URL {
        var base = server.absoluteString
        while base.hasSuffix("/") { base.removeLast() }
        return URL(string: base + path + query) ?? server
    }

    private func fetch(_ task: WKURLSchemeTask, _ request: URLRequest,
                       done: @escaping (Result<(HTTPURLResponse, Data), Error>) -> Void) {
        let id = ObjectIdentifier(task)
        let job = session.dataTask(with: request) { data, response, error in
            DispatchQueue.main.async {
                self.running.removeValue(forKey: id)
                guard self.active.contains(id) else { return }  // the page stopped waiting
                if let response = response as? HTTPURLResponse, error == nil {
                    done(.success((response, data ?? Data())))
                } else {
                    done(.failure(error ?? URLError(.badServerResponse)))
                }
            }
        }
        running[id] = job
        job.resume()
    }

    private func respond(_ task: WKURLSchemeTask, status: Int, data: Data, contentType: String?,
                         headers: [String: String] = [:]) {
        let id = ObjectIdentifier(task)
        guard active.contains(id), let url = task.request.url else { return }
        active.remove(id)
        var fields = headers
        if let contentType { fields["Content-Type"] = contentType }
        fields["Content-Length"] = String(data.count)
        let response = HTTPURLResponse(url: url, statusCode: status, httpVersion: "HTTP/1.1", headerFields: fields)!
        task.didReceive(response)
        task.didReceive(data)
        task.didFinish()
    }

    private func respondJSON(_ task: WKURLSchemeTask, _ object: [String: Any]) {
        let data = (try? JSONSerialization.data(withJSONObject: object)) ?? Data("{}".utf8)
        respond(task, status: 200, data: data, contentType: "application/json")
    }

    private func fail(_ task: WKURLSchemeTask, status: Int) {
        respond(task, status: status, data: Data(), contentType: "text/plain")
    }

    static func mediaID(_ path: String, kind: String) -> String? {
        let parts = path.split(separator: "/")
        guard parts.count == 4, parts[0] == "api", parts[1] == "uploads", parts[3] == kind,
              parts[2].allSatisfy(\.isNumber) else { return nil }
        return String(parts[2])
    }

    /// The request's body, whether the page sent it in one piece or as a stream (file uploads).
    static func body(of request: URLRequest) -> Data? {
        if let body = request.httpBody { return body }
        guard let stream = request.httpBodyStream else { return nil }
        stream.open()
        defer { stream.close() }
        var data = Data()
        let size = 64 * 1024
        let buffer = UnsafeMutablePointer<UInt8>.allocate(capacity: size)
        defer { buffer.deallocate() }
        while stream.hasBytesAvailable {
            let read = stream.read(buffer, maxLength: size)
            if read <= 0 { break }
            data.append(buffer, count: read)
        }
        return data
    }
}

/// Files saved on this phone (the app, thumbnails) in Application Support, which iOS doesn't clear
/// by itself the way it can clear caches.
final class DiskCache {
    struct Entry { let data: Data; let contentType: String? }
    private let folder: URL

    init() {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        folder = base.appendingPathComponent("NEXUS/saved", isDirectory: true)
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        var url = folder
        try? url.setResourceValues(values)
    }

    private func file(_ key: String) -> URL {
        let digest = SHA256.hash(data: Data(key.utf8)).map { String(format: "%02x", $0) }.joined()
        let prefix = key.split(separator: ":").first.map(String.init) ?? "x"
        return folder.appendingPathComponent("\(prefix)-\(digest)")
    }

    func put(_ key: String, data: Data, contentType: String?) {
        let url = file(key)
        try? data.write(to: url, options: .atomic)
        let meta = ["key": key, "type": contentType ?? "application/octet-stream"]
        try? JSONSerialization.data(withJSONObject: meta).write(to: url.appendingPathExtension("meta"), options: .atomic)
    }

    func get(_ key: String) -> Entry? {
        let url = file(key)
        guard let data = try? Data(contentsOf: url) else { return nil }
        let meta = (try? Data(contentsOf: url.appendingPathExtension("meta")))
            .flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: String] }
        return Entry(data: data, contentType: meta?["type"])
    }

    /// How many entries of one kind are saved ("thumb:" …).
    func count(prefix: String) -> Int {
        files(kind: prefix).count
    }

    /// Keep only the newest `keep` entries of one kind.
    func trim(prefix: String, keep: Int) {
        let all = files(kind: prefix)
        guard all.count > keep + 50 else { return }  // trim in batches, not on every save
        let dated = all.map { url in
            (url, (try? url.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast)
        }
        for (url, _) in dated.sorted(by: { $0.1 > $1.1 }).dropFirst(keep) {
            try? FileManager.default.removeItem(at: url)
            try? FileManager.default.removeItem(at: url.appendingPathExtension("meta"))
        }
    }

    private func files(kind prefix: String) -> [URL] {
        let kind = (prefix.split(separator: ":").first.map(String.init) ?? prefix) + "-"
        let all = (try? FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: [.contentModificationDateKey])) ?? []
        return all.filter { $0.lastPathComponent.hasPrefix(kind) && $0.pathExtension != "meta" }
    }
}
