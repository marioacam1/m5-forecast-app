"""Cliente HTTP del endpoint de pronóstico de demanda (Databricks Model Serving).

Qué hace este módulo:
- envía filas al endpoint con tiempo de espera y reintentos con espera creciente;
- clasifica los errores (entrada inválida, autenticación, servicio no disponible...);
- comprueba que la respuesta tenga el formato esperado antes de usarla.

No contiene ningún modelo: todo pronóstico sale de una consulta HTTP al endpoint publicado.
"""
import math
import time

import requests

# Variables que recibe el modelo, con el tipo que exige la firma del endpoint
CAT_COLS = ["cat_id", "dept_id"]
FLOAT_COLS = ["lag_28", "lag_35", "lag_42", "roll7_l28", "roll28_l28", "sell_price", "price_ratio"]
INT_COLS = ["snap", "is_event", "wday", "month"]
FEATURES = CAT_COLS + FLOAT_COLS + INT_COLS

# Respuestas que justifican reintentar (el servicio puede estar iniciándose o saturado)
RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}

# (segundos para conectar, segundos esperando la respuesta)
DEFAULT_TIMEOUT = (5, 45)
DEFAULT_ATTEMPTS = 4
MAX_BACKOFF_SECONDS = 20


class ApiError(Exception):
    """Error base al consultar el endpoint."""

    user_message = "No se pudo completar la consulta al modelo."

    def __init__(self, detail="", status=None):
        super().__init__(detail)
        self.detail = detail
        self.status = status


class InvalidInput(ApiError):
    user_message = "El servicio rechazó los datos de entrada. Corrige los valores y vuelve a intentar."


class AuthError(ApiError):
    user_message = "No se pudo autenticar con el modelo: el token es inválido o venció."


class NotFound(ApiError):
    user_message = "No se encontró el servicio del modelo. Revisa el nombre del endpoint."


class Unavailable(ApiError):
    user_message = (
        "El modelo no respondió a tiempo. Puede estar iniciándose: espera un minuto y vuelve a intentar."
    )


class InvalidResponse(ApiError):
    user_message = "El servicio respondió con un formato que la página no puede usar."


class ConfigError(ApiError):
    user_message = "Falta configurar la conexión con el modelo."


def to_record(row):
    """Convierte una fila (dict o Series) en el cuerpo que espera el endpoint, con los tipos correctos."""
    record = {c: str(row[c]) for c in CAT_COLS}
    record.update({c: float(row[c]) for c in FLOAT_COLS})
    record.update({c: int(row[c]) for c in INT_COLS})
    return record


def _error_message(response):
    """Extrae el mensaje de error de la respuesta, tenga o no formato JSON."""
    try:
        body = response.json()
        if isinstance(body, dict):
            return str(body.get("message") or body.get("error") or response.text[:300])
    except ValueError:
        pass
    return response.text[:300].strip() or "sin detalle"


def classify_error(response):
    """Traduce un código de estado distinto de 200 en el error correspondiente."""
    status = response.status_code
    message = _error_message(response)
    if status == 400:
        return InvalidInput(message, status)
    if status in (401, 403):
        return AuthError(message, status)
    if status == 404:
        return NotFound(message, status)
    if status in RETRY_STATUS:
        return Unavailable(f"El servicio devolvió HTTP {status}: {message}", status)
    return ApiError(f"HTTP {status}: {message}", status)


def parse_predictions(response, expected_count):
    """Comprueba que la respuesta 200 tenga `predictions` válido y devuelve la lista de números."""
    try:
        predictions = response.json()["predictions"]
    except (ValueError, KeyError, TypeError):
        raise InvalidResponse("La respuesta no es JSON o no incluye 'predictions'.", response.status_code)

    if not isinstance(predictions, list) or len(predictions) != expected_count:
        raise InvalidResponse(
            f"Se esperaban {expected_count} predicciones y llegó: {str(predictions)[:120]}",
            response.status_code,
        )

    clean = []
    for value in predictions:
        valid = isinstance(value, (int, float)) and not isinstance(value, bool)
        if not valid or not math.isfinite(value) or value < 0:
            raise InvalidResponse(f"Predicción no válida: {str(value)[:60]}", response.status_code)
        clean.append(float(value))
    return clean


def query_endpoint(records, host, token, endpoint, max_attempts=DEFAULT_ATTEMPTS,
                   timeout=DEFAULT_TIMEOUT, sleep=time.sleep):
    """Consulta el endpoint y devuelve (predicciones, info).

    Reintenta con espera creciente (3, 6, 12 s...) solo cuando tiene sentido: tiempo agotado,
    error de conexión, 429 y 5xx. Los errores 400, 401, 403 y 404 no se reintentan.
    """
    if not host.startswith("https://") or not token or not endpoint:
        raise ConfigError("Faltan DATABRICKS_HOST (con https://), DATABRICKS_TOKEN o ENDPOINT_NAME.")
    if not records:
        raise InvalidInput("No hay filas para consultar.")

    url = f"{host.rstrip('/')}/serving-endpoints/{endpoint}/invocations"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"dataframe_records": records}

    started = time.perf_counter()
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=timeout)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            last_error = Unavailable(f"{type(exc).__name__}: el servicio no respondió.")
        except requests.exceptions.RequestException as exc:
            raise ApiError(f"{type(exc).__name__}: {str(exc)[:200]}")
        else:
            if response.status_code == 200:
                predictions = parse_predictions(response, len(records))
                info = {"attempts": attempt, "seconds": time.perf_counter() - started}
                return predictions, info
            error = classify_error(response)
            if not isinstance(error, Unavailable):
                raise error
            last_error = error

        if attempt < max_attempts:
            sleep(min(3 * 2 ** (attempt - 1), MAX_BACKOFF_SECONDS))

    last_error.detail = f"{last_error.detail} (tras {max_attempts} intentos)"
    raise last_error
