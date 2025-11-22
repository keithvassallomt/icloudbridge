import Cocoa

final class BackendProcessManager {
    private var process: Process?
    private let queue = DispatchQueue(label: "app.icloudbridge.backend")
    private var crashCount = 0
    private var lastCrashTime: Date?
    private let appSupportVenv = URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent("Library/Application Support/iCloudBridge/venv")
    private let appSupportGems = URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent("Library/Application Support/iCloudBridge/gems")

    private let healthURL = URL(string: "http://127.0.0.1:27731/api/health")!

    func start() {
        queue.async { [weak self] in
            guard let self else { return }
            if self.process?.isRunning == true {
                return
            }
            if self.backendHealthySync() {
                return
            }

            guard let launch = self.backendLaunchCommand() else { return }

            do {
                var environment = ProcessInfo.processInfo.environment
                if let resources = Bundle.main.resourceURL {
                    let publicDir = resources.appendingPathComponent("public")
                    environment["ICLOUDBRIDGE_FRONTEND_DIST"] = publicDir.path
                    environment["ICLOUDBRIDGE_LOG_ROOT"] = "\(NSHomeDirectory())/Library/Logs/iCloudBridge"
                    environment["ICLOUDBRIDGE_BACKEND_LOG_FILE"] = "\(NSHomeDirectory())/Library/Logs/iCloudBridge/backend.log"
                }
                let proc = Process()
                proc.executableURL = launch.executable
                proc.arguments = launch.arguments
                proc.environment = environment.merging(launch.environment) { _, new in new }
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
            killProcessesOnPort(27731)
        }
    }

    func killProcessesOnPort(_ port: Int) {
        let lsof = Process()
        lsof.executableURL = URL(fileURLWithPath: "/usr/sbin/lsof")
        lsof.arguments = ["-ti", "tcp:\(port)"]

        let pipe = Pipe()
        lsof.standardOutput = pipe
        lsof.standardError = nil

        do {
            try lsof.run()
            lsof.waitUntilExit()
        } catch {
            NSLog("Unable to run lsof for port kill: \(error.localizedDescription)")
            return
        }

        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        guard let output = String(data: data, encoding: .utf8) else { return }
        let pids = output
            .split(whereSeparator: { $0.isNewline })
            .compactMap { Int32($0.trimmingCharacters(in: .whitespacesAndNewlines)) }

        for pid in pids {
            let kill = Process()
            kill.executableURL = URL(fileURLWithPath: "/bin/kill")
            kill.arguments = ["-TERM", String(pid)]
            do {
                try kill.run()
            } catch {
                NSLog("Failed to kill pid \(pid) on port \(port): \(error.localizedDescription)")
            }
        }
    }

    func backendHealthy(completion: @escaping (Bool) -> Void) {
        var request = URLRequest(url: healthURL)
        request.timeoutInterval = 1.5
        let task = URLSession.shared.dataTask(with: request) { _, response, error in
            if error == nil, let http = response as? HTTPURLResponse, http.statusCode == 200 {
                completion(true)
            } else {
                completion(false)
            }
        }
        task.resume()
    }

    func backendHealthySync() -> Bool {
        var result = false
        let semaphore = DispatchSemaphore(value: 0)
        backendHealthy { healthy in
            result = healthy
            semaphore.signal()
        }
        _ = semaphore.wait(timeout: .now() + 2.0)
        return result
    }

    private func backendLaunchCommand() -> (executable: URL, arguments: [String], environment: [String: String])? {
        guard let resources = Bundle.main.resourceURL else {
            NSLog("Missing bundle resources path")
            return nil
        }
        let venvPython = appSupportVenv.appendingPathComponent("bin/python3")
        guard FileManager.default.isExecutableFile(atPath: venvPython.path) else {
            NSLog("Venv python not found at \(venvPython.path)")
            return nil
        }
        let backendSrc = resources.appendingPathComponent("backend_src", isDirectory: true)
        let env: [String: String] = [
            "PYTHONPATH": backendSrc.path,
            "BUNDLE_APP_CONFIG": appSupportGems.appendingPathComponent(".bundle").path,
            "BUNDLE_PATH": appSupportGems.path,
            "BUNDLE_WITHOUT": "development test",
            "ICLOUDBRIDGE_VENV_PYTHON": venvPython.path
        ]
        let args = ["-m", "icloudbridge.scripts.menubar_backend"]
        return (executable: venvPython, arguments: args, environment: env)
    }

}
