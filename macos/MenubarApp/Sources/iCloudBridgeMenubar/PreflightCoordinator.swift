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

    func start() {
        preflightManager.runFullCheck()
    }

    func presentPreflightWindow() {
        showWindow(force: true)
    }

    private func handle(event: PreflightEvent) {
        switch event {
        case .statusesUpdated(let statuses):
            latestStatuses = statuses
            maybeStartRuntimeInstalls()

            let snapshot = currentSnapshot()
            let blockingIssue = statuses.contains { status in
                switch status.state {
                case .actionRequired, .failed:
                    return true
                default:
                    return false
                }
            }

            if blockingIssue {
                showWindow(force: true)
                windowController?.apply(snapshot: snapshot)
                return
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
        let baseSatisfied = augmented.allSatisfy { $0.state.isSatisfied }
        let runtimeSatisfied = runtimeInstaller.pythonState.succeeded && runtimeInstaller.rubyState.succeeded
        return baseSatisfied && runtimeSatisfied
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

    private func showWindow(force: Bool = false) {
        let presentWindow: () -> Void = { [weak self] in
            guard let self else { return }
            if self.windowController == nil {
                let controller = PreflightWindowController()
                controller.onInstallHomebrew = { [weak self] in self?.preflightManager.installHomebrew() }
                controller.onInstallXcodeCLT = { [weak self] in self?.preflightManager.installXcodeCommandLineTools() }
                controller.onInstallPython = { [weak self] in self?.preflightManager.installPython() }
                controller.onInstallRuby = { [weak self] in self?.preflightManager.installRuby() }
                controller.onOpenFullDiskAccess = { [weak self] in self?.preflightManager.openFullDiskAccessPreferences() }
                controller.onOpenAccessibility = { [weak self] in self?.preflightManager.requestAccessibilityPrompt() }
                controller.onOpenNotesAutomation = { [weak self] in self?.preflightManager.requestNotesAutomation() }
                controller.onOpenRemindersAutomation = { [weak self] in self?.preflightManager.requestRemindersAccess() }
                controller.onOpenPhotosAutomation = { [weak self] in self?.preflightManager.requestPhotosAutomation() }
                controller.onRefresh = { [weak self] in self?.preflightManager.runFullCheck() }
                controller.onCloseRequested = { [weak self] in self?.handleCloseRequested() }
                controller.onToggleSuppress = { [weak self] suppress in
                    self?.defaults.set(suppress, forKey: self?.suppressKey ?? "")
                }

                self.windowController = controller
                controller.showWindow(nil)
                controller.window?.makeKeyAndOrderFront(nil)
                self.defaults.set(true, forKey: self.shownKey)

                let snapshot = PreflightSnapshot(
                    statuses: self.preflightManager.currentStatuses(),
                    suppressNext: self.shouldSuppressWhenHealthy,
                    allSatisfied: false,
                    progress: self.runtimeProgress(),
                    logs: self.runtimeLogs()
                )
                controller.apply(snapshot: snapshot)
            } else {
                if force || !(self.windowController?.window?.isVisible ?? false) {
                    self.windowController?.showWindow(nil)
                    self.windowController?.window?.makeKeyAndOrderFront(nil)
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
            if healthy {
                self.backendStarted = true
                self.handlePostBackend(showWindowIfAllowed: showWindowIfAllowed, forceShowWindow: forceShowWindow, snapshot: snapshot, healthy: true)
                return
            }

            self.backendStarted = true
            self.backendManager?.start()
            self.handlePostBackend(showWindowIfAllowed: showWindowIfAllowed, forceShowWindow: forceShowWindow, snapshot: snapshot, healthy: false)
        }
    }

    private func handlePostBackend(showWindowIfAllowed: Bool, forceShowWindow: Bool, snapshot: PreflightSnapshot?, healthy: Bool) {
        if showWindowIfAllowed {
            if forceShowWindow || !shouldSuppressWhenHealthy {
                showWindow(force: true)
                if let snapshot { windowController?.apply(snapshot: snapshot) }
            } else if shouldSuppressWhenHealthy && healthy {
                windowController?.close()
            }
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
