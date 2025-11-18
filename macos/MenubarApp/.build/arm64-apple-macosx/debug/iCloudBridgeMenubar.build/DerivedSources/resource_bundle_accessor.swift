import Foundation

extension Foundation.Bundle {
    static let module: Bundle = {
        let mainPath = Bundle.main.bundleURL.appendingPathComponent("iCloudBridgeMenubar_iCloudBridgeMenubar.bundle").path
        let buildPath = "/Users/keithv/LocalCode/keithvassallomt/iCloudBridge/macos/MenubarApp/.build/arm64-apple-macosx/debug/iCloudBridgeMenubar_iCloudBridgeMenubar.bundle"

        let preferredBundle = Bundle(path: mainPath)

        guard let bundle = preferredBundle ?? Bundle(path: buildPath) else {
            // Users can write a function called fatalError themselves, we should be resilient against that.
            Swift.fatalError("could not load resource bundle: from \(mainPath) or \(buildPath)")
        }

        return bundle
    }()
}