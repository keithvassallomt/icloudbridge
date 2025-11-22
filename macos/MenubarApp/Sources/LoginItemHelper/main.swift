import Cocoa

NSApplication.shared.setActivationPolicy(.prohibited)

// Locate the main app four levels up: Helper.app/Contents/MacOS -> LoginItems -> Contents -> App
let appURL = Bundle.main.bundleURL
    .deletingLastPathComponent() // MacOS
    .deletingLastPathComponent() // Contents
    .deletingLastPathComponent() // Helper.app
    .deletingLastPathComponent() // LoginItems

let configuration = NSWorkspace.OpenConfiguration()
configuration.activates = false

NSWorkspace.shared.openApplication(at: appURL, configuration: configuration, completionHandler: { app, error in
    if let error = error {
        let errorMessage = "LoginItemHelper failed to launch main app: " + error.localizedDescription
        NSLog(errorMessage)
    } else {
        let bundleID = app?.bundleIdentifier ?? "unknown"
        NSLog("LoginItemHelper launched main app: " + bundleID)
    }
    NSApp.terminate(nil)
})

NSApp.run()
