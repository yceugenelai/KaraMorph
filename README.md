<div align="center">
  <img src="assets/icon_doc.png" width="112" alt="KaraMorph icon" />
  <h1>KaraMorph · カラモーフ</h1>
  <p>KaraMorph — Transform the style. Keep the song.</p>
  <p>Your favorite songs. Your favorite styles. Your own singing playlist.</p>
</div>

[English](README.md) · [繁體中文](README.zh-TW.md) · [日本語](README.ja.md)

## Why KaraMorph?

Sometimes the song you want to sing just is not in the karaoke catalog. Sometimes you want to try a familiar song as jazz, rock, or something else. That is why I started KaraMorph: to make a little more room for personal choice. I wish existing karaoke systems could offer this kind of personalization, too.

### Song processing

![Song processing](docs/images/processing-placeholder.png)

### Karaoke playback

![Karaoke playback](docs/images/singing-placeholder.png)

<sub>Screenshot demo: <a href="https://commons.wikimedia.org/wiki/File:Amazing_Grace_US_Marine_Band.ogg">Amazing Grace — United States Marine Band</a>. Wikimedia Commons identifies the recording and John Newton’s original English lyrics as public domain.</sub>

The default shared backgrounds folder is `outputs/shared_images/` (under your selected output directory if changed). The included backgrounds are copied on first use. Add your own images there; existing user images and deleted defaults are respected. [Adding backgrounds](assets/README.md).

A Windows karaoke app with vocal separation, key/speed changes, AI style variations, playlists, lyrics, backgrounds and microphone recording. Traditional Chinese, English and Japanese UI are supported.

## Quick start

This example turns one MP3 into a jazz backing track, then adds it to your singing playlist. No coding or Python installation is needed.

1. **Download**: Open [Releases](https://github.com/yceugenelai/KaraMorph/releases), expand **Assets** under the version you want, and download `KaraMorph-bootstrap-poc.zip`. This is the application package.
2. **Extract and launch**: In File Explorer, right-click the ZIP → **Extract All**, for example into a KaraMorph folder on your desktop. Open the extracted folder, find `KaraMorph.exe` and double-click it. Extract the ZIP before running the app.
3. **Complete first-run setup**: Choose your UI language, keep vocal separation and ACE-Step selected, and start preparation as shown on screen. Stay connected to the internet and wait for it to finish. Allow roughly 35–40 GiB including download caches.
4. **Choose an input folder**: Create a folder named `input` beside `KaraMorph.exe`. In the app's **Settings**, click **Browse…** beside **Music folder**, select that `input` folder, then click **Save settings**. Leave the output folder at its default.
5. **Add one song**: Use File Explorer to copy an MP3 you are authorized to use into `input`, for example `My Song.mp3`. Open **Songs** and click **Reload songs** to see it in the list.
6. **Separate vocals**: Check the box beside the song, click **Separate vocals** and wait for completion. Expand the arrow beside the song name to find **Backing only**.
7. **Find lyrics**: Keep the song checked and click **Fetch lyrics**. If none are found, click the song's **Assets** to search manually or add your own lyrics. Playback also works without lyrics.
8. **Change the style**: Click **Create version** beside the song. Choose **Lounge Jazz** under **Style**, leave speed at 1.0× and key at 0, then click **Create style versions** and wait. AI processing can take a while.
9. **Add to your singing playlist**: Expand the song, find a completed jazz version and click its **Add to playlist** button. You can also add **Backing only** to sing with the original backing style.
10. **Start singing**: Open **Sing**, select the song in your playlist and click **Play**. The backing track and lyrics window will open so you can sing along. Click **Save playlist** to keep your list.

## Build the lightweight ZIP

On Windows 11 x64, open PowerShell in this source folder. Use a Python environment with PySide6 installed (see [Python setup and source execution](docs/DEVELOPMENT.md)):

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build-bootstrap-poc.ps1 -UiPython C:\path\to\python.exe
```

The archive is `dist/KaraMorph-bootstrap-poc.zip`; runtimes, models and personal data are excluded. Extract the entire ZIP to a separate writable folder, then run its `KaraMorph.exe`. To rebuild an existing package, add `-Repack`. [Build details](docs/BOOTSTRAP.md).

## First-run setup

Extract the entire bootstrap ZIP to a writable folder and run `KaraMorph.exe`. No installed Python or Git is required. Do not run inside the ZIP or Program Files.

Choose English, Traditional Chinese or Japanese. On the first launch English is selected; later launches use your saved language. Vocal separation and ACE-Step are selected by default: setup prepares their Python environments, models and pinned ACE-Step source in this folder. Both features may require roughly 35–40 GiB including download caches. The small ZIP size does not represent the installed size.

Optional features can be unchecked. Basic playback works without models; key/speed changes currently require the ACE-Step runtime. Completed components are kept if preparation fails or is cancelled. Retry, or start with the available features. Settings → Manage installation can add features later; normal launches do not install previously unchecked features. [Setup and build details](docs/BOOTSTRAP.md).

NVIDIA CUDA is preferred for AI processing. Long tracks or insufficient VRAM can use CPU after confirmation; CPU styling is slow and needs substantial RAM. AMD/Intel GPU acceleration is not supported in this preview. Windows 11 x64 is the primary target; audio devices and drivers vary. WASAPI exclusive output can prevent other apps from using the same device.

## Everyday use

The processing library follows the actual music folder. Click Reload songs after adding, removing or moving files. Missing originals disappear from this list; generated audio, recordings and assets remain on disk. Existing playlist items stay marked unavailable until their original source returns. There is no persistent song blacklist.

Settings, playlists, logs and caches live in `.app_data/`; models in `models/`; processed audio in `outputs/`. Output and model locations can be selected in the UI. Language changes require restart. Updates are manual: keep your data folders and replace application files after backup. See [known limits](docs/RELEASE.md).

Use only material you are authorized to process. Personal use does not automatically grant permission. Publishing modified music can require reproduction, adaptation, public-performance/public-transmission and recording-related permissions, depending on applicable law. Lyrics and artwork also have rights. LRCLIB availability does not itself grant redistribution permission.

For Python environment setup and running from source, see [the developer guide](docs/DEVELOPMENT.md).

## License

Development of KaraMorph was assisted by OpenAI ChatGPT and Codex.

KaraMorph's own source: [MIT](LICENSE). Dependencies, models and native binaries have separate terms. The lightweight ZIP has a separate [distribution scope and artifact check](docs/DISTRIBUTION.md). No audio tracks or model weights are supplied. Screenshots and visual assets have separate provenance notes in [asset credits](docs/ASSET_CREDITS.md).

The icons were generated using OpenAI ChatGPT; shared backgrounds were generated using paid Nano Banana Pro in Google AI Studio. The screenshots are actual app captures. See [asset provenance and license scope](docs/ASSET_CREDITS.md).

Code and the four shared backgrounds are MIT. Icons remain rights-reserved KaraMorph branding assets; screenshot content has its own rights. [Visual asset license scope](assets/ASSET_LICENSE.md).

Maintained by [OpticalPivot](https://www.opticalpivot.net/).
