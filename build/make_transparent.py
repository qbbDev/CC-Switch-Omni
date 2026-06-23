#!/usr/bin/env python3
import os
import sys
from PIL import Image

def make_transparent(input_path, output_path):
    img = Image.open(input_path).convert("RGBA")
    datas = img.getdata()

    newData = []
    for item in datas:
        r, g, b, a = item
        # If pixel is very close to pure black, make it transparent
        if r < 18 and g < 18 and b < 18:
            newData.append((0, 0, 0, 0))
        else:
            newData.append(item)

    img.putdata(newData)
    img.save(output_path, "PNG")
    print(f"Saved transparent image to {output_path}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: make_transparent.py <input> <output>")
        sys.exit(1)
    make_transparent(sys.argv[1], sys.argv[2])
