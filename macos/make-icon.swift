// Draws every raster of the Memoreei mark (assets/icon.svg): an amber speech bubble
// holding two dots and an i, on a teal tile. Flat fills, no gradient or shadow.
// At 32 px and under it draws the favicon cut (assets/favicon.svg) instead: bigger
// glyphs, tighter bubble, so the i survives. Run on a Mac, from the repo root:
//   swift macos/make-icon.swift && iconutil -c icns macos/build/AppIcon.iconset -o macos/AppIcon.icns
// Writes macos/build/AppIcon.iconset, linux/icons/<size>.png and the dashboard's icon.png.
import AppKit

let teal = NSColor(srgbRed: 0x17 / 255, green: 0x70 / 255, blue: 0x6A / 255, alpha: 1)
let amber = NSColor(srgbRed: 0xF2 / 255, green: 0xA3 / 255, blue: 0x3A / 255, alpha: 1)
let deepTeal = NSColor(srgbRed: 0x0E / 255, green: 0x3B / 255, blue: 0x38 / 255, alpha: 1)

/// The mark in its own units, y down: 128 across, or 32 for the favicon cut.
func drawMark(small: Bool) {
    if small {
        teal.setFill()
        NSBezierPath(roundedRect: NSRect(x: 0, y: 0, width: 32, height: 32), xRadius: 7, yRadius: 7).fill()
        let bubble = NSBezierPath(roundedRect: NSRect(x: 3, y: 6, width: 26, height: 18), xRadius: 7, yRadius: 7)
        bubble.move(to: NSPoint(x: 9, y: 23))
        bubble.line(to: NSPoint(x: 14, y: 23.9))
        bubble.line(to: NSPoint(x: 9, y: 28))
        bubble.close()
        amber.setFill()
        bubble.fill()
        deepTeal.setFill()
        NSBezierPath(ovalIn: NSRect(x: 7.2, y: 12.7, width: 5.6, height: 5.6)).fill()
        NSBezierPath(ovalIn: NSRect(x: 13.2, y: 12.7, width: 5.6, height: 5.6)).fill()
        NSBezierPath(ovalIn: NSRect(x: 20, y: 8.8, width: 4, height: 4)).fill()
        NSBezierPath(roundedRect: NSRect(x: 20, y: 13.6, width: 4, height: 6.4), xRadius: 2, yRadius: 2).fill()
    } else {
        teal.setFill()
        NSBezierPath(roundedRect: NSRect(x: 0, y: 0, width: 128, height: 128), xRadius: 28, yRadius: 28).fill()
        let bubble = NSBezierPath(roundedRect: NSRect(x: 18, y: 30, width: 92, height: 60), xRadius: 26, yRadius: 26)
        bubble.move(to: NSPoint(x: 43.76, y: 89))
        bubble.line(to: NSPoint(x: 57.76, y: 89))
        bubble.line(to: NSPoint(x: 43.76, y: 102))
        bubble.close()
        amber.setFill()
        bubble.fill()
        deepTeal.setFill()
        NSBezierPath(ovalIn: NSRect(x: 34, y: 54, width: 16, height: 16)).fill()
        NSBezierPath(ovalIn: NSRect(x: 56, y: 54, width: 16, height: 16)).fill()
        NSBezierPath(ovalIn: NSRect(x: 80, y: 42, width: 12, height: 12)).fill()
        NSBezierPath(roundedRect: NSRect(x: 80, y: 58, width: 12, height: 18), xRadius: 6, yRadius: 6).fill()
    }
}

/// One square PNG, `pixels` across. `inset`: the tile sits inside Apple's icon grid (824
/// of 1024, so it lines up with other apps in the Dock); otherwise it fills the square.
func render(pixels: Int, inset: Bool) -> Data {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels,
                               bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                               colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    let context = NSGraphicsContext(bitmapImageRep: rep)!
    NSGraphicsContext.current = context
    context.imageInterpolation = .high
    let size = CGFloat(pixels)
    let tile = inset ? size * 824 / 1024 : size
    let origin = inset ? size * 100 / 1024 : 0
    let small = tile <= 32
    let units: CGFloat = small ? 32 : 128
    // Flip to y down, then scale the mark's units onto the tile.
    let t = NSAffineTransform()
    t.translateX(by: origin, yBy: size - origin)
    t.scaleX(by: tile / units, yBy: -tile / units)
    t.concat()
    drawMark(small: small)
    NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using: .png, properties: [:])!
}

let fm = FileManager.default
let iconset = URL(fileURLWithPath: "macos/build/AppIcon.iconset")
try? fm.removeItem(at: iconset)
try! fm.createDirectory(at: iconset, withIntermediateDirectories: true)
for base in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let name = scale == 1 ? "icon_\(base)x\(base).png" : "icon_\(base)x\(base)@2x.png"
        try! render(pixels: base * scale, inset: true).write(to: iconset.appendingPathComponent(name))
    }
}
for size in [16, 32, 48, 64, 128, 256, 512] {
    try! render(pixels: size, inset: false).write(to: URL(fileURLWithPath: "linux/icons/\(size).png"))
}
try! render(pixels: 64, inset: false).write(to: URL(fileURLWithPath: "src/memoreei/admin/static/icon.png"))
