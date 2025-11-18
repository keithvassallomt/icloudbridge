import Cocoa

final class StatusIconProvider {
    private let icon: NSImage?

    init() {
        icon = StatusIconProvider.loadImage(named: "icloudbridge_menubar")
    }

    private static func loadImage(named resource: String) -> NSImage? {
        // Look in main bundle Resources directory (works for both dev and release builds)
        if let url = Bundle.main.url(forResource: resource, withExtension: "pdf") {
            return NSImage(contentsOf: url)
        }

        NSLog("Unable to locate status icon resource: \(resource) in bundle: \(Bundle.main.bundlePath)")
        NSLog("Resource URL: \(String(describing: Bundle.main.resourceURL))")
        return nil
    }

    func image() -> NSImage? {
        icon
    }
}
