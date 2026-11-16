import { X } from 'lucide-react'
import { useId, useLayoutEffect, useRef, useState, type ReactNode, type Ref } from 'react'
import { DropdownContainer } from './ui/Dropdown'

interface Overlay {
  title: string
  dismissLabel?: string
  children: ReactNode
  closing: boolean
  opener: HTMLElement | null
  onDismiss: () => void
  onExited: () => void
}

interface Props {
  title: string
  wide?: boolean
  closeRequested?: boolean
  focusRevision?: number | string
  getFocusFallback?: () => HTMLElement | null
  onClose: () => void
  children: ReactNode
  overlay?: Overlay
}

interface SurfaceProps {
  title: string
  titleId: string
  headingRef: Ref<HTMLHeadingElement>
  paneRef?: Ref<HTMLDivElement>
  layer?: 'main' | 'overlay'
  dismissLabel?: string
  covered?: boolean
  closing?: boolean
  onDismiss: () => void
  children: ReactNode
}

function ModalSurface({ title, titleId, headingRef, paneRef, layer = 'main', dismissLabel = 'Закрыть', covered, closing, onDismiss, children }: SurfaceProps) {
  return (
    <div
      ref={paneRef}
      className={`modal-surface modal-${layer}`}
      inert={covered}
      data-closing={closing || undefined}
    >
      <header className="modal-heading">
        <h2 id={titleId} ref={headingRef} tabIndex={-1}>{title}</h2>
        <button type="button" className="icon-button close-button" onClick={onDismiss} aria-label={dismissLabel}>
          <X size={21} aria-hidden="true" />
        </button>
      </header>
      <div className="modal-body">{children}</div>
    </div>
  )
}

export function Modal({ title, wide = false, closeRequested = false, focusRevision, getFocusFallback, onClose, children, overlay }: Props) {
  const modal = useRef<HTMLDialogElement>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  const pointerOnBackdrop = useRef(false)
  const pointerOutsideDropdown = useRef(false)
  const overlayPane = useRef<HTMLDivElement>(null)
  const overlayHeading = useRef<HTMLHeadingElement>(null)
  const overlayOpener = useRef<HTMLElement | null>(null)
  const hadOverlay = useRef(false)
  const [dismissing, setDismissing] = useState(false)
  const closing = dismissing || closeRequested
  const titleId = useId()
  const overlayClosing = overlay?.closing ?? false
  const onOverlayExited = overlay?.onExited

  function dropdownIsOpen() {
    return Boolean(modal.current?.querySelector('.filter-popup[data-open]'))
  }

  function dismiss() {
    if (overlay) overlay.onDismiss()
    else setDismissing(true)
  }

  useLayoutEffect(() => {
    const element = modal.current
    if (!element) return
    const previousFocus = document.activeElement
    element.showModal()
    heading.current?.focus({ preventScroll: true })
    return () => {
      element.close()
      queueMicrotask(() => {
        if (element.open) return
        const target = previousFocus instanceof HTMLElement && previousFocus.isConnected && !previousFocus.matches(':disabled')
          ? previousFocus
          : getFocusFallback?.()
        target?.focus({ preventScroll: true })
      })
    }
  }, [getFocusFallback])

  useLayoutEffect(() => {
    if (!modal.current?.contains(document.activeElement)) heading.current?.focus({ preventScroll: true })
  }, [focusRevision])

  useLayoutEffect(() => {
    if (overlay && !hadOverlay.current) {
      hadOverlay.current = true
      overlayOpener.current = overlay.opener
      overlayHeading.current?.focus({ preventScroll: true })
    } else if (!overlay && hadOverlay.current) {
      hadOverlay.current = false
      const opener = overlayOpener.current
      const target = opener?.isConnected && !opener.matches(':disabled') ? opener : heading.current
      target?.focus({ preventScroll: true })
    }
  }, [overlay])

  useLayoutEffect(() => {
    if (!overlayClosing || !overlayPane.current || !onOverlayExited) return
    const pending = overlayPane.current.getAnimations()
    if (!pending.length) {
      onOverlayExited()
      return
    }
    let active = true
    void Promise.allSettled(pending.map((animation) => animation.finished)).then(() => {
      if (active) onOverlayExited()
    })
    return () => { active = false }
  }, [overlayClosing, onOverlayExited])

  useLayoutEffect(() => {
    const element = modal.current
    if (!closing || !element) return

    const exitTransitions = element.getAnimations({ subtree: true }).filter(({ effect }) =>
      effect instanceof KeyframeEffect && effect.target === element,
    )
    if (exitTransitions.length === 0) {
      onClose()
      return
    }
    let mounted = true
    void Promise.allSettled(exitTransitions.map((transition) => transition.finished)).then(() => {
      if (mounted) onClose()
    })
    return () => { mounted = false }
  }, [closing, onClose])

  return (
    <DropdownContainer.Provider value={modal}>
      <dialog
        ref={modal}
        aria-labelledby={overlay ? `${titleId}-overlay` : titleId}
        className={`modal${wide ? ' modal--wide' : ''}${closing ? ' is-closing' : ''}`}
        onCancel={(event) => {
          event.preventDefault()
          if (!dropdownIsOpen()) dismiss()
        }}
        onPointerDownCapture={(event) => {
          pointerOnBackdrop.current = event.target === event.currentTarget
          const target = event.target
          pointerOutsideDropdown.current = dropdownIsOpen() && target instanceof Element
            && !target.closest('.filter-popup, .filter-trigger')
        }}
        onClickCapture={(event) => {
          const dismissedDropdown = pointerOutsideDropdown.current && event.detail > 0
          pointerOutsideDropdown.current = false
          if (!closing && !overlayClosing && !dismissedDropdown) return
          event.preventDefault()
          event.stopPropagation()
        }}
        onClick={(event) => {
          if (pointerOnBackdrop.current && event.target === event.currentTarget) dismiss()
          pointerOnBackdrop.current = false
        }}
      >
        <div className="modal-frame">
          <ModalSurface title={title} titleId={titleId} headingRef={heading} covered={Boolean(overlay)} onDismiss={dismiss}>
            {children}
          </ModalSurface>
          {overlay && (
            <ModalSurface
              title={overlay.title}
              titleId={`${titleId}-overlay`}
              headingRef={overlayHeading}
              paneRef={overlayPane}
              layer="overlay"
              dismissLabel={overlay.dismissLabel}
              closing={overlayClosing}
              onDismiss={dismiss}
            >
              {overlay.children}
            </ModalSurface>
          )}
        </div>
      </dialog>
    </DropdownContainer.Provider>
  )
}
