/** Stateless integer hashing — the engine's only source of variation.
 *
 * Nothing here holds state. Every random quantity in the piece is a pure function
 * of (seed, identity, quantised time), which is what makes the whole engine an
 * `f(seed, t)`: a film render, a browser tab, and three synchronised projectors
 * all derive the same value for the same moment without exchanging anything.
 *
 * 32-bit, not the 64-bit SplitMix64 the notes reference. JavaScript has no native
 * 64-bit integer arithmetic — BigInt would cost roughly two orders of magnitude
 * per call, and this runs per fragment-selection per frame. A 32-bit avalanche has
 * ample decorrelation for choosing among 162 photographs.
 */

function avalanche(h) {
  h ^= h >>> 16;
  h = Math.imul(h, 0x85ebca6b);
  h ^= h >>> 13;
  h = Math.imul(h, 0xc2b2ae35);
  h ^= h >>> 16;
  return h >>> 0;
}

/** Hash any number of integers to a uint32. Order matters: hash(1,2) ≠ hash(2,1). */
export function hash(...words) {
  let h = 0x9e3779b9;
  for (const w of words) {
    h = Math.imul(h ^ (w | 0), 0x5bd1e995);
    h = ((h << 15) | (h >>> 17)) >>> 0;
  }
  return avalanche(h);
}

/** Uniform in [0, 1). */
export const rand = (...words) => hash(...words) / 4294967296;

/** Uniform in [lo, hi). */
export const range = (lo, hi, ...words) => lo + (hi - lo) * rand(...words);

/** Signed uniform in [-m, m). */
export const signed = (m, ...words) => range(-m, m, ...words);

/** Pick one element deterministically. */
export const pick = (arr, ...words) => arr[hash(...words) % arr.length];
