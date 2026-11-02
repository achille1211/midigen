"""Piano-roll PNG preview (optional, needs matplotlib)."""
from __future__ import annotations

from pathlib import Path

from .arranger import Performance, render

COLORS = {"drums": "#555555", "bass": "#1f77b4", "chords": "#2ca02c", "pad": "#98df8a", "arp": "#ff7f0e",
          "lead": "#d62728", "other": "#9467bd"}


def piano_roll(spec_or_perf, path: str | Path, title: str | None = None) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    perf = spec_or_perf if isinstance(spec_or_perf, Performance) else render(spec_or_perf)
    tracks = [t for t in perf.tracks if t.notes]
    fig, axes = plt.subplots(len(tracks), 1, figsize=(14, 1.6 * len(tracks) + 1), sharex=True, squeeze=False)
    bb = perf.bar_beats
    for ax, t in zip(axes[:, 0], tracks):
        col = COLORS.get(t.role, COLORS["other"])
        lo = min(n.pitch for n in t.notes)
        hi = max(n.pitch for n in t.notes)
        for n in t.notes:
            ax.add_patch(Rectangle((n.start / bb, n.pitch - 0.4), max(n.dur / bb, 0.02), 0.8,
                                   color=col, alpha=0.35 + 0.65 * n.vel / 127, linewidth=0))
        ax.set_ylim(lo - 2, hi + 2)
        ax.set_ylabel(f"{t.name}\n({len(t.notes)})", rotation=0, ha="right", va="center", fontsize=8)
        ax.tick_params(axis="y", labelsize=6)
        for s in perf.sections:
            ax.axvline(s.start / bb, color="k", lw=0.6, alpha=0.5)
        ax.grid(axis="x", alpha=0.15)
    top = axes[0, 0]
    for s in perf.sections:
        top.text(s.start / bb + 0.1, 1.02, f"{s.name} ({s.energy:.1f})", transform=top.get_xaxis_transform(), fontsize=8)
    axes[-1, 0].set_xlim(0, perf.total_beats / bb)
    axes[-1, 0].set_xlabel("bar")
    sp = perf.spec
    fig.suptitle(title or f"{sp.get('title', '')} — {sp.get('genre', '')} {sp['key']} {sp['mode']} {sp['tempo']} BPM", y=0.995)
    fig.tight_layout()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return path
