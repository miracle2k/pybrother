#!/usr/bin/env python3
"""
Universal Brother Label Printer
Supports both PNG-based and labelprinterkit-based printing modes
Works with W3.5 • W6 • W9 • W12 • W18 • W24 tapes
"""

import asyncio
import sys
import platform
import struct
import argparse
from PIL import Image, ImageDraw, ImageFont
from pyipp import IPP
from pyipp.enums import IppOperation

# Try importing labelprinterkit (optional dependency)
try:
    from labelprinterkit import BrotherQLPrinter
    LABELPRINTERKIT_AVAILABLE = True
except ImportError:
    LABELPRINTERKIT_AVAILABLE = False

# ──────────────────────────────────────────────────────────────
# Tape catalogue (data from Brother "Raster Command Reference")
TAPE_SPECS = {
    "W3_5": {"mm": 3.5, "media_byte": 0x04, "pins": 24},
    "W6":   {"mm": 6,   "media_byte": 0x06, "pins": 32},
    "W9":   {"mm": 9,   "media_byte": 0x09, "pins": 50},
    "W12":  {"mm": 12,  "media_byte": 0x0C, "pins": 70},
    "W18":  {"mm": 18,  "media_byte": 0x12, "pins": 112},
    "W24":  {"mm": 24,  "media_byte": 0x18, "pins": 128},
}

FEED_PX_PER_MM = 14  # ≅ 360 dpi

