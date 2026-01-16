import Foundation

struct RuntimeInstallState {
    var progress: Double
    var message: String
    var isRunning: Bool
    var succeeded: Bool
    var logURL: URL?
}

enum RuntimeInstallerError: Error, LocalizedError {
    case brewPythonMissing
    case brewRubyMissing
    case requirementsMissing
    case gemfileMissing
    case pipFailed(String)
    case bundleFailed(String)

    var errorDescription: String? {
        switch self {
        case .brewPythonMissing:
            return "python@3.12 not found at the expected Homebrew path"
        case .brewRubyMissing:
            return "Ruby not found at the expected Homebrew path"
        case .requirementsMissing:
            return "requirements.lock is missing from app resources"
        case .gemfileMissing:
            return "Gemfile/Gemfile.lock missing from app resources"
        case .pipFailed(let msg):
            return "pip install failed: \(msg)"
        case .bundleFailed(let msg):
            return "bundle install failed: \(msg)"
        }
    }
}

final class RuntimeInstaller {
    private let fm = FileManager.default
    private let queue = DispatchQueue(label: "app.icloudbridge.runtimeinstaller")

    private let brewPythonDir = URL(fileURLWithPath: "/opt/homebrew/opt/python@3.12/bin", isDirectory: true)
    private let brewRuby = URL(fileURLWithPath: "/opt/homebrew/opt/ruby/bin/ruby")

    private let appSupportBase: URL = {
        let url = URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent("Library/Application Support/iCloudBridge", isDirectory: true)
        try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }()

    var pythonState = RuntimeInstallState(progress: 0, message: "Pending", isRunning: false, succeeded: false)
    var rubyState = RuntimeInstallState(progress: 0, message: "Pending", isRunning: false, succeeded: false)

    var onProgress: (() -> Void)?

    // MARK: Public API

    func ensurePython(from resources: URL) {
        queue.async { [weak self] in
            self?.installPythonIfNeeded(resources: resources)
        }
    }

    func ensureRuby(from resources: URL) {
        queue.async { [weak self] in
            self?.installRubyIfNeeded(resources: resources)
        }
    }

    // MARK: Internals

    private func installPythonIfNeeded(resources: URL) {
        updatePython(progress: 0.05, message: "Preparing Python venv", running: true, succeeded: false)
        guard let python = locateBrewPython() else {
            updatePython(message: RuntimeInstallerError.brewPythonMissing.localizedDescription, running: false, succeeded: false)
            return
        }

        let requirements = resources.appendingPathComponent("requirements.lock")
        guard fm.fileExists(atPath: requirements.path) else {
            updatePython(message: RuntimeInstallerError.requirementsMissing.localizedDescription, running: false, succeeded: false)
            return
        }

        let venvDir = appSupportBase.appendingPathComponent("venv", isDirectory: true)
        let venvPython = venvDir.appendingPathComponent("bin/python3")
        let pyVersion = Shell.run(python.path, ["--version"]).output.trimmingCharacters(in: .whitespacesAndNewlines)
        let cacheKey = requirementsFingerprint(requirements, pythonVersion: pyVersion)
        let marker = venvDir.appendingPathComponent(".fingerprint")

        if fm.fileExists(atPath: venvPython.path),
           let existing = try? String(contentsOf: marker), existing == cacheKey {
            updatePython(progress: 1.0, message: "Python venv ready", running: false, succeeded: true)
            return
        }

        try? fm.removeItem(at: venvDir)
        try? fm.createDirectory(at: venvDir, withIntermediateDirectories: true)

        let pythonLog = logFile(named: "python-install.log")
        resetLog(at: pythonLog)

        let venvResult = Shell.run(python.path, ["-m", "venv", venvDir.path])
        appendLog(venvResult.output, to: pythonLog)
        guard venvResult.status == 0 else {
            updatePython(message: "venv creation failed: \(venvResult.output)", running: false, succeeded: false, log: pythonLog)
            return
        }

        updatePython(progress: 0.4, message: "Installing Python deps", running: true, succeeded: false)
        let pip = venvDir.appendingPathComponent("bin/pip")
        let install = Shell.run(pip.path, ["install", "--upgrade", "--require-hashes", "-r", requirements.path])
        appendLog(install.output, to: pythonLog)
        guard install.status == 0 else {
            updatePython(message: RuntimeInstallerError.pipFailed(install.output).localizedDescription, running: false, succeeded: false, log: pythonLog)
            return
        }

        updatePython(progress: 0.7, message: "Installing backend package", running: true, succeeded: false)
        let pkgResult = Shell.run(pip.path, ["install", resources.path])
        appendLog(pkgResult.output, to: pythonLog)
        guard pkgResult.status == 0 else {
            updatePython(message: RuntimeInstallerError.pipFailed(pkgResult.output).localizedDescription, running: false, succeeded: false, log: pythonLog)
            return
        }

        try? cacheKey.write(to: marker, atomically: true, encoding: .utf8)
        updatePython(progress: 1.0, message: "Python venv ready", running: false, succeeded: true, log: pythonLog)
    }

