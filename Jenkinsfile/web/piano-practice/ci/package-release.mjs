import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { lstat, mkdir, readFile, readdir, writeFile } from 'node:fs/promises'
import { isAbsolute, join, relative, resolve, sep } from 'node:path'
import { pathToFileURL } from 'node:url'

const repository = 'https://github.com/maksonlee/piano-practice.git'
const publicOrigin = 'https://piano.maksonlee.com/'
// The job's former name stays valid while both jobs exist.
const sourceJobs = ['web/piano-practice/ci', 'web/piano-practice-main-ci']
const hash = (bytes) => createHash('sha256').update(bytes).digest('hex')
const jsonBytes = (value) => Buffer.from(`${JSON.stringify(value, null, 2)}\n`)

async function releaseFiles(dist, prefix = '') {
  const entries = await readdir(join(dist, prefix), { withFileTypes: true })
  const files = []
  for (const entry of entries) {
    assert.match(entry.name, /^[A-Za-z0-9._-]+$/, `Unsafe release path: ${prefix}${entry.name}`)
    const path = prefix + entry.name
    if (entry.isDirectory()) files.push(...(await releaseFiles(dist, path + '/')))
    else {
      assert(entry.isFile(), `Release entry must be a regular file: ${path}`)
      const content = await readFile(join(dist, path))
      files.push({ path, size: content.length, sha256: hash(content) })
    }
  }
  return files
}

export async function packageRelease({ dist, output, commit, ciJob, buildNumber }) {
  assert.match(commit ?? '', /^[0-9a-f]{40}$/, 'Expected a full lowercase Git commit SHA.')
  assert(sourceJobs.includes(ciJob), 'Only the fixed CI job may package this release.')
  assert.match(String(buildNumber), /^[1-9]\d*$/, 'Expected a positive CI build number.')
  const ciBuild = Number(buildNumber)
  assert(Number.isSafeInteger(ciBuild), 'Build number exceeds the safe integer range.')
  dist = resolve(dist)
  output = resolve(output)
  assert((await lstat(dist)).isDirectory(), 'Expected a real dist directory.')
  const outputRelative = relative(dist, output)
  assert(
    outputRelative === '..' || outputRelative.startsWith(`..${sep}`) || isAbsolute(outputRelative),
    'Release output belongs outside dist.',
  )
  const files = (await releaseFiles(dist)).sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0))
  for (const path of ['index.html', 'manifest.webmanifest', 'service-worker.js']) {
    assert(
      files.some((file) => file.path === path && file.size > 0),
      `Missing release file: ${path}`,
    )
  }
  const index = await readFile(join(dist, 'index.html'), 'utf8')
  assert(
    index.includes(`<link rel="canonical" href="${publicOrigin}">`),
    'Unexpected production canonical origin.',
  )
  const manifest = JSON.parse(await readFile(join(dist, 'manifest.webmanifest'), 'utf8'))
  assert.equal(manifest.scope, '/', 'Production manifest must cover the domain root.')
  const worker = await readFile(join(dist, 'service-worker.js'), 'utf8')
  const releaseId = worker.match(/^const VERSION = "([0-9a-f]{20})"\r?\n/)?.[1]
  assert(releaseId, 'Expected an offline release ID in the production worker.')

  await mkdir(output, { recursive: true })
  const archive = join(output, 'piano-practice-dist.tar.gz')
  // Only the enumerated regular files enter the archive. Normalize tar metadata for repeatable packaging.
  const tar = spawnSync(
    'tar',
    [
      '--force-local',
      '--format=ustar',
      '--mtime=@0',
      '--owner=0',
      '--group=0',
      '--numeric-owner',
      '--mode=a=r,u+w',
      '--no-recursion',
      '--hard-dereference',
      '-czf',
      archive,
      '-C',
      dist,
      '--null',
      '--files-from=-',
    ],
    {
      input: Buffer.from(files.map((file) => file.path).join('\0') + '\0'),
      stdio: ['pipe', 'inherit', 'inherit'],
    },
  )
  if (tar.error) throw tar.error
  assert.equal(tar.status, 0, 'Packaging dist with GNU tar failed.')
  const checksums = jsonBytes({ schemaVersion: 1, releaseId, files })
  const release = {
    schemaVersion: 1,
    repository,
    branch: 'main',
    commit,
    ciJob,
    ciBuild,
    releaseId,
    publicOrigin,
    archiveSha256: hash(await readFile(archive)),
    checksumsSha256: hash(checksums),
    fileCount: files.length,
    totalBytes: files.reduce((sum, file) => sum + file.size, 0),
  }
  await writeFile(join(output, 'checksums.json'), checksums)
  await writeFile(join(output, 'release.json'), jsonBytes(release))
  return release
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  const [dist, output, commit, ciJob, buildNumber] = process.argv.slice(2)
  assert.equal(
    process.argv.length,
    7,
    'Usage: package-release.mjs <dist> <output> <commit> <job> <build-number>',
  )
  const release = await packageRelease({ dist, output, commit, ciJob, buildNumber })
  console.log(`Archived ${release.fileCount} files for ${release.releaseId} from ${release.commit}.`)
}
