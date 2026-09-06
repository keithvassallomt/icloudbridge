import Foundation

/// Publishes `PhotoKitExportService` to the Python backend over loopback HTTP.
///
/// On start it writes a handshake file containing the bound port and a freshly
/// generated bearer token. The backend reads that file to find the service; the
/// token stops any other local process from driving the user's photo library.
/// The file is rewritten on every launch, so a stale token never lingers.
final class PhotoKitBridge {
    static let handshakeURL: URL = {
        FileManager.default
            .homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/iCloudBridge")
            .appendingPathComponent("photokit-service.json")
    }()

    private static let preferredPort: UInt16 = 27732

    private let service = PhotoKitExportService()
    private var server: LocalHTTPServer?
    private let authQueue = DispatchQueue(label: "app.icloudbridge.photokit.auth")

    func start() {
        let token = Self.makeToken()
        let server = LocalHTTPServer(token: token)
        server.handler = { [weak self] request in
            self?.route(request) ?? .error("Bridge unavailable", status: 503)
        }

        let port: UInt16
        do {
            port = try server.start(preferredPort: Self.preferredPort)
        } catch {
            // Fall back to any free port before giving up, in case something
            // else already holds the preferred one.
            do {
                port = try server.start(preferredPort: 0)
            } catch {
                NSLog("PhotoKit bridge failed to start: \(error.localizedDescription)")
                return
            }
        }

        do {
            try Self.writeHandshake(port: port, token: token)
        } catch {
            NSLog("PhotoKit bridge handshake failed: \(error.localizedDescription)")
            server.stop()
            return
        }

        self.server = server
        NSLog("PhotoKit bridge listening on 127.0.0.1:\(port)")
    }

    func stop() {
        server?.stop()
        server = nil
        try? FileManager.default.removeItem(at: Self.handshakeURL)
    }

    // MARK: - Routing

    private func route(_ request: LocalHTTPServer.Request) -> LocalHTTPServer.Response {
        if request.method == "UNAUTHORIZED" {
            return .error("Invalid or missing token", status: 401)
        }

        switch (request.method, request.path) {
        case ("GET", "/health"):
            return .json([
                "status": "ok",
                "authorization": service.authorizationStatus(),
            ])

        case ("POST", "/authorize"):
            // Returns immediately: the system prompt needs the user, and
            // blocking here would stall the listener. Poll /health for the
            // outcome.
            authQueue.async { [service] in
                service.requestAuthorization { status in
                    NSLog("PhotoKit authorization: \(status)")
                }
            }
            return .json(["status": "requested",
                          "authorization": service.authorizationStatus()])

        case ("POST", "/export"):
            return startExport(request)

        case ("GET", "/job"):
            guard let id = request.query["id"] else {
                return .error("Missing id", status: 400)
            }
            let since = Int(request.query["since"] ?? "0") ?? 0
            guard let snapshot = service.snapshot(id: id, since: since) else {
                return .error("Unknown job", status: 404)
            }
            return .json(snapshot)

        case ("POST", "/cancel"):
            guard let id = request.query["id"] else {
                return .error("Missing id", status: 400)
            }
            return service.cancel(id: id)
                ? .json(["status": "cancelled"])
                : .error("Unknown job", status: 404)

        case ("POST", "/forget"):
            guard let id = request.query["id"] else {
                return .error("Missing id", status: 400)
            }
            service.forget(id: id)
            return .json(["status": "forgotten"])

        default:
            return .error("Not found", status: 404)
        }
    }

    private func startExport(_ request: LocalHTTPServer.Request) -> LocalHTTPServer.Response {
        guard
            let payload = try? JSONSerialization.jsonObject(with: request.body),
            let object = payload as? [String: Any],
            let uuids = object["uuids"] as? [String],
            let destination = object["destination"] as? String
        else {
            return .error("Expected {uuids: [String], destination: String}", status: 400)
        }

        guard destination.hasPrefix("/") else {
            return .error("Destination must be an absolute path", status: 400)
        }

        switch service.startExport(
            uuids: uuids, destination: URL(fileURLWithPath: destination)
        ) {
        case .success(let jobID):
            return .json(["job_id": jobID, "total": uuids.count], status: 202)
        case .failure(let failure):
            return .error(failure.message, status: 409)
        }
    }

    // MARK: - Handshake

    private static func makeToken() -> String {
        var bytes = [UInt8](repeating: 0, count: 32)
        if SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes) != errSecSuccess {
            // Should not happen; a UUID pair still beats a predictable token.
            return UUID().uuidString + UUID().uuidString
        }
        return bytes.map { String(format: "%02x", $0) }.joined()
    }

    private static func writeHandshake(port: UInt16, token: String) throws {
        let directory = handshakeURL.deletingLastPathComponent()
        try FileManager.default.createDirectory(
            at: directory, withIntermediateDirectories: true
        )

        let data = try JSONSerialization.data(
            withJSONObject: ["port": Int(port), "token": token]
        )
        try data.write(to: handshakeURL, options: .atomic)

        // Owner-only: the token is what authorizes photo-library access.
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o600], ofItemAtPath: handshakeURL.path
        )
    }
}