    private func installRubyIfNeeded(resources: URL) {
        updateRuby(progress: 0.05, message: "Preparing Ruby gems", running: true, succeeded: false)
        guard fm.isExecutableFile(atPath: brewRuby.path) else {
            updateRuby(message: RuntimeInstallerError.brewRubyMissing.localizedDescription, running: false, succeeded: false)
            return
        }

        let gemfile = resources.appendingPathComponent("Gemfile")
        let gemlock = resources.appendingPathComponent("Gemfile.lock")
        guard fm.fileExists(atPath: gemfile.path), fm.fileExists(atPath: gemlock.path) else {
            updateRuby(message: RuntimeInstallerError.gemfileMissing.localizedDescription, running: false, succeeded: false)
            return
        }

        let gemHome = appSupportBase.appendingPathComponent("gems", isDirectory: true)
        let marker = gemHome.appendingPathComponent(".fingerprint")
        let cacheKey = (try? String(contentsOf: gemlock)) ?? ""

        if fm.fileExists(atPath: marker.path), let existing = try? String(contentsOf: marker), existing == cacheKey {
            updateRuby(progress: 1.0, message: "Ruby bundle ready", running: false, succeeded: true)
            return
        }

        try? fm.createDirectory(at: gemHome, withIntermediateDirectories: true)
        let env = [
            "BUNDLE_APP_CONFIG": gemHome.appendingPathComponent(".bundle").path,
            "BUNDLE_PATH": gemHome.path,
            "BUNDLE_WITHOUT": "development test",
            "BUNDLE_DEPLOYMENT": "true"
        ]
        let rubyLog = logFile(named: "ruby-install.log")
        resetLog(at: rubyLog)
        updateRuby(progress: 0.4, message: "Installing Ruby gems", running: true, succeeded: false)
        let result = Shell.run(
            brewRuby.path,
            ["-S", "bundle", "install", "--gemfile", gemfile.path],
            environment: env
        )
        appendLog(result.output, to: rubyLog)
        guard result.status == 0 else {
            updateRuby(message: RuntimeInstallerError.bundleFailed(result.output).localizedDescription, running: false, succeeded: false, log: rubyLog)
            return
        }

        try? cacheKey.write(to: marker, atomically: true, encoding: .utf8)
        updateRuby(progress: 1.0, message: "Ruby bundle ready", running: false, succeeded: true, log: rubyLog)
    }

    private func updatePython(progress: Double? = nil, message: String, running: Bool, succeeded: Bool, log: URL? = nil) {
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            if let p = progress { self.pythonState.progress = p }
            self.pythonState.message = message
            self.pythonState.isRunning = running
            self.pythonState.succeeded = succeeded
            if let log { self.pythonState.logURL = log }
            self.onProgress?()
        }
    }

    private func updateRuby(progress: Double? = nil, message: String, running: Bool, succeeded: Bool, log: URL? = nil) {
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            if let p = progress { self.rubyState.progress = p }
            self.rubyState.message = message
            self.rubyState.isRunning = running
            self.rubyState.succeeded = succeeded
            if let log { self.rubyState.logURL = log }
            self.onProgress?()
        }
    }

    private func requirementsFingerprint(_ url: URL, pythonVersion: String) -> String {
        let contents = (try? String(contentsOf: url)) ?? ""
        return pythonVersion + "|" + contents
    }

    private func locateBrewPython() -> URL? {
        let primary = brewPythonDir.appendingPathComponent("python3")
        let fallback = brewPythonDir.appendingPathComponent("python3.12")
        if fm.isExecutableFile(atPath: primary.path) {
            return primary
        }
        if fm.isExecutableFile(atPath: fallback.path) {
            return fallback
        }
        return nil
    }

    private func logFile(named: String) -> URL {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("icloudbridge-preflight-logs", isDirectory: true)
        try? fm.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.appendingPathComponent(named)
    }

    private func resetLog(at url: URL) {
        try? "".write(to: url, atomically: true, encoding: .utf8)
    }

    private func appendLog(_ text: String, to url: URL) {
        guard !text.isEmpty else { return }
        if let handle = try? FileHandle(forWritingTo: url) {
            handle.seekToEndOfFile()
            if let data = text.data(using: .utf8) {
                handle.write(data)
            }
            try? handle.close()
        } else {
            try? text.write(to: url, atomically: true, encoding: .utf8)
        }
    }
}
