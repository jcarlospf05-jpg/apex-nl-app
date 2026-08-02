"""
Revisor de cotizaciones CAPEX - Nuevo León
==========================================

La aplicación acepta:

- Excel .xlsx
- Excel .xls
- Excel .xlsm
- CSV
- PDF digital con tablas
e:

- La hoja correcta
La aplicación intenta detectar automáticament
- La fila donde empiezan los encabezados
- La columna de concepto
- La columna de unidad
- La columna de cantidad
- La columna de precio unitario
- La columna de importe

Después convierte los datos al formato interno:

concepto | unidad | precio_unitario

y realiza la evaluación contra:

1. Histórico de precios de Nuevo León
2. Tabulador de precios de CDMX
3. Histórico interno de Google Sheets
"""

import io
import re
import unicodedata
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from comparador_multifuente_v2 import ComparadorMultiFuente
import ajuste_inflacion
import revision_ia


# ==========================================================
# CONFIGURACIÓN GENERAL
# ==========================================================

st.set_page_config(
    page_title="Revisor de cotizaciones CAPEX - NL",
    layout="wide",
)

BASE_PATH = "Base_Precios_Unitarios_NL_CDMX.xlsx"

DEFAULT_SHEET_ID = (
    "13cqz5_MwOcDHwrQ4rNBb9NFI8odWKLEAV_vYQN2p70g"
)


st.title("Revisor de cotizaciones CAPEX - Nuevo León")

st.caption(
    "La base de precios de Nuevo León, CDMX y el histórico interno "
    "ya están integrados. Sube tu cotización o licitación."
)


# ==========================================================
# CARGA DEL COMPARADOR
# ==========================================================

@st.cache_resource
def cargar_comparador():
    return ComparadorMultiFuente(BASE_PATH)


@st.cache_resource
def cargar_historico():
    """
    Intenta conectar el histórico interno mediante Google Sheets.

    Cuando no existen credenciales configuradas, devuelve None.
    La aplicación continúa funcionando con Nuevo León y CDMX.
    """
    try:
        from historico_google_sheets import HistoricoGoogleSheets

        if "gcp_service_account" not in st.secrets:
            return None

        sheet_id = st.secrets.get(
            "sheet_id",
            DEFAULT_SHEET_ID,
        )

        return HistoricoGoogleSheets(
            sheet_id=sheet_id,
            creds_dict=st.secrets["gcp_service_account"],
        )

    except Exception as error:
        st.warning(
            "No se pudo conectar el histórico interno "
            f"de Google Sheets: {error}"
        )

        return None


comparador = cargar_comparador()
historico = cargar_historico()

# Revisa una sola vez si hay una API key de IA configurada -- acepta
# Gemini (Secrets: gemini_api_key) o OpenAI (Secrets: openai_api_key),
# lo que esté disponible primero. Si no hay ninguna, la casilla de
# "Activar revisión con IA" en la barra lateral se muestra
# deshabilitada y la app sigue funcionando normal sin esta capa
# opcional.
ia_disponible = revision_ia.ia_disponible()

# Intenta traer el dato mas reciente del INPC directo de la API de INEGI.
# Si no hay token configurado (Secrets: inegi_api_token) o falla la
# conexion, no truena nada: se queda con el valor de respaldo de
# ajuste_inflacion.py.
try:
    inpc_en_vivo = ajuste_inflacion.refrescar_nivel_actual()
except Exception:
    inpc_en_vivo = False


# ==========================================================
# SINÓNIMOS DE COLUMNAS
# ==========================================================

COLUMNAS_OBJETIVO = {
    "partida": [
        "partida",
        "item",
        "ítem",
        "renglon",
        "renglón",
        "numero",
        "número",
        "no",
        "num",
        "clave",
        "posición",
        "posicion",
    ],
    "concepto": [
        "concepto",
        "descripcion",
        "descripción",
        "descripcion de los trabajos",
        "descripción de los trabajos",
        "alcance",
        "servicio",
        "material",
        "partida descripcion",
        "partida descripción",
        "trabajo",
        "trabajos",
    ],
    "unidad": [
        "unidad",
        "um",
        "u m",
        "u.m",
        "unid",
        "unidad de medida",
        "medida",
    ],
    "cantidad": [
        "cantidad",
        "cant",
        "volumen",
        "vol",
        "qty",
        "cantidad solicitada",
    ],
    "precio_unitario": [
        "precio unitario",
        "p u",
        "p.u",
        "pu",
        "unitario",
        "precio",
        "unit price",
        "costo unitario",
        "precio por unidad",
    ],
    "importe": [
        "importe",
        "total",
        "monto",
        "importe total",
        "precio total",
        "subtotal",
    ],
}


# ==========================================================
# FUNCIONES DE LIMPIEZA
# ==========================================================

def normalizar_texto(valor) -> str:
    """
    Convierte un texto a una forma comparable.

    Ejemplo:
    'Precio Unitario' -> 'precio unitario'
    """
    if valor is None:
        return ""

    texto = str(valor).strip().lower()

    texto = unicodedata.normalize(
        "NFKD",
        texto,
    )

    texto = "".join(
        caracter
        for caracter in texto
        if not unicodedata.combining(caracter)
    )

    texto = re.sub(
        r"[\n\r\t]+",
        " ",
        texto,
    )

    texto = re.sub(
        r"[^a-z0-9]+",
        " ",
        texto,
    )

    texto = re.sub(
        r"\s+",
        " ",
        texto,
    ).strip()

    return texto


def convertir_numero(valor):
    if valor is None:
        return None

    if isinstance(valor, (list, tuple, dict, set)):
        if not valor:
            return None

        if isinstance(valor, (list, tuple)) and len(valor) == 1:
            valor = valor[0]
        else:
            valor = " ".join(str(x) for x in valor)

    if isinstance(valor, (int, float)):
        if pd.isna(valor):
            return None
        return float(valor)

    texto = str(valor).strip()

    if texto.lower() in {"", "none", "nan", "null", "[]", "-", "--"}:
        return None

    texto = texto.replace("$", "")
    texto = texto.replace("MXN", "")
    texto = texto.replace("USD", "")
    texto = texto.replace("EUR", "")
    texto = texto.replace(",", "")
    texto = texto.replace(" ", "")
    texto = texto.replace("(", "-")
    texto = texto.replace(")", "")

    texto = re.sub(r"[^0-9.\-]", "", texto)

    if texto in {"", "-", ".", "-."}:
        return None

    try:
        return float(texto)
    except (ValueError, TypeError):
        return None


def normalizar_unidad(valor) -> str:
    """
    Normaliza unidades frecuentes.
    """
    texto = normalizar_texto(valor)

    equivalencias = {
        "m3": "M3",
        "m 3": "M3",
        "metro cubico": "M3",
        "metros cubicos": "M3",
        "m2": "M2",
        "m 2": "M2",
        "metro cuadrado": "M2",
        "metros cuadrados": "M2",
        "ml": "M",
        "metro lineal": "M",
        "metros lineales": "M",
        "m": "M",
        "kg": "KG",
        "kilogramo": "KG",
        "kilogramos": "KG",
        "ton": "TON",
        "tonelada": "TON",
        "toneladas": "TON",
        "pza": "PZA",
        "pieza": "PZA",
        "piezas": "PZA",
        "lote": "LOTE",
        "servicio": "SERVICIO",
        "juego": "JGO",
        "jgo": "JGO",
    }

    if texto in equivalencias:
        return equivalencias[texto]

    return str(valor).strip().upper()


# ==========================================================
# DETECCIÓN DE COLUMNAS
# ==========================================================

def detectar_campo(nombre_columna: str):
    """
    Relaciona una columna del proveedor con el formato interno.
    """
    columna_normalizada = normalizar_texto(
        nombre_columna
    )

    mejor_campo = None
    mejor_puntaje = 0

    for campo, sinonimos in COLUMNAS_OBJETIVO.items():

        for sinonimo in sinonimos:
            sinonimo_normalizado = normalizar_texto(
                sinonimo
            )

            if (
                columna_normalizada
                == sinonimo_normalizado
            ):
                puntaje = 100

            elif (
                sinonimo_normalizado
                in columna_normalizada
            ):
                puntaje = 80

            elif (
                columna_normalizada
                in sinonimo_normalizado
                and columna_normalizada
            ):
                puntaje = 70

            else:
                puntaje = 0

            if puntaje > mejor_puntaje:
                mejor_puntaje = puntaje
                mejor_campo = campo

    if mejor_puntaje >= 70:
        return mejor_campo

    return None


