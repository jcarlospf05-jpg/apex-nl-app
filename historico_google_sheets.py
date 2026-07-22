"""
Historico interno respaldado en Google Sheets (memoria persistente de verdad)
================================================================================

Misma idea que historico_interno.py, pero en vez de guardar en un Excel local
(que se pierde entre sesiones de Colab/Streamlit), lee y escribe directo en
una Google Sheet - asi la memoria sobrevive sin importar donde corras el
codigo (Colab, tu compu, o la app en Streamlit Cloud).

Requiere:
    pip install gspread google-auth

Como se autentica (dos formas, usa la que aplique):

  A) Local / Colab: descarga el JSON de la cuenta de servicio y pasa la RUTA
     del archivo:
         hist = HistoricoGoogleSheets(sheet_id="TU_SHEET_ID", creds_path="credenciales.json")

  B) Streamlit Cloud: el JSON se guarda como "secret" (Settings -> Secrets) y
     se lee como diccionario:
         hist = HistoricoGoogleSheets(sheet_id="TU_SHEET_ID", creds_dict=st.secrets["gcp_service_account"])

La Google Sheet debe:
  - tener una pestaña llamada "Historico"
  - tener en la fila 1 estos encabezados exactos:
    fecha_carga | proyecto | proveedor | concepto | concepto_norm | unidad | unidad_norm | precio_unitario | cluster_id
  - estar compartida (permiso Editor) con el correo de la cuenta de servicio
    (algo como capex-sheets-bot@TU-PROYECTO.iam.gserviceaccount.com)
"""

import re
import unicodedata
from datetime import datetime

import gspread
import pandas as pd
from google.oauth2.service_account import Credentials
from rapidfuzz import fuzz, process

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

COLUMNAS = ['fecha_carga', 'proyecto', 'proveedor', 'concepto', 'concepto_norm',
            'unidad', 'unidad_norm', 'precio_unitario', 'cluster_id']

LEADING_CODE_RE = re.compile(r'^\s*[\d.]{1,15}\s+')
WS_RE = re.compile(r'\s+')
NUM_RE = re.compile(r'\d+(?:\.\d+)?')


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


def numeric_signature(t: str):
    nums = NUM_RE.findall(t)
    return tuple(sorted(round(float(n), 1) for n in nums))


def clasificar(precio, low, high):
    if precio < low:
        return 'BAJO'
    if precio > high:
        return 'ALTO'
    return 'EN MERCADO'


