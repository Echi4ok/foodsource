import { ArrowUpRight, Globe, Mail, Phone } from 'lucide-react'
import { catalogDate, phoneLink, type Supplier } from '../catalog/model'

export function FieldValue({ value }: { value: string | null }) {
  return value ? <>{value}</> : <span className="unknown" title="Нет данных">—</span>
}

export function SupplierLinks({ supplier }: { supplier: Supplier }) {
  const { phone, email } = supplier.contacts
  if (!phone && !email && !supplier.website) return <span className="unknown">Контакты не указаны</span>
  return (
    <ul className="fact-links">
      {phone && <li><a href={phoneLink(phone)}><Phone size={15} aria-hidden="true" />{phone}</a></li>}
      {email && <li><a href={`mailto:${email}`}><Mail size={15} aria-hidden="true" />{email}</a></li>}
      {supplier.website && (
        <li>
          <a href={supplier.website} target="_blank" rel="noopener noreferrer">
            <Globe size={15} aria-hidden="true" />Сайт <ArrowUpRight size={13} aria-hidden="true" />
          </a>
        </li>
      )}
    </ul>
  )
}

export function SourceLinks({ supplier, limit }: { supplier: Supplier; limit?: number }) {
  const productEvidence = supplier.evidence?.find(({ field }) => field === 'products')
  const primary = supplier.sources.find(({ url }) => url === productEvidence?.url)
    ?? supplier.sources.find(({ url }) => url === supplier.website)
  const ordered = primary ? [primary, ...supplier.sources.filter(({ url }) => url !== primary.url)] : supplier.sources
  const visible = ordered.slice(0, limit ?? 4)
  const additional = limit === undefined ? ordered.slice(visible.length) : []
  return (
    <>
      <SourceList sources={visible} />
      {additional.length > 0 && (
        <details className="additional-sources">
          <summary>Ещё источники ({additional.length})</summary>
          <SourceList sources={additional} />
        </details>
      )}
    </>
  )
}

function SourceList({ sources }: { sources: Supplier['sources'] }) {
  return (
    <ul className="fact-links">
      {sources.map(({ url, label }) => (
        <li key={url}>
          <a href={url} target="_blank" rel="noopener noreferrer">
            {label} <ArrowUpRight size={13} aria-hidden="true" />
          </a>
        </li>
      ))}
    </ul>
  )
}

export function EvidenceLink({ supplier, field }: { supplier: Supplier; field: string }) {
  const fact = supplier.evidence?.find((entry) => entry.field === field)
  if (!fact) return null
  const date = fact.observed_at && Number.isFinite(Date.parse(fact.observed_at)) ? catalogDate(fact.observed_at) : null
  return (
    <a className="evidence-link" href={fact.url} target="_blank" rel="noopener noreferrer">
      Источник{date && <> · <time dateTime={fact.observed_at ?? undefined}>{date}</time></>}
      <ArrowUpRight size={12} aria-hidden="true" />
    </a>
  )
}
