#!/usr/bin/env python3
"""Record the river — deterministically, in restartable segments.

This does not make the work; it makes a RECORDING of the work. The piece is the
engine running, unbounded, never the same passage twice. What comes out of here
is one stretch of it, named by the passage it caught.

The engine is a pure f(seed, t). This is the thing that exploits that: segment
*k* renders t ∈ [k·N/fps, (k+1)·N/fps) from the same function, so segments can be
rendered out of order, in parallel, on different days, and concatenated without a
seam. A failed segment costs one segment, not one film.

The capture path, every step of it chosen from measurement rather than intuition:

    draw                                        8–17 ms
      → readPixels into a PIXEL_PACK_BUFFER     direct readPixels is 889 ms. Never.
      → fenceSync + clientWaitSync(f, 0, 0)     POLLED — a large timeout throws
      → getBufferSubData                        11–28 ms
      → new Blob([buf])                         Blob 1470 MB/s vs Uint8Array 34 MB/s
      → POST to the local sink → ffmpeg stdin

Segmenting is not an optimisation. Sustained per-frame blob churn in one browser
process eventually raises net::ERR_BLOB_OUT_OF_MEMORY; a fresh process per segment
caps memory by construction.

    render.py --capture passage --tier film --segment 0
    render.py --determinism --segment 3          # render twice, require equal hashes
    render.py --concat out/passage               # stitch the segments into one recording
"""

from __future__ import annotations

import argparse
import hashlib
import math
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from browser import browser, serve  # noqa: E402

HERE = Path(__file__).resolve().parent
APP = HERE.parent
OUT = HERE / "out"

# GL reads bottom-up; every encode flips once, here, so no downstream consumer
# has to remember. ProRes 422 HQ is profile 3.
CODECS = {
    "prores": ["-c:v", "prores_ks", "-profile:v", "3", "-qscale:v", "9", "-pix_fmt", "yuv422p10le"],
    "h264": ["-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart"],
    "preview": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p"],
}
SUFFIX = {"prores": ".mov", "h264": ".mp4", "preview": ".mp4"}

# Read the frame off the GPU without stalling the pipeline on it.
CAPTURE_JS = """
() => {
  const gl = document.getElementById("stage").getContext("webgl2");
  let pbo = null, size = 0;
  window.danseCapture = async function capture(url) {
    const w = gl.drawingBufferWidth, h = gl.drawingBufferHeight;
    const need = w * h * 4;
    if (!pbo || size !== need) {
      if (pbo) gl.deleteBuffer(pbo);
      pbo = gl.createBuffer();
      size = need;
      gl.bindBuffer(gl.PIXEL_PACK_BUFFER, pbo);
      gl.bufferData(gl.PIXEL_PACK_BUFFER, need, gl.STREAM_READ);
    }
    gl.bindBuffer(gl.PIXEL_PACK_BUFFER, pbo);
    gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, 0);
    const fence = gl.fenceSync(gl.SYNC_GPU_COMMANDS_COMPLETE, 0);
    gl.flush();
    // Poll with a ZERO timeout. clientWaitSync with a large timeout raises
    // INVALID_OPERATION in WebGL2 — the spec forbids blocking the event loop.
    for (;;) {
      const s = gl.clientWaitSync(fence, 0, 0);
      if (s === gl.ALREADY_SIGNALED || s === gl.CONDITION_SATISFIED) break;
      if (s === gl.WAIT_FAILED) { gl.deleteSync(fence); throw new Error("fence wait failed"); }
      await new Promise((r) => setTimeout(r, 0));
    }
    gl.deleteSync(fence);
    const buf = new Uint8Array(need);
    gl.getBufferSubData(gl.PIXEL_PACK_BUFFER, 0, buf);
    gl.bindBuffer(gl.PIXEL_PACK_BUFFER, null);
    const res = await fetch(url, { method: "POST", body: new Blob([buf]) });
    if (!res.ok) throw new Error("sink " + res.status);
    return need;
  };
  return true;
}
"""


def ffmpeg_for(path: Path, width: int, height: int, fps: float, codec: str) -> subprocess.Popen:
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{width}x{height}", "-r", str(fps), "-i", "-",
        "-vf", "vflip",
        *CODECS[codec],
        str(path),
    ]  # fmt: skip
    path.parent.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)


class _Slot:
    """The sink, armed late.

    The server has to exist before the page loads, but ffmpeg cannot start until
    the page has told us the frame size. One server with a swappable callback
    keeps the capture POST same-origin — two servers would make it cross-origin
    and the browser would refuse it.
    """

    fn = None

    def __call__(self, path: str, body: bytes) -> None:
        if self.fn is None:
            raise RuntimeError("frame posted before the encoder was armed")
        self.fn(path, body)


