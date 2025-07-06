#!/usr/bin/env python3
"""
Test suite for Brother label printer PNG generation and binary output
Uses inline snapshots to ensure layout consistency
"""

import pytest
from inline_snapshot import snapshot
import base64
import io
from PIL import Image

from brother_printer import (
    create_label_png, 
    png_to_bw_matrix, 
    convert_to_brother_raster,
    TAPE_SPECS
)


def png_to_b64(png_image):
    """Convert PIL Image to base64 string for snapshot comparison"""
    buffer = io.BytesIO()
    png_image.save(buffer, format='PNG')
    return base64.b64encode(buffer.getvalue()).decode('utf-8')


def binary_to_b64(binary_data):
    """Convert binary data to base64 string for snapshot comparison"""
    return base64.b64encode(binary_data).decode('utf-8')


class TestPNGGeneration:
    """Test PNG generation with various parameters"""
    
    def test_basic_6mm_black_tape(self):
        """Test basic 6mm black tape with default settings"""
        png, spec = create_label_png("Test", 40, "W6", 10, white_tape=False)
        
        # Verify PNG dimensions and content
        assert png.size[1] == 32  # 6mm = 32 pins
        assert spec["mm"] == 6
        
        # Snapshot the PNG content
        png_b64 = png_to_b64(png)
        assert png_b64 == snapshot()
    
    def test_basic_6mm_white_tape(self):
        """Test basic 6mm white tape with black text"""
        png, spec = create_label_png("Test", 40, "W6", 10, white_tape=True)
        
        # Verify PNG dimensions and content
        assert png.size[1] == 32  # 6mm = 32 pins
        assert spec["mm"] == 6
        
        # Snapshot the PNG content
        png_b64 = png_to_b64(png)
        assert png_b64 == snapshot()
    
    def test_large_text_12mm(self):
        """Test large text on 12mm tape"""
        png, spec = create_label_png("Large", 60, "W12", 15, white_tape=False)
        
        # Verify PNG dimensions
        assert png.size[1] == 70  # 12mm = 70 pins
        assert spec["mm"] == 12
        
        # Snapshot the PNG content
        png_b64 = png_to_b64(png)
        assert png_b64 == snapshot()
    
    def test_small_text_3_5mm(self):
        """Test small text on 3.5mm tape"""
        png, spec = create_label_png("Small", 20, "W3_5", 5, white_tape=False)
        
        # Verify PNG dimensions
        assert png.size[1] == 24  # 3.5mm = 24 pins
        assert spec["mm"] == 3.5
        
        # Snapshot the PNG content
        png_b64 = png_to_b64(png)
        assert png_b64 == snapshot()
    
    def test_long_text_18mm(self):
        """Test long text on 18mm tape"""
        png, spec = create_label_png("This is a longer text", 45, "W18", 20, white_tape=False)
        
        # Verify PNG dimensions
        assert png.size[1] == 112  # 18mm = 112 pins
        assert spec["mm"] == 18
        
        # Snapshot the PNG content
        png_b64 = png_to_b64(png)
        assert png_b64 == snapshot()
    
    def test_zero_margin(self):
        """Test zero margins"""
        png, spec = create_label_png("NoMargin", 35, "W6", 0, white_tape=False)
        
        # Verify minimal width (should be just the text width)
        assert png.size[1] == 32  # 6mm = 32 pins
        
        # Snapshot the PNG content
        png_b64 = png_to_b64(png)
        assert png_b64 == snapshot()
    
    def test_large_margin(self):
        """Test large margins"""
        png, spec = create_label_png("BigMargin", 30, "W9", 50, white_tape=False)
        
        # Verify PNG dimensions
        assert png.size[1] == 50  # 9mm = 50 pins
        assert spec["mm"] == 9
        
        # Snapshot the PNG content
        png_b64 = png_to_b64(png)
        assert png_b64 == snapshot()


class TestMatrixConversion:
    """Test PNG to black/white matrix conversion"""
    
    def test_black_tape_matrix(self):
        """Test matrix conversion for black tape (white text)"""
        png, _ = create_label_png("Matrix", 40, "W6", 10, white_tape=False)
        matrix = png_to_bw_matrix(png)
        
        # Verify matrix structure
        assert matrix["width"] == png.size[0]
        assert matrix["height"] == png.size[1]
        assert len(matrix["data"]) == matrix["height"]
        assert len(matrix["data"][0]) == matrix["width"]
        
        # For black tape, text should be white (0s in matrix), background black (1s)
        # Check that we have some 0s (white text pixels) and some 1s (black background)
        flat_data = [pixel for row in matrix["data"] for pixel in row]
        assert 0 in flat_data  # White text pixels
        assert 1 in flat_data  # Black background pixels
    
    def test_white_tape_matrix(self):
        """Test matrix conversion for white tape (black text)"""
        png, _ = create_label_png("Matrix", 40, "W6", 10, white_tape=True)
        matrix = png_to_bw_matrix(png)
        
        # Verify matrix structure
        assert matrix["width"] == png.size[0]
        assert matrix["height"] == png.size[1]
        
        # For white tape, text should be black (1s in matrix), background white (0s)
        flat_data = [pixel for row in matrix["data"] for pixel in row]
        assert 0 in flat_data  # White background pixels
        assert 1 in flat_data  # Black text pixels


