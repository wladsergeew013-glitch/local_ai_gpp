"""Package Blender's transparent PNG sequences for the Workspace desktop companion."""

import argparse
from pathlib import Path

from PIL import Image


LOOPS = {"ready": 36, "thinking": 28, "speaking": 28, "documents": 42}


def pack(renders: Path, destination: Path, state=None):
    destination.mkdir(parents=True, exist_ok=True)
    for name, count in LOOPS.items():
        if state and state != name:
            continue
        frames = []
        for index in range(count):
            with Image.open(renders / name / f"{index:02d}.png") as source:
                frames.append(source.convert("RGBA"))
        durations = [round((i + 1) * 1000 / 14) - round(i * 1000 / 14) for i in range(count)]
        frames[0].save(destination / f"{name}.webp", save_all=True, append_images=frames[1:],
                       duration=durations, loop=0, quality=93, method=5, alpha_quality=100)
        if name == "ready":
            frames[0].save(destination / "preview.png", optimize=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("renders", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--state", choices=LOOPS)
    options = parser.parse_args()
    pack(options.renders, options.destination, options.state)