class HistoricoGoogleSheets:
    def __init__(self, sheet_id: str, creds_path: str = None, creds_dict: dict = None,
                 sim_threshold: float = 72.0):
        if creds_path:
            creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)
        elif creds_dict:
            creds = Credentials.from_service_account_info(dict(creds_dict), scopes=SCOPES)
        else:
            raise ValueError("Pasa creds_path (archivo JSON local) o creds_dict (st.secrets en Streamlit)")

        self.client = gspread.authorize(creds)
        self.sheet = self.client.open_by_key(sheet_id).worksheet("Historico")
        self.sim_threshold = sim_threshold
        self._cargar()

    def _cargar(self):
        registros = self.sheet.get_all_records()
        if registros:
            self.df = pd.DataFrame(registros)
            for col in COLUMNAS:
                if col not in self.df.columns:
                    self.df[col] = None
            self.df['cluster_id'] = pd.to_numeric(self.df['cluster_id'], errors='coerce').fillna(0).astype(int)
            self.df['precio_unitario'] = pd.to_numeric(self.df['precio_unitario'], errors='coerce')
        else:
            self.df = pd.DataFrame(columns=COLUMNAS)
        self._next_cluster_id = (self.df['cluster_id'].max() + 1) if len(self.df) else 0

    def _homologar_uno(self, concepto_norm: str, unidad_norm: str) -> int:
        firma = numeric_signature(concepto_norm)
        candidatos = self.df[self.df['unidad_norm'] == unidad_norm]
        if not candidatos.empty:
            candidatos = candidatos.copy()
            candidatos['_firma'] = candidatos['concepto_norm'].map(numeric_signature)
            candidatos = candidatos[candidatos['_firma'] == firma]
        if candidatos.empty:
            cid = self._next_cluster_id
            self._next_cluster_id += 1
            return cid
        mejor_cid, mejor_score = None, -1
        for cid, grupo in candidatos.groupby('cluster_id'):
            rep = grupo['concepto_norm'].mode().iloc[0]
            score = fuzz.token_set_ratio(concepto_norm, rep)
            if score > mejor_score:
                mejor_score, mejor_cid = score, cid
        if mejor_score >= self.sim_threshold:
            return int(mejor_cid)
        cid = self._next_cluster_id
        self._next_cluster_id += 1
        return cid

    def ingerir(self, datos, proveedor: str, proyecto: str) -> pd.DataFrame:
        """Agrega las partidas de una cotizacion/licitacion nueva y las escribe
        de inmediato en la Google Sheet (persistente)."""
        if isinstance(datos, pd.DataFrame):
            nuevo = datos.copy()
        elif str(datos).lower().endswith('.csv'):
            nuevo = pd.read_csv(datos)
        else:
            nuevo = pd.read_excel(datos)

        faltantes = {'concepto', 'unidad', 'precio_unitario'} - set(nuevo.columns)
        if faltantes:
            raise ValueError(f"Faltan columnas obligatorias: {faltantes}")

        nuevo['concepto_norm'] = nuevo['concepto'].map(normalize_text)
        nuevo['unidad_norm'] = nuevo['unidad'].map(normalize_unit)
        nuevo['proveedor'] = proveedor
        nuevo['proyecto'] = proyecto
        nuevo['fecha_carga'] = datetime.now().strftime('%Y-%m-%d')

        filas_nuevas = []
        for _, r in nuevo.iterrows():
            cid = self._homologar_uno(r['concepto_norm'], r['unidad_norm'])
            fila = {**r.to_dict(), 'cluster_id': cid}
            self.df = pd.concat([self.df, pd.DataFrame([fila])[COLUMNAS]], ignore_index=True)
            filas_nuevas.append([fila[c] for c in COLUMNAS])

        # escribe solo las filas nuevas al final de la hoja (rapido, no reescribe todo)
        self.sheet.append_rows(filas_nuevas, value_input_option="USER_ENTERED")

        nuevo['cluster_id'] = [f[-1] for f in filas_nuevas]
        return nuevo[COLUMNAS]

    def consultar(self, descripcion: str, unidad: str, precio_cotizado: float = None,
                  min_score: float = 70.0) -> dict:
        t = normalize_text(descripcion)
        u = normalize_unit(unidad)
        pool = self.df[self.df['unidad_norm'] == u]
        if pool.empty:
            return {'match': None, 'motivo': f'historico vacio o sin unidad {u} todavia'}

        choices = pool['concepto_norm'].tolist()
        result = process.extractOne(t, choices, scorer=fuzz.token_set_ratio, score_cutoff=min_score)
        if result is None:
            return {'match': None, 'motivo': f'sin coincidencia >= {min_score}% en el historico'}
        _, score, idx = result
        cid = pool.iloc[idx]['cluster_id']
        grupo = self.df[self.df['cluster_id'] == cid]

        out = {
            'match': grupo['concepto'].mode().iloc[0], 'score': round(float(score), 1),
            'n_registros': len(grupo), 'n_proveedores': grupo['proveedor'].nunique(),
            'proveedores': sorted(grupo['proveedor'].unique().tolist()),
            'precio_min': float(grupo['precio_unitario'].min()),
            'precio_mediana': float(grupo['precio_unitario'].median()),
            'precio_max': float(grupo['precio_unitario'].max()),
        }
        if precio_cotizado is not None and len(grupo) >= 2:
            low, high = grupo['precio_unitario'].quantile(0.25), grupo['precio_unitario'].quantile(0.75)
            out['clasificacion'] = clasificar(precio_cotizado, low, high)
        elif precio_cotizado is not None:
            out['clasificacion'] = None
            out['nota'] = 'solo 1 registro historico: referencia puntual, no banda estadistica'
        return out

    def resumen(self) -> dict:
        return {
            'total_renglones': len(self.df),
            'conceptos_homologados': self.df['cluster_id'].nunique() if len(self.df) else 0,
            'proveedores': sorted(self.df['proveedor'].dropna().unique().tolist()) if len(self.df) else [],
            'proyectos': sorted(self.df['proyecto'].dropna().unique().tolist()) if len(self.df) else [],
        }
