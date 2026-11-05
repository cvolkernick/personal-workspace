# AtlasGO fleet portal demo

Sample-data walkthrough of a fleet return ledger. Sign-in is a picker with three sample investors and one sample admin. There are no passwords. Do not deploy this app on a public production domain. Keep it on a protected preview.

```bash
cd apps/atlasgo-demo
npm install
npm test
npm run lint
npm run build
npm run start
```

Open http://localhost:3000 and continue as a sample investor or the sample admin.

The demo clock is 5 Nov 2026 (`ATLASGO_TODAY`) so due-back flags stay lined up with the May–October 2026 sample history.

## Data source

Every read and write goes through the `DataSource` interface in `lib/data/types.ts`. Pages import `getDataSource()` and the schema types. They do not import the JSON seed or an adapter class.

| `DATA_SOURCE` | Adapter | Behavior |
| --- | --- | --- |
| `mock` (default) | `MockDataSource` | Reads `lib/data/seed/fleet.json` through the interface |
| `live` | `LiveDataSource` | Stub. Every method rejects with `NotImplementedError` |
| `memory` | `AltMemorySource` | Second in-memory adapter, used to prove the UI can swap sources |

```bash
DATA_SOURCE=live npm run dev
DATA_SOURCE=memory npm test
```

`npm run test:e2e` runs the browser suite against the mock adapter. `npm run test:e2e:memory` runs that same suite against the alternate in-memory adapter.

## Add a new adapter

1. Add `lib/data/your-source.ts` and `export class YourDataSource implements DataSource`.
2. Use the entity shapes already declared in `lib/data/types.ts` (`Investor`, `Car`, `Trip`, `Expense`, `Invoice`, `CarStatus`). Do not invent a second model for the pages.
3. Register the class in `createDataSource()` in `lib/data/index.ts` under a new `DATA_SOURCE` value.
4. Add that class to `tests/contract.test.ts` so the shared contract runs against it, next to the mock source and the live stub.
5. Leave `app/` and `components/` unchanged. The ESLint rule `atlasgo/no-direct-adapter-import` rejects imports of the seed file or adapter modules from those folders, and `tests/arch.test.ts` checks the same boundary.

ROI math in `lib/roi/calc.ts` is a pure function of cars, trips, and expenses. It does not load the seed. A new adapter only has to return those entities.

Net for a month is income minus maintenance, other expenses, and the monthly payment. Income is trip gross minus fees. ROI percent is cumulative net divided by purchase cost plus down payment. The ROI hit badge appears when cumulative net reaches that capital. A maintenance alert appears when maintenance is more than 25% of that month's gross.
