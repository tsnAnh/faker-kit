import { describe, expect, test } from 'bun:test'
import { evaluate, run, sanitizeDenials, withRetry } from './claude-review-postcondition.mjs'

const SHA = '1578f0cc1ab6655dcf7c2d0d16c71368fa101e60'
const APP = 'claude[bot]'
const review = (over = {}) => ({ user: { login: APP }, commit_id: SHA, state: 'APPROVED', ...over })
const env = { EXPECTED_SHA: SHA, REVIEW_APP: APP }

const harness = (over = {}) => {
  const lines = []
  const calls = { reviews: 0, head: 0 }
  const opts = {
    env,
    fetchReviews: async () => (calls.reviews++, [review()]),
    fetchHead: async () => (calls.head++, SHA),
    readExecution: null,
    log: (line) => lines.push(line),
    sleep: async () => {},
    ...over,
  }
  return { opts, lines, calls }
}

describe('evaluate', () => {
  const base = { currentHead: SHA, expectedSha: SHA, app: APP }
  test('valid approval on head passes', () => {
    expect(evaluate({ ...base, reviews: [review()] }).verdict).toBe('pass')
  })
  test('valid change request on head passes', () => {
    expect(evaluate({ ...base, reviews: [review({ state: 'CHANGES_REQUESTED' })] }).verdict).toBe('pass')
  })
  test('no review fails', () => {
    expect(evaluate({ ...base, reviews: [] }).verdict).toBe('fail')
  })
  test('review on an older commit fails', () => {
    expect(evaluate({ ...base, reviews: [review({ commit_id: 'a'.repeat(40) })] }).verdict).toBe('fail')
  })
  test('review by another author fails', () => {
    expect(evaluate({ ...base, reviews: [review({ user: { login: 'github-actions[bot]' } })] }).verdict).toBe('fail')
  })
  test.each(['COMMENTED', 'PENDING', 'DISMISSED'])('state %s fails', (state) => {
    expect(evaluate({ ...base, reviews: [review({ state })] }).verdict).toBe('fail')
  })
  test('head moved is neither pass nor fail', () => {
    const result = evaluate({ ...base, currentHead: 'b'.repeat(40), reviews: [] })
    expect(result.verdict).toBe('head-moved')
  })
})

describe('run', () => {
  test('passes with a valid review', async () => {
    const { opts } = harness()
    expect(await run(opts)).toBe(0)
  })
  test('fails with no review', async () => {
    const { opts, lines } = harness({ fetchReviews: async () => [] })
    expect(await run(opts)).toBe(1)
    expect(lines[0]).toContain('::error::')
  })
  test('head moved exits 0 with a notice, not a pass on the old head', async () => {
    const { opts, lines } = harness({ fetchHead: async () => 'b'.repeat(40), fetchReviews: async () => [] })
    expect(await run(opts)).toBe(0)
    expect(lines[0]).toStartWith('::notice::head moved')
  })
  test('transient error is retried then succeeds', async () => {
    let attempts = 0
    const { opts } = harness({
      fetchReviews: async () => {
        if (++attempts < 3) throw new Error('gh: Bad Gateway (HTTP 502)')
        return [review()]
      },
    })
    expect(await run(opts)).toBe(0)
    expect(attempts).toBe(3)
  })
  test('transient error is retried a bounded number of times', async () => {
    let attempts = 0
    const { opts } = harness({
      fetchReviews: async () => {
        attempts++
        throw new Error('HTTP 503')
      },
    })
    await expect(run(opts)).rejects.toThrow('HTTP 503')
    expect(attempts).toBe(3)
  })
  test('a non-transient HTTP error is not retried', async () => {
    let attempts = 0
    const { opts } = harness({
      fetchReviews: async () => {
        attempts++
        throw new Error('Resource not accessible by integration (HTTP 403)')
      },
    })
    await expect(run(opts)).rejects.toThrow('HTTP 403')
    expect(attempts).toBe(1)
  })
  test('a permission denial fails without any GitHub call, even if a review exists', async () => {
    const execution = [{ type: 'result', permission_denials: [{ tool_name: 'Bash', tool_input: { command: 'gh pr diff 7' } }] }]
    const { opts, lines, calls } = harness({ readExecution: () => execution })
    expect(await run(opts)).toBe(1)
    expect(calls).toEqual({ reviews: 0, head: 0 })
    expect(lines).toEqual(['::error::permission denied: Bash: gh pr diff <arg>'])
  })
})

describe('withRetry', () => {
  test('waits between attempts', async () => {
    const waits = []
    let n = 0
    await withRetry(async () => { if (++n < 3) throw new Error('ECONNRESET') }, { sleep: async (ms) => waits.push(ms) })
    expect(waits).toEqual([5000, 15000])
  })
})

describe('sanitizeDenials', () => {
  const secret = 'ghp_SECRETTOKEN0123456789'
  test('keeps tool name and command shape, drops values and arguments', () => {
    const execution = [
      { type: 'system' },
      {
        type: 'result',
        permission_denials: [
          {
            tool_name: 'Bash',
            tool_input: {
              command: `gh api repos/tsnAnh/faker-kit/pulls/145/reviews -f commit_id=${SHA} -f event=APPROVE -F body=@review-body.md --header Authorization:${secret}`,
            },
          },
          { tool_name: 'Write', tool_input: { file_path: '/home/runner/.ssh/id_rsa', content: secret } },
        ],
      },
    ]
    const out = sanitizeDenials(execution)
    expect(out).toEqual([
      'Bash: gh api repos/<path> -f commit_id=<value> -f event=<value> -F body=<value> --header <arg>',
      'Write',
    ])
    expect(JSON.stringify(out)).not.toContain(secret)
    expect(JSON.stringify(out)).not.toContain(SHA)
  })
  test('no denials gives an empty list', () => {
    expect(sanitizeDenials([{ type: 'result', permission_denials: [] }])).toEqual([])
    expect(sanitizeDenials([{ type: 'assistant' }])).toEqual([])
  })
  test('an unusual tool name is masked', () => {
    expect(sanitizeDenials([{ type: 'result', permission_denials: [{ tool_name: `x;${'y'.repeat(60)}` }] }])).toEqual(['<tool>'])
  })
})
