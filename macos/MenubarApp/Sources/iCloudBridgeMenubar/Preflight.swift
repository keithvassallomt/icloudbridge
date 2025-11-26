import Cocoa
import ApplicationServices
import EventKit
import Photos

enum Requirement: CaseIterable {
    case homebrew
    case xcodeCommandLineTools
    case python
    case ruby
    case fullDiskAccess
    case accessibility
    case notesAutomation
    case remindersAutomation
    case photosAutomation

    var title: String {
        switch self {
        case .homebrew: return "Homebrew"
        case .xcodeCommandLineTools: return "Xcode Command Line Tools"
        case .python: return "Python 3.12 (Homebrew)"
        case .ruby: return "Ruby >= 3.4 (Homebrew)"
        case .fullDiskAccess: return "Full Disk Access"
        case .accessibility: return "Accessibility"
        case .notesAutomation: return "Apple Notes"
        case .remindersAutomation: return "Apple Reminders"
        case .photosAutomation: return "Apple Photos"
        }
    }
}

enum RequirementState {
    case pending
    case checking
    case installing(String)
    case satisfied(String)
    case actionRequired(String)
    case failed(String)

    var isSatisfied: Bool {
        if case .satisfied = self { return true }
        return false
    }

    var isActionable: Bool {
        switch self {
        case .actionRequired, .failed:
            return true
        default:
            return false
        }
    }

    var message: String {
        switch self {
        case .pending: return "Pending"
        case .checking: return "Checking…"
        case .installing(let detail): return detail
        case .satisfied(let detail): return detail
        case .actionRequired(let detail): return detail
        case .failed(let detail): return detail
        }
    }
}

struct RequirementStatus {
    let requirement: Requirement
    var state: RequirementState
}

struct Semver: Comparable {
    private let parts: [Int]

    init(_ string: String) {
        let filtered = string.split(whereSeparator: { !$0.isNumber && $0 != "." })
        if let first = filtered.first {
            parts = first.split(separator: ".").compactMap { Int($0) }
        } else {
            parts = []
        }
    }

    static func < (lhs: Semver, rhs: Semver) -> Bool {
        let maxCount = max(lhs.parts.count, rhs.parts.count)
        for idx in 0..<maxCount {
            let l = idx < lhs.parts.count ? lhs.parts[idx] : 0
            let r = idx < rhs.parts.count ? rhs.parts[idx] : 0
            if l != r { return l < r }
        }
        return false
    }
}

struct ShellResult {
    let status: Int32
    let output: String
}

enum PreflightEvent {
    case statusesUpdated([RequirementStatus])
    case allSatisfied
}

final class Shell {
    static func run(_ launchPath: String, _ arguments: [String], environment: [String: String] = [:]) -> ShellResult {
        let task = Process()
        task.executableURL = URL(fileURLWithPath: launchPath)
        task.arguments = arguments
        var env = ProcessInfo.processInfo.environment
        environment.forEach { env[$0.key] = $0.value }
        task.environment = env

        let pipe = Pipe()
        task.standardOutput = pipe
        task.standardError = pipe

        do {
            try task.run()
        } catch {
            return ShellResult(status: -1, output: "Failed to start: \(error.localizedDescription)")
        }
        task.waitUntilExit()
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        let output = String(data: data, encoding: .utf8) ?? ""
        return ShellResult(status: task.terminationStatus, output: output)
    }
}

final class PreflightManager {
    private let queue = DispatchQueue(label: "app.icloudbridge.preflight")
    private var statuses: [RequirementStatus] = Requirement.allCases.map { RequirementStatus(requirement: $0, state: .pending) }
    private var brewPath: String?
    private var installing = Set<Requirement>()

    var onEvent: ((PreflightEvent) -> Void)?

    func currentStatuses() -> [RequirementStatus] {
        statuses
    }

