# -*- coding: utf-8 -*-
"""
Atualizador do Monitor MPO — Requerimentos de Informação (RIC).

COMO GARANTE "TODOS OS RICs":
A fonte primária deixa de ser o endpoint paginado e passa a ser o ARQUIVO ANUAL
COMPLETO dos Dados Abertos:
    https://dadosabertos.camara.leg.br/arquivos/proposicoes/json/proposicoes-{ano}.json
Esse arquivo (atualização diária, completo de 2001 em diante) traz TODAS as
proposições do ano — em qualquer situação, com ementa, ementaDetalhada, keywords e a
tramitação mais recente (ultimoStatus). Lendo o arquivo, vemos o universo inteiro de
RICs do ano e aplicamos o filtro do MPO sobre ele, sem paginação e sem descartar
requerimentos já respondidos ou arquivados. Se o arquivo do ano falhar, há fallback
para o endpoint paginado (método antigo), preservando o funcionamento.

Filtro do destinatário (precisão + recall): casa quando o RIC é DIRIGIDO ao
Planejamento e Orçamento (nome exclusivo da pasta, ministro(a) do Planejamento, sigla
MPO isolada ou "Simone Tebet"), com guarda que rejeita o caso em que o sinal aparece
apenas no objeto do pedido e o destinatário é outra pasta.

Temas: classificação oficial do CEDOC via /proposicoes/{id}/temas; reserva por
palavra-chave só quando o CEDOC ainda não classificou.
"""

import re
import json
import time
import unicodedata
from datetime import datetime, timezone, timedelta
from collections import Counter

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ==========================================
# SESSÃO HTTP
# ==========================================
session = requests.Session()
retry = Retry(connect=5, read=5, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
adapter = HTTPAdapter(max_retries=retry)
session.mount("http://", adapter)
session.mount("https://", adapter)

API = "https://dadosabertos.camara.leg.br/api/v2"
ARQUIVO_ANO = "https://dadosabertos.camara.leg.br/arquivos/proposicoes/json/proposicoes-{ano}.json"

# ==========================================
# PARÂMETROS
# ==========================================
ANOS_BUSCA = [2023, 2024, 2025, 2026]  # legislatura atual; amplie se quiser histórico
DELAY_API = 0.4                         # respiro entre chamadas de enriquecimento
APENAS_ATIVOS = False                   # False = captura TODOS os RICs (recomendado)

MESES_BR = {1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr", 5: "Mai", 6: "Jun",
            7: "Jul", 8: "Ago", 9: "Set", 10: "Out", 11: "Nov", 12: "Dez"}

SITUACOES_ATIVAS = [
    "aguardando designacao de relator", "aguardando encaminhamento",
    "aguardando envio ao executivo", "aguardando resposta", "aguardando recebimento",
    "aguardando despacho do presidente da camara dos deputados",
    "aguardando deliberacao", "pronta para pauta", "aguardando parecer",
]

# ==========================================
# TEXTO / DESTINATÁRIO
# ==========================================
def _norm(texto):
    texto = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", texto.lower()).strip()

_VERBOS = r"(?:requer(?:imento)?|requeiro|solicit[ao]|solicito|encaminh\w+)"
_CORTE = (r"(?:\bacerca\b|\bsobre\b|\ba respeito\b|\breferente\b|\bno sentido\b|"
          r"\bquanto\b|\bque preste|\binforma\w+ sobre|\binforma\w+ acerca|\brelativ\w+\b|,)")

# Núcleos distintivos de OUTRAS pastas (guarda de precisão).
_OUTRAS_PASTAS = (r"(fazenda|saude|educacao|justica|seguranca publica|relacoes exteriores|"
                  r"minas e energia|agricultura|pecuaria|desenvolvimento agrario|trabalho e emprego|"
                  r"previdencia|portos|aeroportos|meio ambiente|casa civil|comunicacao social|"
                  r"relacoes institucionais|gestao e inovacao|industria|comercio|defesa|transportes|"
                  r"cidades|turismo|cultura|esporte|direitos humanos|povos indigenas|"
                  r"desenvolvimento social|ciencia|tecnologia|pesca)")

def _trecho_destinatario(ementa):
    e = _norm(ementa)
    m = re.search(_VERBOS, e)
    if m:
        e = e[m.end():]
    c = re.search(_CORTE, e)
    if c:
        e = e[:c.start()]
    return e

def _sinal_mpo(txt):
    t = _norm(txt)
    return bool(
        "planejamento e orcamento" in t
        or re.search(r"ministr[oa]s?(?: de estado)?(?: d[oae])? planejamento", t)
        or "ministerio do planejamento" in t
        or "simone tebet" in t or "simone nassar tebet" in t
        or re.search(r"\bmpo\b", t)
    )

def dirigido_ao_mpo(ementa, ementa_det="", keywords=""):
    """True quando o RIC é dirigido ao MPO. Usa ementa, ementa detalhada e keywords
    para recall; usa o trecho do destinatário para precisão."""
    if not _sinal_mpo(f"{ementa} {ementa_det} {keywords}"):
        return False
    dest = _trecho_destinatario(ementa)
    if _sinal_mpo(dest):
        return True
    # sinal só fora do destinatário: rejeita se o destinatário nomeia outra pasta
    if re.search(_OUTRAS_PASTAS, dest):
        return False
    return True

# ==========================================
# DATA / SITUAÇÃO / TEMA
# ==========================================
def parse_data(v):
    """Aceita epoch (ms), 'YYYY-MM-DD[ T]HH:MM:SS[.f]' e 'YYYY-MM-DD'."""
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v / 1000, tz=timezone.utc).replace(tzinfo=None)
    s = str(v).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:26], fmt)
        except ValueError:
            continue
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d")
    except ValueError:
        return None

