import SwiftUI
import UIKit
import WebKit

/// NEXUS itself, in a web view that loads through `NexusSchemeHandler`.
struct NexusView: UIViewRepresentable {
    let server: URL
    let onChangeServer: () -> Void

    func makeCoordinator() -> Coordinator { Coordinator(server: server, onChangeServer: onChangeServer) }

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.setURLSchemeHandler(context.coordinator.handler, forURLScheme: NexusSchemeHandler.scheme)
        config.allowsInlineMediaPlayback = true
        config.mediaTypesRequiringUserActionForPlayback = []
        config.websiteDataStore = .default()  // the saved copy (IndexedDB) stays between launches
        let version = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? ""
        let script = "window.NEXUS_APP = { platform: 'ios', version: '\(version)' };"
        config.userContentController.addUserScript(WKUserScript(source: script, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        config.userContentController.add(WeakMessageHandler(context.coordinator), name: "nexus")

        let web = WKWebView(frame: .zero, configuration: config)
        web.navigationDelegate = context.coordinator
        web.uiDelegate = context.coordinator
        web.allowsBackForwardNavigationGestures = true
        web.scrollView.contentInsetAdjustmentBehavior = .automatic
        if #available(iOS 16.4, *) { web.isInspectable = true }  // Safari → Develop, for troubleshooting
        let refresh = UIRefreshControl()  // pull down: refresh the saved copy and this page
        refresh.addTarget(context.coordinator, action: #selector(Coordinator.pulled(_:)), for: .valueChanged)
        web.scrollView.refreshControl = refresh
        context.coordinator.web = web
        web.load(URLRequest(url: NexusSchemeHandler.origin))
        return web
    }

    func updateUIView(_ web: WKWebView, context: Context) {}

    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler, WKDownloadDelegate {
        let handler: NexusSchemeHandler
        let onChangeServer: () -> Void
        weak var web: WKWebView?

        @objc func pulled(_ control: UIRefreshControl) {
            let script = "try { syncOfflineCopy({ force: true }); render(); } catch (e) { location.reload(); }"
            web?.evaluateJavaScript(script, completionHandler: nil)
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) { control.endRefreshing() }
        }
        private var downloads: [ObjectIdentifier: URL] = [:]

        init(server: URL, onChangeServer: @escaping () -> Void) {
            handler = NexusSchemeHandler(server: server)
            self.onChangeServer = onChangeServer
        }

        // Messages from the page: window.webkit.messageHandlers.nexus.postMessage({action: …})
        func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
            guard let body = message.body as? [String: Any], let action = body["action"] as? String else { return }
            if action == "changeServer" { onChangeServer() }
        }

        // Links to other websites (Tailscale, Ollama …) open in Safari; files are saved or shared.
        func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                     decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            if action.shouldPerformDownload {
                return decisionHandler(.download)
            }
            if let url = action.request.url, let scheme = url.scheme?.lowercased(),
               ["http", "https", "mailto", "tel"].contains(scheme) {
                UIApplication.shared.open(url)
                return decisionHandler(.cancel)
            }
            decisionHandler(.allow)
        }