    func runFullCheck() {
        update(.homebrew, state: .checking)
        update(.xcodeCommandLineTools, state: .checking)
        update(.python, state: .checking)
        update(.ruby, state: .checking)
        update(.fullDiskAccess, state: .checking)
        update(.accessibility, state: .checking)
        update(.notesAutomation, state: .checking)
        update(.remindersAutomation, state: .checking)
        update(.photosAutomation, state: .checking)

        queue.async { [weak self] in
            guard let self else { return }
            let brewState = self.checkHomebrew()
            self.update(.homebrew, state: brewState)

            let cltState = self.checkXcodeCommandLineTools()
            self.update(.xcodeCommandLineTools, state: cltState)
            if !cltState.isSatisfied { self.installIfNeeded(.xcodeCommandLineTools) }

            if brewState.isSatisfied {
                let pythonState = self.checkPython()
                self.update(.python, state: pythonState)
                if !pythonState.isSatisfied { self.installIfNeeded(.python) }

                if cltState.isSatisfied {
                    let rubyState = self.checkRuby()
                    self.update(.ruby, state: rubyState)
                    if !rubyState.isSatisfied { self.installIfNeeded(.ruby) }
                } else {
                    self.update(.ruby, state: .actionRequired("Install Xcode Command Line Tools before setting up Ruby"))
                }
            } else {
                self.update(.python, state: .actionRequired("Requires Homebrew to install python@3.12"))
                self.update(.ruby, state: .actionRequired("Requires Homebrew to install Ruby"))
                self.installIfNeeded(.homebrew)
            }

            let fdaState = self.checkFullDiskAccess()
            self.update(.fullDiskAccess, state: fdaState)

            let accessibilityState = self.checkAccessibilityStatus()
            self.update(.accessibility, state: accessibilityState)

            let notesState = self.checkAutomationPermission(appName: "Notes", requestIfNeeded: false)
            self.update(.notesAutomation, state: notesState)

            let remindersState = self.checkRemindersPermission(requestIfNeeded: false)
            self.update(.remindersAutomation, state: remindersState)

            let photosState = self.checkPhotosPermission(requestIfNeeded: false)
            self.update(.photosAutomation, state: photosState)
        }
    }

    func installHomebrew() {
        update(.homebrew, state: .installing("Installing Homebrew…"))
        queue.async { [weak self] in
            guard let self else { return }
            let script = "NONINTERACTIVE=1 /bin/bash -c \"$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
            let result = Shell.run("/bin/bash", ["-c", script])
            if result.status == 0 {
                self.brewPath = self.locateBrew()
                self.runFullCheck()
            } else {
                self.update(.homebrew, state: .failed("Homebrew install failed: \(result.output.trimmingCharacters(in: .whitespacesAndNewlines))"))
            }
        }
    }

    func installXcodeCommandLineTools() {
        update(.xcodeCommandLineTools, state: .installing("Requesting Command Line Tools install…"))
        queue.async { [weak self] in
            guard let self else { return }
            let result = Shell.run("/usr/bin/xcode-select", ["--install"])
            let output = result.output.trimmingCharacters(in: .whitespacesAndNewlines)
            // The installer returns a non-zero exit code even when it successfully prompts the user.
            if output.localizedCaseInsensitiveContains("already installed") {
                self.runFullCheck()
                return
            }

            if result.status == 0 || output.localizedCaseInsensitiveContains("install requested") {
                self.pollForXcodeCommandLineTools(start: Date())
            } else {
                self.update(.xcodeCommandLineTools, state: .failed("Command Line Tools install failed: \(output)"))
            }
        }
    }

    func installPython() {
        guard let brew = ensureBrew() else {
            update(.python, state: .actionRequired("Homebrew is required to install python@3.12"))
            return
        }
        update(.python, state: .installing("Installing python@3.12…"))
        queue.async { [weak self] in
            guard let self else { return }
            let result = Shell.run(brew, ["install", "python@3.12"], environment: ["HOMEBREW_NO_AUTO_UPDATE": "1", "NONINTERACTIVE": "1"])
            if result.status == 0 {
                self.runFullCheck()
            } else {
                self.update(.python, state: .failed("python@3.12 install failed: \(result.output.trimmingCharacters(in: .whitespacesAndNewlines))"))
            }
        }
    }

