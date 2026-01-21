import streamlit as st
import pandas as pd
import json
from pathlib import Path
import unicodedata, re

# ================ CONFIG ================
st.set_page_config(page_title="Painel de Bônus - Tokyo (T4)", layout="wide")
st.title("🚀 Painel de Bônus Trimestral - Tokyo")

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

# ================ HELPERS DE TEXTO ================
def norm_txt(s: str) -> str:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    s = str(s).strip().upper()
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = re.sub(r"\s+", " ", s)
    return s

def up(s):
    return norm_txt(s)

def is_org_loja(item: str) -> bool:
    k = norm_txt(item)
    return "ORGANIZACAO DA LOJA" in k

def is_lider_org(item: str) -> bool:
    k = norm_txt(item)
    return ("LIDERANCA" in k) and ("ORGANIZACAO" in k)

def texto_obs(valor):
    if pd.isna(valor):
        return ""
    s = str(valor).strip()
    return "" if s.lower() in ["nan", "none", ""] else s

def int_safe(x):
    try:
        return int(float(x))
    except Exception:
        return 0

def pct_safe(x):
    """
    Garante que o valor venha como FRAÇÃO.
    - Se vier 0.035, mantém (3,5%)
    - Se vier 3.5, converte para 0.035
    """
    try:
        x = float(x)
        if x > 1:
            return x / 100.0
        return x
    except Exception:
        return 0.0

def fmt_pct(x):
    try:
        return f"{float(x) * 100:.2f}%"
    except Exception:
        return "0.00%"

# ================ SUPERVISOR(ES) E SUAS CIDADES ================
# MADSON: Produção (20%) dividida igualmente: 10% Ribamar + 10% Chapadinha
_SUPERVISORES_CIDADES_RAW = {
    "MADSON RONNY PEREIRA MELO": {
        "SÃO JOSÉ DE RIBAMAR": 0.5,   # 50% da parcela de Produção
        "CHAPADINHA": 0.5            # 50% da parcela de Produção
    }
}
SUPERVISORES_CIDADES = {
    norm_txt(nome): {norm_txt(cidade): peso for cidade, peso in cidades.items()}
    for nome, cidades in _SUPERVISORES_CIDADES_RAW.items()
}

# ================ LOAD JSONS ================
def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

try:
    PESOS = load_json(DATA_DIR / "pesos_tokyofilial.json")  # pesos e metas por função
    INDICADORES = load_json(DATA_DIR / "empresa_indicadores_tokyofilial.json")  # status por mês/cidade
except Exception as e:
    st.error(f"Erro ao carregar JSONs: {e}")
    st.stop()

MESES = ["TRIMESTRE", "JULHO", "AGOSTO", "SETEMBRO"]
filtro_mes = st.radio("📅 Selecione o mês:", MESES, horizontal=True)

def ler_planilha(mes: str) -> pd.DataFrame:
    base = DATA_DIR / "RESUMO PARA PAINEL - TOKYO FILIAL.xlsx"
    if base.exists():
        return pd.read_excel(base, sheet_name=mes)
    candidatos = list(DATA_DIR.glob("RESUMO PARA PAINEL - TOKYO FILIAL*.xls*"))
    if not candidatos:
        st.error("Planilha não encontrada na pasta data/")
        st.stop()
    return pd.read_excel(sorted(candidatos)[0], sheet_name=mes)

# ================ REGRAS (QUALIDADE EM %) POR CIDADE ================
# BARRA DO CORDA - 3,5% / 1,5%
# CHAPADINHA     - 5%   / 2%
# SANTA INÊS     - 3,5% / 1,5%
# SÃO JOÃO DOS PATOS - 3,5% / 1,5%
# SÃO JOSÉ DE RIBAMAR - 3,5% / 1,5%
LIMITES_QUALIDADE_POR_CIDADE = {
    up("BARRA DO CORDA"): {"total": 0.035, "graves": 0.015},
    up("CHAPADINHA"): {"total": 0.05, "graves": 0.02},
    up("SANTA INÊS"): {"total": 0.035, "graves": 0.015},
    up("SÃO JOÃO DOS PATOS"): {"total": 0.035, "graves": 0.015},
    up("SÃO JOSÉ DE RIBAMAR"): {"total": 0.035, "graves": 0.015},
}

