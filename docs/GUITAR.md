# Guitar practice (optional plugin)

[English](GUITAR.md) · [繁體中文](GUITAR.zh-TW.md) · [日本語](GUITAR.ja.md)

Open **Guitar practice** between MV export and Settings. Before first use, select **Install / repair guitar plugin** under **Settings → Plugin management**. LV-Chordia and its CPU runtime are downloaded to `.runtime/guitar`, independently of the existing separation and styling environments. Installation needs network access and disk space; recognition then runs locally. The same settings section enables or disables the plugin.

Generation, cancellation, chart version, capo, smoothing and PDF export share one row, with playback controls underneath. **Edit chords** is collapsed by default. Chord diagrams appear above their corresponding lyrics with spacing to avoid overlap; click a diagram to seek. Strings run from low E on the left to high E on the right; × means muted, ○ means open, and 1–4 indicate fingers. A number at the left indicates the starting fret relative to the capo.

Choose small, medium or large diagrams; small is the default, and the selection is saved with the chart. During playback the current diagram has a gold background, border and dot, including no-chord sections in the intro. Automatic scrolling keeps the active diagram visible.

The chart header uses the thumbnail selected in the song's image assets, falling back to its embedded cover. Expand **Edit chart information** below the chart to set a title, artist, note and chart-specific image for this song version; saving does not change the music file or its original thumbnail setting. **Export PDF…** saves the selected chart, capo shapes, lyrics and header as an automatically paginated A4 PDF. The playback highlight is omitted. Generate a chart before exporting.

Common chords use built-in shapes. For supported chord symbols without a built-in shape, the app attempts to generate a standard-tuning voicing subject to pitch, bass and finger-span constraints, and saves successful results in `.app_data/guitar-voicings.json` for reuse. Generated diagrams are labelled **Generated**. These checks do not guarantee a comfortable fingering or a suitable musical choice, so verify them yourself. Unsupported symbols and chords for which no suitable shape was found show **No diagram available**.

Choose a song and version, then generate a chart. The original/separated-backing choice analyzes the existing backing; prepare separation first if it is missing. Other versions are analyzed using their own audio and have separate charts. Original and separated backing can be selected for playback; derived versions play their own audio, avoiding mismatched arrangements and speeds.

The chart uses saved LRC lyrics, version speed and lyric offsets. Play, pause, seek, or click the chart or list to jump; playback follows the current line and chord. Ordinary LRC has line timestamps only, so positions within each line are estimated and are not word-accurate. Chord timings remain available without LRC.

Choose raw, smoothed, standard or simple charts. Adjust smoothing strength and apply it without rerunning the model. Double-click a list item to edit a single **concert-pitch** chord; edits are saved for the current chart mode. Edits are tied to their original chord and time interval: if smoothing changes the interval, the old edit remains saved but is not applied to a different interval. Regeneration backs up charts containing edits; **Restore previous chart** restores the most recent backup. The model's original output is kept separate from manual edits.

Capo 0–7 changes displayed shapes and is saved with the chart; it does not change the audio or saved concert-pitch chords. For example, F# with capo 2 displays an E shape. Recognition is approximate; verify by listening. Guitar recording is not yet supported. Regenerate after changing the version's audio. Charts are stored under `songs/<song ID>/guitar/` in the output folder.
