"""Place the portrait, speech panel and pointer in one transparent Tk surface."""

from dataclasses import dataclass

AVATAR_SIZE = (154, 210)
FACE_ANCHORS = {"pixel": (60, 98, 72), "studio": (49, 104, 73)}


@dataclass(frozen=True)
class CompanionLayout:
    origin: tuple[int, int]
    size: tuple[int, int]
    avatar: tuple[int, int]
    panel: tuple[int, int, int, int]
    side: str
    pointer: tuple[int, int, int, int, int, int]


def attached_layout(position, requested, bounds, face_anchor=FACE_ANCHORS["pixel"]):
    """All child coordinates are relative to the shared window, also on monitors left of zero."""
    ax, ay = position
    aw, ah = AVATAR_SIZE
    left, top, right, bottom = bounds
    # The tip is painted beneath the visible cheek, inside the sprite's transparent margins.
    left_cheek, right_cheek, cheek_y = ax + face_anchor[0], ax + face_anchor[1], ay + face_anchor[2]
    reach = 48
    left_space, right_space = left_cheek - reach - left, right - right_cheek - reach
    side = "left" if left_space >= requested[0] or left_space >= right_space else "right"
    room = left_space if side == "left" else right_space
    width = min(requested[0], max(1, room))
    height = min(requested[1], bottom - top)
    py = max(top, min(ay + 6, bottom - height))
    tip_x = left_cheek if side == "left" else right_cheek
    px = tip_x - reach - width if side == "left" else tip_x + reach
    # Keep the controls usable on narrow work areas by docking vertically.
    if width < min(340, requested[0]):
        width = min(requested[0], right - left)
        px = max(left, min(ax + aw // 2 - width // 2, right - width))
        side = "above" if ay - top >= bottom - ay - ah else "below"
        tip_x = ax + aw // 2
        if side == "above":
            cheek_y = ay + 32
            edge = max(top + 1, cheek_y - 45)
            height = min(height, edge - top)
            py = edge - height
        else:
            tip_x = ax + 62
            cheek_y = ay + ah - 26
            py = min(bottom - 1, cheek_y + 40)
            height = min(height, bottom - py)
    gx, gy = min(ax, px), min(ay, py)
    size = (max(ax + aw, px + width) - gx, max(ay + ah, py + height) - gy)
    if side in {"left", "right"}:
        bx = px + width - 2 if side == "left" else px + 2
        by = max(py + 22, min(cheek_y, py + height - 22))
        points = (bx, by - 16, tip_x, cheek_y, bx, by + 16)
    else:
        bx = max(px + 20, min(tip_x, px + width - 20))
        by = py + height - 2 if side == "above" else py + 2
        points = (bx - 16, by, tip_x, cheek_y, bx + 16, by)
    pointer = tuple(value - (gx if i % 2 == 0 else gy) for i, value in enumerate(points))
    return CompanionLayout((gx, gy), size, (ax - gx, ay - gy),
                           (px - gx, py - gy, width, height), side, pointer)