def render_segment(args, segment: int, dest: Path) -> dict:
    """One segment, start to finish, in its own browser process."""
    slot = _Slot()
    with serve(sink=slot) as base:
        # The window's format unless overridden; the page is asked for exactly
        # this size so the drawing buffer IS the delivery format.
        page_url = (
            f"{base}/film.html?s={args.seed or ''}&capture={args.window}&from={args.start}"
            f"&tier={args.tier}&width={args.width or ''}&height={args.height or ''}"
        )
        with browser(headless=not args.headed, width=320, height=240) as page:
            page.goto(page_url, wait_until="load")
            page.wait_for_function("() => window.danseFilmReady === true", timeout=300_000)
            film = page.evaluate(
                "() => ({ t0: window.danseFilm.window.t0, t1: window.danseFilm.window.t1,"
                " fps: window.danseFilm.window.fps, w: window.danseFilm.width, h: window.danseFilm.height,"
                " sig: window.danseFilm.signature, seed: window.danseFilm.seed,"
                " passage: window.danseFilm.passage, passageHex: window.danseFilm.passageHex })"
            )

            fps = args.fps or film["fps"]
            total = int(round((film["t1"] - film["t0"]) * fps))
            start = segment * args.segment_frames
            if start >= total:
                return {"frames": 0, "skipped": True}
            count = min(args.segment_frames, total - start)

            enc = ffmpeg_for(dest, film["w"], film["h"], fps, args.codec)
            digest = hashlib.sha256()
            written = [0]

            def sink(_path: str, body: bytes) -> None:
                digest.update(body)
                enc.stdin.write(body)
                written[0] += 1

            slot.fn = sink
            page.evaluate(CAPTURE_JS)
            began = time.time()
            missing = 0
            for i in range(count):
                t = film["t0"] + (start + i) / fps
                r = page.evaluate("(t) => window.danseFilm.renderAt(t)", t)
                missing += r["missing"]
                page.evaluate("(u) => window.danseCapture(u)", f"{base}/frame")
                if args.progress and (i % 30 == 0 or i == count - 1):
                    done = i + 1
                    rate = done / max(1e-6, time.time() - began)
                    left = (count - done) / max(1e-6, rate)
                    print(
                        f"\r  seg {segment:>3} · {done}/{count} · {rate:.1f} fps · "
                        f"{r['movement']:<9} · {left / 60:4.1f} min left    ",
                        end="",
                        flush=True,
                    )
            if args.progress:
                print()

            enc.stdin.close()
            err = enc.stderr.read().decode(errors="replace")
            if enc.wait() != 0:
                raise SystemExit(f"ffmpeg failed on segment {segment}:\n{err}")
            if written[0] != count:
                raise SystemExit(f"segment {segment}: sank {written[0]} frames, rendered {count}")

            return {
                "frames": count,
                "missing": missing,
                "sha256": digest.hexdigest(),
                "seconds": time.time() - began,
                "signature": film["sig"],
                "size": f"{film['w']}x{film['h']}",
                "fps": fps,
            }


def expected_frames(segment: int, total: int, per_segment: int) -> int:
    return max(0, min(per_segment, total - segment * per_segment))


def complete(dest: Path, want: int) -> bool:
    """Does this segment already hold every frame it is supposed to?

    Segmenting was built so a FAILURE costs one segment rather than one film, but
    without this a RE-RUN costs the whole film anyway — and a 4K master is 39
    segments and half an hour. Frame count, not file existence: a segment killed
    mid-write leaves a perfectly plausible file with half the frames in it.
    """
    if want <= 0 or not dest.is_file() or dest.stat().st_size == 0:
        return False
    out = subprocess.run(
        # fmt: off
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-count_frames",
            "-show_entries",
            "stream=nb_read_frames",
            "-of",
            "default=nw=1:nk=1",
            str(dest),
        ],
        # fmt: on
        capture_output=True,
        text=True,
    )
    return out.returncode == 0 and out.stdout.strip().isdigit() and int(out.stdout.strip()) == want


