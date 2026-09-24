"""
Busqueda de precios de mercado en internet via Gemini (Google Search grounding)
================================================================================

4a fuente del comparador: cuando queremos saber si un precio esta "en mercado"
mas alla de NL/CDMX/historico interno, le pedimos a Gemini que BUSQUE en
internet (no que invente de memoria) cotizaciones o precios publicados de ese
concepto en Nuevo Leon / Mexico, y que cite la fuente real que encontro.

Como no se inventa nada:
  - Se usa la herramienta de "google_search" (grounding) de la API de Gemini.
    Gemini solo puede citar paginas que de verdad encontro en la busqueda; no
    se le pide "cual crees que es el precio", se le pide "busca y reporta lo
    que encuentres, con la fuente".
  - Si no encuentra nada confiable, debe regresar precio_encontrado=false en
    vez de adivinar.

Como se ahorran tokens/llamadas (importante, la cuota gratis es limitada):
  1. Cache en memoria por (concepto normalizado + unidad) via st.cache_data,
     con TTL largo (30 dias por default) - el mismo concepto no se vuelve a
     buscar mientras el cache siga vigente, sin importar cuantas cotizaciones
     se suban.
  2. Deduplicacion dentro de una misma cotizacion: si el mismo concepto+unidad
     aparece repetido en el archivo, solo se busca una vez.
  3. Limite configurable de busquedas por sesion (se corta si se llega al
     limite, para no vaciar la cuota diaria sin darte cuenta).
  4. Modelo "flash" (el mas barato/rapido de Gemini) y respuesta corta
     (maxOutputTokens bajo) en vez del modelo "pro".

Requiere una API key gratuita de Gemini (Google AI Studio ->
https://aistudio.google.com/apikey). No requiere ninguna libreria adicional,
usa la API REST directo con "requests" (ya viene con Python/Streamlit).
"""

import json
import re
import requests

MODELO = "gemini-2.5-flash-lite"  # el modelo mas barato/rapido de Gemini que soporta busqueda
URL_API = f"https://generativelanguage.googleapis.com/v1beta/models/{MODELO}:generateContent"

PROMPT_TEMPLATE = """Eres un asistente que SOLO reporta precios que encuentres realmente \
publicados en internet, nunca inventas ni estimas de memoria.

Busca en internet el precio de mercado actual en México (idealmente en Nuevo León) para este \
concepto de construcción/obra:

Concepto: {concepto}
Unidad: {unidad}

Instrucciones:
- Si encuentras un precio real publicado (proveedor, ferretería, portal de precios de \
  construcción, tabulador oficial, cotización pública, etc.), repórtalo.
- Si NO encuentras nada confiable, responde precio_encontrado=false. NO inventes ni estimes \
  un número "típico" de tu conocimiento general.
- Responde ÚNICAMENTE con un JSON válido, sin texto adicional, con este formato exacto:

{{"precio_encontrado": true o false, "precio_mxn": número o null, "unidad_encontrada": "texto o null", "fuente_nombre": "texto o null", "fuente_url": "texto o null", "nota": "texto breve"}}
"""


def _extraer_json(texto: str):
    texto = texto.strip()
    texto = re.sub(r'^```json\s*|\s*```$', '', texto.strip(), flags=re.MULTILINE)
    match = re.search(r'\{.*\}', texto, flags=re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def buscar_precio_mercado(concepto: str, unidad: str, api_key: str, timeout: int = 20) -> dict:
    """Llama a Gemini (con Google Search grounding) UNA vez para buscar un
    precio real de este concepto. No hace cache aqui (eso se hace afuera, en
    la app, con st.cache_data) para que este modulo sea facil de probar."""
    if not api_key:
        return {'precio_encontrado': False, 'motivo': 'Sin API key de Gemini configurada.'}

    prompt = PROMPT_TEMPLATE.format(concepto=concepto, unidad=unidad)
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0.0, "maxOutputTokens": 400},
    }

    try:
        resp = requests.post(
            URL_API, params={"key": api_key}, json=body, timeout=timeout,
            headers={"Content-Type": "application/json"},
        )
    except requests.RequestException as e:
        return {'precio_encontrado': False, 'motivo': f'Error de conexion con Gemini: {e}'}

    if resp.status_code != 200:
        return {'precio_encontrado': False, 'motivo': f'Gemini respondio error {resp.status_code}: {resp.text[:200]}'}

    data = resp.json()
    try:
        candidato = data['candidates'][0]
        texto = ''.join(p.get('text', '') for p in candidato['content']['parts'])
    except (KeyError, IndexError):
        return {'precio_encontrado': False, 'motivo': 'Respuesta de Gemini sin contenido utilizable.'}

    parsed = _extraer_json(texto)
    if parsed is None:
        return {'precio_encontrado': False, 'motivo': 'No se pudo interpretar la respuesta de Gemini.'}

    # Fuentes reales que Gemini consulto durante el grounding (si vienen)
    fuentes_grounding = []
    try:
        chunks = candidato.get('groundingMetadata', {}).get('groundingChunks', [])
        for ch in chunks:
            uri = ch.get('web', {}).get('uri')
            if uri:
                fuentes_grounding.append(uri)
    except Exception:
        pass

    resultado = {
        'precio_encontrado': bool(parsed.get('precio_encontrado')),
        'precio_mxn': parsed.get('precio_mxn'),
        'unidad_encontrada': parsed.get('unidad_encontrada'),
        'fuente_nombre': parsed.get('fuente_nombre'),
        'fuente_url': parsed.get('fuente_url') or (fuentes_grounding[0] if fuentes_grounding else None),
        'fuentes_consultadas': fuentes_grounding,
        'nota': parsed.get('nota'),
    }
    return resultado
