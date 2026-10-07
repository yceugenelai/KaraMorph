"""Whole-song routing and coarse CPU estimates from the local CPU report.

Pure planning helpers also run in the isolated style worker; Qt is imported
only for the confirmation dialog. GiB values use binary units.
"""
from app.i18n import t
import math


def execution_plan(hardware, duration, count):
    duration = float(duration)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Invalid style input duration")
    if not 1 <= int(count) <= 8:
        raise ValueError("Invalid candidate count")
    limit = float(hardware.get("gpu_max_seconds") or 0)
    gpu = bool(hardware.get("cuda")) and duration <= limit
    # 60s CPU report: 229s generation, ~15 GiB RSS, eight threads.
    # Extrapolation includes headroom; long songs have not been benchmarked.
    seconds = 45 + 4.0 * duration * int(count)
    ram = math.ceil(16 + max(0, duration - 60) * 0.012)
    return {"device": "cuda" if gpu else "cpu", "duration_sec": duration,
            "count": int(count), "hardware": dict(hardware),
            "cpu_minutes_low": max(1, math.ceil(seconds / 60)),
            "cpu_minutes_high": max(2, math.ceil(seconds * 3 / 60)),
            "cpu_ram_gib": ram,
            "generation_timeout": max(1800, math.ceil((45 + 4 * duration) * 6))}


def confirm_execution(parent, plan):
    if plan["device"] == "cuda":
        return True
    from PySide6.QtWidgets import QMessageBox
    hw = plan["hardware"]
    if hw.get("cuda"):
        reason = (t('歌曲長度 {p0:.1f} 秒超過目前顯卡的估計處理上限 {p1:.0f} 秒。\n{p2}，VRAM 約 {p3:.1f} GiB；預估 VRAM 不足，將改用 CPU。', p0=plan['duration_sec'], p1=hw['gpu_max_seconds'], p2=hw.get('gpu_name', 'NVIDIA GPU'), p3=hw['vram_gib']))
    else:
        reason = t('未偵測到可用的 NVIDIA CUDA GPU，將使用 CPU。')
    text = (t('{p0}\n\n這次製作 {p1} 個版本，CPU 處理可能需要較長時間。\n粗估約 {p2}–{p3} 分鐘，程式 RAM 用量約 {p4} GiB，另需保留 Windows 與其他程式的記憶體。', p0=reason, p1=plan['count'], p2=plan['cpu_minutes_low'], p3=plan['cpu_minutes_high'], p4=plan['cpu_ram_gib']))
    if hw.get("ram_available_gib") is not None:
        text += t('\n目前可用 RAM：約 {p0:.1f} GiB。', p0=hw['ram_available_gib'])
        if hw["ram_available_gib"] < plan["cpu_ram_gib"] + 2:
            text += t('\n可用 RAM 可能不足，可能大量使用磁碟換頁或執行失敗。')
    text += (t('\n\n以上依 60 秒片段／Ryzen 7 4800H 的測試粗估，整首歌曲尚未驗證；實際需求可能更高。\n按「OK」開始 CPU 處理，或取消這首的風格轉換。'))
    return QMessageBox.question(parent, t('改用 CPU 製作風格'), text,
                                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                                QMessageBox.StandardButton.Cancel) == QMessageBox.StandardButton.Ok
