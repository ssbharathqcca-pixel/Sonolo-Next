/**
 * Shared radar vertex math used by the existing six-dimension speaking
 * radar and the C9 four-skill radar. Discrete 0–100 values.
 */

export function clampScore(value: number): number {
  return Math.min(100, Math.max(0, value));
}

/** Polygon vertices for the given 0–100 values, starting at 12 o'clock. */
export function radarVertices(
  values: number[],
  center: number,
  radius: number,
): Array<{ x: number; y: number }> {
  return values.map((value, index) => {
    const angle = (Math.PI * 2 * index) / values.length - Math.PI / 2;
    const scaled = (clampScore(value) / 100) * radius;
    return {
      x: center + scaled * Math.cos(angle),
      y: center + scaled * Math.sin(angle),
    };
  });
}
