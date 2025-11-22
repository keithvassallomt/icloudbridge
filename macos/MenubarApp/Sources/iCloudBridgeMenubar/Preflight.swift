import Cocoa
import ApplicationServices

enum Requirement: CaseIterable {
    case homebrew
    case python
    case ruby
    case fullDiskAccess
    case accessibility

    var title: String {
        switch self {
        case .homebrew: return "Homebrew"
        case .python: return "Python 3.12 (Homebrew)"
        case .ruby: return "Ruby >= 3.4 (Homebrew)"
        case .fullDiskAccess: return "Notes Full Disk Access"
        case .accessibility: return "Accessibility"
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
        update(.python, state: .checking)
        update(.ruby, state: .checking)
        update(.fullDiskAccess, state: .checking)
        update(.accessibility, state: .checking)

        queue.async { [weak self] in
            guard let self else { return }
            let brewState = self.checkHomebrew()
            self.update(.homebrew, state: brewState)

            if brewState.isSatisfied {
                let pythonState = self.checkPython()
                self.update(.python, state: pythonState)
                if !pythonState.isSatisfied { self.installIfNeeded(.python) }

                let rubyState = self.checkRuby()
                self.update(.ruby, state: rubyState)
                if !rubyState.isSatisfied { self.installIfNeeded(.ruby) }
            } else {
                self.update(.python, state: .actionRequired("Requires Homebrew to install python@3.12"))
                self.update(.ruby, state: .actionRequired("Requires Homebrew to install Ruby"))
                self.installIfNeeded(.homebrew)
            }

            let fdaState = self.checkFullDiskAccess()
            self.update(.fullDiskAccess, state: fdaState)

            // Accessibility prompt must execute on the main thread; update state there.
            self.checkAccessibilityAsync()
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

    func openFullDiskAccessPreferences() {
        guard let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles") else { return }
        NSWorkspace.shared.open(url)
    }

    func openAccessibilityPreferences() {
        guard let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility") else { return }
        NSWorkspace.shared.open(url)
    }

    private func checkAccessibilityAsync() {
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            if AXIsProcessTrusted() {
                self.update(.accessibility, state: .satisfied("Accessibility permission granted"))
                return
            }
            let options = [kAXTrustedCheckOptionPrompt.takeRetainedValue() as String: true] as CFDictionary
            _ = AXIsProcessTrustedWithOptions(options)
            self.update(.accessibility, state: .actionRequired("Grant Accessibility permission in System Settings > Privacy & Security > Accessibility"))

            // Re-check shortly after prompting in case the user just granted it.
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { [weak self] in
                guard let self else { return }
                if AXIsProcessTrusted() {
                    self.update(.accessibility, state: .satisfied("Accessibility permission granted"))
                }
            }
        }
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
        case .python:
            installPython()
        case .ruby:
            installRuby()
        case .fullDiskAccess, .accessibility:
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
}