def evaluar_fila_encabezado(fila) -> int:
    """
    Calcula cuántos encabezados reconocibles contiene una fila.
    """
    campos_detectados = set()

    for valor in fila:
        campo = detectar_campo(
            str(valor)
        )

        if campo:
            campos_detectados.add(
                campo
            )

    puntaje = len(
        campos_detectados
    )

    if "concepto" in campos_detectados:
        puntaje += 2

    if "precio_unitario" in campos_detectados:
        puntaje += 2

    if "unidad" in campos_detectados:
        puntaje += 1

    return puntaje


def encontrar_encabezado(
    df_crudo: pd.DataFrame,
    limite_filas: int = 50,
):
    """
    Busca la fila donde realmente comienza la tabla.
    """
    mejor_fila = None
    mejor_puntaje = 0

    limite = min(
        limite_filas,
        len(df_crudo),
    )

    for indice in range(limite):

        fila = df_crudo.iloc[
            indice
        ].tolist()

        puntaje = evaluar_fila_encabezado(
            fila
        )

        if puntaje > mejor_puntaje:
            mejor_puntaje = puntaje
            mejor_fila = indice

    if mejor_puntaje < 6:
        return None, mejor_puntaje

    return mejor_fila, mejor_puntaje


# ==========================================================
# NORMALIZACIÓN DE TABLAS
# ==========================================================

