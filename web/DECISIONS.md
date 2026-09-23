# Frontend decisions

1. **The page was built without PLAN.md.** It wasn't in the local checkout, and fetching from GitHub was declined during this session. Screens, numbers, and wording come from the brief: 52.56 baseline, 57.24 maximum, 694 395 plans, 14 measures, 5 directions, 5 slots, budget 100, 8 quarters, and a re-election line at 50. Reconcile with PLAN.md §4 and §12 once it is available.
2. **The API contract is our guess.** It is written down in `API_REQUESTS.md` and implemented by `mock/engine.js`. The page accepts a few naming variants through `normalizeDataset()`.
3. **One base URL.** `CONFIG` at the top of the script in `index.html` sets it. `?api=` overrides the base, and `?mock=1` forces the mocks.
4. **Offline fallback.** If `GET /api/dataset` fails, the page switches entirely to the mocks and shows an amber banner. If the dataset came from the real API and a later call fails, the page shows an error card with a retry button. It does not mix data sources.
5. **The mock dataset is fictional.** It uses Astana's 5 districts: Алматы, Байконур, Есиль, Сарыарка, Нура. Population-weighted, it reproduces the 52.56 baseline. Gains are scaled so the best of every valid plan scores exactly 57.24.
6. **Mock approval rules.** Visible gains by Q4, the number of districts helped, red districts, and concentration of spending in one district all count. As a result, the AQLS-optimal plan, which pours everything into Алматы, gets about 49%, and the akim is fired. That is the "optimum gets fired" beat.
7. **Recommendation logic in the mock.** The mock recommends up to 2 swaps from the user's own plan that raise AQLS while keeping approval at 50 or above. This makes «Применить рекомендацию» actionable.
8. **Crisis.** The mock shock is «Весенний паводок на Есиле». The budget limit drops to 85, ЖКХ and health in Нура and Есиль count ×1.5, and exactly one swap is forced. The swap is re-validated live against 85.
9. **The crisis opens from a pulsing banner at the top of Вердикт,** not on a timer. That keeps the live demo in the presenter's hands.
10. **Every plan needs exactly 5 decisions.** Each measure can appear once. A district measure needs a district, picked in a modal.
11. **The mock leaderboard lives in localStorage.** It is seeded with the "Оптимум (перебор всех планов)" row at 57.24 and 49%, marked Уволен, for the reveal. `?session=` filters rows.
12. **Look.** A dark slate background with one accent, #19b8d8, a nod to the flag's sky blue. Red is used only for danger states: overrun, below 40, fired, crisis. The newspaper uses a light paper style with Playfair Display and PT Serif, both of which support Cyrillic.
13. **Stack.** No build step: Tailwind play CDN, Chart.js 4.4.1 from jsDelivr, and Google Fonts. Mock files load only in mock or offline mode. The page must be served over HTTP, not `file://`. Running `python -m http.server` inside `web/` is enough.
14. **Smoke test.** `web/tools/smoke.cjs` drives the whole demo path at desktop and phone widths with Playwright. It fails on any browser error and checks for horizontal overflow. With `SHOTS=1` it also writes the screenshots.