def agrupar_situacao(status):
    s = _norm(status)
    if "arquiv" in s:
        return "Arquivado"
    if ("resposta" in s and ("recebid" in s or "respondid" in s)) or "transformad" in s:
        return "Respondido"
    # Separação Exclusiva para KPI e Gestão de Prazos do Dashboard
    if "aguardando resposta" in s or "aguardando recebimento" in s:
        return "Aguardando resposta"
    if any(x in s for x in ["aguardando encaminhamento", "aguardando despacho", "aguardando envio", "pronta para pauta"]):
        return "Não encaminhada"
        
    if any(a in s for a in SITUACOES_ATIVAS) or "tramita" in s:
        return "Em tramitação"
    return "Outros"

def esta_ativo(status):
    return any(a in _norm(status) for a in SITUACOES_ATIVAS)

def temas_oficiais(id_prop):
    try:
        time.sleep(DELAY_API)
        dados = session.get(f"{API}/proposicoes/{id_prop}/temas", timeout=15).json().get("dados", [])
        return [t.get("tema", "").strip() for t in dados if t.get("tema")]
    except Exception as e:
        print(f"  ! temas {id_prop}: {e}")
        return []

def autor_principal(id_prop):
    try:
        time.sleep(DELAY_API)
        a = session.get(f"{API}/proposicoes/{id_prop}/autores", timeout=15).json().get("dados", [])
        if a:
            x = a[0]
            return f"Dep. {x.get('nome')} ({x.get('siglaPartido','')}-{x.get('siglaUf','')})"
    except Exception:
        pass
    return "Dep. Desconhecido"

def tema_reserva(texto):
    e = _norm(texto)
    if any(x in e for x in ["jornada de trabalho", "escala 6x1", "6x1", "mercado de trabalho",
                            "emprego", "informalidade", "trabalhador"]):
        return "Trabalho e Emprego"
    if any(x in e for x in ["orcament", "fiscal", "contingenciamento", "bloqueio orcament",
                            "lrf", "dotacao", "receita", "despesa", "arrecadacao"]):
        return "Finanças Públicas e Orçamento"
    if any(x in e for x in ["servidor", "concurso", "carreira", "cargo", "reestruturacao"]):
        return "Administração Pública"
    if any(x in e for x in ["ibge", "ipea", "estatistic", "censo", "renda real", "endividamento"]):
        return "Economia"
    return "Não classificado"

