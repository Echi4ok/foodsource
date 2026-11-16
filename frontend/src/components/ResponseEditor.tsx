import { useRef, useState, type FormEvent } from 'react'
import type { Supplier } from '../catalog/model'
import {
  CURRENCIES, PRICE_UNITS, dateTimeLocal, validCurrency, validUnit, type Currency, type PriceUnit, type SupplierResponse,
} from '../catalog/responses'
import { DropdownField } from './ui/Dropdown'

export interface ResponseDraft {
  product: string
  amount: string
  currency: Currency
  unit: PriceUnit
  minimumOrder: string
  contactedAt: string
  nextStep: string
  note: string
}

const currencyOptions = CURRENCIES.map((currency) => ({ id: currency, label: currency }))
const unitOptions = Object.entries(PRICE_UNITS).map(([id, label]) => ({ id, label }))

interface Props {
  supplier: Supplier
  response?: SupplierResponse
  draft?: ResponseDraft
  onDraftChange: (draft: ResponseDraft) => void
  onSave: (response: SupplierResponse | null) => boolean
  onBack: () => void
}

export function ResponseEditor({ supplier, response, draft, onDraftChange, onSave, onBack }: Props) {
  const [defaultDate] = useState(() => dateTimeLocal(new Date().toISOString()))
  const [error, setError] = useState('')
  const [currency, setCurrency] = useState<Currency>(draft?.currency ?? response?.price?.currency ?? 'RUB')
  const [unit, setUnit] = useState<PriceUnit>(draft?.unit ?? response?.price?.unit ?? '')
  const amountInput = useRef<HTMLInputElement>(null)
  const form = useRef<HTMLFormElement>(null)

  function rememberDraft(nextCurrency = currency, nextUnit = unit) {
    if (!form.current) return
    const data = new FormData(form.current)
    const read = (field: string) => String(data.get(field) ?? '')
    onDraftChange({
      product: read('product'),
      amount: read('amount'),
      currency: nextCurrency,
      unit: nextUnit,
      minimumOrder: read('minimumOrder'),
      contactedAt: read('contactedAt'),
      nextStep: read('nextStep'),
      note: read('note'),
    })
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    const read = (field: string) => String(data.get(field) ?? '').trim()
    const product = read('product')
    const amount = read('amount').replaceAll(/\s/g, '').replace(',', '.')
    const contactDate = new Date(read('contactedAt'))

    if (!product) {
      setError('Укажите товар, о котором ответил поставщик.')
      event.currentTarget.querySelector<HTMLInputElement>('[name="product"]')?.focus()
      return
    }
    if (amount && !/^\d{1,10}(\.\d{1,2})?$/.test(amount)) {
      setError('Укажите сумму с точностью до двух знаков после запятой.')
      amountInput.current?.focus()
      return
    }
    if (!onSave({
      product,
      price: amount ? { amount: Number(amount), currency, unit } : null,
      minimumOrder: read('minimumOrder'),
      contactedAt: Number.isNaN(contactDate.getTime()) ? '' : contactDate.toISOString(),
      note: read('note'),
      nextStep: read('nextStep'),
    })) {
      setError('Браузер не разрешил сохранить запись.')
    }
  }

  return (
    <form className="response-editor" ref={form} onInput={() => rememberDraft()} onSubmit={handleSubmit}>
      <div>
        <p className="detail-region">{supplier.name}</p>
        <p className="local-record-note">Запись хранится в этом браузере и видна только в нём.</p>
      </div>
      <div className="response-content">
        <div className="response-group">
          <label className="field">
            <span>Товар</span>
            <input name="product" required maxLength={120} defaultValue={draft?.product ?? response?.product ?? ''} placeholder={supplier.products[0]} />
          </label>
          <div className="response-fields response-fields--price">
            <label className="field">
              <span>Цена</span>
              <input
                ref={amountInput}
                name="amount"
                inputMode="decimal"
                maxLength={20}
                defaultValue={draft?.amount ?? response?.price?.amount ?? ''}
                placeholder="Сумма"
                onInput={() => { if (error) setError('') }}
              />
            </label>
            <DropdownField label="Валюта" options={currencyOptions} value={currency} onChange={(next) => {
              if (!validCurrency(next)) return
              setCurrency(next)
              rememberDraft(next, unit)
            }} />
            <DropdownField label="За единицу" options={unitOptions} value={unit} onChange={(next) => {
              if (!validUnit(next)) return
              setUnit(next)
              rememberDraft(currency, next)
            }} />
          </div>
          <div className="response-fields">
            <label className="field">
              <span>Мин. заказ</span>
              <input name="minimumOrder" maxLength={120} defaultValue={draft?.minimumOrder ?? response?.minimumOrder ?? ''} placeholder="Например, 25 кг" />
            </label>
            <label className="field">
              <span>Дата и время связи</span>
              <input name="contactedAt" type="datetime-local" defaultValue={draft?.contactedAt ?? (response ? dateTimeLocal(response.contactedAt) : defaultDate)} />
            </label>
          </div>
        </div>
        <div className="response-group">
          <label className="field">
            <span>Следующий шаг</span>
            <input name="nextStep" maxLength={160} defaultValue={draft?.nextStep ?? response?.nextStep ?? ''} placeholder="Например, запросить образец" />
          </label>
          <label className="field">
            <span>Заметка</span>
            <textarea name="note" maxLength={800} rows={3} defaultValue={draft?.note ?? response?.note ?? ''} />
          </label>
        </div>
      </div>
      <p className="response-error" role="alert">{error}</p>
      <div className="response-actions">
        <button className="button button--primary" type="submit">Сохранить запись</button>
        <button className="button button--outline" type="button" onClick={onBack}>Назад</button>
        {response && (
          <button className="button" type="button" onClick={() => {
            if (!onSave(null)) setError('Браузер не разрешил удалить запись.')
          }}>Удалить запись</button>
        )}
      </div>
    </form>
  )
}
