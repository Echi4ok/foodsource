import { Popover } from '@base-ui/react/popover'
import { Check } from 'lucide-react'
import { useId, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { DropdownValue, type DropdownOption } from './ui/Dropdown'

interface Props {
  options: DropdownOption[] | undefined
  value: string
  onChange: (value: string) => void
}

const MATCH_LIMIT = 12
const ALL: DropdownOption = { id: '', label: 'Все регионы' }
const fold = (text: string) => text.toLocaleLowerCase('ru').replaceAll('ё', 'е').trim()

export function GeographySelect({ options, value, onChange }: Props) {
  const id = useId()
  const search = useRef<HTMLInputElement>(null)
  const list = useRef<HTMLUListElement>(null)
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [focusedId, setFocusedId] = useState(value)
  const selected = options?.find((option) => option.id === value)
  const indexed = useMemo(() => (options ?? []).map((option) => ({
    option,
    name: fold(option.label),
    text: fold(`${option.label} ${option.group ?? ''}`),
  })), [options])
  const needle = fold(query)
  const terms = needle.split(/\s+/).filter(Boolean)
  const rank = (name: string) => name === needle ? 2 : terms.every((term) => name.includes(term)) ? 1 : 0
  const candidates = terms.length
    ? indexed
      .filter(({ text }) => terms.every((term) => text.includes(term)))
      .sort((left, right) => rank(right.name) - rank(left.name))
      .map(({ option }) => option)
    : selected
      ? [ALL, selected, ...(options ?? []).filter(({ id }) => id !== value)]
      : [ALL, ...(options ?? [])]
  const visible = candidates.slice(0, MATCH_LIMIT)
  const tabbable = visible.some((option) => option.id === focusedId) ? focusedId : visible[0]?.id

  function focusOption(index: number) {
    list.current?.querySelectorAll<HTMLButtonElement>('button')[index]?.focus()
  }

  function choose(next: string) {
    setOpen(false)
    if (next !== value) onChange(next)
  }

  function navigate(event: KeyboardEvent<HTMLUListElement>) {
    const index = visible.findIndex((option) => option.id === focusedId)
    const next = {
      ArrowUp: Math.max(0, index - 1),
      ArrowDown: Math.min(visible.length - 1, index + 1),
      Home: 0,
      End: visible.length - 1,
    }[event.key]
    if (next === undefined) return
    event.preventDefault()
    focusOption(next)
  }

  function show(next: boolean) {
    if (next) {
      setQuery('')
      setFocusedId(value)
    }
    setOpen(next)
  }

  return (
    <div className="field">
      <span id={`${id}-label`}>Город или регион</span>
      <Popover.Root open={open} onOpenChange={show} modal={false}>
        <Popover.Trigger
          className="filter-trigger"
          type="button"
          disabled={!options}
          aria-labelledby={`${id}-label ${id}-value`}
          aria-haspopup="listbox"
          onKeyDown={(event) => {
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
              event.preventDefault()
              if (!open) event.currentTarget.click()
              else search.current?.focus()
            }
          }}
        >
          <DropdownValue id={`${id}-value`} label={selected?.label ?? ALL.label} />
        </Popover.Trigger>
        <Popover.Portal>
          <Popover.Positioner className="dropdown-positioner dropdown-positioner--geography" align="start" sideOffset={5} positionMethod="fixed">
            <Popover.Popup className="filter-popup" initialFocus={search} role="group" aria-labelledby={`${id}-label`}>
              <div className="filter-search">
                <input
                  ref={search}
                  type="search"
                  aria-label="Поиск города или региона"
                  placeholder="Город или регион"
                  autoComplete="off"
                  maxLength={120}
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.nativeEvent.isComposing) return
                    if (event.key === 'Escape') {
                      event.preventDefault()
                      setOpen(false)
                    } else if (event.key === 'ArrowDown' || event.key === 'Enter') {
                      event.preventDefault()
                      if (event.key === 'Enter' && candidates.length === 1) choose(candidates[0].id)
                      else focusOption(0)
                    }
                  }}
                />
              </div>
              <ul
                className="filter-options"
                role="listbox"
                aria-labelledby={`${id}-label`}
                hidden={visible.length === 0}
                ref={list}
                onKeyDown={navigate}
              >
                {visible.map((option) => (
                  <li key={option.id} role="presentation">
                    <button
                      type="button"
                      role="option"
                      aria-selected={option.id === value}
                      tabIndex={option.id === tabbable ? 0 : -1}
                      onFocus={() => setFocusedId(option.id)}
                      onClick={() => choose(option.id)}
                    >
                      <span>{option.label}{option.group && <small>{option.group}</small>}</span>
                      {option.id === value && <Check className="dropdown-check" size={16} aria-hidden="true" />}
                    </button>
                  </li>
                ))}
              </ul>
              <p className="filter-search-status" aria-live="polite">
                {candidates.length > MATCH_LIMIT ? 'Уточните название, чтобы увидеть остальные варианты.' : candidates.length === 0 ? 'Совпадений нет.' : ''}
              </p>
            </Popover.Popup>
          </Popover.Positioner>
        </Popover.Portal>
      </Popover.Root>
    </div>
  )
}
