#!/usr/bin/env python3
"""
Brother Label Printer - Pure Python Solution
Uses labelprinterkit + pyipp for 6mm tape labels with perfect centering
"""

import asyncio
import sys
import platform
from pyipp import IPP
from pyipp.enums import IppOperation
from labelprinterkit.printers import P750W
from labelprinterkit.label import Label, Text, Padding
from labelprinterkit.job import Job
from labelprinterkit.constants import Media

def create_6mm_label(text="Hello!", font_scale=0.75):
    """
    Create a 6mm label using labelprinterkit with perfect centering
    
    Args:
        text: Text to print on the label
        font_scale: Font size as percentage of print area (0.75 = 75% recommended)
    """
    
    print(f"Creating 6mm label with text: '{text}'")
    
    # Get system font path
    if platform.system() == "Darwin":  # macOS
        font_path = "/System/Library/Fonts/Arial.ttf"
    else:  # Linux
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    
    # Create job for 6mm tape
    job = Job(Media.W6)
    
    # Get 6mm tape specifications
    media_info = Media.W6.value
    print(f"6mm tape - Print area: {media_info.printarea} pixels")
    
    # Use full print area as height but control font size separately
    font_size = int(media_info.printarea * font_scale)
    
    print(f"Font size: {font_size}px ({font_scale*100:.0f}% of {media_info.printarea}px print area)")
    
    # Calculate padding for perfect vertical centering (tested and confirmed)
    remaining_space = media_info.printarea - font_size
    centering_offset = 2  # Tested perfect value for 6mm tape
    vertical_padding = (remaining_space // 2) + centering_offset
    
    print(f"Perfect centering: {vertical_padding}px top padding")
    
    # Create text with vertical padding to center it
    text_obj = Text(
        height=media_info.printarea, 
        text=text, 
        font_path=font_path, 
        font_size=font_size,
        padding=Padding(left=0, top=vertical_padding, bottom=0, right=0)
    )
    
    # Create label directly - labelprinterkit should center it
    label = Label(text_obj)
    job.add_page(label)
    
    # Create backend to capture data
    class DataCapture:
        def __init__(self):
            self.data = b""
        
        def write(self, data):
            self.data += data
            return len(data)
    
    backend = DataCapture()
    printer = P750W(backend)
    
    # Generate the binary data
    printer.print(job)
    
    return backend.data

async def send_to_printer_ipp(binary_data, printer_ip="192.168.1.175"):
    """Send binary data to printer using proper IPP protocol"""
    
    print(f"Connecting to printer at {printer_ip}...")
    
    try:
        # Create IPP client
        async with IPP(host=printer_ip, port=631, base_path="/ipp/print") as ipp:
            
            # Check printer status first
            print("Checking printer status...")
            printer_info = await ipp.printer()
            print(f"Printer: {printer_info.info.name}")
            print(f"State: {printer_info.state}")
            
            if printer_info.state.printer_state != "idle":
                print(f"Warning: Printer state is '{printer_info.state.printer_state}', not idle")
            else:
                print("✓ Printer is ready")
            
            # Send print job using execute method
            print(f"Sending {len(binary_data)} bytes to printer...")
            
            # Use execute method to send Print-Job operation
            message = {
                "operation-attributes-tag": {
                    "requesting-user-name": "python",
                    "job-name": "python_label",
                    "document-format": "application/octet-stream",
                },
                "job-attributes-tag": {
                    "copies": 1,
                    "sides": "one-sided", 
                    "orientation-requested": 4,  # 4 = landscape
                },
                "data": binary_data
            }
            
            response = await ipp.execute(IppOperation.PRINT_JOB, message)
            
            # Don't print the full response as it's verbose
            
            # Check if job was successful (status-code 0 = successful-ok)
            status_code = response.get("status-code", -1)
            if status_code == 0:  # 0 = successful-ok
                job_info = response.get("jobs", [{}])[0]
                job_id = job_info.get("job-id", "unknown")
                job_state = job_info.get("job-state", "unknown")
                print(f"✓ Job submitted successfully! ID: {job_id}, State: {job_state}")
                return True
            else:
                print(f"✗ Job submission failed with status: {status_code}")
                print(f"Response: {response}")
                return False
            
    except Exception as e:
        print(f"✗ Error communicating with printer: {e}")
        return False

def main():
    """Main function for 6mm label printing"""
    
    print("Brother 6mm Label Printer - Pure Python")
    print("=" * 40)
    
    # Get text from command line or use default
    if len(sys.argv) > 1:
        text = " ".join(sys.argv[1:])
    else:
        text = "Python ✓"
    
    # Create the 6mm label with perfect centering
    font_scale = 0.75      # 75% of height for nice margins
    binary_data = create_6mm_label(text, font_scale)
    
    if binary_data:
        print(f"✓ Generated {len(binary_data)} bytes of label data")
        
        # Save for inspection
        with open("6mm_label.bin", "wb") as f:
            f.write(binary_data)
        print("✓ Saved to 6mm_label.bin")
        
        # Send to printer using asyncio
        print("\nSending to printer via IPP...")
        success = asyncio.run(send_to_printer_ipp(binary_data))
        
        if success:
            print("✓ Label printed successfully!")
        else:
            print("✗ Print failed")
    else:
        print("✗ Failed to generate label data")

if __name__ == "__main__":
    main()