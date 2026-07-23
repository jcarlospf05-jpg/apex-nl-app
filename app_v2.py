"""
Revisor de cotizaciones CAPEX - Nuevo Leon (version completa)
==================================================================

Sube tu cotizacion o licitacion y obten el veredicto por partida (ALTO / BAJO /
EN MERCADO) contra 3 fuentes a la vez:
  1. Historico real de obra publica de Nuevo Leon (2021-2025)
  2. Tabulador oficial de precios unitarios del Gobierno de la Ciudad de Mexico
  3. Historico interno propio (Ragasa + otros proveedores) guardado en tu
     Google Sheet - se va llenando solo cada vez que subes una cotizacion

No necesitas subir ninguna base: todo ya viene integrado en la aplicacion.

Como correrla:
  local:    streamlit run app_v2.py
  en linea: desplegar en share.streamlit.io (ver instrucciones en el chat)

Configuracion de Google Sheets (una sola vez, en Streamlit Cloud):
  Settings -> Secrets, pega:

    sheet_id = "13cqz5_MwOcDHwrQ4rNBb9NFI8odWKLEAV_vYQN2p70g"

    [gcp_service_account]
    type = "service_account"
    project_id = "..."
    private_key_id = "..."
    private_key = "-----BEGIN PRIVATE KEY-----\\n...\\n-----END PRIVATE KEY-----\\n"
    client_email = "capex-sheets-bot@....iam.gserviceaccount.com"
    client_id = "..."
    token_uri = "https://oauth2.googleapis.com/token"

  (todos esos campos estan en el archivo .json que descargaste de Google Cloud)
"""

import io
import pandas as pd
import streamlit as st
from comparador_multifuente_v2 import ComparadorMultiFuente

st.set_page_config(page_title="Revisor de cotizaciones CAPEX - NL", layout="wide")

BASE_PATH = "Base_Precios_Unitarios_NL_CDMX.xlsx"
DEFAULT_SHEET_ID = "13cqz5_MwOcDHwrQ4rNBb9NFI8odWKLEAV_vYQN2p70g"

st.title("Revisor de cotizaciones CAPEX - Nuevo León")
st.caption(
    "La base de precios (NL + CDMX) y el histórico interno ya están integrados. "
    "Solo sube tu cotización o licitación."
)


@st.cache_resource
def cargar_comparador():
    return ComparadorMultiFuente(BASE_PATH)


@st.cache_resource
def cargar_historico():
    """Intenta conectar el historico interno via Google Sheets. Si no hay
    credenciales configuradas todavia, regresa None (la app sigue funcionando
    solo con NL + CDMX)."""
    try:
        from historico_google_sheets import HistoricoGoogleSheets
        if "gcp_service_account" not in st.secrets:
            return None
        sheet_id = st.secrets.get("sheet_id", DEFAULT_SHEET_ID)
        return HistoricoGoogleSheets(sheet_id=sheet_id, creds_dict=st.secrets["gcp_service_account"])
    except Exception as e:
        st.warning(f"No se pudo conectar el histórico interno (Google Sheets): {e}")
        return None


comparador = cargar_comparador()
historico = cargar_historico()

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
        with st.spinner(f"Revisando {len(cotizacion)} partidas contra NL + CDMX + histórico interno..."):
            filas = []
            for _, r in cotizacion.iterrows():
                precio = float(r["precio_unitario"])
                res = comparador.evaluar(
                    str(r["concepto"]), str(r["unidad"]), precio,
                    ajustar_inflacion=ajustar_inflacion,
                )
                nl = res["fuentes"]["nl_historico"]
                cdmx = res["fuentes"]["cdmx_gobierno"]

                fila = {
                    "Concepto": r["concepto"],
                    "Unidad": r["unidad"],
                    "Precio cotizado": precio,
                    "Match NL": nl.get("match"),
                    "Año del dato NL": nl.get("anio_dato_mas_reciente"),
                    "Precio mediana NL (original)": nl.get("precio_mediana"),
                    "Precio mediana NL (ajustado hoy)": nl.get("precio_mediana_ajustada"),
                    "Veredicto NL": nl.get("clasificacion"),
                    "Match CDMX": cdmx.get("match"),
                    "Precio referencia CDMX": cdmx.get("precio_referencia"),
                    "Veredicto CDMX": cdmx.get("clasificacion"),
                }

                clasificaciones = [v for v in (nl.get("clasificacion"), cdmx.get("clasificacion")) if v]

                if historico is not None:
                    h = historico.consultar(str(r["concepto"]), str(r["unidad"]), precio)
                    if h.get("match"):
                        fila["Match histórico interno"] = h["match"]
                        fila["Proveedores en histórico"] = ", ".join(h.get("proveedores", []))
                        fila["Precio mediana histórico"] = h.get("precio_mediana")
                        fila["Veredicto histórico"] = h.get("clasificacion")
                        if h.get("clasificacion"):
                            clasificaciones.append(h["clasificacion"])

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
    "e histórico interno propio guardado en Google Sheets."
)
st.caption(
    "Nota sobre el ajuste por inflación: la base de Nuevo León tiene muy poca información desde 2024 "
    "(el estado no ha publicado licitaciones más recientes). Cuando el ajuste está activo, los precios "
    "de años anteriores se actualizan a su equivalente de hoy usando el Índice Nacional de Precios al "
    "Consumidor (INPC) oficial del INEGI (https://www.inegi.org.mx/temas/inpc/) — el mismo tipo de índice "
    "que usa la industria de la construcción en México para actualizar costos. El INPC es un índice general "
    "de consumo, no específico de insumos de construcción, así que es una aproximación razonable, no exacta."
)
