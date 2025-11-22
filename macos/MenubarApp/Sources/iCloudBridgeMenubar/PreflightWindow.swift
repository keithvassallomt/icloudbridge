import Cocoa
import SwiftUI

struct PreflightSnapshot {
    let statuses: [RequirementStatus]
    let suppressNext: Bool
    let allSatisfied: Bool
    let progress: [Requirement: Double]
    let logs: [Requirement: URL?]
}

final class PreflightModel: ObservableObject {
    @Published var snapshot: PreflightSnapshot

    init(snapshot: PreflightSnapshot) {
        self.snapshot = snapshot
    }
}

final class PreflightWindowController: NSWindowController {
    private let model = PreflightModel(snapshot: PreflightSnapshot(statuses: [], suppressNext: true, allSatisfied: false, progress: [:], logs: [:]))
    private let hostingController: NSHostingController<PreflightView>

    var onInstallHomebrew: (() -> Void)? {
        didSet { setCallbacks() }
    }
    var onInstallPython: (() -> Void)? {
        didSet { setCallbacks() }
    }
    var onInstallRuby: (() -> Void)? {
        didSet { setCallbacks() }
    }
    var onOpenFullDiskAccess: (() -> Void)? {
        didSet { setCallbacks() }
    }
    var onOpenAccessibility: (() -> Void)? {
        didSet { setCallbacks() }
    }
    var onRefresh: (() -> Void)? {
        didSet { setCallbacks() }
    }
    var onCloseRequested: (() -> Void)? {
        didSet { setCallbacks() }
    }
    var onToggleSuppress: ((Bool) -> Void)? {
        didSet { setCallbacks() }
    }

    init() {
        hostingController = NSHostingController(rootView: PreflightView(model: model))
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 900, height: 520),
            styleMask: [.titled, .miniaturizable],
            backing: .buffered,
            defer: false
        )
        window.center()
        window.title = "iCloudBridge Setup"
        window.contentViewController = hostingController
        window.isReleasedWhenClosed = false
        super.init(window: window)
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    func apply(snapshot: PreflightSnapshot) {
        model.snapshot = snapshot
    }

    private func setCallbacks() {
        hostingController.rootView.onInstallHomebrew = onInstallHomebrew
        hostingController.rootView.onInstallPython = onInstallPython
        hostingController.rootView.onInstallRuby = onInstallRuby
        hostingController.rootView.onOpenFullDiskAccess = onOpenFullDiskAccess
        hostingController.rootView.onOpenAccessibility = onOpenAccessibility
        hostingController.rootView.onRefresh = onRefresh
        hostingController.rootView.onCloseRequested = onCloseRequested
        hostingController.rootView.onToggleSuppress = onToggleSuppress
    }
}

struct PreflightView: View {
    @ObservedObject var model: PreflightModel

    var onInstallHomebrew: (() -> Void)?
    var onInstallPython: (() -> Void)?
    var onInstallRuby: (() -> Void)?
    var onOpenFullDiskAccess: (() -> Void)?
    var onOpenAccessibility: (() -> Void)?
    var onRefresh: (() -> Void)?
    var onCloseRequested: (() -> Void)?
    var onToggleSuppress: ((Bool) -> Void)?

    private var snapshot: PreflightSnapshot { model.snapshot }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Confirm the prerequisites below before starting the sync engine.")
                .font(.system(size: 14, weight: .semibold))

            VStack(alignment: .leading, spacing: 12) {
                Text("Runtimes").font(.headline)
                VStack(spacing: 8) {
                    RequirementRow(
                        title: "Homebrew",
                        status: status(for: .homebrew),
                        actionTitle: buttonTitle(for: .homebrew, defaultTitle: "Install Homebrew"),
                        actionEnabled: isActionEnabled(for: .homebrew),
                        showsProgress: isInProgress(for: .homebrew),
                        progress: progress(for: .homebrew),
                        logURL: logURL(for: .homebrew),
                        onShowLogs: { openLogs(for: .homebrew) },
                        action: { onInstallHomebrew?() }
                    )
                    RequirementRow(
                        title: "Python 3.12",
                        status: status(for: .python),
                        actionTitle: buttonTitle(for: .python, defaultTitle: "Install python@3.12"),
                        actionEnabled: isActionEnabled(for: .python),
                        showsProgress: isInProgress(for: .python),
                        progress: progress(for: .python),
                        logURL: logURL(for: .python),
                        onShowLogs: { openLogs(for: .python) },
                        action: { onInstallPython?() }
                    )
                    RequirementRow(
                        title: "Ruby",
                        status: status(for: .ruby),
                        actionTitle: buttonTitle(for: .ruby, defaultTitle: "Install Ruby"),
                        actionEnabled: isActionEnabled(for: .ruby),
                        showsProgress: isInProgress(for: .ruby),
                        progress: progress(for: .ruby),
                        logURL: logURL(for: .ruby),
                        onShowLogs: { openLogs(for: .ruby) },
                        action: { onInstallRuby?() }
                    )
                }
                .padding(12)
                .background(.quaternary.opacity(0.6))
                .cornerRadius(10)

                Text("Permissions").font(.headline).padding(.top, 8)
                VStack(spacing: 8) {
                    RequirementRow(
                        title: "Notes Full Disk Access",
                        status: status(for: .fullDiskAccess),
                        actionTitle: "Open System Settings",
                        actionEnabled: isActionEnabled(for: .fullDiskAccess),
                        showsProgress: false,
                        progress: nil,
                        logURL: nil,
                        onShowLogs: nil,
                        action: { onOpenFullDiskAccess?() }
                    )
                    RequirementRow(
                        title: "Accessibility",
                        status: status(for: .accessibility),
                        actionTitle: "Open System Settings",
                        actionEnabled: isActionEnabled(for: .accessibility),
                        showsProgress: false,
                        progress: nil,
                        logURL: nil,
                        onShowLogs: nil,
                        action: { onOpenAccessibility?() }
                    )
                }
                .padding(12)
                .background(.quaternary.opacity(0.6))
                .cornerRadius(10)
            }

