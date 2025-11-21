import Cocoa

@main
struct LoginItemHelper {
    static func main() {
        NSApplication.shared.setActivationPolicy(.prohibited)

        // Locate the main app four levels up: Helper.app/Contents/MacOS -> LoginItems -> Contents -> App
        let appURL = Bundle.main.bundleURL
            .deletingLastPathComponent() // MacOS
            .deletingLastPathComponent() // Contents
            .deletingLastPathComponent() // Helper.app
            .deletingLastPathComponent() // LoginItems

        let configuration = NSWorkspace.OpenConfiguration()
        configuration.activates = false

        NSWorkspace.shared.openApplication(at: appURL, configuration: configuration) { app, error in
            if let error {
                NSLog("LoginItemHelper failed to launch main app: \(error.localizedDescription)")
            } else {
                NSLog("LoginItemHelper launched main app: \(app?.bundleIdentifier ?? "unknown")")
            }
            NSApp.terminate(nil)
        }

        NSApp.run()
    }
}
