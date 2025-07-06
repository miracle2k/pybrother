#!/usr/bin/env python3
"""
Universal Brother Label Printer
Supports both PNG-based and labelprinterkit-based printing modes
Works with W3.5 • W6 • W9 • W12 • W18 • W24 tapes
"""

import argparse
import asyncio
import os
import platform
import socket
import struct
import sys
import time

from PIL import Image, ImageDraw, ImageFont
from pyipp import IPP
from pyipp.enums import IppOperation

# Try importing optional dependencies
try:
    from labelprinterkit import BrotherQLPrinter

    LABELPRINTERKIT_AVAILABLE = True
except ImportError:
    LABELPRINTERKIT_AVAILABLE = False

try:
    from zeroconf import ServiceBrowser, ServiceListener, Zeroconf

    ZEROCONF_AVAILABLE = True
except ImportError:
    ZEROCONF_AVAILABLE = False

    # Define dummy classes for when zeroconf is not available
    class ServiceListener:
        pass

    ServiceBrowser = None
    Zeroconf = None

# ──────────────────────────────────────────────────────────────
# Tape catalogue (data from Brother "Raster Command Reference")
TAPE_SPECS = {
    "W3_5": {"mm": 3.5, "media_byte": 0x04, "pins": 24},
    "W6": {"mm": 6, "media_byte": 0x06, "pins": 32},
    "W9": {"mm": 9, "media_byte": 0x09, "pins": 50},
    "W12": {"mm": 12, "media_byte": 0x0C, "pins": 70},
    "W18": {"mm": 18, "media_byte": 0x12, "pins": 112},
    "W24": {"mm": 24, "media_byte": 0x18, "pins": 128},
}

FEED_PX_PER_MM = 14  # ≅ 360 dpi


# ──────────────────────────────────────────────────────────────
# PNG-based implementation
def create_label_png(text, font_size, tape_key, margin_px, white_tape=False):
    """Create PNG with perfect symmetric centering using ink-based measurement

    Args:
        white_tape: If True, creates white background with black text (for white tapes)
                   If False, creates black background with white text (for black tapes)
    """
    spec = TAPE_SPECS[tape_key]
    tape_h_px = spec["pins"]

    # Choose font
    try:
        font_path = (
            "/System/Library/Fonts/Arial.ttf"
            if platform.system() == "Darwin"
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        )
        font = ImageFont.truetype(font_path, font_size)
    except Exception:
        font = ImageFont.load_default()

    # Measure ink only (no bearings)
    mask = Image.new("1", (2000, 1000), 0)
    ImageDraw.Draw(mask).text((0, 0), text, font=font, fill=1)
    left, top, right, bottom = mask.getbbox()
    glyph_w, glyph_h = right - left, bottom - top

    # Create canvas with symmetric margins
    canvas_w = glyph_w + 2 * margin_px
    canvas_h = tape_h_px

    # Set colors based on tape type
    if white_tape:
        # White tape: white background (255), black text (0)
        bg_color = 255
        text_color = 0
    else:
        # Black tape: black background (0), white text (255)
        bg_color = 0
        text_color = 255

    img = Image.new("L", (canvas_w, canvas_h), bg_color)
    draw = ImageDraw.Draw(img)

    # Position text so ink is perfectly centered
    x = margin_px - left
    y = (canvas_h - glyph_h) // 2 - top
    draw.text((x, y), text, font=font, fill=text_color)
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
    data = [
        b"\x00" * 400,  # NULL * 400
        b"\x1b\x40",  # ESC @
        b"\x1b\x69\x61\x01",
    ]  # ESC i a 01 (raster mode)

    # ESC i z – print-info (tell cassette width)
    data.append(
        struct.pack(
            "<BBBBBBBBBBBBB",
            0x1B,
            0x69,
            0x7A,  # ESC i z
            0x84,  # 0x84 = PI_KIND|PI_WIDTH
            0x00,  # media-type (auto) -> 0
            spec["media_byte"],  # WIDTH byte
            0x00,
            0xAA,
            0x02,
            0x00,
            0x00,
            0x00,
            0x00,
        )
    )

    # Mode: auto-cut setting
    if auto_cut:
        data.append(b"\x1b\x69\x4d\x40")

    # Advanced mode: hi-res if asked, chain printing control
    adv = 0x40 if hi_res else 0x00  # bit 6
    if not auto_cut:
        adv |= 0x08  # bit 3 = no-chain-printing
    data.append(b"\x1b\x69\x4b" + bytes([adv]))

    # Feed margin ESC i d (same front & back)
    dots_per_mm = spec["pins"] / spec["mm"]
    margin_dots = int(dots_per_mm * feed_mm)
    data.append(b"\x1b\x69\x64" + struct.pack("<H", margin_dots))

    # Enable TIFF compression
    data.append(b"\x4d\x02")

    # Graphics rows (one per X pixel)
    pins_total = 128  # print-head columns
    blank_left = (pins_total - spec["pins"]) // 2

    for x in range(w):
        row = bytearray(20)
        row[:4] = b"\x47\x11\x00\x0f"  # 'G' row header
        for y in range(h):
            if matrix["data"][y][x]:
                bitpos = y + blank_left
                byte = 4 + bitpos // 8
                row[byte] |= 1 << (7 - (bitpos % 8))
        data.append(bytes(row))

    data.append(b"\x1a")  # CTRL-Z = print+feed
    return b"".join(data)


