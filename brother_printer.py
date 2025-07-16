#!/usr/bin/env python3
"""
Universal Brother Label Printer
Supports both PNG-based and labelprinterkit-based printing modes
Works with W3.5 • W6 • W9 • W12 • W18 • W24 tapes

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
    safe_text = re.sub(r'[^a-zA-Z0-9\s\-_]', '', text)
    # Replace spaces with underscores
    return safe_text.replace(' ', '_')[:50]  # Limit length to 50 chars


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


def convert_to_brother_raster(matrix, spec, hi_res=True, feed_mm=2, auto_cut=True):
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
            0x1B,         # ESC
            0x69,         # i
            0x7A,         # z
            0x84,         # flags: PI_KIND|PI_WIDTH
            0x00,         # media type: continuous tape
            spec["media_byte"],  # tape width (0x06=6mm, 0x09=9mm, etc.)
            0x00,         # reserved
            0xAA,         # fixed value
            0x02,         # fixed value
            0x00,         # reserved
            0x00,         # reserved
            0x00,         # reserved
            0x00,         # reserved
        )
    )

    # AUTO CUT MODE - ESC i M @ (0x1B 0x69 0x4D 0x40)
    # 0x40 = enable auto cut after printing
    # Without this, the tape won't cut automatically
    if auto_cut:
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
    # Bit 3 (0x08): 1 = no chain printing, 0 = chain printing
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
    dots_per_mm = 14 if hi_res else 7
    margin_dots = int(dots_per_mm * feed_mm)
    data.append(b"\x1b\x69\x64" + struct.pack("<H", margin_dots))

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
                bit_offset = 7 - (bitpos % 8)   # Which bit (MSB first)
                row[byte_index] |= 1 << bit_offset
                
        data.append(bytes(row))

    # PRINT COMMAND - CTRL-Z (0x1A)
    # Tells printer to print the buffered data and feed/cut the label
    # This is the final command that triggers the actual printing
    data.append(b"\x1a")
    
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
    filename = f"{tape_key}_{sanitize_filename(text)}_labelprinterkit.png"
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
class PassivePrinterListener(ServiceListener):
    """Enhanced listener for passive mDNS discovery - listens for unsolicited announcements"""
    
    def __init__(self, verbose=False):
        self.printers = []
        self.verbose = verbose
        self.found_event = None  # Will be set to asyncio.Event for async usage
    
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
                    already_found = any(p['ip'] == ip and p['port'] == info.port 
                                      for p in self.printers)
                    
                    if not already_found:
                        self.printers.append(printer_info)
                        if self.verbose:
                            print(f"✓ Found Brother printer: {printer_info['name']} at {ip}:{info.port}")
                    elif self.verbose:
                        print(f"  (Already discovered: {printer_info['name']} at {ip}:{info.port})")
                    
                    # Signal that we found a printer (for async usage)
                    if self.found_event:
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
        print(f"Listening for Brother printer mDNS announcements ({timeout}s timeout)...")
        print("Note: Brother printers typically announce themselves every ~60 seconds")
    
    try:
        # Use IPv4-only to match Brother printer behavior (192.168.x.x → 224.0.0.251:5353)
        zeroconf = Zeroconf(ip_version=IPVersion.V4Only)
        listener = PassivePrinterListener(verbose=verbose)

        # Create browser that will accept unsolicited announcements
        browser = ServiceBrowser(zeroconf, "_ipp._tcp.local.", listener)
        
        # Wait for announcements (Brother printers announce every ~60s with 4min TTL)
        time.sleep(timeout)
        
        if verbose:
            print(f"Passive listening completed. Found {len(listener.printers)} Brother printer(s)")
        
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
                'operation-attributes-tag': {
                    'requesting-user-name': 'pyipp',
                    'requested-attributes': [
                        'media-ready',
                        'media-default', 
                        'media-supported',
                        'printer-name',
                        'printer-make-and-model'
                    ]
                }
            }
            
            # Execute the request and get media information
            result = await ipp.execute(IppOperation.GET_PRINTER_ATTRIBUTES, message)
            
            # Extract media information from the first printer in the response
            if result.get('printers') and len(result['printers']) > 0:
                printer_attrs = result['printers'][0]
                
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
                    elif "12x" in media_str or "12mm" in media_str or "_12x" in media_str:
                        return "W12"
                    elif "18x" in media_str or "18mm" in media_str or "_18x" in media_str:
                        return "W18"
                    elif "24x" in media_str or "24mm" in media_str or "_24x" in media_str:
                        return "W24"

            # If no specific width found, try to get printer info for fallback
            printer_info = await ipp.printer()
            printer_name = printer_info.info.model.lower() if printer_info.info.model else ""
            
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
        help="printer IP address (required unless using --listen or BROTHER_PRINTER_IP env var)",
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
        "--white-tape",
        action="store_true",
        help="use white tape mode (black text on white background)",
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
    if args.font <= 0 or args.font > 200:
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

    print(f"Brother Label Printer - Mode: {args.mode}")

    # Get printer IP: either specified, discovered via passive listening, or from env var
    printer_ip = args.printer
    if not printer_ip:
        if args.listen:
            # Use passive listening discovery
            printers = discover_with_passive_listening(timeout=args.listen_timeout, verbose=True)
            
            if printers:
                printer_ip = printers[0]["ip"]
                print(f"✓ Using printer: {printers[0]['name']} at {printer_ip}")
                if len(printers) > 1:
                    print(f"Note: Found {len(printers)} printers, using first one")
            else:
                print("❌ No Brother printers found during passive listening")
                print("Tip: Increase --listen-timeout (try 60-90s) or specify IP with --printer")
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
            print("  3. Set environment variable: export BROTHER_PRINTER_IP=192.168.1.175")
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
        filename = f"{tape_size}_{sanitize_filename(args.text)}.png"
        png.save(filename)
        print(f"✓ Saved PNG: {filename}")

        matrix = png_to_bw_matrix(png)
        raster = convert_to_brother_raster(
            matrix, spec, hi_res=True, auto_cut=args.auto_cut
        )

        bin_filename = f"{tape_size}_{sanitize_filename(args.text)}.bin"
        with open(bin_filename, "wb") as f:
            f.write(raster)
        print(f"✓ Saved binary: {bin_filename}")

        ok = asyncio.run(send_via_ipp(raster, args.copies, printer_ip))
        print("✓ printed" if ok else "✗ failed")


if __name__ == "__main__":
    main()
