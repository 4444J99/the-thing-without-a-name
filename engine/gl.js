/** WebGL2 without a build step, a bundler, or a dependency.
 *
 * The piece has to run for years on a gallery machine nobody maintains, be
 * renderable frame-by-frame offline, and be servable from a static host. Every one
 * of those argues for raw context management over a framework: there is no version
 * of three.js that is still installable and unbroken in 2036, but there is a
 * version of `gl.drawArrays` that is.
 */

/** Compile one shader, and fail loudly. A silently-null program is the single
 *  worst hour in WebGL debugging, so the log is raised as an Error, never warned. */
function shader(gl, type, source) {
  const s = gl.createShader(type);
  gl.shaderSource(s, source);
  gl.compileShader(s);
  if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
    const kind = type === gl.VERTEX_SHADER ? "vertex" : "fragment";
    const log = gl.getShaderInfoLog(s);
    // Number the source so the driver's "ERROR: 0:47" points at something.
    const listing = source
      .split("\n")
      .map((l, i) => `${String(i + 1).padStart(3)} | ${l}`)
      .join("\n");
    gl.deleteShader(s);
    throw new Error(`${kind} shader failed to compile\n${log}\n${listing}`);
  }
  return s;
}

export function program(gl, vertexSource, fragmentSource) {
  const p = gl.createProgram();
  gl.attachShader(p, shader(gl, gl.VERTEX_SHADER, vertexSource));
  gl.attachShader(p, shader(gl, gl.FRAGMENT_SHADER, fragmentSource));
  gl.linkProgram(p);
  if (!gl.getProgramParameter(p, gl.LINK_STATUS)) {
    throw new Error(`program failed to link\n${gl.getProgramInfoLog(p)}`);
  }
  return p;
}

/** Every active uniform, resolved once, as a plain `{name: location}`.
 *  Looking these up per frame is a needless string hash on the hot path. */
export function uniforms(gl, p) {
  const out = {};
  const n = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS);
  for (let i = 0; i < n; i++) {
    const { name } = gl.getActiveUniform(p, i);
    const base = name.replace(/\[0\]$/, ""); // arrays report as `uFoo[0]`
    out[base] = gl.getUniformLocation(p, name);
  }
  return out;
}

/** A unit quad in the XY plane, corners at ±1, as a triangle strip.
 *  Position doubles as the plane-local parameterisation — no separate UV buffer. */
export function unitQuad(gl) {
  const vao = gl.createVertexArray();
  gl.bindVertexArray(vao);
  const buffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  // prettier-ignore
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([
    -1, -1,   1, -1,   -1,  1,   1,  1,
  ]), gl.STATIC_DRAW);
  gl.enableVertexAttribArray(0);
  gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
  gl.bindVertexArray(null);
  return vao;
}

/** Upload an image, canvas, or bitmap.
 *
 * `flipY` is on by default and is load-bearing rather than cosmetic: projector UVs
 * arrive from clip space with +y up, while every image format stores row 0 at the
 * top. Flipping at upload makes one convention true everywhere downstream.
 */
export function texture(gl, source, { flipY = true, wrap = null, mipmap = true } = {}) {
  const t = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, t);
  gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, flipY);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, source);
  // Mipmaps are for planes that have receded. A plane at the picture plane is
  // sampled at roughly 1:1, and projective texturing's derivatives can still push
  // the sampler down a level and blur the flat state below the score it is meant
  // to reproduce — so this is switchable and the measurement turns it off.
  if (mipmap) gl.generateMipmap(gl.TEXTURE_2D);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, mipmap ? gl.LINEAR_MIPMAP_LINEAR : gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  // Clamp by default: a fragment sampling past a photograph's edge must go
  // transparent, never wrap the far side of the room into view.
  const mode = wrap ?? gl.CLAMP_TO_EDGE;
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, mode);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, mode);
  gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
  return t;
}

/** Size the drawing buffer to the element's true device pixels.
 *  Returns true when the size changed, so callers can re-set the viewport. */
export function resize(gl, canvas, maxRatio = 2) {
  const ratio = Math.min(window.devicePixelRatio || 1, maxRatio);
  const w = Math.round(canvas.clientWidth * ratio);
  const h = Math.round(canvas.clientHeight * ratio);
  if (canvas.width === w && canvas.height === h) return false;
  canvas.width = w;
  canvas.height = h;
  gl.viewport(0, 0, w, h);
  return true;
}

export function context(canvas) {
  const gl = canvas.getContext("webgl2", {
    alpha: false,
    antialias: true,
    depth: true,
    preserveDrawingBuffer: true, // the film renderer reads pixels back after draw
    powerPreference: "high-performance",
  });
  if (!gl) throw new Error("WebGL2 unavailable — this piece requires it");
  return gl;
}