# ──────────────────────────────────────────────────────────────
# Labelprinterkit-based implementation
def print_with_labelprinterkit(
    text, font_size, tape_key, margin_px, copies, printer_ip, white_tape=False
):
    """Print using labelprinterkit library"""
    if not LABELPRINTERKIT_AVAILABLE:
        raise ImportError(
            "labelprinterkit not available. Install with: pip install labelprinterkit"
        )

    # Create PNG using same centering logic
    png, spec = create_label_png(text, font_size, tape_key, margin_px, white_tape)

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
# Auto-discovery functions
class PrinterDiscoveryListener(ServiceListener):
    """Zeroconf service listener for Brother printers"""

    def __init__(self):
        self.printers = []

    def add_service(self, zeroconf, service_type, name):
        info = zeroconf.get_service_info(service_type, name)
        if info and "brother" in name.lower():
            # Extract IP address
            if info.addresses:
                ip = socket.inet_ntoa(info.addresses[0])
                printer_info = {
                    "name": name,
                    "ip": ip,
                    "port": info.port,
                    "properties": info.properties,
                }
                self.printers.append(printer_info)
                print(f"Found Brother printer: {name} at {ip}:{info.port}")


def discover_brother_printers(timeout=5):
    """Discover Brother printers on the network using mDNS/Zeroconf"""
    if not ZEROCONF_AVAILABLE:
        print("Warning: zeroconf not available for auto-discovery")
        print("Install with: pip install zeroconf")
        return []

    print(f"Scanning for Brother printers ({timeout}s timeout)...")
    zeroconf = Zeroconf()
    listener = PrinterDiscoveryListener()

    # Brother printers typically advertise IPP services
    services = [
        "_ipp._tcp.local.",
        "_printer._tcp.local.",
        "_pdl-datastream._tcp.local.",
    ]
    browsers = []

    try:
        for service in services:
            browser = ServiceBrowser(zeroconf, service, listener)
            browsers.append(browser)

        # Wait for discovery
        time.sleep(timeout)

        return listener.printers

    finally:
        zeroconf.close()


# ──────────────────────────────────────────────────────────────
# Auto-detection functions
async def detect_tape_size(printer_ip):
    """Auto-detect tape size from printer configuration"""
    try:
        async with IPP(host=printer_ip, port=631, base_path="/ipp/print") as ipp:
            printer_info = await ipp.printer()

            # Check printer attributes for media information
            attrs = printer_info.printer_attributes

            # Look for media-ready or media-default attributes
            media_ready = attrs.get("media-ready", [])
            media_default = attrs.get("media-default", "")
            media_supported = attrs.get("media-supported", [])

            print(f"Media ready: {media_ready}")
            print(f"Media default: {media_default}")
            print(f"Media supported: {media_supported}")

            # Try to extract tape width from media names
            # Brother printers often report media like "w6mm" or "oe6mm"
            for media in media_ready + [media_default] + media_supported:
                if not media:
                    continue
                media_str = str(media).lower()

                # Match common Brother tape formats
                if "w3.5mm" in media_str or "3.5mm" in media_str:
                    return "W3_5"
                elif "w6mm" in media_str or "6mm" in media_str:
                    return "W6"
                elif "w9mm" in media_str or "9mm" in media_str:
                    return "W9"
                elif "w12mm" in media_str or "12mm" in media_str:
                    return "W12"
                elif "w18mm" in media_str or "18mm" in media_str:
                    return "W18"
                elif "w24mm" in media_str or "24mm" in media_str:
                    return "W24"

            # If no specific width found, check printer model for common defaults
            printer_name = attrs.get("printer-name", "").lower()
            if "pt-p750w" in printer_name:
                print("Detected PT-P750W, defaulting to W6 (6mm)")
                return "W6"

            return None

    except Exception as e:
        print(f"Warning: Could not auto-detect tape size: {e}")
        return None


# ──────────────────────────────────────────────────────────────
# IPP communication
async def send_via_ipp(binary, copies, printer=None):
    """Send Brother raster data via IPP"""
    if printer is None:
        raise ValueError("Printer IP address must be specified")
    async with IPP(host=printer, port=631, base_path="/ipp/print") as ipp:
        msg = {
            "operation-attributes-tag": {
                "requesting-user-name": "python",
                "job-name": "brother_label",
                "document-format": "application/octet-stream",
            },
            "job-attributes-tag": {
                "copies": copies,
                "sides": "one-sided",
                "orientation-requested": 4,
            },
            "data": binary,
        }
        res = await ipp.execute(IppOperation.PRINT_JOB, msg)
        return res.get("status-code", -1) == 0


