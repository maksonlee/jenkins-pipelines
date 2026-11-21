import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { readFile, writeFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import { extname, join, resolve } from 'node:path'
import { chromium } from 'playwright'

const productionOrigin = 'https://piano.maksonlee.com/'
const svgPath = '/resources/piano-practice-v1/scores/svg/repertoire/frere-jacques-bass.svg'
// Archived CI #3 uses the original lesson flow. CI #4 starts daily examples automatically.
const legacyRelease = {
  id: 'ed7ff5d48ff1c9f51da4',
  checksums: '6d95687acd4a0c961b87176f9883618adf03bf1279006c8aed9093f3af33b247',
}
const automaticDailyRelease = {
  id: 'e6f9e04743629ca7639e',
  checksums: 'ca56e8a0788c2eebbc33281ef7fd359d95f9b8e0281a2b6d9079ec9dc6ab973e',
}
const firstTask = 'F01-press-release'
const nextTask = 'F02-meet-middle'
const timeout = 35_000
const smokeStarted = performance.now()
const reloads = []
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

async function observeFirstDemo(page) {
  // Install before choosing input to cover archived builds that start the example automatically.
  await page.evaluate((task) => {
    const observer = new MutationObserver(() => {
      const cue = document.querySelector(
        `[data-testid="learn-shell"][data-task="${task}"][data-stage="watching"] [data-pitch="62"][data-action="release"]`,
      )
      if (!cue?.getClientRects().length) return
      document.documentElement.dataset.smokeObservedDemoLift = 'true'
      observer.disconnect()
    })
    observer.observe(document.documentElement, { childList: true, subtree: true, attributes: true })
  }, firstTask)
}

async function assertFirstDemo(page) {
  assert.equal(
    await page.locator('html').getAttribute('data-smoke-observed-demo-lift'),
    'true',
    'The F01 example did not display a D4 lift.',
  )
}

async function playFirstLesson(page) {
  const pitch = (action) =>
    page.locator(
      `[data-testid="learn-shell"][data-task="${firstTask}"] [data-pitch="62"][data-action="${action}"]`,
    )
  for (let attempt = 0; attempt < 2; attempt += 1) {
    await pitch('press').waitFor({ state: 'visible' })
    await page.keyboard.down('x')
    await pitch('release').waitFor({ state: 'visible' })
    assert.equal(await page.getByTestId('learn-shell').getAttribute('data-stage'), 'playing')
    await page.keyboard.up('x')
  }
}

async function reloadSnapshot(page) {
  let snapshotDeadline
  try {
    return await Promise.race([
      page.evaluate(async () => {
        const controller = navigator.serviceWorker.controller
        const pageState = {
          path: location.pathname,
          online: navigator.onLine,
          controller: controller && {
            path: new URL(controller.scriptURL).pathname,
            state: controller.state,
          },
        }
        const names = (await caches.keys()).filter((name) => /^piano-practice-[0-9a-f]{20}$/.test(name))
        const cacheEntries = await Promise.all(
          names.slice(0, 4).map(async (name) => ({
            name,
            hasIndex: Boolean(await caches.match('/index.html', { cacheName: name })),
          })),
        )
        return { ...pageState, cacheCount: names.length, cacheEntries }
      }),
      // Evidence collection must not hold up the navigation if the browser is unresponsive.
      new Promise((resolve) => {
        snapshotDeadline = setTimeout(() => resolve(null), 250)
      }),
    ])
  } catch {
    return null
  } finally {
    clearTimeout(snapshotDeadline)
  }
}

function reloadTime() {
  return Math.round(performance.now() - smokeStarted)
}

async function observeReload(page, phase) {
  const navigation = { phase, before: await reloadSnapshot(page), startedMs: reloadTime(), failures: [] }
  reloads.push(navigation)
  const origin = new URL(page.url()).origin
  const onRequestFailed = (request) => {
    if (!request.isNavigationRequest() || navigation.failures.length >= 4) return
    try {
      const url = new URL(request.url())
      if (url.origin !== origin || request.frame() !== page.mainFrame()) return
      navigation.failures.push({
        path: url.pathname,
        error: request.failure()?.errorText,
        atMs: reloadTime(),
      })
    } catch {
      // Some failed navigation requests have no frame yet.
    }
  }
  page.on('requestfailed', onRequestFailed)
  try {
    const response = await page.reload({ waitUntil: 'domcontentloaded' })
    const url = response && new URL(response.url())
    navigation.response = response && {
      path: url.origin === origin ? url.pathname : null,
      status: response.status(),
      fromServiceWorker: response.fromServiceWorker(),
    }
  } catch (error) {
    navigation.failed = true
    throw error
  } finally {
    navigation.finishedMs = reloadTime()
    page.off('requestfailed', onRequestFailed)
  }
}

async function walkLegacy(page, context, stage) {
  await stage('find')
  await page.getByTestId('learn-primary').click()
  await stage('watching')
  await stage('ready')
  await assertFirstDemo(page)
  assert.equal(await page.getByTestId('learn-evidence').count(), 0, 'Demo granted evidence.')

  await page.getByTestId('learn-primary').click()
  await stage('playing')
  await playFirstLesson(page)
  await stage('success')
  await page.getByTestId('learn-primary').click()
  await stage('find')
  assert.match(page.url(), /#learn\/F02\//)

  await context.setOffline(true)
  await observeReload(page, 'legacy F02 offline')
  await stage('find')
  assert.match(page.url(), /#learn\/F02\//)
  return { learnerFlow: 'legacy' }
}

async function assertDailyBackup(page, url, saved) {
  // The learner-facing export is a committed snapshot, independent of IndexedDB internals.
  await page.goto(url + '#progress', { waitUntil: 'domcontentloaded' })
  await page.getByTestId('daily-progress').waitFor({ state: 'visible' })
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('button', { name: 'Export', exact: true }).click(),
  ])
  const backup = JSON.parse(await readFile(await download.path(), 'utf8'))
  assert.equal(backup.format, 'piano-practice-progress')
  assert.equal(backup.settings.profile, 'simulator')
  assert.deepEqual(
    backup.cursors.map(({ sessionId, profile }) => ({ sessionId, profile })),
    [{ sessionId: saved.session, profile: 'simulator' }],
  )
  const session = backup.sessions.find(({ id }) => id === saved.session)
  assert.equal(session?.cursor, saved.cursor, 'The persisted session cursor changed during offline resume.')
  assert.equal(session.rounds[saved.cursor].id, saved.round, 'The persisted F02 round changed.')
  const evidence = backup.evidence.filter(({ sessionId }) => sessionId === saved.session)
  const models = evidence.filter(({ kind, targetId }) => kind === 'demo' && targetId === firstTask)
  assert.equal(models.length, 1, 'Expected one committed, separate F01 demonstration.')
  assert(models[0].activeDemoMs > 0 && !models[0].result, 'F01 demo must have listening time without a score.')
  const plays = evidence.filter(({ kind }) => kind === 'learner')
  assert.equal(plays.length, 1, 'Only one F01 learner turn should be saved; F02 had no input.')
  const [played] = plays
  assert.equal(played.targetId, firstTask)
  assert.equal(played.profile, 'simulator')
  assert.notEqual(played.roundId, models[0].roundId)
  const { result } = played
  assert(result?.passed && !result.interrupted && result.mode === 'guided', 'F01 learner turn did not pass.')
  assert.equal(result.targetCount, 2)
  assert.equal(result.matchedCount, 2)
  assert.equal(result.extraCount, 0)
  assert.equal(result.matches.length, 2)
  assert.equal(new Set(result.matches.map(({ inputEventId }) => inputEventId)).size, 2)
  assert(
    result.matches.every(
      ({ inputEventId, releasedAtMs }) =>
        Number.isFinite(releasedAtMs) && !result.uncreditedInputEventIds.includes(inputEventId),
    ),
    'F01 requires two fresh, credited attacks and their real releases.',
  )
}

async function walkDaily(page, context, url, automaticExamples) {
  const turn = (task) =>
    page.locator(
      `[data-testid="daily-shell"][data-daily-state="turn"] [data-testid="learn-shell"][data-round-target="${task}"][data-status="running"]`,
    )
  const snapshot = () =>
    page.getByTestId('daily-shell').evaluate((shell) => ({
      session: shell.dataset.session,
      cursor: Number(shell.dataset.cursor),
      round: shell.querySelector('[data-testid="learn-shell"]')?.dataset.round,
    }))
  const startExample = (task) =>
    page
      .locator(`[data-testid="learn-shell"][data-round-target="${task}"][data-stage="find"]`)
      .getByTestId('daily-resume')
      .click()

  if (!automaticExamples) await startExample(firstTask)
  await turn(firstTask).waitFor({ state: 'visible' })
  await assertFirstDemo(page)
  await playFirstLesson(page)
  if (!automaticExamples) await startExample(nextTask)
  await turn(nextTask).waitFor({ state: 'visible' })
  const saved = await snapshot()
  assert(
    saved.session && saved.round && Number.isInteger(saved.cursor) && saved.cursor >= 2,
    'F02 did not expose a saved session, cursor and round.',
  )

  await context.setOffline(true)
  await observeReload(page, 'daily F02 offline')
  assert.equal(new URL(page.url()).hash, '#daily', 'Offline reload left the daily route.')
  await page.locator('[data-testid="daily-shell"][data-daily-state="ready"]').waitFor({ state: 'visible' })
  assert.deepEqual(await snapshot(), saved, 'Offline reload did not recover the committed F02 round.')
  await page.getByTestId('daily-resume').click()
  await turn(nextTask).waitFor({ state: 'visible' })
  assert.deepEqual(await snapshot(), saved, 'Resuming moved the F02 cursor before learner input.')
  await assertDailyBackup(page, url, saved)
  return {
    learnerFlow: 'daily',
    exampleStart: automaticExamples ? 'automatic' : 'learner-ready',
    offlineSessionResumed: true,
    f01LearnerRounds: 1,
  }
}

function matchesRelease(release, expected) {
  return release.releaseId === expected.id && release.checksumsSha256 === expected.checksums
}

async function walkLearner(url, release, expectedScript, scoreDigest) {
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
    const verifyWorker = async () => {
      await page.waitForFunction(async (name) => {
        const names = (await caches.keys()).filter((cache) => cache.startsWith('piano-practice-'))
        return navigator.serviceWorker.controller && names.length === 1 && names[0] === name
      }, `piano-practice-${release.releaseId}`)
      const src = await page.locator('script[type="module"][src]').getAttribute('src')
      assert.equal(src, expectedScript, 'Browser loaded an unexpected application script.')
    }

    assert((await page.goto(url, { waitUntil: 'domcontentloaded' }))?.ok(), 'Public page did not load.')
    await verifyWorker()
    await page.getByTestId('start-daily').click()
    await page.waitForURL((location) => location.hash.startsWith('#setup/'))
    await observeFirstDemo(page)
    await page.getByTestId('choose-simulator').click()
    await page.waitForURL((location) => !location.hash.startsWith('#setup/'))
    const legacy = matchesRelease(release, legacyRelease)
    assert.equal(
      new URL(page.url()).hash,
      legacy ? `#learn/F01/${firstTask}` : '#daily',
      `Unexpected first lesson route for release ${release.releaseId}.`,
    )
    const flow = legacy
      ? await walkLegacy(page, context, stage)
      : await walkDaily(page, context, url, matchesRelease(release, automaticDailyRelease))
    await verifyWorker()
    await page.goto(url + '#piece/frere-jacques-bass', { waitUntil: 'domcontentloaded' })
    await stage('find')
    await observeReload(page, 'Frère Jacques offline')
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
    await observeReload(page, 'Frère Jacques after reconnecting')
    await stage('find')
    await verifyWorker()
    assert.deepEqual(errors, [], 'Browser page errors during smoke.')
    return {
      chromium: browser.version(),
      firstLesson: 'F01',
      nextLesson: 'F02',
      ...flow,
      offlineScore: svgPath,
    }
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
let report = { schemaVersion: 1, target, startedAt: new Date().toISOString(), passed: false, reloads }
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
  const journey = await walkLearner(url, release, expectedScript, files.get(svgPath.slice(1)).sha256)
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