    func installRuby() {
        guard let brew = ensureBrew() else {
            update(.ruby, state: .actionRequired("Homebrew is required to install Ruby"))
            return
        }
        guard checkXcodeCommandLineTools().isSatisfied else {
            update(.ruby, state: .actionRequired("Install Xcode Command Line Tools first"))
            return
        }
        update(.ruby, state: .installing("Installing Ruby…"))
        queue.async { [weak self] in
            guard let self else { return }
            let result = Shell.run(brew, ["install", "ruby"], environment: ["HOMEBREW_NO_AUTO_UPDATE": "1", "NONINTERACTIVE": "1"])
            if result.status == 0 {
                self.runFullCheck()
            } else {
                self.update(.ruby, state: .failed("Ruby install failed: \(result.output.trimmingCharacters(in: .whitespacesAndNewlines))"))
            }
        }
    }

    private func pollForXcodeCommandLineTools(start: Date, attempt: Int = 0) {
        let state = checkXcodeCommandLineTools()
        if state.isSatisfied {
            update(.xcodeCommandLineTools, state: state)
            return
        }

        let elapsed = Date().timeIntervalSince(start)
        if elapsed > 900 {
            update(.xcodeCommandLineTools, state: .actionRequired("Command Line Tools install did not complete. Retry from Software Update."))
            return
        }

        let delay = min(15.0, 3.0 + Double(attempt))
        update(.xcodeCommandLineTools, state: .installing("Waiting for Command Line Tools to finish…"))
        queue.asyncAfter(deadline: .now() + delay) { [weak self] in
            self?.pollForXcodeCommandLineTools(start: start, attempt: attempt + 1)
        }
    }

    func openFullDiskAccessPreferences() {
        guard let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles") else { return }
        NSWorkspace.shared.open(url)
    }

    func openAccessibilityPreferences() {
        guard let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility") else { return }
        NSWorkspace.shared.open(url)
    }

    func openAutomationPreferences() {
        guard let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation") else { return }
        NSWorkspace.shared.open(url)
    }

    private func checkAccessibilityStatus() -> RequirementState {
        if AXIsProcessTrusted() {
            return .satisfied("Accessibility permission granted")
        }
        return .actionRequired("Allow Accessibility control")
    }

    @discardableResult
    private func checkAutomationPermission(appName: String, requestIfNeeded: Bool) -> RequirementState {
        let scriptSource: String
        switch appName {
        case "Notes":
            scriptSource = "tell application \"Notes\" to get id of default account"
        case "Reminders":
            scriptSource = "tell application \"Reminders\" to get name of default list"
        default:
            scriptSource = ""
        }

        guard let script = NSAppleScript(source: scriptSource) else {
            return .failed("Could not create automation request for \(appName)")
        }

        var errorInfo: NSDictionary?
        if requestIfNeeded {
            _ = script.executeAndReturnError(&errorInfo)
        } else {
            _ = script.executeAndReturnError(&errorInfo)
        }

        if let errorInfo, let code = errorInfo[NSAppleScript.errorNumber] as? Int {
            if code == -1743 || code == -1744 {
                return .actionRequired("Allow Automation control of \(appName) in Privacy & Security > Automation")
            }
            let message = errorInfo[NSAppleScript.errorMessage] as? String ?? "Unknown error"
            return .failed("Automation error for \(appName): \(message)")
        }

        return .satisfied("Automation permission granted for \(appName)")
    }

