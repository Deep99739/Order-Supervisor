/**
 * Identifiers minted in the browser.
 *
 * A `command_id` is the retry identity of one logical request: it is generated once per
 * submission and reused on every retry of that submission, which is what lets the backend
 * return the original outcome instead of acting twice. Generating a fresh one after a
 * transport error would create a second operation, so nothing here regenerates silently.
 */
export function newId(): string {
  return crypto.randomUUID();
}

/** A source event identity. Reusing one is how a redelivery is recognised. */
export function newEventId(): string {
  return `ui-${crypto.randomUUID().slice(0, 12)}`;
}

/** Clearly synthetic, and unique enough that a demo order never collides with a real one. */
export function exampleOrderId(): string {
  const suffix = crypto.randomUUID().replace(/-/g, "").slice(0, 6).toUpperCase();
  return `ORD-DEMO-${suffix}`;
}
