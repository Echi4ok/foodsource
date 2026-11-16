import { useEffect, useState } from 'react'
import { parseSupplierResponse, type SupplierResponse } from './responses'
import { isCatalogId, readStored, RESPONSES_KEY, writeStored } from './storage'

function readResponses() {
  const stored = readStored(RESPONSES_KEY)
  const records = new Map<string, SupplierResponse>()
  if (typeof stored !== 'object' || stored === null || Array.isArray(stored)) return records

  for (const [id, value] of Object.entries(stored)) {
    const response = parseSupplierResponse(value)
    if (response && isCatalogId(id)) records.set(id, response)
  }
  return records
}

export function useResponses() {
  const [records, setRecords] = useState(readResponses)

  useEffect(() => {
    function sync(event: StorageEvent) {
      if (event.key === RESPONSES_KEY || event.key === null) setRecords(readResponses())
    }

    window.addEventListener('storage', sync)
    return () => window.removeEventListener('storage', sync)
  }, [])

  function update(id: string, response: SupplierResponse | null) {
    const next = readResponses()
    if (response) next.set(id, response)
    else next.delete(id)
    if (!writeStored(RESPONSES_KEY, Object.fromEntries(next))) return false
    setRecords(next)
    return true
  }

  return { records, update }
}