# fallback (se a cidade vier diferente/sem cadastro)
LIMITE_TOTAL_PADRAO = 0.035
LIMITE_GRAVES_PADRAO = 0.015

def limites_qualidade(cidade: str):
    c = up(cidade)
    cfg = LIMITES_QUALIDADE_POR_CIDADE.get(c)
    if cfg:
        return float(cfg["total"]), float(cfg["graves"])
    return LIMITE_TOTAL_PADRAO, LIMITE_GRAVES_PADRAO

def pct_qualidade_vistoriador(erros_total_frac: float, erros_graves_frac: float, limite_total: float, limite_graves: float) -> float:
    """
    Retorna 0.0, 0.5 ou 1.0 para o indicador 'Qualidade' do Vistoriador
    usando limites por cidade (frações).
    """
    et = 0.0 if pd.isna(erros_total_frac) else float(erros_total_frac)
    eg = 0.0 if pd.isna(erros_graves_frac) else float(erros_graves_frac)

    total_ok = et <= float(limite_total)
    graves_ok = eg <= float(limite_graves)

    if total_ok and graves_ok:
        return 1.0
    if (not total_ok and graves_ok) or (total_ok and not graves_ok):
        return 0.5
    return 0.0

def elegivel(valor_meta, obs):
    obs_u = norm_txt(obs)
    if pd.isna(valor_meta) or float(valor_meta) == 0:
        return False, "Sem elegibilidade no mês"
    if "LICEN" in obs_u:
        return False, "Licença no mês"
    return True, ""

