/** 4x4 column-major matrices, WebGL layout: m[col * 4 + row].
 *
 * Deliberately small. The engine needs exactly one non-obvious thing from linear
 * algebra — a second view-projection matrix for the projector — and nothing here
 * exists that isn't on that path. No general inverse: `lookAt` already returns an
 * inverted camera transform, which is the only inversion the renderer performs.
 */

export const identity = () =>
  new Float32Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);

/** a · b, applied right-to-left (so `multiply(view, model)` is model-then-view). */
export function multiply(a, b) {
  const o = new Float32Array(16);
  for (let c = 0; c < 4; c++) {
    for (let r = 0; r < 4; r++) {
      o[c * 4 + r] =
        a[r] * b[c * 4] +
        a[4 + r] * b[c * 4 + 1] +
        a[8 + r] * b[c * 4 + 2] +
        a[12 + r] * b[c * 4 + 3];
    }
  }
  return o;
}

export const chain = (...ms) => ms.reduce(multiply);

export function perspective(fovy, aspect, near, far) {
  const f = 1 / Math.tan(fovy / 2);
  const d = near - far;
  // prettier-ignore
  return new Float32Array([
    f / aspect, 0, 0,                 0,
    0,          f, 0,                 0,
    0,          0, (far + near) / d, -1,
    0,          0, (2 * far * near) / d, 0,
  ]);
}

const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const cross = (a, b) => [
  a[1] * b[2] - a[2] * b[1],
  a[2] * b[0] - a[0] * b[2],
  a[0] * b[1] - a[1] * b[0],
];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];

export function normalize(v) {
  const l = Math.hypot(v[0], v[1], v[2]) || 1;
  return [v[0] / l, v[1] / l, v[2] / l];
}

/** The view matrix for a camera at `eye` aimed at `center` — already the inverse
 *  of that camera's world transform, which is why no invert() lives in this file. */
export function lookAt(eye, center, up) {
  const z = normalize(sub(eye, center));
  const x = normalize(cross(up, z));
  const y = cross(z, x);
  // prettier-ignore
  return new Float32Array([
    x[0], y[0], z[0], 0,
    x[1], y[1], z[1], 0,
    x[2], y[2], z[2], 0,
    -dot(x, eye), -dot(y, eye), -dot(z, eye), 1,
  ]);
}

/** Model matrix as translate · yaw · pitch · scale, in that application order. */
export function compose(position, rotation, scale) {
  const [cy, sy] = [Math.cos(rotation[1]), Math.sin(rotation[1])];
  const [cx, sx] = [Math.cos(rotation[0]), Math.sin(rotation[0])];
  const [ax, ay, az] = scale;
  // R = Ry · Rx, then columns scaled — cheaper and clearer than three multiplies.
  // prettier-ignore
  return new Float32Array([
    cy * ax,        0 * ax,   -sy * ax,       0,
    sy * sx * ay,   cx * ay,   cy * sx * ay,  0,
    sy * cx * az,  -sx * az,   cy * cx * az,  0,
    position[0], position[1], position[2],    1,
  ]);
}
