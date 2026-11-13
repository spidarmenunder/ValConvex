# ValConvex 1.3

## Reliability and performance

- Use exclusive Windows socket binding for the single-instance listener; acquire it before app data initialization, and preserve a request to bring the window forward if it arrives during startup.
- Fetch independent static-data endpoints concurrently. Cached/offline fallback behavior is unchanged.
- Replace per-action daemon thread creation with a bounded, keyed background task scheduler. Repeated profile searches cancel queued predecessors and stale in-flight profiles cannot overwrite the newest result.
- Add cancellation checks to stats/profile work and lobby loading. Shutdown stops polling, cancels queued work, joins the service and workers, and closes SQLite only after background tasks finish.
- Release per-match locks when no caller is waiting and periodically prune expired/excess JSON cache entries toward a 5,000-entry ceiling.
- Record bounded timing samples for startup, idle/match polling, lobby loading, match finalization, and navigation. Periodic logs include aggregate process CPU percentage.

## Analytics

- Newly parsed KAST is unavailable (`null`/displayed as `-`) when round events are absent, incomplete, refer to unknown players, or do not reconcile with scoreboard deaths. Complete feeds retain the existing KAST definition; existing cached/saved match values are preserved.
- New post-match single-game ratings use the shared game score, shrinkage, and output bounds. New MatchData records mark `rating_model: 2`; existing saved matches and `.vtrack` v1 files are not rewritten.
- Effective Skill fetches competitive updates page-by-page until outside the five-hour window or API exhaustion, with a defensive ceiling of 120 updates. A `session_truncated` flag indicates when that ceiling is reached.

Streamer Mode behavior and configuration defaults are unchanged. The release adds no runtime dependencies and does not remove existing UI preferences or match-history behavior.
