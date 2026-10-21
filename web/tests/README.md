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
