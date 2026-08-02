/** Draw the room at (seed, t).
 *
 * One shader does everything, because everything IS one thing: a fragment finds
 * its pixel by projecting its world position through the matrix that stands where
 * the camera stood on 20 June 2017. Planes at unrelated angles and depths land the
 * poster rail on the same screen-space line, and — since all 162 exposures came
 * from one locked-off camera — a plane showing IMG_1611 and a plane showing
 * IMG_1588 register to each other too. That registration was done in 2017, by not
 * moving the tripod.
 *
 * The two texture units are not a crossfade feature. The 2017 composite needed two
 * layers for 77 of its 256 tiles, because roughly a third of its area is two
 * photographs superimposed. The same pair carries turnover over time. One
 * mechanism, two readings:
 *
 *   additive    C = gainA·A + gainB·B + lift     the 2017 solve, reproduced
 *   crossfade   C = gain·mix(A, B, w) + lift     one cell changing its mind
 *
 * The recovered room plate is drawn first, at home, as the ground everything else
 * hangs in front of. That is what the arrangement opens INTO: at high spread the
 * planes separate and the gaps show the empty apartment rather than black.
 */

import { context, program, resize, texture, unitQuad, uniforms } from "./gl.js";
import { compose, multiply, perspective } from "./mat4.js";
import { camera, homePlacement, projector, rectUV, scatter, viewDepth } from "./room.js";

/** How far past the picture plane the backdrop reaches, as a multiple of it.
 *
 *  The camera departing the projector's eye is the whole reveal, and a backdrop
 *  exactly the size of the frame runs out the moment it does — leaving a ragged
 *  black margin that says "rectangle of image" when the piece is claiming
 *  "space". At home the surplus falls outside the frustum entirely and changes
 *  nothing: the flat state measures 31.60 dB with the room behind it and 31.60 dB
 *  with the room switched off. */
const ROOM_REACH = 4;

const VERT = `#version 300 es
layout(location = 0) in vec2 aPos;

uniform mat4 uModel;
uniform mat4 uViewProj;
uniform mat4 uProjectorVP;
uniform vec4 uRectUV;

out vec4 vProjClip;
out vec2 vLocalUV;

void main() {
  vec4 world  = uModel * vec4(aPos, 0.0, 1.0);
  gl_Position = uViewProj * world;
  vProjClip   = uProjectorVP * world;                       // the shared address space
  vLocalUV    = mix(uRectUV.xy, uRectUV.zw, aPos * 0.5 + 0.5);
}`;

const FRAG = `#version 300 es
precision highp float;

in vec4 vProjClip;
in vec2 vLocalUV;

uniform sampler2D uPlateA;
uniform sampler2D uPlateB;
uniform sampler2D uMatte;

uniform vec3  uGainA;
uniform vec3  uGainB;
uniform vec3  uLift;
uniform float uMix;        // crossfade weight toward B
uniform float uAdditive;   // 1 = the 2017 two-layer solve, 0 = turnover
uniform float uProjK;      // 0 window onto the room, 1 carried picture
uniform float uTreat;      // 0 raw photograph, 1 recovered hand-treatment
uniform float uMatteK;     // cut to the figure rather than to the rectangle
uniform float uOpacity;
uniform float uHasB;
uniform float uEdge;       // half-width of the edge fade, in UV; 0 = hard abutment
uniform float uClamp;      // 1 = backdrop: extend the edge texel instead of discarding

out vec4 fragColor;

void main() {
  if (vProjClip.w <= 0.0) discard;                          // behind the projector
  vec2 projUV = (vProjClip.xy / vProjClip.w) * 0.5 + 0.5;
  vec2 uv     = mix(projUV, vLocalUV, uProjK);

  // A soft edge keeps a scattered plane from tearing along a pixel boundary. But
  // in the FLAT state the tiles abut exactly and any fade lets what is behind
  // them bleed through 256 seams, which is a real error against the composite —
  // so the width is a uniform the flat path sets to zero.
  float mask;
  if (uClamp > 0.5) {
    // The backdrop. Its quad reaches well past the picture plane so that when the
    // camera leaves the projector's eye there is still a room out there — without
    // this the frame ends in a ragged black margin and the piece reads as a
    // rectangle of image floating in a void rather than as a space. Clamping the
    // sample extends the wall and the carpet outward, which is what is actually
    // out there.
    vec2 over = max(-uv, uv - 1.0);
    uv = clamp(uv, 0.0, 1.0);
    // Extending the edge texel forever smears a single column across half the
    // frame. Dissolving instead gives the room a soft outer limit — which is also
    // what the installation this is a study for actually looks like: lit surfaces
    // with unlit room around them.
    mask = 1.0 - smoothstep(0.0, 0.34, max(over.x, over.y));
  } else if (uEdge <= 0.0) {
    mask = (uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0) ? 0.0 : 1.0;
  } else {
    vec2 e = smoothstep(0.0, uEdge, uv) * smoothstep(0.0, uEdge, 1.0 - uv);
    mask = e.x * e.y;
  }
  if (mask <= 0.0) discard;

  vec3 a = texture(uPlateA, uv).rgb;
  vec3 b = uHasB > 0.5 ? texture(uPlateB, uv).rgb : a;

  vec3 raw     = mix(a, b, uHasB > 0.5 ? uMix : 0.0);
  vec3 treated = uAdditive > 0.5
    ? uGainA * a + uGainB * b + uLift
    : uGainA * raw + uLift;

  vec3 c = mix(raw, treated, uTreat);
  float alpha = uOpacity * mask;
  if (uMatteK > 0.0) {
    alpha *= mix(1.0, texture(uMatte, uv).r, uMatteK);
  }
  fragColor = vec4(c, alpha);
}`;

