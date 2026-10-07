"""ACE-Step 1.5 styling: paths, model names, device settings, default parameters.

Runs in the project-owned .runtime/styling environment.
"""
import os
import sys
from pathlib import Path

STYLE_POC_ROOT = Path(__file__).resolve().parent
MAIN_PROJECT_ROOT = STYLE_POC_ROOT.parents[1]
ACE_STEP_ROOT = Path(os.environ.get("AIK_ACE_STEP_ROOT", MAIN_PROJECT_ROOT / "third_party/ACE-Step-1.5"))
OUTPUT_ROOT = Path(os.environ.get("AIK_OUTPUT_ROOT", MAIN_PROJECT_ROOT / "outputs"))

if str(ACE_STEP_ROOT) not in sys.path:
    sys.path.insert(0, str(ACE_STEP_ROOT))  # `import acestep` works from any cwd

SAMPLE_RATE = 44100  # same as the main project's config.SAMPLE_RATE

COVER_MODEL = "acestep-v15-turbo"

# RTX 3050 Ti Laptop (4GB) = ACE-Step "Tier 1": no LM, INT8 weights, CPU/DiT offload
DIT_INIT_KWARGS = dict(
    device="cuda",
    use_flash_attention=False,
    compile_model=False,  # avoids a long torch.compile warmup
    offload_to_cpu=True,
    offload_dit_to_cpu=True,
    quantization="int8_weight_only",
)

DEFAULT_START = 0.0
DEFAULT_DURATION = 0.0  # 0 = to the end of the song
DEFAULT_SEEDS = (1234, 5678, 9012)
# Conservative standalone default; the app worker replaces this per request
# with the detected GPU tier, or the accepted CPU input length.
MAX_CLIP_SECONDS = 360

# Turbo bakes guidance into the weights (guidance_scale is forced to 1.0) but shift must be set.
COVER_INFERENCE_STEPS = 8
COVER_SHIFT = 3.0

DEFAULT_COVER_STRENGTHS = (0.2, 0.5, 0.8)
# cover_noise_strength is what anchors the output to the source's harmony/melody
# (0 = start from pure noise, chords drift). audio_cover_strength does NOT do this.
# Starting point only: find the real value per song with cover-search.
DEFAULT_COVER_NOISE_STRENGTH = 0.6

# cover-search / cover-explore: turbo accepts up to 20 steps, and more steps give a finer
# timestep grid, so nearby cover_noise_strength values don't snap to the same anchor.
SEARCH_INFERENCE_STEPS = 20
# cover-search varies all three axes at once (strength x noise strength x seed, paired so every run
# differs on each axis), then flags a spread of candidates. Strength is capped at 0.5 on purpose:
# above ~0.7 the target style barely takes hold, and near it every run starts to sound alike.
DEFAULT_SEARCH_COVER_STRENGTHS = (0.1, 0.18, 0.26, 0.34, 0.42, 0.5)
DEFAULT_SEARCH_COVER_NOISE_STRENGTHS = (0.1, 0.15, 0.2, 0.3, 0.45, 0.6)
DEFAULT_SEARCH_SEEDS = (1234, 5678, 9012, 2024, 4242, 8888)
DEFAULT_SEARCH_COUNT = 6  # keep generations per process <= ~8-10 (VRAM creep, see usage.md)
DEFAULT_SEARCH_CANDIDATES = 4

# Skewed low on purpose: above ~0.7 the target style barely takes hold.
DEFAULT_EXPLORE_COVER_STRENGTHS = (0.1, 0.2, 0.3, 0.4, 0.5, 0.65)
DEFAULT_EXPLORE_SEEDS = (1234, 5678, 9012, 2024, 4242, 8888)
DEFAULT_EXPLORE_COUNT = 6