# ==========================================
# MONTAGEM DE UM REGISTRO
# ==========================================
def montar_registro(id_prop, sigla, numero, ano, ementa, status, comissao, data_obj, data_ult_mov_obj=None, data_prazo_obj=None):
    nomes_tema = temas_oficiais(id_prop)
    if nomes_tema:
        tema, fonte = nomes_tema[0], "oficial"
    else:
        tema, fonte = tema_reserva(ementa), "reserva"
        
    return {
        "data": data_obj.strftime("%d/%m/%Y") if data_obj else "—",
        "data_iso": data_obj.strftime("%Y-%m-%d") if data_obj else "",
        "mes_ano": f"{MESES_BR[data_obj.month]}/{str(data_obj.year)[2:]}" if data_obj else "—",
        
        # Colunas e Lógicas Novas
        "data_ult_mov_br": data_ult_mov_obj.strftime("%d/%m/%Y") if data_ult_mov_obj else "—",
        "data_ult_mov_iso": data_ult_mov_obj.strftime("%Y-%m-%d") if data_ult_mov_obj else "",
        "data_prazo_br": data_prazo_obj.strftime("%d/%m/%Y") if data_prazo_obj else "—",
        "data_prazo_iso": data_prazo_obj.strftime("%Y-%m-%d") if data_prazo_obj else "",
        
        "casa": "Câmara",
        "sigla": sigla,
        "numero": numero,
        "ano": ano,
        "autor": autor_principal(id_prop),
        "comissao": comissao or "MESA",
        "tema": tema,
        "temas": nomes_tema,
        "tema_fonte": fonte,
        "ementa": ementa,
        "status": (status or "Em Tramitação").strip(),
        "situacao_grupo": agrupar_situacao(status),
        "link": f"https://www.camara.leg.br/proposicoesWeb/fichadetramitacao?idProposicao={id_prop}",
    }

# ==========================================
# FONTE PRIMÁRIA: ARQUIVO ANUAL COMPLETO
# ==========================================
def _registros_do_arquivo(payload):
    """O arquivo pode vir como {'dados': [...]} ou como lista pura."""
    if isinstance(payload, dict):
        return payload.get("dados") or payload.get("proposicoes") or []
    if isinstance(payload, list):
        return payload
    return []

def buscar_por_arquivo(ano):
    url = ARQUIVO_ANO.format(ano=ano)
    print(f"[{ano}] baixando arquivo anual completo...")
    resp = session.get(url, timeout=180)
    resp.raise_for_status()
    registros = _registros_do_arquivo(resp.json())
    print(f"[{ano}] {len(registros)} proposições no arquivo. Filtrando RICs do MPO...")

    achados = []
    for p in registros:
        if (p.get("siglaTipo") or "").strip().upper() != "RIC":
            continue
        ementa = p.get("ementa", "") or ""
        ementa_det = p.get("ementaDetalhada", "") or ""
        keywords = p.get("keywords", "") or ""
        if not dirigido_ao_mpo(ementa, ementa_det, keywords):
            continue

        ultimo = p.get("ultimoStatus") or p.get("statusProposicao") or {}
        status = ultimo.get("descricaoSituacao") or "Em Tramitação"
        if APENAS_ATIVOS and not esta_ativo(status):
            continue
            
        comissao = ultimo.get("siglaOrgao") or "MESA"

        id_prop = p.get("id")
        data_obj = parse_data(p.get("dataApresentacao"))
        
        # Lógica de Captura da Última Movimentação e Cálculo de Vencimento
        data_ult_mov_obj = parse_data(ultimo.get("dataHora"))
        prazo_obj = None
        if agrupar_situacao(status) == "Aguardando resposta" and data_ult_mov_obj:
            prazo_obj = data_ult_mov_obj + timedelta(days=30)

        achados.append(montar_registro(
            id_prop, p.get("siglaTipo"), p.get("numero"), p.get("ano"),
            ementa, status, comissao, data_obj, data_ult_mov_obj, prazo_obj))
        print(f"  + RIC {p.get('numero')}/{ano} | {achados[-1]['tema']} ({achados[-1]['tema_fonte']}) | {status.strip()}")
    return achados

