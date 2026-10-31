# ValConvex

Windows desktop VALORANT companion/tracker.

## Build

On Windows with Python 3.10+ and internet access:

1. Extract this folder.
2. Run `build_valconvex.bat`.
3. Copy `dist\ValConvex.exe` wherever you want.

The build is a PyInstaller one-file/windowed executable.

## Match Results

ValConvex keeps match results in `Documents\ValConvex\Match Results` with category folders:

- Ranked
- Scrims
- Unrated
- Unsorted

Match History can create additional folders, import `.vtrack` files into a selected folder, filter by folder, move matches between folders, rename/delete matches, and open the results directory in Explorer.

Live autosaves are categorized from the match queue.

## Player tracker

- Search a Riot ID from Tracker, Post-match, or Match History.
- Players already present in saved matches can be opened by PUUID without a HenrikDev API key.
- New Riot ID searches can use the optional HenrikDev key configured in Settings.
- The key is stored locally and is not embedded in the executable.

## UI and layout preferences

The current visual design is preserved. Settings now includes Compact, Balanced, and Comfortable layout modes; 80–120% interface scaling; compact/comfortable table density; and automatic/expanded/collapsed sidebar behavior. Settings are saved in `config.json` and applied without restarting. The window can be resized, and the sidebar adapts automatically in auto mode. The live roster and Focused Player match history use compact responsive column widths to reduce clipping in windowed mode.

Effective Skill uses the player's competitive matches from the last five hours, weighting newer games more heavily. When there are no usable matches in that window, it falls back to the original last-three-match calculation. The fallback skill value is amber unless the player meets the existing green above-average threshold; hovering a fallback Effective Skill value shows the three matches' scores, RR changes, and age.

Non-live surfaces retain darker subtle borders, larger typography, and thin dark Apple-style scrollbars where scrolling is shown. Post-match uses agent portraits and rank badges, while keeping full rank tier text (Iron 1/2/3, Bronze 1/2/3, etc.).

## Windows icon build

`build_valconvex.bat` now cleans old PyInstaller output before every build and uses the dedicated `valconvex.ico` for the executable and Windows application icon. The ICO contains 16, 24, 32, 48, 64, 128, and 256 pixel variants.

If Windows still shows an old icon on an existing desktop shortcut, delete that shortcut and create a new shortcut to `dist\\ValConvex.exe`; Windows caches shortcut icons separately from the executable.
