import { formatPrice, type SupplierResponse } from '../catalog/responses'

const contactTime = new Intl.DateTimeFormat('ru', { dateStyle: 'short', timeStyle: 'short' })

export function ResponseSummary({ response }: { response: SupplierResponse }) {
  return (
    <div className="response-summary">
      <strong>{response.product}</strong>
      <dl>
        {response.price && <div><dt>Цена</dt><dd>{formatPrice(response.price)}</dd></div>}
        {response.minimumOrder && <div><dt>Мин. заказ</dt><dd>{response.minimumOrder}</dd></div>}
        {response.contactedAt && (
          <div><dt>Связь</dt><dd><time dateTime={response.contactedAt}>{contactTime.format(new Date(response.contactedAt))}</time></dd></div>
        )}
      </dl>
      {response.note && <p className="response-note">{response.note}</p>}
    </div>
  )
}