# ================ CÁLCULO (por mês) ================
def calcula_mes(df_mes, nome_mes):
    ind_mes_raw = INDICADORES[nome_mes]
    ind_flags = {norm_txt(k): v for k, v in ind_mes_raw.items() if k != "producao_por_cidade"}
    prod_cid_norm = {norm_txt(k): v for k, v in ind_mes_raw["producao_por_cidade"].items()}

    def flag(chave: str, default=True):
        return ind_flags.get(norm_txt(chave), default)

    df = df_mes.copy()

    def calcula_recebido(row):
        func = norm_txt(row["FUNÇÃO"])
        cidade = norm_txt(row["CIDADE"])
        nome  = norm_txt(row.get("NOME", ""))
        obs = row.get("OBSERVAÇÃO", "")
        valor_meta = row.get("VALOR MENSAL META", 0)

        ok, motivo = elegivel(valor_meta, obs)
        perdeu_itens = []

        if not ok:
            return pd.Series({
                "MES": nome_mes, "META": 0.0, "RECEBIDO": 0.0, "PERDA": 0.0, "%": 0.0,
                "_badge": motivo, "_obs": texto_obs(obs), "perdeu_itens": perdeu_itens
            })

        metainfo = PESOS.get(row["FUNÇÃO"], {})  # usa a chave original do arquivo
        total_func = float(metainfo.get("total", valor_meta))
        itens = metainfo.get("metas", {})

        recebido, perdas = 0.0, 0.0

        for item, peso in itens.items():
            parcela = total_func * float(peso)
            item_norm = norm_txt(item)

            # --- PRODUÇÃO ---
            if item_norm.startswith(norm_txt("Produção")):
                if func == norm_txt("SUPERVISOR") and nome in SUPERVISORES_CIDADES:
                    perdas_cids = []
                    for cid_norm, w in SUPERVISORES_CIDADES[nome].items():
                        bateu = prod_cid_norm.get(cid_norm, True)
                        if bateu:
                            recebido += parcela * float(w)
                        else:
                            perdas   += parcela * float(w)
                            perdas_cids.append(cid_norm.title())
                    if perdas_cids:
                        perdeu_itens.append("Produção – " + ", ".join(perdas_cids))
                else:
                    bateu_prod = prod_cid_norm.get(cidade, True)
                    if bateu_prod:
                        recebido += parcela
                    else:
                        perdas += parcela
                        perdeu_itens.append("Produção – " + (row["CIDADE"].title() if row["CIDADE"] else "Cidade não informada"))
                continue

            # --- QUALIDADE (AGORA EM % E POR CIDADE) ---
            if item_norm == norm_txt("Qualidade"):
                if func == norm_txt("VISTORIADOR"):
                    et_frac = pct_safe(row.get("ERROS TOTAL", 0))
                    eg_frac = pct_safe(row.get("ERROS GG", 0))

                    lim_total, lim_graves = limites_qualidade(row.get("CIDADE", ""))
                    frac = pct_qualidade_vistoriador(et_frac, eg_frac, lim_total, lim_graves)

                    if frac == 1.0:
                        recebido += parcela
                    elif frac == 0.5:
                        recebido += parcela * 0.5
                        perdas += parcela * 0.5
                        perdeu_itens.append(
                            f"Qualidade (50%) — total {fmt_pct(et_frac)} | graves {fmt_pct(eg_frac)} "
                            f"(meta: {fmt_pct(lim_total)} / {fmt_pct(lim_graves)})"
                        )
                    else:
                        perdas += parcela
                        perdeu_itens.append(
                            f"Qualidade (0%) — total {fmt_pct(et_frac)} | graves {fmt_pct(eg_frac)} "
                            f"(meta: {fmt_pct(lim_total)} / {fmt_pct(lim_graves)})"
                        )
                else:
                    if flag("qualidade", True):
                        recebido += parcela
                    else:
                        perdas += parcela
                        perdeu_itens.append("Qualidade")
                continue

            # --- LUCRATIVIDADE (depende de Financeiro) ---
            if item_norm == norm_txt("Lucratividade"):
                if flag("financeiro", True):
                    recebido += parcela
                else:
                    perdas += parcela
                    perdeu_itens.append("Lucratividade")
                continue

            # --- ORGANIZAÇÃO DA LOJA 5s (empresa-wide) ---
            if is_org_loja(item):
                if flag("organizacao_da_loja", True):
                    recebido += parcela
                else:
                    perdas += parcela
                    perdeu_itens.append("Organização da Loja 5s")
                continue

            # --- LIDERANÇA & ORGANIZAÇÃO (empresa-wide) ---
            if is_lider_org(item):
                if flag("Liderança & Organização", True):
                    recebido += parcela
                else:
                    perdas += parcela
                    perdeu_itens.append("Liderança & Organização")
                continue

            # --- demais metas: considerar batidas ---
            recebido += parcela

        meta = total_func
        perc = 0 if meta == 0 else (recebido / meta) * 100
        return pd.Series({
            "MES": nome_mes, "META": meta, "RECEBIDO": recebido, "PERDA": perdas,
            "%": perc, "_badge": "", "_obs": texto_obs(obs), "perdeu_itens": perdeu_itens
        })

    calc = df.apply(calcula_recebido, axis=1)
    return pd.concat([df.reset_index(drop=True), calc], axis=1)

# ================ TRIMESTRE OU MÊS ================
if filtro_mes == "TRIMESTRE":
    df_j, df_a, df_s = [ler_planilha(m) for m in ["JULHO", "AGOSTO", "SETEMBRO"]]
    st.success("✅ Planilhas carregadas (TRIMESTRE)")
    dados_full = pd.concat([
        calcula_mes(df_j, "JULHO"),
        calcula_mes(df_a, "AGOSTO"),
        calcula_mes(df_s, "SETEMBRO")
    ], ignore_index=True)

    group_cols = ["CIDADE", "NOME", "FUNÇÃO", "DATA DE ADMISSÃO", "TEMPO DE CASA"]
    agg = (dados_full
           .groupby(group_cols, dropna=False)
           .agg({"META": "sum", "RECEBIDO": "sum", "PERDA": "sum",
                 "_obs": lambda x: ", ".join(sorted({s for s in x if s})),
                 "_badge": lambda x: " / ".join(sorted({s for s in x if s}))})
           .reset_index())
    agg["%"] = agg.apply(lambda r: 0 if r["META"] == 0 else (r["RECEBIDO"]/r["META"])*100, axis=1)

    perdas_pessoa = (
        dados_full.assign(_lost=lambda d: d.apply(
            lambda r: [f"{it} ({r['MES']})" for it in r["perdeu_itens"]],
            axis=1))
        .groupby(group_cols, dropna=False)["_lost"]
        .sum()
        .apply(lambda L: ", ".join(sorted(set(L))))
        .reset_index()
        .rename(columns={"_lost": "INDICADORES_NAO_ENTREGUES"})
    )
    dados_calc = agg.merge(perdas_pessoa, on=group_cols, how="left").fillna("")