# ==========================================
# FALLBACK: ENDPOINT PAGINADO
# ==========================================
def buscar_por_api(ano):
    print(f"[{ano}] FALLBACK: varrendo endpoint paginado...")
    achados, pagina = 1
    while True:
        url = (f"{API}/proposicoes?siglaTipo=RIC&ano={ano}"
               f"&itens=100&ordem=DESC&ordenarPor=id&pagina={pagina}")
        resp = session.get(url, timeout=30)
        if resp.status_code != 200:
            break
        props = resp.json().get("dados", [])
        if not props:
            break
        for p in props:
            ementa = p.get("ementa", "") or ""
            if not dirigido_ao_mpo(ementa):
                continue
            id_prop = p.get("id")
            time.sleep(DELAY_API)
            det = session.get(f"{API}/proposicoes/{id_prop}", timeout=15).json().get("dados", {})
            statusProposicao = det.get("statusProposicao", {})
            status = statusProposicao.get("descricaoSituacao", "Em Tramitação")
            
            if APENAS_ATIVOS and not esta_ativo(status):
                continue
            
            comissao = statusProposicao.get("siglaOrgao", "MESA")
            data_obj = parse_data(p.get("dataApresentacao"))
            
            # Captura de Vencimento (Fallback)
            data_ult_mov_obj = parse_data(statusProposicao.get("dataHora"))
            prazo_obj = None
            if agrupar_situacao(status) == "Aguardando resposta" and data_ult_mov_obj:
                prazo_obj = data_ult_mov_obj + timedelta(days=30)

            achados.append(montar_registro(
                id_prop, p.get("siglaTipo"), p.get("numero"), p.get("ano"),
                ementa, status, comissao, data_obj, data_ult_mov_obj, prazo_obj))
        pagina += 1
    return achados

def buscar_camara():
    todos = []
    for ano in ANOS_BUSCA:
        try:
            todos += buscar_por_arquivo(ano)
        except Exception as e:
            print(f"[{ano}] arquivo indisponível ({e}); usando fallback.")
            try:
                todos += buscar_por_api(ano)
            except Exception as e2:
                print(f"[{ano}] fallback também falhou: {e2}")
    return todos