class TestBrotherRasterOutput:
    """Test Brother binary raster format generation"""
    
    def test_6mm_black_tape_raster(self):
        """Test Brother raster output for 6mm black tape"""
        png, spec = create_label_png("Raster", 40, "W6", 10, white_tape=False)
        matrix = png_to_bw_matrix(png)
        raster = convert_to_brother_raster(matrix, spec, hi_res=True, feed_mm=1, auto_cut=True)
        
        # Verify raster format basics
        assert isinstance(raster, bytes)
        assert len(raster) > 1000  # Should be substantial binary data
        assert raster.startswith(b'\x00' * 400)  # Should start with NULL padding
        assert raster[400:402] == b'\x1B\x40'  # ESC @ command
        
        # Snapshot the binary content
        raster_b64 = binary_to_b64(raster)
        assert raster_b64 == snapshot()
    
    def test_12mm_white_tape_raster(self):
        """Test Brother raster output for 12mm white tape"""
        png, spec = create_label_png("WhiteRaster", 50, "W12", 15, white_tape=True)
        matrix = png_to_bw_matrix(png)
        raster = convert_to_brother_raster(matrix, spec, hi_res=True, feed_mm=1, auto_cut=True)
        
        # Verify raster format basics
        assert isinstance(raster, bytes)
        assert len(raster) > 1000
        assert raster.startswith(b'\x00' * 400)
        
        # Snapshot the binary content
        raster_b64 = binary_to_b64(raster)
        assert raster_b64 == snapshot()
    
    def test_raster_no_auto_cut(self):
        """Test Brother raster output without auto-cut"""
        png, spec = create_label_png("NoCut", 35, "W6", 10, white_tape=False)
        matrix = png_to_bw_matrix(png)
        raster = convert_to_brother_raster(matrix, spec, hi_res=True, feed_mm=1, auto_cut=False)
        
        # Verify raster format
        assert isinstance(raster, bytes)
        assert len(raster) > 1000
        
        # Snapshot the binary content
        raster_b64 = binary_to_b64(raster)
        assert raster_b64 == snapshot()
    
    def test_different_feed_margin(self):
        """Test Brother raster output with different feed margin"""
        png, spec = create_label_png("Feed", 40, "W9", 10, white_tape=False)
        matrix = png_to_bw_matrix(png)
        raster = convert_to_brother_raster(matrix, spec, hi_res=True, feed_mm=2, auto_cut=True)
        
        # Verify raster format
        assert isinstance(raster, bytes)
        assert len(raster) > 1000
        
        # Snapshot the binary content
        raster_b64 = binary_to_b64(raster)
        assert raster_b64 == snapshot()


class TestAllTapeSizes:
    """Test all supported tape sizes"""
    
    @pytest.mark.parametrize("tape_key", list(TAPE_SPECS.keys()))
    def test_all_tape_sizes_png(self, tape_key):
        """Test PNG generation for all tape sizes"""
        png, spec = create_label_png("Test", 35, tape_key, 10, white_tape=False)
        
        # Verify correct tape height
        expected_height = TAPE_SPECS[tape_key]["pins"]
        assert png.size[1] == expected_height
        assert spec["mm"] == TAPE_SPECS[tape_key]["mm"]
        
        # Verify we can generate a matrix and raster
        matrix = png_to_bw_matrix(png)
        raster = convert_to_brother_raster(matrix, spec)
        
        assert isinstance(raster, bytes)
        assert len(raster) > 500  # Should have substantial content


class TestSymmetricCentering:
    """Test that text is perfectly centered"""
    
    def test_centering_consistency(self):
        """Test that centering is mathematically consistent"""
        # Create two identical labels with different margins
        png1, _ = create_label_png("Center", 40, "W6", 10, white_tape=False)
        png2, _ = create_label_png("Center", 40, "W6", 20, white_tape=False)
        
        # Convert to matrices
        matrix1 = png_to_bw_matrix(png1)
        matrix2 = png_to_bw_matrix(png2)
        
        # Both should have identical text positioning relative to their margins
        # The text portion should be identical, just with different margin sizes
        assert matrix1["height"] == matrix2["height"]  # Same tape height
        assert matrix1["width"] + 20 == matrix2["width"]  # Width difference = margin difference * 2
        
        # Verify the text is centered in both cases
        # (This is a basic check - the snapshots will catch detailed positioning issues)
        text_pixels1 = sum(1 for row in matrix1["data"] for pixel in row if pixel == 1)
        text_pixels2 = sum(1 for row in matrix2["data"] for pixel in row if pixel == 1)
        
        # Should have same number of text pixels (same text, same font)
        assert text_pixels1 == text_pixels2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])