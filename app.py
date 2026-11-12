"""Página de consulta del modelo de pronóstico de demanda (M5 Walmart, tienda CA_1).

La página no contiene ningún modelo: cada pronóstico sale de una consulta HTTP al endpoint
publicado en Databricks Model Serving (ver client.py).
"""
import os
import time
from pathlib import Path

import pandas as pd
import streamlit as st

import client

st.set_page_config(page_title="Pronóstico de demanda M5", page_icon="📈", layout="wide")

DEPTS = {
    "FOODS": ["FOODS_1", "FOODS_2", "FOODS_3"],
    "HOBBIES": ["HOBBIES_1", "HOBBIES_2"],
    "HOUSEHOLD": ["HOUSEHOLD_1", "HOUSEHOLD_2"],
}
COOLDOWN_SECONDS = 2


def get_config(key, default=""):
    """Lee la configuración de los secretos de Streamlit o, si no hay, de variables de entorno."""
    try:
        value = st.secrets[key]
    except Exception:
        value = os.environ.get(key, default)
    return str(value).strip()


HOST = get_config("DATABRICKS_HOST")
TOKEN = get_config("DATABRICKS_TOKEN")
ENDPOINT = get_config("ENDPOINT_NAME", "m5-demand-forecast")


@st.cache_data
def load_sample():
    return pd.read_csv(Path(__file__).parent / "sample_test_rows.csv")


def cooldown_ok():
    """Evita consultas seguidas (doble clic) para no saturar el endpoint."""
    now = time.time()
    if now - st.session_state.get("last_call", 0) < COOLDOWN_SECONDS:
        st.warning("Espera un par de segundos antes de consultar de nuevo.")
        return False
    st.session_state["last_call"] = now
    return True


def show_error(err):
    detail = err.detail + (f" (HTTP {err.status})" if err.status else "")
    message = f"{err.user_message}\n\nDetalle técnico: {detail}"
    if isinstance(err, client.Unavailable):
        st.warning(message)
    else:
        st.error(message)


def run_query(records):
    """Consulta el endpoint; devuelve (predicciones, info) o None si hubo un error ya mostrado."""
    if not cooldown_ok():
        return None
    try:
        with st.spinner("Consultando el modelo. Si estaba inactivo, la primera consulta puede tardar uno o dos minutos."):
            return client.query_endpoint(records, HOST, TOKEN, ENDPOINT)
    except client.ApiError as err:
        show_error(err)
        return None


try:
    sample = load_sample()
except FileNotFoundError:
    st.error("No se encontró sample_test_rows.csv junto a app.py.")
    st.stop()

# ---------------------------------------------------------------- barra lateral
with st.sidebar:
    st.header("Modelo publicado")
    st.write(f"Endpoint: `{ENDPOINT}`")
    st.write("Servicio: Databricks Model Serving")
    st.write("Tarea: pronosticar las ventas diarias de un producto de la tienda CA_1.")
    if HOST and TOKEN and st.button("Probar conexión con el modelo"):
        result = run_query([client.to_record(sample.iloc[0])])
        if result:
            _, info = result
            st.success(f"Conexión correcta: {info['seconds']:.2f} s, {info['attempts']} intento(s).")

st.title("Pronóstico de demanda diaria")
st.write(
    "Elige un producto de la tienda CA_1 y consulta al modelo cuántas unidades venderá. "
    "El pronóstico lo calcula el endpoint de Databricks, no esta página."
)

if not HOST or not TOKEN:
    st.error(
        "Falta configurar la conexión. Agrega DATABRICKS_HOST y DATABRICKS_TOKEN en los secretos de la app."
    )
    st.stop()

tab_real, tab_manual, tab_about = st.tabs(["Ejemplo real", "Entrada manual", "Acerca del proyecto"])

# ---------------------------------------------------------------- ejemplo real
with tab_real:
    col_item, col_day = st.columns(2)
    item = col_item.selectbox("Producto", sorted(sample["item_id"].unique()))
    product = sample[sample["item_id"] == item].sort_values("day_num").reset_index(drop=True)
    day = col_day.selectbox("Día del periodo de prueba", product["date"].tolist(), index=len(product) - 1)
    row = product[product["date"] == day].iloc[0]

    st.caption("Variables que recibe el modelo para ese día")
    st.dataframe(product.loc[product["date"] == day, client.FEATURES], hide_index=True)

    if st.button("Pronosticar este día"):
        result = run_query([client.to_record(row)])
        if result:
            predictions, info = result
            real = int(row["ventas_reales"])
            m1, m2, m3 = st.columns(3)
            m1.metric("Pronóstico del modelo", f"{predictions[0]:.2f} unidades")
            m2.metric("Ventas reales", f"{real} unidades")
            m3.metric("Diferencia", f"{predictions[0] - real:+.2f}")
            st.caption(f"Respuesta en {info['seconds']:.2f} s, {info['attempts']} intento(s).")

    if st.button("Pronosticar los 28 días de este producto"):
        records = [client.to_record(r) for _, r in product.iterrows()]
        result = run_query(records)
        if result:
            predictions, info = result
            chart = pd.DataFrame(
                {"Ventas reales": product["ventas_reales"].values, "Pronóstico del modelo": predictions},
                index=pd.to_datetime(product["date"]),
            )
            st.line_chart(chart)
            mae = (chart["Ventas reales"] - chart["Pronóstico del modelo"]).abs().mean()
            st.caption(
                f"Error absoluto medio de este producto: {mae:.2f} unidades al día. "
                f"Una sola petición de {len(records)} filas, {info['seconds']:.2f} s, {info['attempts']} intento(s)."
            )

