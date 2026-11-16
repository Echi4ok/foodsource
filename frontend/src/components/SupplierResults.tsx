import { useLayoutEffect, useMemo, useRef } from 'react'
import { COMPARE_LIMIT, supplierCompanyId, supplierCount, type Catalog, type LoadedSearchResult, type PageStatus, type Supplier } from '../catalog/model'
import { CompareControl } from './CompareControl'
import { SupplierPlaceholder, SupplierRow } from './SupplierRows'
import { useResultTransition } from './useResultTransition'

interface Props {
  formId: string
  catalog: Catalog | null
  result: LoadedSearchResult | null
  pageStatus: PageStatus
  pending: boolean
  error: boolean
  filtered: boolean
  selected: string[]
  selectedCompanies: ReadonlySet<string>
  onToggle: (supplier: Supplier) => void
  onDetails: (supplier: Supplier) => void
  onCompare: () => void
  onRetry: () => void
  onLoadMore: () => void
}

export function SupplierResults({
  formId, catalog, result, pageStatus, pending, error, filtered, selected, selectedCompanies, onToggle, onDetails,
  onCompare, onRetry, onLoadMore,
}: Props) {
  const target = useMemo(() => {
    if (error) return { kind: 'error' as const, transitionKey: 'error' }
    if (!catalog || !result) return { kind: 'skeleton' as const, transitionKey: 'skeleton' }
    if (!result.items.length) return { kind: 'empty' as const, total: result.total, transitionKey: result.revision }
    return { kind: 'rows' as const, catalog, result, transitionKey: result.revision }
  }, [catalog, result, error])
  const { displayed, changing, pane, body, focusFallback } = useResultTransition(target, pending && !error)
  const content = displayed.content
  const visibleResult = content.kind === 'rows' ? content.result : null
  const loadMoreButton = useRef<HTMLButtonElement>(null)
  const focusRequest = useRef<{ revision: number; loaded: number }>(null)
  const busy = pending || changing
  const pagePending = pageStatus === 'pending'
  const status = busy && !error
    ? 'Загрузка…'
    : content.kind === 'error'
      ? 'Ошибка загрузки'
      : supplierCount(content.kind === 'rows' ? content.result.total : content.kind === 'empty' ? content.total : 0)

  useLayoutEffect(() => {
    const request = focusRequest.current
    if (!request || !visibleResult) return
    if (visibleResult.revision !== request.revision) {
      focusRequest.current = null
      return
    }
    if (visibleResult.items.length <= request.loaded) return
    focusRequest.current = null
    if (document.activeElement === loadMoreButton.current || document.activeElement === document.body) {
      body.current?.rows[request.loaded]?.querySelector('button')?.focus({ preventScroll: true })
    }
  }, [body, visibleResult])

  return (
    <section className="results" id="results" aria-label="Результаты поиска">
      <header className="results-heading">
        <div className="results-status-group">
          <p id={`${formId}-results-status`} className="results-status" role="status" ref={focusFallback} tabIndex={-1}>{status}</p>
          <button
            className="button reset-button"
            type="reset"
            form={formId}
            disabled={!filtered}
          >Сбросить</button>
        </div>
        <span className="mobile-selection-label" aria-hidden="true">Выбор</span>
        <CompareControl count={selected.length} onOpen={onCompare} />
      </header>

      <div className="results-content" aria-busy={busy} ref={pane}>
        <table className="supplier-table" role="table" aria-label="Поставщики">
          <thead role="rowgroup">
            <tr role="row">
              <th scope="col" role="columnheader">Поставщик</th>
              <th scope="col" role="columnheader">Ассортимент</th>
              <th scope="col" role="columnheader">География</th>
              <th scope="col" role="columnheader">Выбор</th>
            </tr>
          </thead>
          <tbody
            ref={body}
            role="rowgroup"
            className={`supplier-tbody${pending && !error ? ' is-pending' : ''}`}
            key={displayed.revision}
          >
            {content.kind === 'rows' && content.result.items.map((supplier) => {
              const checked = selected.includes(supplier.id)
              return (
                <SupplierRow
                  key={supplier.id}
                  supplier={supplier}
                  catalog={content.catalog}
                  selected={checked}
                  disabled={selected.length >= COMPARE_LIMIT && !checked && !selectedCompanies.has(supplierCompanyId(supplier))}
                  onToggle={onToggle}
                  onDetails={onDetails}
                />
              )
            })}
            {content.kind === 'skeleton' && Array.from({ length: 5 }, (_, index) => <SupplierPlaceholder key={index} />)}
            {(content.kind === 'error' || content.kind === 'empty') && (
              <tr role="row" className="supplier-state-row">
                <td role="cell" colSpan={4} className="supplier-message-cell">
                  <div className="result-message">
                    {content.kind === 'error' ? (
                      <>
                        <h2>Не удалось загрузить каталог</h2>
                        <button type="button" className="button button--outline" onClick={onRetry} disabled={pending}>Повторить</button>
                      </>
                    ) : (
                      <>
                        <h2>Поставщики не найдены</h2>
                        <p>Попробуйте другой товар, категорию или регион.</p>
                      </>
                    )}
                  </div>
                </td>
              </tr>
            )}
          </tbody>
        </table>
        {visibleResult && (visibleResult.next_offset !== null || visibleResult.items.length > visibleResult.initialCount) && (
          <div className="results-pagination">
            <p aria-live="polite">
              {pageStatus === 'error'
                ? 'Не удалось загрузить.'
                : pagePending
                  ? 'Загружаем ещё…'
                  : `Показано ${visibleResult.items.length} из ${visibleResult.total}`}
            </p>
            {visibleResult.next_offset !== null && (
              <button
                ref={loadMoreButton}
                className="button button--outline"
                type="button"
                aria-disabled={busy || pagePending}
                onClick={(event) => {
                  if (busy || pagePending) return
                  focusRequest.current = event.currentTarget === document.activeElement
                    ? { revision: visibleResult.revision, loaded: visibleResult.items.length }
                    : null
                  onLoadMore()
                }}
              >
                {pageStatus === 'error' ? 'Повторить' : 'Показать ещё'}
              </button>
            )}
          </div>
        )}
      </div>
    </section>
  )
}
