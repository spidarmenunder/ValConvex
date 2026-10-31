# ValConvex responsiveness update

This update focuses on the reported hard freezes while switching pages. It preserves the existing feature set and data model.

## Changes

- Match History is loaded on first visit instead of competing with startup and live-match detection.
- Match History renders up to 24 cards per page, in batches of four. Older entries remain accessible with **Load more matches**; the database query still considers the latest 200 matches.
- Live-service UI events are coalesced when newer lobby/progress/icon updates supersede old ones.
- UI event handling is spread across Tk event-loop turns with a small processing budget, helping input and redraw events stay responsive during bursts.
- Reopening the current player's Profile no longer triggers a redundant reload after that profile has already been displayed.
- Slow UI event handlers and slow navigation paths are logged to help diagnose any remaining stalls.

## Validation

- Python source compiles successfully.
- UI smoke tests passed, including the full demo session and reported-fix tests.
- One pre-existing service test (`test_full_cycle_with_demo_source`) fails because it expected an autosaved file but found none; it is unrelated to the UI changes and should be investigated separately.

## Build on Windows

Run `build_valconvex.bat` from this project directory. This environment cannot produce or validate a Windows `.exe`, so the included executable from the original archive is intentionally not repackaged as if it contained these changes.


## Windowed layout and Effective Skill update (2026-10-25)

- Added persistent Settings controls for Compact/Balanced/Comfortable layout, 80–120% Tk scaling, compact/comfortable table density, and automatic/expanded/collapsed sidebar.
- Lowered the default window size and minimum size so the app can run windowed; the sidebar collapses automatically on narrower windows.
- Adjusted Live dashboard column proportions and dynamically sizes roster columns to the available table width.
- Compressed the Focused Player match-history columns so Score, Map, Agent, K/D/A, ACS, RR, and When fit in the side panel.
- Effective Skill now uses all available match records within a rolling five-hour window with exponential recency weighting. If no usable session games are available, it falls back to the original last-three-game formula.
- Fallback Effective Skill is amber unless the player is still above the existing green performance threshold; hovering the focused player's Effective Skill value shows the fallback match scores, RR change, and time ago.
- Added regression coverage for the session/fallback selection and fallback tooltip data.
