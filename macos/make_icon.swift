// Draws the app icon at 1024x1024: swift make_icon.swift out.png

import AppKit

let size: CGFloat = 1024
let out = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "icon.png"

func color(_ hex: UInt32, _ alpha: CGFloat = 1) -> NSColor {
    NSColor(srgbRed: CGFloat((hex >> 16) & 0xFF) / 255, green: CGFloat((hex >> 8) & 0xFF) / 255,
            blue: CGFloat(hex & 0xFF) / 255, alpha: alpha)
}

let ink = color(0x1C1B18)
let paper = color(0xF6F4EE)
let sheet = color(0xFCFBF8)
let hair = color(0xD9D5CA)
let marker = color(0xF2D43D)
let cardBack = color(0x6E8FB5)

let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(size), pixelsHigh: Int(size),
                           bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                           colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
let ctx = NSGraphicsContext.current!.cgContext

// macOS icon grid: 824pt rounded square centered in 1024
let tile = CGRect(x: 100, y: 100, width: 824, height: 824)
let tilePath = NSBezierPath(roundedRect: tile, xRadius: 185, yRadius: 185)

ctx.saveGState()
let tileShadow = NSShadow()
tileShadow.shadowColor = NSColor.black.withAlphaComponent(0.3)
tileShadow.shadowOffset = NSSize(width: 0, height: -12)
tileShadow.shadowBlurRadius = 28
tileShadow.set()
ink.setFill()
tilePath.fill()
ctx.restoreGState()

ctx.saveGState()
tilePath.addClip()
NSGradient(starting: color(0x2E2C27), ending: color(0x16150F))!.draw(in: tile, angle: -90)
ctx.restoreGState()

func card(center: CGPoint, angle: CGFloat, fill: NSColor, draw: (CGRect) -> Void) {
    let w: CGFloat = 560, h: CGFloat = 380
    let rect = CGRect(x: -w / 2, y: -h / 2, width: w, height: h)
    ctx.saveGState()
    ctx.translateBy(x: center.x, y: center.y)
    ctx.rotate(by: angle * .pi / 180)
    let path = NSBezierPath(roundedRect: rect, xRadius: 30, yRadius: 30)
    let shadow = NSShadow()
    shadow.shadowColor = NSColor.black.withAlphaComponent(0.45)
    shadow.shadowOffset = NSSize(width: 0, height: -14)
    shadow.shadowBlurRadius = 30
    ctx.saveGState()
    shadow.set()
    fill.setFill()
    path.fill()
    ctx.restoreGState()
    path.addClip()
    draw(rect)
    ctx.restoreGState()
}

func line(_ x: CGFloat, _ y: CGFloat, _ w: CGFloat, _ c: NSColor, _ h: CGFloat = 22) {
    c.setFill()
    NSBezierPath(roundedRect: CGRect(x: x, y: y - h / 2, width: w, height: h), xRadius: h / 2, yRadius: h / 2).fill()
}

// Back card
card(center: CGPoint(x: 548, y: 600), angle: 9, fill: cardBack) { r in
    line(r.minX + 60, r.maxY - 80, 260, color(0xFFFFFF, 0.35))
}

// Front card: a page of text with one highlighted line
card(center: CGPoint(x: 492, y: 448), angle: -6, fill: sheet) { r in
    paper.setFill()
    CGRect(x: r.minX, y: r.maxY - 70, width: r.width, height: 70).fill()
    hair.setFill()
    CGRect(x: r.minX, y: r.maxY - 72, width: r.width, height: 4).fill()

    let left = r.minX + 56
    let gray = color(0xB9B4A8)
    let rows: [(CGFloat, CGFloat)] = [(r.maxY - 130, 440), (r.maxY - 190, 380), (r.maxY - 250, 448), (r.maxY - 310, 300)]
    for (i, row) in rows.enumerated() {
        if i == 1 {
            marker.setFill()
            let swipe = NSBezierPath(roundedRect: CGRect(x: left - 18, y: row.0 - 26, width: row.1 + 36, height: 52),
                                     xRadius: 10, yRadius: 10)
            swipe.fill()
            line(left, row.0, row.1, ink)
        } else {
            line(left, row.0, row.1, gray)
        }
    }
}

NSGraphicsContext.current = nil
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: out))
