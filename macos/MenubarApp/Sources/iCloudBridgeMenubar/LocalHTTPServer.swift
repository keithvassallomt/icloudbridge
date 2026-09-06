import Foundation
import Network

/// A deliberately small HTTP/1.1 server bound to the loopback interface.
///
/// The Python backend cannot use PhotoKit: TCC attributes it to the Homebrew
/// python binary (bundle id `org.python.python`), whose Info.plist carries no
/// photo-library usage description, so authorization is denied outright. This
/// app *is* granted Photos access, so it exposes the few operations the backend
/// needs over loopback instead.
///
/// Every request must carry the shared bearer token from `ServiceHandshake`;
/// any other local process sees 401.
final class LocalHTTPServer {
    struct Request {
        let method: String
        let path: String
        let query: [String: String]
        let body: Data
    }

    struct Response {
        let status: Int
        let body: Data

        static func json(_ object: Any, status: Int = 200) -> Response {
            let data = (try? JSONSerialization.data(withJSONObject: object))
                ?? Data("{}".utf8)
            return Response(status: status, body: data)
        }

        static func error(_ message: String, status: Int) -> Response {
            return .json(["error": message], status: status)
        }
    }

    /// Handlers run on `queue`; they may block only briefly. Long work must be
    /// dispatched elsewhere so the listener keeps accepting connections.
    var handler: ((Request) -> Response)?

    private let token: String
    private let queue = DispatchQueue(label: "app.icloudbridge.httpserver")
    private var listener: NWListener?

    init(token: String) {
        self.token = token
    }

    /// Binds to 127.0.0.1 on `port`, or an OS-assigned port when `port` is 0.
    /// Returns the port actually bound.
    func start(preferredPort: UInt16) throws -> UInt16 {
        let parameters = NWParameters.tcp
        parameters.requiredInterfaceType = .loopback
        parameters.allowLocalEndpointReuse = true

        let listener: NWListener
        if let port = NWEndpoint.Port(rawValue: preferredPort) {
            listener = try NWListener(using: parameters, on: port)
        } else {
            listener = try NWListener(using: parameters)
        }

        let ready = DispatchSemaphore(value: 0)
        var startError: Error?

        listener.stateUpdateHandler = { state in
            switch state {
            case .ready:
                ready.signal()
            case .failed(let error), .waiting(let error):
                startError = error
                ready.signal()
            default:
                break
            }
        }
        listener.newConnectionHandler = { [weak self] connection in
            self?.accept(connection)
        }
        listener.start(queue: queue)

        if ready.wait(timeout: .now() + 5) == .timedOut {
            listener.cancel()
            throw NSError(
                domain: "LocalHTTPServer", code: 1,
                userInfo: [NSLocalizedDescriptionKey: "Timed out binding to port \(preferredPort)"]
            )
        }
        if let startError {
            listener.cancel()
            throw startError
        }
        guard let bound = listener.port?.rawValue else {
            listener.cancel()
            throw NSError(
                domain: "LocalHTTPServer", code: 2,
                userInfo: [NSLocalizedDescriptionKey: "Listener reported no port"]
            )
        }

        self.listener = listener
        return bound
    }

    func stop() {
        listener?.cancel()
        listener = nil
    }

    // MARK: - Connection handling

    private func accept(_ connection: NWConnection) {
        connection.start(queue: queue)
        receive(connection, buffer: Data())
    }

