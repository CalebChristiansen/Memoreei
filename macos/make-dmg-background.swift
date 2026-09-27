// Draws dmg-background.tiff: create-dmg's background (app → Applications arrow) with a
// line under the icons saying what to do. Run on a Mac, from the repo root:
//   swift macos/make-dmg-background.swift && tiffutil -cathidpicheck \
//     macos/build/dmg-bg.png macos/build/dmg-bg@2x.png -out macos/dmg-background.tiff
import AppKit

let caption = "Drag Memoreei into the Applications folder to install it"

func render(_ source: String, scale: CGFloat, to output: String) {
    let base = NSImage(contentsOfFile: source)!
    let width = 660 * scale, height = 400 * scale
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(width), pixelsHigh: Int(height),
                               bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                               colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    base.draw(in: NSRect(x: 0, y: 0, width: width, height: height))
    // Below the icon labels (icons at y 170 from the top, 160 points tall).
    let style = NSMutableParagraphStyle()
    style.alignment = .center
    let attrs: [NSAttributedString.Key: Any] = [
        .font: NSFont.systemFont(ofSize: 15 * scale, weight: .medium),
        .foregroundColor: NSColor(white: 0.35, alpha: 1),
        .paragraphStyle: style,
    ]
    caption.draw(in: NSRect(x: 0, y: 62 * scale, width: width, height: 24 * scale), withAttributes: attrs)
    NSGraphicsContext.restoreGraphicsState()
    try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: output))
}

try? FileManager.default.createDirectory(atPath: "macos/build", withIntermediateDirectories: true)
render("macos/dmg-background/create-dmg.png", scale: 1, to: "macos/build/dmg-bg.png")
render("macos/dmg-background/create-dmg@2x.png", scale: 2, to: "macos/build/dmg-bg@2x.png")
