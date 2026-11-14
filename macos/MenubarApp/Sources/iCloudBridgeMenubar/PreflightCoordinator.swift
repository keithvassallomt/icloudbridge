import Cocoa

final class PreflightCoordinator {
    private let preflightManager = PreflightManager()
    private let runtimeInstaller = RuntimeInstaller()
    private weak var backendManager: BackendProcessManager?
    private var windowController: PreflightWindowController?
    private var backendStarted = false
    private var latestStatuses: [RequirementStatus] = []

    private let defaults = UserDefaults.standard
    private let shownKey = "preflight.hasShownOnce"
    private let suppressKey = "preflight.suppressWhenHealthy"

    private var interpreterWatchTimer: Timer?
    private var isRecoveringInterpreter = false
    private var lastInterpreterRecovery: Date?

    /// How often to ask the backend whether its interpreter is still valid.
    private let interpreterCheckInterval: TimeInterval = 10 * 60
    /// Floor between recovery attempts, so a rebuild that fails to help does not
    /// turn into a restart loop.
    private let interpreterRecoveryCooldown: TimeInterval = 30 * 60

    init(backendManager: BackendProcessManager) {
        self.backendManager = backendManager
        if defaults.object(forKey: suppressKey) == nil {
            defaults.set(true, forKey: suppressKey)
        }
        preflightManager.onEvent = { [weak self] event in
            self?.handle(event: event)
        }
        runtimeInstaller.onProgress = { [weak self] in
            self?.refreshRuntimeStatuses()
        }
    }

    deinit {
        interpreterWatchTimer?.invalidate()
    }

    func start() {
        preflightManager.runFullCheck()
        startInterpreterWatch()
    }

    // MARK: Interpreter recovery

    /// Watch for the backend's interpreter being replaced underneath it.
    ///
    /// A Homebrew Python upgrade deletes the Cellar directory the backend is
    /// executing from. macOS then revokes the process's Reminders, Photos and
    /// file access without any error: syncs keep running and simply see nothing.
    /// Rebuilding the venv and restarting is the only way back.
    private func startInterpreterWatch() {
        guard interpreterWatchTimer == nil else { return }

        let schedule: () -> Void = { [weak self] in
            guard let self else { return }
            self.interpreterWatchTimer = Timer.scheduledTimer(
                withTimeInterval: self.interpreterCheckInterval,
                repeats: true
            ) { [weak self] _ in
                self?.checkInterpreterHealth()
                self?.checkRubyRuntime()
            }
        }

        if Thread.isMainThread {
            schedule()
        } else {
            DispatchQueue.main.async { schedule() }
        }
    }

    /// Reinstall the notes ripper's gems when Homebrew upgrades Ruby under them.
    private func checkRubyRuntime() {
        let state = runtimeInstaller.rubyState
        guard state.succeeded, !state.isRunning,
              runtimeInstaller.rubyRuntimeIsStale(),
              let resources = Bundle.main.resourceURL
        else { return }
        NSLog("Homebrew Ruby changed; reinstalling the notes ripper's gems")
        runtimeInstaller.ensureRuby(from: resources.appendingPathComponent("ruby_deps", isDirectory: true))
    }

    private func checkInterpreterHealth() {
        guard backendStarted, !isRecoveringInterpreter else { return }

        if let last = lastInterpreterRecovery,
           Date().timeIntervalSince(last) < interpreterRecoveryCooldown {
            return
        }

        backendManager?.interpreterHealthy { [weak self] healthy, reason in
            guard let self, !healthy else { return }
            NSLog("Backend interpreter is no longer valid: \(reason ?? "unknown reason")")
            // Also a URLSession callback; recovery touches UI state and timers.
            DispatchQueue.main.async { self.recoverInterpreter() }
        }
    }

    private func recoverInterpreter() {
        guard !isRecoveringInterpreter, let resources = Bundle.main.resourceURL else { return }

        isRecoveringInterpreter = true
        lastInterpreterRecovery = Date()

        // Rebuild first: restarting alone would just relaunch from the same
        // stale venv, whose recorded interpreter no longer exists.
        NSLog("Rebuilding Python venv and restarting backend to restore macOS permissions")
        let backendDir = resources.appendingPathComponent("backend_src", isDirectory: true)
        runtimeInstaller.ensurePython(from: backendDir)

        waitForPythonInstall { [weak self] succeeded in
            guard let self else { return }
            if succeeded {
                self.backendManager?.restart()
                // The rebuilt venv runs from a binary macOS has not seen before,
                // so permissions have to be granted again.
                self.preflightManager.runFullCheck()
            } else {
                NSLog("Python venv rebuild failed; leaving backend as-is")
            }
            self.isRecoveringInterpreter = false
        }
    }

