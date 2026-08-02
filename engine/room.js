/** The room, and where a photograph hangs in it.
 *
 * On 20 June 2017 a camera stood in one place for 161 exposures. That fixed
 * viewpoint is not incidental history — it is the coordinate system every part of
 * this engine agrees on. Here it becomes an explicit object: a *projector*, sitting
 * exactly where the camera stood, casting that afternoon through the scene.
 *
 * The consequence is the whole architecture. A fragment does not decide what it
 * shows by carrying its own crop; it shows whatever the projector casts onto the
 * place it happens to occupy. Two planes at unrelated angles and depths therefore
 * put the floor line and the poster rail on the *same screen-space lines*, because
 * both fetched their pixels through one matrix. Continuity is a property of how
 * pixels are addressed, not a rule the generator has to remember to obey.
 *
 * Two independent axes open the still into a room:
 *
 *   geometry   planes at home and coplanar  →  planes at angles and depths
 *   projK      the plane is a WINDOW (0)    →  the plane is a CARRIED PICTURE (1)
 *
 * At `projK = 0` a plane shows whatever is behind it and its content changes as it
 * moves. At `projK = 1` it holds the crop it was assigned and carries it wherever it
 * goes. At the home position the two are *numerically identical* — which is exactly
 * why the 2017 composite is ambiguous between collage and room, and is asserted as
 * a self-test in `probe.html`.
 */

import { lookAt, multiply, perspective } from "./mat4.js";
import { range, signed } from "./rng.js";

/** The room plane: 4:3, matching both the frames (3264×2448) and the 1024×768
 *  composite. Half-extents, so the plane spans x ∈ [-1, 1], y ∈ [-0.75, 0.75]. */
export const HALF_W = 1.0;
export const HALF_H = 0.75;

/** Where the projector stands. Chosen so its frustum at z = 0 exactly fills the
 *  room plane — the reason the 2017 composite is reproduced at unit scale. */
export const PROJECTOR_DIST = 2.4;

/** The room's own horizontal architecture, as fractions of frame height, measured
 *  on IMG_1570 — the single frame of 162 with no dancer in it. The solver's band
 *  edges (0.500, 0.799) landed here independently, which is the evidence that the
 *  2017 cuts followed the room rather than a grid. */
export const POSTER_TOP = 0.489;
export const POSTER_BOTTOM = 0.802;

const FOVY = 2 * Math.atan(HALF_H / PROJECTOR_DIST);
const ASPECT = HALF_W / HALF_H;

/** The projector: fixed at the 2017 camera position, aimed at the room plane.
 *  `viewProj` is the matrix every fragment projects itself through. */
export function projector({ near = 0.1, far = 50 } = {}) {
  const eye = [0, 0, PROJECTOR_DIST];
  const view = lookAt(eye, [0, 0, 0], [0, 1, 0]);
  const proj = perspective(FOVY, ASPECT, near, far);
  return { eye, fovy: FOVY, aspect: ASPECT, view, proj, viewProj: multiply(proj, view) };
}

/** A camera that orbits the projector's position.
 *
 * `divergence` is the reveal. At 0 the camera IS the projector and the render is
 * the flat 2017 composite no matter how the planes are arranged — projective
 * texturing looks painted-on from the projector's own viewpoint. Past 0 the room
 * opens, and the arrangement becomes visible as depth. Camera motion alone
 * un-flattens the picture; no geometry has to change.
 */
export function camera(divergence, azimuth, elevation, { near = 0.05, far = 100 } = {}) {
  const d = Math.max(0, divergence);
  const orbit = PROJECTOR_DIST;
  const eye = [
    Math.sin(azimuth) * orbit * d,
    Math.sin(elevation) * orbit * d,
    PROJECTOR_DIST - orbit * d * (1 - Math.cos(azimuth) * Math.cos(elevation)),
  ];
  const view = lookAt(eye, [0, 0, 0], [0, 1, 0]);
  return { eye, view, near, far };
}

/** Depth along the camera's forward axis. Column-major, so row 2 of the view
 *  matrix is strided by 4. Shared by picture and sound to keep their ordering exact. */
export function viewDepth(view, p) {
  return view[2] * p[0] + view[6] * p[1] + view[10] * p[2] + view[14];
}

/** Place a tile's rect where the 2017 composite put it: on the room plane, at unit
 *  scale, filling exactly the projector frustum's share of that rect.
 *
 *  `rect` is [x0, y0, x1, y1] in [0,1] image coordinates with y measured DOWN from
 *  the top, which is how the score stores it and how every image format works.
 *  World y is measured UP, hence the flip.
 */
export function homePlacement(rect) {
  const [x0, y0, x1, y1] = rect;
  const wx0 = -HALF_W + 2 * HALF_W * x0;
  const wx1 = -HALF_W + 2 * HALF_W * x1;
  const wy0 = HALF_H - 2 * HALF_H * y0; // top edge (larger world y)
  const wy1 = HALF_H - 2 * HALF_H * y1; // bottom edge
  return {
    position: [(wx0 + wx1) / 2, (wy0 + wy1) / 2, 0],
    rotation: [0, 0, 0],
    scale: [(wx1 - wx0) / 2, (wy0 - wy1) / 2, 1],
  };
}

/** The tile's own crop, as texture coordinates, with v flipped to match the
 *  y-up convention that `gl.texture`'s UNPACK_FLIP_Y establishes.
 *  Returns [u0, v0, u1, v1] with v0 < v1. */
export const rectUV = ([x0, y0, x1, y1]) => [x0, 1 - y1, x1, 1 - y0];

/** Scatter a tile off the picture plane into the arrangement the piece is for:
 *  screens at different angles, at different depths, at different transparencies.
 *
 * `spread` ∈ [0, 1] is the amplitude of the whole departure, so `spread = 0` is
 * provably the 2017 composite and every intermediate value is a real state of the
 * piece rather than a transition between two authored ones. Deterministic in
 * (seed, tile id): the same seed reaches the same room forever.
 */
export function scatter(rect, id, seed, spread, opts = {}) {
  const { depth = 1.1, tilt = 0.55, drift = 0.12, opacityFloor = 0.35 } = opts;
  const home = homePlacement(rect);
  const s = Math.max(0, Math.min(1, spread));
  if (s === 0) return { ...home, opacity: 1 };

  // Depth is signed, so the arrangement occupies space in front of and behind the
  // picture plane rather than only receding from it.
  const z = signed(depth, seed, id, 1) * s;
  return {
    position: [
      home.position[0] + signed(drift, seed, id, 2) * s,
      home.position[1] + signed(drift, seed, id, 3) * s,
      z,
    ],
    rotation: [signed(tilt, seed, id, 4) * s, signed(tilt, seed, id, 5) * s, 0],
    scale: home.scale,
    // Nearer planes go more transparent, so depth reads as veiling rather than
    // occlusion — the thing that makes hanging scrim legible as layers of one room.
    opacity: 1 - (1 - range(opacityFloor, 1, seed, id, 6)) * s,
  };
}