def normalizar_dataframe(
    df_crudo: pd.DataFrame,
    nombre_origen: str = "",
) -> pd.DataFrame:
    """
    Convierte una tabla irregular al formato interno.
    """
    if df_crudo.empty:
        return pd.DataFrame()

    fila_encabezado, puntaje = encontrar_encabezado(
        df_crudo
    )

    if fila_encabezado is None:
        return pd.DataFrame()

    encabezados = []

    for indice, valor in enumerate(
        df_crudo.iloc[
            fila_encabezado
        ].tolist()
    ):
        if (
            valor is not None
            and not pd.isna(valor)
        ):
            encabezado = str(
                valor
            ).strip()

        else:
            encabezado = (
                f"columna_{indice}"
            )

        encabezados.append(
            encabezado
        )

    df = df_crudo.iloc[
        fila_encabezado + 1:
    ].copy()

    df.columns = encabezados

    df = df.reset_index(
        drop=True
    )

    mapa_columnas = {}

    for columna in df.columns:

        campo = detectar_campo(
            columna
        )

        if (
            campo
            and campo not in mapa_columnas
        ):
            mapa_columnas[
                campo
            ] = columna

    if "concepto" not in mapa_columnas:
        return pd.DataFrame()

    if "precio_unitario" not in mapa_columnas:
        return pd.DataFrame()

    resultado = pd.DataFrame()

    for campo in COLUMNAS_OBJETIVO:

        columna_origen = mapa_columnas.get(
            campo
        )

        if columna_origen is not None:
            resultado[
                campo
            ] = df[
                columna_origen
            ]

        else:
            resultado[
                campo
            ] = None

    resultado[
        "origen"
    ] = nombre_origen

    resultado[
        "fila_encabezado"
    ] = fila_encabezado + 1

    resultado[
        "puntaje_deteccion"
    ] = puntaje

    resultado[
        "concepto"
    ] = (
        resultado[
            "concepto"
        ]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    resultado[
        "unidad"
    ] = resultado[
        "unidad"
    ].apply(
        normalizar_unidad
    )

    resultado[
        "cantidad"
    ] = resultado[
        "cantidad"
    ].apply(
        convertir_numero
    )

    resultado[
        "precio_unitario"
    ] = resultado[
        "precio_unitario"
    ].apply(
        convertir_numero
    )

    resultado[
        "importe"
    ] = resultado[
        "importe"
    ].apply(
        convertir_numero
    )
    resultado["cantidad"] = pd.to_numeric(
        resultado["cantidad"],
        errors="coerce",
    )

    resultado["precio_unitario"] = pd.to_numeric(
        resultado["precio_unitario"],
        errors="coerce",
    )

    resultado["importe"] = pd.to_numeric(
        resultado["importe"],
        errors="coerce",
    )

    importe_calculado = (
        resultado["cantidad"]
        * resultado["precio_unitario"]
    )

    resultado["importe"] = resultado["importe"].fillna(
        importe_calculado
    )

    palabras_excluir = [
        "subtotal",
        "iva",
        "impuesto",
        "total general",
        "gran total",
        "condiciones de pago",
        "vigencia de la oferta",
        "forma de pago",
        "notas",
        "observaciones",
    ]

    concepto_normalizado = resultado[
        "concepto"
    ].apply(
        normalizar_texto
    )

    mascara_excluir = pd.Series(
        False,
        index=resultado.index,
    )

    for palabra in palabras_excluir:

        palabra_normalizada = normalizar_texto(
            palabra
        )

        mascara_excluir |= (
            concepto_normalizado
            == palabra_normalizada
        )

    resultado = resultado[
        ~mascara_excluir
        & resultado[
            "concepto"
        ].ne("")
        & resultado[
            "precio_unitario"
        ].notna()
        & resultado[
            "precio_unitario"
        ].gt(0)
    ]

    resultado = resultado.reset_index(
        drop=True
    )

    return resultado


# ==========================================================
# LECTURA DE EXCEL
# ==========================================================

def leer_excel(archivo):
    """
    Revisa todas las hojas del Excel y selecciona
    la tabla más probable.
    """
    contenido = archivo.getvalue()

    extension = Path(
        archivo.name
    ).suffix.lower()

    if extension == ".xls":
        motor = "xlrd"
    else:
        motor = "openpyxl"

    hojas = pd.read_excel(
        io.BytesIO(contenido),
        sheet_name=None,
        header=None,
        dtype=object,
        engine=motor,
    )

    candidatos = []

    for nombre_hoja, df_crudo in hojas.items():

        normalizado = normalizar_dataframe(
            df_crudo,
            nombre_origen=nombre_hoja,
        )

        if not normalizado.empty:

            candidatos.append(
                {
                    "hoja": nombre_hoja,
                    "datos": normalizado,
                    "partidas": len(
                        normalizado
                    ),
                    "puntaje": normalizado[
                        "puntaje_deteccion"
                    ].max(),
                }
            )

    if not candidatos:
        raise ValueError(
            "No se encontró una tabla con concepto, "
            "unidad y precio unitario en ninguna hoja."
        )

    candidatos.sort(
        key=lambda item: (
            item["puntaje"],
            item["partidas"],
        ),
        reverse=True,
    )

    mejor = candidatos[0]

    confianza = min(
        100,
        int(
            mejor["puntaje"]
            / 9
            * 100
        ),
    )

    metadatos = {
        "tipo": "Excel",
        "hoja_detectada": mejor["hoja"],
        "partidas": mejor["partidas"],
        "confianza": confianza,
        "hojas_candidatas": [
            item["hoja"]
            for item in candidatos
        ],
    }

    return mejor["datos"], metadatos


# ==========================================================
# LECTURA DE CSV
# ==========================================================

def leer_csv(archivo):
    contenido = archivo.getvalue()

    intentos = [
        {
            "sep": None,
            "engine": "python",
        },
        {
            "sep": ",",
        },
        {
            "sep": ";",
        },
        {
            "sep": "\t",
        },
    ]

    ultimo_error = None

    for configuracion in intentos:

        try:
            df_crudo = pd.read_csv(
                io.BytesIO(contenido),
                header=None,
                dtype=object,
                encoding="utf-8-sig",
                **configuracion,
            )

            normalizado = normalizar_dataframe(
                df_crudo,
                nombre_origen="CSV",
            )

            if not normalizado.empty:

                confianza = min(
                    100,
                    int(
                        normalizado[
                            "puntaje_deteccion"
                        ].max()
                        / 9
                        * 100
                    ),
                )

                metadatos = {
                    "tipo": "CSV",
                    "hoja_detectada": "CSV",
                    "partidas": len(
                        normalizado
                    ),
                    "confianza": confianza,
                }

                return normalizado, metadatos

        except Exception as error:
            ultimo_error = error

    raise ValueError(
        "No fue posible interpretar el CSV. "
        f"Detalle: {ultimo_error}"
    )


# ==========================================================
# LECTURA DE PDF
# ==========================================================

def leer_pdf(archivo):
    """
    Lee PDF digitales con distintos formatos:

    1. Tablas de 8 columnas.
    2. Tablas de 6 columnas.
    3. Cotizaciones con una sola partida global.
    4. Cotizaciones de obra con descripciones de varias líneas.

    No utiliza OCR.
    """
    try:
        import pdfplumber

    except ImportError as error:
        raise RuntimeError(
            "Falta instalar pdfplumber. "
            "Agrégalo al archivo requirements.txt."
        ) from error

    contenido = archivo.getvalue()

    filas_validas = []
    paginas_detectadas = set()

    # ======================================================
    # PRIMER INTENTO: TABLAS DE 6 U 8 COLUMNAS
    # ======================================================

    with pdfplumber.open(
        io.BytesIO(contenido)
    ) as pdf:

        for numero_pagina, pagina in enumerate(
            pdf.pages,
            start=1,
        ):
            tablas = pagina.extract_tables() or []

            for tabla in tablas:

                if not tabla:
                    continue

                for fila in tabla:

                    if not fila or len(fila) < 6:
                        continue

                    valores = list(fila)

                    # Formato de 8 columnas:
                    # PDA | DESCRIPCIÓN | UNIDAD | CANTIDAD |
                    # MATERIAL | M.O. | P.U. | IMPORTE
                    if len(valores) >= 8:
                        partida = valores[0]
                        concepto = valores[1]
                        unidad = valores[2]
                        cantidad = valores[3]
                        precio_unitario = valores[6]
                        importe = valores[7]

                    # Formato de 6 columnas:
                    # CLAVE | DESCRIPCIÓN | UNIDAD |
                    # CANTIDAD | P.U. | IMPORTE
                    else:
                        partida = valores[0]
                        concepto = valores[1]
                        unidad = valores[2]
                        cantidad = valores[3]
                        precio_unitario = valores[4]
                        importe = valores[5]

                    # Algunas cotizaciones (ej. proveedores que usan
                    # PARTIDA|DESCRIPTION|CANTIDAD|UNIDAD|...) traen las
                    # columnas CANTIDAD y UNIDAD en el orden contrario al
                    # que asumimos arriba. En vez de adivinar el orden por
                    # posición fija, se detecta por el CONTENIDO: la
                    # columna "cantidad" siempre debe poder leerse como
                    # número (1.00, 53.70...) y "unidad" nunca (PIEZA,
                    # METROS...). Si están al revés, se intercambian antes
                    # de seguir procesando. Esto evita que un documento con
                    # el orden invertido termine con cantidad=None y
                    # unidad="1.00" (y por lo tanto pierda esas partidas
                    # frente a los otros métodos de lectura).
                    if (
                        convertir_numero(cantidad) is None
                        and convertir_numero(unidad) is not None
                    ):
                        cantidad, unidad = unidad, cantidad

                    partida_texto = (
                        str(partida).strip()
                        if partida is not None
                        else ""
                    )

                    concepto_texto = (
                        str(concepto).strip()
                        if concepto is not None
                        else ""
                    )

                    unidad_texto = (
                        str(unidad).strip()
                        if unidad is not None
                        else ""
                    )

                    concepto_texto = re.sub(
                        r"\s+",
                        " ",
                        concepto_texto,
                    ).strip()

                    if normalizar_texto(partida_texto) in {
                        "pda",
                        "partida",
                        "item",
                        "clave",
                    }:
                        continue

                    if normalizar_texto(concepto_texto) in {
                        "descripcion",
                        "descripción",
                    }:
                        continue

                    cantidad_numero = convertir_numero(
                        cantidad
                    )

                    precio_numero = convertir_numero(
                        precio_unitario
                    )

                    importe_numero = convertir_numero(
                        importe
                    )

                    if not concepto_texto:
                        continue

                    if (
                        precio_numero is None
                        or precio_numero <= 0
                    ):
                        continue

                    coincidencia_partida = re.search(
                        r"\d+(?:\.\d+)?",
                        partida_texto,
                    )

                    if coincidencia_partida:
                        partida_limpia = (
                            coincidencia_partida.group()
                        )
                    else:
                        partida_limpia = str(
                            len(filas_validas) + 1
                        )

                    filas_validas.append(
                        {
                            "partida": partida_limpia,
                            "concepto": concepto_texto,
                            "unidad": normalizar_unidad(
                                unidad_texto
                            ),
                            "cantidad": cantidad_numero,
                            "precio_unitario": precio_numero,
                            "importe": importe_numero,
                            "origen": (
                                f"Página {numero_pagina}"
                            ),
                            "fila_encabezado": None,
                            "puntaje_deteccion": 9,
                        }
                    )

                    paginas_detectadas.add(
                        numero_pagina
                    )
    # Detectar cotizaciones de obra con partidas decimales.
    # En este formato, la extracción automática de tablas
    # puede mezclar las descripciones de las partidas

    with pdfplumber.open(
        io.BytesIO(contenido)
    ) as pdf:
        texto_para_detectar_formato = "\n".join(
            pagina.extract_text() or ""
            for pagina in pdf.pages
        )

    encabezado_normalizado = normalizar_texto(
        texto_para_detectar_formato
    )

    claves_obra_detectadas = re.findall(
        r"\b\d+\.\d+\b",
        texto_para_detectar_formato,
    )

    es_cotizacion_obra = (
        "precio unitario" in encabezado_normalizado
        and "unidad" in encabezado_normalizado
        and "cantidad" in encabezado_normalizado
        and "importe" in encabezado_normalizado
        and len(set(claves_obra_detectadas)) >= 2
    )

    if es_cotizacion_obra:
        filas_validas = []
        paginas_detectadas = set()



    # ======================================================
    # SEGUNDO INTENTO: UNA SOLA PARTIDA GLOBAL
    # ======================================================

    if not filas_validas:

        with pdfplumber.open(
            io.BytesIO(contenido)
        ) as pdf:
            texto_completo = "\n".join(
                pagina.extract_text() or ""
                for pagina in pdf.pages
            )

        patron_servicio_global = re.search(
            r"Descripción\s+Cantidad\s+Precio\s+unitario"
            r"\s+Impuestos\s+Importe\s+"
            r"(.+?)\s+"
            r"(\d+(?:\.\d+)?)\s+"
            r"([A-Za-zÁÉÍÓÚáéíóúÑñ]+)\s+"
            r"\$?\s*([\d,]+\.\d{2})\s+"
            r"IVA\s*\(\s*16%\s*\)\s+"
            r"\$?\s*([\d,]+\.\d{2})",
            texto_completo,
            flags=re.IGNORECASE | re.DOTALL,
        )

        if patron_servicio_global:

            descripcion = re.sub(
                r"\s+",
                " ",
                patron_servicio_global.group(1),
            ).strip()

            cantidad = convertir_numero(
                patron_servicio_global.group(2)
            )

            unidad = normalizar_unidad(
                patron_servicio_global.group(3)
            )

            precio_unitario = convertir_numero(
                patron_servicio_global.group(4)
            )

            importe = convertir_numero(
                patron_servicio_global.group(5)
            )

            filas_validas.append(
                {
                    "partida": "1",
                    "concepto": descripcion,
                    "unidad": unidad,
                    "cantidad": cantidad,
                    "precio_unitario": precio_unitario,
                    "importe": importe,
                    "origen": "Página 1",
                    "fila_encabezado": None,
                    "puntaje_deteccion": 8,
                }
            )

            paginas_detectadas.add(1)

    # ======================================================
    # TERCER INTENTO: OBRA CON DESCRIPCIONES MULTILÍNEA
    # ======================================================

    filas_tabla = list(filas_validas)
    filas_obra = []
    lineas_pdf = []

    with pdfplumber.open(
        io.BytesIO(contenido)
    ) as pdf:
        for numero_pagina, pagina in enumerate(
            pdf.pages,
            start=1,
        ):
            texto_pagina = pagina.extract_text() or ""

            for linea in texto_pagina.splitlines():
                linea = re.sub(
                    r"\s+",
                    " ",
                    linea,
                ).strip()

                if linea:
                    lineas_pdf.append(
                        (numero_pagina, linea)
                    )

    patron_renglon_precio = re.compile(
        r"^(?:(\d+(?:\.\d+)?)\s+)?"
        r"(.*?)"
        r"\b(M2|M3|ML|M|PZA|PZAS|SERVICIO|LOTE|KG|TON)\b"
        r"\s+([\d,]+(?:\.\d+)?)"
        r"\s+\$?\s*([\d,]+\.\d{2})"
        r"\s+\$?\s*([\d,]+\.\d{2})$",
        flags=re.IGNORECASE,
    )

    descripcion_acumulada = []

    textos_ignorar = [
        "clave descripcion unidad cantidad",
        "saro construcciones",
        "www saroconstrucciones com",
        "fecha monterrey",
        "cliente",
        "nombre de la empresa",
        "proyecto",
        "no de cotizacion",
        "subtotal",
        "iva",
        "total",
        "notas y condiciones",
    ]

    for numero_pagina, linea in lineas_pdf:
        linea_normalizada = normalizar_texto(
            linea
        )

        # Los títulos de sección marcan una partida nueva.
        if re.match(
            r"^(PRELIMINARES|BANQUETA|LIMPIEZA FINA)"
            r"(?:\s+\$[\d,]+\.\d{2})?$",
            linea,
            flags=re.IGNORECASE,
        ):
            descripcion_acumulada = []
            continue

        coincidencia = patron_renglon_precio.match(
            linea
        )

        if coincidencia:
            clave = coincidencia.group(1)
            descripcion_en_linea = (
                coincidencia.group(2).strip()
            )

            partes = list(
                descripcion_acumulada
            )

            if descripcion_en_linea:
                partes.append(
                    descripcion_en_linea
                )

            descripcion = re.sub(
                r"\s+",
                " ",
                " ".join(partes),
            ).strip()

            unidad = normalizar_unidad(
                coincidencia.group(3)
            )

            cantidad = convertir_numero(
                coincidencia.group(4)
            )

            precio_unitario = convertir_numero(
                coincidencia.group(5)
            )

            importe = convertir_numero(
                coincidencia.group(6)
            )

            if (
                descripcion
                and precio_unitario is not None
                and precio_unitario > 0
            ):
                filas_obra.append(
                    {
                        "partida": (
                            clave
                            if clave
                            else str(len(filas_obra) + 1)
                        ),
                        "concepto": descripcion,
                        "unidad": unidad,
                        "cantidad": cantidad,
                        "precio_unitario": precio_unitario,
                        "importe": importe,
                        "origen": f"Página {numero_pagina}",
                        "fila_encabezado": None,
                        "puntaje_deteccion": 8,
                    }
                )

                paginas_detectadas.add(
                    numero_pagina
                )

            descripcion_acumulada = []
            continue

        if any(
            texto in linea_normalizada
            for texto in textos_ignorar
        ):
            continue



        descripcion_acumulada.append(
            linea
        )

    # ======================================================
    # CUARTO INTENTO: COTIZACIONES ESTILO ODOO/ZOHO/FACTURAMA
    # (cantidad y unidad juntas, con "IVA(XX%)" en medio del renglón)
    # ======================================================
    # Formato muy comun en cotizaciones que llegan de proveedores reales
    # (ej. "SUMINISTRO DE TEE ... 4.00 Pieza 462.00 IVA(16%) $ 1,848.00"):
    # no hay columna de "Unidad" separada (va pegada a la cantidad), el
    # orden es cantidad->unidad->precio->impuesto->importe (al reves de
    # "TERCER INTENTO", que espera unidad->cantidad), la unidad es una
    # palabra completa como "Pieza" (no una abreviatura de una lista
    # fija), y casi siempre hay una linea "ENTREGA: X SEMANAS" pegada
    # despues de cada renglon que hay que ignorar para que no se cuele
    # en la descripcion del siguiente renglon.
    #
    # Las lineas de encabezado/pie de pagina (razon social, direccion,
    # RFC, telefono, "Pagina X / Y", etc.) se repiten IDENTICAS en cada
    # pagina del PDF -- en vez de tratar de adivinar el nombre de cada
    # proveedor, se detectan automaticamente como cualquier linea que
    # aparezca en TODAS las paginas.
    #
    # IMPORTANTE: el umbral debe ser "en todas las paginas", no solo
    # "en 2 o mas". Con productos similares (ej. varios cuples/reducciones
    # con la misma norma) es comun que dos renglones DISTINTOS compartan
    # una especificacion identica como "S/C BE, B16.25, C 40, A420 WPL6,
    # ANSI B16.9" -- eso puede repetirse en 2 paginas sin ser encabezado,
    # y si se ignora se pierde contenido real de la descripcion.
    total_paginas_pdf = len(
        {numero_pagina for numero_pagina, _ in lineas_pdf}
    )
    paginas_por_linea = {}
    for numero_pagina, linea in lineas_pdf:
        paginas_por_linea.setdefault(linea, set()).add(numero_pagina)
    lineas_repetidas_en_paginas = {
        linea
        for linea, paginas in paginas_por_linea.items()
        if total_paginas_pdf >= 2 and len(paginas) >= total_paginas_pdf
    }

    patrones_ruido_factura = [
        re.compile(r"^RFC\s*:", re.IGNORECASE),
        re.compile(r"P[aá]gina\s+\d+\s*/\s*\d+", re.IGNORECASE),
        re.compile(r"^N[uú]mero de cotizaci[oó]n", re.IGNORECASE),
        re.compile(r"^Direcci[oó]n de (facturaci[oó]n|env[ií]o)", re.IGNORECASE),
        re.compile(r"^ENTREGA\s*:", re.IGNORECASE),
        re.compile(r"^T[eé]rminos\s+(y\s+condiciones|de\s+pago)", re.IGNORECASE),
        re.compile(r"^Subtotal\b", re.IGNORECASE),
        # Renglon de TOTALES de IVA (sin parentesis), distinto del
        # token "IVA(16%)" que va DENTRO de cada renglon de partida.
        re.compile(r"^IVA\s+\d{1,2}\s*%", re.IGNORECASE),
        re.compile(r"^Total\s*\$", re.IGNORECASE),
        re.compile(r"[\w.\-]+@[\w.\-]+\.\w+"),
        re.compile(r"https?://\S+"),
        re.compile(r"^\+?\d[\d\s]{8,}\d"),
        # Linea de encabezado de la tabla en si.
        re.compile(
            r"^Descripci[oó]n\s+Cantidad\s+Precio\s+unitario",
            re.IGNORECASE,
        ),
        # Fecha suelta (dd/mm/aaaa), tipica debajo de "Fecha de
        # cotizacion"/"Vencimiento" cuando la etiqueta y el valor
        # vienen en lineas separadas.
        re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4}\s*$"),
    ]

    # Etiquetas cuyo VALOR viene en el renglon de abajo (no en la misma
    # linea), asi que hay que saltarse tambien esa siguiente linea -
    # ej. "Vendedor" seguido de "Javier Vega" en la linea de abajo. Sin
    # esto, el nombre del vendedor se cuela en la descripcion de la
    # siguiente partida real.
    patrones_etiqueta_valor_abajo = [
        re.compile(r"^Fecha de cotizaci[oó]n\s*$", re.IGNORECASE),
        re.compile(r"^Vencimiento\s*$", re.IGNORECASE),
        re.compile(r"^Vendedor\s*$", re.IGNORECASE),
    ]

    patron_fila_servicio = re.compile(
        r"^(.*?)"
        r"\b(\d+(?:[.,]\d+)?)\s+"
        r"([A-Za-zÁÉÍÓÚáéíóúÑñ]{2,20})\s+"
        r"\$?\s*([\d,]+\.\d{2})\s+"
        r"(?:IVA\s*\(\s*\d{1,2}\s*%\s*\)\s+)?"
        r"\$?\s*([\d,]+\.\d{2})\s*$",
        flags=re.IGNORECASE,
    )

    filas_odoo = []
    descripcion_acumulada = []
    saltar_siguiente_linea = False
    # En PDFs tipo Odoo, cuando la descripcion es larga el sobrante NO
    # queda ANTES de la linea con los numeros, sino DESPUES (el motor de
    # layout del PDF "recorta" la descripcion en la columna y el resto
    # cae en la siguiente linea, antes del "ENTREGA: ..."). Ejemplo real:
    #   'SUMINISTRO DE TEE REDUCC. SOLD. CED-40 DE 1-1/4" 4.00 Pieza
    #    462.00 IVA(16%) $ 1,848.00'
    #   'X 1/2" ASTM A-234 WPB'          <- esto sigue siendo la MISMA
    #                                       partida, no la siguiente.
    #   'ENTREGA: 2 A 3 SEMANAS'
    # Esta bandera indica "la ultima linea cerro un renglon con numeros;
    # si la siguiente linea no es ruido ni un renglon nuevo, es el
    # sobrante de la descripcion de ESE renglon, hay que pegarlo ahi".
    esperando_continuacion_descripcion = False

    for numero_pagina, linea in lineas_pdf:

        if saltar_siguiente_linea:
            saltar_siguiente_linea = False
            esperando_continuacion_descripcion = False
            continue

        if linea in lineas_repetidas_en_paginas:
            descripcion_acumulada = []
            esperando_continuacion_descripcion = False
            continue

        if any(
            patron.search(linea)
            for patron in patrones_etiqueta_valor_abajo
        ):
            descripcion_acumulada = []
            esperando_continuacion_descripcion = False
            saltar_siguiente_linea = True
            continue

        if any(
            patron.search(linea)
            for patron in patrones_ruido_factura
        ):
            descripcion_acumulada = []
            esperando_continuacion_descripcion = False
            continue

        coincidencia = patron_fila_servicio.match(linea)

        if coincidencia:
            descripcion_en_linea = coincidencia.group(1).strip()

            partes = list(descripcion_acumulada)
            if descripcion_en_linea:
                partes.append(descripcion_en_linea)

            descripcion = re.sub(
                r"\s+", " ", " ".join(partes)
            ).strip()

            cantidad = convertir_numero(coincidencia.group(2))
            unidad = normalizar_unidad(coincidencia.group(3))
            precio_unitario = convertir_numero(coincidencia.group(4))
            importe = convertir_numero(coincidencia.group(5))

            if (
                descripcion
                and precio_unitario is not None
                and precio_unitario > 0
            ):
                filas_odoo.append(
                    {
                        "partida": str(len(filas_odoo) + 1),
                        "concepto": descripcion,
                        "unidad": unidad,
                        "cantidad": cantidad,
                        "precio_unitario": precio_unitario,
                        "importe": importe,
                        "origen": f"Página {numero_pagina}",
                        "fila_encabezado": None,
                        "puntaje_deteccion": 8,
                    }
                )
                paginas_detectadas.add(numero_pagina)
                esperando_continuacion_descripcion = True
            else:
                esperando_continuacion_descripcion = False

            descripcion_acumulada = []
            continue

        if esperando_continuacion_descripcion and filas_odoo:
            filas_odoo[-1]["concepto"] = re.sub(
                r"\s+",
                " ",
                f"{filas_odoo[-1]['concepto']} {linea}",
            ).strip()
            esperando_continuacion_descripcion = False
            continue

        descripcion_acumulada.append(linea)

    # Para decidir cuál de los 3 métodos (tabla con bordes, obra
    # multilínea, facturas estilo Odoo) usar, primero se intenta contra
    # el SUBTOTAL declarado en el PDF -- comparar la suma de importes de
    # cada método contra ese número es mucho más confiable que solo
    # contar renglones, porque un método puede "ganar" por tener más
    # filas pero con datos rotos o mal alineados (columnas de una tabla
    # leídas en el orden equivocado, texto de factura cortado mal,
    # etc.). Ojo: se compara contra el Subtotal (antes de impuestos), NO
    # el Total, porque cada importe de renglón ya viene sin IVA.
    #
    # El PDF a veces trae el número del subtotal con un espacio suelto
    # en medio (ej. "$ 5 72,088.24" en vez de "$ 572,088.24" -- artefacto
    # de cómo el PDF codifica ese texto), así que el patrón permite
    # espacios/tabs sueltos dentro del número (pero no saltos de línea,
    # para no cruzarse con la línea del IVA que le sigue).
    coincidencia_subtotal = re.search(
        r"Subtotal[ \t]*\$?[ \t]*(\d[\d,\t ]*\.\d{2})",
        texto_para_detectar_formato,
        flags=re.IGNORECASE,
    )
    subtotal_declarado = (
        convertir_numero(coincidencia_subtotal.group(1))
        if coincidencia_subtotal
        else None
    )

    def _suma_importes(filas):
        total = 0.0
        for f in filas:
            importe = convertir_numero(f.get("importe"))
            if importe is None:
                cantidad = convertir_numero(f.get("cantidad"))
                precio = convertir_numero(f.get("precio_unitario"))
                if cantidad is not None and precio is not None:
                    importe = cantidad * precio
                else:
                    importe = 0.0
            total += importe
        return total

    metodo_por_subtotal = None
    if subtotal_declarado:
        mejor_diferencia = None
        for nombre, filas in (
            ("tabla", filas_tabla),
            ("obra", filas_obra),
            ("odoo", filas_odoo),
        ):
            if not filas:
                continue
            diferencia = abs(_suma_importes(filas) - subtotal_declarado)
            if mejor_diferencia is None or diferencia < mejor_diferencia:
                mejor_diferencia = diferencia
                metodo_por_subtotal = nombre
        # Tolerancia de $1 (redondeos de centavos). Si ni el que más se
        # acerca cuadra de verdad, no forzar esta señal: mejor caer al
        # criterio de respaldo de abajo.
        if mejor_diferencia is None or mejor_diferencia >= 1.0:
            metodo_por_subtotal = None

    # IMPORTANTE: el subtotal por sí solo NO basta para decidir. Un método
    # de texto (TERCER/CUARTO INTENTO) puede sumar exactamente el
    # subtotal correcto renglón por renglón (los números de cada línea se
    # leen bien) pero con las DESCRIPCIONES desfasadas una posición
    # (porque el texto de una celda que ocupa varias líneas se agrupó con
    # el renglón de números equivocado) -- en ese caso el resultado
    # "cuadra" en dinero pero cada partida queda con el texto de otra, lo
    # cual es inútil para buscar coincidencias en la base de precios. Por
    # eso primero se prioriza la extracción de tabla real (PRIMER
    # INTENTO, basada en las líneas de borde que trae el PDF), que
    # respeta la celda de descripción de cada renglón tal cual está en el
    # documento y no depende de heurísticas de texto: si encontró un
    # número de renglones razonable frente a los otros métodos (>=90%) Y
    # su suma no se aleja mucho del subtotal (dentro de 2%, para tolerar
    # como mucho 1-2 renglones que la tabla no haya podido leer por un
    # salto de página), se usa esa. Solo si la tabla no es confiable se
    # cae al criterio de subtotal exacto entre los métodos de texto, y si
    # tampoco hay subtotal, al conteo de renglones.
    max_otros = max(len(filas_obra), len(filas_odoo))
    tabla_confiable = False
    if filas_tabla and len(filas_tabla) >= max_otros * 0.9:
        if subtotal_declarado:
            diferencia_relativa = (
                abs(_suma_importes(filas_tabla) - subtotal_declarado)
                / subtotal_declarado
            )
            tabla_confiable = diferencia_relativa <= 0.02
        else:
            tabla_confiable = True

    if tabla_confiable:
        filas_validas = filas_tabla
    elif metodo_por_subtotal == "tabla":
        filas_validas = filas_tabla
    elif metodo_por_subtotal == "obra":
        filas_validas = filas_obra
    elif metodo_por_subtotal == "odoo":
        filas_validas = filas_odoo
    elif len(filas_odoo) > len(filas_obra) and len(filas_odoo) > len(filas_tabla):
        filas_validas = filas_odoo
    elif len(filas_obra) > len(filas_tabla):
        filas_validas = filas_obra
    else:
        filas_validas = filas_tabla
    # ======================================================
    # VALIDACIÓN Y LIMPIEZA FINAL
    # ======================================================

    if not filas_validas:
        raise ValueError(
            "No se detectaron partidas válidas en el PDF. "
            "El documento puede estar escaneado o tener "
            "una estructura diferente."
        )

    resultado = pd.DataFrame(
        filas_validas
    )

    resultado["cantidad"] = pd.to_numeric(
        resultado["cantidad"],
        errors="coerce",
    )

    resultado["precio_unitario"] = pd.to_numeric(
        resultado["precio_unitario"],
        errors="coerce",
    )

    resultado["importe"] = pd.to_numeric(
        resultado["importe"],
        errors="coerce",
    )

    importe_calculado = (
        resultado["cantidad"]
        * resultado["precio_unitario"]
    )

    resultado["importe"] = resultado["importe"].fillna(
        importe_calculado
    )

    resultado = resultado.drop_duplicates(
        subset=[
            "partida",
            "concepto",
            "unidad",
            "precio_unitario",
        ],
        keep="first",
    )

    resultado = resultado.sort_values(
        by="partida",
        key=lambda serie: pd.to_numeric(
            serie,
            errors="coerce",
        ),
    )

    resultado = resultado.reset_index(
        drop=True
    )

    metadatos = {
        "tipo": "PDF digital",
        "hoja_detectada": (
            f"{len(paginas_detectadas)} páginas con partidas"
        ),
        "partidas": len(resultado),
        "confianza": (
            95 if len(resultado) > 0 else 0
        ),
    }

    # Verificación contra el SUBTOTAL del PDF (no el Total), reutilizando
    # el valor ya extraído arriba para decidir el método. El importe de
    # cada renglón es antes de impuestos -- el "IVA(16%)" que aparece
    # junto a cada partida es solo la tasa aplicable, no un monto ya
    # sumado al importe de esa fila -- así que la suma de todos los
    # importes debe cuadrar con el Subtotal de la cotización, NO con el
    # Total (que ya trae el IVA sumado). Comparar contra el Total aquí
    # daría una diferencia falsa del ~16% y haría parecer que la lectura
    # del PDF falló cuando en realidad está correcta.
    if subtotal_declarado:
        suma_importes = float(
            resultado["importe"].sum()
        )

        diferencia = abs(
            suma_importes - subtotal_declarado
        )

        metadatos["subtotal_declarado"] = subtotal_declarado
        metadatos["suma_importes_detectados"] = round(
            suma_importes, 2
        )
        # Tolerancia de $1: redondeos de centavos entre renglones.
        metadatos["coincide_con_subtotal"] = diferencia < 1.0

        if diferencia < 1.0:
            metadatos["confianza"] = max(
                metadatos["confianza"], 98
            )

    return resultado, metadatos