    private func update(_ requirement: Requirement, state: RequirementState) {
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            if let idx = self.statuses.firstIndex(where: { $0.requirement == requirement }) {
                self.statuses[idx].state = state
            }
            self.onEvent?(.statusesUpdated(self.statuses))

            if self.statuses.allSatisfy({ $0.state.isSatisfied }) {
                self.onEvent?(.allSatisfied)
            }
        }
    }

    private func installIfNeeded(_ requirement: Requirement) {
        guard !installing.contains(requirement) else { return }
        installing.insert(requirement)

        switch requirement {
        case .homebrew:
            installHomebrew()
        case .xcodeCommandLineTools:
            installXcodeCommandLineTools()
        case .python:
            installPython()
        case .ruby:
            installRuby()
        case .fullDiskAccess, .accessibility, .notesAutomation, .remindersAutomation, .photosAutomation:
            break
        }
    }

    @discardableResult
    private func checkHomebrew() -> RequirementState {
        if let path = locateBrew() {
            brewPath = path
            return .satisfied("Found Homebrew at \(path)")
        }
        return .actionRequired("Homebrew not found; required to install dependencies")
    }

    @discardableResult
    private func checkXcodeCommandLineTools() -> RequirementState {
        let result = Shell.run("/usr/bin/xcode-select", ["-p"])
        let path = result.output.trimmingCharacters(in: .whitespacesAndNewlines)
        if result.status == 0, !path.isEmpty {
            var isDir: ObjCBool = false
            if FileManager.default.fileExists(atPath: path, isDirectory: &isDir), isDir.boolValue {
                let clang = (path as NSString).appendingPathComponent("usr/bin/clang")
                if FileManager.default.isExecutableFile(atPath: clang) {
                    return .satisfied("Command Line Tools installed")
                }
                return .satisfied("Command Line Tools path found at \(path)")
            }
        }
        return .actionRequired("Install Xcode Command Line Tools (xcode-select --install)")
    }

    @discardableResult
    private func checkPython() -> RequirementState {
        guard let brew = ensureBrew() else {
            return .actionRequired("Install Homebrew first")
        }
        let result = Shell.run(brew, ["list", "--versions", "python@3.12"], environment: ["HOMEBREW_NO_AUTO_UPDATE": "1"])
        if result.status == 0, !result.output.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            let components = result.output.split(separator: " ")
            let versionString = components.dropFirst().first.map(String.init) ?? ""
            let binDir = "/opt/homebrew/opt/python@3.12/bin"
            let py3 = "\(binDir)/python3"
            let py312 = "\(binDir)/python3.12"
            if FileManager.default.isExecutableFile(atPath: py3) || FileManager.default.isExecutableFile(atPath: py312) {
                return .satisfied("python@3.12 installed (\(versionString))")
            }
            return .actionRequired("python@3.12 not linked at expected path")
        }
        return .actionRequired("python@3.12 not installed")
    }

    @discardableResult
    private func checkRuby() -> RequirementState {
        guard let brew = ensureBrew() else {
            return .actionRequired("Install Homebrew first")
        }
        let result = Shell.run(brew, ["list", "--versions", "ruby"], environment: ["HOMEBREW_NO_AUTO_UPDATE": "1"])
        if result.status == 0, !result.output.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            let parts = result.output.split(separator: " ")
            let versionString = parts.dropFirst().first.map(String.init) ?? ""
            let detected = Semver(versionString)
            if detected >= Semver("3.4.0") {
                let rubyPath = "/opt/homebrew/opt/ruby/bin/ruby"
                if FileManager.default.isExecutableFile(atPath: rubyPath) {
                    return .satisfied("Ruby installed (\(versionString))")
                }
                return .actionRequired("Ruby not linked at expected path")
            }
            return .actionRequired("Ruby \(versionString) found; need >= 3.4")
        }
        return .actionRequired("Ruby not installed")
    }

    @discardableResult
    private func checkFullDiskAccess() -> RequirementState {
        let notesPath = (NSHomeDirectory() as NSString).appendingPathComponent("Library/Group Containers/group.com.apple.notes")
        let fm = FileManager.default
        var isDir: ObjCBool = false
        if fm.fileExists(atPath: notesPath, isDirectory: &isDir), isDir.boolValue {
            do {
                _ = try fm.contentsOfDirectory(atPath: notesPath)
                return .satisfied("Notes data readable; Full Disk Access granted")
            } catch let error as NSError {
                if error.domain == NSCocoaErrorDomain && error.code == NSFileReadNoPermissionError {
                    return .actionRequired("Full Disk Access required to read Notes database")
                }
                return .failed("Could not verify Full Disk Access: \(error.localizedDescription)")
            }
        }
        // If Notes directory does not exist, treat as pass but surface message.
        return .satisfied("Notes database not found; Full Disk Access check passed")
    }

    private func locateBrew() -> String? {
        let candidates = ["/opt/homebrew/bin/brew", "/usr/local/bin/brew"]
        for path in candidates where FileManager.default.isExecutableFile(atPath: path) {
            return path
        }

        let which = Shell.run("/usr/bin/which", ["brew"])
        let path = which.output.trimmingCharacters(in: .whitespacesAndNewlines)
        return which.status == 0 && !path.isEmpty ? path : nil
    }

    private func ensureBrew() -> String? {
        if let brewPath { return brewPath }
        brewPath = locateBrew()
        return brewPath
    }

    private func checkRemindersPermission(requestIfNeeded: Bool) -> RequirementState {
        let status = EKEventStore.authorizationStatus(for: .reminder)
        switch status {
        case .authorized, .fullAccess, .writeOnly:
            return .satisfied("Reminders permission granted")
        case .notDetermined:
            if !requestIfNeeded {
                return .actionRequired("Allow Reminders access")
            }
            let store = EKEventStore()
            let semaphore = DispatchSemaphore(value: 0)
            var granted = false
            store.requestAccess(to: .reminder) { ok, _ in
                granted = ok
                semaphore.signal()
            }
            _ = semaphore.wait(timeout: .now() + 5)
            return granted ? .satisfied("Reminders permission granted") : .actionRequired("Allow Reminders access")
        case .denied, .restricted:
            return .actionRequired("Allow Reminders access in System Settings > Privacy & Security > Reminders")
        @unknown default:
            return .failed("Unknown Reminders permission state")
        }
    }

    private func checkPhotosPermission(requestIfNeeded: Bool) -> RequirementState {
        let status = PHPhotoLibrary.authorizationStatus(for: .readWrite)
        switch status {
        case .authorized, .limited:
            return .satisfied("Photos permission granted")
        case .notDetermined:
            if !requestIfNeeded { return .actionRequired("Allow Photos access") }
            var result: PHAuthorizationStatus = .notDetermined
            let sema = DispatchSemaphore(value: 0)
            PHPhotoLibrary.requestAuthorization(for: .readWrite) { auth in
                result = auth
                sema.signal()
            }
            _ = sema.wait(timeout: .now() + 5)
            return (result == .authorized || result == .limited) ? .satisfied("Photos permission granted") : .actionRequired("Allow Photos access")
        case .denied, .restricted:
            return .actionRequired("Allow Photos access in System Settings > Privacy & Security > Photos")
        @unknown default:
            return .failed("Unknown Photos permission state")
        }
    }

    // User-initiated permission requests
    func requestAccessibilityPrompt() {
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            let options = [kAXTrustedCheckOptionPrompt.takeRetainedValue() as String: true] as CFDictionary
            _ = AXIsProcessTrustedWithOptions(options)
            // Poll for a short window to allow TCC to record the new trust decision without forcing a relaunch.
            self.pollAccessibilityStatus(start: Date(), openSettingsOnFailure: true)
        }
    }

    private func pollAccessibilityStatus(start: Date, attempt: Int = 0, openSettingsOnFailure: Bool = false) {
        let state = checkAccessibilityStatus()
        if state.isSatisfied {
            update(.accessibility, state: state)
            return
        }

        let elapsed = Date().timeIntervalSince(start)
        if elapsed > 15 {
            update(.accessibility, state: state)
            if openSettingsOnFailure {
                DispatchQueue.main.async { [weak self] in
                    self?.openAccessibilityPreferences()
                }
            }
            return
        }

        let delay = min(2.0, 0.5 + Double(attempt) * 0.25)
        queue.asyncAfter(deadline: .now() + delay) { [weak self] in
            self?.pollAccessibilityStatus(start: start, attempt: attempt + 1, openSettingsOnFailure: openSettingsOnFailure)
        }
    }

    func requestNotesAutomation() {
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            let state = self.checkAutomationPermission(appName: "Notes", requestIfNeeded: true)
            self.update(.notesAutomation, state: state)

            // If the user denies or the prompt does not appear, surface the System Settings panel.
            if !state.isSatisfied {
                self.openAutomationPreferences()
            }
        }
    }

    func requestPhotosAutomation() {
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            PHPhotoLibrary.requestAuthorization(for: .readWrite) { auth in
                DispatchQueue.main.async { [weak self] in
                    guard let self else { return }
                    let finalState: RequirementState
                    if auth == .authorized || auth == .limited {
                        finalState = .satisfied("Photos permission granted")
                    } else {
                        finalState = .actionRequired("Allow Photos access in System Settings > Privacy & Security > Photos")
                    }
                    self.update(.photosAutomation, state: finalState)
                }
            }
        }
    }

    func requestRemindersAccess() {
        let state = checkRemindersPermission(requestIfNeeded: true)
        update(.remindersAutomation, state: state)
    }
}
