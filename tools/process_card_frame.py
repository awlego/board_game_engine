#!/usr/bin/env python3
"""Turn a generated card-frame image into a layered client asset.

Takes a frame template (opaque image on a white background, with a
white art window) and produces a WebP with alpha: transparent outside
the rounded card rectangle and inside the art window, so card art can
sit on a layer beneath the frame.

Usage:
  python tools/process_card_frame.py SRC OUT --rect X0 Y0 X1 Y1 \
      --radius 46 --window-seed CX CY [--badge]

  --rect / --radius   card rectangle in source pixels + corner radius
  --window-seed       any pixel inside the white art window; the hole
                      is flood-filled from here (omit for no window)
  --badge             badge mode: instead of a card rect, make the
                      white exterior transparent (flood from corners),
                      trim to content, and downscale to 256px

Prints the window geometry as width/height fractions for the client
component. Requires pillow + numpy (uv venv, not the engine venv).
"""

import argparse
import os
from collections import deque

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

WHITE_TOL = 45


def flood(white, seeds):
    h, w = white.shape
    mask = np.zeros_like(white, bool)
    q = deque()
    for y, x in seeds:
        if 0 <= y < h and 0 <= x < w and white[y, x] and not mask[y, x]:
            mask[y, x] = True
            q.append((y, x))
    while q:
        y, x = q.popleft()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and not mask[ny, nx] and white[ny, nx]:
                mask[ny, nx] = True
                q.append((ny, nx))
    return mask


def feather(mask):
    """Dilate 1px into the anti-aliased edge, then blur softly."""
    m = mask.copy()
    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        m |= np.roll(mask, (dy, dx), (0, 1))
    img = Image.fromarray((m * 255).astype("uint8")).filter(ImageFilter.GaussianBlur(1))
    return np.asarray(img).astype(float) / 255


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--rect", nargs=4, type=int, metavar=("X0", "Y0", "X1", "Y1"))
    ap.add_argument("--radius", type=int, default=46)
    ap.add_argument("--window-seed", nargs=2, type=int, metavar=("CX", "CY"))
    ap.add_argument("--badge", action="store_true")
    ap.add_argument("--quality", type=int, default=88)
    args = ap.parse_args()

    im = Image.open(args.src).convert("RGB")
    w, h = im.size
    rgb = np.asarray(im).astype(int)
    white = np.abs(rgb - 252).sum(axis=2) <= WHITE_TOL

    if args.badge:
        ext = feather(flood(white, [(0, 0), (0, w - 1), (h - 1, 0), (h - 1, w - 1)]))
        alpha = np.clip(1 - ext, 0, 1)
    else:
        if not args.rect:
            ys, xs = np.where(~white)
            args.rect = [xs.min(), ys.min(), xs.max(), ys.max()]
            print("auto rect:", args.rect)
        big = Image.new("L", (w * 4, h * 4), 0)
        ImageDraw.Draw(big).rounded_rectangle(
            [v * 4 for v in args.rect], radius=args.radius * 4, fill=255)
        alpha = np.asarray(big.resize((w, h), Image.LANCZOS)).astype(float) / 255
        if args.window_seed:
            cx, cy = args.window_seed
            hole = flood(white, [(cy, cx)])
            ys, xs = np.where(hole)
            r = ((xs.max() - xs.min()) / 2 + (ys.max() - ys.min()) / 2) / 2
            print("window center frac: cx=%.4f cy=%.4f  r(of width)=%.4f" % (
                (xs.min() + xs.max()) / 2 / w, (ys.min() + ys.max()) / 2 / h, r / w))
            alpha = np.clip(alpha * (1 - feather(hole)), 0, 1)

    out = Image.fromarray(
        np.dstack([np.asarray(im), (alpha * 255).astype("uint8")]), "RGBA")
    if args.badge:
        ys, xs = np.where(alpha > 0.03)
        out = out.crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))
        out.thumbnail((256, 256), Image.LANCZOS)
    out.save(args.out, "WEBP", quality=args.quality, method=6)
    print("saved", args.out, os.path.getsize(args.out) // 1024, "KB", out.size)


if __name__ == "__main__":
    main()