export class Renderer {
  constructor(canvas, corpus) {
    const gl = context(canvas);
    this.gl = gl;
    this.canvas = canvas;
    this.corpus = corpus;
    this.program = program(gl, VERT, FRAG);
    this.u = uniforms(gl, this.program);
    this.vao = unitQuad(gl);
    this.projector = projector();
    this.stats = { planes: 0, missing: 0 };

    gl.useProgram(this.program);
    gl.uniform1i(this.u.uPlateA, 0);
    gl.uniform1i(this.u.uPlateB, 1);
    gl.uniform1i(this.u.uMatte, 2);
    gl.enable(gl.BLEND);
    gl.blendFuncSeparate(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA, gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
  }

  /** One frame. `cells` comes from the grammar, `state` from the clock. */
  draw(cells, state, opts = {}) {
    const { gl, u, corpus } = this;
    const { seed = 0, tier = "screen", treat = 1, matteK = 0, showRoom = true, pixelRatio = 2, edge = null, fit = "contain" } = opts;

    // The edge fade tracks the departure, and must be EXACTLY zero at home. The
    // tiles tile the frame — at spread 0 they abut with no gaps, so any fade lets
    // what is behind them bleed through 256 seams. Measured: leaving it on costs
    // 1.5 dB against the 2017 composite (30.11 vs 31.60), which is most of the gap
    // between "close" and "the same picture".
    const edgeWidth = edge ?? (state.spread > 0 ? 0.004 * Math.min(1, state.spread * 4) : 0);

    // pixelRatio is a cap, not a request. A measurement pins it to 1 so the drawing
    // buffer is exactly the composite's 1024x768 and neither side is resampled.
    resize(gl, this.canvas, pixelRatio);

    // The closing movement. Its cut is `black`, so there is nothing to arrange —
    // just the one line that names what was watched.
    if (state.cut === "black") {
      gl.clearColor(0, 0, 0, 1);
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      if (opts.signature) {
        gl.useProgram(this.program);
        gl.bindVertexArray(this.vao);
        gl.uniformMatrix4fv(u.uViewProj, false, multiply(perspectiveFor(this.canvas, fit), camera(0, 0, 0).view));
        gl.disable(gl.DEPTH_TEST);
        gl.depthMask(false);
        this.drawText(opts.signature, opts.signatureStyle);
      }
      this.stats = { planes: 0, missing: 0 };
      return this.stats;
    }

    gl.clearColor(0.055, 0.055, 0.06, 1);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);

    const view = camera(state.divergence, state.azimuth, state.elevation);
    const proj = perspectiveFor(this.canvas, fit);
    const viewProj = multiply(proj, view.view);

    gl.useProgram(this.program);
    gl.bindVertexArray(this.vao);
    gl.uniformMatrix4fv(u.uViewProj, false, viewProj);
    gl.uniformMatrix4fv(u.uProjectorVP, false, this.projector.viewProj);
    // Transparent geometry is sorted, not depth-tested — writing depth would let
    // a near plane occlude the far ones it is meant to veil.
    gl.disable(gl.DEPTH_TEST);
    gl.depthMask(false);

    if (showRoom && corpus.room) this.drawRoom();

    // Back to front, by view-space depth. The whole point of the arrangement is
    // that a near plane veils the far ones, and alpha blending is order-dependent.
    const drawn = [];
    for (const cell of cells) {
      const place = scatter(cell.rect, cell.id, seed, state.spread);
      drawn.push({ cell, place, z: viewDepth(view.view, place.position) });
    }
    drawn.sort((a, b) => a.z - b.z);

    let missing = 0;
    const now = new Map();
    for (const { cell, place, z } of drawn) {
      if (!this.drawCell(cell, place, state, { tier, treat, matteK, edge: edgeWidth })) {
        missing++;
      } else {
        // Record what is on screen for recast detection
        now.set(cell.id, { frame: cell.layers?.[0]?.frame ?? null, z, x: (cell.rect[0] + cell.rect[2]) - 1, area: (cell.rect[2] - cell.rect[0]) * (cell.rect[3] - cell.rect[1]) });
      }
    }

    // Emit spatial sound triggers (recasts) for the live web environment
    if (this._previous && state.cut === this._previousCut) {
      for (const [id, current] of now) {
        const prev = this._previous.get(id);
        if (prev && prev.frame !== current.frame) {
          globalThis.dispatchEvent?.(new CustomEvent("danse:recast", { detail: current }));
        }
      }
    }
    this._previous = now;
    this._previousCut = state.cut;

    this.stats = { planes: drawn.length, missing };
    return this.stats;
  }

