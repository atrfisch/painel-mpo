import requests
import json
import time
from datetime import datetime
from collections import Counter
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ==========================================
# CONFIGURAÇÕES DA SESSÃO (Para evitar Timeout)
# ==========================================
session = requests.Session()
retry = Retry(connect=5, read=5, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
adapter = HTTPAdapter(max_retries=retry)
session.mount('http://', adapter)
session.mount('https://', adapter)

# ==========================================
# PARÂMETROS E FILTROS ALVO
# ==========================================
ANOS_BUSCA = [2023, 2024, 2025, 2026] # Busca toda a legislatura atual (RICs antigos são arquivados - Art 105 RICD)
DELAY_API = 0.5 # Respiro para a API da Câmara (evita bloqueios)

SITUACOES_ALVO = [
    "aguardando designação de relator",
    "aguardando encaminhamento",
    "aguardando envio ao executivo",
    "aguardando resposta",
    "aguardando recebimento", # Início da tramitação (adicionado)
    "aguardando despacho do presidente da câmara dos deputados" # Início da tramitação (adicionado)
]

MESES_BR = {1: 'Jan', 2: 'Fev', 3: 'Mar', 4: 'Abr', 5: 'Mai', 6: 'Jun', 
            7: 'Jul', 8: 'Ago', 9: 'Set', 10: 'Out', 11: 'Nov', 12: 'Dez'}

TERMOS_MPO = [
    'planejamento e orçamento',
    'ministério do planejamento',
    'ministro do planejamento',
    'ministra do planejamento',
    'mpo',
    'simone tebet'
]

def classificar_tema_oficial(ementa):
    """Classifica o tema baseado nas áreas temáticas da Câmara"""
    ementa = ementa.lower()
    if any(x in ementa for x in ['orçamento', 'lrf', 'fiscal', 'receita', 'despesa', 'contingenciamento']): 
        return "Orçamento e Finanças"
    if any(x in ementa for x in ['servidor', 'concurso', 'previdência', 'cargo']): 
        return "Administração Pública"
    if any(x in ementa for x in ['pac', 'obra', 'infraestrutura', 'rodovia', 'ferrovia']): 
        return "Infraestrutura"
    return "Outros"

def buscar_dados_camara():
    """Busca RICs da Câmara iterando sobre todas as páginas de cada ano"""
    print("Buscando lista principal da Câmara (Legislatura atual)...")
    dados = []
    
    for ano in ANOS_BUSCA:
        pagina = 1
        total_parcial = 0
        
        while True:
            # Paginação adicionada: itens=100 (limite real da API) e iterando o parâmetro 'pagina'
            url = f"https://dadosabertos.camara.leg.br/api/v2/proposicoes?siglaTipo=RIC&ano={ano}&itens=100&ordem=DESC&ordenarPor=id&pagina={pagina}"
            
            try:
                response = session.get(url, timeout=30)
                if response.status_code == 200:
                    proposicoes = response.json().get('dados', [])
                    
                    if not proposicoes:
                        print(f"Fim das páginas para o ano {ano}. Total processado: {total_parcial}")
                        break # Fim dos dados para este ano
                        
                    total_parcial += len(proposicoes)
                    
                    for p in proposicoes:
                        ementa = p.get('ementa', '').lower()
                        
                        # FILTRO 1: Flexibilizado para os termos da lista TERMOS_MPO
                        if any(termo in ementa for termo in TERMOS_MPO):
                            id_prop = p.get('id')
                            
                            # FILTRO 2: Busca a "Capa" da proposição para ver a Situação Oficial atual
                            time.sleep(DELAY_API)
                            detalhes_url = f"https://dadosabertos.camara.leg.br/api/v2/proposicoes/{id_prop}"
                            
                            try:
                                detalhes_res = session.get(detalhes_url, timeout=15).json().get('dados', {})
                                situacao_real = detalhes_res.get('statusProposicao', {}).get('descricaoSituacao', '').lower()
                                print(f"DEBUG: Analisando RIC {p.get('numero')}/{ano} (Match MPO) - Situação: '{situacao_real}'")
                                
                                # Verifica se a situação real está dentro da lista de alvos
                                if any(alvo in situacao_real for alvo in SITUACOES_ALVO):
                                    
                                    # BUSCA DETALHADA: Autor
                                    time.sleep(DELAY_API)
                                    url_autores = f"https://dadosabertos.camara.leg.br/api/v2/proposicoes/{id_prop}/autores"
                                    res_autores = session.get(url_autores, timeout=15).json().get('dados', [])
                                    autor_formatado = "Dep. Desconhecido"
                                    if res_autores:
                                        a = res_autores[0]
                                        autor_formatado = f"Dep. {a.get('nome')} ({a.get('siglaPartido', '')}-{a.get('siglaUf', '')})"
                                    
                                    # BUSCA DETALHADA: Comissão/Local
                                    time.sleep(DELAY_API)
                                    url_tram = f"https://dadosabertos.camara.leg.br/api/v2/proposicoes/{id_prop}/tramitacoes"
                                    res_tram = session.get(url_tram, timeout=15).json().get('dados', [])
                                    comissao_atual = res_tram[-1].get('siglaOrgao', 'MESA') if res_tram else 'MESA'

                                    data_str = p.get('dataApresentacao', f'{ano}-01-01T00:00').split('T')[0]
                                    data_obj = datetime.strptime(data_str, '%Y-%m-%d')
                                    mes_ano = f"{MESES_BR[data_obj.month]}/{str(data_obj.year)[2:]}"

                                    dados.append({
                                        "data": data_obj.strftime('%d/%m/%Y'),
                                        "mes_ano": mes_ano,
                                        "casa": "Camara",
                                        "sigla": p.get('siglaTipo'),
                                        "numero": p.get('numero'),
                                        "ano": p.get('ano'),
                                        "autor": autor_formatado,
                                        "comissao": comissao_atual,
                                        "tema": classificar_tema_oficial(ementa),
                                        "ementa": p.get('ementa'),
                                        "status": detalhes_res.get('statusProposicao', {}).get('descricaoSituacao', 'Em Tramitação'),
                                        "link": f"https://www.camara.leg.br/proposicoesWeb/fichadetramitacao?idProposicao={id_prop}"
                                    })
                            except Exception as e:
                                print(f"Erro ao detalhar RIC {id_prop}: {e}")
                else:
                    print(f"Erro na API da Câmara ao acessar página {pagina} do ano {ano}. Código: {response.status_code}")
                    break
            except Exception as e:
                print(f"Erro fatal na conexão com a Câmara no ano {ano}, página {pagina}: {e}")
                break
            
            # Avança para a próxima página de resultados
            pagina += 1

    return dados

def buscar_dados_senado():
    """Busca REQs do Senado e filtra por MPO"""
    print("Buscando lista principal do Senado...")
    headers = {"Accept": "application/json"}
    dados = []
    
    for ano in ANOS_BUSCA:
        url = f"https://legis.senado.leg.br/dadosabertos/materia/pesquisa/lista?sigla=REQ&ano={ano}"
        
        try:
            response = session.get(url, headers=headers, timeout=30)
            if response.status_code == 200:
                materias = response.json().get('PesquisaBasicaMateria', {}).get('Materias', {}).get('Materia', [])
                if isinstance(materias, dict): materias = [materias]
                    
                for m in materias:
                    ementa = m.get('DadosBasicosMateria', {}).get('EmentaMateria', '').lower()
                    
                    # FILTRO FLEXIBILIZADO NO SENADO
                    if any(termo in ementa for termo in TERMOS_MPO):
                        codigo_materia = m.get('IdentificacaoMateria', {}).get('CodigoMateria')
                        
                        time.sleep(DELAY_API)
                        url_detalhe = f"https://legis.senado.leg.br/dadosabertos/materia/{codigo_materia}"
                        res_detalhe = session.get(url_detalhe, headers=headers, timeout=15)
                        
                        autor_formatado = "Senador(a)"
                        comissao_atual = "Plenário"
                        status = "Aguardando Leitura/Resposta"
                        
                        if res_detalhe.status_code == 200:
                            detalhe_json = res_detalhe.json().get('DetalheMateria', {}).get('Materia', {})
                            autores_list = detalhe_json.get('Autoria', {}).get('Autor', [])
                            if isinstance(autores_list, dict): autores_list = [autores_list]
                            if autores_list:
                                a = autores_list[0]
                                nome_autor = a.get('NomeAutor', '')
                                partido = a.get('IdentificacaoParlamentar', {}).get('SiglaPartidoParlamentar', '')
                                uf = a.get('IdentificacaoParlamentar', {}).get('UfParlamentar', '')
                                autor_formatado = f"Sen. {nome_autor} ({partido}-{uf})" if partido else f"Sen. {nome_autor}"
                                
                            situacao = detalhe_json.get('SituacaoAtual', {}).get('Autuacoes', {}).get('Autuacao', [])
                            if isinstance(situacao, dict): situacao = [situacao]
                            if situacao:
                                comissao_atual = situacao[0].get('Local', {}).get('SiglaLocal', 'Plenário')

                        data_str = m.get('DadosBasicosMateria', {}).get('DataApresentacao', f'{ano}-01-01')
                        data_obj = datetime.strptime(data_str, '%Y-%m-%d')
                        mes_ano = f"{MESES_BR[data_obj.month]}/{str(data_obj.year)[2:]}"

                        dados.append({
                            "data": data_obj.strftime('%d/%m/%Y'),
                            "mes_ano": mes_ano,
                            "casa": "Senado",
                            "sigla": m.get('IdentificacaoMateria', {}).get('SiglaMateria'),
                            "numero": m.get('IdentificacaoMateria', {}).get('NumeroMateria'),
                            "ano": m.get('IdentificacaoMateria', {}).get('AnoMateria'),
                            "autor": autor_formatado,
                            "comissao": comissao_atual,
                            "tema": classificar_tema_oficial(ementa),
                            "ementa": m.get('DadosBasicosMateria', {}).get('EmentaMateria', ''),
                            "status": status,
                            "link": f"https://www25.senado.leg.br/web/atividade/materias/-/materia/{codigo_materia}"
                        })
        except Exception as e:
            print(f"Erro ao processar Senado no ano {ano}: {e}")
            
    return dados

def gerar_dashboard_data():
    """Função principal que compila os dados e salva o arquivo"""
    print("Iniciando extração de dados...")
    camara = buscar_dados_camara()
    senado = buscar_dados_senado()
    todos_reqs = camara + senado
    
    # Cálculos para os cartões (KPIs) e gráficos
    timeline_count = Counter([r['mes_ano'] for r in todos_reqs])
    autores_count = Counter([r['autor'] for r in todos_reqs])
    temas_count = Counter([r['tema'] for r in todos_reqs])
    comissoes_count = Counter([r['comissao'] for r in todos_reqs])

    dados_finais = {
        "ultima_atualizacao": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "resumo": {
            "total_requerimentos": len(todos_reqs),
            "total_autores": len(autores_count)
        },
        "timeline": dict(timeline_count),
        "autores": dict(autores_count),
        "temas": dict(temas_count),
        "comissoes": dict(comissoes_count),
        "lista_requerimentos": todos_reqs
    }

    with open('dados.json', 'w', encoding='utf-8') as f:
        json.dump(dados_finais, f, ensure_ascii=False, indent=4)
        
    print("=========================================")
    print(f"Processo concluído. {len(todos_reqs)} requerimentos válidos salvos.")

if __name__ == "__main__":
    gerar_dashboard_data()