# ──────────────────────────────────────────────────────────────
def main():
    """Main entry point with mode selection"""
    ap = argparse.ArgumentParser(description="Universal Brother Label Printer")
    ap.add_argument("text", help="label text, quotes for spaces")
    ap.add_argument(
        "-f", "--font", type=int, default=40, help="font size px (default 40)"
    )
    ap.add_argument(
        "-t",
        "--tape",
        default=None,
        choices=TAPE_SPECS.keys(),
        help="tape cassette (auto-detected if not specified)",
    )
    ap.add_argument(
        "-m",
        "--margin",
        type=int,
        default=10,
        help="left/right margin inside label in px",
    )
    ap.add_argument("-c", "--copies", type=int, default=1)
    ap.add_argument(
        "-p",
        "--printer",
        default=None,
        help="printer IP address (auto-discovered if not specified)",
    )
    ap.add_argument(
        "--mode",
        choices=["png", "labelprinterkit"],
        default="png",
        help="printing mode: png (built-in) or labelprinterkit (library)",
    )
    ap.add_argument(
        "--auto-cut",
        action="store_true",
        default=True,
        help="enable auto-cut (default: enabled)",
    )
    ap.add_argument(
        "--no-auto-cut", action="store_false", dest="auto_cut", help="disable auto-cut"
    )
    ap.add_argument(
        "--no-auto-detect",
        action="store_true",
        help="disable auto-detection of tape size",
    )
    ap.add_argument(
        "--no-discover",
        action="store_true",
        help="disable auto-discovery of printer IP",
    )
    ap.add_argument(
        "--white-tape",
        action="store_true",
        help="use white tape mode (black text on white background)",
    )

    args = ap.parse_args()

    print(f"Brother Label Printer - Mode: {args.mode}")

    # Auto-discover printer IP if not specified
    printer_ip = args.printer
    if not printer_ip and not args.no_discover:
        print("Auto-discovering Brother printers...")
        printers = discover_brother_printers(timeout=5)
        if printers:
            printer_ip = printers[0]["ip"]
            print(f"✓ Using printer: {printers[0]['name']} at {printer_ip}")
            if len(printers) > 1:
                print(f"Note: Found {len(printers)} printers, using first one")
        else:
            # Try environment variable, then error if not found
            default_ip = os.getenv("BROTHER_PRINTER_IP")
            if default_ip:
                print(
                    f"⚠ No Brother printers found, using BROTHER_PRINTER_IP: {default_ip}"
                )
                printer_ip = default_ip
            else:
                print("❌ No Brother printers found and no default IP configured")
                print(
                    "Set BROTHER_PRINTER_IP environment variable or use --printer option"
                )
                sys.exit(1)
    elif not printer_ip:
        # Try environment variable, then error if not found
        default_ip = os.getenv("BROTHER_PRINTER_IP")
        if default_ip:
            print(f"No printer IP specified, using BROTHER_PRINTER_IP: {default_ip}")
            printer_ip = default_ip
        else:
            print("❌ No printer IP specified and no default IP configured")
            print("Set BROTHER_PRINTER_IP environment variable or use --printer option")
            sys.exit(1)

    # Auto-detect tape size if not specified
    tape_size = args.tape
    if not tape_size and not args.no_auto_detect:
        print("Auto-detecting tape size...")
        tape_size = asyncio.run(detect_tape_size(printer_ip))
        if tape_size:
            print(f"✓ Detected tape: {tape_size}")
        else:
            print("⚠ Could not auto-detect tape size, defaulting to W6")
            tape_size = "W6"
    elif not tape_size:
        print("No tape size specified, defaulting to W6")
        tape_size = "W6"

    tape_type = "white" if args.white_tape else "black"
    print(
        f"Text: '{args.text}' | Font: {args.font}px | Tape: {tape_size} ({tape_type})"
    )

    if args.mode == "labelprinterkit":
        if not LABELPRINTERKIT_AVAILABLE:
            print("❌ labelprinterkit not available")
            print("Install with: pip install labelprinterkit")
            print("Or use --mode png for built-in PNG mode")
            sys.exit(1)

        try:
            success = print_with_labelprinterkit(
                args.text,
                args.font,
                tape_size,
                args.margin,
                args.copies,
                printer_ip,
                args.white_tape,
            )
            print("✓ printed" if success else "✗ failed")
        except Exception as e:
            print(f"✗ labelprinterkit error: {e}")
            sys.exit(1)

    else:  # PNG mode
        png, spec = create_label_png(
            args.text, args.font, tape_size, args.margin, args.white_tape
        )
        filename = f"{tape_size}_{args.text.replace(' ','_')}.png"
        png.save(filename)
        print(f"✓ Saved PNG: {filename}")

        matrix = png_to_bw_matrix(png)
        raster = convert_to_brother_raster(
            matrix, spec, hi_res=True, feed_mm=1, auto_cut=args.auto_cut
        )

        bin_filename = f"{tape_size}_{args.text.replace(' ','_')}.bin"
        open(bin_filename, "wb").write(raster)
        print(f"✓ Saved binary: {bin_filename}")

        ok = asyncio.run(send_via_ipp(raster, args.copies, printer_ip))
        print("✓ printed" if ok else "✗ failed")


if __name__ == "__main__":
    main()
