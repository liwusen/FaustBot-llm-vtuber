"""Video input for OmniJev: a clip becomes ONE image - 16 uniformly spaced frames, letterboxed to 384 px,
each stamped with its timestamp, tiled 4x4 - plus a small "video" record that tells the model how to read it.
This is the same preparation the served demo uses. Needs the ffmpeg and ffprobe binaries on PATH.

    from mso.video import video_state
    state = video_state("clip.mp4")              # writes clip.mosaic.jpg next to the clip
    answers = m.system_one(state, questions)
"""
import io
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor

from PIL import Image, ImageDraw, ImageFont

K, COLS, TILE = 16, 4, 384


def font(sz):
    for f in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(f, sz)
        except Exception:
            pass
    return ImageFont.load_default()


FT = font(26)


def letterbox(img, t):
    img = img.convert("RGB")
    w, h = img.size
    s = min(TILE / w, TILE / h)
    img = img.resize((max(1, int(w * s)), max(1, int(h * s))), Image.BILINEAR)
    tile = Image.new("RGB", (TILE, TILE), (0, 0, 0))
    tile.paste(img, ((TILE - img.size[0]) // 2, (TILE - img.size[1]) // 2))
    d = ImageDraw.Draw(tile)
    label = "t=%ds" % t
    d.rectangle([0, 0, 16 + 15 * len(label), 36], fill=(0, 0, 0))
    d.text((6, 4), label, fill=(255, 255, 255), font=FT)
    return tile


def probe_duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True, timeout=60).stdout.strip()
    try:
        return float(out)
    except ValueError:
        raise ValueError("could not read the video (ffprobe gave no duration)")


def grab(path, t):
    out = subprocess.run(["ffmpeg", "-v", "error", "-ss", "%.3f" % t, "-i", path, "-frames:v", "1", "-f", "image2pipe",
                          "-vcodec", "mjpeg", "-q:v", "3", "pipe:1"], capture_output=True, timeout=120).stdout
    return Image.open(io.BytesIO(out)).convert("RGB") if out else None


def video_mosaic(path, out_jpg):
    """16 uniformly spaced frames, letterboxed to 384 px, timestamp stamped, one 4x4 mosaic"""
    dur = probe_duration(path)
    if dur < 1.0:
        raise ValueError("video shorter than one second")
    ts = [(i + 0.5) / K * dur for i in range(K)]
    with ThreadPoolExecutor(max_workers=4) as ex:
        imgs = list(ex.map(lambda t: grab(path, min(t, max(0.0, dur - 0.05))), ts))
    last = None
    tiles = []
    for t, im in zip(ts, imgs):
        if im is None:
            im = last if last is not None else Image.new("RGB", (TILE, TILE), (0, 0, 0))
        last = im
        tiles.append(letterbox(im, int(round(t))))
    mosaic = Image.new("RGB", (COLS * TILE, (K // COLS) * TILE))
    for k, tl in enumerate(tiles):
        r, c = divmod(k, COLS)
        mosaic.paste(tl, (c * TILE, r * TILE))
    mosaic.save(out_jpg, quality=85)
    return dur, [int(round(t)) for t in ts]


def video_state(path, out_jpg=None):
    """-> {"images": [mosaic path], "video": {...}}: the state to pass to MSO1.system_one for a video clip"""
    out_jpg = out_jpg or (os.path.splitext(path)[0] + ".mosaic.jpg")
    dur, ts = video_mosaic(path, out_jpg)
    return {"images": [out_jpg], "video": {"n_frames": K, "cols": COLS, "tile": TILE, "timestamps": ts, "duration": round(dur, 1)}}
