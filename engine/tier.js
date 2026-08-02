/** Require exact offline-render pixels instead of accepting progressive fallbacks. */

export async function requireTier(corpus, tier, ids) {
  const unique = [...new Set(ids)];
  for (const kind of ["plates", "mattes"]) await corpus.ensure(kind, tier, unique);
  const unavailable = [];
  for (const kind of ["plates", "mattes"]) {
    for (const id of unique) if (!corpus.has(kind, tier, id)) unavailable.push(`${kind}/${id}`);
  }
  if (unavailable.length) throw new Error(`requested ${tier} tier failed for ${unavailable.join(", ")}`);
}
