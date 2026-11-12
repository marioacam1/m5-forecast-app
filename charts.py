"""Gráficas de ventas reales contra pronóstico del modelo.

Todas las gráficas reciben el mismo DataFrame que antes: las fechas en el índice y una columna por serie
("Ventas reales" y "Pronóstico del modelo"). Sin líneas horizontales; los filtros interactivos
(series, rango de fechas y tipo de día) se aplican antes de dibujar, con `filter_days`.
"""
import altair as alt
import pandas as pd

REAL = "Ventas reales"
FORECAST = "Pronóstico del modelo"
SERIES = [REAL, FORECAST]

MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
DAY_TYPES = ["Todos", "Entre semana", "Fin de semana", "Con SNAP", "Con evento"]
GRID = "#8A94A3"  # gris medio: se ve bien en tema claro y oscuro

PALETTES = {
    "lines": {REAL: "#2F6DB5", FORECAST: "#F28C28"},
    "bars": {REAL: "#7C5CBF", FORECAST: "#27A596"},
    "gap": {REAL: "#1E96A8", FORECAST: "#E8B02E"},
}


def day_label(timestamp):
    """25 de abril de 2016 -> '25 abr' (meses en español)."""
    return f"{timestamp.day} {MESES[timestamp.month - 1]}"


def to_long(df):
    """Pasa del formato ancho (fechas en el índice) al formato largo y devuelve el orden de los días."""
    wide = df.copy()
    wide.index = pd.to_datetime(wide.index)
    wide = wide.rename_axis("fecha").reset_index()
    wide["dia"] = wide["fecha"].map(day_label)
    long = wide.melt(id_vars=["fecha", "dia"], var_name="serie", value_name="unidades")
    return long, wide["dia"].tolist()


def filter_days(data, start, end, day_type):
    """Filtra los días de `data` (con columnas date, wday, snap e is_event).

    En M5, wday 1 es sábado y 2 es domingo.
    """
    dates = pd.to_datetime(data["date"]).dt.date
    mask = (dates >= start) & (dates <= end)
    weekend = data["wday"].isin([1, 2])
    if day_type == "Entre semana":
        mask &= ~weekend
    elif day_type == "Fin de semana":
        mask &= weekend
    elif day_type == "Con SNAP":
        mask &= data["snap"] == 1
    elif day_type == "Con evento":
        mask &= data["is_event"] == 1
    return mask


def _color(palette, series):
    present = [s for s in SERIES if s in series]
    return alt.Color(
        "serie:N",
        scale=alt.Scale(domain=present, range=[palette[s] for s in present]),
        legend=alt.Legend(title=None, orient="top", symbolSize=140),
    )


def _x(order, grid):
    axis = alt.Axis(
        title=None, labelAngle=-45, grid=grid, gridColor=GRID, gridOpacity=0.35, gridDash=[2, 3],
        domainColor=GRID, tickColor=GRID,
    )
    return alt.X("dia:O", sort=order, axis=axis)


def _y(field="unidades"):
    # Sin líneas horizontales: solo el eje con sus marcas. Todas las capas usan el mismo eje y título.
    axis = alt.Axis(title="Unidades vendidas", grid=False, domainColor=GRID, tickColor=GRID)
    return alt.Y(field=field, type="quantitative", axis=axis)


def _finish(chart):
    return (
        chart.properties(width="container", height=380)
        .configure_view(stroke=None)
        .configure_axis(labelFontSize=12, titleFontSize=12)
    )


def _tooltips(series):
    tips = [alt.Tooltip("dia:O", title="Día")]
    tips += [alt.Tooltip(field=s, type="quantitative", format=".2f") for s in series]
    return tips


