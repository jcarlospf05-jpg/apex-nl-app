"""
Revisor de cotizaciones CAPEX - Nuevo Leon (version completa)
==================================================================

Sube tu cotizacion o licitacion y obten el veredicto por partida (ALTO / BAJO /
EN MERCADO) contra 4 fuentes a la vez:
  1. Historico real de obra publica de Nuevo Leon (2021-2025), ajustado a
     precios de hoy con el INPC (INEGI).
  2. Tabulador oficial de precios unitarios del Gobierno de la Ciudad de Mexico.
  3. Historico interno propio (Ragasa + otros proveedores) guardado en tu
     Google Sheet - se va llenando solo cada vez que subes una cotizacion.
  4. Busqueda en internet con IA (Gemini + Google Search): cuando las 3
     fuentes anteriores no alcanzan, se le pide a Gemini que BUSQUE (no que
     invente) un precio real publicado para ese concepto.

No necesitas subir ninguna base: todo ya viene integrado en la aplicacion.

Como correrla:
  local:    streamlit run app_v2.py
  en linea: desplegar en share.streamlit.io (ver instrucciones en el chat)

Configuracion en Streamlit Cloud (Settings -> Secrets):

    sheet_id = "13cqz5_MwOcDHwrQ4rNBb9NFI8odWKLEAV_vYQN2p70g"
    gemini_api_key = "TU_API_KEY_DE_GEMINI"

    [gcp_service_account]
    type = "service_account"
    project_id = "..."
    private_key_id = "..."
    private_key = "-----BEGIN PRIVATE KEY-----\\n...\\n-----END PRIVATE KEY-----\\n"
    client_email = "capex-sheets-bot@....iam.gserviceaccount.com"
    client_id = "..."
    token_uri = "https://oauth2.googleapis.com/token"
"""

import io
import pandas as pd
import streamlit as st
from comparador_multifuente_v2 import ComparadorMultiFuente
from gemini_busqueda import buscar_precio_mercado

st.set_page_config(page_title="Revisor de cotizaciones CAPEX - NL", layout="wide")

BASE_PATH = "Base_Precios_Unitarios_NL_CDMX.xlsx"
DEFAULT_SHEET_ID = "13cqz5_MwOcDHwrQ4rNBb9NFI8odWKLEAV_vYQN2p70g"
SIN_DATO = "— sin dato —"

st.title("Revisor de cotizaciones CAPEX - Nuevo León")
st.caption(
    "La base de precios (NL + CDMX), el histórico interno y la búsqueda con IA ya están "
    "integrados. Solo sube tu cotización o licitación."
)


@st.cache_resource
def cargar_comparador():
    return ComparadorMultiFuente(BASE_PATH)


@st.cache_resource
def cargar_historico():
    """Intenta conectar el historico interno via Google Sheets. Si no hay
    credenciales configuradas todavia, regresa None (la app sigue funcionando
    con el resto de fuentes)."""
    try:
        from historico_google_sheets import HistoricoGoogleSheets
        if "gcp_service_account" not in st.secrets:
            return None
        sheet_id = st.secrets.get("sheet_id", DEFAULT_SHEET_ID)
        return HistoricoGoogleSheets(sheet_id=sheet_id, creds_dict=st.secrets["gcp_service_account"])
    except Exception as e:
        st.warning(f"No se pudo conectar el histórico interno (Google Sheets): {e}")
        return None


@st.cache_data(ttl=60 * 60 * 24 * 30, show_spinner=False)
def buscar_ia_cacheado(concepto: str, unidad: str, api_key: str) -> dict:
    """Cache de 30 dias: si ya se busco este mismo concepto+unidad antes (en
    cualquier cotizacion, de cualquier usuario, mientras la app siga viva),
    no se vuelve a llamar a Gemini - esto es lo que mas ahorra tokens."""
    return buscar_precio_mercado(concepto, unidad, api_key)


