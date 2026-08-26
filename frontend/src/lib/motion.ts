/**
 * Shared, test-safe entrance-animation helpers.
 *
 * Ports the prototype's staggered "reveal" (docs/ux/knowledge-birth-prototype.html
 * `.rv → .rv.in`, #475 Screen 01): elements fade/slide in on mount, staggered by
 * position. Implemented as a CSS animation (`animate-reveal`, defined in
 * tailwind.config.ts) so the full content is ALWAYS in the DOM — nothing depends
 * on JS timing, screen readers get the whole text, and `motion-reduce:animate-none`
 * honours prefers-reduced-motion.
 *
 * The prototype's char-by-char typewriter and number count-up are intentionally
 * NOT ported: they hide content until an animation frame advances, which would
 * break content-presence (tests assert the full draft/number is rendered) and
 * accessibility (a screen reader would read a partial string). The fade/slide
 * reveal keeps the same "things arrive in sequence" feel without that cost.
 */

/** Class string: fade/slide-in on mount, disabled under reduced motion. */
export const REVEAL_CLASS = "animate-reveal motion-reduce:animate-none";

/** Default per-item stagger step (ms). */
const STEP_MS = 70;
/** Cap so a long list never makes the last item wait unreasonably long. */
const MAX_MS = 420;

/**
 * Stagger delay (ms) for the item at `index`. index ≤ 0 → 0ms; later items are
 * delayed `index * stepMs`, capped at `maxMs`.
 */
export function revealDelayMs(
  index: number,
  stepMs: number = STEP_MS,
  maxMs: number = MAX_MS,
): number {
  if (index <= 0) {
    return 0;
  }
  return Math.min(index * stepMs, maxMs);
}

/** Inline style applying the stagger delay for `index` (pairs with REVEAL_CLASS). */
export function revealStyle(index: number): { animationDelay: string } {
  return { animationDelay: `${revealDelayMs(index)}ms` };
}
