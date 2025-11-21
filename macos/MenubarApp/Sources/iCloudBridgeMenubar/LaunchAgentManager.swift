import Cocoa
import ServiceManagement

enum LaunchAgentError: LocalizedError {
    case bundlePathMissing
    case backendMissing
    case loginHelperMissing
    case processFailed(message: String)

    var errorDescription: String? {
        switch self {
        case .bundlePathMissing:
            return "Could not locate the menubar bundle path."
        case .backendMissing:
            return "Backend executable is missing from the app bundle."
        case .loginHelperMissing:
            return "Login helper is missing from the app bundle."
        case .processFailed(let message):
            return message
        }
    }
}

final class LaunchAgentManager {
    private let label = "com.icloudbridge.server"
    private let fm = FileManager.default
    private let loginHelperIdentifier = "app.icloudbridge.loginhelper"

    private var currentUserIdentifier: String {
        let uid = getuid()
        return "gui/\(uid)"
    }

    func isInstalled() -> Bool {
        guard #available(macOS 13.0, *) else { return false }
        let service = SMAppService.loginItem(identifier: loginHelperIdentifier)
        return service.status == .enabled
    }

    func install() throws {
        guard #available(macOS 13.0, *) else {
            throw LaunchAgentError.processFailed(message: "Start at Login requires macOS 13 or later.")
        }
        try installWithSMAppService()
    }

    func remove() throws {
        guard #available(macOS 13.0, *) else { return }
        try removeWithSMAppService()
    }

    @discardableResult
    private func runLaunchctl(arguments: [String]) throws -> Int32 {
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        task.arguments = arguments

        let pipe = Pipe()
        task.standardError = pipe
        task.standardOutput = pipe

        try task.run()
        task.waitUntilExit()

        if task.terminationStatus != 0 {
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            let output = String(data: data, encoding: .utf8) ?? "Unknown error"
            throw LaunchAgentError.processFailed(message: output.trimmingCharacters(in: .whitespacesAndNewlines))
        }

        return task.terminationStatus
    }

    private func backendExecutablePath() -> URL {
        guard let resources = Bundle.main.resourceURL else {
            return URL(fileURLWithPath: "/tmp/icloudbridge-backend")
        }

        // Prefer the top-level packaged backend (matches BackendProcessManager)
        let primary = resources.appendingPathComponent("icloudbridge-backend")
        if FileManager.default.isExecutableFile(atPath: primary.path) {
            return primary
        }

        // Fallback to legacy backend/ path
        let legacy = resources.appendingPathComponent("backend/icloudbridge-backend")
        if FileManager.default.isExecutableFile(atPath: legacy.path) {
            return legacy
        }

        // Development/testing escape hatch
        return URL(fileURLWithPath: "/tmp/icloudbridge-backend")
    }

    @available(macOS 13.0, *)
    private func installWithSMAppService() throws {
        let backendPath = backendExecutablePath()
        guard fm.isExecutableFile(atPath: backendPath.path) else {
            throw LaunchAgentError.backendMissing
        }

        // SMAppService requires the helper to be embedded at Contents/Library/LoginItems inside the running bundle.
        guard let appBundle = Bundle.main.bundleURL as URL?, appBundle.pathExtension == "app" else {
            throw LaunchAgentError.bundlePathMissing
        }
        let helperBundle = appBundle
            .appendingPathComponent("Contents")
            .appendingPathComponent("Library")
            .appendingPathComponent("LoginItems")
            .appendingPathComponent("iCloudBridgeLoginHelper.app")
        guard fm.fileExists(atPath: helperBundle.path) else {
            throw LaunchAgentError.loginHelperMissing
        }

        let service = SMAppService.loginItem(identifier: loginHelperIdentifier)
        try service.register()
    }

    @available(macOS 13.0, *)
    private func removeWithSMAppService() throws {
        guard let appBundle = Bundle.main.bundleURL as URL?, appBundle.pathExtension == "app" else {
            return
        }
        let helperBundle = appBundle
            .appendingPathComponent("Contents")
            .appendingPathComponent("Library")
            .appendingPathComponent("LoginItems")
            .appendingPathComponent("iCloudBridgeLoginHelper.app")
        let service = SMAppService.loginItem(identifier: loginHelperIdentifier)

        // Attempt SM removal, then force bootout of the helper label
        try? service.unregister()
        _ = try? runLaunchctl(arguments: ["bootout", "gui/\(getuid())/\(loginHelperIdentifier)"])

        if fm.fileExists(atPath: helperBundle.path) {
            // Best-effort cleanup of helper bundle remnants
            try? fm.removeItem(at: helperBundle)
        }
    }
}