        func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse,
                     decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
            decisionHandler(response.canShowMIMEType ? .allow : .download)
        }

        func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
            download.delegate = self
        }

        func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) {
            download.delegate = self
        }

        func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String,
                      completionHandler: @escaping (URL?) -> Void) {
            let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
            try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            let url = folder.appendingPathComponent(suggestedFilename.isEmpty ? "NEXUS download" : suggestedFilename)
            downloads[ObjectIdentifier(download)] = url
            completionHandler(url)
        }

        func downloadDidFinish(_ download: WKDownload) {
            guard let url = downloads.removeValue(forKey: ObjectIdentifier(download)) else { return }
            present(UIActivityViewController(activityItems: [url], applicationActivities: nil))
        }

        func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
            downloads.removeValue(forKey: ObjectIdentifier(download))
            alert("The download didn't finish: \(error.localizedDescription)")
        }

        // target=_blank links
        func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                     for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
            if let url = action.request.url { UIApplication.shared.open(url) }
            return nil
        }

        // alert(), confirm() and prompt() from the page, as native dialogs.
        func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                     initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
            let dialog = UIAlertController(title: nil, message: message, preferredStyle: .alert)
            dialog.addAction(UIAlertAction(title: "OK", style: .default) { _ in completionHandler() })
            present(dialog, orElse: completionHandler)
        }

        func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                     initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
            let dialog = UIAlertController(title: nil, message: message, preferredStyle: .alert)
            dialog.addAction(UIAlertAction(title: "Cancel", style: .cancel) { _ in completionHandler(false) })
            dialog.addAction(UIAlertAction(title: "OK", style: .default) { _ in completionHandler(true) })
            present(dialog, orElse: { completionHandler(false) })
        }

        func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String, defaultText: String?,
                     initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (String?) -> Void) {
            let dialog = UIAlertController(title: nil, message: prompt, preferredStyle: .alert)
            dialog.addTextField { $0.text = defaultText }
            dialog.addAction(UIAlertAction(title: "Cancel", style: .cancel) { _ in completionHandler(nil) })
            dialog.addAction(UIAlertAction(title: "OK", style: .default) { _ in completionHandler(dialog.textFields?.first?.text ?? "") })
            present(dialog, orElse: { completionHandler(nil) })
        }

        // Recording a voice note: ask the person (iOS shows its own microphone prompt too).
        @available(iOS 15.0, *)
        func webView(_ webView: WKWebView, requestMediaCapturePermissionFor origin: WKSecurityOrigin,
                     initiatedByFrame frame: WKFrameInfo, type: WKMediaCaptureType,
                     decisionHandler: @escaping (WKPermissionDecision) -> Void) {
            decisionHandler(.grant)
        }

        // Self-test for the automated build (launch argument -nexus-selftest): send a typed capture and a
        // file through the app, then write what the page shows to Documents/selftest-N.txt.
        private var pagesLoaded = 0
        static let selfTesting = ProcessInfo.processInfo.arguments.contains("-nexus-selftest")

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            guard Self.selfTesting else { return }
            pagesLoaded += 1
            let n = pagesLoaded
            DispatchQueue.main.asyncAfter(deadline: .now() + 4) {
                webView.evaluateJavaScript(Self.selfTestScript, completionHandler: nil)
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 12) {
                webView.evaluateJavaScript("document.body.innerText") { text, _ in
                    let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
                    try? ((text as? String) ?? "").write(to: docs.appendingPathComponent("selftest-\(n).txt"), atomically: true, encoding: .utf8)
                }
            }
        }

        static let selfTestScript = """
        (async () => {
          const report = { offlineCopy: typeof HAS_OFFLINE_COPY !== 'undefined' && HAS_OFFLINE_COPY,
            secure: window.isSecureContext, mic: !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia),
            recorder: typeof MediaRecorder !== 'undefined', cacheApi: 'caches' in window, idb: 'indexedDB' in window };
          try {
            const r = await fetch('/api/entries', { method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ original_text: 'iOS self-test: typed capture', analyze: false }) });
            report.typed = r.status;
            const fd = new FormData();
            fd.append('file', new Blob(['iOS self-test file'], { type: 'text/plain' }), 'ios-selftest.txt');
            fd.append('analyze', 'false');
            report.upload = (await apiNetwork('/api/uploads', { method: 'POST', body: fd })).id ? 201 : 0;  // as the app sends files
            await fetch('/api/entries', { method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ original_text: 'iOS self-test report ' + JSON.stringify(report), analyze: false }) });
          } catch (e) { /* the computer is off in the second half of the test */ }
        })();
        """

        // If iOS ends the web view's process (low memory), reload rather than show a blank page.
        func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
            webView.load(URLRequest(url: NexusSchemeHandler.origin))
        }

        private func alert(_ message: String) {
            let dialog = UIAlertController(title: nil, message: message, preferredStyle: .alert)
            dialog.addAction(UIAlertAction(title: "OK", style: .default))
            present(dialog)
        }

        private func present(_ controller: UIViewController, orElse fallback: (() -> Void)? = nil) {
            guard var top = UIApplication.shared.connectedScenes.compactMap({ ($0 as? UIWindowScene)?.keyWindow }).first?.rootViewController else {
                fallback?()
                return
            }
            while let next = top.presentedViewController { top = next }
            if let popover = controller.popoverPresentationController {  // iPad
                popover.sourceView = top.view
                popover.sourceRect = CGRect(x: top.view.bounds.midX, y: top.view.bounds.midY, width: 0, height: 0)
            }
            top.present(controller, animated: true)
        }
    }
}

/// Lets the page talk to the app without the web view keeping the coordinator alive forever.
final class WeakMessageHandler: NSObject, WKScriptMessageHandler {
    weak var target: WKScriptMessageHandler?
    init(_ target: WKScriptMessageHandler) { self.target = target }
    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        target?.userContentController(controller, didReceive: message)
    }
}