# ==========================================================
# IDENTIFICACIÓN DE FORMATO
# ==========================================================


def cargar_y_normalizar_archivo(archivo):
    extension = Path(
        archivo.name
    ).suffix.lower()

    if extension in {
        ".xlsx",
        ".xlsm",
        ".xls",
    }:
        return leer_excel(
            archivo
        )

    if extension == ".csv":
        return leer_csv(
            archivo
        )

    if extension == ".pdf":
        return leer_pdf(
            archivo
        )

    raise ValueError(
        f"El formato {extension} todavía no está soportado."
    )


# ==========================================================
# BARRA LATERAL
# ==========================================================

with st.sidebar:

    st.subheader(
        "Datos de esta cotización"
    )

    proveedor = st.text_input(
        "Proveedor",
        placeholder="Nombre del proveedor",
    )

    proyecto = st.text_input(
        "Proyecto / licitación",
        placeholder="Ej. Planta Norte 2026",
    )

    guardar_en_historico = st.checkbox(
        "Guardar esta cotización en el histórico",
        value=True,
        disabled=historico is None,
        help=(
            "Cada partida quedará guardada para "
            "comparaciones futuras."
        ),
    )

    ajustar_inflacion = st.checkbox(
        "Ajustar precios viejos de NL por inflación",
        value=True,
        help=(
            "Los precios históricos de Nuevo León "
            "se actualizan usando INPC."
        ),
    )

    usar_ia = st.checkbox(
        "Activar revisión con IA (segunda opinión)",
        value=ia_disponible,
        disabled=not ia_disponible,
        help=(
            "Para coincidencias de confianza BAJA, le pide a la IA "
            "que confirme si de verdad es el mismo material. Para "
            "partidas sin ningún dato de referencia, da una opinión "
            "orientativa (NO un precio verificado) sobre si el precio "
            "cotizado suena razonable. Requiere una API key de "
            "Gemini o de OpenAI configurada en Secrets."
        ),
    )

    if not ia_disponible:

        st.caption(
            "Revisión con IA no conectada. Falta configurar "
            "gemini_api_key u openai_api_key en Secrets."
        )

    if historico is None:

        st.caption(
            "Histórico interno no conectado. "
            "Falta configurar Google Sheets en Secrets."
        )

    else:

        resumen = historico.resumen()

        st.caption(
            f"Histórico interno: "
            f"{resumen['total_renglones']} renglones · "
            f"{len(resumen['proveedores'])} proveedores · "
            f"{len(resumen['proyectos'])} proyectos"
        )

    if inpc_en_vivo:

        st.caption(
            f"INPC: dato en vivo de INEGI "
            f"({ajuste_inflacion.ETIQUETA_ACTUAL}, "
            f"nivel {ajuste_inflacion.NIVEL_ACTUAL})."
        )

    else:

        st.caption(
            f"INPC: usando valor de respaldo "
            f"({ajuste_inflacion.ETIQUETA_ACTUAL}, "
            f"nivel {ajuste_inflacion.NIVEL_ACTUAL}). "
            "Configura 'inegi_api_token' en Secrets "
            "para traerlo automático."
        )