    /// Poll the installer until it stops running, then report whether it worked.
    ///
    /// `sawRunning` guards against sampling before ensurePython's async work has
    /// started, which would otherwise read the *previous* run's result.
    private func waitForPythonInstall(attempt: Int = 0, sawRunning: Bool = false, completion: @escaping (Bool) -> Void) {
        let maxAttempts = 120  // ~10 minutes at 5s intervals
        let running = runtimeInstaller.pythonState.isRunning

        if sawRunning && !running {
            completion(runtimeInstaller.pythonState.succeeded)
            return
        }

        guard attempt < maxAttempts else {
            NSLog("Timed out waiting for the Python venv rebuild")
            completion(false)
            return
        }

        DispatchQueue.main.asyncAfter(deadline: .now() + 5) { [weak self] in
            self?.waitForPythonInstall(
                attempt: attempt + 1,
                sawRunning: sawRunning || running,
                completion: completion
            )
        }
    }

    func presentPreflightWindow() {
        showWindow(bringToFront: true)
    }

    /// Ask macOS for a sync service's missing permissions on behalf of the web
    /// UI, which cannot show a system prompt itself. The answers arrive as
    /// ordinary status updates, which rewrite permissions.json for the backend.
    ///
    /// Returns false for an unknown service.
    func requestPermissions(for service: String) -> Bool {
        let requirements: [Requirement]
        switch service {
        case "notes": requirements = [.fullDiskAccess, .notesAutomation]
        case "reminders": requirements = [.remindersAutomation]
        case "photos": requirements = [.photosAutomation, .photosAppAutomation]
        default: return false
        }
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            let statuses = self.preflightManager.currentStatuses()
            let missing = requirements.filter { requirement in
                !(statuses.first { $0.requirement == requirement }?.state.isSatisfied ?? false)
            }
            self.request(missing)
        }
        return true
    }

    /// One prompt at a time: each waits for the answer to the one before.
    private func request(_ requirements: [Requirement]) {
        guard let requirement = requirements.first else { return }
        let next: () -> Void = { [weak self] in self?.request(Array(requirements.dropFirst())) }
        switch requirement {
        case .fullDiskAccess:
            // There is no prompt for it; only System Settings can grant it.
            preflightManager.openFullDiskAccessPreferences()
            next()
        case .notesAutomation, .photosAppAutomation:
            preflightManager.requestAutomation(for: requirement, completion: next)
        case .remindersAutomation:
            preflightManager.requestRemindersAccess(completion: next)
        case .photosAutomation:
            preflightManager.requestPhotosAutomation(completion: next)
        case .homebrew, .xcodeCommandLineTools, .python, .ruby:
            next()
        }
    }

    private func handle(event: PreflightEvent) {
        switch event {
        case .statusesUpdated(let statuses):
            latestStatuses = statuses
            maybeStartRuntimeInstalls()
            persistPermissions(statuses: statuses)

            let snapshot = currentSnapshot()
            // Keep an open window current. Its rows used to be refreshed only
            // on the way to (re)showing it, which never happens once the
            // essentials are ready and "Don't show this next time" is on.
            windowController?.apply(snapshot: snapshot)

            // Only essential requirements block the daemon
            let blockingIssue = statuses.contains { status in
                guard status.requirement.isEssential else { return false }
                switch status.state {
                case .actionRequired, .failed:
                    return true
                default:
                    return false
                }
            }

            if blockingIssue {
                showWindow()
                windowController?.apply(snapshot: snapshot)
                return
            }

            // Open setup on first launch even when nothing blocks the backend,
            // so the optional permissions are offered here, not prompted for
            // by the backend halfway through its first sync.
            if !hasShownOnce {
                showWindow()
                windowController?.apply(snapshot: snapshot)
            }

            if allRequirementsSatisfied() {
                ensureBackendRunning(showWindowIfAllowed: true, forceShowWindow: false, snapshot: snapshot)
            }
            // If still checking/ installing but no blocking issues, don't force the window.

        case .allSatisfied:
            if allRequirementsSatisfied() {
                ensureBackendRunning(showWindowIfAllowed: !shouldSuppressWhenHealthy, forceShowWindow: false, snapshot: currentSnapshot())
            }
        }
    }

    private func refreshRuntimeStatuses() {
        let snapshot = PreflightSnapshot(
            statuses: augmentedStatuses(base: latestStatuses),
            suppressNext: shouldSuppressWhenHealthy,
            allSatisfied: allRequirementsSatisfied(),
            progress: runtimeProgress(),
            logs: runtimeLogs()
        )
        windowController?.apply(snapshot: snapshot)
        if allRequirementsSatisfied() {
            ensureBackendRunning(showWindowIfAllowed: !shouldSuppressWhenHealthy, forceShowWindow: false, snapshot: snapshot)
        }
    }

    private func currentSnapshot() -> PreflightSnapshot {
        PreflightSnapshot(
            statuses: augmentedStatuses(base: latestStatuses),
            suppressNext: shouldSuppressWhenHealthy,
            allSatisfied: allRequirementsSatisfied(),
            progress: runtimeProgress(),
            logs: runtimeLogs()
        )
    }

    private func augmentedStatuses(base: [RequirementStatus]) -> [RequirementStatus] {
        var result = base
        if let idx = result.firstIndex(where: { $0.requirement == .python }) {
            let state = runtimeInstaller.pythonState
            if state.isRunning {
                result[idx].state = .installing(state.message)
            } else if state.succeeded {
                result[idx].state = .satisfied(state.message)
            } else {
                result[idx].state = .actionRequired(state.message)
            }
        }
        if let idx = result.firstIndex(where: { $0.requirement == .ruby }) {
            let state = runtimeInstaller.rubyState
            if state.isRunning {
                result[idx].state = .installing(state.message)
            } else if state.succeeded {
                result[idx].state = .satisfied(state.message)
            } else {
                result[idx].state = .actionRequired(state.message)
            }
        }
        return result
    }

    private func allRequirementsSatisfied() -> Bool {
        let augmented = augmentedStatuses(base: latestStatuses)
        let essentialsSatisfied = augmented
            .filter { $0.requirement.isEssential }
            .allSatisfy { $0.state.isSatisfied }
        let runtimeSatisfied = runtimeInstaller.pythonState.succeeded && runtimeInstaller.rubyState.succeeded
        return essentialsSatisfied && runtimeSatisfied
    }

    private func runtimeProgress() -> [Requirement: Double] {
        var map: [Requirement: Double] = [:]
        map[.python] = runtimeInstaller.pythonState.progress
        map[.ruby] = runtimeInstaller.rubyState.progress
        return map
    }

    private func runtimeLogs() -> [Requirement: URL?] {
        var map: [Requirement: URL?] = [:]
        map[.python] = runtimeInstaller.pythonState.logURL
        map[.ruby] = runtimeInstaller.rubyState.logURL
        return map
    }

    private func maybeStartRuntimeInstalls() {
        guard let resources = Bundle.main.resourceURL else { return }
        if let pythonStatus = latestStatuses.first(where: { $0.requirement == .python }), pythonStatus.state.isSatisfied {
            if !runtimeInstaller.pythonState.isRunning && !runtimeInstaller.pythonState.succeeded {
                let backendDir = resources.appendingPathComponent("backend_src", isDirectory: true)
                runtimeInstaller.ensurePython(from: backendDir)
            }
        }
        if let rubyStatus = latestStatuses.first(where: { $0.requirement == .ruby }), rubyStatus.state.isSatisfied {
            if !runtimeInstaller.rubyState.isRunning && !runtimeInstaller.rubyState.succeeded {
                let rubyDir = resources.appendingPathComponent("ruby_deps", isDirectory: true)
                runtimeInstaller.ensureRuby(from: rubyDir)
            }
        }
    }

    /// Show the setup window, creating it if needed.
    ///
    /// Automatic callers only open a window that isn't already up. Pulling an
    /// open one to the front on every status update would drag it over System
    /// Settings while the user is granting something there. `bringToFront` is
    /// for when the user asked to see it.
    private func showWindow(bringToFront: Bool = false) {
        let presentWindow: () -> Void = { [weak self] in
            guard let self else { return }
            if self.windowController == nil {
                let controller = PreflightWindowController()
                controller.onInstallHomebrew = { [weak self] in self?.preflightManager.installHomebrew() }
                controller.onInstallXcodeCLT = { [weak self] in self?.preflightManager.installXcodeCommandLineTools() }
                controller.onInstallPython = { [weak self] in self?.preflightManager.installPython() }
                controller.onInstallRuby = { [weak self] in self?.preflightManager.installRuby() }
                controller.onOpenFullDiskAccess = { [weak self] in self?.preflightManager.openFullDiskAccessPreferences() }
                // A macOS prompt takes focus from the window, and when it goes
                // away focus returns to whichever app had it before, not
                // necessarily us. Bring the window back once it is answered.
                controller.onOpenNotesAutomation = { [weak self] in
                    self?.preflightManager.requestAutomation(for: .notesAutomation) { self?.windowController?.bringToFront() }
                }
                controller.onOpenRemindersAutomation = { [weak self] in
                    self?.preflightManager.requestRemindersAccess { self?.windowController?.bringToFront() }
                }
                controller.onOpenPhotosAutomation = { [weak self] in
                    self?.preflightManager.requestPhotosAutomation { self?.windowController?.bringToFront() }
                }
                controller.onOpenPhotosAppAutomation = { [weak self] in
                    self?.preflightManager.requestAutomation(for: .photosAppAutomation) { self?.windowController?.bringToFront() }
                }
                controller.onRefresh = { [weak self] in self?.preflightManager.runFullCheck() }
                controller.onBecameKey = { [weak self] in self?.preflightManager.refreshPermissions() }
                controller.onCloseRequested = { [weak self] in self?.handleCloseRequested() }
                controller.onToggleSuppress = { [weak self] suppress in
                    self?.defaults.set(suppress, forKey: self?.suppressKey ?? "")
                }

                self.windowController = controller
                controller.present()
                self.defaults.set(true, forKey: self.shownKey)

                let snapshot = PreflightSnapshot(
                    statuses: self.preflightManager.currentStatuses(),
                    suppressNext: self.shouldSuppressWhenHealthy,
                    allSatisfied: false,
                    progress: self.runtimeProgress(),
                    logs: self.runtimeLogs()
                )
                controller.apply(snapshot: snapshot)
            } else if let controller = self.windowController {
                // Minimised or hidden (Cmd-H) still counts as open: the user put it there.
                let isOpen = (controller.window?.isVisible ?? false)
                    || (controller.window?.isMiniaturized ?? false)
                    || NSApp.isHidden
                if bringToFront || !isOpen {
                    controller.present()
                }
            }
        }

        if Thread.isMainThread {
            presentWindow()
        } else {
            DispatchQueue.main.async { presentWindow() }
        }
    }

    private var hasShownOnce: Bool {
        defaults.bool(forKey: shownKey)
    }

    private var shouldSuppressWhenHealthy: Bool {
        defaults.bool(forKey: suppressKey)
    }

    private func ensureBackendRunning(showWindowIfAllowed: Bool, forceShowWindow: Bool, snapshot: PreflightSnapshot?) {
        guard let backendManager else { return }

        backendManager.backendHealthy { [weak self] healthy in
            guard let self else { return }
            // URLSession delivers this on its own delegate queue, and
            // handlePostBackend shows and closes windows. AppKit traps when
            // touched off the main thread, which killed the app - and the
            // menu bar item with it - the moment the window was dismissed.
            DispatchQueue.main.async {
                self.backendStarted = true
                if !healthy {
                    self.backendManager?.start()
                }
                self.handlePostBackend(
                    showWindowIfAllowed: showWindowIfAllowed,
                    forceShowWindow: forceShowWindow,
                    snapshot: snapshot
                )
            }
        }
    }

    /// Never closes the window. This runs after every status update, and it
    /// used to close the window whenever the backend was healthy and "Don't
    /// show this next time" was on - which is the default, and is switched on
    /// automatically once the essentials are ready. So the window vanished
    /// the moment the user granted a permission. That setting only decides
    /// whether the window opens by itself; once open, it stays until closed.
    private func handlePostBackend(showWindowIfAllowed: Bool, forceShowWindow: Bool, snapshot: PreflightSnapshot?) {
        guard showWindowIfAllowed, forceShowWindow || !shouldSuppressWhenHealthy else { return }
        showWindow()
        if let snapshot { windowController?.apply(snapshot: snapshot) }
    }

    private func persistPermissions(statuses: [RequirementStatus]) {
        let permissionKeys: [(Requirement, String)] = [
            (.fullDiskAccess, "full_disk_access"),
            (.notesAutomation, AutomationTarget.notes.permissionKey),
            (.remindersAutomation, "reminders_automation"),
            (.photosAutomation, "photos_automation"),
            (.photosAppAutomation, AutomationTarget.photos.permissionKey),
        ]

        var dict: [String: Bool] = [:]
        for (req, key) in permissionKeys {
            let satisfied = statuses.first(where: { $0.requirement == req })?.state.isSatisfied ?? false
            dict[key] = satisfied
        }

        let fileURL = PermissionsFile.url
        try? FileManager.default.createDirectory(at: fileURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        if let data = try? JSONSerialization.data(withJSONObject: dict, options: [.prettyPrinted, .sortedKeys]) {
            try? data.write(to: fileURL)
        }
    }

    private func handleCloseRequested() {
        let allSatisfied = allRequirementsSatisfied()
        if !allSatisfied {
            // Keep the app alive so the menu bar and retry actions remain available.
            backendManager?.stop()
            windowController?.close()
            return
        }
        windowController?.close()
    }
}
