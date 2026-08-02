"""
Revision asistida con IA - capa OPCIONAL de segunda opinion
======================================================================
Este modulo NO reemplaza las fuentes de precio reales (NL, CDMX,
historico interno) -- esas siguen siendo la unica fuente de verdad
para el precio de referencia. Lo que hace esta capa es ayudar a
JUZGAR los casos que el comparador de texto (rapidfuzz) no puede
resolver bien por si solo:

  1. Coincidencias "BAJA confianza": el texto se parece pero no esta
     claro si es el mismo material o una coincidencia de palabras
     genericas (ej. "TUBERIA DE 6 DE DIAMETRO" pegandole por
     casualidad a varias medidas distintas de aislamiento). Aqui la
     IA revisa el candidato y dice si de verdad es el mismo concepto
     o no, con una razon breve.

  2. Partidas SIN NINGUN dato de referencia (ningun match en NL,
     CDMX ni historico interno): la IA puede dar una opinion
     orientativa de si el precio cotizado suena razonable para ese
     tipo de material/equipo, basada en su conocimiento general --
     pero esto NUNCA se presenta como un precio de mercado
     verificado, siempre queda marcado como "opinion de IA, no dato
     verificado".

SOBRE EL PROVEEDOR DE IA: en Ragasa el uso de Claude (Anthropic) esta
bloqueado por politica de la empresa, y todavia no esta confirmado si
se puede usar OpenAI/ChatGPT. Para no depender de una sola respuesta
que despues cambie, este modulo detecta AUTOMATICAMENTE cual proveedor
tiene una API key configurada y usa ese -- funciona igual sin importar
cual termine aprobado:

  - Gemini (Google):  Secrets -> gemini_api_key  (o env GEMINI_API_KEY)
  - OpenAI (ChatGPT):  Secrets -> openai_api_key  (o env OPENAI_API_KEY)

Si hay las dos, se prefiere Gemini. Si no hay ninguna, o falta
instalar el paquete correspondiente (`google-genai` / `openai`), o la
llamada falla por cualquier motivo (sin internet, rate limit, etc.),
todas las funciones de este modulo regresan None sin tronar la app --
exactamente el mismo patron que ya usan ajuste_inflacion.py (token de
INEGI) y historico_google_sheets.py (credenciales de Google): la app
sigue funcionando normal, solo sin esta capa extra.
"""
import json
import os

MODELO_GEMINI_POR_DEFECTO = "gemini-3.5-flash-lite"
MODELO_OPENAI_POR_DEFECTO = "gpt-5-mini"

_proveedor_cache = {"proveedor": None, "cliente": None, "intentado": False}


def _leer_secret(nombre):
    try:
        import streamlit as st

        if nombre in st.secrets:
            return st.secrets[nombre]
    except Exception:
        pass
    return None


def _obtener_cliente(api_key=None, proveedor_forzado=None):
    """Detecta que proveedor de IA tiene una key configurada (Gemini
    primero, luego OpenAI) y regresa (proveedor, cliente). Si no hay
    ninguna key o falta el paquete correspondiente, regresa
    (None, None). Nunca lanza excepcion."""
    usar_cache = api_key is None and proveedor_forzado is None
    if usar_cache and _proveedor_cache["intentado"]:
        return _proveedor_cache["proveedor"], _proveedor_cache["cliente"]

    proveedor, cliente = None, None

    if proveedor_forzado in (None, "gemini"):
        key = api_key or os.environ.get("GEMINI_API_KEY") or _leer_secret("gemini_api_key")
        if key:
            try:
                from google import genai

                cliente = genai.Client(api_key=key)
                proveedor = "gemini"
            except Exception:
                cliente = None

    if cliente is None and proveedor_forzado in (None, "openai"):
        key = api_key or os.environ.get("OPENAI_API_KEY") or _leer_secret("openai_api_key")
        if key:
            try:
                import openai

                cliente = openai.OpenAI(api_key=key)
                proveedor = "openai"
            except Exception:
                cliente = None

    if usar_cache:
        _proveedor_cache["intentado"] = True
        _proveedor_cache["proveedor"] = proveedor
        _proveedor_cache["cliente"] = cliente

    return proveedor, cliente


def ia_disponible(api_key=None) -> bool:
    proveedor, cliente = _obtener_cliente(api_key)
    return cliente is not None


def _llamar_gemini(cliente, prompt, modelo, max_tokens):
    try:
        respuesta = cliente.models.generate_content(
            model=modelo,
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "max_output_tokens": max_tokens,
            },
        )
        return respuesta.text
    except Exception:
        pass

    try:
        respuesta = cliente.models.generate_content(
            model=modelo,
            contents=prompt,
        )
        return respuesta.text
    except Exception:
        return None