else:
    df_mes = ler_planilha(filtro_mes)
    st.success(f"✅ Planilha carregada ({filtro_mes})")
    dados_calc = calcula_mes(df_mes, filtro_mes)
    dados_calc["INDICADORES_NAO_ENTREGUES"] = dados_calc["perdeu_itens"].apply(
        lambda L: ", ".join(L) if isinstance(L, list) and L else ""
    )

# ================ FILTROS VISUAIS ================
st.markdown("### 🔎 Filtros")
c1, c2, c3, c4 = st.columns(4)
with c1:
    f_nome = st.text_input("Buscar por nome", "")
with c2:
    f_func = st.selectbox("Função", ["Todas"] + sorted(PESOS.keys()))
with c3:
    f_cid = st.selectbox("Cidade", ["Todas"] + sorted(dados_calc["CIDADE"].dropna().unique()))
with c4:
    f_tempo = st.selectbox("Tempo de casa", ["Todos"] + sorted(dados_calc["TEMPO DE CASA"].dropna().unique()))

dados_view = dados_calc.copy()
if f_nome:
    dados_view = dados_view[dados_view["NOME"].str.contains(f_nome, case=False, na=False)]
if f_func != "Todas":
    dados_view = dados_view[dados_view["FUNÇÃO"] == f_func]
if f_cid != "Todas":
    dados_view = dados_view[dados_view["CIDADE"] == f_cid]
if f_tempo != "Todos":
    dados_view = dados_view[dados_view["TEMPO DE CASA"] == f_tempo]

# ================ RESUMO ====================
st.markdown("### 📊 Resumo Geral")
r1, r2, r3 = st.columns(3)
with r1: st.success(f"💰 Total possível: R$ {dados_view['META'].sum():,.2f}")
with r2: st.info(f"📈 Recebido: R$ {dados_view['RECEBIDO'].sum():,.2f}")
with r3: st.error(f"📉 Deixou de ganhar: R$ {dados_view['PERDA'].sum():,.2f}")

# ================ CARDS ====================
st.markdown("### 👥 Colaboradores")
cols = st.columns(3)
dados_view = dados_view.sort_values(by="%", ascending=False)

for idx, row in dados_view.iterrows():
    pct = float(row["%"])
    meta = float(row["META"])
    recebido = float(row["RECEBIDO"])
    perdido = float(row["PERDA"])
    badge = row.get("_badge", "")
    obs_txt = texto_obs(row.get("_obs", ""))
    perdidos_txt = texto_obs(row.get("INDICADORES_NAO_ENTREGUES", ""))

    bg = "#f9f9f9" if not badge else "#eeeeee"
    with cols[idx % 3]:
        st.markdown(f"""
        <div style="border:1px solid #ccc;padding:16px;border-radius:12px;margin-bottom:12px;background:{bg}">
            <h4>{row['NOME'].title()}</h4>
            <p><strong>{row['FUNÇÃO']}</strong> — {row['CIDADE']}</p>
            <p><strong>Meta {'Trimestral' if filtro_mes=='TRIMESTRE' else 'Mensal'}:</strong> R$ {meta:,.2f}<br>
            <strong>Recebido:</strong> R$ {recebido:,.2f}<br>
            <strong>Deixou de ganhar:</strong> R$ {perdido:,.2f}<br>
            <strong>Cumprimento:</strong> {pct:.1f}%</p>
            <div style="height:10px;background:#ddd;border-radius:5px;overflow:hidden;">
                <div style="width:{pct:.1f}%;background:black;height:100%;"></div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        if badge:
            st.caption(f"⚠️ {badge}")
        if obs_txt:
            st.caption(f"🗒️ {obs_txt}")
        if perdidos_txt and "100%" not in perdidos_txt:
            st.caption(f"🔻 Indicadores não entregues: {perdidos_txt}")