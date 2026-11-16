import { memo } from 'react'
import { Check } from 'lucide-react'
import { optionLabel, type Catalog, type Supplier } from '../catalog/model'

interface Props {
  supplier: Supplier
  catalog: Catalog
  selected: boolean
  disabled: boolean
  onToggle: (supplier: Supplier) => void
  onDetails: (supplier: Supplier) => void
}

export function supplierCheckboxId(supplierId: string) {
  return `compare-supplier-${supplierId}`
}

export const SupplierRow = memo(function SupplierRow({ supplier, catalog, selected, disabled, onToggle, onDetails }: Props) {
  const category = supplier.category_ids.map((id) => optionLabel(catalog.categories, id)).join(' / ')
  const region = optionLabel(catalog.regions, supplier.region_id)
  const delivery = supplier.reasons.find(({ code }) => code === 'delivery')
  const products = supplier.products.slice(0, 3).join(', ')

  return (
    <tr role="row" className={selected ? 'is-selected' : undefined}>
      <td role="cell" className="supplier-identity">
        <button className="supplier-link" type="button" onClick={() => onDetails(supplier)} title={supplier.name}>{supplier.name}</button>
        <span className="row-secondary" title={category}>{category}</span>
      </td>
      <td role="cell" className="supplier-assortment">
        <span title={supplier.products.join(', ')}>{products || supplier.description}</span>
      </td>
      <td role="cell" className="supplier-geography">
        <span>{region}</span>
        {delivery && <span className="row-secondary" title={delivery.label}>{delivery.label}</span>}
      </td>
      <td role="cell" className="supplier-selection">
        <label className="selection-target">
          <input
            id={supplierCheckboxId(supplier.id)}
            type="checkbox"
            checked={selected}
            disabled={disabled}
            onChange={() => onToggle(supplier)}
            aria-label={`Сравнить: ${supplier.name}`}
          />
          <span className="selection-mark" aria-hidden="true"><Check size={13} strokeWidth={2.4} /></span>
        </label>
      </td>
    </tr>
  )
})

export function SupplierPlaceholder() {
  return (
    <tr role="row" className="supplier-placeholder" aria-hidden="true">
      <td role="cell" className="supplier-identity"><span /><span /></td>
      <td role="cell" className="supplier-assortment"><span /></td>
      <td role="cell" className="supplier-geography"><span /></td>
      <td role="cell" className="supplier-selection"><span /></td>
    </tr>
  )
}