def _llamar_openai(cliente, prompt, modelo, max_tokens):
    try:
        respuesta = cliente.chat.completions.create(
            model=modelo,
            max_completion_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        return respuesta.choices[0].message.content
    except Exception:
        pass

    try:
        respuesta = cliente.chat.completions.create(
            model=modelo,
            max_completion_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return respuesta.choices[0].message.content
    except Exception:
        return None


def _llamar_ia(proveedor, cliente, prompt, modelo=None, max_tokens=250):
    if proveedor == "gemini":
        modelo = modelo or MODELO_GEMINI_POR_DEFECTO
        return _llamar_gemini(cliente, prompt, modelo, max_tokens), modelo
    if proveedor == "openai":
        modelo = modelo or MODELO_OPENAI_POR_DEFECTO
        return _llamar_openai(cliente, prompt, modelo, max_tokens), modelo
    return None, modelo


def _extraer_json(texto):
    """La respuesta puede traer texto alrededor del JSON aunque se le
    pida limpio; esto saca el primer bloque {...} valido."""
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


def revisar_coincidencia_debil(
    descripcion_cotizada: str,
    unidad: str,
    descripcion_candidato: str,
    fuente: str,
    api_key=None,
    modelo=None,
):
    """
    Para un match ya encontrado con confianza BAJA: le pide a la IA
    que confirme o rechace si de verdad es el mismo material/concepto
    (no solo texto parecido).

    Regresa dict {'veredicto': 'CONFIRMA'|'RECHAZA'|'NO_SEGURO',
    'razon': str, 'modelo': str}, o None si la IA no esta disponible
    o la llamada fallo -- en ese caso quien llama debe seguir
    tratando el match como BAJA sin cambiar nada.
    """
    proveedor, cliente = _obtener_cliente(api_key)
    if cliente is None:
        return None

    prompt = (
        "Eres un experto en materiales y precios de construccion/obra "
        "en Mexico (civil, electrico, mecanico/HVAC, plomeria, "
        "acabados).\n\n"
        "Un sistema de busqueda por texto encontro esta posible "
        "coincidencia, pero con confianza BAJA (el texto se parece "
        "pero no es seguro que sea el mismo material). Tu trabajo es "
        "decidir si de verdad es el MISMO concepto/material, no solo "
        "texto parecido.\n\n"
        f'Partida cotizada: "{descripcion_cotizada}" (unidad: {unidad})\n'
        f'Candidato encontrado en la base de {fuente}: '
        f'"{descripcion_candidato}"\n\n'
        "Responde SOLO con un JSON (sin texto alrededor) con este "
        "formato exacto:\n"
        '{"veredicto": "CONFIRMA" o "RECHAZA" o "NO_SEGURO", '
        '"razon": "explicacion breve en espanol, maximo 25 palabras"}\n\n'
        "CONFIRMA solo si estas genuinamente seguro de que es el "
        "mismo material/servicio (aunque este redactado distinto). "
        "RECHAZA si son materiales o servicios distintos aunque "
        "compartan palabras o medidas sueltas. NO_SEGURO si de "
        "verdad no se puede saber con la informacion dada."
    )

    texto, modelo_usado = _llamar_ia(proveedor, cliente, prompt, modelo=modelo, max_tokens=200)
    datos = _extraer_json(texto)
    if not datos or "veredicto" not in datos:
        return None

    veredicto = str(datos.get("veredicto", "")).strip().upper()
    if veredicto not in {"CONFIRMA", "RECHAZA", "NO_SEGURO"}:
        veredicto = "NO_SEGURO"

    return {
        "veredicto": veredicto,
        "razon": str(datos.get("razon", "")).strip(),
        "modelo": f"{proveedor}:{modelo_usado}",
    }


def opinar_sin_datos(
    descripcion_cotizada: str,
    unidad: str,
    precio_cotizado: float,
    api_key=None,
    modelo=None,
):
    """
    Para una partida SIN NINGUN match en ninguna fuente: pide una
    opinion orientativa (NO un precio de mercado verificado) sobre si
    el precio cotizado suena razonable, basada en el conocimiento
    general del modelo.

    Regresa dict {'opinion': 'RAZONABLE'|'ALTO'|'BAJO'|
    'NO_PUEDO_OPINAR', 'razon': str, 'modelo': str}, o None si la IA
    no esta disponible o la llamada fallo.
    """
    proveedor, cliente = _obtener_cliente(api_key)
    if cliente is None:
        return None

    prompt = (
        "Eres un experto en materiales, equipo y precios de "
        "construccion/obra/instalaciones en Mexico (civil, "
        "electrico, mecanico/HVAC, plomeria, acabados), con "
        "conocimiento de precios de mercado aproximados en pesos "
        "mexicanos (MXN) a mediados de 2026.\n\n"
        "Esta partida NO tiene ninguna referencia en las bases de "
        "precios internas de la empresa (Nuevo Leon, CDMX, historico "
        "interno) -- por eso se te pide tu opinion general, sabiendo "
        "que NO es un dato verificado.\n\n"
        f'Partida: "{descripcion_cotizada}" (unidad: {unidad})\n'
        f"Precio unitario cotizado: ${precio_cotizado:,.2f} MXN\n\n"
        "Responde SOLO con un JSON (sin texto alrededor):\n"
        '{"opinion": "RAZONABLE" o "ALTO" o "BAJO" o '
        '"NO_PUEDO_OPINAR", "razon": "explicacion breve en espanol, '
        'maximo 30 palabras, menciona un rango aproximado si '
        'puedes"}\n\n'
        "Usa NO_PUEDO_OPINAR si el material/equipo es demasiado "
        "especifico (marca, modelo, capacidad exacta) como para dar "
        "una opinion responsable sin mas contexto."
    )

    texto, modelo_usado = _llamar_ia(proveedor, cliente, prompt, modelo=modelo, max_tokens=200)
    datos = _extraer_json(texto)
    if not datos or "opinion" not in datos:
        return None

    opinion = str(datos.get("opinion", "")).strip().upper()
    if opinion not in {"RAZONABLE", "ALTO", "BAJO", "NO_PUEDO_OPINAR"}:
        opinion = "NO_PUEDO_OPINAR"

    return {
        "opinion": opinion,
        "razon": str(datos.get("razon", "")).strip(),
        "modelo": f"{proveedor}:{modelo_usado}",
    }
