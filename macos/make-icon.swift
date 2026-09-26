// Draws AppIcon.icns: a speech bubble with a magnifier, on a deep blue squircle.
// Run on a Mac: swift macos/make-icon.swift && iconutil -c icns AppIcon.iconset -o macos/AppIcon.icns
import AppKit

func draw(size: CGFloat) -> NSBitmapImageRep {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(size), pixelsHigh: Int(size),
                               bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                               colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    let s = size / 1024
    // Squircle, inset like Apple's template (824 of 1024).
    let tile = NSRect(x: 100 * s, y: 100 * s, width: 824 * s, height: 824 * s)
    let body = NSBezierPath(roundedRect: tile, xRadius: 185 * s, yRadius: 185 * s)
    NSGraphicsContext.current?.saveGraphicsState()
    let shadow = NSShadow()
    shadow.shadowColor = NSColor.black.withAlphaComponent(0.3)
    shadow.shadowOffset = NSSize(width: 0, height: -10 * s)
    shadow.shadowBlurRadius = 20 * s
    shadow.set()
    NSColor(red: 0.16, green: 0.22, blue: 0.62, alpha: 1).setFill()
    body.fill()
    NSGraphicsContext.current?.restoreGraphicsState()
    NSGradient(starting: NSColor(red: 0.33, green: 0.45, blue: 0.95, alpha: 1),
               ending: NSColor(red: 0.13, green: 0.17, blue: 0.52, alpha: 1))!.draw(in: body, angle: -90)

    // Speech bubble.
    let bubble = NSBezierPath(roundedRect: NSRect(x: 230 * s, y: 330 * s, width: 564 * s, height: 420 * s),
                              xRadius: 150 * s, yRadius: 150 * s)
    let tail = NSBezierPath()
    tail.move(to: NSPoint(x: 330 * s, y: 380 * s))
    tail.line(to: NSPoint(x: 280 * s, y: 250 * s))
    tail.line(to: NSPoint(x: 450 * s, y: 340 * s))
    tail.close()
    bubble.append(tail)
    NSColor.white.setFill()
    bubble.fill()

    // Magnifier inside it.
    let blue = NSColor(red: 0.20, green: 0.30, blue: 0.78, alpha: 1)
    blue.setStroke()
    let lens = NSBezierPath(ovalIn: NSRect(x: 395 * s, y: 450 * s, width: 190 * s, height: 190 * s))
    lens.lineWidth = 44 * s
    lens.stroke()
    let handle = NSBezierPath()
    handle.move(to: NSPoint(x: 568 * s, y: 468 * s))
    handle.line(to: NSPoint(x: 648 * s, y: 388 * s))
    handle.lineWidth = 56 * s
    handle.lineCapStyle = .round
    handle.stroke()
    NSGraphicsContext.restoreGraphicsState()
    return rep
}

let dir = URL(fileURLWithPath: "AppIcon.iconset")
try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
for base in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let name = scale == 1 ? "icon_\(base)x\(base).png" : "icon_\(base)x\(base)@2x.png"
        let data = draw(size: CGFloat(base * scale)).representation(using: .png, properties: [:])!
        try! data.write(to: dir.appendingPathComponent(name))
    }
}
