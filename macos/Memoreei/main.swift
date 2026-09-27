import AppKit

/// Memoreei in the menu bar. The server runs as long as the app does: quitting one stops
/// the other, and "Start at login" brings both back.
final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private let server = Server()
    private let updates = Updates()
    private var statusItem: NSStatusItem!
    private var onboarding: OnboardingWindow?
    private var openWhenReady = false

    private let statusLine = NSMenuItem(title: "Starting…", action: nil, keyEquivalent: "")
    private let accessItem = NSMenuItem(title: "Set Up Full Disk Access…", action: #selector(showOnboarding), keyEquivalent: "")
    private let openItem = NSMenuItem(title: "Open Memoreei…", action: #selector(openDashboard), keyEquivalent: "o")
    private let retryItem = NSMenuItem(title: "Start Server", action: #selector(retry), keyEquivalent: "")
    private let loginItem = NSMenuItem(title: "Start at Login", action: #selector(toggleLogin), keyEquivalent: "")
    private let logItem = NSMenuItem(title: "Show Log", action: #selector(showLog), keyEquivalent: "")
    private let updateItem = NSMenuItem(title: "Update Available…", action: #selector(openUpdate), keyEquivalent: "")
    private let aboutItem = NSMenuItem(title: "About Memoreei", action: #selector(about), keyEquivalent: "")
    private let quitItem = NSMenuItem(title: "Quit Memoreei", action: #selector(quit), keyEquivalent: "q")

    func applicationDidFinishLaunching(_ notification: Notification) {
        Paths.ensureDirectories()
        guard claimInstance() else { return }

        buildMenu()
        server.onChange = { [weak self] _ in self?.serverChanged() }
        updates.onChange = { [weak self] in self?.refreshMenu() }
        DistributedNotificationCenter.default().addObserver(
            self, selector: #selector(otherCopyAskedToShow), name: Signals.show, object: nil,
            suspensionBehavior: .deliverImmediately)

        // Opened by hand (not by launchd at login): the user wants to see something.
        if !LaunchAgent.launchedByLaunchd { openWhenReady = true }
        if FileManager.default.fileExists(atPath: Signals.openOnStart.path) {
            try? FileManager.default.removeItem(at: Signals.openOnStart)
            openWhenReady = true
        }

        Log.app("Memoreei \(AppInfo.version) starting\(LaunchAgent.launchedByLaunchd ? " (launchd)" : "")")
        server.start()
        updates.start()
        if DiskAccess.check() == .missing {
            openWhenReady = false
            showOnboarding()
        }
    }

    /// One copy runs at a time. A second copy opened by hand wakes the first and leaves;
    /// a hand-opened copy with Start at Login on hands over to launchd's copy, so the
    /// running server is always the one launchd restarts after a crash.
    private func claimInstance() -> Bool {
        // Straight off the disk image, or from the read-only copy macOS runs a downloaded
        // app from until it's moved: Start at Login would point at a path that goes away.
        if !LaunchAgent.launchedByLaunchd && Paths.bundleIsReadOnly {
            Log.app("running from a read-only volume (\(Bundle.main.bundlePath)); asking to be moved")
            alert("Move Memoreei to Applications first",
                  "Memoreei is running from the disk image, or from a temporary copy macOS " +
                  "made of it. Drag it into the Applications folder and open it from there.")
            exit(0)
        }
        if LaunchAgent.launchedByLaunchd {
            if InstanceLock.acquire(waiting: 15) { return true }
            Log.app("another copy is running; launchd's copy is leaving")
            exit(0)
        }
        if !InstanceLock.tryAcquire() {
            Signals.postShow()
            exit(0)
        }
        if LaunchAgent.isInstalled {
            // The agent starts another copy (this one was moved, or is a newer one kept
            // somewhere else): the copy that was opened is the one that runs from now on.
            if !LaunchAgent.startsThisCopy {
                do {
                    try LaunchAgent.repoint()
                    Log.app("Start at Login now starts \(Bundle.main.bundlePath)")
                } catch {
                    Log.app("couldn't point Start at Login here (\(error)); running without it")
                    return true
                }
            }
            FileManager.default.createFile(atPath: Signals.openOnStart.path, contents: nil)
            LaunchAgent.handOver()
            exit(0)
        }
        return true
    }

    // MARK: - Menu

    private func buildMenu() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = statusItem.button {
            if let image = NSImage(systemSymbolName: "brain", accessibilityDescription: "Memoreei") {
                image.isTemplate = true
                button.image = image
            } else {
                button.title = "M"
            }
            button.toolTip = "Memoreei"
        }
        let menu = NSMenu()
        menu.delegate = self
        menu.autoenablesItems = false
        statusLine.isEnabled = false
        for item in [accessItem, openItem, retryItem, loginItem, logItem, updateItem, aboutItem, quitItem] {
            item.target = self
        }
        for item in [statusLine, accessItem, openItem, retryItem, .separator(), loginItem, logItem,
                     updateItem, .separator(), aboutItem, quitItem] {
            menu.addItem(item)
        }
        statusItem.menu = menu
        refreshMenu()
    }

    func menuWillOpen(_ menu: NSMenu) { refreshMenu() }

    private func refreshMenu() {
        switch server.state {
        case .starting: statusLine.title = "Starting…"
        case .running: statusLine.title = "Running on port \(server.port)"
        case .portInUse(let holder): statusLine.title = "Port \(server.port) is in use by \(holder)"
        case .stopped(let reason): statusLine.title = reason
        }
        openItem.isEnabled = server.state == .running
        retryItem.isHidden = server.state == .running || server.state == .starting
        accessItem.isHidden = DiskAccess.check() != .missing
        loginItem.state = LaunchAgent.isInstalled ? .on : .off
        if let release = updates.available {
            updateItem.title = "Update Available: \(release.version)…"
            updateItem.isHidden = false
        } else {
            updateItem.isHidden = true
        }
        aboutItem.title = "About Memoreei \(AppInfo.version)"
    }

    private func serverChanged() {
        refreshMenu()
        switch server.state {
        case .running:
            if openWhenReady && onboarding == nil {
                openWhenReady = false
                openDashboard()
            }
        case .portInUse(let holder):
            alert("Memoreei can't start",
                  "Port \(server.port) is already in use by \(holder). Memoreei won't start its " +
                  "server while something else holds it. Quit that program (or stop its service), " +
                  "then choose Start Server from the Memoreei menu.")
        default:
            break
        }
    }

    // MARK: - Actions

    @objc private func openDashboard() {
        guard server.state == .running else {
            openWhenReady = true
            return
        }
        Server.runCLI(["admin-url"]) { [weak self] url in
            guard let link = url.flatMap(URL.init(string:)) else {
                self?.alert("Couldn't open the dashboard", "See the log for what went wrong.")
                return
            }
            NSWorkspace.shared.open(link)
        }
    }

    @objc private func otherCopyAskedToShow() {
        if DiskAccess.check() == .missing { showOnboarding() } else { openDashboard() }
    }

    @objc private func showOnboarding() {
        if let existing = onboarding { existing.show(); return }
        let window = OnboardingWindow { [weak self] granted in
            guard let self = self else { return }
            self.onboarding = nil
            self.refreshMenu()
            if granted { self.finishOnboarding() }
        }
        onboarding = window
        window.show()
    }

    /// After the grant: Start at Login goes on by default (handing over to launchd, whose
    /// copy opens the dashboard), unless the user has already chosen.
    private func finishOnboarding() {
        let decidedKey = "startAtLoginDecided"
        if !UserDefaults.standard.bool(forKey: decidedKey) {
            UserDefaults.standard.set(true, forKey: decidedKey)
            if !LaunchAgent.isInstalled {
                enableStartAtLogin(openDashboardAfter: true)
                return
            }
        }
        openDashboard()
    }

    @objc private func toggleLogin() {
        UserDefaults.standard.set(true, forKey: "startAtLoginDecided")
        if LaunchAgent.isInstalled {
            LaunchAgent.remove()
            refreshMenu()
        } else {
            enableStartAtLogin(openDashboardAfter: false)
        }
    }

    private func enableStartAtLogin(openDashboardAfter: Bool) {
        do {
            try LaunchAgent.write()
        } catch {
            alert("Couldn't turn on Start at Login", error.localizedDescription)
            return
        }
        refreshMenu()
        // Already launchd's: the file is all that was missing. Otherwise hand over, so
        // launchd's copy (restarted if it ever crashes) is the one serving from now on.
        if LaunchAgent.launchedByLaunchd {
            if openDashboardAfter { openDashboard() }
            return
        }
        if openDashboardAfter {
            FileManager.default.createFile(atPath: Signals.openOnStart.path, contents: nil)
        }
        server.stop()
        LaunchAgent.handOver()
        exit(0)
    }

    @objc private func retry() { server.restart() }

    @objc private func showLog() {
        NSWorkspace.shared.open(Paths.serverLog)
    }

    @objc private func openUpdate() {
        if let page = updates.available?.page { NSWorkspace.shared.open(page) }
    }

    @objc private func about() {
        NSApp.activate(ignoringOtherApps: true)
        NSApp.orderFrontStandardAboutPanel(options: [
            .applicationVersion: AppInfo.version,
            .version: "",
        ])
    }

    @objc private func quit() {
        NSApp.activate(ignoringOtherApps: true)
        let a = NSAlert()
        a.messageText = "Quit Memoreei?"
        a.informativeText = "Apps that search your memories through Memoreei lose access until " +
            "you open it again" + (LaunchAgent.isInstalled ? ", or until you next log in." : ".")
        a.addButton(withTitle: "Quit")
        a.addButton(withTitle: "Cancel")
        guard a.runModal() == .alertFirstButtonReturn else { return }
        server.stop()
        Log.app("quit")
        exit(0)  // a clean exit: launchd leaves it quit
    }

    private func alert(_ title: String, _ text: String) {
        NSApp.activate(ignoringOtherApps: true)
        let a = NSAlert()
        a.messageText = title
        a.informativeText = text
        a.runModal()
    }

    func applicationWillTerminate(_ notification: Notification) {
        server.stop()
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
signal(SIGTERM, SIG_IGN)
let sigterm = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
sigterm.setEventHandler { NSApp.terminate(nil) }
sigterm.resume()
app.run()
