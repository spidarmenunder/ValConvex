# ValConvex

An open-source, lightweight desktop tracker for VALORANT, built to provide a broader view of match data, player performance, and live-game information.

## Features

### Live Game Tracking
- Automatic detection of agent select, pregame, and active matches
- Live lobby and player information
- Player identification through Riot IDs and PUUIDs where available
- Agent and team composition tracking
- Rank and player statistics where available
- Custom match data retrieval through supported Riot Client endpoints

### Player Analytics
- Custom Effective Skill rating system
- ACS and ADR-based performance analysis
- Agent-specific performance adjustments
- Session-based performance evaluation
- Recent-match fallback calculations
- Team win probability estimates
- Player and team statistical comparisons

### Match History
- Automatic match detection and local saving
- Persistent match history
- Custom match and scrim organization
- Rename and delete saved matches
- Custom folders for organizing matches
- Review previously recorded match data

### Team Analysis
- Both-team player statistics
- Agent composition analysis
- Player rank information where available
- Party grouping indicators where detectable
- Player identification across supported game states
- Team-level performance comparisons

### Match Visualization
- Match heatmaps where supported by available data
- Focused-player statistics
- Match result and performance summaries
- Agent and team comparison views

### Desktop Application
- Dedicated Python desktop application
- No browser-based interface required
- No in-game overlay required
- Resizable window with adaptive layouts
- Configurable interface scaling and layout preferences
- Persistent application settings
- Designed with performance and low resource usage in mind

### Local Data Management
- Locally stored match history and application preferences
- Automatic retrieval of supported game data
- No manual match ID entry required for automatically detected matches
- Local organization of recorded matches

## Project Goals

ValConvex aims to provide a convenient and analytical alternative to conventional VALORANT trackers by combining live-game information, match history, and custom performance metrics in a lightweight desktop application.

The project prioritizes performance, usability, transparency, and community-driven development.

## Disclaimer

ValConvex is an independent, open-source community project and is not affiliated with, endorsed by, or sponsored by Riot Games.

Data availability depends on supported Riot Client endpoints, permissions, and match type. Custom metrics, including Effective Skill and team win probability, are unofficial estimates and are not representations of Riot Games' official ratings or predictions.
