import { useCallback, useRef, useState } from 'react'
import { catalogDate, supplierCompanyId, type Supplier } from './catalog/model'
import type { SupplierResponse } from './catalog/responses'
import { useCatalog } from './catalog/useCatalog'
import { useComparison } from './catalog/useComparison'
import { useResponses } from './catalog/useResponses'
import { ComparisonTable } from './components/ComparisonTable'
import { Modal } from './components/Modal'
import { ResponseEditor, type ResponseDraft } from './components/ResponseEditor'
import { SearchForm } from './components/SearchForm'
import { SupplierDetails } from './components/SupplierDetails'
import { SupplierResults } from './components/SupplierResults'
import { supplierCheckboxId } from './components/SupplierRows'

type Inspection = { kind: 'details'; supplier: Supplier } | { kind: 'compare'; wide: boolean; removeOnClose?: Supplier }
interface Editor {
  supplier: Supplier
  opener: HTMLButtonElement
  saved?: SupplierResponse
  draft?: ResponseDraft
  closing: boolean
}
const SEARCH_FORM_ID = 'supplier-search'

export function App() {
  const { catalog, result, searchStatus, pageStatus, filters, submit, retry, loadMore } = useCatalog()
  const { selected, selectedIds, status: shortlistStatus, retry: restoreShortlist, toggle } = useComparison(catalog?.catalog_revision)
  const { records, update } = useResponses()
  const [view, setView] = useState<Inspection | null>(null)
  const [editor, setEditor] = useState<Editor | null>(null)
  const drafts = useRef(new Map<string, ResponseDraft>())
  const pageTitle = useRef<HTMLHeadingElement | null>(null)
  const comparisonReturnId = useRef<string | null>(null)
  const getResultsFocus = useCallback(
    () => document.getElementById(`${SEARCH_FORM_ID}-results-status`) ?? pageTitle.current,
    [],
  )
  const getComparisonFocus = useCallback(() => {
    const id = comparisonReturnId.current
    return (id ? document.getElementById(id) : null) ?? getResultsFocus()
  }, [getResultsFocus])
  const openDetails = useCallback((supplier: Supplier) => {
    comparisonReturnId.current = null
    setView({ kind: 'details', supplier })
  }, [])
  const error = searchStatus === 'error'
  const pending = !error && (searchStatus === 'pending' || !catalog)
  const filtered = Boolean(filters.q || filters.category || filters.region)
  const closeModal = useCallback(() => {
    if (view?.kind === 'compare' && view.removeOnClose) toggle(view.removeOnClose)
    setView(null)
  }, [view, toggle])

  const title = view?.kind === 'details' ? view.supplier.name
    : selectedIds.length < 2 ? 'Выбранные поставщики' : 'Сравнение поставщиков'

  const closeEditor = useCallback(() => {
    setEditor((current) => current && !current.closing ? { ...current, closing: true } : current)
  }, [])
  const removeEditor = useCallback(() => setEditor(null), [])

  function openResponse(supplier: Supplier, opener: HTMLButtonElement) {
    setEditor({
      supplier, opener, saved: records.get(supplier.id), draft: drafts.current.get(supplier.id), closing: false,
    })
  }

  function removeFromComparison(supplier: Supplier) {
    if (selected.length === 1 && view?.kind === 'compare') {
      comparisonReturnId.current = supplierCheckboxId(supplier.id)
      setView({ ...view, removeOnClose: supplier })
    } else {
      toggle(supplier)
    }
  }

  return (
    <>
      <a className="skip-link" href={`#${SEARCH_FORM_ID}`}>Перейти к поиску</a>
      <header className="site-header">
        <div className="page-width header-inner">
          <a className="brand" href="#top" aria-label="FoodSource, к началу страницы">FoodSource<span>.</span></a>
        </div>
      </header>

      <main className="page-width main-page" id="top" aria-labelledby="page-title">
        <header className="page-heading">
          <div>
            <h1 className="page-title" id="page-title" ref={pageTitle} tabIndex={-1}>Поставщики</h1>
            <p className="page-purpose">Подберите поставщиков по товару и региону, сравните условия и запишите ответы.</p>
          </div>
          <p className="catalog-date">
            {catalog && <>Снимок каталога от <time dateTime={catalog.updated_at}>{catalogDate(catalog.updated_at)}</time></>}
          </p>
        </header>

        <SearchForm formId={SEARCH_FORM_ID} catalog={catalog} pending={pending} filters={filters} onSubmit={submit} />
        <SupplierResults
          formId={SEARCH_FORM_ID}
          catalog={catalog}
          result={result}
          pageStatus={pageStatus}
          pending={pending}
          error={error}
          filtered={filtered}
          selected={selectedIds}
          selectedCompanies={new Set(selected.map(supplierCompanyId))}
          onToggle={toggle}
          onDetails={openDetails}
          onCompare={() => {
            comparisonReturnId.current = null
            setView({ kind: 'compare', wide: selectedIds.length >= 2 })
          }}
          onRetry={retry}
          onLoadMore={loadMore}
        />
      </main>

      {view && catalog && (
        <Modal
          title={title}
          wide={view.kind === 'compare' && view.wide}
          closeRequested={view.kind === 'compare' && Boolean(view.removeOnClose)}
          focusRevision={view.kind === 'compare' ? `${selectedIds.length}:${shortlistStatus}` : view.supplier.id}
          getFocusFallback={getComparisonFocus}
          onClose={closeModal}
          overlay={editor ? {
            title: 'Запись об ответе',
            dismissLabel: 'Закрыть запись',
            opener: editor.opener,
            closing: editor.closing,
            onDismiss: closeEditor,
            onExited: removeEditor,
            children: (
              <ResponseEditor
                supplier={editor.supplier}
                response={editor.saved}
                draft={editor.draft}
                onDraftChange={(draft) => drafts.current.set(editor.supplier.id, draft)}
                onBack={closeEditor}
                onSave={(response) => {
                  if (!update(editor.supplier.id, response)) return false
                  drafts.current.delete(editor.supplier.id)
                  closeEditor()
                  return true
                }}
              />
            ),
          } : undefined}
        >
          {view.kind === 'details' && (
            <SupplierDetails supplier={view.supplier} catalog={catalog} response={records.get(view.supplier.id)} onRecord={openResponse} />
          )}
          {view.kind === 'compare' && (
            shortlistStatus === 'ready' ? (
              <ComparisonTable suppliers={selected} catalog={catalog} responses={records} onRemove={removeFromComparison} onRecord={openResponse} />
            ) : shortlistStatus === 'pending' ? (
              <p role="status">Восстанавливаем подборку…</p>
            ) : (
              <div className="comparison-restore">
                <p role="status">Не удалось загрузить сохранённую подборку.</p>
                <button className="button button--outline" type="button" onClick={restoreShortlist}>Повторить</button>
              </div>
            )
          )}
        </Modal>
      )}
    </>
  )
}
