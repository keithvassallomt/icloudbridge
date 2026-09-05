import Foundation

/// The single source of truth for where iCloudBridge's managed Ruby lives.
///
/// `RuntimeInstaller` builds this tree and `BackendProcessManager` points the
/// backend at it, so the layout has to be described in exactly one place.
///
/// The root deliberately avoids `~/Library/Application Support`. `bundle exec`
/// starts its child Ruby with a `RUBYOPT` it constructs itself:
///
///     RUBYOPT=-r<abs path to bundler gem>/lib/bundler/setup
///
/// Ruby splits `RUBYOPT` on whitespace, so a single space anywhere in the
/// Bundler gem's path makes everything after it parse as command-line switches.
/// Under "Application Support" the trailing fragment reads as `-S...`, and Ruby
/// aborts with `invalid switch in RUBYOPT: -S` before the ripper starts. There
/// is no quoting or escaping that avoids this - the path itself must be clean.
enum RubyRuntime {
    /// Runtime root relative to the user's home directory. No whitespace.
    static let relativeRoot = "Library/iCloudBridge/Runtime/Ruby"

    /// The interpreter the managed gems are built against.
    ///
    /// Always name this by absolute path. `bundle exec ruby` resolves the word
    /// "ruby" through PATH, and a GUI-launched app inherits only
    /// /usr/bin:/bin:/usr/sbin:/sbin - where `ruby` is macOS's own 2.6, far too
    /// old for a modern Bundler (it fails with `uninitialized constant
    /// Gem::Resolver::APISet::GemParser`). The `bundle` binstub has an absolute
    /// shebang so it runs correctly either way, which is what makes this easy
    /// to miss: the install succeeds and only the child interpreter is wrong.
    static let brewRuby = URL(fileURLWithPath: "/opt/homebrew/opt/ruby/bin/ruby")

    static var interpreter: URL? {
        FileManager.default.isExecutableFile(atPath: brewRuby.path) ? brewRuby : nil
    }

    /// Bumped when the layout changes, to force a clean rebuild on upgrade.
    static let schemaVersion = "ruby-runtime-v2"

    static var root: URL {
        URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent(relativeRoot, isDirectory: true)
    }

    static var gemHome: URL { root.appendingPathComponent("gems", isDirectory: true) }
    static var binDir: URL { root.appendingPathComponent("bin", isDirectory: true) }
    static var bundleConfig: URL { root.appendingPathComponent(".bundle", isDirectory: true) }
    static var bundleExecutable: URL { binDir.appendingPathComponent("bundle") }
    static var fingerprintFile: URL { root.appendingPathComponent(".fingerprint") }
    static var layoutFile: URL { root.appendingPathComponent(".layout") }
    static var bundlerVersionFile: URL { root.appendingPathComponent(".bundler-version") }

    /// The pre-v2 gem tree, kept only so it can be cleaned up once v2 works.
    static var legacyGemHome: URL {
        URL(fileURLWithPath: NSHomeDirectory())
            .appendingPathComponent("Library/Application Support/iCloudBridge/gems", isDirectory: true)
    }

    /// The invariant this whole layout exists to hold.
    static func isWhitespaceFree(_ url: URL) -> Bool {
        !url.path.contains(where: { $0.isWhitespace })
    }

    /// The Bundler version recorded by a successful install, if there is one.
    static func pinnedBundlerVersion() -> String? {
        guard let raw = try? String(contentsOf: bundlerVersionFile, encoding: .utf8) else { return nil }
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    /// The Bundler version pinned by a `Gemfile.lock`'s `BUNDLED WITH` stanza.
    ///
    /// Without this the app runs whichever Bundler a `brew upgrade` last left
    /// behind, and a mismatch makes Bundler install and re-exec into its own
    /// copy under `BUNDLE_PATH` - which is how the managed gem tree, and its
    /// path, ended up in `RUBYOPT` in the first place.
    static func bundledWithVersion(lockfile: URL) -> String? {
        guard let contents = try? String(contentsOf: lockfile, encoding: .utf8) else { return nil }
        let lines = contents.split(whereSeparator: { $0.isNewline }).map(String.init)
        guard let marker = lines.firstIndex(where: {
            $0.trimmingCharacters(in: .whitespaces) == "BUNDLED WITH"
        }) else { return nil }

        for line in lines[(marker + 1)...] {
            let candidate = line.trimmingCharacters(in: .whitespaces)
            if candidate.isEmpty { continue }
            // A version and nothing else. Leading digit rules out the next
            // stanza's keyword; the rest allows prereleases like "4.1.0.rc1".
            guard candidate.first?.isNumber == true,
                  candidate.allSatisfy({ $0.isLetter || $0.isNumber || $0 == "." || $0 == "-" })
            else { return nil }
            return candidate
        }
        return nil
    }

    /// Environment every managed Bundler invocation needs.
    static func environment(bundleWithout: String = "development test") -> [String: String] {
        var env = [
            "GEM_HOME": gemHome.path,
            "GEM_PATH": gemHome.path,
            "BUNDLE_APP_CONFIG": bundleConfig.path,
            "BUNDLE_PATH": gemHome.path,
            "BUNDLE_WITHOUT": bundleWithout
        ]
        if let interpreter {
            // Name it explicitly for anything that needs the interpreter, and
            // put its directory on PATH so nested `ruby` lookups - the ripper's
            // own subprocesses, native gem tooling - find the same one.
            env["ICLOUDBRIDGE_RUBY_PATH"] = interpreter.path
            env["PATH"] = pathPrepending(interpreter.deletingLastPathComponent())
        }
        return env
    }

    /// `dir` ahead of the inherited PATH, without duplicating it.
    static func pathPrepending(_ dir: URL) -> String {
        let fallback = "/usr/bin:/bin:/usr/sbin:/sbin"
        let existing = ProcessInfo.processInfo.environment["PATH"] ?? fallback
        let entries = existing.split(separator: ":").map(String.init)
        return entries.contains(dir.path) ? existing : "\(dir.path):\(existing)"
    }
}
