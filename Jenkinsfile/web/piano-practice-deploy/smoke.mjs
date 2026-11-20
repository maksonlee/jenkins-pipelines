import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { readFile, writeFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import { extname, join, resolve } from 'node:path'
import { chromium } from 'playwright'

const productionOrigin = 'https://piano.maksonlee.com/'
const svgPath = '/resources/piano-practice-v1/scores/svg/repertoire/frere-jacques-bass.svg'
const timeout = 35_000
const shellPaths = ['/', '/index.html', '/manifest.webmanifest', '/service-worker.js']
const mediaTypes = {
  '.html': 'text/html',
  '.js': 'text/javascript',
  '.css': 'text/css',
  '.json': 'application/json',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.webmanifest': 'application/manifest+json',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
}
const sha256 = (bytes) => createHash('sha256').update(bytes).digest('hex')

async function serveFixture(root, files) {
  const server = createServer(async (request, response) => {
    const path = new URL(request.url, 'http://127.0.0.1').pathname
    const file = path === '/' ? 'index.html' : path.slice(1)
    if (!files.has(file)) {
      response.writeHead(404).end()
      return
    }
    try {
      const content = await readFile(join(root, file))
      response
        .writeHead(200, {
          'Content-Type': mediaTypes[extname(file)] ?? 'application/octet-stream',
          'Cache-Control': file.startsWith('assets/') ? 'public, max-age=31536000, immutable' : 'no-cache',
        })
        .end(content)
    } catch {
      response.writeHead(500).end()
    }
  })
  await new Promise((done, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', done)
  })
  return {
    url: `http://127.0.0.1:${server.address().port}/`,
    close: () => new Promise((done) => server.close(done)),
  }
}

async function verifyShell(url, files, releaseId) {
  const assetPaths = [...files.keys()].filter((path) => path.startsWith('assets/')).map((path) => `/${path}`)
  const paths = [...shellPaths, svgPath, ...assetPaths]
  const payloads = await Promise.all(
    paths.map(async (path) => {
      const file = files.get(path === '/' ? 'index.html' : path.slice(1))
      assert(file, `Expected ${path} in the release manifest.`)
      const response = await fetch(new URL(path, url), {
        redirect: 'error',
        signal: AbortSignal.timeout(timeout),
      })
      assert.equal(response.status, 200, `Unexpected HTTP status for ${path}.`)
      const cacheControl =
        response.headers
          .get('cache-control')
          ?.toLowerCase()
          .split(',')
          .map((directive) => directive.trim()) ?? []
      if (shellPaths.includes(path)) {
        assert(
          cacheControl.includes('no-cache') && !cacheControl.includes('immutable'),
          `Expected shell revalidation for ${path}.`,
        )
      }
      if (path.startsWith('/assets/')) {
        assert(
          cacheControl.includes('immutable') &&
            cacheControl.includes('max-age=31536000') &&
            !cacheControl.includes('no-cache'),
          `Expected one-year immutable caching for ${path}.`,
        )
      }
      const type = response.headers.get('content-type')?.split(';')[0]
      const expected = mediaTypes[extname(file.path)]
      if (expected === 'text/javascript')
        assert(
          ['text/javascript', 'application/javascript'].includes(type),
          `Invalid JavaScript MIME for ${path}.`,
        )
      else if (expected) assert.equal(type, expected, `Unexpected MIME type for ${path}.`)
      const body = Buffer.from(await response.arrayBuffer())
      assert.equal(body.length, file.size, `Size mismatch for ${path}.`)
      const digest = sha256(body)
      assert.equal(digest, file.sha256, `SHA-256 mismatch for ${path}.`)
      return { path, body, digest }
    }),
  )
  const script = payloads.find(({ path }) => path === '/service-worker.js').body.toString()
  assert.equal(
    script.match(/^const VERSION = "([0-9a-f]{20})"\r?\n/)?.[1],
    releaseId,
    'Wrong offline worker version.',
  )
  const html = payloads.find(({ path }) => path === '/').body.toString()
  const expectedScript = html.match(/<script\b[^>]*\bsrc="(\/assets\/[^"/]+\.js)"/)?.[1]
  assert(expectedScript && assetPaths.includes(expectedScript), 'Release shell has no archived module script.')
  return { expectedScript, assets: payloads.map(({ path, digest }) => ({ path, sha256: digest })) }
}

