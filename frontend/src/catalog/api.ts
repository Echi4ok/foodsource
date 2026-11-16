import type { Catalog, Filters, SearchResult } from './model'

export class UnknownFilterError extends Error {
  constructor(readonly field: 'category' | 'region') {
    super(`Unknown filter: ${field}`)
  }
}

async function read<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(path, {
    signal,
    headers: { Accept: 'application/json' },
  })
  if (response.status === 422) {
    const problem = await response.json().catch(() => null) as {
      detail?: { type?: string; loc?: unknown[] }[]
    } | null
    const field = Array.isArray(problem?.detail)
      ? problem.detail.find((issue) => issue?.type === 'unknown_filter')?.loc?.[1]
      : undefined
    if (field === 'category' || field === 'region') throw new UnknownFilterError(field)
  }
  if (!response.ok) throw new Error(`Catalog request failed (${response.status})`)
  return response.json() as Promise<T>
}

export function readCatalog(signal: AbortSignal): Promise<Catalog> {
  return read('/api/catalog', signal)
}

export function readSuppliers(filters: Filters, signal: AbortSignal, offset = 0): Promise<SearchResult> {
  const query = new URLSearchParams()
  if (filters.category) query.set('category', filters.category)
  if (filters.region) query.set('region', filters.region)
  if (filters.q) query.set('q', filters.q)
  if (offset) query.set('offset', String(offset))
  const suffix = query.size ? `?${query}` : ''
  return read(`/api/suppliers${suffix}`, signal)
}

export function readShortlist(ids: string[], signal: AbortSignal): Promise<SearchResult> {
  const query = new URLSearchParams()
  for (const id of ids) query.append('id', id)
  return read(`/api/suppliers/shortlist?${query}`, signal)
}

async function compatibleSearch(filters: Filters, knownCatalog: Catalog | null, signal: AbortSignal) {
  let [catalog, page] = await Promise.all([knownCatalog ?? readCatalog(signal), readSuppliers(filters, signal)])
  if (catalog.catalog_revision !== page.catalog_revision) {
    catalog = await readCatalog(signal)
    if (catalog.catalog_revision !== page.catalog_revision) page = await readSuppliers(filters, signal)
    if (catalog.catalog_revision !== page.catalog_revision) throw new Error('Catalog changed during search')
  }
  return { catalog, page, filters }
}

export async function readSearch(filters: Filters, knownCatalog: Catalog | null, signal: AbortSignal) {
  try {
    return await compatibleSearch(filters, knownCatalog, signal)
  } catch (error) {
    if (!(error instanceof UnknownFilterError) || !filters[error.field]) throw error
    const catalog = await readCatalog(signal)
    const corrected = {
      q: filters.q,
      category: catalog.categories.some(({ id }) => id === filters.category) ? filters.category : '',
      region: catalog.regions.some(({ id }) => id === filters.region) ? filters.region : '',
      [error.field]: '',
    }
    return compatibleSearch(corrected, catalog, signal)
  }
}
