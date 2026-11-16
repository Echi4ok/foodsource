import { useEffect, useLayoutEffect, useRef, useState } from 'react'

const REDUCED_MOTION = '(prefers-reduced-motion: reduce)'

export function useResultTransition<T extends { transitionKey: number | string }>(target: T, pending: boolean) {
  const [displayed, setDisplayed] = useState({ content: target, revision: 0 })
  const pane = useRef<HTMLDivElement>(null)
  const body = useRef<HTMLTableSectionElement>(null)
  const focusFallback = useRef<HTMLParagraphElement>(null)
  const restoreFocus = useRef(false)
  const matchingKey = displayed.content.transitionKey === target.transitionKey
  const changing = !pending && !matchingKey

  useLayoutEffect(() => {
    if (matchingKey && displayed.content !== target) {
      setDisplayed({ ...displayed, content: target })
      return
    }
    if (!changing) return
    const root = pane.current
    const region = body.current
    if (!root || !region) return

    function commit() {
      if (!root || !region) return
      const otherHeight = document.documentElement.scrollHeight - root.offsetHeight
      const rect = root.getBoundingClientRect()
      const visibleExtent = Math.min(rect.bottom, window.innerHeight) - rect.top
      const scrollExtent = window.scrollY > 0 ? window.scrollY + window.innerHeight - otherHeight : 0
      root.style.setProperty('--scroll-reserve', `${Math.ceil(Math.max(0, visibleExtent, scrollExtent))}px`)
      restoreFocus.current = region.contains(document.activeElement)
      setDisplayed(({ revision }) => ({ content: target, revision: revision + 1 }))
    }

    if (window.matchMedia(REDUCED_MOTION).matches || displayed.revision === 0) {
      commit()
      return
    }

    const fade = region.animate(
      [{ opacity: getComputedStyle(region).opacity }, { opacity: 0 }],
      { duration: 120, fill: 'forwards', easing: 'cubic-bezier(.4,0,1,1)' },
    )
    let current = true
    void fade.finished.then(() => {
      if (current) commit()
    }, () => {})
    return () => {
      current = false
      fade.cancel()
    }
  }, [changing, displayed, matchingKey, target])

  useLayoutEffect(() => {
    if (restoreFocus.current) {
      restoreFocus.current = false
      focusFallback.current?.focus({ preventScroll: true })
    }
    if (!displayed.revision || !body.current || body.current.getBoundingClientRect().bottom >= 64) return

    function revealResults() {
      if (body.current && body.current.getBoundingClientRect().bottom < 64) pane.current?.scrollIntoView({
        block: 'start',
        behavior: window.matchMedia(REDUCED_MOTION).matches ? 'instant' : 'smooth',
      })
    }

    const dialog = document.querySelector('dialog[open]')
    if (!dialog) {
      revealResults()
      return
    }
    dialog.addEventListener('close', revealResults, { once: true })
    return () => dialog.removeEventListener('close', revealResults)
  }, [displayed.revision])

  useEffect(() => {
    function releaseAtTop() {
      if (window.scrollY === 0) pane.current?.style.removeProperty('--scroll-reserve')
    }
    window.addEventListener('scroll', releaseAtTop, { passive: true })
    return () => window.removeEventListener('scroll', releaseAtTop)
  }, [])

  return { displayed, changing, pane, body, focusFallback }
}
