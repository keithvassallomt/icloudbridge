import Foundation
import Photos

/// Exports Photos originals — including cloud-only ones — using PhotoKit.
///
/// This replaces the AppleScript export path for cloud-only assets. Photos'
/// scripting interface can only read a property off the *whole* `media items`
/// collection quickly; every form of element resolution (`whose filename is`,
/// `media item id`, even a contiguous index range) rescans the library, so
/// selecting a subset to export cost seconds per asset and made a full-library
/// migration take days.
///
/// `PHAsset.fetchAssets(withLocalIdentifiers:)` is an indexed lookup, and
/// `PHAssetResourceManager` downloads cloud originals directly with real
/// per-asset progress.
final class PhotoKitExportService {
    struct ExportedItem {
        let identifier: String
        let filename: String
        let path: String
        let kind: String   // "original" or "pairedVideo"
        let bytes: Int

        var dictionary: [String: Any] {
            ["identifier": identifier, "filename": filename, "path": path,
             "kind": kind, "bytes": bytes]
        }
    }

    struct FailedItem {
        let identifier: String
        let message: String

        var dictionary: [String: Any] {
            ["identifier": identifier, "message": message]
        }
    }

    final class Job {
        let id: String
        let total: Int
        var completed: Int = 0
        var items: [ExportedItem] = []
        var failures: [FailedItem] = []
        var state: String = "running"   // running | done | failed | cancelled
        var message: String = ""
        var cancelled: Bool = false

        init(id: String, total: Int) {
            self.id = id
            self.total = total
        }
    }

    private let stateQueue = DispatchQueue(label: "app.icloudbridge.photokit.state")
    private let workQueue = DispatchQueue(label: "app.icloudbridge.photokit.work")
    private var jobs: [String: Job] = [:]

    /// Photos assigns identifiers of the form "<ZUUID>/L0/001"; the backend
    /// stores only the bare ZUUID.
    private static let identifierSuffix = "/L0/001"

    // MARK: - Authorization

    func authorizationStatus() -> String {
        Self.describe(PHPhotoLibrary.authorizationStatus(for: .readWrite))
    }

    func requestAuthorization(completion: @escaping (String) -> Void) {
        PHPhotoLibrary.requestAuthorization(for: .readWrite) { status in
            completion(Self.describe(status))
        }
    }

    private static func describe(_ status: PHAuthorizationStatus) -> String {
        switch status {
        case .authorized: return "authorized"
        case .limited: return "limited"
        case .denied: return "denied"
        case .restricted: return "restricted"
        case .notDetermined: return "notDetermined"
        @unknown default: return "unknown"
        }
    }

    private var isAuthorized: Bool {
        let status = PHPhotoLibrary.authorizationStatus(for: .readWrite)
        return status == .authorized || status == .limited
    }

    // MARK: - Job lifecycle

    /// Why an export could not be started at all (as opposed to per-asset
    /// failures, which are recorded on the job).
    struct StartFailure: Error {
        let message: String

        init(_ message: String) {
            self.message = message
        }
    }

    /// Starts an export and returns immediately with the new job's id.
    func startExport(uuids: [String], destination: URL) -> Result<String, StartFailure> {
        guard isAuthorized else {
            return .failure(StartFailure("Photos access is \(authorizationStatus())"))
        }
        guard !uuids.isEmpty else {
            return .failure(StartFailure("No identifiers supplied"))
        }

        do {
            try FileManager.default.createDirectory(
                at: destination, withIntermediateDirectories: true
            )
        } catch {
            return .failure(StartFailure("Cannot create destination: \(error.localizedDescription)"))
        }

        let job = Job(id: UUID().uuidString, total: uuids.count)
        stateQueue.sync { jobs[job.id] = job }

        workQueue.async { [weak self] in
            self?.run(job: job, uuids: uuids, destination: destination)
        }

        return .success(job.id)
    }

    /// Snapshot of a job. `since` trims already-delivered items so polling a
    /// 28k-asset run does not re-send the whole list every time.
    func snapshot(id: String, since: Int) -> [String: Any]? {
        stateQueue.sync {
            guard let job = jobs[id] else { return nil }
            let start = max(0, min(since, job.items.count))
            return [
                "job_id": job.id,
                "state": job.state,
                "total": job.total,
                "completed": job.completed,
                "exported": job.items.count,
                "failed": job.failures.count,
                "message": job.message,
                "next_cursor": job.items.count,
                "items": job.items[start...].map { $0.dictionary },
                "failures": job.failures.map { $0.dictionary },
            ]
        }
    }

    func cancel(id: String) -> Bool {
        stateQueue.sync {
            guard let job = jobs[id] else { return false }
            job.cancelled = true
            return true
        }
    }

    /// Drops a finished job's bookkeeping once the backend has read it.
    func forget(id: String) {
        _ = stateQueue.sync { jobs.removeValue(forKey: id) }
    }

    // MARK: - Export

    private func run(job: Job, uuids: [String], destination: URL) {
        let assets = resolve(uuids: uuids)

        for uuid in uuids {
            if stateQueue.sync(execute: { job.cancelled }) {
                finish(job, state: "cancelled", message: "Cancelled")
                return
            }

            guard let asset = assets[uuid] else {
                record(job, failure: FailedItem(
                    identifier: uuid, message: "Not found in Photos library"
                ))
                stateQueue.sync { job.completed += 1 }
                continue
            }

            export(asset: asset, uuid: uuid, destination: destination, job: job)
        }

        finish(job, state: "done", message: "")
    }