def concat(stem: Path, codec: str) -> Path:
    """Stitch the segments without re-encoding. They are the same stream."""
    parts = sorted(stem.parent.glob(f"{stem.name}-seg-*{SUFFIX[codec]}"))
    if not parts:
        raise SystemExit(f"no segments at {stem}-seg-*{SUFFIX[codec]}")
    listing = stem.parent / f"{stem.name}-segments.txt"
    listing.write_text("".join(f"file '{p.name}'\n" for p in parts))
    dest = stem.with_suffix(SUFFIX[codec])
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
         "-i", str(listing), "-c", "copy", str(dest)],
        check=True,
    )  # fmt: skip
    print(f"  {dest.name} ← {len(parts)} segments")
    return dest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--window", "--capture", dest="window", default="passage",
                    help="a named capture preset from render/program.json")
    ap.add_argument("--start", type=float, default=0.0,
                    help="where in the river to begin recording, in seconds. A `passages` capture snaps "
                         "forward to the next passage boundary; a `seconds` capture starts exactly here.")
    ap.add_argument("--tier", default="screen", help="corpus tier (`film` for the 4K master)")
    ap.add_argument("--seed", type=int, help="override the program's seed")
    ap.add_argument("--codec", default="prores", choices=sorted(CODECS))
    ap.add_argument("--width", type=int, help="override the window's width")
    ap.add_argument("--height", type=int, help="override the window's height")
    ap.add_argument("--fps", type=float, help="override the window's frame rate")
    ap.add_argument("--segment", type=int, help="render one segment (default: all of them)")
    ap.add_argument("--segment-frames", type=int, default=600)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--quiet", dest="progress", action="store_false")
    ap.add_argument("--concat", action="store_true", help="stitch existing segments and exit")
    ap.add_argument(
        "--resume",
        action="store_true",
        help="keep segments that already hold their full frame count. Safe because the engine is a "
        "pure f(seed, t): a segment rendered yesterday is the same file it would be rendered now.",
    )
    ap.add_argument(
        "--determinism",
        action="store_true",
        help="render the segment TWICE and require identical sha256 — the gate that catches "
        "any leak of impurity into the engine",
    )
    args = ap.parse_args()

    stem = args.out / f"{args.window}-{args.seed or 'default'}"
    if args.concat:
        concat(stem, args.codec)
        return 0

    if args.determinism:
        seg = args.segment if args.segment is not None else 3
        args.progress = False
        hashes = []
        for pass_ in (1, 2):
            r = render_segment(args, seg, args.out / f".determinism-{pass_}{SUFFIX[args.codec]}")
            hashes.append(r["sha256"])
            print(f"  pass {pass_}: {r['frames']} frames · {r['sha256'][:16]}… · {r['seconds']:.1f}s")
        for p in (1, 2):
            (args.out / f".determinism-{p}{SUFFIX[args.codec]}").unlink(missing_ok=True)
        if hashes[0] != hashes[1]:
            print("\nDETERMINISM BROKEN — the same segment rendered two different films.")
            print("Something in engine/ is reading a clock, an rAF timestamp, or Math.random.")
            return 1
        print(f"\nDETERMINISM HOLDS — segment {seg} is bit-identical across two renders")
        return 0

    segments = [args.segment] if args.segment is not None else None
    total = None
    if segments is None:
        # Ask the page for the window length rather than assuming it here.
        with serve() as base, browser(headless=True, width=320, height=240) as page:
            page.goto(f"{base}/film.html?capture={args.window}&tier={args.tier}&from={args.start}", wait_until="load")
            page.wait_for_function("() => window.danseFilmReady === true", timeout=300_000)
            w = page.evaluate("() => ({ ...window.danseFilm.window, passage: window.danseFilm.passage })")
        fps = args.fps or w["fps"]
        total = int(round((w["t1"] - w["t0"]) * fps))
        segments = list(range(math.ceil(total / args.segment_frames)))
        print(f"{args.window}: {total} frames at {fps} fps → {len(segments)} segments\n")

    for seg in segments:
        dest = stem.parent / f"{stem.name}-seg-{seg:03d}{SUFFIX[args.codec]}"
        if args.resume and total is not None and complete(dest, expected_frames(seg, total, args.segment_frames)):
            print(f"  {dest.name} · already complete, kept")
            continue
        r = render_segment(args, seg, dest)
        if r.get("skipped"):
            continue
        note = f" · {r['missing']} MISSING PLATES" if r["missing"] else ""
        print(
            f"  {dest.name} · {r['frames']} frames · {r['size']} @{r['fps']} · "
            f"{r['frames'] / max(1e-6, r['seconds']):.1f} fps · {r['sha256'][:12]}…{note}"
        )

    if len(segments) > 1:
        concat(stem, args.codec)
    return 0


if __name__ == "__main__":
    sys.exit(main())
