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
