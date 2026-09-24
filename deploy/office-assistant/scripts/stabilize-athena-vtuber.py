#!/usr/bin/env python3
"""Lock Athena's body to idle. Only eyelids and inner mouth change."""
from __future__ import annotations

import urllib.request
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1] / "assets" / "avatars" / "athena" / "vtuber"
RAW = ROOT / "raw"
LAYERS = ROOT / "layers"
CASCADE_URL = "https://raw.githubusercontent.com/nagadomi/lbpcascade_animeface/master/lbpcascade_animeface.xml"
CASCADE_PATH = Path("/tmp/lbpcascade_animeface.xml")
CANVAS = 1024
VISEMES = ("a", "e", "i", "o", "u")
# Inner-mouth ellipse radii as fractions of face width/height. Jaw never moves.
MOUTH_SHAPES = {
    "a": (0.055, 0.032),
    "e": (0.062, 0.018),
    "i": (0.048, 0.012),
    "o": (0.032, 0.030),
    "u": (0.024, 0.018),
}


def square_canvas(image: np.ndarray) -> np.ndarray:
    height, width = image.shape[:2]
    side = min(height, width)
    x0 = (width - side) // 2
    y0 = (height - side) // 2
    cropped = image[y0 : y0 + side, x0 : x0 + side]
    if side != CANVAS:
        cropped = cv2.resize(cropped, (CANVAS, CANVAS), interpolation=cv2.INTER_AREA)
    return cropped


def load_bgr(path: Path) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise FileNotFoundError(path)
    return square_canvas(image)


def write_png(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), image)


def ensure_cascade() -> cv2.CascadeClassifier:
    if not CASCADE_PATH.exists() or CASCADE_PATH.stat().st_size < 1000:
        urllib.request.urlretrieve(CASCADE_URL, CASCADE_PATH)
    cascade = cv2.CascadeClassifier(str(CASCADE_PATH))
    if cascade.empty():
        raise RuntimeError("failed to load anime-face cascade")
    return cascade


def detect_face(gray: np.ndarray, cascade: cv2.CascadeClassifier) -> tuple[int, int, int, int]:
    faces = cascade.detectMultiScale(gray, scaleFactor=1.05, minNeighbors=3, minSize=(80, 80))
    if len(faces) == 0:
        raise RuntimeError("no face in idle")
    return tuple(int(v) for v in max(faces, key=lambda row: row[2] * row[3]))


def detect_mouth(gray: np.ndarray, face: tuple[int, int, int, int]) -> tuple[int, int]:
    """Find the short lip line, not the chin/collar shadow."""
    fx, fy, fw, fh = face
    cx = fx + fw // 2
    best: tuple[float, int] | None = None
    y0, y1 = fy + int(fh * 0.58), fy + int(fh * 0.72)
    for y in range(y0, y1):
        center = int(gray[y, cx])
        neigh = (int(gray[y - 6, cx]) + int(gray[y + 6, cx])) // 2
        dip = neigh - center
        if dip < 12:
            continue
        left = cx
        while left > cx - 40 and int(gray[y, left]) < neigh - 5:
            left -= 1
        right = cx
        while right < cx + 40 and int(gray[y, right]) < neigh - 5:
            right += 1
        width = right - left
        if 10 <= width <= 70:
            score = dip / width
            if best is None or score > best[0]:
                best = (score, y)
    if best is None:
        return cx, fy + int(fh * 0.67)
    return cx, best[1]


def detect_irises(gray: np.ndarray, face: tuple[int, int, int, int]) -> list[tuple[int, int, int, int]]:
    fx, fy, fw, fh = face
    y0, y1 = fy + int(fh * 0.24), fy + int(fh * 0.48)
    x0, x1 = fx + int(fw * 0.12), fx + int(fw * 0.88)
    region = gray[y0:y1, x0:x1]
    pupils = np.column_stack(np.where(region < 40))
    if len(pupils) < 40:
        raise RuntimeError("could not find irises")
    mid = float(np.median(pupils[:, 1]))
    irises = []
    for select in (pupils[:, 1] < mid, pupils[:, 1] >= mid):
        blob = pupils[select]
        cy = int(blob[:, 0].mean()) + y0
        cx = int(blob[:, 1].mean()) + x0
        # Cover the full open eye: iris plus sclera notches at the bottom.
        irises.append((cx, cy, max(int(fw * 0.11), 28), max(int(fh * 0.09), 22)))
    irises.sort(key=lambda item: item[0])
    return irises


def eyes_box(irises: list[tuple[int, int, int, int]]) -> tuple[int, int, int, int]:
    left, right = irises
    pad = 18
    x = left[0] - left[2] - pad
    y = min(left[1], right[1]) - max(left[3], right[3]) - pad
    x2 = right[0] + right[2] + pad
    y2 = max(left[1], right[1]) + max(left[3], right[3]) + pad
    return x, y, x2 - x, y2 - y


