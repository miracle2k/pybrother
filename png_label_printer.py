#!/usr/bin/env python3
"""
PNG-Based Brother Label Printer for 6mm tape
Creates PNG with PIL, converts to Brother raster format, prints via IPP

This reproduces the same result as labelprinterkit but starting from PNG generation.
"""

import asyncio
import sys
import platform
import struct
from PIL import Image, ImageDraw, ImageFont
from pyipp import IPP
from pyipp.enums import IppOperation

def create_6mm_label_png(text="Hello!", font_size=40):
    """
    Create a 6mm label PNG exactly like JavaScript demo.ts
    
    Args:
        text: Text to print on the label
        font_size: Font size in pixels (default: 40 for high-res)
    """
    
    print(f"Creating 6mm PNG label with text: '{text}' (JavaScript demo approach)")
    
    # Fixed tape dimensions
    tape_width_mm = 6
    high_resolution = True
    pixels_per_mm = 14 if high_resolution else 7  # 14 pixels/mm for high-res
    tape_width_px = tape_width_mm * pixels_per_mm  # 84 pixels across tape
    
    # Get font first to measure text accurately
    try:
        if platform.system() == "Darwin":
            font_path = "/System/Library/Fonts/Arial.ttf"
        else:
            font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        
        font = ImageFont.truetype(font_path, font_size)
        
    except Exception:
        font = ImageFont.load_default()
        # If default font is used, scale the requested size appropriately
        font_size = min(font_size, 30)  # Default font limitations
    
    print(f"Font size: {font_size}px")
    
    # Create temporary image to measure text accurately
    temp_img = Image.new('L', (1000, 1000), color=255)
    temp_draw = ImageDraw.Draw(temp_img)
    
    # Get actual text bounding box for precise measurements
    bbox = temp_draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    
    print(f"Measured text: {text_width}x{text_height}px")
    
    # Calculate label dimensions with padding
    padding = 20  # 10px on each side
    label_length_px = text_width + padding
    
    print(f"PNG dimensions: {label_length_px}x{tape_width_px}px (fitted to text)")
    
    # Create actual image with exact dimensions needed
    img = Image.new('L', (label_length_px, tape_width_px), color=255)  # White background
    draw = ImageDraw.Draw(img)
    
    # True centering: compensate for PIL's textbbox font margins
    # textbbox() includes font spacing above ascenders and to the left
    # We need to subtract bbox[0] and bbox[1] to center the actual glyphs
    
    # Center horizontally and vertically, compensating for font's internal offset
    x = (label_length_px - text_width) // 2 - bbox[0]
    y = (tape_width_px - text_height) // 2 - bbox[1]
    
    print(f"Text centered at: ({x}, {y}) with bbox offset compensation")
    print(f"  Label: {label_length_px}x{tape_width_px}px")
    print(f"  Text: {text_width}x{text_height}px")
    print(f"  Bbox offset: ({bbox[0]}, {bbox[1]})")
    
    # Draw BLACK TEXT on white background (like JavaScript demo)
    # fillStyle = '#000' means black text
    draw.text((x, int(y)), text, fill=0, font=font)  # 0 = black text
    
    return img

def png_to_black_white_matrix(img, threshold=128):
    """
    Convert PIL image to black/white matrix exactly like JavaScript brother.ts
    
    This replicates convertToBlackAndWhiteMatrixImage function:
    - No transformations applied to image
    - Direct threshold conversion: pixels < 128 = black (1), >= 128 = white (0)
    
    Returns matrix where 0=white, 1=black (ready for Brother format)
    """
    
    print("Converting PNG to black/white matrix (JavaScript algorithm):")
    print(f"  Image size: {img.size}")
    print(f"  Threshold: {threshold}")
    
    # Convert image to grayscale if needed
    if img.mode != 'L':
        img = img.convert('L')
    
    width, height = img.size
    pixels = list(img.getdata())
    
    # Convert to matrix format using exact JavaScript algorithm
    matrix = []
    for y in range(height):
        row = []
        for x in range(width):
            pixel_value = pixels[y * width + x]
            
            # JavaScript logic: if (image.data[pos] < threshold) pixel = 1;
            # pixels < threshold = black (1), pixels >= threshold = white (0)
            pixel = 1 if pixel_value < threshold else 0
            row.append(pixel)
        matrix.append(row)
    
    print(f"  Matrix dimensions: {width}x{height}")
    
    return {
        'width': width,
        'height': height,
        'data': matrix
    }

