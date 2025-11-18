import Cocoa

final class MenuController {
    private let backendManager: BackendProcessManager
    private let launchAgentManager: LaunchAgentManager
    private let iconProvider = StatusIconProvider()

    private let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private let openWebItem = NSMenuItem(title: "Open Web UI", action: #selector(openWebUI), keyEquivalent: "")
    private let toggleLoginItem = NSMenuItem(title: "Start at Login", action: #selector(toggleLoginItemAction), keyEquivalent: "")
    private let quitItem = NSMenuItem(title: "Quit iCloudBridge", action: #selector(quitApp), keyEquivalent: "q")
    private let activityIndicator: NSProgressIndicator = {
        let indicator = NSProgressIndicator()
        indicator.style = .spinning
        indicator.controlSize = .small
        indicator.isDisplayedWhenStopped = false
        indicator.translatesAutoresizingMaskIntoConstraints = false
        return indicator
    }()

    private var indicatorConstraintsInstalled = false
    private var syncObserver: SyncStatusObserver?

    init(backendManager: BackendProcessManager, launchAgentManager: LaunchAgentManager) {
        self.backendManager = backendManager
        self.launchAgentManager = launchAgentManager
        configureStatusItem()
        rebuildMenu()
        observeSyncStatus()
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
        guard let url = URL(string: "http://127.0.0.1:27731/") else { return }
        NSWorkspace.shared.open(url)
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