# ──────────────────────────────────────────────────────────────
# PNG-based implementation
def create_label_png(text, font_size, tape_key, margin_px):
    """Create PNG with perfect symmetric centering using ink-based measurement"""
    spec = TAPE_SPECS[tape_key]
    tape_h_px = spec["pins"]
    
    # Choose font
    try:
        font_path = ("/System/Library/Fonts/Arial.ttf"
                     if platform.system() == "Darwin"
                     else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
        font = ImageFont.truetype(font_path, font_size)
    except Exception:
        font = ImageFont.load_default()

    # Measure ink only (no bearings)
    mask = Image.new("1", (2000, 1000), 0)
    ImageDraw.Draw(mask).text((0, 0), text, font=font, fill=1)
    left, top, right, bottom = mask.getbbox()
    glyph_w, glyph_h = right-left, bottom-top

    # Create canvas with symmetric margins
    canvas_w = glyph_w + 2*margin_px
    canvas_h = tape_h_px
    img = Image.new("L", (canvas_w, canvas_h), 255)
    draw = ImageDraw.Draw(img)

    # Position text so ink is perfectly centered
    x = margin_px - left
    y = (canvas_h - glyph_h)//2 - top
    draw.text((x, y), text, font=font, fill=0)
    return img, spec

def png_to_bw_matrix(img, threshold=128):
    """Convert PNG to black/white matrix"""
    if img.mode != "L":
        img = img.convert("L")
    w, h = img.size
    data = [
        [1 if img.getpixel((x, y)) < threshold else 0 for x in range(w)]
        for y in range(h)
    ]
    return {"width": w, "height": h, "data": data}

def convert_to_brother_raster(matrix, spec, hi_res=True, feed_mm=1, auto_cut=True):
    """Convert matrix to Brother raster format"""
    w, h = matrix["width"], matrix["height"]
    data = [b"\x00"*400,                     # NULL * 400
            b"\x1B\x40",                     # ESC @
            b"\x1B\x69\x61\x01"]            # ESC i a 01 (raster mode)

    # ESC i z – print-info (tell cassette width)
    data.append(struct.pack("<BBBBBBBBBBBBB",
        0x1B,0x69,0x7A,                   # ESC i z
        0x84,                             # 0x84 = PI_KIND|PI_WIDTH
        0x00,                             # media-type (auto) -> 0
        spec["media_byte"],               # WIDTH byte
        0x00, 0xAA,0x02,0x00,0x00,0x00,0x00))

    # Mode: auto-cut setting
    if auto_cut:
        data.append(b"\x1B\x69\x4D\x40")

    # Advanced mode: hi-res if asked, chain printing control
    adv = 0x40 if hi_res else 0x00        # bit 6
    if not auto_cut:
        adv |= 0x08                       # bit 3 = no-chain-printing
    data.append(b"\x1B\x69\x4B" + bytes([adv]))

    # Feed margin ESC i d (same front & back)
    dots_per_mm = spec["pins"] / spec["mm"]
    margin_dots = int(dots_per_mm * feed_mm)
    data.append(b"\x1B\x69\x64" + struct.pack("<H", margin_dots))

    # Enable TIFF compression
    data.append(b"\x4D\x02")

    # Graphics rows (one per X pixel)
    pins_total = 128                      # print-head columns
    blank_left = (pins_total - spec["pins"]) // 2

    for x in range(w):
        row = bytearray(20)
        row[:4] = b"\x47\x11\x00\x0F"     # 'G' row header
        for y in range(h):
            if matrix["data"][y][x]:
                bitpos = y + blank_left
                byte = 4 + bitpos//8
                row[byte] |= 1 << (7 - (bitpos % 8))
        data.append(bytes(row))

    data.append(b"\x1A")                  # CTRL-Z = print+feed
    return b"".join(data)

# ──────────────────────────────────────────────────────────────
# Labelprinterkit-based implementation
def print_with_labelprinterkit(text, font_size, tape_key, margin_px, copies, printer_ip):
    """Print using labelprinterkit library"""
    if not LABELPRINTERKIT_AVAILABLE:
        raise ImportError("labelprinterkit not available. Install with: pip install labelprinterkit")
    
    # Create PNG using same centering logic
    png, spec = create_label_png(text, font_size, tape_key, margin_px)
    
    # Save PNG for reference
    filename = f"{tape_key}_{text.replace(' ', '_')}_labelprinterkit.png"
    png.save(filename)
    print(f"✓ Saved PNG: {filename}")
    
    # Use labelprinterkit to print
    printer = BrotherQLPrinter(f"ipp://{printer_ip}:631/ipp/print")
    
    # Convert tape key to labelprinterkit format
    tape_size = tape_key.replace("_", ".")  # W6 -> W6, W3_5 -> W3.5
    
    success = printer.print_image(png, tape_size=tape_size, copies=copies)
    return success

# ──────────────────────────────────────────────────────────────
# IPP communication
async def send_via_ipp(binary, copies, printer="192.168.1.175"):
    """Send Brother raster data via IPP"""
    async with IPP(host=printer, port=631, base_path="/ipp/print") as ipp:
        msg = {"operation-attributes-tag":
                   {"requesting-user-name":"python",
                    "job-name":"brother_label",
                    "document-format":"application/octet-stream"},
               "job-attributes-tag": {"copies": copies,
                                      "sides":"one-sided",
                                      "orientation-requested":4},
               "data": binary}
        res = await ipp.execute(IppOperation.PRINT_JOB, msg)
        return res.get("status-code",-1)==0

# ──────────────────────────────────────────────────────────────
def main():
    """Main entry point with mode selection"""
    ap = argparse.ArgumentParser(description="Universal Brother Label Printer")
    ap.add_argument("text", help="label text, quotes for spaces")
    ap.add_argument("-f", "--font", type=int, default=40,
                    help="font size px (default 40)")
    ap.add_argument("-t", "--tape", default="W6",
                    choices=TAPE_SPECS.keys(), help="tape cassette")
    ap.add_argument("-m", "--margin", type=int, default=10,
                    help="left/right margin inside label in px")
    ap.add_argument("-c", "--copies", type=int, default=1)
    ap.add_argument("-p", "--printer", default="192.168.1.175",
                    help="printer IP address")
    ap.add_argument("--mode", choices=["png", "labelprinterkit"], default="png",
                    help="printing mode: png (built-in) or labelprinterkit (library)")
    ap.add_argument("--auto-cut", action="store_true", default=True,
                    help="enable auto-cut (default: enabled)")
    ap.add_argument("--no-auto-cut", action="store_false", dest="auto_cut",
                    help="disable auto-cut")
    
    args = ap.parse_args()

    print(f"Brother Label Printer - Mode: {args.mode}")
    print(f"Text: '{args.text}' | Font: {args.font}px | Tape: {args.tape}")
    
    if args.mode == "labelprinterkit":
        if not LABELPRINTERKIT_AVAILABLE:
            print("❌ labelprinterkit not available")
            print("Install with: pip install labelprinterkit")
            print("Or use --mode png for built-in PNG mode")
            sys.exit(1)
        
        try:
            success = print_with_labelprinterkit(
                args.text, args.font, args.tape, args.margin, 
                args.copies, args.printer
            )
            print("✓ printed" if success else "✗ failed")
        except Exception as e:
            print(f"✗ labelprinterkit error: {e}")
            sys.exit(1)
    
    else:  # PNG mode
        png, spec = create_label_png(args.text, args.font, args.tape, args.margin)
        filename = f"{args.tape}_{args.text.replace(' ','_')}.png"
        png.save(filename)
        print(f"✓ Saved PNG: {filename}")

        matrix = png_to_bw_matrix(png)
        raster = convert_to_brother_raster(matrix, spec, hi_res=True, 
                                         feed_mm=1, auto_cut=args.auto_cut)
        
        bin_filename = f"{args.tape}_{args.text.replace(' ','_')}.bin"
        open(bin_filename, "wb").write(raster)
        print(f"✓ Saved binary: {bin_filename}")

        ok = asyncio.run(send_via_ipp(raster, args.copies, args.printer))
        print("✓ printed" if ok else "✗ failed")

if __name__ == "__main__":
    main()