def line_chart(df):
    """Opción 1: líneas con marcas, cuadrícula solo vertical y regla vertical al pasar el cursor."""
    long, order = to_long(df)
    series = [s for s in SERIES if s in long["serie"].unique()]
    palette = PALETTES["lines"]
    color = _color(palette, series)
    legend_pick = alt.selection_point(fields=["serie"], bind="legend")
    hover = alt.selection_point(
        name="hover", fields=["dia"], nearest=True, on="mouseover", clear="mouseout", empty=False
    )
    opacity = alt.condition(legend_pick, alt.value(1), alt.value(0.2))
    dash = alt.StrokeDash(
        "serie:N", scale=alt.Scale(domain=[REAL, FORECAST], range=[[1, 0], [6, 4]]), legend=None
    )

    base = alt.Chart().encode(x=_x(order, grid=True), y=_y())
    lines = base.mark_line(strokeWidth=2.6).encode(color=color, strokeDash=dash, opacity=opacity)
    points = base.mark_point(filled=True, size=55).encode(color=color, opacity=opacity)
    rule = (
        alt.Chart()
        .transform_pivot("serie", value="unidades", groupby=["dia"])
        .mark_rule(color=GRID, strokeWidth=1.5)
        .encode(
            x=_x(order, grid=True),
            opacity=alt.condition(hover, alt.value(0.9), alt.value(0)),
            tooltip=_tooltips(series),
        )
        .add_params(hover)
    )
    layered = alt.layer(lines, points, rule, data=long).add_params(legend_pick)
    return _finish(layered)


def bars_chart(df):
    """Opción 2: columnas verticales agrupadas por día, sin cuadrícula."""
    long, order = to_long(df)
    series = [s for s in SERIES if s in long["serie"].unique()]
    palette = PALETTES["bars"]
    legend_pick = alt.selection_point(fields=["serie"], bind="legend")
    chart = (
        alt.Chart(long)
        .mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
        .encode(
            x=_x(order, grid=False),
            xOffset=alt.XOffset("serie:N"),
            y=_y(),
            color=_color(palette, series),
            opacity=alt.condition(legend_pick, alt.value(1), alt.value(0.25)),
            tooltip=[
                alt.Tooltip("dia:O", title="Día"),
                alt.Tooltip("serie:N", title="Serie"),
                alt.Tooltip("unidades:Q", title="Unidades", format=".2f"),
            ],
        )
        .add_params(legend_pick)
    )
    return _finish(chart)


def gap_chart(df):
    """Opción 3: un conector vertical por día entre las ventas reales y el pronóstico."""
    long, order = to_long(df)
    series = [s for s in SERIES if s in long["serie"].unique()]
    palette = PALETTES["gap"]
    color = _color(palette, series)
    legend_pick = alt.selection_point(fields=["serie"], bind="legend")

    shape = alt.Shape(
        "serie:N",
        scale=alt.Scale(domain=[REAL, FORECAST], range=["circle", "diamond"]),
        legend=alt.Legend(title=None, orient="top", symbolSize=140),
    )
    points = (
        alt.Chart()
        .mark_point(filled=True, size=120, opacity=1)
        .encode(
            x=_x(order, grid=False),
            y=_y(),
            color=color,
            shape=shape,
            opacity=alt.condition(legend_pick, alt.value(1), alt.value(0.2)),
            tooltip=[
                alt.Tooltip("dia:O", title="Día"),
                alt.Tooltip("serie:N", title="Serie"),
                alt.Tooltip("unidades:Q", title="Unidades", format=".2f"),
            ],
        )
        .add_params(legend_pick)
    )
    if len(series) < 2:  # con una sola serie no hay diferencia que conectar
        return _finish(alt.layer(points, data=long))

    connector = (
        alt.Chart()
        .transform_pivot("serie", value="unidades", groupby=["dia"])
        .transform_calculate(diferencia=f"datum['{FORECAST}'] - datum['{REAL}']")
        .mark_rule(color=GRID, strokeWidth=3, opacity=0.55)
        .encode(
            x=_x(order, grid=False),
            y=_y(REAL),
            y2=alt.Y2(field=FORECAST),
            tooltip=[
                alt.Tooltip("dia:O", title="Día"),
                alt.Tooltip(field=REAL, type="quantitative", format=".2f"),
                alt.Tooltip(field=FORECAST, type="quantitative", format=".2f"),
                alt.Tooltip(field="diferencia", type="quantitative", format="+.2f", title="Pronóstico − real"),
            ],
        )
    )
    return _finish(alt.layer(connector, points, data=long))


CHART_STYLES = {
    "Líneas con cuadrícula vertical": line_chart,
    "Columnas verticales": bars_chart,
    "Diferencia diaria": gap_chart,
}
