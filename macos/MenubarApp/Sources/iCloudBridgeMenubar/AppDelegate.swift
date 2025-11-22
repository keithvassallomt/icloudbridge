import Cocoa

final class AppDelegate: NSObject, NSApplicationDelegate {
    private let backendManager = BackendProcessManager()
    private let launchAgentManager = LaunchAgentManager()
    private var preflightCoordinator: PreflightCoordinator?
    private var menuController: MenuController?

    func applicationDidFinishLaunching(_ notification: Notification) {
        let coordinator = PreflightCoordinator(backendManager: backendManager)
        preflightCoordinator = coordinator
        menuController = MenuController(
            backendManager: backendManager,
            launchAgentManager: launchAgentManager,
            preflightCoordinator: coordinator
        )
        coordinator.start()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        // Menubar app should keep running even when its preflight window closes.
        return false
    }

    func applicationWillTerminate(_ notification: Notification) {
        backendManager.stop()
    }
}
