"""Extracción de avistamientos con Gemini (SDK `google-genai`)."""
import json
import re
import time
import traceback
from importlib import import_module

from . import config
from .normalizar import normalizar_texto

# HistorialIA: la "caja negra" de cada llamada a la IA (éxito o error). Se importa
# así, en vez de con un import directo, para no acoplar este módulo al nombre de la
# app Django (config.APP_MODELOS), igual que hace guardar.py.
_models = import_module(f"{config.APP_MODELOS}.models")
HistorialIA = _models.HistorialIA

PROMPT = """Del siguiente artículo periodístico, extraé los avistamientos de
especies de fauna o flora ocurridos en Misiones, Argentina.

Devolvé un JSON con una clave "avistamientos" que contenga una lista. Cada elemento con:
- nombre_comun
- nombre_cientifico (null si no lo sabés con certeza)
- lugar_texto (tal como aparece en la nota; el lugar más específico posible)
- fecha_avistamiento (YYYY-MM-DD o null si no se menciona)
- tipo_registro (uno de: {tipos})
- cita (frase copiada exactamente de la nota que lo respalda, máx. 25 palabras)

Reglas:
- Si la nota no describe ningún avistamiento, devolvé una lista vacía.
- No inventes datos que no estén en el texto.
- Solo avistamientos ocurridos en Misiones; ignorá otras provincias o países.
- Fechas relativas ("ayer", "el sábado") resolvelas con la fecha de publicación; si no podés con certeza, null.
- Ignorá animales domésticos y menciones generales (por ejemplo "el yaguareté está en peligro de extinción") que no sean un encuentro o registro concreto.
- Un elemento por especie y lugar; no repitas.
- La cita debe ser una copia literal del texto, sin parafrasear ni corregir.

Título: {titulo}
Fecha de publicación: {fecha_pub}
Texto: {texto}"""

_client = None


def _cliente():
    global _client
    if _client is None:
        if not config.GEMINI_API_KEY:
            raise RuntimeError("Falta la variable de entorno GEMINI_API_KEY")
        from google import genai
        _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client


def parece_relevante(titulo: str, texto: str) -> bool:
    """Filtro barato previo para no gastar llamadas a la IA en notas sin relación."""
    t = normalizar_texto(titulo + " " + texto)
    return any(normalizar_texto(p) in t for p in config.PALABRAS_CLAVE)


def _parsear_json(texto: str) -> list:
    texto = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto.strip())
    data = json.loads(texto)
    if isinstance(data, dict):
        data = data.get("avistamientos", [])
    return [a for a in data if isinstance(a, dict)] if isinstance(data, list) else []


def _registrar_historial(noticia, modelo: str, prompt_enviado: str, respuesta_cruda: str,
                          exito: bool, detalle_error: str = None):
    """Guarda en HistorialIA la caja negra de UNA llamada a la IA (un intento).

    Si no hay `noticia` (por ejemplo con config.GUARDAR_EN_BD = False, donde no se
    crea Noticia en la base) no hay a qué vincular el historial, así que no se
    guarda nada; el resto del flujo sigue igual. Un error al guardar el log tampoco
    debe interrumpir el scraping: se informa por consola y se continúa.
    """
    if noticia is None:
        return
    try:
        HistorialIA.objects.create(
            noticia=noticia,
            modelo=modelo[:100],
            prompt_enviado=prompt_enviado,
            respuesta_cruda=respuesta_cruda or "",
            exito=exito,
            detalle_error=detalle_error,
        )
    except Exception as e:
        print(f"  [HistorialIA] no se pudo guardar el log: {type(e).__name__}: {e}")


def extraer_avistamientos(noticia, titulo: str, fecha_pub: str, texto: str):
    """Devuelve una lista de avistamientos (puede ser vacía) o None si la IA falló.
    None permite reintentar la nota en otra corrida.

    Cada llamada a la API (incluidos los reintentos) queda auditada en HistorialIA,
    vinculada a `noticia`: el prompt enviado, la respuesta cruda tal como llegó y si
    se pudo parsear como JSON válido. Así queda trazabilidad aunque la IA falle,
    se caiga la API, haya timeout o "alucine" una respuesta que no es JSON.
    """
    prompt = PROMPT.format(
        titulo=titulo, fecha_pub=fecha_pub or "desconocida",
        texto=texto[: config.MAX_CHARS_TEXTO], tipos=", ".join(config.TIPOS_REGISTRO),
    )
    for intento in range(1, config.GEMINI_REINTENTOS + 1):
        respuesta_cruda = ""
        try:
            resp = _cliente().models.generate_content(
                model=config.GEMINI_MODEL,
                contents=prompt,
                config={"response_mime_type": "application/json", "temperature": 0},
            )
            respuesta_cruda = resp.text or ""
            avistamientos = _parsear_json(respuesta_cruda)  # puede lanzar JSONDecodeError
            time.sleep(config.GEMINI_PAUSA)
            _registrar_historial(noticia, config.GEMINI_MODEL, prompt, respuesta_cruda, exito=True)
            return avistamientos
        except json.JSONDecodeError as e:
            print(f"  [IA] JSON inválido (intento {intento}): {e}")
            _registrar_historial(
                noticia, config.GEMINI_MODEL, prompt, respuesta_cruda,
                exito=False, detalle_error=f"JSONDecodeError: {e}",
            )
        except Exception as e:  # cuota, red, timeout, API caída, etc.
            print(f"  [IA] error (intento {intento}): {e}")
            _registrar_historial(
                noticia, config.GEMINI_MODEL, prompt, respuesta_cruda,
                exito=False, detalle_error=traceback.format_exc(),
            )
        time.sleep(5 * 2 ** (intento - 1))
    return None