def mouth_box(mouth: tuple[int, int], face: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    cx, cy = mouth
    _, _, fw, fh = face
    w, h = int(fw * 0.18), int(fh * 0.10)
    return cx - w // 2, cy - h // 2, w, h


def transparent_diff(
    idle: np.ndarray,
    composed: np.ndarray,
    box: tuple[int, int, int, int],
    feather: int,
) -> np.ndarray:
    x, y, w, h = box
    mask = np.zeros(composed.shape[:2], dtype=np.uint8)
    cv2.ellipse(mask, (x + w // 2, y + h // 2), (max(w // 2, 1), max(h // 2, 1)), 0, 0, 360, 255, -1)
    if feather > 0:
        k = feather | 1
        mask = cv2.GaussianBlur(mask, (k, k), 0)
    diff = cv2.absdiff(composed, idle).max(axis=2)
    alpha = np.minimum(mask.astype(np.uint16), np.clip(diff.astype(np.uint16) * 6, 0, 255)).astype(np.uint8)
    bgra = cv2.cvtColor(composed, cv2.COLOR_BGR2BGRA)
    bgra[:, :, 3] = alpha
    return bgra


def paint_closed_eyes(
    idle: np.ndarray,
    irises: list[tuple[int, int, int, int]],
) -> np.ndarray:
    """Cover both irises with sampled skin so no silver remains. Jaw is untouched."""
    out = idle.copy()
    cheek = idle[560:590, 490:534].reshape(-1, 3)
    skin = tuple(int(v) for v in np.median(cheek, axis=0))
    lash = (18, 16, 22)
    crease = (120, 134, 170)
    for cx, cy, rx, ry in irises:
        axes = (rx + 14, ry + 22)
        cv2.ellipse(out, (cx, cy + 10), axes, 0, 0, 360, skin, -1, cv2.LINE_AA)
        cover = np.zeros(out.shape[:2], dtype=np.uint8)
        cv2.ellipse(cover, (cx, cy + 6), (rx + 16, ry + 24), 0, 0, 360, 255, -1)
        leftover = (cv2.absdiff(out, idle).max(axis=2) < 20) & (cover > 0)
        out[leftover] = skin
        cv2.ellipse(out, (cx, cy + 12), (int(rx * 0.92), max(int(ry * 0.32), 5)), 0, 200, 340, lash, 4, cv2.LINE_AA)
        cv2.ellipse(out, (cx, cy + 14), (int(rx * 0.78), max(int(ry * 0.14), 2)), 0, 20, 160, lash, 2, cv2.LINE_AA)
        cv2.ellipse(out, (cx, cy), (int(rx * 0.62), 3), 0, 200, 340, crease, 1, cv2.LINE_AA)
    return out


def paint_mouth(
    idle: np.ndarray,
    face: tuple[int, int, int, int],
    mouth: tuple[int, int],
    viseme: str,
) -> np.ndarray:
    """Open only the inner mouth. Chin, jaw, and collar stay idle pixels."""
    out = idle.copy()
    _, _, fw, fh = face
    cx, cy = mouth
    rw, rh = MOUTH_SHAPES[viseme]
    axes = (max(int(fw * rw), 6), max(int(fh * rh), 3))
    inner = (48, 32, 72)
    lip = (92, 78, 118)
    cv2.ellipse(out, (cx, cy), axes, 0, 0, 360, inner, -1, cv2.LINE_AA)
    cv2.ellipse(out, (cx, cy), axes, 0, 0, 360, lip, 2, cv2.LINE_AA)
    return out


def main() -> None:
    idle = load_bgr(RAW / "athena-vtuber-idle.png" if (RAW / "athena-vtuber-idle.png").exists() else ROOT / "athena-vtuber-idle.png")
    gray = cv2.cvtColor(idle, cv2.COLOR_BGR2GRAY)
    face = detect_face(gray, ensure_cascade())
    mouth = detect_mouth(gray, face)
    irises = detect_irises(gray, face)
    eye_roi = eyes_box(irises)
    mouth_roi = mouth_box(mouth, face)

    write_png(ROOT / "athena-vtuber-idle.png", idle)
    write_png(ROOT / "athena-vtuber-listen.png", idle)
    write_png(LAYERS / "eyes-listen.png", np.zeros((CANVAS, CANVAS, 4), dtype=np.uint8))

    blink = paint_closed_eyes(idle, irises)
    write_png(ROOT / "athena-vtuber-blink.png", blink)
    write_png(LAYERS / "eyes-blink.png", transparent_diff(idle, blink, eye_roi, 9))

    for viseme in VISEMES:
        painted = paint_mouth(idle, face, mouth, viseme)
        write_png(ROOT / f"athena-vtuber-{viseme}.png", painted)
        write_png(LAYERS / f"mouth-{viseme}.png", transparent_diff(idle, painted, mouth_roi, 7))

    print(f"wrote locked Athena frames in {ROOT}")
    print(f"face={face} mouth={mouth} irises={irises} eye_roi={eye_roi} mouth_roi={mouth_roi}")


if __name__ == "__main__":
    main()
