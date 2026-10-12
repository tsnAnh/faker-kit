# Build the docs site

The Mintlify site under `site/` is generated in part from `docs/` and deployed by `.github/workflows/docs-site.yml`.

## Sub-features

- `site-generate`: `how-to/`, `explanation/`, and `reference/` pages generate from `docs/`, and every link and navigation entry resolves.
- `site-preview`: the site renders locally.

## How to get to it (user POV)

- Readers open the deployed site; writers edit `docs/` and run `node scripts/gen-site.mjs`.

## Driving it in this checkout

- **Generate.** `node scripts/gen-site.mjs`. Proof: `site: <n> pages, links and navigation resolve`.
- **Preview.** `cd site && npx mint dev`, then open the printed local URL and the page the change touches. Proof: a screenshot or the page text of that page.

## Gotchas

- Edit `docs/`, never the generated copies under `site/`.
- A new `docs/` page needs an entry in `site/docs.json`, or generation fails.
