"""Build Plotly charts for valuation history."""
from __future__ import annotations

import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots

# ── colour palette ──────────────────────────────────────────────────────────
BG_COLOR = "#0d1117"
PAPER_COLOR = "#161b22"
GRID_COLOR = "#21262d"
TEXT_COLOR = "#c9d1d9"
ACCENT_COLORS = [
    "#58a6ff",  # blue
    "#3fb950",  # green
    "#f78166",  # red
    "#d2a8ff",  # purple
    "#ffa657",  # orange
]
MEAN_COLOR = "#e3b341"
BAND_COLOR = "#8b949e"
CHEAP_COLOR = "#3fb950"
EXPENSIVE_COLOR = "#f78166"
NEUTRAL_COLOR = "#58a6ff"


def _zscore_color(zscore: float) -> str:
    if zscore < -1.0:
        return CHEAP_COLOR
    if zscore > 1.0:
        return EXPENSIVE_COLOR
    return NEUTRAL_COLOR


def _band_traces(stats: dict, x_range: list[str]) -> list[go.Scatter]:
    """Return horizontal band line traces."""
    traces = []
    bands = [
        ("plus2", "+2σ", "dash"),
        ("plus1", "+1σ", "dot"),
        ("mean", "Mean", "solid"),
        ("minus1", "−1σ", "dot"),
        ("minus2", "−2σ", "dash"),
    ]
    for key, name, dash in bands:
        val = stats[key]
        color = MEAN_COLOR if key == "mean" else BAND_COLOR
        width = 1.5 if key == "mean" else 1.0
        traces.append(
            go.Scatter(
                x=x_range,
                y=[val, val],
                mode="lines",
                line=dict(color=color, dash=dash, width=width),
                name=name,
                showlegend=False,
                hovertemplate=f"{name}: {val:.2f}x<extra></extra>",
            )
        )
    return traces


def build_single_chart(data: dict, height: int = 520) -> str:
    """Return a Plotly chart JSON string for a single ticker."""
    dates = data["dates"]
    values = data["values"]
    stats = data["stats"]
    current = data["current"]
    zscore = data["zscore"]
    label = data["label"]
    symbol = data["symbol"]

    cur_color = _zscore_color(zscore)

    fig = go.Figure()

    # ── band fills ──────────────────────────────────────────────────────────
    fig.add_trace(
        go.Scatter(
            x=dates + dates[::-1],
            y=[stats["plus2"]] * len(dates) + [stats["plus1"]] * len(dates),
            fill="toself",
            fillcolor="rgba(247,129,102,0.06)",
            line=dict(width=0),
            showlegend=False,
            hoverinfo="skip",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=dates + dates[::-1],
            y=[stats["minus1"]] * len(dates) + [stats["minus2"]] * len(dates),
            fill="toself",
            fillcolor="rgba(63,185,80,0.06)",
            line=dict(width=0),
            showlegend=False,
            hoverinfo="skip",
        )
    )

    # ── horizontal band lines ───────────────────────────────────────────────
    for trace in _band_traces(stats, [dates[0], dates[-1]]):
        fig.add_trace(trace)

    # ── main valuation line ─────────────────────────────────────────────────
    fig.add_trace(
        go.Scatter(
            x=dates,
            y=values,
            mode="lines",
            line=dict(color=ACCENT_COLORS[0], width=2),
            name=f"{symbol} {label}",
            hovertemplate="%{x}<br>" + label + ": %{y:.2f}x<extra></extra>",
        )
    )

    # ── current value marker ────────────────────────────────────────────────
    fig.add_trace(
        go.Scatter(
            x=[dates[-1]],
            y=[current],
            mode="markers+text",
            marker=dict(color=cur_color, size=10, symbol="circle"),
            text=[f"  {current:.2f}x"],
            textposition="middle right",
            textfont=dict(color=cur_color, size=12),
            name="Current",
            hovertemplate=f"Current: {current:.2f}x (z={zscore:+.2f})<extra></extra>",
            showlegend=False,
        )
    )

    # ── right-side annotations ──────────────────────────────────────────────
    band_labels = [
        ("plus2", f"+2σ  {stats['plus2']:.1f}x"),
        ("plus1", f"+1σ  {stats['plus1']:.1f}x"),
        ("mean", f"Mean  {stats['mean']:.1f}x"),
        ("minus1", f"−1σ  {stats['minus1']:.1f}x"),
        ("minus2", f"−2σ  {stats['minus2']:.1f}x"),
    ]
    for key, text in band_labels:
        val = stats[key]
        color = MEAN_COLOR if key == "mean" else BAND_COLOR
        fig.add_annotation(
            x=1.01,
            y=val,
            xref="paper",
            yref="y",
            text=text,
            showarrow=False,
            font=dict(size=10, color=color),
            xanchor="left",
        )

    # ── layout ──────────────────────────────────────────────────────────────
    fig.update_layout(
        height=height,
        paper_bgcolor=PAPER_COLOR,
        plot_bgcolor=BG_COLOR,
        font=dict(color=TEXT_COLOR, family="Inter, sans-serif"),
        title=dict(
            text=f"<b>{symbol}</b> — {label}",
            font=dict(size=16, color=TEXT_COLOR),
            x=0.02,
        ),
        xaxis=dict(
            showgrid=False,
            zeroline=False,
            color=TEXT_COLOR,
            linecolor=GRID_COLOR,
            tickfont=dict(size=11),
        ),
        yaxis=dict(
            showgrid=True,
            gridcolor=GRID_COLOR,
            zeroline=False,
            color=TEXT_COLOR,
            linecolor=GRID_COLOR,
            tickformat=".1f",
            ticksuffix="x",
            tickfont=dict(size=11),
        ),
        margin=dict(l=60, r=120, t=60, b=50),
        hovermode="x unified",
        legend=dict(
            bgcolor="rgba(0,0,0,0)",
            font=dict(size=11),
        ),
    )

    return fig.to_json()