async function walkLearner(url, releaseId, expectedScript, scoreDigest) {
  const browser = await chromium.launch({ timeout })
  try {
    const context = await browser.newContext({ locale: 'en-US' })
    const page = await context.newPage()
    page.setDefaultTimeout(timeout)
    const errors = []
    page.on('pageerror', (error) => errors.push(error.message))
    const stage = (name) =>
      page.waitForFunction(
        (expected) =>
          document.querySelector('[data-testid="learn-shell"]')?.getAttribute('data-stage') === expected,
        name,
      )
    const pitch = (action) =>
      page.locator(`[data-testid="learn-shell"] [data-pitch="62"][data-action="${action}"]`)
    const verifyWorker = async () => {
      await page.waitForFunction(async (name) => {
        const names = (await caches.keys()).filter((cache) => cache.startsWith('piano-practice-'))
        return navigator.serviceWorker.controller && names.length === 1 && names[0] === name
      }, `piano-practice-${releaseId}`)
      const src = await page.locator('script[type="module"][src]').getAttribute('src')
      assert.equal(src, expectedScript, 'Browser loaded an unexpected application script.')
    }

    assert((await page.goto(url, { waitUntil: 'domcontentloaded' }))?.ok(), 'Public page did not load.')
    await verifyWorker()
    await page.getByTestId('start-daily').click()
    await page.getByTestId('choose-simulator').click()
    await stage('find')
    assert.match(page.url(), /#learn\/F01\/F01-press-release$/)
    await page.evaluate(() => {
      const shell = document.querySelector('[data-testid="learn-shell"]')
      const observer = new MutationObserver(() => {
        if (
          shell.getAttribute('data-stage') === 'watching' &&
          shell.querySelector('[data-pitch="62"][data-action="release"]')
        ) {
          shell.dataset.smokeObservedDemoLift = 'true'
          observer.disconnect()
        }
      })
      observer.observe(shell, { childList: true, subtree: true, attributes: true })
    })
    await page.getByTestId('learn-primary').click()
    await stage('watching')
    await stage('ready')
    assert.equal(await page.getByTestId('learn-shell').getAttribute('data-smoke-observed-demo-lift'), 'true')
    assert.equal(await page.getByTestId('learn-evidence').count(), 0, 'Demo granted evidence.')

    await page.getByTestId('learn-primary').click()
    await stage('playing')
    for (let attempt = 0; attempt < 2; attempt += 1) {
      await pitch('press').waitFor({ state: 'visible' })
      await page.keyboard.down('x')
      await pitch('release').waitFor({ state: 'visible' })
      await stage('playing')
      await page.keyboard.up('x')
    }
    await stage('success')
    await page.getByTestId('learn-primary').click()
    await stage('find')
    assert.match(page.url(), /#learn\/F02\//)

    await context.setOffline(true)
    await page.reload({ waitUntil: 'domcontentloaded' })
    await stage('find')
    assert.match(page.url(), /#learn\/F02\//)
    await verifyWorker()
    await page.goto(url + '#piece/frere-jacques-bass', { waitUntil: 'domcontentloaded' })
    await stage('find')
    await page.reload({ waitUntil: 'domcontentloaded' })
    await stage('find')
    const score = await page.evaluate(async (path) => {
      const response = await fetch(path)
      const bytes = await response.arrayBuffer()
      const digest = await crypto.subtle.digest('SHA-256', bytes)
      return {
        status: response.status,
        sha256: Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join(''),
      }
    }, svgPath)
    assert.deepEqual(score, { status: 200, sha256: scoreDigest }, 'Offline score did not match the release.')
    await context.setOffline(false)
    await page.reload({ waitUntil: 'domcontentloaded' })
    await stage('find')
    await verifyWorker()
    assert.deepEqual(errors, [], 'Browser page errors during smoke.')
    return { chromium: browser.version(), firstLesson: 'F01', nextLesson: 'F02', offlineScore: svgPath }
  } finally {
    await browser.close()
  }
}

const [target, root, releasePath, checksumsPath, output] = process.argv.slice(2)
assert(
  [6, 7].includes(process.argv.length),
  'Usage: smoke.mjs <local|production-origin> <dist> <release.json> <checksums.json> [result.json]',
)
assert(target === 'local' || target === productionOrigin, 'Expected local or the fixed public origin.')
assert.notEqual(
  process.env.NODE_TLS_REJECT_UNAUTHORIZED,
  '0',
  'TLS certificate validation must remain enabled.',
)
let local
let report = { schemaVersion: 1, target, startedAt: new Date().toISOString(), passed: false }
try {
  const release = JSON.parse(await readFile(releasePath, 'utf8'))
  const checksumsBytes = await readFile(checksumsPath)
  const checksums = JSON.parse(checksumsBytes)
  assert.equal(release.schemaVersion, 1)
  assert.equal(checksums.schemaVersion, 1)
  assert.equal(release.publicOrigin, productionOrigin)
  assert.match(release.releaseId, /^[0-9a-f]{20}$/)
  assert.equal(checksums.releaseId, release.releaseId)
  assert.equal(sha256(checksumsBytes), release.checksumsSha256)
  const files = new Map(
    checksums.files.map((file) => {
      assert.match(file.path, /^[A-Za-z0-9._-]+(?:\/[A-Za-z0-9._-]+)*$/)
      assert(!file.path.split('/').some((part) => part === '.' || part === '..'))
      return [file.path, file]
    }),
  )
  assert.equal(files.size, release.fileCount)
  assert.equal(files.size, checksums.files.length)
  if (target === 'local') local = await serveFixture(resolve(root), files)
  const url = local?.url ?? target
  report = { ...report, url, releaseId: release.releaseId }
  const { expectedScript, assets } = await verifyShell(url, files, release.releaseId)
  const journey = await walkLearner(url, release.releaseId, expectedScript, files.get(svgPath.slice(1)).sha256)
  report = { ...report, passed: true, script: expectedScript, assets, ...journey }
} catch (error) {
  report.error = error.message
  process.exitCode = 1
} finally {
  await local?.close()
  report.finishedAt = new Date().toISOString()
  const result = JSON.stringify(report, null, 2) + '\n'
  if (output) await writeFile(output, result)
  console.log(result.trim())
}