    /// Indexed lookup first; only assets that miss fall back to a single
    /// enumeration, so the common case stays O(1) per asset.
    private func resolve(uuids: [String]) -> [String: PHAsset] {
        var byUUID: [String: PHAsset] = [:]

        let identifiers = uuids.map { $0 + Self.identifierSuffix }
        let fetched = PHAsset.fetchAssets(withLocalIdentifiers: identifiers, options: nil)
        fetched.enumerateObjects { asset, _, _ in
            byUUID[Self.uuidPrefix(of: asset.localIdentifier)] = asset
        }

        let missing = Set(uuids).subtracting(byUUID.keys)
        guard !missing.isEmpty else { return byUUID }

        // Some assets carry a different resource suffix; one full pass is far
        // cheaper than per-asset AppleScript lookups ever were.
        let all = PHAsset.fetchAssets(with: nil)
        all.enumerateObjects { asset, _, stop in
            let prefix = Self.uuidPrefix(of: asset.localIdentifier)
            if missing.contains(prefix) && byUUID[prefix] == nil {
                byUUID[prefix] = asset
                if byUUID.count == uuids.count { stop.pointee = true }
            }
        }

        return byUUID
    }

    private static func uuidPrefix(of localIdentifier: String) -> String {
        String(localIdentifier.split(separator: "/").first ?? "")
    }

    private func export(asset: PHAsset, uuid: String, destination: URL, job: Job) {
        let resources = PHAssetResource.assetResources(for: asset)
        guard let original = Self.pickOriginal(from: resources) else {
            record(job, failure: FailedItem(
                identifier: uuid, message: "No exportable resource"
            ))
            return
        }

        var exportedOriginal = false
        if let item = write(resource: original, uuid: uuid, kind: "original",
                            destination: destination, job: job) {
            record(job, item: item)
            exportedOriginal = true
        }

        // Live Photos carry a paired video the AppleScript path used to pick up
        // as a stray .mov in the export folder.
        if exportedOriginal,
           let paired = resources.first(where: { $0.type == .pairedVideo }) {
            if let item = write(resource: paired, uuid: uuid, kind: "pairedVideo",
                                destination: destination, job: job) {
                record(job, item: item)
            }
        }

        stateQueue.sync { job.completed += 1 }
    }

    /// Prefers the untouched original over any rendered/edited derivative.
    private static func pickOriginal(from resources: [PHAssetResource]) -> PHAssetResource? {
        let preference: [PHAssetResourceType] = [
            .photo, .video, .audio, .fullSizePhoto, .fullSizeVideo,
        ]
        for type in preference {
            if let match = resources.first(where: { $0.type == type }) {
                return match
            }
        }
        return resources.first
    }

    private func write(
        resource: PHAssetResource,
        uuid: String,
        kind: String,
        destination: URL,
        job: Job
    ) -> ExportedItem? {
        let filename = resource.originalFilename
        let target = Self.uniquePath(in: destination, filename: filename)

        let options = PHAssetResourceRequestOptions()
        options.isNetworkAccessAllowed = true   // downloads cloud-only originals

        let done = DispatchSemaphore(value: 0)
        var writeError: Error?

        PHAssetResourceManager.default().writeData(
            for: resource, toFile: target, options: options
        ) { error in
            writeError = error
            done.signal()
        }
        done.wait()

        if let writeError {
            try? FileManager.default.removeItem(at: target)
            record(job, failure: FailedItem(
                identifier: uuid,
                message: "\(kind): \(writeError.localizedDescription)"
            ))
            return nil
        }

        let bytes = (try? FileManager.default.attributesOfItem(atPath: target.path)[.size])
            .flatMap { $0 as? Int } ?? 0

        return ExportedItem(
            identifier: uuid,
            filename: filename,
            path: target.path,
            kind: kind,
            bytes: bytes
        )
    }

    /// Two assets can share an original filename, so never overwrite.
    private static func uniquePath(in directory: URL, filename: String) -> URL {
        let candidate = directory.appendingPathComponent(filename)
        guard FileManager.default.fileExists(atPath: candidate.path) else {
            return candidate
        }

        let base = (filename as NSString).deletingPathExtension
        let ext = (filename as NSString).pathExtension
        var counter = 2
        while true {
            let name = ext.isEmpty ? "\(base) \(counter)" : "\(base) \(counter).\(ext)"
            let next = directory.appendingPathComponent(name)
            if !FileManager.default.fileExists(atPath: next.path) {
                return next
            }
            counter += 1
        }
    }

    // MARK: - State mutation

    private func record(_ job: Job, item: ExportedItem) {
        stateQueue.sync { job.items.append(item) }
    }

    /// Does not advance `completed`; the caller owns that so an asset is
    /// counted exactly once whether it succeeded, failed or was unresolvable.
    private func record(_ job: Job, failure: FailedItem) {
        stateQueue.sync { job.failures.append(failure) }
    }

    private func finish(_ job: Job, state: String, message: String) {
        stateQueue.sync {
            job.state = state
            job.message = message
        }
    }
}
