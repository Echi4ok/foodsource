import type { Supplier } from './model'

export const CURRENCIES = ['RUB', 'BYN', 'KZT', 'CNY', 'EUR', 'USD'] as const
export const PRICE_UNITS = {
  '': 'Не указана',
  kg: 'кг',
  g: 'г',
  t: 'т',
  l: 'л',
  item: 'шт.',
  pack: 'упаковка',
} as const

export type Currency = (typeof CURRENCIES)[number]
export type PriceUnit = keyof typeof PRICE_UNITS

export interface OfferPrice {
  amount: number
  currency: Currency
  unit: PriceUnit
}

export interface SupplierResponse {
  product: string
  price: OfferPrice | null
  minimumOrder: string
  contactedAt: string
  note: string
  nextStep: string
}

export function validCurrency(value: unknown): value is Currency {
  return CURRENCIES.some((currency) => currency === value)
}

export function validUnit(value: unknown): value is PriceUnit {
  return typeof value === 'string' && Object.hasOwn(PRICE_UNITS, value)
}

export function formatPrice(price: OfferPrice): string {
  const value = new Intl.NumberFormat('ru', { style: 'currency', currency: price.currency }).format(price.amount)
  return price.unit ? `${value} / ${PRICE_UNITS[price.unit]}` : value
}

export function dateTimeLocal(value: string): string {
  if (!value) return ''
  const date = new Date(value)
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16)
}

export function questionsFor(supplier: Supplier, response?: SupplierResponse) {
  return [
    !supplier.price && !response?.price ? 'Цена за единицу, НДС и срок действия предложения' : '',
    !supplier.minimum_order && !response?.minimumOrder ? 'Минимальный объём заказа' : '',
    !supplier.documents ? 'Документы и сертификаты' : '',
    !supplier.delivery ? 'Сроки и стоимость доставки' : '',
    !supplier.service_region_ids.length ? 'Доставляете ли в мой город' : '',
  ].filter(Boolean)
}

function trimText(value: unknown, maxLength: number) {
  return typeof value === 'string' ? value.trim().slice(0, maxLength) : ''
}

export function parseSupplierResponse(value: unknown): SupplierResponse | null {
  if (typeof value !== 'object' || value === null) return null
  const input = value as Record<string, unknown>
  const product = trimText(input.product, 120)
  if (!product) return null

  const rawPrice = input.price as Partial<OfferPrice> | undefined
  const amount = rawPrice?.amount
  const currency = rawPrice?.currency
  const price = typeof amount === 'number' && Number.isFinite(amount) && amount >= 0 && amount < 1e10 && validCurrency(currency)
    ? { amount, currency, unit: validUnit(rawPrice?.unit) ? rawPrice.unit : '' as const }
    : null
  const time = new Date(trimText(input.contactedAt, 30))

  return {
    product,
    price,
    minimumOrder: trimText(input.minimumOrder, 120),
    contactedAt: Number.isNaN(time.getTime()) ? '' : time.toISOString(),
    note: trimText(input.note, 800),
    nextStep: trimText(input.nextStep, 160),
  }
}
