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
    case bundlerInstallFailed(String)
    case bundlerVersionUnknown

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
        case .bundlerInstallFailed(let msg):
            return "Could not install the pinned Bundler: \(msg)"
        case .bundlerVersionUnknown:
            return "Gemfile.lock has no BUNDLED WITH version"
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

    /// Managed Ruby runtime root.
    ///
    /// Deliberately NOT under "Application Support": `bundle exec` hands the
    /// child Ruby its own `RUBYOPT=-r<abs path>/bundler/setup`, and Ruby splits
    /// that value on whitespace. A space anywhere in the Bundler path makes the
    /// remainder parse as switches - "Application Support/..." surfaces as
    /// `invalid switch in RUBYOPT: -S` before any script runs.
    private let rubyRuntimeBase: URL = {
        let url = URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent(RubyRuntime.relativeRoot, isDirectory: true)
        try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }()

    var pythonState = RuntimeInstallState(progress: 0, message: "Pending", isRunning: false, succeeded: false)
    var rubyState = RuntimeInstallState(progress: 0, message: "Pending", isRunning: false, succeeded: false)

    // `pythonState`/`rubyState` are published to the UI on the main thread, so
    // they flip to isRunning only *after* the work has already been queued.
    // Callers poll those flags to decide whether to start an install, and would
    // otherwise queue the same install several times before the first one is
    // visibly running - each redundant pass resetting the progress message and
    // making a finished install look like it had started over.
    private let dispatchLock = NSLock()
    private var pythonQueued = false
    private var rubyQueued = false

    var onProgress: (() -> Void)?

    // MARK: Public API

    func ensurePython(from resources: URL) {
        dispatchLock.lock()
        if pythonQueued {
            dispatchLock.unlock()
            return
        }
        pythonQueued = true
        dispatchLock.unlock()

        queue.async { [weak self] in
            guard let self else { return }
            defer {
                self.dispatchLock.lock()
                self.pythonQueued = false
                self.dispatchLock.unlock()
            }
            self.installPythonIfNeeded(resources: resources)
        }
    }

    func ensureRuby(from resources: URL) {
        dispatchLock.lock()
        if rubyQueued {
            dispatchLock.unlock()
            return
        }
        rubyQueued = true
        dispatchLock.unlock()

        queue.async { [weak self] in
            guard let self else { return }
            defer {
                self.dispatchLock.lock()
                self.rubyQueued = false
                self.dispatchLock.unlock()
            }
            self.installRubyIfNeeded(resources: resources)
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
        let pyproject = resources.appendingPathComponent("pyproject.toml")
        let cacheKey = requirementsFingerprint(
            requirements,
            pyproject: pyproject,
            pythonVersion: pyVersion,
            interpreter: resolvedInterpreter(python)
        )
        let marker = venvDir.appendingPathComponent(".fingerprint")

        // Rebuild whenever the recorded interpreter has been removed, even if the
        // fingerprint still matches - venvs built before fingerprints tracked the
        // interpreter path would otherwise stay broken forever.
        let stale = venvIsStale()
        if stale {
            NSLog("Rebuilding venv: its base interpreter is no longer installed")
        }

        if !stale,
           fm.fileExists(atPath: venvPython.path),
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

        guard let bundlerVersion = RubyRuntime.bundledWithVersion(lockfile: gemlock) else {
            updateRuby(message: RuntimeInstallerError.bundlerVersionUnknown.localizedDescription, running: false, succeeded: false)
            return
        }

        let gemHome = RubyRuntime.gemHome
        let binDir = RubyRuntime.binDir
        let bundleExe = RubyRuntime.bundleExecutable
        let marker = RubyRuntime.fingerprintFile

        let rubyVersion = Shell.run(brewRuby.path, ["--version"]).output.trimmingCharacters(in: .whitespacesAndNewlines)
        let cacheKey = rubyRuntimeFingerprint(
            lockfile: gemlock,
            rubyVersion: rubyVersion,
            interpreter: resolvedInterpreter(brewRuby),
            bundlerVersion: bundlerVersion
        )

        if fm.isExecutableFile(atPath: bundleExe.path),
           let existing = try? String(contentsOf: marker), existing == cacheKey {
            // Retry the legacy cleanup here too: the tree is only removed once
            // the new runtime works, and that removal can fail transiently.
            removeLegacyGemTree(log: logFile(named: "ruby-install.log"))
            updateRuby(progress: 1.0, message: "Ruby bundle ready", running: false, succeeded: true)
            return
        }

        let rubyLog = logFile(named: "ruby-install.log")
        resetLog(at: rubyLog)

        // The whole point of the v2 layout. Assert it rather than trusting it -
        // a future refactor that moves the root back under a path with a space
        // would otherwise reintroduce the RUBYOPT failure silently.
        guard RubyRuntime.isWhitespaceFree(rubyRuntimeBase) else {
            let msg = "Ruby runtime path contains whitespace: \(rubyRuntimeBase.path)"
            appendLog(msg, to: rubyLog)
            updateRuby(message: msg, running: false, succeeded: false, log: rubyLog)
            return
        }

        // Wipe only when the tree was built somewhere else or against a
        // different interpreter. Gem installs carry wrappers and cached paths
        // tied to where they were built, so those cases need a clean rebuild -
        // but a smoke-test failure leaves a perfectly good tree behind, and
        // deleting tens of thousands of files just to reinstall the same gems
        // turns every retry into a multi-minute native rebuild.
        let layoutKey = [RubyRuntime.schemaVersion, rubyRuntimeBase.path, resolvedInterpreter(brewRuby)]
            .joined(separator: "|")
        let existingLayout = try? String(contentsOf: RubyRuntime.layoutFile)
        if existingLayout != layoutKey, fm.fileExists(atPath: gemHome.path) {
            updateRuby(progress: 0.1, message: "Cleaning previous Ruby runtime", running: true, succeeded: false)
            appendLog("layout changed; rebuilding gem tree from scratch\n", to: rubyLog)
            try? fm.removeItem(at: gemHome)
            try? fm.removeItem(at: binDir)
        }
        try? fm.removeItem(at: marker)
        try? fm.removeItem(at: RubyRuntime.bundlerVersionFile)
        try? fm.createDirectory(at: gemHome, withIntermediateDirectories: true)
        try? fm.createDirectory(at: binDir, withIntermediateDirectories: true)
        try? fm.createDirectory(at: RubyRuntime.bundleConfig, withIntermediateDirectories: true)
        try? layoutKey.write(to: RubyRuntime.layoutFile, atomically: true, encoding: .utf8)

        var env = RubyRuntime.environment()
        env["BUNDLE_DEPLOYMENT"] = "true"

        updateRuby(progress: 0.25, message: "Installing Bundler \(bundlerVersion)", running: true, succeeded: false)
        // Address `gem` directly rather than via `ruby -S`, which searches PATH -
        // and a GUI-launched app's PATH need not contain Homebrew's Ruby.
        let brewGem = brewRuby.deletingLastPathComponent().appendingPathComponent("gem")
        let bundlerResult = Shell.run(
            brewGem.path,
            ["install", "bundler",
             "-v", bundlerVersion,
             "--no-document",
             "--install-dir", gemHome.path,
             "--bindir", binDir.path],
            environment: env
        )
        appendLog(bundlerResult.output, to: rubyLog)
        guard bundlerResult.status == 0, fm.isExecutableFile(atPath: bundleExe.path) else {
            updateRuby(message: RuntimeInstallerError.bundlerInstallFailed(bundlerResult.output).localizedDescription, running: false, succeeded: false, log: rubyLog)
            return
        }

        updateRuby(progress: 0.5, message: "Installing Ruby gems (this can take a few minutes)", running: true, succeeded: false)
        let result = Shell.run(
            bundleExe.path,
            ["_\(bundlerVersion)_", "install", "--gemfile", gemfile.path],
            environment: env
        )
        appendLog(result.output, to: rubyLog)
        guard result.status == 0 else {
            updateRuby(message: RuntimeInstallerError.bundleFailed(result.output).localizedDescription, running: false, succeeded: false, log: rubyLog)
            return
        }

        try? bundlerVersion.write(to: RubyRuntime.bundlerVersionFile, atomically: true, encoding: .utf8)
        try? cacheKey.write(to: marker, atomically: true, encoding: .utf8)
        removeLegacyGemTree(log: rubyLog)
        updateRuby(progress: 1.0, message: "Ruby bundle ready", running: false, succeeded: true, log: rubyLog)
    }

    /// Drop the pre-v2 gem tree once the new runtime has proven itself.
    private func removeLegacyGemTree(log: URL) {
        let legacy = RubyRuntime.legacyGemHome
        guard fm.fileExists(atPath: legacy.path) else { return }
        do {
            try fm.removeItem(at: legacy)
            appendLog("removed legacy gem tree at \(legacy.path)\n", to: log)
        } catch {
            // Non-fatal: the new runtime is already live and in use.
            appendLog("could not remove legacy gem tree: \(error.localizedDescription)\n", to: log)
        }
    }

    private func summarise(_ output: String) -> String {
        let trimmed = output.trimmingCharacters(in: .whitespacesAndNewlines)
        let lines = trimmed.split(whereSeparator: { $0.isNewline })
        return lines.suffix(5).joined(separator: "\n")
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

    private func requirementsFingerprint(_ url: URL, pyproject: URL, pythonVersion: String, interpreter: String) -> String {
        let reqContents = (try? String(contentsOf: url)) ?? ""
        let pyprojectContents = (try? String(contentsOf: pyproject)) ?? ""
        // `interpreter` is the fully resolved binary, not the /opt/homebrew/opt
        // symlink. Homebrew revision bumps (3.12.13_2 -> 3.12.13_4) leave
        // `python --version` unchanged but move the binary, which is what macOS
        // keys TCC permissions on - so the version string alone would let a venv
        // survive an upgrade that silently stripped its permissions.
        return pythonVersion + "|" + interpreter + "|" + reqContents + "|" + pyprojectContents
    }

    /// Fingerprint for the managed Ruby runtime.
    ///
    /// Wider than the lockfile alone: a `brew upgrade` can move the Ruby binary
    /// without changing `ruby --version`, and the gem tree is built against a
    /// specific interpreter. The schema version forces the rebuild that moves
    /// existing installs off the old Application Support path.
    private func rubyRuntimeFingerprint(lockfile: URL, rubyVersion: String, interpreter: String, bundlerVersion: String) -> String {
        let lockContents = (try? String(contentsOf: lockfile)) ?? ""
        return [
            RubyRuntime.schemaVersion,
            rubyRuntimeBase.path,
            interpreter,
            rubyVersion,
            bundlerVersion,
            lockContents
        ].joined(separator: "|")
    }

    /// The real path of the interpreter behind a Homebrew symlink.
    private func resolvedInterpreter(_ python: URL) -> String {
        python.resolvingSymlinksInPath().path
    }

    /// The base interpreter recorded in an existing venv's pyvenv.cfg.
    private func venvBaseInterpreter(_ venvDir: URL) -> String? {
        let cfg = venvDir.appendingPathComponent("pyvenv.cfg")
        guard let contents = try? String(contentsOf: cfg) else { return nil }

        for line in contents.split(whereSeparator: { $0.isNewline }) {
            let parts = line.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
            guard parts.count == 2, parts[0].trimmingCharacters(in: .whitespaces) == "executable" else { continue }
            return parts[1].trimmingCharacters(in: .whitespaces)
        }
        return nil
    }

    /// True when the venv points at an interpreter that is no longer installed.
    ///
    /// A venv in this state still runs - the symlinks re-resolve to whatever
    /// Homebrew has now - but it runs from a binary macOS never granted
    /// permissions to, so Reminders and protected folders silently read empty.
    func venvIsStale() -> Bool {
        let venvDir = appSupportBase.appendingPathComponent("venv", isDirectory: true)
        guard fm.fileExists(atPath: venvDir.appendingPathComponent("bin/python3").path) else {
            return false  // No venv yet; nothing stale about that.
        }
        guard let recorded = venvBaseInterpreter(venvDir) else { return false }
        return !fm.fileExists(atPath: recorded)
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
