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
import streamlit as st

from comparador_multifuente_v2 import ComparadorMultiFuente


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
    # Detectar cotizaciones de obra tipo SARO.
    # En este formato, la extracción automática de tablas
    # puede mezclar las descripciones de las partidas.
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

    es_cotizacion_obra = (
        "clave descripcion unidad cantidad "
        "precio unitario importe"
        in encabezado_normalizado
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

    if not filas_validas:

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

        patron_datos_partida = re.compile(
            r"^(.*?)"
            r"\b(M2|M3|ML|M|PZA|PZAS|SERVICIO|LOTE|KG|TON)\b"
            r"\s+([\d,]+(?:\.\d+)?)"
            r"\s+\$?\s*([\d,]+\.\d{2})"
            r"\s+\$?\s*([\d,]+\.\d{2})$",
            flags=re.IGNORECASE,
        )

        patron_clave = re.compile(
            r"^(\d+(?:\.\d+)+)\s*(.*)$"
        )

        encabezados_seccion = [
            "preliminares",
            "banqueta",
            "limpieza fina",
        ]

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
            "tiempo de ejecucion",
            "condiciones de pago",
            "duracion de propuesta",
            "garantia",
            "alcance",
            "correo",
            "telefono",
            "celular",
        ]

        descripcion_acumulada = []
        clave_pendiente = None
        capturando_partida = False

        for numero_pagina, linea in lineas_pdf:

            linea_normalizada = normalizar_texto(
                linea
            )
            # Detectar claves como:
            # 1.1
            # 1.2 EXCAVACIÓN...
            # 1.3 RELLENO...
            coincidencia_clave = re.match(
                r"^(\d+(?:\.\d+)+)\b\s*(.*)$",
                linea,
            )

            if coincidencia_clave:
                clave_pendiente = coincidencia_clave.group(1)
                descripcion_acumulada = []
                capturando_partida = True

                texto_despues_clave = (
                    coincidencia_clave.group(2).strip()
                )

                if texto_despues_clave:
                    descripcion_acumulada.append(
                        texto_despues_clave
                    )

                continue

            # Iniciar partidas sin clave visible:
            # BANQUETA y LIMPIEZA FINA.
            if re.match(
                r"^BANQUETA(?:\s+\$[\d,]+\.\d{2})?$",
                linea,
                flags=re.IGNORECASE,
            ):
                clave_pendiente = str(
                    len(filas_validas) + 1
                )
                descripcion_acumulada = []
                capturando_partida = True
                continue

            if re.match(
                r"^LIMPIEZA FINA(?:\s+\$[\d,]+\.\d{2})?$",
                linea,
                flags=re.IGNORECASE,
            ):
                clave_pendiente = str(
                    len(filas_validas) + 1
                )
                descripcion_acumulada = []
                capturando_partida = True
                continue

            coincidencia = patron_datos_partida.match(
                linea
            )

            if coincidencia and capturando_partida:

                descripcion_en_linea = (
                    coincidencia.group(1).strip()
                )

                partes_descripcion = list(
                    descripcion_acumulada
                )

                if descripcion_en_linea:
                    partes_descripcion.append(
                        descripcion_en_linea
                    )

                descripcion = re.sub(
                    r"\s+",
                    " ",
                    " ".join(partes_descripcion),
                ).strip()

                unidad = normalizar_unidad(
                    coincidencia.group(2)
                )

                cantidad = convertir_numero(
                    coincidencia.group(3)
                )

                precio_unitario = convertir_numero(
                    coincidencia.group(4)
                )

                importe = convertir_numero(
                    coincidencia.group(5)
                )

                if (
                    descripcion
                    and precio_unitario is not None
                    and precio_unitario > 0
                ):
                    filas_validas.append(
                        {
                            "partida": (
                                clave_pendiente
                                if clave_pendiente
                                else str(
                                    len(filas_validas) + 1
                                )
                            ),
                            "concepto": descripcion,
                            "unidad": unidad,
                            "cantidad": cantidad,
                            "precio_unitario": precio_unitario,
                            "importe": importe,
                            "origen": (
                                f"Página {numero_pagina}"
                            ),
                            "fila_encabezado": None,
                            "puntaje_deteccion": 8,
                        }
                    )

                    paginas_detectadas.add(
                        numero_pagina
                    )

                descripcion_acumulada = []
                clave_pendiente = None
                capturando_partida = False
                continue

            # Solo acumular texto cuando ya comenzó una partida.
            if not capturando_partida:
                continue

            # Evitar encabezados y datos generales.
            if any(
                texto in linea_normalizada
                for texto in textos_ignorar
            ):
                continue

            descripcion_acumulada.append(
                linea
            )
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
                        (
                            "Precio referencia CDMX"
                        ): cdmx.get(
                            "precio_referencia"
                        ),
                        "Veredicto CDMX": cdmx.get(
                            "clasificacion"
                        ),
                    }

                    clasificaciones = [
                        valor
                        for valor in (
                            nl.get(
                                "clasificacion"
                            ),
                            cdmx.get(
                                "clasificacion"
                            ),
                        )
                        if valor
                    ]

                    if historico is not None:

                        consulta_historico = (
                            historico.consultar(
                                concepto,
                                unidad,
                                precio,
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

                            fila[
                                "Veredicto histórico"
                            ] = consulta_historico.get(
                                "clasificacion"
                            )

                            if consulta_historico.get(
                                "clasificacion"
                            ):

                                clasificaciones.append(
                                    consulta_historico[
                                        "clasificacion"
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
                            "Veredicto final"
                        ] = max(
                            conteo,
                            key=conteo.get,
                        )

                    else:

                        fila[
                            "Veredicto final"
                        ] = (
                            "SIN DATOS SUFICIENTES"
                        )

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
                    "Veredicto final"
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

                st.dataframe(
                    tabla.style.map(
                        resaltar,
                        subset=[
                            "Veredicto final"
                        ],
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
