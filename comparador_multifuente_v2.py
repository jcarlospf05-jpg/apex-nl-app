"""
Comparador multi-fuente - Revision economica de cotizaciones/licitaciones CAPEX
=================================================================================

Evalua una partida cotizada contra TODAS las fuentes de precio disponibles y
regresa un veredicto por fuente + un veredicto combinado.

Fuentes ya integradas (datos reales, con URL/origen verificable):
  1. NL historico (Tabulador Homologado NL) - contratos reales de obra publica
     de Nuevo Leon 2021-2025 (SIASI / Open Contracting Partnership).
  2. CDMX gobierno (Tabulador CDMX) - tabulador oficial de precios unitarios
     del Gobierno de la Ciudad de Mexico, edicion 2026.

Fuentes con interfaz lista pero SIN datos todavia (no se inventa nada):
  3. Historico Ragasa - requiere que Ragasa proporcione su propio historico
     de compras/ordenes de compra (concepto, unidad, precio, fecha).
  4. Comparacion entre proveedores de la misma licitacion - requiere las
     propuestas economicas reales de los demas participantes en la licitacion
     que se esta revisando.

Uso:
    from comparador_multifuente_v2 import ComparadorMultiFuente

    c = ComparadorMultiFuente("Base_Precios_Unitarios_NL_CDMX.xlsx")
    veredicto = c.evaluar(
        descripcion="Suministro y colocacion de acero de refuerzo en losas, varilla corrugada",
        unidad="KG",
        precio_cotizado=30.0,
    )
    print(veredicto)
"""

import re
import unicodedata
import pandas as pd
from rapidfuzz import fuzz, process

LEADING_CODE_RE = re.compile(r'^\s*[\d.]{1,15}\s+')
WS_RE = re.compile(r'\s+')


