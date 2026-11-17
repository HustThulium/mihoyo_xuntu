"""Offline image registration, printing diagnostics without touching the game."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from map_overlay.project import read_image
from map_overlay.vision import Localizer
from map_overlay.geometry import transform_points

parser = argparse.ArgumentParser()
parser.add_argument("reference")
parser.add_argument("screenshot")
parser.add_argument("--rect", nargs=4, type=int, metavar=("X", "Y", "W", "H"))
args = parser.parse_args()
reference = read_image(args.reference)
query = read_image(args.screenshot)
if args.rect:
    x, y, w, h = args.rect
    if min(x, y) < 0 or min(w, h) < 64 or x+w > query.shape[1] or y+h > query.shape[0]:
        parser.error("--rect must be inside the screenshot")
    query = query[y:y+h, x:x+w].copy()
localizer = Localizer(reference)
match = localizer.locate(query)
result = asdict(match)
result["matrix"] = match.matrix.tolist() if match.ok else None
result["error"] = match.error if match.error != float("inf") else None
result["reference_features"] = len(localizer.points)
if match.ok:
    h, w = query.shape[:2]
    result["center_in_reference"] = transform_points([[w/2, h/2]], match.matrix)[0].tolist()
print(json.dumps(result, ensure_ascii=True, indent=2))
