#!/usr/bin/env python3
"""
Universal Brother Label Printer
Works with W3.5 • W6 • W9 • W12 • W18 • W24 tapes

Font sizing:
- Font size is specified in pixels (dots at 360 dpi)
- Default: auto-sizes to 75% of tape height for safe fit
- Manual: use -f/--font to specify exact pixel size

Printer discovery options:
- Manual IP: --printer 192.168.1.175 (fastest)
- Passive listening: --listen (waits for printer announcements every ~60s)
- Environment variable: export BROTHER_PRINTER_IP=192.168.1.175
"""

import argparse
import asyncio
import os
import platform
import re
import socket
import struct
import sys
import threading
import time

from PIL import Image, ImageDraw, ImageFont
from pyipp import IPP
from pyipp.enums import IppOperation
from pyipp.exceptions import IPPError

# Try importing optional dependencies
try:
    from zeroconf import ServiceBrowser, ServiceListener, Zeroconf, IPVersion

    ZEROCONF_AVAILABLE = True
except ImportError:
    ZEROCONF_AVAILABLE = False

    # Define dummy classes for when zeroconf is not available
    class ServiceListener:
        pass

    ServiceBrowser = None
    Zeroconf = None
    IPVersion = None

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
# Utility functions
def sanitize_filename(text):
    """Remove dangerous characters from filename to prevent path traversal"""
    # Keep only alphanumeric, spaces, hyphens, underscores
    safe_text = re.sub(r"[^a-zA-Z0-9\s\-_]", "", text)
    # Replace spaces with underscores
    return safe_text.replace(" ", "_")[:50]  # Limit length to 50 chars


# ──────────────────────────────────────────────────────────────
# PNG-based implementation
def create_label_png(text, font_size, tape_key, margin_px):
    """Create PNG with perfect symmetric centering using ink-based measurement

    Font size is specified in pixels (dots at 360 dpi). Tape heights in pixels:
    - W3.5 (3.5mm): 24 pixels
    - W6 (6mm): 32 pixels
    - W9 (9mm): 50 pixels
    - W12 (12mm): 70 pixels
    - W18 (18mm): 112 pixels
    - W24 (24mm): 128 pixels

    If font_size is None, automatically selects size based on tape width.
    """
    spec = TAPE_SPECS[tape_key]
    tape_h_px = spec["pins"]

    # Auto-size font if not specified
    if font_size is None:
        # Use 75% of tape height for safe fit with ascenders/descenders
        font_size = int(tape_h_px * 0.75)
        print(
            f"Auto-selected font size: {font_size}px for {tape_key} tape ({tape_h_px}px height)"
        )

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
    # IMPORTANT: We always create the image as if printing on white tape
    # The printer handles the inversion for black tape automatically
    # Dark pixels (0) → thermal head activates → black on white tape, white on black tape
    # Light pixels (255) → thermal head doesn't activate → white on white tape, black on black tape

    # Always use white background with black text
    bg_color = 255  # White background
    text_color = 0  # Black text

    img = Image.new("L", (canvas_w, canvas_h), bg_color)
    draw = ImageDraw.Draw(img)

    # Position text so ink is perfectly centered
    x = margin_px - left
    y = (canvas_h - glyph_h) // 2 - top
    draw.text((x, y), text, font=font, fill=text_color)
    return img, spec


def png_to_bw_matrix(img, threshold=128):
    """Convert PNG to black/white matrix

    IMPORTANT: The matrix represents what to PRINT, not the final appearance:
    - 1 = print a dot (thermal head activates)
    - 0 = don't print (thermal head doesn't activate)

    On white tape: printing dots makes it black (so 1 = black result)
    On black tape: printing dots makes it white (so 1 = white result)

    Therefore we need to handle the logic based on what we want to PRINT,
    not what color we want to see.
    """
    if img.mode != "L":
        img = img.convert("L")
    w, h = img.size

    # For Brother printers: pixels < threshold should be printed
    # This works correctly: dark pixels in image → print dots
    data = [
        [1 if img.getpixel((x, y)) < threshold else 0 for x in range(w)]
        for y in range(h)
    ]
    return {"width": w, "height": h, "data": data}


