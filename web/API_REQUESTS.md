# Requests from the frontend to the backend

The frontend follows `api/CONTRACT.md`. Nothing below blocks the demo. Each item has a workaround already in `web/index.html`.

1. **`session` on `/api/submit` and `?session=` on `GET /api/leaderboard`.** This gives the QR audience its own room.
   - Workaround: the page appends ` [room]` to the team name and filters rows client-side by that suffix.
2. **LLM `comment` on `/api/shock/resolve`.** This is the "AI verdict on the swap" from PLAN §1.3.
   - Workaround: the page builds the comment from `crisis_cost`, `recovered`, `swap_was_optimal`, and `best_possible_swap`.
3. **Crisis-aware `/api/optimize` and `/api/analyze`.** Optional: accept `event_id` so rank and analysis can describe the post-crisis plan.
   - Workaround: after a crisis, the regret meter and the AI panel are labelled «исходный план».
