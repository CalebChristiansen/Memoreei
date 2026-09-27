import AppKit

/// Full Disk Access, as this app sees it. The server is this app's child, so they share
/// one answer: the grant belongs to Memoreei.app.
enum DiskAccess {
    enum Status { case granted, missing, noMessages }

    static func check() -> Status {
        let fd = open(Paths.messagesDB.path, O_RDONLY)
        if fd >= 0 { close(fd); return .granted }
        // Without access even the folder is hidden, so a missing file reads as EPERM.
        return errno == ENOENT ? .noMessages : .missing
    }

    static let paneURL = URL(
        string: "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles")!
}

/// The first-run window: how to give Memoreei Full Disk Access, which no app can ask
/// for. It watches for the grant and moves on by itself once it lands.
final class OnboardingWindow: NSWindowController, NSWindowDelegate {
    private var timer: Timer?
    private var promptObserver: NSObjectProtocol?
    private let statusLabel = NSTextField(labelWithString: "")
    private let waitingDots = TypingDots()
    private let openButton = NSButton(title: "", target: nil, action: nil)
    private let continueButton = NSButton(title: "Continue", target: nil, action: nil)
    private let onDone: (_ granted: Bool) -> Void

    init(onDone: @escaping (_ granted: Bool) -> Void) {
        self.onDone = onDone
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 672, height: 440),
            styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "Welcome to Memoreei"
        window.backgroundColor = Brand.surface
        super.init(window: window)
        window.delegate = self
        window.isReleasedWhenClosed = false
        build()
        window.center()
    }

    required init?(coder: NSCoder) { fatalError() }

    private func build() {
        let settings = AppInfo.settingsName
        let title = NSTextField(labelWithString: "Let Memoreei read your Messages")
        title.font = .systemFont(ofSize: 24, weight: .bold)
        let intro = label("macOS keeps Messages locked. Memoreei needs Full Disk Access, and only you can give it.",
                          size: 15, color: .secondaryLabelColor)

        let steps = NSStackView(views: [
            step(1, bold("Click ", settings == "System Settings" ? "Open System Settings" : "Open System Preferences", ".")),
            step(2, NSAttributedString(string: "Drag the Memoreei icon into the list.", attributes: body)),
            step(3, {
                let text = NSMutableAttributedString(string: "Turn its switch on. ", attributes: body)
                text.append(NSAttributedString(string: "After an update it's already listed, just off.",
                                               attributes: body.merging([.foregroundColor: NSColor.secondaryLabelColor]) { $1 }))
                return text
            }()),
        ])
        steps.orientation = .vertical
        steps.alignment = .leading
        steps.spacing = 12

        let icon = DraggableAppIcon()
        let caption = label("Drag me in", size: 13, color: .secondaryLabelColor)
        caption.alignment = .center
        let iconBox = DashedBox(views: [icon, caption])

        let stepsRow = NSStackView(views: [steps, iconBox])
        stepsRow.alignment = .centerY
        stepsRow.spacing = 24

        let note = Panel(text: {
            let text = NSMutableAttributedString(string: "If macOS offers to ", attributes: small)
            text.append(NSAttributedString(string: "Quit & Reopen", attributes: smallBold))
            text.append(NSAttributedString(string: ", say yes. If it asks about incoming connections, click ", attributes: small))
            text.append(NSAttributedString(string: "Allow", attributes: smallBold))
            text.append(NSAttributedString(string: " so your other devices can reach Memoreei.", attributes: small))
            return text
        }())

        openButton.title = "Open \(settings)"
        openButton.target = self
        openButton.action = #selector(openSettings)
        openButton.bezelStyle = .rounded
        openButton.bezelColor = Brand.teal
        openButton.keyEquivalent = "\r"
        let later = NSButton(title: "Not Now", target: self, action: #selector(notNow))
        later.bezelStyle = .rounded
        continueButton.target = self
        continueButton.action = #selector(finish)
        continueButton.bezelStyle = .rounded
        continueButton.bezelColor = Brand.teal
        continueButton.isHidden = true

        statusLabel.font = .systemFont(ofSize: 13)
        let footer = NSStackView(views: [waitingDots, statusLabel, NSView(), later, openButton, continueButton])
        footer.spacing = 8
        footer.setCustomSpacing(12, after: later)

        let stack = NSStackView(views: [title, intro, stepsRow, note, footer])
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 20
        stack.setCustomSpacing(6, after: title)
        stack.edgeInsets = NSEdgeInsets(top: 16, left: 32, bottom: 24, right: 32)
        stack.translatesAutoresizingMaskIntoConstraints = false

        let content = window!.contentView!
        content.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: content.leadingAnchor),
            stack.trailingAnchor.constraint(equalTo: content.trailingAnchor),
            stack.topAnchor.constraint(equalTo: content.topAnchor),
            stack.bottomAnchor.constraint(equalTo: content.bottomAnchor),
            intro.widthAnchor.constraint(equalTo: stack.widthAnchor, constant: -64),
            stepsRow.widthAnchor.constraint(equalTo: stack.widthAnchor, constant: -64),
            note.widthAnchor.constraint(equalTo: stack.widthAnchor, constant: -64),
            footer.widthAnchor.constraint(equalTo: stack.widthAnchor, constant: -64),
            iconBox.widthAnchor.constraint(equalToConstant: 148),
            icon.widthAnchor.constraint(equalToConstant: 88),
            icon.heightAnchor.constraint(equalToConstant: 88),
        ])
        waiting()
    }

    private let body: [NSAttributedString.Key: Any] = [.font: NSFont.systemFont(ofSize: 15), .foregroundColor: NSColor.labelColor]
    private let small: [NSAttributedString.Key: Any] = [.font: NSFont.systemFont(ofSize: 13), .foregroundColor: NSColor.labelColor]
    private let smallBold: [NSAttributedString.Key: Any] = [.font: NSFont.systemFont(ofSize: 13, weight: .bold), .foregroundColor: NSColor.labelColor]

    private func bold(_ before: String, _ strong: String, _ after: String) -> NSAttributedString {
        let text = NSMutableAttributedString(string: before, attributes: body)
        text.append(NSAttributedString(string: strong, attributes: body.merging([.font: NSFont.systemFont(ofSize: 15, weight: .bold)]) { $1 }))
        text.append(NSAttributedString(string: after, attributes: body))
        return text
    }

    private func step(_ n: Int, _ text: NSAttributedString) -> NSView {
        let field = NSTextField(labelWithAttributedString: text)
        field.lineBreakMode = .byWordWrapping
        field.maximumNumberOfLines = 0
        field.preferredMaxLayoutWidth = 400
        let row = NSStackView(views: [NumberBadge(n), field])
        row.alignment = .top
        row.spacing = 12
        return row
    }

    private func label(_ text: String, size: CGFloat, color: NSColor) -> NSTextField {
        let field = NSTextField(wrappingLabelWithString: text)
        field.font = .systemFont(ofSize: size)
        field.textColor = color
        return field
    }

    private func waiting() {
        statusLabel.stringValue = "Waiting for access…"
        statusLabel.textColor = .secondaryLabelColor
    }

    func show() {
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
        watchForSystemPrompts()
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 1.5, repeats: true) { [weak self] _ in
            self?.poll()
        }
        poll()
    }

    private func poll() {
        guard DiskAccess.check() == .granted else { return }
        timer?.invalidate()
        waitingDots.isHidden = true
        statusLabel.stringValue = "Memoreei can read your Messages."
        statusLabel.textColor = Brand.teal
        statusLabel.font = .systemFont(ofSize: 13, weight: .semibold)
        openButton.isHidden = true
        openButton.keyEquivalent = ""
        continueButton.isHidden = false
        continueButton.keyEquivalent = "\r"
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
    }

    /// The firewall's "accept incoming connections?" prompt appears as the server starts,
    /// over this window, and when it's answered macOS brings back whatever app was in
    /// front before (the browser the DMG came from), burying this window. The prompt is
    /// UserNotificationCenter's, so come back to the front when that steps aside.
    private func watchForSystemPrompts() {
        guard promptObserver == nil else { return }
        promptObserver = NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didDeactivateApplicationNotification, object: nil, queue: .main
        ) { [weak self] note in
            let app = note.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication
            guard app?.bundleIdentifier == "com.apple.UserNotificationCenter" else { return }
            // After macOS has finished handing the front back to the previous app.
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
                guard let window = self?.window, window.isVisible else { return }
                NSApp.activate(ignoringOtherApps: true)
                window.makeKeyAndOrderFront(nil)
            }
        }
    }

    private func stopWatching() {
        if let observer = promptObserver {
            NSWorkspace.shared.notificationCenter.removeObserver(observer)
            promptObserver = nil
        }
    }

    @objc private func openSettings() {
        NSWorkspace.shared.open(DiskAccess.paneURL)
    }

    @objc private func notNow() { close(granted: false) }
    @objc private func finish() { close(granted: true) }

    private func close(granted: Bool) {
        timer?.invalidate()
        stopWatching()
        window?.delegate = nil
        window?.close()
        onDone(granted)
    }

    func windowWillClose(_ notification: Notification) {
        timer?.invalidate()
        stopWatching()
        onDone(DiskAccess.check() == .granted)
    }
}

