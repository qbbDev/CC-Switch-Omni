#!/usr/bin/env python3
import os
import sys
from PIL import Image, ImageDraw

def create_macos_icon(input_path, output_path):
    # 1. Load the original transparent icon
    img = Image.open(input_path).convert("RGBA")
    
    # 2. Scale down the logo slightly so it fits inside the macOS squircle margins
    # The squircle is 824x824 (from 100 to 924).
    # We scale the logo to 680x680 (about 82% of the squircle size) for optimal padding.
    scaled_logo_size = 680
    logo_scaled = img.resize((scaled_logo_size, scaled_logo_size), Image.Resampling.LANCZOS)
    
    # 3. Create a blank transparent 1024x1024 canvas
    canvas = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    
    # 4. Draw the official macOS squircle background (824x824, centered)
    # Bounding box: [100, 100, 924, 924]
    # Corner radius: 230px (according to Apple HIG)
    draw.rounded_rectangle(
        [100, 100, 924, 924],
        radius=230,
        fill=(0, 0, 0, 255)
    )
    
    # 5. Composite the scaled logo onto the black squircle (centered)
    # Centering coordinates: (1024 - scaled_logo_size) // 2 = (1024 - 680) // 2 = 172
    offset = (1024 - scaled_logo_size) // 2
    canvas.alpha_composite(logo_scaled, (offset, offset))
    
    # 6. Save as PNG
    canvas.save(output_path, "PNG")
    print(f"Created official macOS squircle icon at {output_path}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: make_macos_squircle.py <input> <output>")
        sys.exit(1)
    create_macos_icon(sys.argv[1], sys.argv[2])