def convert_to_brother_raster(image_matrix, tape_width=6, high_resolution=True):
    """
    Convert image matrix to Brother P-touch raster format
    
    This replicates your working JavaScript convertImageToDotlabel function
    """
    
    print(f"Converting to Brother raster format: {image_matrix['width']}x{image_matrix['height']}px")
    
    data = []
    
    # Header commands (copied from your working JS code)
    
    # Invalidate
    data.append(b'\x00' * 400)
    
    # Initialize
    data.append(b'\x1B\x40')
    
    # Switch to Raster Mode
    data.append(b'\x1B\x69\x61\x01')
    
    # Print information command — Sets tape width
    data.append(struct.pack('<BBBBBBBBBBBBB', 
        0x1B, 0x69, 0x7A, 0x84, 0x00, tape_width, 0x00, 0xAA, 0x02, 0x00, 0x00, 0x00, 0x00))
    
    # Various mode settings - Auto Cut
    data.append(b'\x1B\x69\x4D\x40')
    
    # Cut per label setting
    data.append(b'\x1B\x69\x41\x01')
    
    # Advanced Setting Mode (high-res bit)
    advanced_mode = 0x0C | ((1 if high_resolution else 0) << 6)
    data.append(struct.pack('<BBB', 0x1B, 0x69, 0x4B) + bytes([advanced_mode]))
    
    # Margin feed amount
    margin_dots = 14 * (2 if high_resolution else 1)
    data.append(struct.pack('<BBBB', 0x1B, 0x69, 0x64, margin_dots) + b'\x00')
    
    # Enable TIFF Compression (required for P750W)
    data.append(b'\x4D\x02')
    
    # Process image data row by row (exact JavaScript algorithm)
    # JavaScript: for (let x = 0; x < image.width; x++)
    for x in range(image_matrix['width']):
        # Each row: 3 bytes command + 1 TIFF byte + 16 bytes raster = 20 bytes
        row_buffer = bytearray(20)
        
        # Raster Row Command (exact JavaScript values)
        row_buffer[0] = 0x47
        row_buffer[1] = 0x11  # 17
        row_buffer[3] = 0x0F
        
        # Calculate margin for centering (exact JavaScript algorithm)
        tape_dots = tape_width * (14 if high_resolution else 7)  # 84 for 6mm high-res
        raster_width = 128  # Available raster buffer width in dots
        
        # JavaScript logic for 6mm tape
        if tape_width == 6:
            margin = (raster_width - tape_dots) // 2  # Math.floor((128-84)/2) = 22
        else:
            margin = 0
        
        # Set pixels in the raster buffer (exact JavaScript algorithm)
        # JavaScript: for (let y = 0; y < image.height; y++)
        for y in range(image_matrix['height']):
            if image_matrix['data'][y][x] == 1:  # Black pixel
                # JavaScript: let byteNum = (Math.floor((y + margin) / 8) + 4);
                byte_num = ((y + margin) // 8) + 4  # 3 for command + 1 for TIFF
                bit_offset = (y + margin) % 8
                
                # JavaScript: rowBuffer[byteNum] |= (1 << 7 - bitOffset);
                if byte_num < len(row_buffer):
                    row_buffer[byte_num] |= (1 << (7 - bit_offset))
        
        data.append(bytes(row_buffer))
    
    # Send print + cut command
    data.append(b'\x1A')
    
    # Combine all data
    binary_data = b''.join(data)
    
    print(f"Generated {len(binary_data)} bytes of Brother raster data")
    
    return binary_data

async def send_to_printer_ipp(binary_data, printer_ip="192.168.1.175"):
    """Send Brother raster data to printer via IPP (same as labelprinterkit version)"""
    
    print(f"Sending {len(binary_data)} bytes to printer via IPP...")
    
    try:
        async with IPP(host=printer_ip, port=631, base_path="/ipp/print") as ipp:
            
            # Check printer status
            printer_info = await ipp.printer()
            print(f"Printer: {printer_info.info.name}")
            
            if printer_info.state.printer_state != "idle":
                print(f"Warning: Printer state is '{printer_info.state.printer_state}', not idle")
            else:
                print("✓ Printer is ready")
            
            # Send print job (exact same format as labelprinterkit version)
            message = {
                "operation-attributes-tag": {
                    "requesting-user-name": "python",
                    "job-name": "png_label",
                    "document-format": "application/octet-stream",
                },
                "job-attributes-tag": {
                    "copies": 1,
                    "sides": "one-sided", 
                    "orientation-requested": 4,  # landscape
                },
                "data": binary_data
            }
            
            response = await ipp.execute(IppOperation.PRINT_JOB, message)
            
            # Check success
            status_code = response.get("status-code", -1)
            if status_code == 0:
                job_info = response.get("jobs", [{}])[0]
                job_id = job_info.get("job-id", "unknown")
                job_state = job_info.get("job-state", "unknown")
                print(f"✓ Job submitted successfully! ID: {job_id}, State: {job_state}")
                return True
            else:
                print(f"✗ Job submission failed with status: {status_code}")
                return False
            
    except Exception as e:
        print(f"✗ Error communicating with printer: {e}")
        return False

def main():
    """
    PNG-based approach: PIL → PNG → Black/White Matrix → Brother Raster → IPP
    """
    
    print("PNG-Based Brother 6mm Label Printer")
    print("=" * 40)
    print("Flow: PIL → PNG → Matrix → Brother Raster → IPP")
    print()
    print("Usage:")
    print("  python png_label_printer.py \"Your Text\"")
    print("  python png_label_printer.py \"Your Text\" 24   # Custom font size")
    print("  python png_label_printer.py \"Your Text\" 60   # Large font size")
    print()
    
    # Parse command line arguments
    text = "PNG Test"
    font_size = 40  # Default font size
    
    if len(sys.argv) > 1:
        # Check if last argument is a font size (number)
        if len(sys.argv) > 2 and sys.argv[-1].isdigit():
            font_size = int(sys.argv[-1])
            text = " ".join(sys.argv[1:-1])  # All args except the last (font size)
        else:
            text = " ".join(sys.argv[1:])  # All args are text
    
    print(f"Text: '{text}'")
    print(f"Font size: {font_size}px")
    print()
    
    # Step 1: Create PNG with PIL (auto-centered)
    png_image = create_6mm_label_png(text, font_size)
    
    # Save PNG for inspection with clear filename
    png_filename = f"6mm_png_label_{text.replace(' ', '_')}.png"
    png_image.save(png_filename)
    print(f"✓ Saved PNG: {png_filename}")
    print(f"  → Inspect this file: {png_filename}")
    
    # Step 2: Convert PNG to black/white matrix
    image_matrix = png_to_black_white_matrix(png_image, threshold=128)
    print(f"✓ Converted to matrix: {image_matrix['width']}x{image_matrix['height']}")
    
    # Step 3: Convert matrix to Brother raster format
    binary_data = convert_to_brother_raster(image_matrix, tape_width=6, high_resolution=True)
    
    # Save binary for inspection
    with open("6mm_png_label.bin", "wb") as f:
        f.write(binary_data)
    print(f"✓ Saved binary: 6mm_png_label.bin")
    
    # Step 4: Send to printer via IPP
    print("\nSending to printer...")
    success = asyncio.run(send_to_printer_ipp(binary_data))
    
    if success:
        print("✅ PNG-based printing successful!")
        print("This proves we can replicate labelprinterkit's functionality!")
    else:
        print("❌ PNG-based printing failed")

if __name__ == "__main__":
    main()