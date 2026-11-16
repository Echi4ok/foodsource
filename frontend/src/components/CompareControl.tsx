import { COMPARE_LIMIT } from '../catalog/model'

export function CompareControl({ count, onOpen }: { count: number; onOpen: () => void }) {
  return (
    <div className={`compare-control${count > 0 ? ' is-active' : ''}`}>
      <button
        className="button compare-toggle"
        type="button"
        disabled={count === 0}
        onClick={onOpen}
      >
        <span>Сравнить</span>
        <span className="compare-count">{count}/{COMPARE_LIMIT}</span>
      </button>
    </div>
  )
}