def _strip_accents(s: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFKD', s) if not unicodedata.combining(c))


def normalize_text(raw: str) -> str:
    if not isinstance(raw, str):
        return ''
    t = raw.strip()
    t = LEADING_CODE_RE.sub('', t)
    t = _strip_accents(t.upper())
    t = re.sub(r'[^A-Z0-9%./\-\s]', ' ', t)
    return WS_RE.sub(' ', t).strip()


def normalize_unit(u: str) -> str:
    if not isinstance(u, str) or not u.strip():
        return 'SIN_UNIDAD'
    return u.strip().upper().replace('.', '')


def clasificar(precio: float, low: float, high: float) -> str:
    if precio < low:
        return 'BAJO'
    if precio > high:
        return 'ALTO'
    return 'EN MERCADO'


class ComparadorMultiFuente:
    def __init__(self, excel_path: str):
        self.nl = pd.read_excel(excel_path, sheet_name='Tabulador Homologado NL')
        # concepto_homologado ya viene normalizado (mayusculas, sin acentos) desde el homologador
        self.nl['concepto_norm'] = self.nl['concepto_homologado'].map(normalize_text)

        self.cdmx = pd.read_excel(excel_path, sheet_name='Tabulador CDMX (gobierno)')
        self.cdmx['unidad_norm'] = self.cdmx['unidad'].map(normalize_unit)
        self.cdmx['concepto_norm'] = self.cdmx['concepto'].map(normalize_text)

        self._nl_pools = {u: df for u, df in self.nl.groupby('unidad')}
        self._cdmx_pools = {u: df for u, df in self.cdmx.groupby('unidad_norm')}

        # Fuentes pendientes de datos reales del usuario:
        self.ragasa = None
        self.competidores = None

    def cargar_ragasa(self, df_o_ruta):
        """Conecta el historico real de compras de Ragasa cuando este disponible.
        Se espera columnas: concepto, unidad, precio_unitario, fecha (opcional)."""
        self.ragasa = df_o_ruta if isinstance(df_o_ruta, pd.DataFrame) else pd.read_excel(df_o_ruta)
        self.ragasa['concepto_norm'] = self.ragasa['concepto'].map(normalize_text)
        self.ragasa['unidad_norm'] = self.ragasa['unidad'].map(normalize_unit)

    def cargar_competidores(self, df_o_ruta):
        """Conecta las propuestas economicas reales de otros proveedores de la
        MISMA licitacion que se esta revisando (para comparar entre pares).
        Se espera columnas: proveedor, concepto, unidad, precio_unitario."""
        self.competidores = df_o_ruta if isinstance(df_o_ruta, pd.DataFrame) else pd.read_excel(df_o_ruta)
        self.competidores['concepto_norm'] = self.competidores['concepto'].map(normalize_text)
        self.competidores['unidad_norm'] = self.competidores['unidad'].map(normalize_unit)

    def _match_pool(self, pools, t, u, min_score, scorer, text_col='concepto_norm'):
        pool = pools.get(u)
        if pool is None or pool.empty:
            return None
        choices = pool[text_col].tolist()
        result = process.extractOne(t, choices, scorer=scorer, score_cutoff=min_score)
        if result is None:
            return None
        _, score, idx = result
        row = pool.iloc[idx]
        return float(score), row

    def evaluar(self, descripcion: str, unidad: str, precio_cotizado: float,
                min_score: float = 70.0, scorer=fuzz.token_set_ratio) -> dict:
        t = normalize_text(descripcion)
        u = normalize_unit(unidad)

        resultado = {
            'entrada': {'descripcion': descripcion, 'unidad': u, 'precio_cotizado': precio_cotizado},
            'fuentes': {},
        }

        # --- Fuente 1: NL historico ---
        m = self._match_pool(self._nl_pools, t, u, min_score, scorer)
        if m:
            score, row = m
            veredicto = clasificar(precio_cotizado, row['precio_p25'], row['precio_p75'])
            resultado['fuentes']['nl_historico'] = {
                'match': row['concepto_homologado'], 'score': round(score, 1),
                'precio_min': float(row['precio_min']), 'precio_p25': float(row['precio_p25']),
                'precio_mediana': float(row['precio_mediana']), 'precio_p75': float(row['precio_p75']),
                'precio_max': float(row['precio_max']), 'n_registros': int(row['n_registros']),
                'variabilidad': row['variabilidad'], 'clasificacion': veredicto,
            }
        else:
            resultado['fuentes']['nl_historico'] = {'match': None, 'motivo': f'sin coincidencia >= {min_score}% en unidad {u}'}

        # --- Fuente 2: CDMX gobierno ---
        m = self._match_pool(self._cdmx_pools, t, u, min_score, scorer)
        if m:
            score, row = m
            precio_ref = float(row['precio_unitario'])
            low, high = precio_ref * 0.85, precio_ref * 1.15
            resultado['fuentes']['cdmx_gobierno'] = {
                'match': row['concepto'], 'score': round(score, 1), 'clave': row['clave'],
                'precio_referencia': precio_ref, 'banda_baja': round(low, 2), 'banda_alta': round(high, 2),
                'clasificacion': clasificar(precio_cotizado, low, high),
            }
        else:
            resultado['fuentes']['cdmx_gobierno'] = {'match': None, 'motivo': f'sin coincidencia >= {min_score}% en unidad {u}'}

        # --- Fuente 3: Ragasa (requiere datos reales del usuario) ---
        if self.ragasa is not None:
            pools = {uu: dfx for uu, dfx in self.ragasa.groupby('unidad_norm')}
            m = self._match_pool(pools, t, u, min_score, scorer)
            if m:
                score, row = m
                resultado['fuentes']['ragasa_historico'] = {
                    'match': row['concepto'], 'score': round(score, 1),
                    'precio_historico': float(row['precio_unitario']),
                    'clasificacion': clasificar(precio_cotizado, row['precio_unitario'] * 0.9, row['precio_unitario'] * 1.1),
                }
            else:
                resultado['fuentes']['ragasa_historico'] = {'match': None, 'motivo': 'sin coincidencia en historico Ragasa'}
        else:
            resultado['fuentes']['ragasa_historico'] = {
                'match': None,
                'motivo': 'PENDIENTE: no se ha cargado el historico de compras de Ragasa. '
                          'Usa comparador.cargar_ragasa(ruta_o_dataframe) con datos reales para activar esta fuente.'
            }

        # --- Fuente 4: comparacion entre proveedores de la misma licitacion ---
        if self.competidores is not None:
            comp = self.competidores[self.competidores['unidad_norm'] == u].copy()
            if not comp.empty:
                comp['_score'] = comp['concepto_norm'].map(lambda x: scorer(t, x))
                comp = comp[comp['_score'] >= min_score]
            if not comp.empty:
                resultado['fuentes']['comparacion_proveedores'] = {
                    'n_proveedores_comparables': int(comp['proveedor'].nunique()),
                    'precio_min': float(comp['precio_unitario'].min()),
                    'precio_mediana': float(comp['precio_unitario'].median()),
                    'precio_max': float(comp['precio_unitario'].max()),
                    'clasificacion': clasificar(precio_cotizado, comp['precio_unitario'].quantile(0.25), comp['precio_unitario'].quantile(0.75)),
                    'detalle': comp[['proveedor', 'precio_unitario']].to_dict('records'),
                }
            else:
                resultado['fuentes']['comparacion_proveedores'] = {'match': None, 'motivo': 'sin otras propuestas comparables para este concepto'}
        else:
            resultado['fuentes']['comparacion_proveedores'] = {
                'match': None,
                'motivo': 'PENDIENTE: no se han cargado las propuestas de otros proveedores de esta licitacion. '
                          'Usa comparador.cargar_competidores(ruta_o_dataframe) con las cotizaciones reales para activar esta fuente.'
            }

        clasificaciones = [f['clasificacion'] for f in resultado['fuentes'].values() if isinstance(f, dict) and f.get('clasificacion')]
        if clasificaciones:
            conteo = {c: clasificaciones.count(c) for c in set(clasificaciones)}
            resultado['veredicto_combinado'] = max(conteo, key=conteo.get)
            resultado['fuentes_consultadas'] = len(clasificaciones)
            resultado['coincidencia_entre_fuentes'] = len(set(clasificaciones)) == 1
        else:
            resultado['veredicto_combinado'] = 'SIN DATOS SUFICIENTES'
            resultado['fuentes_consultadas'] = 0

        return resultado


if __name__ == '__main__':
    import sys
    import json
    excel = sys.argv[1] if len(sys.argv) > 1 else 'Base_Precios_Unitarios_NL_CDMX.xlsx'
    c = ComparadorMultiFuente(excel)

    ejemplos = [
        ("Suministro y colocacion de acero de refuerzo en losas, varilla corrugada", "KG", 30),
        ("Limpieza final de obra durante todo el periodo de ejecucion", "M2", 9),
        ("Anteproyecto de muro de contencion, primeros 100 m2", "m2", 90),
    ]
    for desc, unidad, precio in ejemplos:
        print('=' * 100)
        print('ENTRADA:', desc, '|', unidad, '| cotizado:', precio)
        print(json.dumps(c.evaluar(desc, unidad, precio), indent=2, ensure_ascii=False, default=str))
        print()
