import { COMPARE_LIMIT, EMPTY_FILTERS, type Filters } from './model'

const FILTERS_KEY = 'foodsource.filters.v1'
const SHORTLIST_KEY = 'foodsource.shortlist.v1'
export const RESPONSES_KEY = 'foodsource.responses.v1'

export function isCatalogId(value: unknown): value is string {
  return typeof value === 'string' && value.length <= 64 && /^[a-z0-9]+(?:[-_][a-z0-9]+)*$/.test(value)
}

export function readStored(key: string): unknown {
  try {
    const value = localStorage.getItem(key)
    return value === null ? null : JSON.parse(value)
  } catch {
    return null
  }
}

export function writeStored(key: string, value: unknown): boolean {
  try {
    localStorage.setItem(key, JSON.stringify(value))
    return true
  } catch {
    return false
  }
}

export function readSavedFilters(): Filters {
  const entry = readStored(FILTERS_KEY)
  if (typeof entry !== 'object' || entry === null) return EMPTY_FILTERS
  const { category, region, q } = entry as Record<string, unknown>
  return {
    category: isCatalogId(category) ? category : '',
    region: isCatalogId(region) ? region : '',
    q: typeof q === 'string' ? q.trim().slice(0, 120) : '',
  }
}

export function saveFilters(filters: Filters) {
  writeStored(FILTERS_KEY, filters)
}

export function readSavedShortlist(): string[] {
  const stored = readStored(SHORTLIST_KEY)
  if (!Array.isArray(stored)) return []
  return [...new Set(stored.filter(isCatalogId))].slice(0, COMPARE_LIMIT)
}

export function saveShortlist(ids: string[]) {
  writeStored(SHORTLIST_KEY, ids)
}
