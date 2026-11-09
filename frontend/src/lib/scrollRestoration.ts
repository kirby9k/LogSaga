/**
 * In-memory scroll restoration for the custom scrollable container (#main-scroll).
 * TanStack Router's built-in scroll restoration targets window, not our <main> element.
 */

const positions = new Map<string, number>()

export function savePosition(key: string): void {
  const el = document.getElementById('main-scroll')
  if (el) positions.set(key, el.scrollTop)
}

export function restorePosition(key: string): void {
  const el = document.getElementById('main-scroll')
  if (el) el.scrollTop = positions.get(key) ?? 0
}

export function resetPosition(): void {
  const el = document.getElementById('main-scroll')
  if (el) el.scrollTop = 0
}
