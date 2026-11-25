import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { mkdir, mkdtemp, readFile, rm, symlink, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { basename, dirname, join, resolve } from 'node:path'
import test from 'node:test'
import { packageRelease } from './package-release.mjs'

const releaseId = '0123456789abcdef0123'
const commit = 'a'.repeat(40)
const ciJob = 'web/piano-practice/ci'
const digest = (bytes) => createHash('sha256').update(bytes).digest('hex')

async function fixture(t) {
  const temp = resolve(tmpdir())
  const root = await mkdtemp(join(temp, 'piano-release-test-'))
  t.after(() => {
    assert.equal(dirname(root).toLowerCase(), temp.toLowerCase())
    assert(basename(root).startsWith('piano-release-test-'))
    return rm(root, { recursive: true, force: true })
  })
  const dist = join(root, 'dist')
  await mkdir(join(dist, 'assets'), { recursive: true })
  await writeFile(join(dist, 'index.html'), '<link rel="canonical" href="https://piano.maksonlee.com/">\n')
  await writeFile(join(dist, 'manifest.webmanifest'), '{"scope":"/"}\n')
  await writeFile(join(dist, 'service-worker.js'), `const VERSION = "${releaseId}"\n`)
  await writeFile(join(dist, 'assets', 'index-fixture.js'), 'console.log("piano")\n')
  return { root, dist, output: join(root, 'release'), commit, ciJob, buildNumber: 42 }
}

test('packages exact sorted root-relative files and SHA-256 metadata', async (t) => {
  const options = await fixture(t)
  const release = await packageRelease(options)
  const manifest = await readFile(join(options.output, 'checksums.json'))
  const archive = join(options.output, 'piano-practice-dist.tar.gz')
  const expected = ['assets/index-fixture.js', 'index.html', 'manifest.webmanifest', 'service-worker.js']
  const listed = execFileSync('tar', ['--force-local', '-tzf', archive], { encoding: 'utf8' })
    .trim()
    .split(/\r?\n/)
  assert.deepEqual(listed, expected)
  const parsed = JSON.parse(manifest.toString())
  assert.deepEqual(
    { ...parsed, files: parsed.files.map(({ path }) => path) },
    { schemaVersion: 1, releaseId, files: expected },
  )
  for (const file of parsed.files) {
    const input = await readFile(join(options.dist, file.path))
    const packed = execFileSync('tar', ['--force-local', '-xOzf', archive, '--', file.path])
    assert.deepEqual(packed, input)
    assert.deepEqual(file, { path: file.path, size: input.length, sha256: digest(input) })
  }
  assert.deepEqual(release, {
    schemaVersion: 1,
    repository: 'https://github.com/maksonlee/piano-practice.git',
    branch: 'main',
    commit,
    ciBuild: 42,
    ciJob,
    publicOrigin: 'https://piano.maksonlee.com/',
    releaseId,
    fileCount: 4,
    totalBytes: parsed.files.reduce((sum, file) => sum + file.size, 0),
    archiveSha256: digest(await readFile(archive)),
    checksumsSha256: digest(manifest),
  })
  assert.deepEqual(JSON.parse(await readFile(join(options.output, 'release.json'), 'utf8')), release)
  const again = await packageRelease({ ...options, output: join(options.root, 'repeat'), buildNumber: 43 })
  assert.equal(again.archiveSha256, release.archiveSha256)
  assert.equal(again.checksumsSha256, release.checksumsSha256)
})

test('rejects foreign release identity and output inside dist', async (t) => {
  const options = await fixture(t)
  await assert.rejects(packageRelease({ ...options, commit: 'main' }), /Git commit SHA/)
  await assert.rejects(packageRelease({ ...options, ciJob: 'other' }), /fixed CI job/)
  const former = await packageRelease({ ...options, output: join(options.root, 'former'), ciJob: 'web/piano-practice-main-ci' })
  assert.equal(former.ciJob, 'web/piano-practice-main-ci')
  await assert.rejects(packageRelease({ ...options, buildNumber: 1.5 }), /positive CI build number/)
  await assert.rejects(packageRelease({ ...options, output: join(options.dist, '..release') }), /outside dist/)
  await writeFile(join(options.dist, 'index.html'), '<link rel="canonical" href="https://elsewhere.example/">')
  await assert.rejects(packageRelease(options), /canonical origin/)
})

test('refuses symbolic links in the tree', async (t) => {
  const options = await fixture(t)
  await symlink(options.root, join(options.dist, 'external'), 'junction')
  await assert.rejects(packageRelease(options), /regular file/)
})