def build_compare_charts(data_list: list[dict], height_each: int = 400) -> str:
    """Return a Plotly JSON with one subplot per ticker, stacked vertically."""
    n = len(data_list)
    if n == 0:
        return go.Figure().to_json()

    fig = make_subplots(
        rows=n,
        cols=1,
        shared_xaxes=False,
        vertical_spacing=0.06,
        subplot_titles=[f"{d['symbol']} — {d['label']}" for d in data_list],
    )

    for i, data in enumerate(data_list, start=1):
        dates = data["dates"]
        values = data["values"]
        stats = data["stats"]
        current = data["current"]
        zscore = data["zscore"]
        color = ACCENT_COLORS[(i - 1) % len(ACCENT_COLORS)]
        cur_color = _zscore_color(zscore)

        # Main line
        fig.add_trace(
            go.Scatter(
                x=dates,
                y=values,
                mode="lines",
                line=dict(color=color, width=1.8),
                name=data["symbol"],
                hovertemplate="%{x}<br>" + data["label"] + ": %{y:.2f}x<extra></extra>",
            ),
            row=i,
            col=1,
        )

        # Band lines
        for key, dash, bcolor, width in [
            ("plus2", "dash", BAND_COLOR, 1.0),
            ("plus1", "dot", BAND_COLOR, 1.0),
            ("mean", "solid", MEAN_COLOR, 1.5),
            ("minus1", "dot", BAND_COLOR, 1.0),
            ("minus2", "dash", BAND_COLOR, 1.0),
        ]:
            val = stats[key]
            fig.add_trace(
                go.Scatter(
                    x=[dates[0], dates[-1]],
                    y=[val, val],
                    mode="lines",
                    line=dict(color=bcolor, dash=dash, width=width),
                    showlegend=False,
                    hoverinfo="skip",
                ),
                row=i,
                col=1,
            )

        # Current marker
        fig.add_trace(
            go.Scatter(
                x=[dates[-1]],
                y=[current],
                mode="markers",
                marker=dict(color=cur_color, size=8),
                showlegend=False,
                hovertemplate=f"Current: {current:.2f}x (z={zscore:+.2f})<extra></extra>",
            ),
            row=i,
            col=1,
        )

    total_height = height_each * n
    fig.update_layout(
        height=total_height,
        paper_bgcolor=PAPER_COLOR,
        plot_bgcolor=BG_COLOR,
        font=dict(color=TEXT_COLOR, family="Inter, sans-serif"),
        showlegend=True,
        margin=dict(l=60, r=60, t=40, b=40),
        hovermode="x",
    )
    for ax in fig.layout:
        if ax.startswith("xaxis") or ax.startswith("yaxis"):
            fig.layout[ax].update(
                showgrid=True,
                gridcolor=GRID_COLOR,
                zeroline=False,
                color=TEXT_COLOR,
                linecolor=GRID_COLOR,
            )

    return fig.to_json()
