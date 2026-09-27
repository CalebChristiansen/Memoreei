// Draws dmg-background.tiff: the disk image window's whole background, 660×400. Cream,
// two pale circles, an amber dotted arrow from the app to Applications, and a caption
// under them. The icons themselves are Finder's, placed by dmg-settings.py at (180, 170)
// and (480, 170), 160 points each. Run on a Mac, from the repo root:
//   swift macos/make-dmg-background.swift && tiffutil -cathidpicheck \
//     macos/build/dmg-bg.png macos/build/dmg-bg@2x.png -out macos/dmg-background.tiff
import AppKit

func color(_ hex: UInt32) -> NSColor {
    NSColor(srgbRed: CGFloat(hex >> 16 & 0xFF) / 255, green: CGFloat(hex >> 8 & 0xFF) / 255,
            blue: CGFloat(hex & 0xFF) / 255, alpha: 1)
}

/// A centred line whose top is `top` points from the top of the window.
func text(_ string: String, size: CGFloat, weight: NSFont.Weight, color c: NSColor, top: CGFloat) {
    let style = NSMutableParagraphStyle()
    style.alignment = .center
    let attrs: [NSAttributedString.Key: Any] = [
        .font: NSFont.systemFont(ofSize: size, weight: weight), .foregroundColor: c, .paragraphStyle: style,
    ]
    let height = ceil(size * 1.35)
    NSAttributedString(string: string, attributes: attrs)
        .draw(in: NSRect(x: 0, y: 400 - top - height, width: 660, height: height))
}

func render(scale: CGFloat, to output: String) {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(660 * scale), pixelsHigh: Int(400 * scale),
                               bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                               colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    rep.size = NSSize(width: 660, height: 400)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    // In points (rep.size does the scaling), y up: the text's way. The shapes flip to
    // y down, the mockup's way.
    NSGraphicsContext.saveGraphicsState()
    let down = NSAffineTransform()
    down.translateX(by: 0, yBy: 400)
    down.scaleX(by: 1, yBy: -1)
    down.concat()

    color(0xFBF3E4).setFill()
    NSRect(x: 0, y: 0, width: 660, height: 400).fill()
    color(0xF3E6CE).setFill()
    NSBezierPath(ovalIn: NSRect(x: 470, y: -30, width: 180, height: 180)).fill()
    NSBezierPath(ovalIn: NSRect(x: 10, y: 290, width: 140, height: 140)).fill()

    // The arrow: a dotted arc, then its head.
    let arc = NSBezierPath()
    arc.move(to: NSPoint(x: 272, y: 164))
    arc.curve(to: NSPoint(x: 358, y: 149), controlPoint1: NSPoint(x: 301.3, y: 142.7), controlPoint2: NSPoint(x: 330, y: 137.7))
    arc.lineWidth = 6
    arc.lineCapStyle = .round
    arc.setLineDash([0, 14], count: 2, phase: 0)
    color(0xF2A33A).setStroke()
    arc.stroke()
    let head = NSBezierPath()
    head.move(to: NSPoint(x: 376.6, y: 145.8))
    head.line(to: NSPoint(x: 384, y: 160))
    head.line(to: NSPoint(x: 368.8, y: 164.9))
    head.lineWidth = 5
    head.lineCapStyle = .round
    head.lineJoinStyle = .round
    head.stroke()
    NSGraphicsContext.restoreGraphicsState()

    text("Drag Memoreei into Applications", size: 18, weight: .bold, color: color(0x0E3B38), top: 290)
    text("Then open it from there. It lives in your menu bar.", size: 13, weight: .regular,
         color: color(0x5E655F), top: 318)
    NSGraphicsContext.restoreGraphicsState()
    try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: output))
}

try? FileManager.default.createDirectory(atPath: "macos/build", withIntermediateDirectories: true)
render(scale: 1, to: "macos/build/dmg-bg.png")
render(scale: 2, to: "macos/build/dmg-bg@2x.png")
