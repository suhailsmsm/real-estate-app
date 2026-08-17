import { useEffect, useState } from "react";

/**
 * Delays reflecting a fast-changing value (keystrokes) so callers can avoid
 * dispatching — and refetching — on every character. Used for the entity
 * search boxes; the free-text/number filter fields debounce themselves
 * directly against the dispatch call instead (see FilterBar.tsx), because
 * they also need to keep the input's own draft text while typing.
 */
export function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(t);
  }, [value, delayMs]);

  return debounced;
}
