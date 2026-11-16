import { useCallback, useEffect, useRef, useState } from 'react'
import { readShortlist } from './api'
import { COMPARE_LIMIT, supplierCompanyId, type Supplier } from './model'
import { readSavedShortlist, saveShortlist } from './storage'

interface Shortlist {
  ids: string[]
  suppliers: Supplier[]
  status: 'pending' | 'ready' | 'error'
  requestVersion: number
}

export function useComparison(revision: number | undefined) {
  const [shortlist, setShortlist] = useState<Shortlist>(() => {
    const ids = readSavedShortlist()
    return { ids, suppliers: [], status: ids.length > 0 ? 'pending' : 'ready', requestVersion: 0 }
  })
  const { requestVersion } = shortlist
  const latestIds = useRef(shortlist.ids)

  useEffect(() => {
    latestIds.current = shortlist.ids
    saveShortlist(shortlist.ids)
  }, [shortlist.ids])

  useEffect(() => {
    if (revision === undefined || latestIds.current.length === 0) return
    const ids = new Set(latestIds.current)
    const controller = new AbortController()
    setShortlist((previous) => ({ ...previous, status: 'pending' }))
    void readShortlist([...ids], controller.signal).then(
      ({ items }) => {
        if (controller.signal.aborted) return
        setShortlist((previous) => {
          const added = previous.suppliers.filter(({ id }) => !ids.has(id))
          const addedCompanies = new Set(added.map(supplierCompanyId))
          const found = new Map([
            ...items.filter((supplier) => !addedCompanies.has(supplierCompanyId(supplier))),
            ...added,
          ].map((supplier) => [supplier.id, supplier]))
          const suppliers = previous.ids.flatMap((id) => {
            const supplier = found.get(id)
            return supplier ? [supplier] : []
          })
          return { ...previous, ids: suppliers.map(({ id }) => id), suppliers, status: 'ready' }
        })
      },
      () => {
        if (!controller.signal.aborted) {
          setShortlist((previous) => previous.ids.length ? { ...previous, status: 'error' } : previous)
        }
      },
    )
    return () => controller.abort()
  }, [revision, requestVersion])

  const toggle = useCallback((supplier: Supplier) => {
    setShortlist((previous) => {
      if (previous.ids.includes(supplier.id)) {
        const ids = previous.ids.filter((id) => id !== supplier.id)
        return {
          ids,
          suppliers: previous.suppliers.filter(({ id }) => id !== supplier.id),
          status: ids.length ? previous.status : 'ready',
          requestVersion: previous.requestVersion + Number(previous.status === 'pending'),
        }
      }
      const other = previous.suppliers.find((existing) => supplierCompanyId(existing) === supplierCompanyId(supplier))
      if (other) {
        return {
          ...previous,
          ids: previous.ids.map((id) => id === other.id ? supplier.id : id),
          suppliers: previous.suppliers.map((existing) => existing.id === other.id ? supplier : existing),
        }
      }
      if (previous.ids.length >= COMPARE_LIMIT) return previous
      return { ...previous, ids: [...previous.ids, supplier.id], suppliers: [...previous.suppliers, supplier] }
    })
  }, [])

  return {
    selected: shortlist.suppliers,
    selectedIds: shortlist.ids,
    status: shortlist.status,
    retry: () => setShortlist((previous) => ({ ...previous, requestVersion: previous.requestVersion + 1 })),
    toggle,
  }
}