/// The app's icon, which can be dragged into the Full Disk Access list: it carries a
/// file URL to this very bundle, which is what the list wants.
final class DraggableAppIcon: NSImageView, NSDraggingSource {
    init() {
        super.init(frame: .zero)
        image = NSWorkspace.shared.icon(forFile: Bundle.main.bundlePath)
        imageScaling = .scaleProportionallyUpOrDown
        toolTip = "Drag into the Full Disk Access list"
        unregisterDraggedTypes()  // a source, not a drop target
    }

    required init?(coder: NSCoder) { fatalError() }

    override func mouseDown(with event: NSEvent) {
        let item = NSDraggingItem(pasteboardWriter: Bundle.main.bundleURL as NSURL)
        item.setDraggingFrame(bounds, contents: image)
        beginDraggingSession(with: [item], event: event, source: self)
    }

    func draggingSession(_ session: NSDraggingSession,
                         sourceOperationMaskFor context: NSDraggingContext) -> NSDragOperation {
        .copy
    }

    override func resetCursorRects() { addCursorRect(bounds, cursor: .openHand) }
}

/// A step's number: cream on a teal circle.
final class NumberBadge: NSView {
    private let n: Int
    init(_ n: Int) {
        self.n = n
        super.init(frame: NSRect(x: 0, y: 0, width: 24, height: 24))
        translatesAutoresizingMaskIntoConstraints = false
        widthAnchor.constraint(equalToConstant: 24).isActive = true
        heightAnchor.constraint(equalToConstant: 24).isActive = true
    }
    required init?(coder: NSCoder) { fatalError() }
    override func draw(_ dirtyRect: NSRect) {
        Brand.teal.setFill()
        NSBezierPath(ovalIn: bounds).fill()
        let text = NSAttributedString(string: "\(n)", attributes: [
            .font: NSFont.systemFont(ofSize: 13, weight: .bold), .foregroundColor: Brand.cream,
        ])
        let size = text.size()
        text.draw(at: NSPoint(x: (bounds.width - size.width) / 2, y: (bounds.height - size.height) / 2))
    }
}