def convert_to_brother_raster(matrix, spec, hi_res=True, feed_mm=2):
    """Convert matrix to Brother raster format

    CRITICAL: This function contains the exact byte sequence required for Brother P-touch
    printers to properly print, feed, and cut labels. Each command is essential and the
    order matters. Missing commands (especially cut settings) will cause printing failures.
    """
    w, h = matrix["width"], matrix["height"]
    data = []

    # INVALIDATE COMMAND - 400 NULL bytes
    # This clears the printer's buffer and ensures a clean start
    # Without this, previous print jobs may interfere
    data.append(b"\x00" * 400)

    # INITIALIZE COMMAND - ESC @ (0x1B 0x40)
    # Resets the printer to default settings
    # Essential for consistent printing behavior
    data.append(b"\x1b\x40")

    # SWITCH TO RASTER MODE - ESC i a 01 (0x1B 0x69 0x61 0x01)
    # Tells printer to expect raster graphics data
    # Mode 01 = raster mode (required for P-touch label printers)
    data.append(b"\x1b\x69\x61\x01")

    # PRINT INFORMATION COMMAND - ESC i z (0x1B 0x69 0x7A)
    # This tells the printer critical information about the tape cassette
    # Format: ESC i z <print info fields>
    # Byte 3: 0x84 = PI_KIND|PI_WIDTH flags (tells printer we're specifying tape width)
    # Byte 4: 0x00 = media type (0 = continuous tape)
    # Byte 5: tape width identifier (0x06 for 6mm, 0x09 for 9mm, etc.)
    # Bytes 6-12: reserved/padding bytes
    data.append(
        struct.pack(
            "<BBBBBBBBBBBBB",
            0x1B,  # ESC
            0x69,  # i
            0x7A,  # z
            0x84,  # flags: PI_KIND|PI_WIDTH
            0x00,  # media type: continuous tape
            spec["media_byte"],  # tape width (0x06=6mm, 0x09=9mm, etc.)
            0x00,  # reserved
            0xAA,  # fixed value
            0x02,  # fixed value
            0x00,  # reserved
            0x00,  # reserved
            0x00,  # reserved
            0x00,  # reserved
        )
    )

    # AUTO CUT MODE - ESC i M @ (0x1B 0x69 0x4D 0x40)
    # 0x40 = enable auto cut after printing
    data.append(b"\x1b\x69\x4d\x40")

    # CUT EVERY 1 LABEL - ESC i A 01 (0x1B 0x69 0x41 0x01)
    # CRITICAL: This command was missing in broken versions!
    # Tells printer to cut after every 1 label
    # Without this, tape may not feed or cut properly
    data.append(b"\x1b\x69\x41\x01")

    # ADVANCED MODE SETTINGS - ESC i K (0x1B 0x69 0x4B)
    # Controls print quality and behavior
    # Base value 0x0C is critical - using 0x00 causes printing issues
    # Bit 6 (0x40): 1 = high resolution (360 dpi), 0 = standard (180 dpi)
    adv = 0x0C  # CRITICAL: Base value must be 0x0C, not 0x00!
    if hi_res:
        adv |= 0x40  # Set bit 6 for high resolution
    data.append(b"\x1b\x69\x4b" + bytes([adv]))

    # MARGIN (FEED) AMOUNT - ESC i d (0x1B 0x69 0x64)
    # Sets how much tape to feed before/after printing
    # Critical for proper label appearance and cutting position
    # Uses fixed dots per mm: 14 for high-res (360 dpi), 7 for standard (180 dpi)
    # Default 2mm provides good balance - enough margin for clean cuts
    # Valid range: 0.5-5mm (too small = cuts through text, too large = wastes tape)
    # Working values: 2mm (28 dots hi-res) verified to work perfectly
    # NOTE: The format is actually <margin_low_byte> <margin_high_byte> (little-endian)
    dots_per_mm = 14 if hi_res else 7
    margin_dots = int(dots_per_mm * feed_mm)
    # Pack as two separate bytes (low, high) instead of using struct.pack("<H")
    margin_low = margin_dots & 0xFF
    margin_high = (margin_dots >> 8) & 0xFF
    data.append(b"\x1b\x69\x64" + bytes([margin_low, margin_high]))

    # COMPRESSION MODE - M 02 (0x4D 0x02)
    # Enables TIFF compression for raster data
    # Required for P750W and similar models
    # Reduces data size and improves reliability
    data.append(b"\x4d\x02")

    # RASTER GRAPHICS DATA
    # Each column of pixels is sent as a separate command
    # The printer prints from right to left, so we send columns in order
    pins_total = 128  # Brother print head has 128 pins (dots) vertically
    blank_left = (pins_total - spec["pins"]) // 2  # Center the tape vertically

    for x in range(w):
        # Each raster line is 20 bytes:
        # - 3 bytes: command header (G 0x11 0x00)
        # - 1 byte: TIFF compression info (0x0F = uncompressed)
        # - 16 bytes: 128 bits for 128 print head pins
        row = bytearray(20)

        # RASTER LINE COMMAND - G (0x47)
        # 0x47 = 'G' command for graphics data
        # 0x11 = 17 decimal = 16 data bytes + 1 TIFF byte
        # 0x00 = high byte of length (not used)
        # 0x0F = TIFF mode (uncompressed)
        row[0] = 0x47  # 'G' command
        row[1] = 0x11  # data length low byte (17 bytes follow)
        row[2] = 0x00  # data length high byte
        row[3] = 0x0F  # TIFF: uncompressed mode

        # Fill in the pixel data for this column
        # Each bit represents one pin on the print head
        # Bit = 1 means print (black), Bit = 0 means no print (white)
        for y in range(h):
            if matrix["data"][y][x]:  # If pixel is black
                bitpos = y + blank_left  # Position on the 128-pin print head
                byte_index = 4 + (bitpos // 8)  # Which byte (4-19)
                bit_offset = 7 - (bitpos % 8)  # Which bit (MSB first)
                row[byte_index] |= 1 << bit_offset

        data.append(bytes(row))

    # PRINT COMMAND - CTRL-Z (0x1A)
    # Tells printer to print the buffered data and feed/cut the label
    # This is the final command that triggers the actual printing
    data.append(b"\x1a")

    return b"".join(data)


# ──────────────────────────────────────────────────────────────
# Auto-discovery functions
class PassivePrinterListener(ServiceListener):
    """Enhanced listener for passive mDNS discovery - listens for unsolicited announcements"""

    def __init__(self, verbose=False):
        self.printers = []
        self.verbose = verbose
        self.found_event = threading.Event()  # Used to signal early termination

    def add_service(self, zeroconf, service_type, name):
        # Only process IPP services to avoid errors
        if "_ipp._tcp" not in service_type:
            return

        if self.verbose:
            print(f"Detected service: {name}")

        # Get service info with error handling
        try:
            info = zeroconf.get_service_info(service_type, name, timeout=3000)
            if info and "brother" in name.lower():
                # Extract IP address
                if info.addresses:
                    ip = socket.inet_ntoa(info.addresses[0])
                    printer_info = {
                        "name": name.replace("._ipp._tcp.local.", ""),
                        "ip": ip,
                        "port": info.port,
                        "properties": info.properties,
                    }

                    # Check if this printer is already in our list
                    already_found = any(
                        p["ip"] == ip and p["port"] == info.port for p in self.printers
                    )

                    if not already_found:
                        self.printers.append(printer_info)
                        if self.verbose:
                            print(
                                f"✓ Found Brother printer: {printer_info['name']} at {ip}:{info.port}"
                            )
                    elif self.verbose:
                        print(
                            f"  (Already discovered: {printer_info['name']} at {ip}:{info.port})"
                        )

                    # Signal that we found a printer
                    self.found_event.set()

        except Exception as e:
            if self.verbose:
                print(f"Error getting service info for {name}: {e}")

    def remove_service(self, zeroconf, service_type, name):
        if self.verbose and "brother" in name.lower():
            print(f"Brother printer removed: {name}")

    def update_service(self, zeroconf, service_type, name):
        # Treat updates as new additions
        self.add_service(zeroconf, service_type, name)


def discover_with_passive_listening(timeout=70, verbose=False):
    """Enhanced discovery using passive mDNS listening for unsolicited announcements

    This method implements the insights from mDNS analysis:
    - Listens passively for unsolicited printer announcements (every ~60s)
    - Uses IPv4-only to match Brother printer behavior
    - Accepts any well-formed mDNS packet, not just replies to queries

    Args:
        timeout: How long to listen for announcements (default 70s)
        verbose: Show detailed discovery messages

    Returns:
        List of discovered Brother printers
    """
    if not ZEROCONF_AVAILABLE:
        print("Warning: zeroconf not available for passive discovery")
        return []

    if verbose:
        print(
            f"Listening for Brother printer mDNS announcements ({timeout}s timeout)..."
        )
        print("Note: Brother printers typically announce themselves every ~60 seconds")

    try:
        # Use IPv4-only to match Brother printer behavior (192.168.x.x → 224.0.0.251:5353)
        zeroconf = Zeroconf(ip_version=IPVersion.V4Only)
        listener = PassivePrinterListener(verbose=verbose)

        # Create browser that will accept unsolicited announcements
        browser = ServiceBrowser(zeroconf, "_ipp._tcp.local.", listener)

        # Wait for announcements, but return early if we find a printer
        # Brother printers announce every ~60s with 4min TTL
        found = listener.found_event.wait(timeout)

        if verbose:
            if found:
                print(f"Found printer early, skipping remaining wait time")
            print(
                f"Passive listening completed. Found {len(listener.printers)} Brother printer(s)"
            )

        return listener.printers

    except Exception as e:
        if verbose:
            print(f"Passive discovery failed: {e}")
        return []
    finally:
        try:
            browser.cancel()
            zeroconf.close()
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────
# Auto-detection functions
async def detect_tape_size(printer_ip):
    """Auto-detect tape size from printer configuration"""
    try:
        async with IPP(host=printer_ip, port=631, base_path="/ipp/print") as ipp:
            # Get media attributes using direct IPP request
            message = {
                "operation-attributes-tag": {
                    "requesting-user-name": "pyipp",
                    "requested-attributes": [
                        "media-ready",
                        "media-default",
                        "media-supported",
                        "printer-name",
                        "printer-make-and-model",
                    ],
                }
            }

            # Execute the request and get media information
            result = await ipp.execute(IppOperation.GET_PRINTER_ATTRIBUTES, message)

            # Extract media information from the first printer in the response
            if result.get("printers") and len(result["printers"]) > 0:
                printer_attrs = result["printers"][0]

                # Look for media-ready or media-default attributes
                media_ready = printer_attrs.get("media-ready", "")
                media_default = printer_attrs.get("media-default", "")
                media_supported = printer_attrs.get("media-supported", [])

                print(f"Media ready: {media_ready}")
                print(f"Media default: {media_default}")
                print(f"Media supported: {media_supported}")

                # Try to extract tape width from media names
                # Brother printers often report media like "roll_current_6x0mm"
                media_list = [media_ready, media_default]
                if isinstance(media_supported, list):
                    media_list.extend(media_supported)

                for media in media_list:
                    if not media:
                        continue
                    media_str = str(media).lower()

                    # Match common Brother tape formats
                    # Look for patterns like "3.5", "6x", "roll_current_6x0mm", etc.
                    if "3.5" in media_str or "3_5" in media_str:
                        return "W3_5"
                    elif "6x" in media_str or "6mm" in media_str or "_6x" in media_str:
                        return "W6"
                    elif "9x" in media_str or "9mm" in media_str or "_9x" in media_str:
                        return "W9"
                    elif (
                        "12x" in media_str or "12mm" in media_str or "_12x" in media_str
                    ):
                        return "W12"
                    elif (
                        "18x" in media_str or "18mm" in media_str or "_18x" in media_str
                    ):
                        return "W18"
                    elif (
                        "24x" in media_str or "24mm" in media_str or "_24x" in media_str
                    ):
                        return "W24"

            # If no specific width found, try to get printer info for fallback
            printer_info = await ipp.printer()
            printer_name = (
                printer_info.info.model.lower() if printer_info.info.model else ""
            )

            if "pt-p750w" in printer_name:
                print("Detected PT-P750W, defaulting to W6 (6mm)")
                return "W6"

            return None

    except Exception as e:
        print(f"Warning: Could not auto-detect tape size: {e}")
        return None


# ──────────────────────────────────────────────────────────────
# IPP communication
IPP_STATUS_BUSY = 1287  # Printer busy status code


async def send_via_ipp(binary, copies, printer=None, max_retries=5, initial_delay=2):
    """Send Brother raster data via IPP with retry on busy.

    Args:
        binary: The raster data to send
        copies: Number of copies to print
        printer: Printer IP address
        max_retries: Maximum number of retry attempts (default 5, ~15s total)
        initial_delay: Initial delay between retries in seconds (increases each attempt)
    """
    if printer is None:
        raise ValueError("Printer IP address must be specified")

    last_error = None
    for attempt in range(max_retries + 1):
        try:
            async with IPP(host=printer, port=631, base_path="/ipp/print") as ipp:
                # Check printer status first - this can help recover stuck printers
                try:
                    printer_info = await ipp.printer()
                    if printer_info.state.printer_state != "idle":
                        print(
                            f"Warning: Printer state is '{printer_info.state.printer_state}', not idle"
                        )
                except Exception as e:
                    # Don't fail if status check fails, just warn
                    print(f"Warning: Could not check printer status: {e}")

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

        except IPPError as e:
            # Check if it's a busy error (status code 1287)
            if len(e.args) >= 2 and isinstance(e.args[1], dict):
                status_code = e.args[1].get("status-code")
                if status_code == IPP_STATUS_BUSY:
                    last_error = e
                    if attempt < max_retries:
                        delay = initial_delay + attempt  # 2s, 3s, 4s, 5s, 6s
                        print(f"Printer busy, retrying in {delay}s... (attempt {attempt + 1}/{max_retries})")
                        await asyncio.sleep(delay)
                        continue
                    else:
                        print("Printer busy, giving up after max retries")
                        return False
            # Re-raise non-busy IPP errors
            raise

    # Should not reach here, but just in case
    if last_error:
        raise last_error
    return False


# ──────────────────────────────────────────────────────────────
def main():
    """Main entry point with mode selection"""
    ap = argparse.ArgumentParser(description="Universal Brother Label Printer")
    ap.add_argument("text", help="label text, quotes for spaces")
    ap.add_argument(
        "-f",
        "--font",
        type=int,
        default=None,
        help="font size in pixels/dots (default: auto-size based on tape width)",
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
        help="printer IP address (required unless using --listen or BROTHER_PRINTER_IP env var)",
    )
    ap.add_argument(
        "--no-auto-detect",
        action="store_true",
        help="disable auto-detection of tape size",
    )
    ap.add_argument(
        "--listen",
        action="store_true",
        help="discover printer via passive mDNS listening (waits for printer announcements every ~60s)",
    )
    ap.add_argument(
        "--listen-timeout",
        type=int,
        default=70,
        help="timeout for passive listening in seconds (default: 70s)",
    )

    args = ap.parse_args()

    # Validate input arguments
    if args.font is not None and (args.font <= 0 or args.font > 200):
        print("Error: Font size must be between 1 and 200")
        sys.exit(1)

    if args.margin < 0 or args.margin > 100:
        print("Error: Margin must be between 0 and 100")
        sys.exit(1)

    if args.copies <= 0 or args.copies > 10:
        print("Error: Copies must be between 1 and 10")
        sys.exit(1)

    if args.listen_timeout <= 0 or args.listen_timeout > 300:
        print("Error: Listen timeout must be between 1 and 300 seconds")
        sys.exit(1)

    print("Brother Label Printer")

    # Get printer IP: either specified, discovered via passive listening, or from env var
    printer_ip = args.printer
    if not printer_ip:
        if args.listen:
            # Use passive listening discovery
            printers = discover_with_passive_listening(
                timeout=args.listen_timeout, verbose=True
            )

            if printers:
                printer_ip = printers[0]["ip"]
                print(f"✓ Using printer: {printers[0]['name']} at {printer_ip}")
                if len(printers) > 1:
                    print(f"Note: Found {len(printers)} printers, using first one")
            else:
                print("❌ No Brother printers found during passive listening")
                print(
                    "Tip: Increase --listen-timeout (try 60-90s) or specify IP with --printer"
                )
        else:
            # Try environment variable
            printer_ip = os.getenv("BROTHER_PRINTER_IP")
            if printer_ip:
                print(f"Using BROTHER_PRINTER_IP: {printer_ip}")

        # If still no IP, show helpful error
        if not printer_ip:
            print("❌ No printer IP specified")
            print("Options:")
            print("  1. Specify IP directly: --printer 192.168.1.175")
            print("  2. Use passive discovery: --listen (waits for announcements)")
            print(
                "  3. Set environment variable: export BROTHER_PRINTER_IP=192.168.1.175"
            )
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

    font_display = "auto" if args.font is None else f"{args.font}px"
    print(f"Text: '{args.text}' | Font: {font_display} | Tape: {tape_size}")

    # PNG mode is now the only mode
    png, spec = create_label_png(args.text, args.font, tape_size, args.margin)
    filename = f"{tape_size}_{sanitize_filename(args.text)}.png"
    png.save(filename)
    print(f"✓ Saved PNG: {filename}")

    matrix = png_to_bw_matrix(png)
    raster = convert_to_brother_raster(matrix, spec, hi_res=True)

    bin_filename = f"{tape_size}_{sanitize_filename(args.text)}.bin"
    with open(bin_filename, "wb") as f:
        f.write(raster)
    print(f"✓ Saved binary: {bin_filename}")

    ok = asyncio.run(send_via_ipp(raster, args.copies, printer_ip))
    print("✓ printed" if ok else "✗ failed")


if __name__ == "__main__":
    main()
