import UIKit

/// Home Screen quick actions: press and hold the NEXUS icon for "New idea" or "Search"
/// (listed in project.yml → UIApplicationShortcutItems).
enum QuickAction {
    static let screens = ["local.nexus.new": "#/", "local.nexus.search": "#/search"]
    /// A quick action chosen while NEXUS wasn't open yet: the web view starts on that screen.
    static var pending: String?

    static func handle(_ item: UIApplicationShortcutItem) {
        guard let screen = screens[item.type] else { return }
        pending = screen
        NotificationCenter.default.post(name: .nexusQuickAction, object: screen)
    }
}

extension Notification.Name {
    static let nexusQuickAction = Notification.Name("nexusQuickAction")
}

final class AppDelegate: NSObject, UIApplicationDelegate {
    func application(_ application: UIApplication, configurationForConnecting session: UISceneSession,
                     options: UIScene.ConnectionOptions) -> UISceneConfiguration {
        if let item = options.shortcutItem { QuickAction.handle(item) }  // NEXUS was closed
        let config = UISceneConfiguration(name: nil, sessionRole: session.role)
        config.delegateClass = SceneDelegate.self
        return config
    }
}

final class SceneDelegate: NSObject, UIWindowSceneDelegate {
    // NEXUS was already open in the background.
    func windowScene(_ windowScene: UIWindowScene, performActionFor shortcutItem: UIApplicationShortcutItem,
                     completionHandler: @escaping (Bool) -> Void) {
        QuickAction.handle(shortcutItem)
        completionHandler(true)
    }
}
