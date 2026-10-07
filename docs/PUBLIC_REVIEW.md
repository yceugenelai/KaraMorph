# Public-source review — updated 2026-10-07

## Source boundary

The exporter selects only `app/`, `workers/`, `scripts/`, `tests/`, `assets/`, `docs/` and the named root source/license/README files. It excludes runtime/model/data/backup/build folders and rejects unexpected media or credential files. Images are permitted only in `assets/` and `docs/images/`. Public source scans found no local user/work paths or recognizable credential material. This is a targeted scan, not a guarantee against every possible secret.

The sole legacy PoC name in runtime code rejects stale settings pointing to that project; its test verifies the warning. It is not an execution dependency. Model weights and pinned ACE-Step source are downloaded separately. `style_test.py` and `report_ops.py` are used runtime modules.

## Corrections during this review

- Updated three README background descriptions from placeholders to the supplied image set.
- Clarified the separate licenses for supplied visual assets and screenshot content. Generation provenance is owner-confirmed and the provider-terms review is recorded in ASSET_CREDITS.md. The replacement captures reviewed on 2026-10-07 contain no album-cover thumbnails and show Amazing Grace original English lyrics; the earlier screenshot-content review items are superseded.
- Updated current acceptance counts and bootstrap build prerequisites (UI Python/PySide6 is needed for icon rendering).
- Added fresh repository-directory export and repository handoff instructions; old review history is archived locally and excluded.

## Validation

82 tests passed in the independent source suite. The fresh exported-tree suite, README links, source ZIP CRC/hash/file membership, Python syntax, asset sizes, packaged-source membership and ignored data boundaries are checked during this handoff. No AI versions or weights are changed by this review. Tests do not certify a fresh Windows machine, every audio driver, or full-length AI conversion.

## Asset review status and remaining publication items

1. Generation sources are confirmed: ChatGPT icons and paid AI Studio Nano Banana Pro backgrounds. The owner chose MIT for the four backgrounds and reserved rights for the three icons. The updated processing screenshot has been visually reviewed and contains no album-cover thumbnail. The current singing screenshot uses Amazing Grace original English lyric lines; see ASSET_CREDITS.md.
2. Choose the public repository URL and review maintainer/copyright attribution.
3. Only the lightweight bootstrap ZIP is provided as an application package. Runtimes, models and uv are downloaded on first launch and are not distributed in the archive. Verify each ZIP and test the extracted application before publishing the draft Release; see DISTRIBUTION.md and RELEASE.md.

The own-code source export is prepared for repository handoff. Its inclusion of supplied visual assets does not certify those assets for public distribution. No Git commit, remote repository or publication is performed by preparation.

## Provider-terms review update

Owner statements and official OpenAI/Google terms were reviewed on 2026-10-06. No noncommercial-only restriction was identified for including the supplied generated icons/backgrounds in the application; this finding is conditional on applicable terms and third-party/input rights. Asset hashes and reported generation tools are recorded in asset-provenance.json. Both current screenshots were visually reviewed on 2026-10-07: processing contains no album-cover thumbnails, and singing displays Amazing Grace original English lyrics. The previous Red River Valley capture is no longer supplied. See ASSET_CREDITS.md for the current demo source and the recording's U.S. public-domain designation; no recording is distributed. Runtime code and model dependencies are unchanged.

## Music-folder synchronization correction

Reload now replaces the processing library with the current directory scan. It drops missing-source entries and discards legacy removed_ids, allowing restored files to appear again. The persistent "remove from library" context menu/helper and its translations were removed. Workspace results, recordings, assets and playlist entries are retained; unresolved playlist items remain marked unavailable. Regression tests exercise actual file removal/restoration, legacy exclusions, preserved output data and Qt reload/neighbor/playlist behavior. Development storage logic is synchronized too.

## Documentation update — 2026-10-07

Three READMEs now include the supplied OpticalPivot maintainer link and a small planned Amazing Grace demo credit below the singing capture. Recording and original English lyric status were separately checked against the Commons file page. This historical note is superseded by the replacement-capture review below; no audio was added. Application code is unchanged.

## Replacement captures received — 2026-10-07

Both screenshots were replaced by the owner and visually reviewed. Singing shows Amazing Grace with original English lyric lines; processing shows versions without cover thumbnails. README captions now describe the actual demo; provenance hashes are current. No application code changed.
