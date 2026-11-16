import { Select } from '@base-ui/react/select'
import { Check, ChevronDown } from 'lucide-react'
import { createContext, useContext, useId, type RefObject } from 'react'

export const DropdownContainer = createContext<RefObject<HTMLElement | null> | undefined>(undefined)

export interface DropdownOption {
  id: string
  label: string
  group?: string
}

interface Props {
  label: string
  options: DropdownOption[]
  value: string
  disabled?: boolean
  onChange: (value: string) => void
}

export function DropdownValue({ id, label }: { id: string; label: string }) {
  return (
    <>
      <span id={id} title={label}>{label}</span>
      <ChevronDown size={16} aria-hidden="true" />
    </>
  )
}

export function DropdownField({ label, options, value, disabled = false, onChange }: Props) {
  const id = useId()
  const container = useContext(DropdownContainer)
  const selected = options.find((option) => option.id === value)

  return (
    <div className="field">
      <span id={`${id}-label`}>{label}</span>
      <Select.Root
        value={value}
        onValueChange={(next) => { if (next !== null && next !== value) onChange(next) }}
        disabled={disabled}
        modal={false}
        items={options.map((option) => ({ value: option.id, label: option.label }))}
      >
        <Select.Trigger className="filter-trigger" type="button" aria-labelledby={`${id}-label ${id}-value`}>
          <DropdownValue id={`${id}-value`} label={selected?.label ?? ''} />
        </Select.Trigger>
        <Select.Portal container={container}>
          <Select.Positioner
            className="dropdown-positioner"
            align="start"
            sideOffset={5}
            alignItemWithTrigger={false}
            positionMethod="fixed"
            collisionBoundary={container?.current ?? undefined}
          >
            <Select.Popup className="filter-popup">
              <Select.List className="filter-options">
                {options.map((option) => (
                  <Select.Item key={option.id} value={option.id}>
                    <Select.ItemText>{option.label}</Select.ItemText>
                    <Select.ItemIndicator className="dropdown-check"><Check size={16} aria-hidden="true" /></Select.ItemIndicator>
                  </Select.Item>
                ))}
              </Select.List>
            </Select.Popup>
          </Select.Positioner>
        </Select.Portal>
      </Select.Root>
    </div>
  )
}
