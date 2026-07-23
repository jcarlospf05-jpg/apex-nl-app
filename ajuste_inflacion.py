"""
Ajuste por inflacion (INPC - INEGI) para precios historicos de Nuevo Leon
==========================================================================

La base de Nuevo Leon casi no tiene informacion de 2024 en adelante, porque
el gobierno del estado no ha publicado licitaciones mas recientes en SIASI
(ver hoja "Fuente y Metodologia"). Para no comparar un precio de 2021 contra
una cotizacion de 2026 "en crudo", este modulo escala los precios viejos a su
equivalente de hoy usando el Indice Nacional de Precios al Consumidor (INPC)
que publica el INEGI - el mismo tipo de indice que se usa en la industria de
la construccion en Mexico para "escalatorias" de contratos (Art. 58 de la Ley
de Obras Publicas y Servicios Relacionados con las Mismas).

Ningun numero esta inventado: los niveles de INPC son los publicados de forma
oficial por el INEGI en sus boletines de prensa mensuales (fuente:
https://www.inegi.org.mx/temas/inpc/). Boletines usados:

  dic-2021: 117.314  (Boletin 9/22,   INEGI, 7 ene 2022)
  dic-2022: 126.539  (Boletin 11/23,  INEGI, 9 ene 2023)
  dic-2023: 132.373  (Boletin s/n,    INEGI, 9 ene 2024)
  dic-2024: 137.977  (Boletin 7/25,   INEGI, 9 ene 2025)
  dic-2025: 143.042  (Boletin 6/26,   INEGI, 8 ene 2026)
  jun-2026: 145.131  (Boletin 417/26, INEGI, 9 jul 2026)  <- dato mas reciente disponible

Limitacion reconocida (importante para no sobre-vender la precision de esto):
el INPC es un indice de precios al consumidor (canasta de gasto de los
hogares: comida, renta, transporte, etc.), NO un indice especifico de
insumos de construccion. El INEGI si publica un indice especializado para
eso (INPP - "Insumos de Obras Publicas"), pero no existe un portal publico
que permita descargar su serie historica completa de forma sencilla y
gratuita como el INPC. Usar el INPC es la aproximacion estandar cuando no
se tiene acceso al INPP, pero puede sub-estimar o sobre-estimar el alza real
de materiales muy volatiles (acero, cobre, cemento, combustibles).
"""

# Nivel del INPC (base: 2a quincena de julio de 2018 = 100).
# Fuente: boletines de prensa del INEGI, https://www.inegi.org.mx/temas/inpc/
INPC_NIVEL_DICIEMBRE = {
    2021: 117.314,
    2022: 126.539,
    2023: 132.373,
    2024: 137.977,
    2025: 143.042,
}

# Dato mensual mas reciente disponible (se actualiza a mano cuando se
# refresque esta base con un INPC mas nuevo).
NIVEL_ACTUAL = 145.131
ETIQUETA_ACTUAL = "junio 2026"

FUENTE = "INEGI, Indice Nacional de Precios al Consumidor (INPC): https://www.inegi.org.mx/temas/inpc/"


def _anio_valido(anio) -> int:
    try:
        return int(str(anio)[:4])
    except (TypeError, ValueError):
        return max(INPC_NIVEL_DICIEMBRE)


def factor_ajuste(anio) -> float:
    """Factor multiplicador para llevar un precio de 'anio' a su equivalente
    en NIVEL_ACTUAL (INPC general INEGI). Ej: un precio de 2021 se multiplica
    por ~1.237 para estimar su equivalente en junio de 2026."""
    anio = _anio_valido(anio)
    anios_disponibles = sorted(INPC_NIVEL_DICIEMBRE)
    if anio < anios_disponibles[0]:
        anio = anios_disponibles[0]
    elif anio > anios_disponibles[-1]:
        anio = anios_disponibles[-1]
    base = INPC_NIVEL_DICIEMBRE[anio]
    return NIVEL_ACTUAL / base


def ajustar_precio(precio: float, anio) -> float:
    if precio is None:
        return None
    try:
        return round(float(precio) * factor_ajuste(anio), 2)
    except (TypeError, ValueError):
        return None
