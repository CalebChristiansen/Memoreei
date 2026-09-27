import AppKit

/// Memoreei's colours, and the pictures the app draws itself. The mark is drawn in code
/// rather than loaded: NSImage reads SVG only from macOS 13. assets/menubar-template.svg
/// is the same shape, and the source of truth for it.
enum Brand {
    static let teal = NSColor(srgbRed: 0x17 / 255, green: 0x70 / 255, blue: 0x6A / 255, alpha: 1)
    static let amber = NSColor(srgbRed: 0xF2 / 255, green: 0xA3 / 255, blue: 0x3A / 255, alpha: 1)
    static let deepTeal = NSColor(srgbRed: 0x0E / 255, green: 0x3B / 255, blue: 0x38 / 255, alpha: 1)
    static let cream = NSColor(srgbRed: 0xFB / 255, green: 0xF3 / 255, blue: 0xE4 / 255, alpha: 1)
    static let attention = NSColor(srgbRed: 0xE0 / 255, green: 0x85 / 255, blue: 0x1A / 255, alpha: 1)

    /// Cream in light mode, the near-black teal in dark: the window background.
    static let surface = NSColor(name: nil) { appearance in
        appearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua
            ? NSColor(srgbRed: 0x10 / 255, green: 0x1A / 255, blue: 0x19 / 255, alpha: 1) : cream
    }
    /// The pale panel behind a note: #F3E6CE, or the dark theme's hairline.
    static let panel = NSColor(name: nil) { appearance in
        appearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua
            ? NSColor(srgbRed: 0x26 / 255, green: 0x3A / 255, blue: 0x37 / 255, alpha: 1)
            : NSColor(srgbRed: 0xF3 / 255, green: 0xE6 / 255, blue: 0xCE / 255, alpha: 1)
    }

    /// A menu item's coloured dot.
    static func dot(_ color: NSColor) -> NSImage {
        NSImage(size: NSSize(width: 8, height: 8), flipped: false) { rect in
            color.setFill()
            NSBezierPath(ovalIn: rect).fill()
            return true
        }
    }

    /// The speech bubble in the mark's own units (the icon is 128 across), y down.
    static func bubble() -> NSBezierPath {
        let path = NSBezierPath(roundedRect: NSRect(x: 18, y: 30, width: 92, height: 60), xRadius: 26, yRadius: 26)
        path.move(to: NSPoint(x: 43.76, y: 89))
        path.line(to: NSPoint(x: 57.76, y: 89))
        path.line(to: NSPoint(x: 43.76, y: 102))
        path.close()
        return path
    }

    /// The glyphs inside the bubble: two dots and an i.
    static func glyphs() -> NSBezierPath {
        let path = NSBezierPath()
        path.appendOval(in: NSRect(x: 34, y: 54, width: 16, height: 16))
        path.appendOval(in: NSRect(x: 56, y: 54, width: 16, height: 16))
        path.appendOval(in: NSRect(x: 80, y: 42, width: 12, height: 12))
        path.appendRoundedRect(NSRect(x: 80, y: 58, width: 12, height: 18), xRadius: 6, yRadius: 6)
        return path
    }

    /// The menu-bar icon: the bubble with its glyphs cut out. A template, so macOS tints
    /// it, unless something needs the user: then it's drawn in the label colour with an
    /// orange dot, since a template can't carry a colour of its own.
    static func menuBarImage(attention: Bool) -> NSImage {
        let image = NSImage(size: NSSize(width: 22, height: 18), flipped: true) { _ in
            guard let context = NSGraphicsContext.current else { return false }
            context.saveGraphicsState()
            let transform = NSAffineTransform()
            transform.scale(by: 0.22)
            transform.translateX(by: -14, yBy: -26)
            transform.concat()
            (attention ? NSColor.labelColor : NSColor.black).setFill()
            bubble().fill()
            context.compositingOperation = .destinationOut
            glyphs().fill()
            context.restoreGraphicsState()
            if attention {
                Brand.attention.setFill()
                NSBezierPath(ovalIn: NSRect(x: 15, y: 0, width: 7, height: 7)).fill()
            }
            return true
        }
        image.isTemplate = !attention
        image.accessibilityDescription = attention ? "Memoreei, needs attention" : "Memoreei"
        return image
    }
}
