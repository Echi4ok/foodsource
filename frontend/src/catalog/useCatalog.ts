import { useEffect, useRef, useState } from 'react'
import { readSearch, readSuppliers, UnknownFilterError } from './api'
import { type Catalog, type Filters, type LoadedSearchResult, type PageStatus } from './model'
import { readSavedFilters, saveFilters } from './storage'

export function useCatalog() {
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [filters, setFilters] = useState(readSavedFilters)
  const [result, setResult] = useState<LoadedSearchResult | null>(null)
  const [search, setSearch] = useState({ filters, revision: 0, refreshMetadata: false })
  const [searchStatus, setSearchStatus] = useState<'pending' | 'success' | 'error'>('pending')
  const [pageStatus, setPageStatus] = useState<PageStatus>('idle')
  const catalogCache = useRef<Catalog | null>(null)
  const submittedFilters = useRef(filters)
  const activeSearch = useRef<AbortController | null>(null)
  const activePage = useRef<AbortController | null>(null)

  useEffect(() => () => activePage.current?.abort(), [])

  useEffect(() => {
    const controller = new AbortController()
    activeSearch.current = controller
    const metadata = search.refreshMetadata ? null : catalogCache.current
    void readSearch(search.filters, metadata, controller.signal).then(
      ({ catalog, page, filters: applied }) => {
        if (controller.signal.aborted) return
        catalogCache.current = catalog
        submittedFilters.current = applied
        saveFilters(applied)
        setCatalog(catalog)
        setFilters(applied)
        setResult({
          ...page,
          revision: search.revision,
          initialCount: page.items.length,
        })
        setSearchStatus('success')
      },
      () => { if (!controller.signal.aborted) setSearchStatus('error') },
    )
    return () => {
      controller.abort()
      if (activeSearch.current === controller) activeSearch.current = null
    }
  }, [search])

  function submit(next: Filters, refreshMetadata = false) {
    const normalized = { ...next, q: next.q.trim() }
    const previous = submittedFilters.current
    if (!refreshMetadata && searchStatus !== 'error' && normalized.category === previous.category
      && normalized.region === previous.region && normalized.q === previous.q) return

    submittedFilters.current = normalized
    activeSearch.current?.abort()
    activePage.current?.abort()
    activePage.current = null
    setSearchStatus('pending')
    setPageStatus('idle')
    setFilters(normalized)
    setSearch(({ revision }) => ({ filters: normalized, revision: revision + 1, refreshMetadata }))
  }

  function retry() {
    submit(filters, true)
  }

  function loadMore() {
    if (!result || result.next_offset === null || searchStatus !== 'success' || activePage.current) return
    const offset = result.next_offset

    const controller = new AbortController()
    activePage.current = controller
    setPageStatus('pending')
    void readSuppliers(filters, controller.signal, offset).then(
      (page) => {
        if (controller.signal.aborted) return
        if (page.catalog_revision !== result.catalog_revision) {
          submit(filters, true)
          return
        }
        setResult((previous) => {
          if (!previous || previous.revision !== search.revision) return previous
          const existingIds = new Set(previous.items.map(({ id }) => id))
          const added = page.items.filter(({ id }) => !existingIds.has(id))
          return {
            ...previous,
            items: [...previous.items, ...added],
            total: page.total,
            next_offset: page.next_offset,
          }
        })
        setPageStatus('idle')
      },
      (error: unknown) => {
        if (controller.signal.aborted) return
        if (error instanceof UnknownFilterError) submit(filters, true)
        else setPageStatus('error')
      },
    ).finally(() => {
      if (activePage.current === controller) activePage.current = null
    })
  }

  return { catalog, result, searchStatus, pageStatus, filters, submit, retry, loadMore }
}
