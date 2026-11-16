import { COMMERCIAL_FIELDS, optionLabel, type Catalog, type Supplier } from '../catalog/model'
import { questionsFor, type SupplierResponse } from '../catalog/responses'
import { EvidenceLink, SourceLinks, SupplierLinks } from './SupplierFacts'
import { ResponseSummary } from './ResponseSummary'

interface Props {
  supplier: Supplier
  catalog: Catalog
  response?: SupplierResponse
  onRecord: (supplier: Supplier, opener: HTMLButtonElement) => void
}

export function SupplierDetails({ supplier, catalog, response, onRecord }: Props) {
  const region = optionLabel(catalog.regions, supplier.region_id)
  const serviceRegions = supplier.service_region_ids.map((id) => optionLabel(catalog.regions, id))
  const conditions = COMMERCIAL_FIELDS.filter(({ key }) => supplier[key])
  const questions = questionsFor(supplier, response)

  return (
    <div className="details">
      <div className="detail-identity">
        <p className="detail-region">{region}</p>
        <p>{supplier.description}</p>
      </div>

      <section className="detail-contact">
        <div>
          <h3>Источники</h3>
          <SourceLinks supplier={supplier} />
        </div>
        <div>
          <h3>Связаться</h3>
          <SupplierLinks supplier={supplier} />
        </div>
      </section>

      <section className="detail-next">
        <div className="section-toolbar">
          <h3>{response ? 'Моя запись' : 'Следующий шаг'}</h3>
          <button className="button button--outline" type="button" onClick={(event) => onRecord(supplier, event.currentTarget)}>
            {response ? 'Изменить запись' : 'Записать ответ'}
          </button>
        </div>
        {response ? (
          <>
            <ResponseSummary response={response} />
            {response.nextStep && <p className="response-next"><strong>Следующий шаг</strong> {response.nextStep}</p>}
          </>
        ) : <p>Уточните условия по нужному товару и запишите ответ поставщика.</p>}
      </section>

      {supplier.products.length > 0 && (
        <section>
          <h3>Ассортимент</h3>
          <p>{supplier.products.join(', ')}</p>
          <EvidenceLink supplier={supplier} field="products" />
        </section>
      )}

      {supplier.reasons.length > 0 && (
        <section className="detail-evidence">
          <h3>Почему в выдаче</h3>
          <ul className="reason-list">{supplier.reasons.map(({ code, label }) => <li key={code}>{label}</li>)}</ul>
        </section>
      )}

      {(conditions.length > 0 || serviceRegions.length > 0) && (
        <section>
          <h3>Опубликованные условия</h3>
          <dl className="detail-fields">
            {conditions.map(({ key, label }) => (
              <div key={key} className={key === 'delivery' ? 'detail-field--wide' : undefined}>
                <dt>{label}</dt>
                <dd>{supplier[key]}<EvidenceLink supplier={supplier} field={key} /></dd>
              </div>
            ))}
            {serviceRegions.length > 0 && (
              <div className="detail-field--wide"><dt>География доставки</dt><dd>{serviceRegions.join(', ')}</dd></div>
            )}
          </dl>
        </section>
      )}

      {questions.length > 0 && (
        <section>
          <h3>Что уточнить</h3>
          <ul className="reason-list">{questions.map((question) => <li key={question}>{question}</li>)}</ul>
        </section>
      )}
    </div>
  )
}
