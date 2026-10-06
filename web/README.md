# OpsPilot dashboard

The operator-facing dashboard. Next.js 16 (App Router) + TypeScript + Tailwind 4.

It talks to the API on `:8000` **directly from the browser process** — there is
no Next.js rewrite proxy, so the API's own CORS policy is what makes this work.
See `docs/api-contract.md` §8.1 for why a proxy was not chosen instead.

## Running it

```bash
cp .env.example .env.local     # then fill in OPSPILOT_OPERATOR_TOKEN
npm run dev                    # http://localhost:3000
```

`.env.local` is gitignored. `.env.example` is committed: it carries no secret
and exists to explain the two variables, including why the token is **not**
`NEXT_PUBLIC_`.

## Where the token lives

**Server-side only.** `OPSPILOT_OPERATOR_TOKEN` has no `NEXT_PUBLIC_` prefix, so
it is read in Server Components and Route Handlers and never reaches the browser
bundle. Every API call therefore originates on the server.

This is a real constraint, not a stylistic one: these endpoints can issue
refunds, so a token inlined into a JavaScript bundle is a refund-capable
credential published to anyone who can view the page. The single-operator
deployment makes the exposure small, not zero. The reasoning is written out in
full at the top of `src/lib/api/client.ts`, because it is the kind of decision
that gets reversed by accident when someone adds a `NEXT_PUBLIC_` prefix to make
a client component work.

Only `NEXT_PUBLIC_API_BASE_URL` is public, and it is a URL rather than a
credential.

## The API client

`src/lib/api/`:

| File | What it is |
|---|---|
| `types.ts` | **Generated.** Do not edit. |
| `client.ts` | Hand-written: bearer token, error envelope, one binding per endpoint. |
| `error-envelope.ts` | The single hand-written type, and why it is an exception. |

### Types are generated, not transcribed

`types.ts` is generated from the FastAPI app's own OpenAPI document. A
hand-written response type is a second copy of the Pydantic models, and the two
copies disagree the first time someone adds a field — which is why the generator
exists rather than an `interface RunDetail`.

```
create_app().openapi()            scripts/openapi_schema.py — no DB, no server
     │  OpenAPI 3.1 JSON
     ▼
openapi-typescript                web/scripts/generate-types.mjs
     │
     ▼
src/lib/api/types.ts              committed, and checked
```

Regenerate after any backend change to a response model:

```bash
npm run generate:types
```

Verify without writing anything (this is what CI and the pytest suite run):

```bash
npm run check:types
```

No database and no running API are needed for either — the schema comes from
constructing the app in-process.

### The drift guard

Generation alone is a one-time act; the copy starts drifting the day it is
written. Three checks keep it honest, all in
`tests/integration/test_web_client_types.py`:

1. the committed `types.ts` still matches the live schema (`npm run check:types`);
2. the hand-written error envelope still matches `schemas.ErrorBody`;
3. if the backend ever starts *exposing* that envelope in its schema, the test
   fails on purpose — because the hand-written copy should then be deleted.

The guard has been verified to go red on a real backend field addition, and to
name the field that drifted.

## Checks

```bash
npm run typecheck     # tsc --noEmit
npm run lint          # eslint
npm run check:types   # client types vs live schema
```

`scripts/` is excluded from ESLint: it is build-time tooling running under Node,
not app code.

## State of the screens

Routing stubs and the nav shell only. The six screens — Dashboard, Tickets,
Runs, Run Detail, Approvals, Knowledge — are not built yet; each route renders a
placeholder naming itself.