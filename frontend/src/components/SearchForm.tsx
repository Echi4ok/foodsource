import { useMemo, useState } from 'react'
import { EMPTY_FILTERS, type Catalog, type Filters } from '../catalog/model'
import { GeographySelect } from './GeographySelect'
import { DropdownField } from './ui/Dropdown'

interface Props {
  formId: string
  catalog: Catalog | null
  pending: boolean
  filters: Filters
  onSubmit: (filters: Filters, refreshMetadata?: boolean) => void
}

export function SearchForm({ formId, catalog, pending, filters, onSubmit }: Props) {
  const [draft, setDraft] = useState(filters.q)
  const geography = useMemo(() => {
    if (!catalog) return undefined
    const byId = new Map(catalog.regions.map((region) => [region.id, region]))
    return catalog.regions.map(({ id, label, parent_id }) => {
      const parent = parent_id ? byId.get(parent_id) : undefined
      return { id, label, group: parent?.parent_id ? parent.label : undefined }
    })
  }, [catalog])

  return (
    <form
      id={formId}
      className="search-form"
      role="search"
      aria-label="Поиск поставщиков"
      onSubmit={(event) => {
        event.preventDefault()
        onSubmit({ ...filters, q: draft }, true)
      }}
      onReset={(event) => {
        event.preventDefault()
        setDraft('')
        onSubmit(EMPTY_FILTERS)
      }}
    >
      <DropdownField
        label="Категория"
        options={[{ id: '', label: 'Все категории' }, ...(catalog?.categories ?? [])]}
        disabled={!catalog}
        value={filters.category}
        onChange={(category) => onSubmit({ ...filters, category })}
      />
      <GeographySelect
        options={geography}
        value={filters.region}
        onChange={(region) => onSubmit({ ...filters, region })}
      />

      <label className="field query-field">
        <span>Товар или компания</span>
        <input name="q" type="search" maxLength={120} placeholder="Например, упаковка" value={draft} onChange={(event) => setDraft(event.target.value)} />
      </label>

      <button className="button button--primary search-button" type="submit" aria-busy={pending}>Найти</button>
    </form>
  )
}