/// The amber dashed frame around the icon to drag.
final class DashedBox: NSStackView {
    convenience init(views: [NSView]) {
        self.init()
        setViews(views, in: .center)
        orientation = .vertical
        alignment = .centerX
        spacing = 8
        edgeInsets = NSEdgeInsets(top: 16, left: 0, bottom: 12, right: 0)
    }
    override func draw(_ dirtyRect: NSRect) {
        let path = NSBezierPath(roundedRect: bounds.insetBy(dx: 1, dy: 1), xRadius: 14, yRadius: 14)
        path.lineWidth = 2
        path.setLineDash([6, 4], count: 2, phase: 0)
        Brand.amber.setStroke()
        path.stroke()
    }
}

/// A note on the pale panel colour.
final class Panel: NSView {
    init(text: NSAttributedString) {
        super.init(frame: .zero)
        let field = NSTextField(labelWithAttributedString: text)
        field.lineBreakMode = .byWordWrapping
        field.maximumNumberOfLines = 0
        field.translatesAutoresizingMaskIntoConstraints = false
        addSubview(field)
        NSLayoutConstraint.activate([
            field.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 16),
            field.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -16),
            field.topAnchor.constraint(equalTo: topAnchor, constant: 12),
            field.bottomAnchor.constraint(equalTo: bottomAnchor, constant: -12),
        ])
        translatesAutoresizingMaskIntoConstraints = false
    }
    required init?(coder: NSCoder) { fatalError() }
    override func draw(_ dirtyRect: NSRect) {
        Brand.panel.setFill()
        NSBezierPath(roundedRect: bounds, xRadius: 14, yRadius: 14).fill()
    }
}

/// Three dots fading in turn, like someone typing: waiting for the grant.
final class TypingDots: NSView {
    private var phase = 0
    private var timer: Timer?
    override init(frame: NSRect) {
        super.init(frame: NSRect(x: 0, y: 0, width: 26, height: 14))
        translatesAutoresizingMaskIntoConstraints = false
        widthAnchor.constraint(equalToConstant: 26).isActive = true
        heightAnchor.constraint(equalToConstant: 14).isActive = true
    }
    convenience init() { self.init(frame: .zero) }
    required init?(coder: NSCoder) { fatalError() }
    override func viewDidMoveToWindow() {
        timer?.invalidate()
        guard window != nil, !NSWorkspace.shared.accessibilityDisplayShouldReduceMotion else { return }
        timer = Timer.scheduledTimer(withTimeInterval: 0.4, repeats: true) { [weak self] _ in
            guard let self = self else { return }
            self.phase = (self.phase + 1) % 3
            self.needsDisplay = true
        }
    }
    override func draw(_ dirtyRect: NSRect) {
        let ink = NSColor(name: nil) { appearance in
            appearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua ? Brand.cream : Brand.deepTeal
        }
        for i in 0..<3 {
            ink.withAlphaComponent([1, 0.55, 0.25][(i - phase + 3) % 3]).setFill()
            NSBezierPath(ovalIn: NSRect(x: CGFloat(i) * 10, y: 4, width: 6, height: 6)).fill()
        }
    }
}