# ==========================================================
# INFORMACIÓN DEL FORMATO
# ==========================================================

with st.expander(
    "Formatos y estructura reconocida"
):

    st.write(
        "La aplicación detecta automáticamente la hoja, "
        "la fila de encabezados y los nombres de las columnas."
    )

    st.write(
        "Formatos admitidos:"
    )

    st.code(
        "Excel: .xlsx, .xls, .xlsm\n"
        "Datos: .csv\n"
        "Documento: .pdf digital",
        language="text",
    )

    st.write(
        "Campos internos utilizados:"
    )

    st.code(
        "partida | concepto | unidad | cantidad | "
        "precio_unitario | importe",
        language="text",
    )

    ejemplo = pd.DataFrame(
        [
            {
                "partida": "001",
                "concepto": (
                    "Suministro y colocación de acero "
                    "de refuerzo en losas"
                ),
                "unidad": "KG",
                "cantidad": 1000,
                "precio_unitario": 30,
                "importe": 30000,
            },
            {
                "partida": "002",
                "concepto": (
                    "Limpieza final de obra"
                ),
                "unidad": "M2",
                "cantidad": 500,
                "precio_unitario": 9,
                "importe": 4500,
            },
        ]
    )

    st.dataframe(
        ejemplo,
        use_container_width=True,
        hide_index=True,
    )


