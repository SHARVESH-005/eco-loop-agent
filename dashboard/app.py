"""
Eco-Loop Building Agent — Savings Dashboard
Premium dark-themed Plotly Dash dashboard for visualizing
baseline vs AI-optimized energy performance.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import json

from dash import Dash, html, dcc, callback, Output, Input
import dash_bootstrap_components as dbc

import config

# ─────────────────────────────────────────────
# Load Data
# ─────────────────────────────────────────────
def load_data():
    """Load baseline and optimized CSVs."""
    baseline_path = config.BASELINE_RESULTS_CSV
    optimized_path = config.OPTIMIZED_RESULTS_CSV
    
    baseline = pd.read_csv(baseline_path) if baseline_path.exists() else pd.DataFrame()
    optimized = pd.read_csv(optimized_path) if optimized_path.exists() else pd.DataFrame()
    
    # Load decision log
    decisions = []
    log_path = config.AGENT_DECISIONS_LOG
    if log_path.exists():
        with open(log_path, "r") as f:
            for line in f:
                try:
                    decisions.append(json.loads(line.strip()))
                except:
                    pass
    
    return baseline, optimized, decisions


# ─────────────────────────────────────────────
# Dashboard Theme
# ─────────────────────────────────────────────
COLORS = {
    "bg": "#0a0e1a",
    "card_bg": "#111827",
    "card_border": "#1e293b",
    "accent_green": "#10b981",
    "accent_red": "#ef4444",
    "accent_blue": "#3b82f6",
    "accent_purple": "#8b5cf6",
    "accent_amber": "#f59e0b",
    "accent_cyan": "#06b6d4",
    "text_primary": "#f1f5f9",
    "text_secondary": "#94a3b8",
    "text_muted": "#64748b",
    "grid": "#1e293b",
    "baseline": "#ef4444",
    "optimized": "#10b981",
    "savings_fill": "rgba(16, 185, 129, 0.15)",
}

PLOT_TEMPLATE = {
    "paper_bgcolor": COLORS["card_bg"],
    "plot_bgcolor": COLORS["card_bg"],
    "font": {"color": COLORS["text_primary"], "family": "Inter, sans-serif"},
    "xaxis": {"gridcolor": COLORS["grid"], "zerolinecolor": COLORS["grid"]},
    "yaxis": {"gridcolor": COLORS["grid"], "zerolinecolor": COLORS["grid"]},
    "margin": {"l": 50, "r": 20, "t": 40, "b": 40},
}


# ─────────────────────────────────────────────
# KPI Card Component
# ─────────────────────────────────────────────
def make_kpi_card(title, value, subtitle="", icon="⚡", color=COLORS["accent_green"],
                  is_highlight=False):
    border_style = f"2px solid {color}" if is_highlight else f"1px solid {COLORS['card_border']}"
    glow = f"0 0 20px {color}33" if is_highlight else "none"
    
    return dbc.Col(
        html.Div([
            html.Div(icon, style={
                "fontSize": "28px", "marginBottom": "8px",
            }),
            html.Div(title, style={
                "fontSize": "12px", "fontWeight": "500",
                "color": COLORS["text_secondary"], "textTransform": "uppercase",
                "letterSpacing": "1px", "marginBottom": "4px",
            }),
            html.Div(value, style={
                "fontSize": "32px" if is_highlight else "26px",
                "fontWeight": "700", "color": color,
                "lineHeight": "1.1",
            }),
            html.Div(subtitle, style={
                "fontSize": "12px", "color": COLORS["text_muted"],
                "marginTop": "4px",
            }),
        ], style={
            "background": f"linear-gradient(135deg, {COLORS['card_bg']} 0%, #1a1f35 100%)",
            "border": border_style, "borderRadius": "12px",
            "padding": "20px", "textAlign": "center",
            "boxShadow": glow,
            "transition": "transform 0.2s, box-shadow 0.2s",
        }),
        width=3, className="mb-3",
    )


# ─────────────────────────────────────────────
# Chart Builders
# ─────────────────────────────────────────────
def build_energy_comparison_chart(baseline, optimized):
    """Main energy comparison time series."""
    fig = go.Figure()
    
    # Baseline
    fig.add_trace(go.Scatter(
        x=baseline["hour"], y=baseline["total_energy_kwh"],
        name="Baseline", line=dict(color=COLORS["baseline"], width=2),
        fill=None, mode="lines",
    ))
    
    # Optimized
    fig.add_trace(go.Scatter(
        x=optimized["hour"], y=optimized["total_energy_kwh"],
        name="AI-Optimized", line=dict(color=COLORS["optimized"], width=2.5),
        fill="tonexty", fillcolor=COLORS["savings_fill"],
        mode="lines",
    ))
    
    fig.update_layout(
        **PLOT_TEMPLATE,
        title={"text": "Energy Consumption: Baseline vs AI-Optimized", "x": 0.5,
               "font": {"size": 16}},
        xaxis_title="Simulation Hour",
        yaxis_title="Energy (kWh)",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        height=350,
    )
    
    # Add day/night shading
    for day in range(7):
        # Night (6PM to 7AM next day)
        fig.add_vrect(
            x0=day * 24 + 18, x1=day * 24 + 24,
            fillcolor="rgba(100, 116, 139, 0.05)", layer="below", line_width=0,
        )
        if day < 6:
            fig.add_vrect(
                x0=(day + 1) * 24, x1=(day + 1) * 24 + 7,
                fillcolor="rgba(100, 116, 139, 0.05)", layer="below", line_width=0,
            )
    
    return fig


def build_comfort_chart(baseline, optimized):
    """Zone temperature and PMV chart."""
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        subplot_titles=("Zone Temperatures (°C)", "PMV Comfort Index"),
                        vertical_spacing=0.12)
    
    temp_cols = [c for c in optimized.columns if c.endswith("_temp") and not c.startswith("outdoor")]
    pmv_cols = [c for c in optimized.columns if c.endswith("_pmv")]
    
    zone_colors = [COLORS["accent_blue"], COLORS["accent_purple"],
                   COLORS["accent_amber"], COLORS["accent_cyan"], COLORS["accent_green"]]
    
    for i, (tcol, pcol) in enumerate(zip(temp_cols, pmv_cols)):
        zone_name = tcol.replace("_temp", "").replace("_", " ").title()
        color = zone_colors[i % len(zone_colors)]
        
        fig.add_trace(go.Scatter(
            x=optimized["hour"], y=optimized[tcol],
            name=zone_name, line=dict(color=color, width=1.5),
            legendgroup=zone_name, showlegend=True,
        ), row=1, col=1)
        
        fig.add_trace(go.Scatter(
            x=optimized["hour"], y=optimized[pcol],
            name=zone_name, line=dict(color=color, width=1.5),
            legendgroup=zone_name, showlegend=False,
        ), row=2, col=1)
    
    # Comfort bands
    fig.add_hline(y=config.COMFORT_TEMP_MIN, line_dash="dash",
                  line_color=COLORS["text_muted"], row=1, col=1)
    fig.add_hline(y=config.COMFORT_TEMP_MAX, line_dash="dash",
                  line_color=COLORS["text_muted"], row=1, col=1)
    fig.add_hline(y=config.PMV_TARGET_MIN, line_dash="dash",
                  line_color=COLORS["text_muted"], row=2, col=1)
    fig.add_hline(y=config.PMV_TARGET_MAX, line_dash="dash",
                  line_color=COLORS["text_muted"], row=2, col=1)
    
    fig.update_layout(**PLOT_TEMPLATE, height=450,
                      legend=dict(orientation="h", yanchor="bottom", y=1.05))
    fig.update_xaxes(gridcolor=COLORS["grid"])
    fig.update_yaxes(gridcolor=COLORS["grid"])
    
    return fig


def build_enduse_chart(baseline, optimized):
    """End-use breakdown bar chart."""
    categories = ["HVAC", "Lighting", "Equipment"]
    baseline_vals = [
        baseline["total_hvac_kwh"].sum(),
        baseline["total_lighting_kwh"].sum(),
        baseline["total_equipment_kwh"].sum(),
    ]
    optimized_vals = [
        optimized["total_hvac_kwh"].sum(),
        optimized["total_lighting_kwh"].sum(),
        optimized["total_equipment_kwh"].sum(),
    ]
    
    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Baseline", x=categories, y=baseline_vals,
        marker_color=COLORS["baseline"], marker_line_width=0,
        text=[f"{v:.0f}" for v in baseline_vals], textposition="auto",
    ))
    fig.add_trace(go.Bar(
        name="AI-Optimized", x=categories, y=optimized_vals,
        marker_color=COLORS["optimized"], marker_line_width=0,
        text=[f"{v:.0f}" for v in optimized_vals], textposition="auto",
    ))
    
    fig.update_layout(
        **PLOT_TEMPLATE,
        title={"text": "Energy by End-Use Category", "x": 0.5},
        barmode="group", height=350,
        yaxis_title="Energy (kWh)",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    
    return fig


def build_carbon_chart(baseline, optimized):
    """Carbon emissions comparison."""
    fig = go.Figure()
    
    # Cumulative carbon
    fig.add_trace(go.Scatter(
        x=baseline["hour"],
        y=baseline["carbon_emissions_g"].cumsum() / 1000,
        name="Baseline", line=dict(color=COLORS["baseline"], width=2),
    ))
    fig.add_trace(go.Scatter(
        x=optimized["hour"],
        y=optimized["carbon_emissions_g"].cumsum() / 1000,
        name="AI-Optimized", line=dict(color=COLORS["optimized"], width=2.5),
        fill="tonexty", fillcolor="rgba(16, 185, 129, 0.1)",
    ))
    
    fig.update_layout(
        **PLOT_TEMPLATE,
        title={"text": "Cumulative Carbon Emissions", "x": 0.5},
        xaxis_title="Simulation Hour",
        yaxis_title="CO₂ Emissions (kg)",
        height=300,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    
    return fig


def build_hvac_heatmap(optimized):
    """HVAC energy heatmap by hour and day."""
    if "day" not in optimized.columns:
        return go.Figure()
    
    pivot = optimized.pivot_table(
        values="total_hvac_kwh", index="hour_of_day", columns="day",
        aggfunc="sum"
    )
    
    fig = go.Figure(data=go.Heatmap(
        z=pivot.values,
        x=[f"Day {c}" for c in pivot.columns],
        y=[f"{h:02d}:00" for h in pivot.index],
        colorscale=[[0, "#0a0e1a"], [0.5, "#3b82f6"], [1, "#ef4444"]],
        colorbar=dict(title="kWh"),
    ))
    
    fig.update_layout(
        **PLOT_TEMPLATE,
        title={"text": "HVAC Energy Heatmap (Hour × Day)", "x": 0.5},
        height=350,
    )
    
    return fig


# ─────────────────────────────────────────────
# Decision Log Table
# ─────────────────────────────────────────────
def build_decision_table(decisions):
    """Build the AI decision log table."""
    if not decisions:
        return html.Div("No decisions logged yet.", 
                        style={"color": COLORS["text_muted"], "padding": "20px"})
    
    rows = []
    for d in decisions[-20:]:  # Show last 20
        rows.append(html.Tr([
            html.Td(str(d.get("hour", "N/A")), style={"fontWeight": "600"}),
            html.Td(d.get("reasoning", "")[:80] + "...", 
                    style={"color": COLORS["text_secondary"]}),
            html.Td(d.get("action", ""), 
                    style={"color": COLORS["accent_green"]}),
            html.Td(d.get("expected_impact", ""),
                    style={"color": COLORS["accent_amber"]}),
        ], style={"borderBottom": f"1px solid {COLORS['card_border']}"}))
    
    return html.Table([
        html.Thead(html.Tr([
            html.Th("Hour"), html.Th("Reasoning"),
            html.Th("Action"), html.Th("Expected Impact"),
        ], style={
            "borderBottom": f"2px solid {COLORS['accent_blue']}",
            "color": COLORS["text_secondary"], "textTransform": "uppercase",
            "fontSize": "11px", "letterSpacing": "1px",
        })),
        html.Tbody(rows),
    ], style={
        "width": "100%", "borderCollapse": "collapse",
        "fontSize": "13px",
    })


# ─────────────────────────────────────────────
# App Layout
# ─────────────────────────────────────────────
app = Dash(
    __name__,
    external_stylesheets=[
        dbc.themes.DARKLY,
        "https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap",
    ],
    title="Eco-Loop Agent Dashboard",
)

def serve_layout():
    baseline, optimized, decisions = load_data()
    
    if baseline.empty or optimized.empty:
        return html.Div([
            html.H1("⏳ Waiting for simulation data...",
                    style={"textAlign": "center", "marginTop": "100px",
                           "color": COLORS["text_primary"]}),
            html.P("Run 'python main.py' first to generate baseline and optimized data.",
                   style={"textAlign": "center", "color": COLORS["text_secondary"]}),
            dcc.Interval(id="refresh", interval=5000),
        ], style={"backgroundColor": COLORS["bg"], "minHeight": "100vh"})
    
    # Calculate KPIs
    b_total = baseline["total_energy_kwh"].sum()
    o_total = optimized["total_energy_kwh"].sum()
    savings = b_total - o_total
    savings_pct = (savings / b_total) * 100
    
    b_hvac = baseline["total_hvac_kwh"].sum()
    o_hvac = optimized["total_hvac_kwh"].sum()
    
    b_carbon = baseline["carbon_emissions_g"].sum() / 1000
    o_carbon = optimized["carbon_emissions_g"].sum() / 1000
    
    pmv_cols = [c for c in optimized.columns if c.endswith("_pmv")]
    avg_pmv = optimized[pmv_cols].mean().mean() if pmv_cols else 0
    
    return html.Div([
        # Header
        html.Div([
            html.Div([
                html.H1("🏢 Eco-Loop Agent", style={
                    "fontSize": "28px", "fontWeight": "700",
                    "background": "linear-gradient(135deg, #10b981, #3b82f6)",
                    "WebkitBackgroundClip": "text", "WebkitTextFillColor": "transparent",
                    "margin": "0",
                }),
                html.P("AI-Driven Autonomous Building Energy Optimization Dashboard",
                      style={"fontSize": "14px", "color": COLORS["text_secondary"],
                             "margin": "4px 0 0 0"}),
            ], style={"flex": "1"}),
            html.Div([
                html.Span("● LIVE", style={
                    "color": COLORS["accent_green"], "fontSize": "12px",
                    "fontWeight": "600", "marginRight": "8px",
                }),
                html.Span(f"Gemini 2.0 Flash | {len(decisions)} AI Decisions",
                         style={"color": COLORS["text_muted"], "fontSize": "12px"}),
            ]),
        ], style={
            "display": "flex", "justifyContent": "space-between",
            "alignItems": "center", "padding": "20px 30px",
            "borderBottom": f"1px solid {COLORS['card_border']}",
        }),
        
        # KPI Cards
        dbc.Row([
            make_kpi_card("Total Energy Savings", f"{savings_pct:.1f}%",
                         f"{savings:.0f} kWh saved", "⚡",
                         COLORS["accent_green"], is_highlight=True),
            make_kpi_card("HVAC Reduction", f"{(b_hvac-o_hvac)/max(b_hvac,1)*100:.1f}%",
                         f"{b_hvac-o_hvac:.0f} kWh", "❄️",
                         COLORS["accent_blue"]),
            make_kpi_card("Carbon Saved", f"{b_carbon-o_carbon:.1f} kg",
                         f"CO₂ reduction", "🌿",
                         COLORS["accent_purple"]),
            make_kpi_card("Avg PMV", f"{avg_pmv:.2f}",
                         "Target: -0.5 to +0.5", "🌡️",
                         COLORS["accent_amber"] if abs(avg_pmv) > 0.5 else COLORS["accent_green"]),
        ], style={"padding": "20px 20px 0"}),
        
        # Main Charts Row
        dbc.Row([
            dbc.Col([
                dcc.Graph(figure=build_energy_comparison_chart(baseline, optimized),
                         config={"displayModeBar": False}),
            ], width=8),
            dbc.Col([
                dcc.Graph(figure=build_enduse_chart(baseline, optimized),
                         config={"displayModeBar": False}),
            ], width=4),
        ], style={"padding": "10px 20px"}),
        
        # Comfort Chart
        dbc.Row([
            dbc.Col([
                dcc.Graph(figure=build_comfort_chart(baseline, optimized),
                         config={"displayModeBar": False}),
            ], width=12),
        ], style={"padding": "10px 20px"}),
        
        # Bottom Row
        dbc.Row([
            dbc.Col([
                dcc.Graph(figure=build_carbon_chart(baseline, optimized),
                         config={"displayModeBar": False}),
            ], width=6),
            dbc.Col([
                dcc.Graph(figure=build_hvac_heatmap(optimized),
                         config={"displayModeBar": False}),
            ], width=6),
        ], style={"padding": "10px 20px"}),
        
        # Decision Log
        html.Div([
            html.H3("🤖 AI Agent Decision Log", style={
                "fontSize": "18px", "fontWeight": "600",
                "color": COLORS["text_primary"], "marginBottom": "12px",
            }),
            html.Div(
                build_decision_table(decisions),
                style={
                    "maxHeight": "400px", "overflowY": "auto",
                    "padding": "10px",
                }
            ),
        ], style={
            "margin": "10px 20px 20px",
            "background": COLORS["card_bg"],
            "border": f"1px solid {COLORS['card_border']}",
            "borderRadius": "12px", "padding": "20px",
        }),
        
        # Footer
        html.Div([
            html.P("Eco-Loop Building Agent | Honeywell OA Hackathon 2026 | "
                   "Powered by EnergyPlus Simulation + Google Gemini + MCP Protocol",
                  style={"fontSize": "12px", "color": COLORS["text_muted"],
                         "textAlign": "center", "margin": "0"}),
        ], style={"padding": "15px", "borderTop": f"1px solid {COLORS['card_border']}"}),
        
    ], style={
        "backgroundColor": COLORS["bg"],
        "minHeight": "100vh",
        "fontFamily": "'Inter', sans-serif",
        "color": COLORS["text_primary"],
    })


app.layout = serve_layout


# ─────────────────────────────────────────────
# Run Server
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("\n[Eco-Loop Dashboard] Starting server...")
    print("   Open http://127.0.0.1:8050 in your browser\n")
    app.run(debug=False, port=8050)
