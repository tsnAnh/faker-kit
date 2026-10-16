#!/usr/bin/env node
import { execFileSync } from 'node:child_process'
import { existsSync, readFileSync } from 'node:fs'
import { pathToFileURL } from 'node:url'

const FINAL_STATES = new Set(['APPROVED', 'CHANGES_REQUESTED'])
const TRANSIENT = /HTTP (5\d\d|429)|timed? ?out|ECONNRESET|ETIMEDOUT|EAI_AGAIN|connection (reset|refused)|temporary failure|unexpected EOF/i
const SAFE_WORD = /^[A-Za-z][A-Za-z0-9._-]{0,29}$/
const SAFE_KEY = /^[A-Za-z_][A-Za-z0-9_.-]{0,29}$/

export function sanitizeCommand(command) {
  const tokens = String(command ?? '').trim().split(/\s+/).filter(Boolean).slice(0, 12)
  return tokens
    .map((token) => {
      const eq = token.indexOf('=')
      if (eq > 0) {
        const key = token.slice(0, eq)
        return SAFE_KEY.test(key) ? `${key}=<value>` : '<arg>'
      }
      if (token.startsWith('repos/')) return 'repos/<path>'
      if (token.startsWith('-') && SAFE_WORD.test(token.replace(/^-+/, ''))) return token
      return SAFE_WORD.test(token) ? token : '<arg>'
    })
    .join(' ')
}

export function sanitizeDenials(execution) {
  const messages = Array.isArray(execution) ? execution : [execution]
  const denials = []
  for (const message of messages) {
    if (message && message.type === 'result' && Array.isArray(message.permission_denials)) {
      denials.push(...message.permission_denials)
    }
  }
  return denials.map((denial) => {
    const tool = SAFE_WORD.test(String(denial?.tool_name ?? '')) ? denial.tool_name : '<tool>'
    return tool === 'Bash' ? `Bash: ${sanitizeCommand(denial?.tool_input?.command)}` : tool
  })
}

export function evaluate({ reviews, currentHead, expectedSha, app }) {
  if (currentHead !== expectedSha) {
    return { verdict: 'head-moved', reason: `head moved from ${expectedSha} to ${currentHead}; the run for the new head decides` }
  }
  const mine = reviews.filter(
    (review) => review?.user?.login === app && review.commit_id === expectedSha && FINAL_STATES.has(review.state),
  )
  if (mine.length === 0) {
    const onHead = reviews.filter((review) => review?.commit_id === expectedSha).length
    return { verdict: 'fail', reason: `no ${app} review in state APPROVED or CHANGES_REQUESTED on ${expectedSha} (${reviews.length} reviews, ${onHead} on this head)` }
  }
  return { verdict: 'pass', reason: `${app} review ${mine[mine.length - 1].state} on ${expectedSha}` }
}

export async function withRetry(operation, { attempts = 3, delaysMs = [5000, 15000], sleep = (ms) => new Promise((r) => setTimeout(r, ms)) } = {}) {
  for (let attempt = 1; ; attempt++) {
    try {
      return await operation()
    } catch (error) {
      const message = String(error?.message ?? error)
      if (attempt >= attempts || !TRANSIENT.test(message)) throw error
      await sleep(delaysMs[Math.min(attempt - 1, delaysMs.length - 1)])
    }
  }
}

function ghJson(args) {
  try {
    return JSON.parse(execFileSync('gh', ['api', ...args], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }))
  } catch (error) {
    throw new Error(String(error.stderr || error.message).split('\n')[0])
  }
}

export async function run({ env, fetchReviews, fetchHead, readExecution, log, sleep }) {
  const denials = readExecution ? sanitizeDenials(readExecution()) : []
  if (denials.length > 0) {
    for (const denial of denials) log(`::error::permission denied: ${denial}`)
    return 1
  }
  const retry = (operation) => withRetry(operation, { sleep })
  const reviews = await retry(fetchReviews)
  const currentHead = await retry(fetchHead)
  const result = evaluate({ reviews, currentHead, expectedSha: env.EXPECTED_SHA, app: env.REVIEW_APP })
  if (result.verdict === 'fail') {
    log(`::error::${result.reason}`)
    return 1
  }
  log(result.verdict === 'head-moved' ? `::notice::${result.reason}` : result.reason)
  return 0
}

async function main() {
  const env = process.env
  const app = env.REVIEW_APP || JSON.parse(readFileSync('.agents/dispatch.json', 'utf8')).review_app
  if (!app) throw new Error('review_app is not set in .agents/dispatch.json')
  const pull = `repos/${env.REPO}/pulls/${env.PR_NUMBER}`
  const file = env.EXECUTION_FILE
  const code = await run({
    env: { ...env, REVIEW_APP: app },
    fetchReviews: async () => ghJson([`${pull}/reviews`, '--paginate', '--slurp']).flat(),
    fetchHead: async () => ghJson([pull]).head.sha,
    readExecution: file && existsSync(file) ? () => JSON.parse(readFileSync(file, 'utf8')) : null,
    log: (line) => console.log(line),
  })
  process.exit(code)
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? '').href) await main()
