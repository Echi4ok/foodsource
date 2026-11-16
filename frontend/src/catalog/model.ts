export interface Option {
  id: string
  label: string
}

export interface Region extends Option {
  parent_id: string | null
}

export interface Catalog {
  catalog_revision: number
  updated_at: string
  categories: Option[]
  regions: Region[]
  supplier_count: number
}

export interface Source {
  label: string
  url: string
}

export interface Evidence {
  field: string
  url: string
  observed_at: string | null
}

export interface Supplier {
  id: string
  company_id: string
  name: string
  description: string
  category_ids: string[]
  region_id: string
  service_region_ids: string[]
  products: string[]
  website: string | null
  contacts: { email: string | null; phone: string | null }
  minimum_order: string | null
  price: string | null
  documents: string | null
  delivery: string | null
  sources: Source[]
  evidence?: Evidence[]
  reasons: { code: string; label: string }[]
}

export interface SearchResult {
  catalog_revision: number
  items: Supplier[]
  total: number
  next_offset: number | null
}

export function supplierCompanyId(supplier: Supplier) {
  return supplier.company_id ?? supplier.id
}

export interface LoadedSearchResult extends SearchResult {
  revision: number
  initialCount: number
}

export type PageStatus = 'idle' | 'pending' | 'error'

export interface Filters {
  category: string
  region: string
  q: string
}

export const EMPTY_FILTERS: Filters = { category: '', region: '', q: '' }
export const COMPARE_LIMIT = 3
export const COMMERCIAL_FIELDS = [
  { key: 'minimum_order', label: 'Мин. заказ' },
  { key: 'price', label: 'Цена' },
  { key: 'documents', label: 'Документы' },
  { key: 'delivery', label: 'Доставка' },
] as const

const plural = new Intl.PluralRules('ru')
const dateFormat = new Intl.DateTimeFormat('ru-RU', { dateStyle: 'long', timeZone: 'UTC' })

export function supplierCount(count: number): string {
  const labels = { one: 'поставщик', few: 'поставщика', other: 'поставщиков' }
  const rule = plural.select(count)
  return `${count} ${rule === 'one' || rule === 'few' ? labels[rule] : labels.other}`
}

export function catalogDate(value: string): string {
  return dateFormat.format(new Date(value))
}

export function optionLabel(options: Option[], id: string): string {
  return options.find((option) => option.id === id)?.label ?? 'Не указан'
}

export function phoneLink(phone: string): string {
  return `tel:${phone.replace(/[^\d+]/g, '')}`
}