  /** The recovered room, drawn behind everything at home and reaching past the
   *  frame on every side. `ROOM_REACH` is a multiple of the picture plane: at
   *  home the surplus is outside the frustum and costs nothing (the measurement
   *  confirms it — "no room behind" scores identically at 31.60 dB), and once the
   *  camera departs it is the difference between a space and a cut-out. */
  drawRoom() {
    const { gl, u, corpus } = this;
    const r = (ROOM_REACH - 1) / 2;
    const place = homePlacement([-r, -r, 1 + r, 1 + r]);
    gl.uniformMatrix4fv(u.uModel, false, compose(place.position, place.rotation, place.scale));
    gl.uniform4fv(u.uRectUV, rectUV([0, 0, 1, 1]));
    gl.uniform3f(u.uGainA, 1, 1, 1);
    gl.uniform3f(u.uGainB, 0, 0, 0);
    gl.uniform3f(u.uLift, 0, 0, 0);
    gl.uniform1f(u.uMix, 0);
    gl.uniform1f(u.uAdditive, 0);
    gl.uniform1f(u.uProjK, 0);
    gl.uniform1f(u.uTreat, 0);
    gl.uniform1f(u.uMatteK, 0);
    gl.uniform1f(u.uOpacity, 1);
    gl.uniform1f(u.uHasB, 0);
    gl.uniform1f(u.uEdge, 0);
    gl.uniform1f(u.uClamp, 1);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, corpus.room);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  }

  /** The closing frame: one line, white on black, held four seconds.
   *
   * It goes through the same GL canvas as everything else rather than being
   * burned in later by ffmpeg, because the capture path reads pixels off this
   * context — a DOM overlay would be invisible to it, and a separately-generated
   * tail would be a second code path for four seconds of film.
   */
  drawText(text, { color = "#f2f2f4", background = "#000000", size = 0.055 } = {}) {
    const { gl, u } = this;
    const w = gl.drawingBufferWidth;
    const h = gl.drawingBufferHeight;
    const key = `${text}/${w}x${h}/${color}/${background}/${size}`;
    if (this._textKey !== key) {
      const c = this._textCanvas ?? (this._textCanvas = document.createElement("canvas"));
      c.width = w;
      c.height = h;
      const ctx = c.getContext("2d");
      ctx.fillStyle = background;
      ctx.fillRect(0, 0, w, h);
      ctx.fillStyle = color;
      const px = Math.round(h * size);
      ctx.font = `300 ${px}px ui-monospace, "SF Mono", Menlo, monospace`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      const tracking = Math.round(px * 0.18);
      ctx.letterSpacing = `${tracking}px`;
      // Canvas adds the tracking AFTER the last glyph too, which drags a centred
      // string half a letter-space to the left. Give it back.
      ctx.fillText(text, w / 2 + tracking / 2, h / 2);
      if (this._textTex) gl.deleteTexture(this._textTex);
      this._textTex = texture(gl, c, { mipmap: false });
      this._textKey = key;
    }

    const place = homePlacement([0, 0, 1, 1]);
    gl.uniformMatrix4fv(u.uModel, false, compose(place.position, place.rotation, place.scale));
    gl.uniform4fv(u.uRectUV, rectUV([0, 0, 1, 1]));
    gl.uniform3f(u.uGainA, 1, 1, 1);
    gl.uniform3f(u.uGainB, 0, 0, 0);
    gl.uniform3f(u.uLift, 0, 0, 0);
    gl.uniform1f(u.uMix, 0);
    gl.uniform1f(u.uAdditive, 0);
    // Local UVs, not projected: the text is a picture the plane carries, which
    // is exactly what projK = 1 means.
    gl.uniform1f(u.uProjK, 1);
    gl.uniform1f(u.uTreat, 0);
    gl.uniform1f(u.uMatteK, 0);
    gl.uniform1f(u.uOpacity, 1);
    gl.uniform1f(u.uHasB, 0);
    gl.uniform1f(u.uEdge, 0);
    gl.uniform1f(u.uClamp, 0);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, this._textTex);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  }

  /** Returns false if the cell could not be drawn because its plate has not
   *  arrived yet — counted rather than logged, so a slow network shows up as a
   *  number instead of a console flood. */
  drawCell(cell, place, state, { tier, treat, matteK, edge }) {
    const { gl, u, corpus } = this;
    const [a, b] = cell.layers;
    const texA = corpus.plate(gl, a.frame, tier);
    if (!texA) return false;
    const texB = b ? corpus.plate(gl, b.frame, tier) : null;

    const additive = cell.solved ? 1 : 0;
    const gainA = a.gain ?? cell.gain ?? [1, 1, 1];
    const gainB = b?.gain ?? [0, 0, 0];

    gl.uniformMatrix4fv(u.uModel, false, compose(place.position, place.rotation, place.scale));
    gl.uniform4fv(u.uRectUV, rectUV(cell.rect));
    gl.uniform3fv(u.uGainA, gainA);
    gl.uniform3fv(u.uGainB, gainB);
    gl.uniform3fv(u.uLift, cell.lift ?? [0, 0, 0]);
    gl.uniform1f(u.uMix, b ? b.weight : 0);
    gl.uniform1f(u.uAdditive, additive);
    gl.uniform1f(u.uProjK, state.projK);
    gl.uniform1f(u.uTreat, treat);
    gl.uniform1f(u.uOpacity, place.opacity ?? 1);
    gl.uniform1f(u.uHasB, texB ? 1 : 0);
    gl.uniform1f(u.uEdge, edge);
    gl.uniform1f(u.uClamp, 0);

    let matte = null;
    if (matteK > 0) matte = corpus.matte(gl, a.frame, tier);
    gl.uniform1f(u.uMatteK, matte ? matteK : 0);

    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, texA);
    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_2D, texB ?? texA);
    if (matte) {
      gl.activeTexture(gl.TEXTURE2);
      gl.bindTexture(gl.TEXTURE_2D, matte);
    }
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    return true;
  }
}

/** The viewing frustum, letterboxed to the room's 4:3 so the composite is never
 *  cropped by the shape of someone's window. */
/** How the 4:3 room meets a frame that is not 4:3.
 *
 *   contain — show all of the room, accept empty frame around it. Right for a
 *             browser window, which can be any shape and did not choose to be.
 *   cover   — fill the frame, accept losing what falls outside it. Right for
 *             every delivery format, because a 16:9 master that letterboxes a
 *             4:3 source is a 4:3 film in a 16:9 container, and a vertical Reel
 *             that does it is unwatchable on a phone.
 *
 * Both are the same arithmetic — the field that exactly fits the room's width,
 * against the field that exactly fits its height. Contain takes the larger,
 * cover takes the smaller.
 */
function perspectiveFor(canvas, fit = "contain") {
  const { fovy, aspect } = projector();
  const view = canvas.clientWidth / canvas.clientHeight;
  const toWidth = 2 * Math.atan(Math.tan(fovy / 2) * (aspect / view));
  const fov = fit === "cover" ? Math.min(fovy, toWidth) : Math.max(fovy, toWidth);
  return perspective(fov, view, 0.05, 100);
}
