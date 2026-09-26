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
    private let statusLabel = NSTextField(labelWithString: "")
    private let spinner = NSProgressIndicator()
    private let continueButton = NSButton(title: "Continue", target: nil, action: nil)
    private let onDone: (_ granted: Bool) -> Void

    init(onDone: @escaping (_ granted: Bool) -> Void) {
        self.onDone = onDone
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 560, height: 400),
            styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "Welcome to Memoreei"
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
        title.font = .boldSystemFont(ofSize: 20)

        let intro = wrapping("""
            Memoreei makes your iMessages searchable by the AI apps you connect to it. \
            macOS keeps Messages private until you give Memoreei Full Disk Access, and it \
            can't ask for that itself, so here's how:
            """)
        let steps = wrapping("""
            1.  Click Open \(settings). It opens at Full Disk Access.
            2.  If the lock at the bottom is closed, click it.
            3.  Drag the Memoreei icon on the right into the list.
            4.  Make sure the switch next to Memoreei is on. After an
                 update it's already listed, just switched off.
            """)

        let icon = DraggableAppIcon()
        let iconCaption = NSTextField(labelWithString: "Drag me into the list")
        iconCaption.font = .systemFont(ofSize: 11)
        iconCaption.textColor = .secondaryLabelColor
        iconCaption.alignment = .center
        let iconStack = NSStackView(views: [icon, iconCaption])
        iconStack.orientation = .vertical
        iconStack.spacing = 4

        let stepsRow = NSStackView(views: [steps, iconStack])
        stepsRow.alignment = .centerY
        stepsRow.spacing = 16

        let firewall = wrapping("""
            If macOS offers to “Quit & Reopen” Memoreei, go ahead: it picks up where it left \
            off. macOS may also ask whether “Memoreei Server” may accept incoming network \
            connections. Click Allow, so your other devices can reach it.
            """)
        firewall.textColor = .secondaryLabelColor

        let openButton = NSButton(title: "Open \(settings)", target: self, action: #selector(openSettings))
        openButton.bezelStyle = .rounded
        openButton.keyEquivalent = "\r"
        let later = NSButton(title: "Not Now", target: self, action: #selector(notNow))
        later.bezelStyle = .rounded
        continueButton.target = self
        continueButton.action = #selector(finish)
        continueButton.bezelStyle = .rounded
        continueButton.isHidden = true

        spinner.style = .spinning
        spinner.controlSize = .small
        spinner.startAnimation(nil)
        let statusRow = NSStackView(views: [spinner, statusLabel])
        statusRow.spacing = 6

        let buttons = NSStackView(views: [later, NSView(), openButton, continueButton])
        buttons.distribution = .fill

        let stack = NSStackView(views: [title, intro, stepsRow, firewall, statusRow, buttons])
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 16
        stack.edgeInsets = NSEdgeInsets(top: 24, left: 28, bottom: 20, right: 28)
        stack.translatesAutoresizingMaskIntoConstraints = false
        buttons.translatesAutoresizingMaskIntoConstraints = false

        let content = window!.contentView!
        content.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: content.leadingAnchor),
            stack.trailingAnchor.constraint(equalTo: content.trailingAnchor),
            stack.topAnchor.constraint(equalTo: content.topAnchor),
            stack.bottomAnchor.constraint(equalTo: content.bottomAnchor),
            buttons.widthAnchor.constraint(equalTo: stack.widthAnchor, constant: -56),
            intro.widthAnchor.constraint(equalToConstant: 500),
            firewall.widthAnchor.constraint(equalToConstant: 500),
            steps.widthAnchor.constraint(equalToConstant: 390),
            icon.widthAnchor.constraint(equalToConstant: 96),
            icon.heightAnchor.constraint(equalToConstant: 96),
        ])
        waiting()
    }

    private func wrapping(_ text: String) -> NSTextField {
        let field = NSTextField(wrappingLabelWithString: text)
        field.font = .systemFont(ofSize: 13)
        return field
    }

    private func waiting() {
        statusLabel.stringValue = "Waiting for Full Disk Access…"
        statusLabel.textColor = .secondaryLabelColor
    }

    func show() {
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 1.5, repeats: true) { [weak self] _ in
            self?.poll()
        }
        poll()
    }

    private func poll() {
        guard DiskAccess.check() == .granted else { return }
        timer?.invalidate()
        spinner.stopAnimation(nil)
        spinner.isHidden = true
        statusLabel.stringValue = "✓  Memoreei can read your Messages."
        statusLabel.textColor = .systemGreen
        continueButton.isHidden = false
        continueButton.keyEquivalent = "\r"
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
    }

    @objc private func openSettings() {
        NSWorkspace.shared.open(DiskAccess.paneURL)
    }

    @objc private func notNow() { close(granted: false) }
    @objc private func finish() { close(granted: true) }

    private func close(granted: Bool) {
        timer?.invalidate()
        window?.delegate = nil
        window?.close()
        onDone(granted)
    }

    func windowWillClose(_ notification: Notification) {
        timer?.invalidate()
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
