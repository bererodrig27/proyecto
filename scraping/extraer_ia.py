"""Extracción de avistamientos con Gemini (SDK `google-genai`)."""
import json
import re
import time

from . import config
from .normalizar import normalizar_texto

PROMPT = """Del siguiente artículo periodístico, extraé los avistamientos de
especies de fauna o flora ocurridos en Misiones, Argentina.

Devolvé un JSON con una clave "avistamientos" que contenga una lista. Cada elemento con:
- nombre_comun
- nombre_cientifico (null si no lo sabés con certeza)
- lugar_texto (tal como aparece en la nota; el lugar más específico posible)
- fecha_avistamiento (YYYY-MM-DD o null si no se menciona)
- cita (frase textual de la nota que lo respalda, máx. 25 palabras)
- confianza (0 a 1)

Reglas:
- Si la nota no describe ningún avistamiento, devolvé una lista vacía.
- No inventes datos que no estén en el texto.
- Solo avistamientos ocurridos en Misiones; ignorá otras provincias o países.
- Fechas relativas ("ayer", "el sábado") resolvelas con la fecha de publicación; si no podés con certeza, null.
- Ignorá animales domésticos y menciones generales (por ejemplo "el yaguareté está en peligro de extinción") que no sean un encuentro o registro concreto.
- Un elemento por especie y lugar; no repitas.

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


def extraer_avistamientos(titulo: str, fecha_pub: str, texto: str):
    """Devuelve una lista de avistamientos (puede ser vacía) o None si la IA falló.
    None permite reintentar la nota en otra corrida."""
    prompt = PROMPT.format(
        titulo=titulo, fecha_pub=fecha_pub or "desconocida",
        texto=texto[: config.MAX_CHARS_TEXTO],
    )
    for intento in range(1, config.GEMINI_REINTENTOS + 1):
        try:
            resp = _cliente().models.generate_content(
                model=config.GEMINI_MODEL,
                contents=prompt,
                config={"response_mime_type": "application/json", "temperature": 0},
            )
            time.sleep(config.GEMINI_PAUSA)
            return _parsear_json(resp.text or "")
        except json.JSONDecodeError as e:
            print(f"  [IA] JSON inválido (intento {intento}): {e}")
        except Exception as e:  # cuota, red, etc.
            print(f"  [IA] error (intento {intento}): {e}")
        time.sleep(5 * 2 ** (intento - 1))
    return None