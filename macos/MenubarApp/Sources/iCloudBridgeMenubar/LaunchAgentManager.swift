import Cocoa

enum LaunchAgentError: LocalizedError {
    case bundlePathMissing
    case backendMissing
    case processFailed(message: String)

    var errorDescription: String? {
        switch self {
        case .bundlePathMissing:
            return "Could not locate the menubar bundle path."
        case .backendMissing:
            return "Backend executable is missing from the app bundle."
        case .processFailed(let message):
            return message
        }
    }
}

final class LaunchAgentManager {
    private let label = "com.icloudbridge.server"
    private let fm = FileManager.default

    private var currentUserIdentifier: String {
        let uid = getuid()
        return "gui/\(uid)"
    }

    private var agentsDirectory: URL {
        fm.homeDirectoryForCurrentUser
            .appendingPathComponent("Library")
            .appendingPathComponent("LaunchAgents")
    }

    private var agentPlistURL: URL {
        agentsDirectory.appendingPathComponent("\(label).plist")
    }

    private func menubarExecutablePath() -> String? {
        guard
            let bundlePath = Bundle.main.bundlePath as String?,
            let executableName = Bundle.main.infoDictionary?["CFBundleExecutable"] as? String
        else {
            return nil
        }
        return "\(bundlePath)/Contents/MacOS/\(executableName)"
    }

    func isInstalled() -> Bool {
        let dataExists = fm.fileExists(atPath: agentPlistURL.path)
        guard dataExists else { return false }

        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        task.arguments = ["print", "\(currentUserIdentifier)/\(label)"]
        do {
            try task.run()
            task.waitUntilExit()
            return task.terminationStatus == 0
        } catch {
            NSLog("launchctl print failed: \(error.localizedDescription)")
            return false
        }
    }

    func install() throws {
        guard let menubarPath = menubarExecutablePath() else {
            throw LaunchAgentError.bundlePathMissing
        }

        let backendPath = backendExecutablePath()
        guard fm.isExecutableFile(atPath: backendPath.path) else {
            throw LaunchAgentError.backendMissing
        }

        try fm.createDirectory(at: agentsDirectory, withIntermediateDirectories: true)

        let plist: [String: Any] = [
            "Label": label,
            "ProgramArguments": [menubarPath],
            "RunAtLoad": true,
            "ProcessType": "Interactive",
            "KeepAlive": ["SuccessfulExit": false],
            "EnvironmentVariables": [
                "ICLOUDBRIDGE_LOG_ROOT": "\(NSHomeDirectory())/Library/Logs/iCloudBridge",
                "ICLOUDBRIDGE_SERVER_HOST": "127.0.0.1",
                "ICLOUDBRIDGE_SERVER_PORT": "27731",
            ],
        ]

        let data = try PropertyListSerialization.data(fromPropertyList: plist, format: .xml, options: 0)
        try data.write(to: agentPlistURL, options: .atomic)

        let bundleID = Bundle.main.bundleIdentifier ?? label
        let alreadyRunning = !NSRunningApplication.runningApplications(withBundleIdentifier: bundleID).isEmpty

        if alreadyRunning {
            // Avoid spawning a second instance when enabling login item from a running app
            _ = try? runLaunchctl(arguments: ["enable", "\(currentUserIdentifier)/\(label)"])
        } else {
            try runLaunchctl(arguments: ["bootstrap", currentUserIdentifier, agentPlistURL.path])
        }
    }

    func remove() throws {
        _ = try? runLaunchctl(arguments: ["bootout", "\(currentUserIdentifier)/\(label)"])
        if fm.fileExists(atPath: agentPlistURL.path) {
            try fm.removeItem(at: agentPlistURL)
        }
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
}
