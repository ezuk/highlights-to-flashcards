// Mac app for Highlights to Flashcards: runs run.sh and shows the app in its own window.
// Closing the window or quitting stops the server.

import AppKit
import WebKit

let appName = "Highlights to Flashcards"

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate {
    private var server: Process?
    private var window: NSWindow!
    private var webView: WKWebView!
    private var appURL: URL?
    private var output = ""
    private var quitting = false
    private var leaving: (() -> Void)?
    private var log: FileHandle?
    private let logURL = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Logs/\(appName).log")

    private var repoURL: URL? {
        guard let path = Bundle.main.object(forInfoDictionaryKey: "H2FRepoPath") as? String else { return nil }
        return URL(fileURLWithPath: path)
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()
        buildWindow()
        guard let repo = repoURL,
              FileManager.default.isExecutableFile(atPath: repo.appendingPathComponent("run.sh").path) else {
            fail("Couldn't find run.sh. Rebuild the app with macos/build.sh from the project folder.")
            return
        }
        startServer(in: repo)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        window.makeKeyAndOrderFront(nil)
        return false
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if quitting { return .terminateLater }
        quitting = true
        // Leaving the page sends edits it hasn't saved yet, so the server stops after that
        leavePage { [weak self] in self?.stopServer() }
        return .terminateLater
    }

    // Window

    private func buildWindow() {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()
        // No word predictions while typing
        if #available(macOS 14.0, *) { config.allowsInlinePredictions = false }
        webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.loadHTMLString(Self.startingPage, baseURL: nil)

        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 860),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = appName
        window.contentView = webView
        window.minSize = NSSize(width: 480, height: 400)
        window.center()
        window.setFrameAutosaveName("Main")
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private static let startingPage = """
        <!doctype html><meta charset="utf-8"><style>:root{color-scheme:light dark}
        body{font:15px -apple-system,sans-serif;display:grid;place-items:center;height:100vh;margin:0;opacity:.6}</style>
        <body>Starting…</body>
        """

    private func show(_ url: URL) {
        guard appURL == nil else { return }
        appURL = url
        webView.load(URLRequest(url: url))
    }

    private func leavePage(then done: @escaping () -> Void) {
        guard appURL != nil, webView.url?.host == appURL?.host else { done(); return }
        leaving = done
        webView.loadHTMLString("", baseURL: nil)
        // In case the page never finishes leaving
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in self?.finishLeaving() }
    }

    private func finishLeaving() {
        let done = leaving
        leaving = nil
        done?()
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        if leaving != nil { finishLeaving() }
    }

    // Links to other sites open in the browser
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url, let app = appURL else { decisionHandler(.allow); return }
        let ours = url.scheme == "about" || (url.host == app.host && url.port == app.port)
        if ours {
            decisionHandler(.allow)
        } else {
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
        }
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = action.request.url { NSWorkspace.shared.open(url) }
        return nil
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        webView.reload()
    }

    // Server

    private func startServer(in repo: URL) {
        FileManager.default.createFile(atPath: logURL.path, contents: nil)
        log = try? FileHandle(forWritingTo: logURL)

        var env = ProcessInfo.processInfo.environment
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let extra = ["\(home)/.local/bin", "\(home)/.cargo/bin", "/opt/homebrew/bin", "/usr/local/bin"]
        let current = (env["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin").split(separator: ":").map(String.init)
        env["PATH"] = (current + extra.filter { !current.contains($0) }).joined(separator: ":")
        // Picks up code changes while the app is open
        env["H2F_LIVE_RELOAD"] = "1"

        let pipe = Pipe()
        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { handle.readabilityHandler = nil; return }
            DispatchQueue.main.async { self?.serverSaid(data) }
        }

        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/sh")
        process.arguments = [repo.appendingPathComponent("run.sh").path, "--no-open"]
        process.currentDirectoryURL = repo
        process.environment = env
        process.standardOutput = pipe
        process.standardError = pipe
        process.terminationHandler = { [weak self] p in
            DispatchQueue.main.async { self?.serverEnded(status: p.terminationStatus) }
        }
        do {
            try process.run()
            server = process
        } catch {
            fail("Couldn't start the server: \(error.localizedDescription)")
        }
    }

    // Writes the server's output to the log and opens the address it prints
    private func serverSaid(_ data: Data) {
        log?.write(data)
        guard appURL == nil else { return }
        output += String(decoding: data, as: UTF8.self)
        guard let match = output.range(of: #"https?://[^\s(]+"#, options: .regularExpression) else { return }
        var text = String(output[match])
        while text.hasSuffix(".") { text.removeLast() }
        if let url = URL(string: text) { show(url) }
    }

    private func stopServer() {
        guard let server, server.isRunning else {
            NSApp.reply(toApplicationShouldTerminate: true)
            return
        }
        server.terminate()
        // Force-stops a server still finishing work after a minute
        DispatchQueue.main.asyncAfter(deadline: .now() + 60) {
            if server.isRunning { kill(server.processIdentifier, SIGKILL) }
        }
    }

    private func serverEnded(status: Int32) {
        if quitting {
            if leaving == nil { NSApp.reply(toApplicationShouldTerminate: true) }
            return
        }
        // Another copy was already running: keep showing it
        if status == 0 && appURL != nil && output.contains("already running") { return }
        if status == 0 {
            NSApp.terminate(nil)
        } else {
            fail("The server stopped unexpectedly (code \(status)).\n\n\(logTail())")
        }
    }

    // Menus and helpers

    @objc func openInBrowser() {
        if let url = webView.url ?? appURL, url.scheme?.hasPrefix("http") == true { NSWorkspace.shared.open(url) }
    }

    @objc func reloadPage() {
        if let appURL, webView.url?.host != appURL.host { webView.load(URLRequest(url: appURL)) } else { webView.reload() }
    }

    @objc func showLog() {
        NSWorkspace.shared.open(logURL)
    }

    private func logTail() -> String {
        guard let text = try? String(contentsOf: logURL, encoding: .utf8) else { return "" }
        return text.split(separator: "\n").suffix(8).joined(separator: "\n")
    }

    private func fail(_ message: String) {
        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = "\(appName) couldn't run"
        alert.informativeText = message
        alert.addButton(withTitle: "Quit")
        alert.addButton(withTitle: "Show Log")
        if alert.runModal() == .alertSecondButtonReturn { showLog() }
        if let server, server.isRunning { server.terminate() }
        exit(1)
    }

    private func menu(_ title: String, _ items: [NSMenuItem]) -> NSMenuItem {
        let item = NSMenuItem()
        let menu = NSMenu(title: title)
        items.forEach(menu.addItem)
        item.submenu = menu
        return item
    }

    private func item(_ title: String, _ action: Selector, _ key: String, _ mods: NSEvent.ModifierFlags = .command) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
        item.keyEquivalentModifierMask = mods
        return item
    }

    private func buildMenu() {
        let main = NSMenu()
        main.addItem(menu(appName, [
            item("Hide \(appName)", #selector(NSApplication.hide(_:)), "h"),
            item("Hide Others", #selector(NSApplication.hideOtherApplications(_:)), "h", [.command, .option]),
            item("Show All", #selector(NSApplication.unhideAllApplications(_:)), ""),
            .separator(),
            item("Quit \(appName)", #selector(NSApplication.terminate(_:)), "q"),
        ]))
        main.addItem(menu("Edit", [
            item("Undo", Selector(("undo:")), "z"),
            item("Redo", Selector(("redo:")), "z", [.command, .shift]),
            .separator(),
            item("Cut", #selector(NSText.cut(_:)), "x"),
            item("Copy", #selector(NSText.copy(_:)), "c"),
            item("Paste", #selector(NSText.paste(_:)), "v"),
            item("Select All", #selector(NSText.selectAll(_:)), "a"),
        ]))
        main.addItem(menu("View", [
            item("Reload", #selector(reloadPage), "r"),
            item("Open in Browser", #selector(openInBrowser), "o"),
            .separator(),
            item("Show Log", #selector(showLog), "l"),
        ]))
        let windowMenu = menu("Window", [
            item("Minimize", #selector(NSWindow.performMiniaturize(_:)), "m"),
            item("Zoom", #selector(NSWindow.performZoom(_:)), ""),
            .separator(),
            item("Close", #selector(NSWindow.performClose(_:)), "w"),
        ])
        main.addItem(windowMenu)
        NSApp.mainMenu = main
        NSApp.windowsMenu = windowMenu.submenu
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
