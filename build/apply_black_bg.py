#!/usr/bin/env python3
import os
import sys
from PIL import Image

def apply_black_bg(input_path, output_path):
    img = Image.open(input_path).convert("RGBA")
    
    # Create solid black background of same dimensions (1024x1024)
    black_bg = Image.new("RGBA", img.size, (0, 0, 0, 255))
    
    # Paste transparent PNG over black background using its own alpha as mask
    combined = Image.alpha_composite(black_bg, img)
    
    # Save as high-quality PNG
    combined.save(output_path, "PNG")
    print(f"Composited transparent image onto pure black background and saved to {output_path}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: apply_black_bg.py <input> <output>")
        sys.exit(1)
    apply_black_bg(sys.argv[1], sys.argv[2])
