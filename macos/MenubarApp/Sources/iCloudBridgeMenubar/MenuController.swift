import Cocoa

final class MenuController {
    private let backendManager: BackendProcessManager
    private let launchAgentManager: LaunchAgentManager
    private let iconProvider = StatusIconProvider()

    private let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private let openWebItem = NSMenuItem(title: "Open Web UI", action: #selector(openWebUI), keyEquivalent: "")
    private let toggleLoginItem = NSMenuItem(title: "Start at Login", action: #selector(toggleLoginItemAction), keyEquivalent: "")
    private let quitItem = NSMenuItem(title: "Quit iCloudBridge", action: #selector(quitApp), keyEquivalent: "q")
    private let backendStartingIndicator: NSImageView = {
        let view = NSImageView()
        view.translatesAutoresizingMaskIntoConstraints = false
        view.isHidden = true
        if let symbol = NSImage(systemSymbolName: "clock.arrow.circlepath", accessibilityDescription: "Starting backend") {
            view.image = symbol
            view.contentTintColor = .systemOrange
        }
        return view
    }()
    private let activityIndicator: NSProgressIndicator = {
        let indicator = NSProgressIndicator()
        indicator.style = .spinning
        indicator.controlSize = .small
        indicator.isDisplayedWhenStopped = false
        indicator.translatesAutoresizingMaskIntoConstraints = false
        return indicator
    }()

    private var indicatorConstraintsInstalled = false
    private var backendStartingConstraintsInstalled = false
    private var syncObserver: SyncStatusObserver?
    private var backendStartupAlertShown = false
    private var backendStartupProbeScheduled = false

    init(backendManager: BackendProcessManager, launchAgentManager: LaunchAgentManager) {
        self.backendManager = backendManager
        self.launchAgentManager = launchAgentManager
        configureStatusItem()
        rebuildMenu()
        observeSyncStatus()
        startBackendStartupMonitor()
    }

    private func configureStatusItem() {
        updateStatusIcon()
        installActivityIndicatorIfNeeded()
        setSyncIndicatorVisible(false)
    }

    private func rebuildMenu() {
        openWebItem.target = self
        toggleLoginItem.target = self
        quitItem.target = self

        let menu = NSMenu()
        menu.addItem(openWebItem)
        menu.addItem(toggleLoginItem)
        menu.addItem(NSMenuItem.separator())
        menu.addItem(quitItem)
        statusItem.menu = menu

        refreshLoginItemState()
    }

    private func updateStatusIcon() {
        guard let button = statusItem.button else {
            return
        }

        if let icon = iconProvider.image() {
            icon.size = NSSize(width: 18, height: 18)
            button.image = icon
            button.image?.isTemplate = true
            button.title = ""
        } else {
            button.title = "☁︎"
        }
    }

    private func installActivityIndicatorIfNeeded() {
        guard !indicatorConstraintsInstalled, let button = statusItem.button else {
            return
        }

        button.addSubview(activityIndicator)
        NSLayoutConstraint.activate([
            activityIndicator.widthAnchor.constraint(equalToConstant: 12),
            activityIndicator.heightAnchor.constraint(equalToConstant: 12),
            activityIndicator.trailingAnchor.constraint(equalTo: button.trailingAnchor, constant: -1),
            activityIndicator.bottomAnchor.constraint(equalTo: button.bottomAnchor, constant: -1),
        ])
        indicatorConstraintsInstalled = true
    }

    private func setSyncIndicatorVisible(_ visible: Bool) {
        installActivityIndicatorIfNeeded()
        if visible {
            activityIndicator.startAnimation(nil)
        } else {
            activityIndicator.stopAnimation(nil)
        }
    }

    private func installBackendStartingIndicatorIfNeeded() {
        guard !backendStartingConstraintsInstalled, let button = statusItem.button else {
            return
        }

        button.addSubview(backendStartingIndicator)
        NSLayoutConstraint.activate([
            backendStartingIndicator.widthAnchor.constraint(equalToConstant: 12),
            backendStartingIndicator.heightAnchor.constraint(equalToConstant: 12),
            backendStartingIndicator.trailingAnchor.constraint(equalTo: button.trailingAnchor, constant: -1),
            backendStartingIndicator.bottomAnchor.constraint(equalTo: button.bottomAnchor, constant: -1),
        ])
        backendStartingConstraintsInstalled = true
    }

    private func setBackendStartingIndicatorVisible(_ visible: Bool) {
        installBackendStartingIndicatorIfNeeded()
        backendStartingIndicator.isHidden = !visible
    }

    private func startBackendStartupMonitor() {
        setBackendStartingIndicatorVisible(true)
        scheduleBackendReadinessProbe()
    }

    private func scheduleBackendReadinessProbe() {
        guard !backendStartupProbeScheduled else { return }
        backendStartupProbeScheduled = true
        DispatchQueue.global().asyncAfter(deadline: .now() + 1.5) { [weak self] in
            self?.backendStartupProbeScheduled = false
            self?.probeBackendReadiness()
        }
    }

    private func probeBackendReadiness() {
        let healthURL = URL(string: "http://127.0.0.1:27731/api/health")!
        let task = URLSession.shared.dataTask(with: healthURL) { [weak self] _, response, error in
            guard let self else { return }
            if error == nil, let httpResponse = response as? HTTPURLResponse, httpResponse.statusCode == 200 {
                DispatchQueue.main.async {
                    self.setBackendStartingIndicatorVisible(false)
                }
            } else {
                DispatchQueue.main.async {
                    self.setBackendStartingIndicatorVisible(true)
                    self.scheduleBackendReadinessProbe()
                }
            }
        }
        task.resume()
    }

    private func observeSyncStatus() {
        syncObserver = SyncStatusObserver()
        syncObserver?.onSyncStateChange = { [weak self] isSyncing in
            self?.setSyncIndicatorVisible(isSyncing)
        }
    }

    private func refreshLoginItemState() {
        toggleLoginItem.state = launchAgentManager.isInstalled() ? .on : .off
    }

    @objc private func openWebUI() {
        checkBackendHealthAndOpenUI()
    }

    private func checkBackendHealthAndOpenUI(showAlertIfStarting: Bool = true) {
        let healthURL = URL(string: "http://127.0.0.1:27731/api/health")!

        // Try to connect to backend
        let task = URLSession.shared.dataTask(with: healthURL) { [weak self] data, response, error in
            guard let self else { return }

            if error == nil, let httpResponse = response as? HTTPURLResponse, httpResponse.statusCode == 200 {
                // Backend is ready, open web UI
                DispatchQueue.main.async {
                    guard let url = URL(string: "http://127.0.0.1:27731/") else { return }
                    NSWorkspace.shared.open(url)
                    self.backendStartupAlertShown = false
                    self.setBackendStartingIndicatorVisible(false)
                }
            } else {
                // Backend not ready yet, show alert and retry
                DispatchQueue.main.async {
                    if showAlertIfStarting {
                        self.showBackendStartingAlert()
                    }
                    self.setBackendStartingIndicatorVisible(true)
                    self.scheduleBackendHealthRetry()
                }
            }
        }
        task.resume()
    }

    private func showBackendStartingAlert() {
        guard !backendStartupAlertShown else { return }
        backendStartupAlertShown = true

        let alert = NSAlert()
        alert.messageText = "Backend is starting"
        alert.informativeText = "iCloudBridge is starting the sync engine, which takes a few seconds. The Web UI will be displayed automatically once this is done."
        alert.alertStyle = .informational
        alert.addButton(withTitle: "OK")

        alert.runModal()
    }

    private func scheduleBackendHealthRetry() {
        DispatchQueue.global().asyncAfter(deadline: .now() + 2) { [weak self] in
            self?.checkBackendHealthAndOpenUI(showAlertIfStarting: false)
        }
    }

    @objc private func toggleLoginItemAction() {
        do {
            if launchAgentManager.isInstalled() {
                try launchAgentManager.remove()
            } else {
                try launchAgentManager.install()
            }
            refreshLoginItemState()
        } catch {
            presentErrorAlert(message: error.localizedDescription)
        }
    }

    @objc private func quitApp() {
        backendManager.stop()
        NSApp.terminate(nil)
    }

    private func presentErrorAlert(message: String) {
        let alert = NSAlert()
        alert.messageText = "iCloudBridge"
        alert.informativeText = message
        alert.alertStyle = .warning
        alert.runModal()
    }
}