# ---------------------------------------------------------------- entrada manual
with tab_manual:
    st.write("Escribe tus propios valores. Activa el modo de prueba para ver cómo responde el servicio a datos inválidos.")
    relaxed = st.checkbox(
        "Modo de prueba: permitir valores fuera de rango",
        help="Quita los límites de los campos para que puedas enviar datos inválidos y ver el error del servicio.",
    )

    def number(label, value, low, high, step):
        limits = {} if relaxed else {"min_value": low, "max_value": high}
        return st.number_input(label, value=value, step=step, **limits)

    c1, c2 = st.columns(2)
    cat_id = c1.selectbox("Categoría", list(DEPTS))
    dept_options = [d for group in DEPTS.values() for d in group] if relaxed else DEPTS[cat_id]
    dept_id = c2.selectbox("Departamento", dept_options)

    values = {
        "cat_id": cat_id,
        "dept_id": dept_id,
        "lag_28": number("Ventas de hace 28 días", 3.0, 0.0, 1000.0, 0.5),
        "lag_35": number("Ventas de hace 35 días", 2.0, 0.0, 1000.0, 0.5),
        "lag_42": number("Ventas de hace 42 días", 4.0, 0.0, 1000.0, 0.5),
        "roll7_l28": number("Promedio de 7 días (hasta hace 28)", 2.5, 0.0, 1000.0, 0.1),
        "roll28_l28": number("Promedio de 28 días (hasta hace 28)", 2.8, 0.0, 1000.0, 0.1),
        "sell_price": number("Precio de venta", 2.98, 0.01, 1000.0, 0.1),
        "price_ratio": number("Precio frente a su promedio reciente", 1.0, 0.01, 10.0, 0.05),
        "snap": number("SNAP activo (0 o 1)", 0, 0, 1, 1),
        "is_event": number("Hay evento (0 o 1)", 0, 0, 1, 1),
        "wday": number("Día de la semana (1 a 7)", 3, 1, 7, 1),
        "month": number("Mes (1 a 12)", 5, 1, 12, 1),
    }
    record = client.to_record(values)

    with st.expander("Petición que se enviará"):
        st.json({"dataframe_records": [record]})

    if st.button("Consultar el modelo"):
        result = run_query([record])
        if result:
            predictions, info = result
            st.metric("Pronóstico del modelo", f"{predictions[0]:.2f} unidades")
            st.caption(f"Respuesta en {info['seconds']:.2f} s, {info['attempts']} intento(s).")

# ---------------------------------------------------------------- acerca del proyecto
with tab_about:
    st.subheader("Qué es esto")
    st.write(
        "Pronóstico de ventas diarias con los datos de la competencia M5 de Walmart. "
        "El pipeline Medallion (Bronce, Plata y Oro) y el entrenamiento corren en Databricks; "
        "el modelo se publica como endpoint y esta página lo consulta por HTTP."
    )
    st.write(
        "Los datos de ejemplo son 100 productos de la tienda CA_1, del 25 de abril al 22 de mayo de 2016 "
        "(28 días que el modelo no vio al entrenar)."
    )
    st.subheader("Calidad del modelo")
    st.write(
        "En ese periodo, el error cuadrático medio (RMSE) del modelo fue 2.31 unidades por producto y día. "
        "La mejor referencia simple, el promedio de 7 días, tuvo 2.50."
    )
    st.subheader("Reglas de validación del servicio")
    st.table(
        pd.DataFrame(
            {
                "Variable": [
                    "Ventas rezagadas y promedios", "Precio de venta", "Relación de precio",
                    "SNAP y evento", "Día de la semana", "Mes", "Categoría y departamento", "Filas por petición",
                ],
                "Regla": [
                    "de 0 a 1000", "de 0.01 a 1000", "de 0.01 a 10", "0 o 1", "de 1 a 7", "de 1 a 12",
                    "el departamento debe pertenecer a la categoría", "de 1 a 1000",
                ],
            }
        )
    )
    st.subheader("Cómo maneja la página los errores")
    st.table(
        pd.DataFrame(
            {
                "Respuesta del servicio": [
                    "400", "401 o 403", "404", "Tiempo agotado, 429 o 5xx", "200 con formato inválido",
                ],
                "Qué hace la página": [
                    "Muestra el motivo del rechazo y no reintenta",
                    "Avisa que el token es inválido o venció; no reintenta",
                    "Avisa que no encontró el endpoint; no reintenta",
                    "Reintenta hasta 4 veces con espera creciente y avisa que el modelo puede estar iniciándose",
                    "Descarta la respuesta y lo informa",
                ],
            }
        )
    )
