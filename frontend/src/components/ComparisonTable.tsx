import type { ReactNode } from 'react'
import { X } from 'lucide-react'
import { COMMERCIAL_FIELDS, optionLabel, type Catalog, type Supplier } from '../catalog/model'
import { questionsFor, type SupplierResponse } from '../catalog/responses'
import { EvidenceLink, FieldValue, SourceLinks, SupplierLinks } from './SupplierFacts'
import { ResponseSummary } from './ResponseSummary'

interface Row {
  key: string
  values: ReactNode[]
}

interface Props {
  suppliers: Supplier[]
  catalog: Catalog
  responses: ReadonlyMap<string, SupplierResponse>
  onRemove: (supplier: Supplier) => void
  onRecord: (supplier: Supplier, opener: HTMLButtonElement) => void
}

function RemoveSelection({ supplier, onRemove }: { supplier: Supplier; onRemove: Props['onRemove'] }) {
  return (
    <button
      className="icon-button"
      type="button"
      onClick={() => onRemove(supplier)}
      aria-label={`Убрать из сравнения: ${supplier.name}`}
    ><X size={16} aria-hidden="true" /></button>
  )
}

export function ComparisonTable({ suppliers, catalog, responses, onRemove, onRecord }: Props) {
  function nextStep(supplier: Supplier) {
    const response = responses.get(supplier.id)
    const label = response ? 'Изменить запись' : 'Записать ответ'
    return (
      <div className="comparison-next">
        {response?.nextStep && <p>{response.nextStep}</p>}
        <button className="button button--outline" type="button" onClick={(event) => onRecord(supplier, event.currentTarget)} aria-label={`${label} — ${supplier.name}`}>
          {label}
        </button>
      </div>
    )
  }

  if (suppliers.length < 2) {
    const supplier = suppliers[0]
    return (
      <div className="comparison-hint">
        {supplier && <p className="selected-name">{supplier.name} <RemoveSelection supplier={supplier} onRemove={onRemove} /></p>}
        {supplier && (
          <div className="comparison-single">
            <SupplierLinks supplier={supplier} />
            {nextStep(supplier)}
          </div>
        )}
        <p>Чтобы сравнить условия, выберите в каталоге двух или трёх поставщиков.</p>
      </div>
    )
  }

  const rows: Row[] = [
    {
      key: 'Источники',
      values: suppliers.map((supplier) => <SourceLinks supplier={supplier} limit={1} />),
    },
    {
      key: 'Связаться',
      values: suppliers.map((supplier) => <SupplierLinks supplier={supplier} />),
    },
    {
      key: 'Следующий шаг',
      values: suppliers.map(nextStep),
    },
  ]
  if (suppliers.some(({ id }) => responses.has(id))) {
    rows.push({
      key: 'Мои записи',
      values: suppliers.map(({ id }) => {
        const response = responses.get(id)
        return response ? <ResponseSummary response={response} /> : <FieldValue value={null} />
      }),
    })
  }
  const questions = suppliers.map((supplier) => questionsFor(supplier, responses.get(supplier.id)))
  if (questions.some((list) => list.length)) {
    rows.push({
      key: 'Что уточнить',
      values: questions.map((list) => list.length ? (
        <ul className="comparison-questions">{list.map((item) => <li key={item}>{item}</li>)}</ul>
      ) : <FieldValue value={null} />),
    })
  }
  rows.push(
    {
      key: 'Город / регион',
      values: suppliers.map((supplier) => optionLabel(catalog.regions, supplier.region_id)),
    },
    {
      key: 'Ассортимент',
      values: suppliers.map((supplier) => (
        <>
          <FieldValue value={supplier.products.join(', ') || null} />
          <EvidenceLink supplier={supplier} field="products" />
        </>
      )),
    },
    ...COMMERCIAL_FIELDS.filter(({ key }) => suppliers.some((supplier) => supplier[key])).map(({ key, label }) => ({
      key: label,
      values: suppliers.map((supplier) => (
        <>
          <FieldValue value={supplier[key]} />
          <EvidenceLink supplier={supplier} field={key} />
        </>
      )),
    })),
  )
  if (suppliers.some(({ service_region_ids }) => service_region_ids.length)) {
    rows.push({
      key: 'География доставки',
      values: suppliers.map((supplier) => (
        <FieldValue value={supplier.service_region_ids.map((id) => optionLabel(catalog.regions, id)).join(', ') || null} />
      )),
    })
  }

  return (
    <>
      <p className="compare-scroll-hint">Прокрутите таблицу, чтобы увидеть все компании.</p>
      <div className="comparison-scroll" role="region" aria-label="Таблица условий поставщиков" tabIndex={0}>
        <table className="comparison-table">
          <thead>
            <tr>
              <th scope="col">Параметр</th>
              {suppliers.map((supplier) => (
                <th scope="col" key={supplier.id}>
                  <span className="table-supplier">
                    {supplier.name} <RemoveSelection supplier={supplier} onRemove={onRemove} />
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map(({ key, values }) => (
              <tr key={key}>
                <th scope="row">{key}</th>
                {values.map((value, index) => <td key={suppliers[index].id}>{value}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}
