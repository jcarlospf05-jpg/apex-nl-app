"""
Busqueda de precios de mercado en internet con IA (4a fuente: "IA busca cotizaciones")
=========================================================================================
A diferencia de revision_ia.py (que solo JUZGA matches ya encontrados contra
las bases de NL/CDMX/historico, o da una opinion generica basada en el
conocimiento del modelo), este modulo hace una BUSQUEDA REAL en internet con
Gemini usando "grounding" (Google Search integrado a la API) para encontrar
precios de mercado actuales para cada partida, con la fuente citada.

Esto SOLO funciona con Gemini (el "grounding" con Google Search es una
capacidad especifica de la API de Gemini; OpenAI no tiene un equivalente
directo compatible con el mismo flujo), y SOLO si hay 'gemini_api_key'
configurada en Secrets -- si no, todas las funciones regresan {} sin tronar
la app, igual que revision_ia.py.

Nunca se inventa un precio: si el modelo no encuentra una fuente real con
Google Search, la partida se marca como 'sin_dato' en vez de rellenarse con
un numero estimado de memoria.

Para no gastar la cuota de la API rapido (la preocupacion explicita de
direccion), se agrupan varias partidas por llamada (BATCH, igual que
revision_ia.TAMANO_LOTE) y el resultado se cachea en la sesion de Streamlit
por texto normalizado de la partida: si la misma cotizacion (u otra parecida)
vuelve a traer el mismo concepto, no se vuelve a consultar a la IA.
"""
import json
import os

MODELO_POR_DEFECTO = "gemini-2.5-flash"

# Menos partidas por lote que revision_ia.TAMANO_LOTE (8): cada partida aqui
# implica que el modelo dispare una o mas busquedas reales en Google, asi
# que el prompt y la respuesta esperada son mas pesados por partida.
TAMANO_LOTE = 5


def _leer_secret(nombre):
    try:
        import streamlit as st

        if nombre in st.secrets:
            return st.secrets[nombre]
    except Exception:
        pass
    return None


_cliente_cache = {"cliente": None, "intentado": False}


def _obtener_cliente(api_key=None):
    usar_cache = api_key is None
    if usar_cache and _cliente_cache["intentado"]:
        return _cliente_cache["cliente"]

    cliente = None
    key = api_key or os.environ.get("GEMINI_API_KEY") or _leer_secret("gemini_api_key")
    if key:
        try:
            from google import genai

            cliente = genai.Client(api_key=key)
        except Exception:
            cliente = None

    if usar_cache:
        _cliente_cache["intentado"] = True
        _cliente_cache["cliente"] = cliente

    return cliente


def busqueda_disponible(api_key=None) -> bool:
    return _obtener_cliente(api_key) is not None


def _extraer_json(texto):
    if not texto:
        return None
    inicio = texto.find("{")
    fin = texto.rfind("}")
    if inicio == -1 or fin == -1 or fin < inicio:
        return None
    try:
        return json.loads(texto[inicio:fin + 1])
    except (json.JSONDecodeError, ValueError):
        return None


def _fuentes_de_respuesta(respuesta):
    """Extrae las URLs reales que Google Search consulto (grounding
    metadata) para poder citarlas -- nunca se muestra un precio de
    'busqueda de IA' sin poder decir de donde salio."""
    urls = []
    try:
        candidatos = respuesta.candidates or []
        for candidato in candidatos:
            metadata = getattr(candidato, "grounding_metadata", None)
            if not metadata:
                continue
            for chunk in getattr(metadata, "grounding_chunks", None) or []:
                web = getattr(chunk, "web", None)
                if web and getattr(web, "uri", None):
                    urls.append(
                        {"titulo": getattr(web, "title", None) or web.uri, "url": web.uri}
                    )
    except Exception:
        pass
    return urls


def buscar_precios_mercado_lote(items, api_key=None, modelo=None):
    """
    items: lista de dicts {id, descripcion, unidad}

    Busca en internet (Google Search real, via grounding de Gemini) un
    precio de mercado actual en Mexico/Nuevo Leon para cada partida.

    Regresa dict {id: {'precio_mxn': float|None, 'unidad_encontrada': str,
    'fuente_nombre': str, 'fuente_url': str, 'nota': str,
    'tiene_dato': bool}} -- solo incluye los ids que el modelo devolvio.
    Si la busqueda no esta disponible o falla, regresa {}.
    """
    cliente = _obtener_cliente(api_key)
    if not cliente or not items:
        return {}

    modelo = modelo or MODELO_POR_DEFECTO

    lineas = []
    for it in items:
        lineas.append(f'ID {it["id"]}: "{it["descripcion"]}" (unidad: {it["unidad"]})')

    prompt = (
        "Busca en internet (usa la busqueda de Google) precios de mercado "
        "ACTUALES en Mexico (idealmente Nuevo Leon/Monterrey; si no hay, "
        "usa el precio nacional promedio) para cada uno de estos materiales "
        "o servicios de construccion/obra/instalaciones:\n\n"
        + "\n".join(lineas) +
        "\n\nPara CADA partida, busca de verdad (no inventes ni calcules de "
        "memoria) y responde SOLO con un JSON (sin texto alrededor):\n"
        '{"resultados": [{"id": <mismo id>, "precio_mxn": <numero o null '
        'si no encontraste nada confiable>, "unidad_encontrada": "<unidad '
        'del precio que encontraste>", "fuente_nombre": "<nombre del sitio '
        'o proveedor>", "fuente_url": "<url real de donde salio, o vacio '
        'si no aplica>", "nota": "<1 frase en espanol, ej. rango de precios '
        'o contexto>"}, ...]}\n\n'
        "Si no encuentras un precio real y verificable para una partida, "
        "pon precio_mxn en null -- NUNCA inventes un numero."
    )

    try:
        respuesta = cliente.models.generate_content(
            model=modelo,
            contents=prompt,
            config={"tools": [{"google_search": {}}]},
        )
    except Exception:
        return {}

    texto = getattr(respuesta, "text", None)
    datos = _extraer_json(texto)
    if not datos or "resultados" not in datos or not isinstance(datos["resultados"], list):
        return {}

    fuentes_citadas = _fuentes_de_respuesta(respuesta)
    url_generica = fuentes_citadas[0]["url"] if fuentes_citadas else ""

    salida = {}
    for r in datos["resultados"]:
        if not isinstance(r, dict) or "id" not in r:
            continue
        id_ = r["id"]
        precio = r.get("precio_mxn")
        try:
            precio = float(precio) if precio is not None else None
        except (TypeError, ValueError):
            precio = None
        salida[id_] = {
            "precio_mxn": precio,
            "unidad_encontrada": str(r.get("unidad_encontrada", "") or ""),
            "fuente_nombre": str(r.get("fuente_nombre", "") or ""),
            "fuente_url": str(r.get("fuente_url", "") or "") or url_generica,
            "nota": str(r.get("nota", "") or ""),
            "tiene_dato": precio is not None,
        }
    return salida
