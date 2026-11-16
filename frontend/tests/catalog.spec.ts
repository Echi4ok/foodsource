import { expect, test } from '@playwright/test'
import type { Catalog, SearchResult } from '../src/catalog/model'

test('repeating an explicit search refreshes the catalog in the open tab', async ({ page }) => {
  let latest = false
  await page.route('**/api/catalog', async (route) => {
    const response = await route.fetch()
    const catalog: Catalog = await response.json()
    await route.fulfill({ response, json: latest ? {
      ...catalog, catalog_revision: catalog.catalog_revision + 1, updated_at: '2029-01-29',
    } : catalog })
  })
  await page.route('**/api/suppliers', async (route) => {
    const response = await route.fetch()
    const result: SearchResult = await response.json()
    await route.fulfill({ response, json: latest ? {
      ...result, catalog_revision: result.catalog_revision + 1,
    } : result })
  })

  await page.goto('/')
  await expect(page.getByRole('checkbox').first()).toBeVisible()
  latest = true
  await page.getByRole('button', { name: 'Найти', exact: true }).click()
  await expect(page.locator('.catalog-date time')).toHaveAttribute('datetime', '2029-01-29')
})

test('keeps the remaining branch when a saved branch is removed during restoration', async ({ page }) => {
  const result: SearchResult = await (await page.request.get('/api/suppliers')).json()
  const [current, alternate] = result.items
  await page.addInitScript(([first, second]) => {
    localStorage.setItem('foodsource.shortlist.v1', JSON.stringify([first, second]))
  }, [current.id, alternate.id])

  let release: () => void = () => {}
  const pending = new Promise<void>((resolve) => { release = resolve })
  await page.route('**/api/suppliers/shortlist?*', async (route) => {
    const ids = new URL(route.request().url()).searchParams.getAll('id')
    if (ids.length === 2) await pending
    const branch = ids[0] === current.id ? current : alternate
    await route.fulfill({ json: {
      ...result, items: [{ ...branch, company_id: current.company_id }], total: 1, next_offset: null,
    } }).catch(() => {})
  })

  try {
    const starting = page.waitForRequest('**/api/suppliers/shortlist?*')
    await page.goto('/')
    await starting
    await page.getByRole('checkbox', { name: `Сравнить: ${current.name}` }).uncheck()
    release()
    await page.getByRole('button', { name: 'Сравнить 1/3' }).click()
    const comparison = page.getByRole('dialog', { name: 'Выбранные поставщики' })
    await expect(comparison.getByText(alternate.name, { exact: true })).toBeVisible()
  } finally {
    release()
  }
})