            Text(summaryText)
                .font(.system(size: 13))
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            Toggle("Don't show this next time", isOn: Binding(
                get: { snapshot.suppressNext },
                set: { value in
                    onToggleSuppress?(value)
                    model.snapshot = PreflightSnapshot(statuses: snapshot.statuses, suppressNext: value, allSatisfied: snapshot.allSatisfied, progress: snapshot.progress, logs: snapshot.logs)
                }
            ))
            .toggleStyle(.switch)
            .onChange(of: snapshot.allSatisfied) { newValue in
                if newValue && !snapshot.suppressNext {
                    onToggleSuppress?(true)
                    model.snapshot = PreflightSnapshot(statuses: snapshot.statuses, suppressNext: true, allSatisfied: snapshot.allSatisfied, progress: snapshot.progress, logs: snapshot.logs)
                }
            }

            HStack {
                Button("Refresh") { onRefresh?() }
                Spacer()
                Button("Close") { onCloseRequested?() }
            }
        }
        .padding(24)
        .frame(minWidth: 900)
    }

    private var summaryText: String {
        snapshot.allSatisfied ? "Sync engine started. You can close this window." : "The sync engine will start automatically when prerequisites are met."
    }

    private func status(for requirement: Requirement) -> RequirementState {
        snapshot.statuses.first(where: { $0.requirement == requirement })?.state ?? .pending
    }

    private func isInProgress(for requirement: Requirement) -> Bool {
        let state = status(for: requirement)
        if case .installing = state { return true }
        if case .checking = state { return true }
        return false
    }

    private func progress(for requirement: Requirement) -> Double? {
        snapshot.progress[requirement]
    }

    private func logURL(for requirement: Requirement) -> URL? {
        snapshot.logs[requirement] ?? nil
    }

    private func openLogs(for requirement: Requirement) {
        guard let url = logURL(for: requirement) else { return }
        NSWorkspace.shared.open(url)
    }

    private func isActionEnabled(for requirement: Requirement) -> Bool {
        let state = status(for: requirement)
        switch state {
        case .pending, .checking, .installing, .satisfied:
            return false
        case .actionRequired, .failed:
            return true
        }
    }

    private func buttonTitle(for requirement: Requirement, defaultTitle: String) -> String {
        let state = status(for: requirement)
        switch state {
        case .installing:
            return "Installing…"
        case .checking:
            return "Checking…"
        case .satisfied:
            return "Installed"
        case .failed, .actionRequired:
            return "Retry"
        default:
            return defaultTitle
        }
    }
}

private struct RequirementRow: View {
    let title: String
    let status: RequirementState
    let actionTitle: String
    let actionEnabled: Bool
    let showsProgress: Bool
    let progress: Double?
    let logURL: URL?
    let onShowLogs: (() -> Void)?
    let action: () -> Void

    var body: some View {
        HStack(alignment: .center, spacing: 10) {
            Text(title)
                .font(.system(size: 13, weight: .semibold))
                .frame(width: 140, alignment: .leading)

            if showsProgress {
                if let progress {
                    ProgressView(value: progress)
                        .progressViewStyle(.linear)
                        .frame(width: 80)
                } else {
                    ProgressView()
                        .progressViewStyle(.circular)
                        .controlSize(.small)
                        .frame(width: 14, height: 14)
                }
            } else {
                Circle()
                    .fill(color(for: status))
                    .frame(width: 10, height: 10)
            }

            Text(status.message)
                .font(.system(size: 13))
                .lineLimit(2)
                .truncationMode(.tail)
                .frame(maxWidth: .infinity, alignment: .leading)

            if actionEnabled {
                Button(actionTitle, action: action)
                    .controlSize(.small)
            }

            if let logURL {
                Button("Logs") { onShowLogs?() ?? openLog(logURL) }
                    .controlSize(.small)
            }
        }
    }

    private func color(for state: RequirementState) -> Color {
        switch state {
        case .satisfied:
            return .green
        case .installing, .checking:
            return .orange
        case .actionRequired, .failed:
            return .red
        case .pending:
            return .gray
        }
    }

    private func openLog(_ url: URL) {
        NSWorkspace.shared.open(url)
    }
}
#if DEBUG
struct PreflightView_Previews: PreviewProvider {
    static var previews: some View {
        let snapshot = PreflightSnapshot(
            statuses: [
                RequirementStatus(requirement: .homebrew, state: .satisfied("Found Homebrew")),
                RequirementStatus(requirement: .python, state: .installing("Installing…")),
                RequirementStatus(requirement: .ruby, state: .actionRequired("Ruby not installed")),
                RequirementStatus(requirement: .fullDiskAccess, state: .failed("Needs Full Disk Access"))
            ],
            suppressNext: true,
            allSatisfied: false
        )
        return PreflightView(model: PreflightModel(snapshot: snapshot))
            .frame(width: 560, height: 420)
    }
}
#endif
