import Foundation

final class SyncStatusObserver {
    private struct SyncMessage: Decodable {
        let type: String
        let data: SyncData?
    }

    private struct SyncData: Decodable {
        let status: String?
    }

    private let session: URLSession
    private var task: URLSessionWebSocketTask?
    private var reconnectWorkItem: DispatchWorkItem?
    private let decoder = JSONDecoder()
    private var isSyncing = false

    var onSyncStateChange: ((Bool) -> Void)?

    init() {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 15
        config.timeoutIntervalForResource = 60
        session = URLSession(configuration: config)
        connect()
    }

    deinit {
        reconnectWorkItem?.cancel()
        task?.cancel()
        session.invalidateAndCancel()
    }

    private func connect() {
        reconnectWorkItem?.cancel()
        guard let url = makeWebSocketURL() else {
            return
        }
        let task = session.webSocketTask(with: url)
        self.task = task
        task.resume()
        listen()
    }

    private func listen() {
        task?.receive { [weak self] result in
            guard let self else { return }
            switch result {
            case .success(let message):
                self.handle(message)
                self.listen()
            case .failure:
                self.scheduleReconnect()
            }
        }
    }

    private func handle(_ message: URLSessionWebSocketTask.Message) {
        let payload: Data?
        switch message {
        case .data(let data):
            payload = data
        case .string(let string):
            payload = string.data(using: .utf8)
        @unknown default:
            payload = nil
        }

        guard
            let payload,
            let syncMessage = try? decoder.decode(SyncMessage.self, from: payload),
            syncMessage.type == "sync_progress",
            let status = syncMessage.data?.status?.lowercased()
        else {
            return
        }

        let currentlySyncing = status == "running"
        updateSyncState(isSyncing: currentlySyncing)
    }

    private func updateSyncState(isSyncing: Bool) {
        guard isSyncing != self.isSyncing else { return }
        self.isSyncing = isSyncing
        DispatchQueue.main.async { [weak self] in
            self?.onSyncStateChange?(isSyncing)
        }
    }

    private func scheduleReconnect() {
        task?.cancel()
        let workItem = DispatchWorkItem { [weak self] in
            self?.connect()
        }
        reconnectWorkItem = workItem
        DispatchQueue.global().asyncAfter(deadline: .now() + 3, execute: workItem)
    }

    private func makeWebSocketURL() -> URL? {
        let env = ProcessInfo.processInfo.environment
        let host = env["ICLOUDBRIDGE_SERVER_HOST"] ?? "127.0.0.1"
        let port = env["ICLOUDBRIDGE_SERVER_PORT"] ?? "27731"
        var components = URLComponents()
        components.scheme = "ws"
        components.host = host
        components.port = Int(port)
        components.path = "/api/ws"
        return components.url
    }
}
