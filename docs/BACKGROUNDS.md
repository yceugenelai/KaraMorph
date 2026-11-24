# Backgrounds

[English](BACKGROUNDS.md) · [繁體中文](BACKGROUNDS.zh-TW.md) · [日本語](BACKGROUNDS.ja.md)

Back to the [README](../README.md)

Background images rotate in random order while you sing. You can add your own images per song, or use images shared by all songs.
Different backgrounds crossfade over approximately two seconds; the transition pauses with playback.

![Assets → Images: pick images for this song](images/en/images-sub0.png)

![Images and lyrics in the singing view](images/en/karaoke-window.png)

## Where backgrounds come from

Choose under **Settings → Karaoke background images**:

- **Song images only**: the music file's artwork and this song's image folder.
- **Shared and song images** (default): song images plus the shared images.
- **Shared images only**.

With no usable image, `no images` is shown. You can also enable **Gentle background motion** for a slight zoom and movement (the full image stays visible).

## Depth background animation (experimental)

In **Settings → Model and component management**, select **Depth background animation**, deselect other models you do not need, and download or import the Depth Anything V2 Small files (approximately 100 MB). The existing styling runtime is required, but the ACE-Step model is not. Enable both **Gentle background motion** and **Depth background animation (experimental)** in Settings and save.

The singing window header shows the current background mode, depth processing state, selected A3 method and mask coverage. Processing runs separately and is cached; normal motion remains in use while a mask is prepared or if processing fails. The current image may wait up to about 25 seconds for processing and, when ready, shows the result for at least about 5 seconds before rotation. This heuristic can select the wrong regions, and first-time processing consumes GPU or CPU resources. Disable the option if audio is affected. MV exports can also use depth animation.

To prepare images in advance, stop playback, select songs on **Process songs**, and choose **Prepare background animation**, or right-click a song or version. It uses the current background-image setting, including shared images and embedded covers where applicable. A green icon in the song's status means all current backgrounds are cached; amber means some are cached. Hover to see the count. Cached results are reused; progress, failures and cancellation are available in the work area. Each uncached image currently starts a separate model process, so large batches may take time. Run preprocessing again to index caches created by earlier versions without rerunning the model.

Expand **Background animation settings** in Settings to adjust motion strength and speed, coverage limits, person and landscape mask expansion, and solid masks. Changing mask options requires new masks; changing motion strength or speed reuses them.

For setup, preprocessing and MV export, see the [depth animation guide](DEPTH_ANIMATION.md).

## Adding images to a song

Click the song's **Assets** → **Images** tab:

- **Song image selection**: paste or drop images; they are copied into the song folder and the originals stay unchanged. You can also pick one as the list thumbnail.
- **Song image search**: keywords default to the title and artist; search in your default browser, then drag or paste the chosen images onto the receiver bar at the top.
- **Shared images**: the same flow, but images go to the shared folder and are available to every song.

Supported formats: JPG, PNG, WebP and BMP; each image must be under 20 MB. Deleting images and thumbnail settings apply after **Save**.

## Shared background folder

The included shared backgrounds are copied on first use. Add your own images, or delete the defaults; your images are never overwritten. See [Background images](SUPPLEMENTARY.md#background-images) for the location and details.

## See also

These images can also be used in [Create an MV](MV_EXPORT.md). Use only images you are authorized to use.
