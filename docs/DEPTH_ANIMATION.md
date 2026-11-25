# Depth background animation (experimental)

[English](DEPTH_ANIMATION.md) · [繁體中文](DEPTH_ANIMATION.zh-TW.md) · [日本語](DEPTH_ANIMATION.ja.md)

Depth estimation selects parts of a still background for slow motion during karaoke or MV export. This experimental effect varies with the image.

## Setup and playback

1. Under **Settings → Model and component management**, select **Depth background animation** and download, repair or import Depth Anything V2 Small (about 100 MB). Deselect other models if unnecessary. Weights are downloaded separately; the [model card](https://huggingface.co/depth-anything/Depth-Anything-V2-Small-hf) identifies the Small weights as Apache-2.0.
2. Prepare the styling runtime; the ACE-Step model itself is not required. Expand **Background animation settings**, enable **Gentle background motion** and **Depth background animation (experimental)**, and save. Depth animation is enabled for new settings by default.
3. Start singing. The window header reports the mode, processing state, selected A3 method and mask coverage. A separate process prepares each image; ordinary background motion remains available while preparing or if preparation fails.

## Preprocessing and cache

Stop singing, select songs on **Process songs** and choose **Prepare background animation**, or right-click a song or version. The current background source determines which song images, shared images and embedded covers are processed. Progress, failures and cancellation are available in the work area.

A green status icon means all current backgrounds are prepared; amber means some are prepared. Hover over the status for the count. Results are stored in `.app_data/cache/depth-motion` and do not belong in version control. Each uncached image starts a model process, so a large batch can take time.

Strength and speed changes reuse masks. Coverage, expansion or solid-mask changes require preparing affected images again; changing an image or the model may also require preparation. Old caches are retained and can be reused when switching back if the image and model still match.

## Settings and MV export

**Background animation settings** provides strength, speed, minimum and maximum coverage, person and landscape expansion, and solid masks. Defaults for new settings are 30% strength, 40% speed, 2%–70% coverage, 18 px person expansion, 4 px landscape expansion, and solid masks enabled. Coverage limits guide mask selection; actual coverage can fall outside them if no candidate fits. Scroll the page with the wheel; edit animation values by typing or using the arrows.

For MV export, enable **Use depth background animation**. Enable **Image motion** too to combine local motion with whole-image pan and zoom. Preview uses prepared masks; missing masks are prepared before export. If the model or runtime is unavailable or preparation fails, export reports the reason and stops; you can disable depth animation and export again. See [MV export](MV_EXPORT.md).

## Limitations

Depth and person/landscape decisions are heuristics rather than semantic segmentation. They can select unwanted regions, move the ground or leave ghosting. Processing uses CUDA when available, otherwise CPU; first-time processing can affect performance. Prepare images in advance or disable depth animation if playback is affected. See [Backgrounds](BACKGROUNDS.md) for image sources and rotation.