comparador = cargar_comparador()
historico = cargar_historico()
gemini_api_key = st.secrets.get("gemini_api_key")

with st.sidebar:
    st.subheader("Datos de esta cotización")
    proveedor = st.text_input("Proveedor", placeholder="Nombre del proveedor")
    proyecto = st.text_input("Proyecto / licitación", placeholder="Ej. Planta Norte 2026")
    guardar_en_historico = st.checkbox(
        "Guardar esta cotización en el histórico", value=True,
        disabled=historico is None,
        help="Si está activo, cada partida queda guardada para comparar en el futuro."
    )
    ajustar_inflacion = st.checkbox(
        "Ajustar precios viejos de NL por inflación (INPC)", value=True,
        help="La base de Nuevo León casi no tiene datos desde 2024. Al activar esto, "
             "los precios de 2021-2023 se actualizan a su equivalente de hoy usando el "
             "INPC oficial del INEGI, antes de comparar contra tu cotización."
    )

    st.divider()
    st.subheader("Búsqueda con IA (Gemini)")
    usar_ia = st.checkbox(
        "Buscar precios en internet con IA", value=gemini_api_key is not None,
        disabled=gemini_api_key is None,
        help="Usa Gemini con Google Search para encontrar precios reales publicados "
             "en internet. Solo reporta precios que de verdad encontró, con su fuente."
    )
    limite_ia = st.number_input(
        "Máximo de conceptos distintos a buscar con IA por cotización",
        min_value=1, max_value=100, value=15, step=1, disabled=not usar_ia,
        help="Para no gastar de más tu cuota gratis de Gemini. Los conceptos repetidos "
             "dentro del mismo archivo solo cuentan una vez, y los ya buscados antes "
             "(en cualquier cotización de los últimos 30 días) no vuelven a gastar cuota."
    )
    if gemini_api_key is None:
        st.caption("IA no conectada todavía (falta 'gemini_api_key' en Secrets).")

    st.divider()
    if historico is None:
        st.caption("Histórico interno no conectado todavía (falta configurar Google Sheets en Secrets).")
    else:
        resumen = historico.resumen()
        st.caption(
            f"Histórico interno: {resumen['total_renglones']} renglones · "
            f"{len(resumen['proveedores'])} proveedores · {len(resumen['proyectos'])} proyectos"
        )

with st.expander("Formato esperado del archivo de cotización"):
    st.write("Un Excel (.xlsx) con estas columnas exactas, una fila por partida:")
    st.code("concepto | unidad | precio_unitario", language="text")
    ejemplo = pd.DataFrame([
        {"concepto": "Suministro y colocación de acero de refuerzo en losas, varilla corrugada", "unidad": "KG", "precio_unitario": 30},
        {"concepto": "Limpieza final de obra durante todo el periodo de ejecución", "unidad": "M2", "precio_unitario": 9},
    ])
    st.dataframe(ejemplo, use_container_width=True)

archivo = st.file_uploader("Sube tu cotización o licitación (.xlsx)", type=["xlsx"])

