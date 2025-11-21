import Cocoa

final class BackendProcessManager {
    private var process: Process?
    private let queue = DispatchQueue(label: "app.icloudbridge.backend")
    private var crashCount = 0
    private var lastCrashTime: Date?

    func start() {
        queue.async { [weak self] in
            guard let self else { return }
            if self.process?.isRunning == true {
                return
            }
            guard let executableURL = self.backendExecutableURL() else {
                NSLog("Unable to locate packaged backend binary")
                return
            }

            do {
                var environment = ProcessInfo.processInfo.environment
                if let resources = Bundle.main.resourceURL {
                    let publicDir = resources.appendingPathComponent("public")
                    environment["ICLOUDBRIDGE_FRONTEND_DIST"] = publicDir.path
                    environment["ICLOUDBRIDGE_LOG_ROOT"] = "\(NSHomeDirectory())/Library/Logs/iCloudBridge"
                    environment["ICLOUDBRIDGE_BACKEND_LOG_FILE"] = "\(NSHomeDirectory())/Library/Logs/iCloudBridge/backend.log"
                }
                let proc = Process()
                proc.executableURL = executableURL
                proc.arguments = []
                proc.environment = environment
                proc.standardOutput = nil
                proc.standardError = nil
                proc.terminationHandler = { [weak self] process in
                    guard let self else { return }
                    self.handleTermination(status: process.terminationStatus)
                }
                try proc.run()
                self.process = proc
                self.resetCrashTracking()
            } catch {
                NSLog("Failed to launch backend: \(error.localizedDescription)")
            }
        }
    }

    private func handleTermination(status: Int32) {
        process = nil

        let now = Date()
        if let last = lastCrashTime, now.timeIntervalSince(last) < 5 {
            crashCount += 1
        } else {
            crashCount = 1
        }
        lastCrashTime = now

        if crashCount >= 3 {
            NSLog("Backend exited repeatedly (status \(status)); giving up to avoid rapid restart loop")
            return
        }

        NSLog("Backend exited with status \(status); restarting…")
        start()
    }

    private func resetCrashTracking() {
        crashCount = 0
        lastCrashTime = nil
    }

    func stop() {
        queue.sync {
            process?.terminate()
            process = nil
        }
    }

    private func backendExecutableURL() -> URL? {
        guard let bundleURL = Bundle.main.bundleURL as URL? else { return nil }
        let candidate = bundleURL
            .appendingPathComponent("Contents")
            .appendingPathComponent("Resources")
            .appendingPathComponent("icloudbridge-backend")
        return FileManager.default.isExecutableFile(atPath: candidate.path) ? candidate : nil
    }
}
