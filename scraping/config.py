"""Configuración central del módulo de scraping."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATOS_DIR = BASE_DIR / "datos"          # caché y registros de corridas
DATOS_DIR.mkdir(exist_ok=True)

# ------------------------------------------------------------------ DJANGO
DJANGO_SETTINGS_MODULE = "config.settings"   # AJUSTAR
APP_MODELOS = "MisionaBio"                   # AJUSTAR (app con Fuente, Noticia, Especie, Ubicacion, Registro y ExtraccionIA)

# ------------------------------------------------------------------ DESCUBRIMIENTO
FEEDS = {
    "Misiones Online - fauna": "https://misionesonline.net/etiqueta/fauna/feed/",
    "Misiones Online - fauna silvestre": "https://misionesonline.net/etiqueta/fauna-silvestre/feed/",
    "Misiones Online - fauna misionera": "https://misionesonline.net/etiqueta/fauna-misionera/feed/",
    "Misiones Online - general": "https://misionesonline.net/feed/",
    "Primera Edición - fauna": "https://www.primeraedicion.com.ar/nota/tag/fauna/feed/",
    # Agregá acá los feeds que confirme probar_medios.py (python scraping/probar_medios.py).
}
PAGINAS_POR_FEED = 10

# Sitios WordPress donde se busca por TERMINOS_BUSQUEDA (API wp-json; si falla, RSS de búsqueda).
# La clave es el nombre del medio: así se guarda en la tabla fuente.
SITIOS_WP = {
    "Misiones Online": "https://misionesonline.net",
    "Primera Edición": "https://www.primeraedicion.com.ar",
    "La Voz de Misiones": "https://www.lavozdemisiones.com",
    "Noticias Ambientales": "https://noticiasambientales.com",
}
# Medios que no son solo de Misiones: a cada término se le agrega " Misiones" para no
# traer (y pagar con la IA) notas de otras provincias.
SITIOS_NACIONALES = {"Noticias Ambientales"}
TERMINOS_BUSQUEDA = [
    "yaguareté", "puma", "tapir", "oso hormiguero", "carayá", "tucán",
    "aguará guazú", "yarará", "coatí", "ocelote", "avistamiento fauna",
    "fauna silvestre", "Parque Nacional Iguazú fauna", "Yabotí fauna",
]
PAGINAS_POR_BUSQUEDA = 3
URLS_MANUALES = []

PROCESADAS = DATOS_DIR / "urls_procesadas.txt"   # solo con GUARDAR_EN_BD = False; con BD las URLs vistas salen de la tabla noticia
LOG_CSV = DATOS_DIR / "avistamientos_extraidos.csv"   # auditoría de lo que devolvió la IA

# ------------------------------------------------------------------ DESCARGA
USER_AGENT = "TPI-Biodiversidad-Misiones/0.2 (proyecto educativo; contacto: tu_mail@ejemplo.com)"
PAUSA_SEGUNDOS = 2
TIMEOUT = 15
MAX_NOTAS_POR_CORRIDA = 80

# ------------------------------------------------------------------ IA (Gemini)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
GEMINI_PAUSA = 4              # segundos entre llamadas (cuidar cuota del plan gratuito)
GEMINI_REINTENTOS = 3
PROMPT_VERSION = "v2"         # subir cuando cambie el PROMPT de extraer_ia.py (se guarda en extraccion_ia)
# Valores que puede devolver la IA en tipo_registro (el último es el valor por defecto).
TIPOS_REGISTRO = [
    "avistamiento directo", "rastro", "cámara trampa", "hallazgo", "captura o rescate", "otro",
]
MAX_CHARS_TEXTO = 12000       # se recorta el texto de la nota para limitar tokens
FILTRO_PREVIO = True          # no llama a la IA si la nota no tiene ninguna palabra clave
PALABRAS_CLAVE = [
    "avistamiento", "avistaron", "avistó", "registro", "fauna", "especie", "flora",
    "yaguareté", "yaguarete", "jaguar", "puma", "tapir", "oso hormiguero",
    "carayá", "mono", "tucán", "tucan", "loro", "aguará", "aguara", "tortuga",
    "ocelote", "gato", "yarará", "yarara", "coatí", "coati", "vampiro", "yacaré",
]

# ------------------------------------------------------------------ VALIDACIÓN
# La confianza de cada extracción la calcula el sistema (confianza.py) y NO es la que
# declara el modelo de lenguaje. Cada control vale entre 0 y 1; el resultado es el
# promedio ponderado (los pesos se normalizan solos, no hace falta que sumen 1).
PESOS_CONFIANZA = {
    "cita_en_texto": 0.25,        # la frase que respalda el registro aparece en la nota
    "especie_en_catalogo": 0.0,   # con REQUIERE_ESPECIE_PARA_BD = True siempre vale 1: no informa, por eso pesa 0
    "especie_en_texto": 0.15,     # el nombre común aparece en la nota
    "lugar_en_texto": 0.15,       # el lugar aparece en la nota
    "fecha": 0.15,                # fecha explícita (1), de la publicación (0.4) o la de hoy (0.2)
    "geocodificacion": 0.10,      # diccionario (1) o Nominatim/caché (0.6)
}
CONFIANZA_MINIMA = 0.40   # por debajo de este valor la extracción se descarta (solo queda en el CSV)
# Caja aproximada de Misiones (con margen): lat_min, lat_max, lon_min, lon_max
MISIONES_BBOX = (-28.25, -25.45, -56.15, -53.55)
# Qué fecha usar cuando la IA no encuentra la fecha del avistamiento en la nota:
#   "publicacion_o_hoy": la fecha de publicación de la nota y, si tampoco está, la de hoy (recomendado)
#   "hoy":               siempre la fecha de hoy (día en que corre el scraping)
#   "publicacion":       la de publicación; si no hay, queda vacía
#   "nula":              queda vacía
#   "omitir":            se descarta el avistamiento
FECHA_SI_FALTA = "publicacion_o_hoy"
COORD_DECIMALES = 4

# ------------------------------------------------------------------ BASE DE DATOS
GUARDAR_EN_BD = True                # False: solo escribe el CSV, sin tocar la base
REQUIERE_ESPECIE_PARA_BD = True     # True: solo se registran especies que ya están en el catálogo (tabla especie); el resto se descarta
REQUIERE_UBICACION = False          # False: guarda el registro sin ubicación si no se pudo geocodificar
PRECISION_UBICACION = "APROXIMADA"  # EXACTA / APROXIMADA / OFUSCADA (las coordenadas son aproximadas)
EXTRACTO_CHARS = 500                # caracteres de la nota que se conservan (nunca el texto completo)

# ------------------------------------------------------------------ GEOCODIFICACIÓN
USAR_NOMINATIM = True
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_PAUSA = 1.1            # política de uso: máx. 1 consulta/segundo
GEOCACHE = DATOS_DIR / "geocache.json"

# Los 17 departamentos de Misiones (así se guardan en ubicacion.departamento).
DEPARTAMENTOS = [
    "Apóstoles", "Cainguás", "Candelaria", "Capital", "Concepción", "Eldorado",
    "General Manuel Belgrano", "Guaraní", "Iguazú", "Leandro N. Alem",
    "Libertador General San Martín", "Montecarlo", "Oberá", "San Ignacio",
    "San Javier", "San Pedro", "25 de Mayo",
]

# Lugar -> (lat, lon, departamento). Coordenadas aproximadas (centro de localidad / área
# protegida). Si un lugar no está acá, se consulta Nominatim, que también informa el departamento.
# REVISAR el departamento de los lugares marcados con "?" (áreas que cruzan departamentos).
COORDENADAS = {
    # Capital
    "Posadas": (-27.3671, -55.8961, "Capital"),
    "Garupá": (-27.4833, -55.8333, "Capital"),
    # Candelaria
    "Candelaria": (-27.4631, -55.7436, "Candelaria"),
    "Santa Ana": (-27.3667, -55.5833, "Candelaria"),
    # Apóstoles
    "Apóstoles": (-27.9086, -55.7555, "Apóstoles"),
    # Concepción
    "Concepción de la Sierra": (-27.9833, -55.5167, "Concepción"),
    # San Ignacio
    "San Ignacio": (-27.2569, -55.5339, "San Ignacio"),
    "Jardín América": (-27.0500, -55.2333, "San Ignacio"),
    # San Javier
    "San Javier": (-27.8667, -55.1333, "San Javier"),
    # Oberá
    "Oberá": (-27.4871, -55.1199, "Oberá"),
    # Leandro N. Alem
    "Leandro N. Alem": (-27.6000, -55.3167, "Leandro N. Alem"),
    # Cainguás
    "Aristóbulo del Valle": (-27.0944, -54.8917, "Cainguás"),
    "Campo Grande": (-27.2000, -54.9667, "Cainguás"),
    "Cuña Pirú": (-27.0000, -54.8500, "Cainguás"),                 # ?
    # Libertador General San Martín
    "Puerto Rico": (-26.7961, -55.0247, "Libertador General San Martín"),
    "Capioví": (-26.9167, -55.0500, "Libertador General San Martín"),
    # Montecarlo
    "Montecarlo": (-26.5667, -54.7500, "Montecarlo"),
    "Puerto Piray": (-26.4667, -54.7167, "Montecarlo"),
    # Eldorado
    "Eldorado": (-26.4000, -54.6333, "Eldorado"),
    # San Pedro
    "San Pedro": (-26.6228, -54.1156, "San Pedro"),
    "Yabotí": (-26.8000, -54.1000, "San Pedro"),                   # ? (la reserva cruza departamentos)
    "Reserva de Biosfera Yabotí": (-26.8000, -54.1000, "San Pedro"),  # ?
    # Guaraní
    "El Soberbio": (-27.2978, -54.1936, "Guaraní"),
    "San Vicente": (-26.9958, -54.4872, "Guaraní"),
    "Moconá": (-27.1583, -53.8964, "Guaraní"),
    "Parque Provincial Moconá": (-27.1500, -53.9000, "Guaraní"),
    # General Manuel Belgrano
    "Bernardo de Irigoyen": (-26.2500, -53.6500, "General Manuel Belgrano"),
    "San Antonio": (-26.0500, -53.7333, "General Manuel Belgrano"),
    "Comandante Andresito": (-25.6833, -53.9833, "General Manuel Belgrano"),
    "Andresito": (-25.6833, -53.9833, "General Manuel Belgrano"),
    # Iguazú
    "Puerto Iguazú": (-25.5991, -54.5736, "Iguazú"),
    "Iguazú": (-25.5991, -54.5736, "Iguazú"),
    "Wanda": (-25.9700, -54.5700, "Iguazú"),
    "Libertad": (-25.9000, -54.6000, "Iguazú"),
    "Puerto Esperanza": (-26.0167, -54.6000, "Iguazú"),
    "Parque Nacional Iguazú": (-25.6953, -54.4367, "Iguazú"),
    "Cataratas del Iguazú": (-25.6953, -54.4367, "Iguazú"),
    "Urugua-í": (-25.9000, -54.1500, "Iguazú"),                    # ? (el parque cruza departamentos)
    "Parque Provincial Urugua-í": (-25.9000, -54.1500, "Iguazú"),  # ?
}

# ------------------------------------------------------------------ TAXONOMÍA (respaldo)
# Nombre común (minúsculas, sin tildes) -> nombre científico
ESPECIES = {
    "yaguarete": "Panthera onca", "jaguar": "Panthera onca",
    "puma": "Puma concolor", "tapir": "Tapirus terrestris", "anta": "Tapirus terrestris",
    "oso hormiguero": "Myrmecophaga tridactyla", "caraya": "Alouatta caraya",
    "tucan": "Ramphastos toco", "aguara guazu": "Chrysocyon brachyurus",
    "coati": "Nasua nasua", "ocelote": "Leopardus pardalis",
    "yarara": "Bothrops alternatus", "loro vinoso": "Amazona vinacea",
    "yacare": "Caiman latirostris",
}
