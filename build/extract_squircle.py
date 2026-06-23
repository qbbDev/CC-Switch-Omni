#!/usr/bin/env python3
import os
import sys
from PIL import Image, ImageDraw

def crop_and_align_squircle(input_path, output_path):
    img = Image.open(input_path).convert("RGBA")
    width, height = img.size
    
    # 1. Find bounding box of the black squircle (pixels that are not white)
    # White threshold: R > 240, G > 240, B > 240
    left = width
    top = height
    right = 0
    bottom = 0
    
    pixels = img.load()
    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            # If not white/light-gray (i.e. part of the black squircle)
            if not (r > 245 and g > 245 and b > 245):
                if x < left: left = x
                if x > right: right = x
                if y < top: top = y
                if y > bottom: bottom = y
                
    print(f"Detected squircle bounds: L={left}, T={top}, R={right}, B={bottom}")
    
    # Extract the squircle bounding box
    squircle_img = img.crop((left, top, right + 1, bottom + 1))
    
    # 2. Resize the squircle exactly to the Apple HIG standard: 824x824
    squircle_img = squircle_img.resize((824, 824), Image.Resampling.LANCZOS)
    
    # 3. Create a blank 1024x1024 transparent canvas
    canvas = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    
    # 4. Create a clean macOS HIG squircle mask (824x824 from 100 to 924, radius 230)
    mask = Image.new("L", (1024, 1024), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle(
        [100, 100, 924, 924],
        radius=230,
        fill=255
    )
    
    # 5. Paste the resized squircle onto the canvas (centered at 100, 100)
    canvas.paste(squircle_img, (100, 100))
    
    # 6. Apply the mask to make sure the corners are perfectly transparent
    final_canvas = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    final_canvas.paste(canvas, (0, 0), mask=mask)
    
    # Save the output image
    final_canvas.save(output_path, "PNG")
    print(f"Successfully cropped, aligned and saved to {output_path}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: extract_squircle.py <input> <output>")
        sys.exit(1)
    crop_and_align_squircle(sys.argv[1], sys.argv[2])
