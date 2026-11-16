# Supplementary information

[English](SUPPLEMENTARY.md) · [繁體中文](SUPPLEMENTARY.zh-TW.md) · [日本語](SUPPLEMENTARY.ja.md)

Back to the [README](../README.md)

## License and legal notice

Personal use does not automatically grant permission. Publishing modified music can require reproduction, adaptation, public-performance/public-transmission and recording-related permissions, depending on applicable law. LRCLIB availability does not itself grant redistribution permission.

- The lightweight ZIP's [distribution scope and artifact check](DISTRIBUTION.md)
- Screenshot and visual asset provenance: [asset credits](ASSET_CREDITS.md), [visual asset license scope](../assets/ASSET_LICENSE.md)
- Icons were generated using OpenAI ChatGPT; shared backgrounds were generated using paid Nano Banana Pro in Google AI Studio. The screenshots are actual app captures.
- Code and the four shared backgrounds are MIT. Icons remain rights-reserved KaraMorph branding assets; screenshot content has its own rights.
- Development of KaraMorph was assisted by OpenAI ChatGPT and Codex.

## Background images

The default shared backgrounds folder is `outputs/shared_images/` (under your selected output directory if changed). The included backgrounds are copied on first use. Add your own images there; existing user images and deleted defaults are respected. [Adding backgrounds](../assets/README.md).

## First-run setup

No installed Python or Git is required. Choose English, Traditional Chinese or Japanese. On the first launch English is selected; later launches use your saved language. Vocal separation and ACE-Step are selected by default: setup prepares their Python environments, models and pinned ACE-Step source in this folder. The small ZIP size does not represent the installed size.

Optional features can be unchecked. Basic playback and live speed/key adjustments work without AI models; first-run setup downloads and verifies SoundTouch. Creating saved speed/key versions still requires the ACE-Step runtime. Completed components are kept if preparation fails or is cancelled. Retry, or start with the available features. Settings → Manage installation can add features later; normal launches do not install previously unchecked features. [Setup and build details](BOOTSTRAP.md).

**Model and component management** shows SoundTouch's installation and verification status and lets you download or repair it independently of the AI models.

## Hardware and platform

NVIDIA CUDA is preferred for AI processing. Long tracks or insufficient VRAM can use CPU after confirmation; CPU styling is slow and needs substantial RAM. AMD/Intel GPU acceleration is not supported in this preview. Windows 11 x64 is the primary target; audio devices and drivers vary. WASAPI exclusive output can prevent other apps from using the same device.

## Playback speed and key

Speed −/+ buttons change it by 0.1×; **More settings → Playback speed / key** retains the slider for fine adjustments and a reset button for speed and key. Changes affect the current music version and guide vocals; microphone monitoring and dry recordings stay unchanged. Each song starts at 1.0× / key 0 unless you click the star button, which remembers the current speed and key for that song and applies them automatically next time; the **Original** button returns to neutral at any time. Best settings, like the playlist, volume preferences and recordings, belong to your profile (`.app_data/profiles/<name>/`) and are matched by song content, so they survive moving files. Lyrics follow its source position. Whole-song recordings additionally save the backing actually played as a WAV for synchronized preview, using extra disk space. Both shared and exclusive playback support this feature; it does not create a new song version.

## Song library and data locations

The processing library follows the actual music folder. Click Reload songs after adding, removing or moving files. Missing originals disappear from this list; generated audio, recordings and assets remain on disk. Existing playlist items stay marked unavailable until their original source returns. There is no persistent song blacklist.

Settings, playlists, logs and caches live in `.app_data/`; models in `models/`; processed audio in `outputs/`. Output and model locations can be selected in the UI. Language changes require restart. Updates are manual: keep your data folders and replace application files after backup. See [known limits](RELEASE.md).

## Moving user data

To move a profile to another computer (or to a new version), use **Settings → Move profile**. Both exports are a single ZIP:

- **Export settings**: volume preferences, playlist and best speed/key settings only. Songs are matched by content, so it applies wherever the same song exists; playlist entries without a match are skipped.
- **Export everything (with music)**: also includes the music files, processed results, lyrics and image assets, this profile's recordings and the shared backgrounds. On import, music goes into that computer's music folder (set it first) and paths and the playlist are remapped automatically, recreating the same environment. The file can be very large; use it only to move to your own computer.
- You can rename the profile on import; an existing profile of the same name is overwritten after confirmation.

Song identity includes the full path, so do not move by copying the music or output folders by hand; versions, recordings and playlists may lose their links.

## Build the lightweight ZIP

On Windows 11 x64, open PowerShell in this source folder. Use a Python environment with PySide6 installed (see [Python setup and source execution](DEVELOPMENT.md)):

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build-bootstrap-poc.ps1 -UiPython C:\path\to\python.exe
```

The archive is `dist/KaraMorph-bootstrap-poc.zip`; runtimes, models and personal data are excluded. To rebuild an existing package, add `-Repack`. [Build details](BOOTSTRAP.md).

## Development

For Python environment setup and running from source, see [the developer guide](DEVELOPMENT.md).