# ==========================================================
# CARGADOR DE ARCHIVOS
# ==========================================================

archivo = st.file_uploader(
    "Sube tu cotización o licitación",
    type=[
        "xlsx",
        "xls",
        "xlsm",
        "csv",
        "pdf",
    ],
    help=(
        "La aplicación buscará automáticamente "
        "la tabla de partidas."
    ),
)


# ==========================================================
# PROCESAMIENTO DEL ARCHIVO
# ==========================================================

if archivo is not None:

    try:

        with st.spinner(
            "Leyendo el archivo y detectando las partidas..."
        ):

            cotizacion, metadatos = (
                cargar_y_normalizar_archivo(
                    archivo
                )
            )

        st.success(
            "Archivo interpretado correctamente. "
            f"Se detectaron {len(cotizacion)} partidas."
        )

        c1, c2, c3 = st.columns(
            3
        )

        c1.metric(
            "Tipo de archivo",
            metadatos.get(
                "tipo",
                "",
            ),
        )

        c2.metric(
            "Hoja o sección",
            metadatos.get(
                "hoja_detectada",
                "",
            ),
        )

        c3.metric(
            "Confianza de lectura",
            (
                f"{metadatos.get('confianza', 0)}%"
            ),
        )

        st.subheader(
            "Vista previa de las partidas detectadas"
        )

        columnas_vista = [
            "partida",
            "concepto",
            "unidad",
            "cantidad",
            "precio_unitario",
            "importe",
            "origen",
        ]

        st.dataframe(
            cotizacion[
                columnas_vista
            ].head(100),
            use_container_width=True,
            hide_index=True,
        )

        if metadatos.get(
            "confianza",
            0,
        ) < 80:

            st.warning(
                "La confianza de lectura es baja. "
                "Revisa la vista previa antes de continuar."
            )

        if "coincide_con_subtotal" in metadatos:

            if metadatos["coincide_con_subtotal"]:

                st.caption(
                    "✅ La suma de los renglones detectados "
                    "coincide con el Subtotal del documento "
                    f"(${metadatos['subtotal_declarado']:,.2f})."
                )

            else:

                st.warning(
                    "La suma de los renglones detectados "
                    f"(${metadatos['suma_importes_detectados']:,.2f}) "
                    "no coincide con el Subtotal del documento "
                    f"(${metadatos['subtotal_declarado']:,.2f}). "
                    "Puede faltar o sobrar alguna partida: revisa la "
                    "vista previa."
                )

        confirmar = st.checkbox(
            "Confirmo que las partidas detectadas son correctas",
            value=False,
        )

        if confirmar:

            with st.spinner(
                f"Revisando {len(cotizacion)} partidas "
                "contra NL, CDMX e histórico interno..."
            ):

                filas = []

                for _, renglon in cotizacion.iterrows():

                    precio = convertir_numero(
                        renglon[
                            "precio_unitario"
                        ]
                    )

                    if (
                        precio is None
                        or precio <= 0
                    ):
                        continue

                    concepto = str(
                        renglon[
                            "concepto"
                        ]
                    ).strip()

                    unidad = str(
                        renglon[
                            "unidad"
                        ]
                    ).strip()

                    resultado = comparador.evaluar(
                        concepto,
                        unidad,
                        precio,
                        ajustar_inflacion=(
                            ajustar_inflacion
                        ),
                        usar_ia=usar_ia,
                    )

                    nl = resultado[
                        "fuentes"
                    ][
                        "nl_historico"
                    ]

                    cdmx = resultado[
                        "fuentes"
                    ][
                        "cdmx_gobierno"
                    ]

                    fila = {
                        "Partida": renglon.get(
                            "partida"
                        ),
                        "Concepto": concepto,
                        "Unidad": unidad,
                        "Cantidad": renglon.get(
                            "cantidad"
                        ),
                        "Precio cotizado": precio,
                        "Importe": renglon.get(
                            "importe"
                        ),
                        "Origen": renglon.get(
                            "origen"
                        ),
                        "Match NL": nl.get(
                            "match"
                        ),
                        "Confianza NL": nl.get(
                            "confianza"
                        ),
                        "Año del dato NL": nl.get(
                            "anio_dato_mas_reciente"
                        ),
                        (
                            "Precio mediana NL "
                            "(original)"
                        ): nl.get(
                            "precio_mediana"
                        ),
                        (
                            "Precio mediana NL "
                            "(ajustado hoy)"
                        ): nl.get(
                            "precio_mediana_ajustada"
                        ),
                        "Veredicto NL": nl.get(
                            "clasificacion"
                        ),
                        "Match CDMX": cdmx.get(
                            "match"
                        ),
                        "Confianza CDMX": cdmx.get(
                            "confianza"
                        ),
                        (
                            "Precio referencia CDMX"
                        ): cdmx.get(
                            "precio_referencia"
                        ),
                        "Veredicto CDMX": cdmx.get(
                            "clasificacion"
                        ),
                    }

                    revision_ia_nl = nl.get("revision_ia")
                    if revision_ia_nl:
                        fila["Revisión IA (match NL débil)"] = (
                            f"{revision_ia_nl['veredicto']}: "
                            f"{revision_ia_nl['razon']}"
                        )

                    revision_ia_cdmx = cdmx.get("revision_ia")
                    if revision_ia_cdmx:
                        fila["Revisión IA (match CDMX débil)"] = (
                            f"{revision_ia_cdmx['veredicto']}: "
                            f"{revision_ia_cdmx['razon']}"
                        )

                    opinion_ia = resultado.get("opinion_ia_sin_datos")
                    if opinion_ia:
                        fila["Opinión IA (sin datos verificados)"] = (
                            f"{opinion_ia['opinion']}: "
                            f"{opinion_ia['razon']}"
                        )

                    # Si la IA revisó un match débil y lo RECHAZÓ, ese
                    # precio de referencia ya está confirmado como
                    # incorrecto -- no debe contar para el resultado
                    # final aunque el buscador de texto lo haya
                    # encontrado. Se deja visible en su columna de
                    # detalle (Veredicto NL / Veredicto CDMX) para que
                    # quede claro qué se descartó y por qué.
                    nl_rechazado_por_ia = (
                        revision_ia_nl
                        and revision_ia_nl.get("veredicto") == "RECHAZA"
                    )
                    cdmx_rechazado_por_ia = (
                        revision_ia_cdmx
                        and revision_ia_cdmx.get("veredicto") == "RECHAZA"
                    )

                    if nl_rechazado_por_ia and fila.get("Veredicto NL"):
                        fila["Veredicto NL"] = (
                            f"{fila['Veredicto NL']} "
                            "(descartado: la IA rechazó el match)"
                        )

                    if cdmx_rechazado_por_ia and fila.get("Veredicto CDMX"):
                        fila["Veredicto CDMX"] = (
                            f"{fila['Veredicto CDMX']} "
                            "(descartado: la IA rechazó el match)"
                        )

                    clasificaciones = [
                        valor
                        for valor in (
                            nl.get("clasificacion")
                            if not nl_rechazado_por_ia
                            else None,
                            cdmx.get("clasificacion")
                            if not cdmx_rechazado_por_ia
                            else None,
                        )
                        if valor
                    ]

                    # Precio(s) de referencia de las mismas fuentes que sí
                    # cuentan para el veredicto (se excluyen las que la IA
                    # rechazó) -- sirve para calcular el % de diferencia
                    # de la partida contra el mercado.
                    referencias_precio = [
                        valor
                        for valor in (
                            nl.get("precio_mediana_ajustada")
                            if not nl_rechazado_por_ia
                            else None,
                            cdmx.get("precio_referencia")
                            if not cdmx_rechazado_por_ia
                            else None,
                        )
                        if valor
                    ]

                    if historico is not None:

                        consulta_historico = (
                            historico.consultar(
                                concepto,
                                unidad,
                                precio,
                                usar_ia=usar_ia,
                            )
                        )

                        if consulta_historico.get(
                            "match"
                        ):

                            fila[
                                "Match histórico interno"
                            ] = consulta_historico[
                                "match"
                            ]

                            fila[
                                "Confianza histórico interno"
                            ] = consulta_historico.get(
                                "confianza"
                            )

                            fila[
                                "Proveedores en histórico"
                            ] = ", ".join(
                                consulta_historico.get(
                                    "proveedores",
                                    [],
                                )
                            )

                            fila[
                                "Precio mediana histórico"
                            ] = consulta_historico.get(
                                "precio_mediana"
                            )

                            veredicto_historico = (
                                consulta_historico.get(
                                    "clasificacion"
                                )
                            )

                            revision_ia_historico = (
                                consulta_historico.get(
                                    "revision_ia"
                                )
                            )

                            if revision_ia_historico:
                                fila[
                                    "Revisión IA (match histórico débil)"
                                ] = (
                                    f"{revision_ia_historico['veredicto']}: "
                                    f"{revision_ia_historico['razon']}"
                                )

                            historico_rechazado_por_ia = (
                                revision_ia_historico
                                and revision_ia_historico.get(
                                    "veredicto"
                                ) == "RECHAZA"
                            )

                            if (
                                historico_rechazado_por_ia
                                and veredicto_historico
                            ):
                                veredicto_historico = (
                                    f"{veredicto_historico} "
                                    "(descartado: la IA rechazó el match)"
                                )

                            fila[
                                "Veredicto histórico"
                            ] = veredicto_historico

                            if (
                                consulta_historico.get(
                                    "clasificacion"
                                )
                                and not historico_rechazado_por_ia
                            ):

                                clasificaciones.append(
                                    consulta_historico[
                                        "clasificacion"
                                    ]
                                )

                                if consulta_historico.get(
                                    "precio_mediana"
                                ):
                                    referencias_precio.append(
                                        consulta_historico[
                                            "precio_mediana"
                                        ]
                                    )

                    if clasificaciones:

                        conteo = {
                            clasificacion: (
                                clasificaciones.count(
                                    clasificacion
                                )
                            )
                            for clasificacion
                            in set(
                                clasificaciones
                            )
                        }

                        fila[
                            "RESULTADO FINAL"
                        ] = max(
                            conteo,
                            key=conteo.get,
                        )

                    else:

                        fila[
                            "RESULTADO FINAL"
                        ] = (
                            "SIN DATOS SUFICIENTES"
                        )

                    # % de diferencia del precio cotizado contra el
                    # promedio de los precios de referencia que sí
                    # contaron para el veredicto (positivo = más caro
                    # que el mercado, negativo = más barato). Si no hubo
                    # ninguna fuente confiable, se deja en blanco -- no
                    # hay con qué comparar.
                    if referencias_precio:

                        precio_referencia_promedio = (
                            sum(referencias_precio)
                            / len(referencias_precio)
                        )

                        if precio_referencia_promedio:

                            fila[
                                "% Diferencia vs referencia"
                            ] = round(
                                (
                                    precio
                                    - precio_referencia_promedio
                                )
                                / precio_referencia_promedio
                                * 100,
                                1,
                            )

                        else:

                            fila[
                                "% Diferencia vs referencia"
                            ] = None

                    else:

                        fila[
                            "% Diferencia vs referencia"
                        ] = None

                    filas.append(
                        fila
                    )

                tabla = pd.DataFrame(
                    filas
                )

                cotizacion_historico = cotizacion[
                    [
                        "concepto",
                        "unidad",
                        "precio_unitario",
                    ]
                ].copy()

                if (
                    historico is not None
                    and guardar_en_historico
                ):

                    if (
                        not proveedor
                        or not proyecto
                    ):

                        st.warning(
                            "Escribe el proveedor y el proyecto "
                            "en la barra lateral para guardar "
                            "la cotización en el histórico."
                        )

                    else:

                        historico.ingerir(
                            cotizacion_historico,
                            proveedor=proveedor,
                            proyecto=proyecto,
                        )

                        st.success(
                            "Cotización guardada en el "
                            "histórico interno. "
                            f"{len(cotizacion_historico)} "
                            "partidas agregadas."
                        )

            if tabla.empty:

                st.error(
                    "No quedaron partidas válidas "
                    "para revisar."
                )

            else:

                resumen_veredictos = tabla[
                    "RESULTADO FINAL"
                ].value_counts()

                c1, c2, c3, c4, c5 = st.columns(
                    5
                )

                c1.metric(
                    "Partidas revisadas",
                    len(tabla),
                )

                c2.metric(
                    "Altas",
                    int(
                        resumen_veredictos.get(
                            "ALTO",
                            0,
                        )
                    ),
                )

                c3.metric(
                    "Bajas",
                    int(
                        resumen_veredictos.get(
                            "BAJO",
                            0,
                        )
                    ),
                )

                c4.metric(
                    "En mercado",
                    int(
                        resumen_veredictos.get(
                            "EN MERCADO",
                            0,
                        )
                    ),
                )

                c5.metric(
                    "Sin datos",
                    int(
                        resumen_veredictos.get(
                            "SIN DATOS SUFICIENTES",
                            0,
                        )
                    ),
                )

                def resaltar(valor):

                    if valor == "ALTO":
                        return (
                            "background-color: #f7c1c1; "
                            "color: #501313"
                        )

                    if valor == "BAJO":
                        return (
                            "background-color: #ffe699; "
                            "color: #7f6000"
                        )

                    if valor == "EN MERCADO":
                        return (
                            "background-color: #c6e0b4; "
                            "color: #006100"
                        )

                    if (
                        valor
                        == "SIN DATOS SUFICIENTES"
                    ):
                        return (
                            "background-color: #d9d9d9; "
                            "color: #595959"
                        )

                    return ""

                def resaltar_diferencia(valor):

                    if valor is None or pd.isna(valor):
                        return ""

                    if valor > 0:
                        return "color: #c0392b"

                    if valor < 0:
                        return "color: #1e8449"

                    return ""

                st.dataframe(
                    tabla.style.map(
                        resaltar,
                        subset=[
                            "RESULTADO FINAL"
                        ],
                    ).map(
                        resaltar_diferencia,
                        subset=[
                            "% Diferencia vs referencia"
                        ],
                    ).format(
                        {
                            "% Diferencia vs referencia": (
                                lambda v: (
                                    f"{v:+.1f}%"
                                    if pd.notna(v)
                                    else ""
                                )
                            )
                        }
                    ),
                    use_container_width=True,
                    height=min(
                        650,
                        60 + 35 * len(
                            tabla
                        ),
                    ),
                )

                buffer = io.BytesIO()

                tabla.to_excel(
                    buffer,
                    index=False,
                    engine="openpyxl",
                )

                nombre_proveedor = (
                    proveedor.strip()
                    if proveedor
                    else "proveedor"
                )

                nombre_proyecto = (
                    proyecto.strip()
                    if proyecto
                    else "proyecto"
                )

                nombre_proveedor = re.sub(
                    r"[^a-zA-Z0-9_-]+",
                    "_",
                    nombre_proveedor,
                )

                nombre_proyecto = re.sub(
                    r"[^a-zA-Z0-9_-]+",
                    "_",
                    nombre_proyecto,
                )

                st.download_button(
                    "Descargar resultado de la revisión",
                    data=buffer.getvalue(),
                    file_name=(
                        f"revision_{nombre_proveedor}_"
                        f"{nombre_proyecto}.xlsx"
                    ),
                    mime=(
                        "application/vnd.openxmlformats-"
                        "officedocument.spreadsheetml.sheet"
                    ),
                )

                # ==========================================================
                # GRÁFICA RESUMEN (mismo conteo de arriba, en dona con %)
                # ==========================================================

                orden_categorias = [
                    "ALTO",
                    "BAJO",
                    "EN MERCADO",
                    "SIN DATOS SUFICIENTES",
                ]

                nombres_categorias = {
                    "ALTO": "Alto",
                    "BAJO": "Bajo",
                    "EN MERCADO": "En mercado",
                    "SIN DATOS SUFICIENTES": "Sin datos",
                }

                # Mismos colores que ya se usan para resaltar la tabla,
                # asi la grafica se ve consistente con "RESULTADO FINAL".
                colores_categorias = {
                    "ALTO": "#f7c1c1",
                    "BAJO": "#ffe699",
                    "EN MERCADO": "#c6e0b4",
                    "SIN DATOS SUFICIENTES": "#d9d9d9",
                }

                etiquetas_grafica = []
                valores_grafica = []
                colores_grafica = []

                for categoria in orden_categorias:
                    conteo = int(
                        resumen_veredictos.get(
                            categoria, 0
                        )
                    )
                    if conteo > 0:
                        etiquetas_grafica.append(
                            nombres_categorias[categoria]
                        )
                        valores_grafica.append(conteo)
                        colores_grafica.append(
                            colores_categorias[categoria]
                        )

                if valores_grafica:

                    st.subheader(
                        "Distribución de resultados"
                    )

                    figura_resumen = go.Figure(
                        data=[
                            go.Pie(
                                labels=etiquetas_grafica,
                                values=valores_grafica,
                                marker=dict(
                                    colors=colores_grafica,
                                    line=dict(
                                        color="#ffffff",
                                        width=2,
                                    ),
                                ),
                                textinfo="label+percent+value",
                                hole=0.45,
                            )
                        ]
                    )

                    figura_resumen.update_layout(
                        showlegend=True,
                        margin=dict(
                            t=10, b=10, l=10, r=10
                        ),
                        height=380,
                    )

                    st.plotly_chart(
                        figura_resumen,
                        use_container_width=True,
                    )

    except Exception as error:

        st.error(
            "No fue posible interpretar "
            "automáticamente el archivo."
        )

        st.exception(
            error
        )

        st.info(
            "Revisa que el archivo contenga una tabla "
            "con concepto, unidad y precio unitario. "
            "Los PDF escaneados todavía requieren OCR."
        )


# ==========================================================
# INFORMACIÓN DE FUENTES
# ==========================================================

st.divider()

st.caption(
    "Fuentes: histórico de licitaciones de obra pública "
    "de Nuevo León, Tabulador General de Precios Unitarios "
    "del Gobierno de la Ciudad de México e histórico interno "
    "guardado en Google Sheets."
)

st.caption(
    "El ajuste por inflación utiliza el INPC como "
    "aproximación para actualizar precios antiguos. "
    "No sustituye un índice específico de construcción."
)