    private func receive(_ connection: NWConnection, buffer: Data) {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 64 * 1024) {
            [weak self] data, _, isComplete, error in
            guard let self else { return }

            var buffer = buffer
            if let data {
                buffer.append(data)
            }

            if error != nil {
                connection.cancel()
                return
            }

            // Wait until the full request (headers plus declared body) arrived.
            if let parsed = self.parse(buffer) {
                let response = self.respond(to: parsed)
                self.send(response, on: connection)
                return
            }

            if isComplete {
                connection.cancel()
                return
            }

            self.receive(connection, buffer: buffer)
        }
    }

    private func respond(to request: Request) -> Response {
        guard let handler else {
            return .error("No handler configured", status: 503)
        }
        return handler(request)
    }

    private func send(_ response: Response, on connection: NWConnection) {
        var header = "HTTP/1.1 \(response.status) \(Self.reason(response.status))\r\n"
        header += "Content-Type: application/json\r\n"
        header += "Content-Length: \(response.body.count)\r\n"
        header += "Connection: close\r\n\r\n"

        var out = Data(header.utf8)
        out.append(response.body)

        connection.send(
            content: out,
            completion: .contentProcessed { _ in connection.cancel() }
        )
    }

    // MARK: - Parsing

    /// Returns nil while the request is still incomplete.
    private func parse(_ buffer: Data) -> Request? {
        let separator = Data("\r\n\r\n".utf8)
        guard let headerEnd = buffer.range(of: separator) else { return nil }

        let headerData = buffer[buffer.startIndex..<headerEnd.lowerBound]
        guard let headerText = String(data: headerData, encoding: .utf8) else { return nil }

        var lines = headerText.components(separatedBy: "\r\n")
        guard !lines.isEmpty else { return nil }

        let requestLine = lines.removeFirst().components(separatedBy: " ")
        guard requestLine.count >= 2 else { return nil }

        var headers: [String: String] = [:]
        for line in lines {
            guard let colon = line.firstIndex(of: ":") else { continue }
            let name = line[line.startIndex..<colon].trimmingCharacters(in: .whitespaces)
            let value = line[line.index(after: colon)...].trimmingCharacters(in: .whitespaces)
            headers[name.lowercased()] = value
        }

        let declaredLength = Int(headers["content-length"] ?? "0") ?? 0
        let bodyStart = headerEnd.upperBound
        let available = buffer.count - buffer.distance(from: buffer.startIndex, to: bodyStart)
        if available < declaredLength {
            return nil
        }

        let body = buffer[bodyStart...].prefix(declaredLength)

        // Authenticate before the request reaches any handler.
        let presented = (headers["authorization"] ?? "")
            .replacingOccurrences(of: "Bearer ", with: "")
        guard constantTimeEquals(presented, token) else {
            return Request(method: "UNAUTHORIZED", path: "", query: [:], body: Data())
        }

        let (path, query) = Self.splitQuery(requestLine[1])
        return Request(
            method: requestLine[0].uppercased(),
            path: path,
            query: query,
            body: Data(body)
        )
    }

    private static func splitQuery(_ target: String) -> (String, [String: String]) {
        let parts = target.split(separator: "?", maxSplits: 1, omittingEmptySubsequences: false)
        let path = String(parts.first ?? "")
        guard parts.count == 2 else { return (path, [:]) }

        var query: [String: String] = [:]
        for pair in parts[1].split(separator: "&") {
            let kv = pair.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
            guard let key = kv.first else { continue }
            let value = kv.count == 2 ? String(kv[1]) : ""
            query[String(key)] = value.removingPercentEncoding ?? value
        }
        return (path, query)
    }

    /// Compares without leaking the token's contents through timing.
    private func constantTimeEquals(_ lhs: String, _ rhs: String) -> Bool {
        let a = Array(lhs.utf8)
        let b = Array(rhs.utf8)
        guard a.count == b.count else { return false }
        var difference: UInt8 = 0
        for index in a.indices {
            difference |= a[index] ^ b[index]
        }
        return difference == 0
    }

    private static func reason(_ status: Int) -> String {
        switch status {
        case 200: return "OK"
        case 202: return "Accepted"
        case 400: return "Bad Request"
        case 401: return "Unauthorized"
        case 403: return "Forbidden"
        case 404: return "Not Found"
        case 409: return "Conflict"
        case 503: return "Service Unavailable"
        default: return "Internal Server Error"
        }
    }
}