if archivo is not None:
    cotizacion = pd.read_excel(archivo)
    faltantes = {"concepto", "unidad", "precio_unitario"} - set(cotizacion.columns)
    if faltantes:
        st.error(f"Faltan columnas obligatorias en el archivo: {faltantes}")
    else:
        # Limite de busquedas IA: solo los primeros N conceptos distintos del
        # archivo pueden usar IA (conceptos repetidos no cuentan doble).
        pares_unicos = list(dict.fromkeys(
            zip(cotizacion["concepto"].astype(str), cotizacion["unidad"].astype(str))
        ))
        pares_permitidos_ia = set(pares_unicos[:limite_ia]) if usar_ia else set()
        if usar_ia and len(pares_unicos) > limite_ia:
            st.info(
                f"Esta cotización tiene {len(pares_unicos)} conceptos distintos; el límite de "
                f"búsqueda IA está en {limite_ia}, así que las primeras {limite_ia} partidas "
                f"únicas usarán IA y el resto mostrará 'límite alcanzado'. Puedes subir el "
                f"límite en la barra lateral."
            )

        def funcion_ia(concepto: str, unidad: str):
            if not usar_ia or gemini_api_key is None:
                return None
            if (concepto, unidad) not in pares_permitidos_ia:
                return {'precio_encontrado': False,
                        'motivo': f'No se buscó: límite de {limite_ia} conceptos distintos con IA alcanzado.'}
            return buscar_ia_cacheado(concepto, unidad, gemini_api_key)

        with st.spinner(f"Revisando {len(cotizacion)} partidas contra NL + CDMX + histórico + IA..."):
            filas = []
            for _, r in cotizacion.iterrows():
                precio = float(r["precio_unitario"])
                res = comparador.evaluar(
                    str(r["concepto"]), str(r["unidad"]), precio,
                    ajustar_inflacion=ajustar_inflacion,
                    funcion_busqueda_ia=funcion_ia,
                )
                nl = res["fuentes"]["nl_historico"]
                cdmx = res["fuentes"]["cdmx_gobierno"]
                ia = res["fuentes"]["ia_busqueda"]

                clasificaciones = []

                fila = {
                    "Concepto": r["concepto"],
                    "Unidad": r["unidad"],
                    "Precio cotizado": precio,
                }

                # --- 1. Nuevo Leon (ajustado a hoy) ---
                if nl.get("match"):
                    fila["Match NL (ajustado)"] = nl["match"]
                    fila["Precio NL ajustado a hoy"] = nl.get("precio_mediana_ajustada")
                    fila["Veredicto NL"] = nl.get("clasificacion")
                    clasificaciones.append(nl.get("clasificacion"))
                else:
                    fila["Match NL (ajustado)"] = "Sin coincidencia en la base de NL"
                    fila["Precio NL ajustado a hoy"] = None
                    fila["Veredicto NL"] = "Sin dato"

                # --- 2. Ciudad de Mexico (gobierno) ---
                if cdmx.get("match"):
                    fila["Match CDMX"] = cdmx["match"]
                    fila["Precio referencia CDMX"] = cdmx.get("precio_referencia")
                    fila["Veredicto CDMX"] = cdmx.get("clasificacion")
                    clasificaciones.append(cdmx.get("clasificacion"))
                else:
                    fila["Match CDMX"] = "Sin coincidencia en el tabulador de CDMX"
                    fila["Precio referencia CDMX"] = None
                    fila["Veredicto CDMX"] = "Sin dato"

                # --- 3. Historico interno (Google Sheets) ---
                if historico is not None:
                    h = historico.consultar(str(r["concepto"]), str(r["unidad"]), precio)
                    if h.get("match"):
                        fila["Match histórico interno"] = h["match"]
                        proveedores_validos = [str(p) for p in h.get("proveedores", []) if p and str(p) != 'nan']
                        fila["Proveedores en histórico"] = ", ".join(proveedores_validos) if proveedores_validos else SIN_DATO
                        fila["Precio mediana histórico"] = h.get("precio_mediana")
                        if h.get("clasificacion"):
                            fila["Veredicto histórico"] = h["clasificacion"]
                            clasificaciones.append(h["clasificacion"])
                        else:
                            fila["Veredicto histórico"] = h.get("nota") or "Solo 1 registro, sin banda estadística"
                    else:
                        fila["Match histórico interno"] = "Todavía no hay histórico para este concepto"
                        fila["Proveedores en histórico"] = SIN_DATO
                        fila["Precio mediana histórico"] = None
                        fila["Veredicto histórico"] = "Todavía no hay"
                else:
                    fila["Match histórico interno"] = "Histórico no conectado"
                    fila["Proveedores en histórico"] = SIN_DATO
                    fila["Precio mediana histórico"] = None
                    fila["Veredicto histórico"] = "Histórico no conectado"

                # --- 4. Busqueda con IA (Gemini) ---
                if ia.get("match"):
                    fila["Match IA (Gemini)"] = ia.get("fuente_nombre") or ia["match"]
                    fila["Precio IA (internet)"] = ia.get("precio_referencia")
                    fila["Fuente IA"] = ia.get("fuente_url") or SIN_DATO
                    fila["Veredicto IA"] = ia.get("clasificacion")
                    clasificaciones.append(ia.get("clasificacion"))
                else:
                    fila["Match IA (Gemini)"] = ia.get("motivo") or "IA desactivada"
                    fila["Precio IA (internet)"] = None
                    fila["Fuente IA"] = SIN_DATO
                    fila["Veredicto IA"] = "Sin dato"

                clasificaciones = [c for c in clasificaciones if c]
                if clasificaciones:
                    conteo = {c: clasificaciones.count(c) for c in set(clasificaciones)}
                    fila["Veredicto final"] = max(conteo, key=conteo.get)
                else:
                    fila["Veredicto final"] = "SIN DATOS SUFICIENTES"

                filas.append(fila)

            tabla = pd.DataFrame(filas)

            if historico is not None and guardar_en_historico:
                if not proveedor or not proyecto:
                    st.warning("Escribe el proveedor y el proyecto en la barra lateral para guardar en el histórico.")
                else:
                    historico.ingerir(cotizacion, proveedor=proveedor, proyecto=proyecto)
                    st.success(f"Cotización guardada en el histórico interno ({len(cotizacion)} partidas).")

        resumen_v = tabla["Veredicto final"].value_counts()
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Partidas revisadas", len(tabla))
        c2.metric("Altas", int(resumen_v.get("ALTO", 0)))
        c3.metric("Bajas", int(resumen_v.get("BAJO", 0)))
        c4.metric("En mercado", int(resumen_v.get("EN MERCADO", 0)))

        def resaltar(v):
            if v == "ALTO":
                return "background-color: #f7c1c1; color: #501313"
            if v == "BAJO":
                return "background-color: #c0dd97; color: #173404"
            if v == "SIN DATOS SUFICIENTES":
                return "background-color: #e8e8e8; color: #444444"
            return ""

        st.dataframe(
            tabla.style.map(resaltar, subset=["Veredicto final"]),
            use_container_width=True,
            height=min(600, 60 + 35 * len(tabla)),
        )

        buffer = io.BytesIO()
        tabla.to_excel(buffer, index=False, engine="openpyxl")
        st.download_button(
            "Descargar resultado (Excel)",
            data=buffer.getvalue(),
            file_name="revision_cotizacion.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

st.divider()
st.caption(
    "Fuentes: histórico real de licitaciones de obra pública de Nuevo León (SIASI / Open Contracting "
    "Partnership), Tabulador General de Precios Unitarios del Gobierno de la Ciudad de México (edición 2026), "
    "histórico interno propio guardado en Google Sheets, y búsqueda en internet con Gemini (Google Search) "
    "cuando las anteriores no encuentran nada."
)
st.caption(
    "Nota sobre el ajuste por inflación: la base de Nuevo León tiene muy poca información desde 2024 "
    "(el estado no ha publicado licitaciones más recientes). Los precios de años anteriores se actualizan "
    "a su equivalente de hoy usando el Índice Nacional de Precios al Consumidor (INPC) oficial del INEGI "
    "(https://www.inegi.org.mx/temas/inpc/). Es un índice general de consumo, no específico de construcción, "
    "así que es una aproximación razonable, no exacta."
)
st.caption(
    "Nota sobre la IA: Gemini solo reporta precios que encontró publicados en internet (con fuente citada), "
    "nunca inventa ni estima de memoria. Si no encuentra nada confiable, lo dice explícitamente."
)