# ==========================================
# SENADO (opcional, mesma regra de destinatário)
# ==========================================
def buscar_senado():
    print("Buscando requerimentos do Senado dirigidos ao MPO...")
    headers = {"Accept": "application/json"}
    achados = []
    for ano in ANOS_BUSCA:
        try:
            url = f"https://legis.senado.leg.br/dadosabertos/materia/pesquisa/lista?sigla=REQ&ano={ano}"
            resp = session.get(url, headers=headers, timeout=30)
            if resp.status_code != 200:
                continue
            materias = resp.json().get("PesquisaBasicaMateria", {}).get("Materias", {}).get("Materia", [])
            if isinstance(materias, dict):
                materias = [materias]
            for m in materias:
                ementa = m.get("DadosBasicosMateria", {}).get("EmentaMateria", "") or ""
                if not dirigido_ao_mpo(ementa):
                    continue
                cod = m.get("IdentificacaoMateria", {}).get("CodigoMateria")
                autor = "Senador(a)"
                
                try:
                    time.sleep(DELAY_API)
                    dj = session.get(f"https://legis.senado.leg.br/dadosabertos/materia/{cod}",
                                     headers=headers, timeout=15).json().get("DetalheMateria", {}).get("Materia", {})
                    autores = dj.get("Autoria", {}).get("Autor", [])
                    if isinstance(autores, dict):
                        autores = [autores]
                    if autores:
                        a = autores[0]
                        nome = a.get("NomeAutor", "")
                        partido = a.get("IdentificacaoParlamentar", {}).get("SiglaPartidoParlamentar", "")
                        uf = a.get("IdentificacaoParlamentar", {}).get("UfParlamentar", "")
                        autor = f"Sen. {nome} ({partido}-{uf})" if partido else f"Sen. {nome}"
                except Exception:
                    pass
                
                data_obj = parse_data(m.get("DadosBasicosMateria", {}).get("DataApresentacao"))
                status = "Aguardando Leitura/Resposta"
                
                # Prazo provisório assumindo a data de apresentação como start no Senado
                data_ult_mov_obj = data_obj 
                prazo_obj = data_ult_mov_obj + timedelta(days=30) if data_ult_mov_obj else None

                achados.append({
                    "data": data_obj.strftime("%d/%m/%Y") if data_obj else "—",
                    "data_iso": data_obj.strftime("%Y-%m-%d") if data_obj else "",
                    "mes_ano": f"{MESES_BR[data_obj.month]}/{str(data_obj.year)[2:]}" if data_obj else "—",
                    "data_ult_mov_br": data_ult_mov_obj.strftime("%d/%m/%Y") if data_ult_mov_obj else "—",
                    "data_ult_mov_iso": data_ult_mov_obj.strftime("%Y-%m-%d") if data_ult_mov_obj else "",
                    "data_prazo_br": prazo_obj.strftime("%d/%m/%Y") if prazo_obj else "—",
                    "data_prazo_iso": prazo_obj.strftime("%Y-%m-%d") if prazo_obj else "",
                    "casa": "Senado",
                    "sigla": m.get("IdentificacaoMateria", {}).get("SiglaMateria"),
                    "numero": m.get("IdentificacaoMateria", {}).get("NumeroMateria"),
                    "ano": m.get("IdentificacaoMateria", {}).get("AnoMateria"),
                    "autor": autor,
                    "comissao": "Plenário",
                    "tema": tema_reserva(ementa),
                    "temas": [],
                    "tema_fonte": "reserva",
                    "ementa": ementa,
                    "status": status,
                    "situacao_grupo": agrupar_situacao(status),
                    "link": f"https://www25.senado.leg.br/web/atividade/materias/-/materia/{cod}",
                })
        except Exception as e:
            print(f"  Erro no Senado ({ano}): {e}")
    return achados

# ==========================================
# COMPILAÇÃO
# ==========================================
def gerar_dashboard_data():
    print("Iniciando extração...")
    reqs = buscar_camara() + buscar_senado()

    # Deduplica (sigla+numero+ano+casa) e ordena por data desc
    vistos, unicos = set(), []
    for r in reqs:
        chave = (r["casa"], r["sigla"], r["numero"], r["ano"])
        if chave in vistos:
            continue
        vistos.add(chave)
        unicos.append(r)
    unicos.sort(key=lambda r: r["data_iso"] or "0000-00-00", reverse=True)

    timeline = Counter(r["mes_ano"] for r in unicos)
    autores = Counter(r["autor"] for r in unicos)
    temas = Counter(r["tema"] for r in unicos)
    comissoes = Counter(r["comissao"] for r in unicos)
    situacoes = Counter(r["situacao_grupo"] for r in unicos)

    saida = {
        "ultima_atualizacao": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "fonte_temas": "oficial",
        "resumo": {
            "total_requerimentos": len(unicos),
            "total_autores": len(autores),
            "total_temas": len(temas),
            "total_comissoes": len(comissoes),
            "total_ativos": sum(1 for r in unicos if r["situacao_grupo"] in ["Em tramitação", "Aguardando resposta", "Não encaminhada"]),
            "total_respondidos": sum(1 for r in unicos if r["situacao_grupo"] == "Respondido"),
        },
        "timeline": dict(timeline),
        "autores": dict(autores),
        "temas": dict(temas),
        "comissoes": dict(comissoes),
        "situacoes": dict(situacoes),
        "lista_requerimentos": unicos,
    }
    with open("dados.json", "w", encoding="utf-8") as f:
        json.dump(saida, f, ensure_ascii=False, indent=2)

    print("=========================================")
    print(f"Concluído. {len(unicos)} RICs do MPO "
          f"({saida['resumo']['total_ativos']} ativos/tramitando, "
          f"{saida['resumo']['total_respondidos']} respondidos).")

if __name__ == "__main__":
    gerar_dashboard_data()
