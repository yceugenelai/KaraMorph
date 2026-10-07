# Third-party notices and redistribution review

The MIT license covers KaraMorph-owned code only. Preserve model notices under `docs/licenses/`, upstream ACE-Step LICENSE, runtime Python LICENSE and all wheel `*.dist-info/licenses` / LICENSE files. The lightweight ZIP inventory records distributed file hashes. Runtime lockfiles identify packages downloaded after launch; their installed notices remain with those packages.

## Reviewed components

- BS-RoFormer architecture and the selected **Kim model** are separate works. Use only the pinned Kim snapshot in `docs/model-assets.lock.json`; its stored model card declares MIT. Do not assume every BS-RoFormer weight has the same license.
- ACE-Step 1.5 pinned source and weight snapshot declare MIT; bundled Qwen embedding declares Apache-2.0. Preserve each notice. htdemucs is not included.
- The unused Demucs-only `diffq-fixed` dependency declares CC BY-NC 4.0; it is excluded from KaraMorph locks and binaries. Only the pinned Kim MDXC architecture is supported.
- TinyTag is MIT. The previous direct Mutagen dependency (GPL-2.0-or-later) was replaced; it is excluded from UI build locks.
- PySide6/Qt are LGPLv3/GPL/commercial licensed. The downloaded runtime uses shared libraries. Preserve notices, provide applicable corresponding Qt source and replacement/relinking rights; do not impose restrictions on reverse engineering for debugging modifications. A package metadata label does not clear every Qt plugin.
- `imageio-ffmpeg` wrapper is BSD-2-Clause, but its bundled Windows FFmpeg 7.1 binary reports `--enable-gpl --enable-version3 --enable-static`: **GPLv3**. Rubber Band in that build also carries GPL/commercial terms. Before distributing that binary, supply its complete corresponding source, configuration, patches and build materials under an appropriate GPLv3 distribution method. A link to generic FFmpeg source is insufficient.
- Styling dependencies include LGPL packages such as frozendict and soxr. Retain their notices and satisfy their own source/replacement obligations. NumPy/SciPy wheels embed additional third-party notices; assess their native components individually.
- PyTorch CUDA wheels contain NVIDIA libraries. Check the actual redistributable files against the applicable CUDA/cuDNN license lists and preserve their EULAs/notices; CUDA tooling as a whole is not automatically redistributable.
- Python and the remaining pinned distributions retain their original licenses. Package metadata alone does not establish the terms of every downloaded native component.

## Primary references

[Qt licensing](https://doc.qt.io/qtforpython-6/licenses.html), [FFmpeg legal](https://www.ffmpeg.org/legal.html), [Rubber Band licensing](https://breakfastquay.com/technology/license.html), [CUDA EULA](https://docs.nvidia.com/cuda/eula/), [GNU GPL FAQ](https://www.gnu.org/licenses/gpl-faq.html.en), [TinyTag MIT](https://github.com/tinytag/tinytag/blob/master/LICENSE), [ACE weights](https://huggingface.co/ACE-Step/Ace-Step1.5/blob/19671f406d603126926c1b7e2adc169acbcade22/README.md).

KaraMorph provides only the lightweight bootstrap ZIP, excluding runtimes, native dependency binaries and model weights. First-run setup downloads them from upstream. Keep the installed notices with those components; the release ZIP review does not grant permission to redistribute an initialized installation. See [distribution scope](docs/DISTRIBUTION.md).

First-run setup downloads uv 0.12.21 from upstream. It is licensed MIT OR Apache-2.0; both license texts are retained in docs/licenses/. The release ZIP does not include its binary.

Metadata gaps: llvmlite packages retain BSD-style LICENSE and LLVM third-party notices; torchao 0.16.0 retains BSD-3-Clause LICENSE ([upstream](https://github.com/pytorch/ao/blob/v0.16.0/LICENSE)). pytorch-wavelets distinguishes its MIT DWT code from DTCWT provenance restrictions; ACE uses DWT1D, retain the installed wheel notices and review their scope before any separate redistribution.

## Project visual assets

The four named generated backgrounds use the project MIT license. The three generated icons are rights-reserved KaraMorph branding and may be distributed with this project; they are not covered by the MIT grant. Screenshots have separate embedded-content status. See [visual asset license scope](assets/ASSET_LICENSE.md) and [provenance review](docs/ASSET_CREDITS.md).
