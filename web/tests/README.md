# Browser state fixture

The production app only ships `src/main.tsx`. This separate entry renders the real
shared components with sample props for visual and interaction verification.

After building and starting the backend, run from the repository root:

```sh
node web/tests/build-preview.mjs
```

On that backend's port, open
`/assets/verification/tests/states.html#loading` (or `#error`, `#empty`, `#unknown`).
The fixture reuses the production tokens, reading column and state styles. Its
retry and form callbacks are local demonstrations, not backend functionality.
Capture all four states in both themes at desktop and mobile widths. A regular
web build removes this temporary preview from `app/static/`.

## Sources components

`Sources` accepts typed `SourceSummary` records and `onAdd(url)` / `onReindex(id)`
callbacks (synchronous or Promise-returning). Pending callbacks keep their context,
block duplicate requests, and show errors with a way to retry. Backend wiring and
polling remain a later increment; the production route is unchanged.

The same preview build also serves `/assets/verification/tests/sources.html`.
Use hashes `#queued`, `#indexing`, `#ready`, `#failed`, `#all`, or `#empty`.
`#pending` holds an add callback open; `#error` rejects it. Submit a URL to exercise
those states. Submit invalid input on `#empty` for validation. The callback log
records arguments for browser checks. Check each at 1440x900, 390x844 and 360x844,
in both themes, including keyboard focus, no horizontal overflow, and reduced motion.

## Answer and citation components

`Answer` accepts a typed `AnswerResponse` (`known`, `answer`, `citations`). Its
`Citation` shape matches the API; URLs and line ranges are used as supplied.
`CitationCard` can also render independently. Unknown answers use the shared
explicit state and show no citations. Live answering remains a later increment.

The preview also serves `/assets/verification/tests/citations.html`: the default
shows two citations (including a long path and a six-row preview); `#literal`
shows hostile HTML as text and a two-row snippet; `#unknown` shows the honest gap.
Use Tab then Enter or Space on the numbered chips: only the corresponding card
highlights, receives focus and scrolls into view. Tab continues to its GitHub link.
Capture these states and focused cards at 1440x900 and 390x844 in both themes,
and verify zero page overflow at 360px. The fixture is not in the production build.
