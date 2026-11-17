"""Dependency-free helpers shared by the separation pipeline and its tests."""

_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def session_folder_name(stem: str, limit: int = 80) -> str:
    """A file name stem that is always a valid, stable Windows folder name.

    Windows silently drops trailing spaces and dots from folder names, so a title such as
    "Song - cover " (the file was "Song - cover .mp3") made the later path differ from the
    folder that was created and the conversion could not write its output.
    """
    name = "".join("_" if ch in '<>:"/\\|?*' or ord(ch) < 32 else ch for ch in stem)
    name = name.strip(" .")[:limit].strip(" .")
    if not name or name.split(".")[0].upper() in _RESERVED:
        name = f"song_{name}" if name else "song"
    return name
