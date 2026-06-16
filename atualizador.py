import requests
import json
import time
from datetime import datetime
from collections import Counter

# ==========================================
# CONFIGURAÇÕES E REFERÊNCIAS
# Câmara: https://dadosabertos.camara.leg.br/swagger/api.html
# Senado: https://legis.senado.leg.br/dadosabertos/docs/ui/index.html
# ==========================================

ANO_BUSCA = 2026
DELAY_API = 0.3 # Segundos entre requisições para evitar bloqueio (Rate Limit)

def classificar_tema(ementa):
    """Classifica o tema baseado em palavras-chave na ementa"""
    ementa = ementa.lower()
    if 'pac' in ementa or 'aceleração' in ementa or 'obras' in ementa: return "PAC / Obras"
    if 'corte' in ementa or 'contingenciamento' in ementa or 'bloqueio' in ementa: return "Cortes de Gastos"
    if 'emenda' in ementa or 'rp8' in ementa or 'rp9' in ementa: return "Emendas Parlamentares"
    if 'arcabouço' in ementa or 'meta' in ementa or 'fiscal' in ementa: return "Arcabouço Fiscal"
    if 'concurso' in ementa or 'servidor' in ementa or 'provimento' in ementa: return "Concursos Públicos"
    return "Geral / Outros"

def obter_detalhes_camara(id_proposicao):
    """Faz chamadas secundárias na Câmara para pegar Autor, Comissão e Status"""
    autor_formatado = "Desconhecido"
    comissao_atual = "Plenário / Mesa"
    status_atual = "Em Tramitação"
    mes_ano_aprovacao = None
    
    # 1. Busca Autores
    try:
        url_autores = f"https://dadosabertos.camara.leg.br/api/v2/proposicoes/{id_proposicao}/autores"
        res_autores = requests.get(url_autores).json()
        if res_autores.get('dados') and len(res_autores['dados']) > 0:
            autor = res_autores['dados'][0]
            nome = autor.get('nome', 'Deputado')
            partido = autor.get('siglaPartido', '')
            uf = autor.get('siglaUf', '')
            autor_formatado = f"Dep. {nome} ({partido}-{uf})" if partido else f"Dep. {nome}"
    except Exception as e:
        print(f"Erro ao buscar autor Câmara (ID {id_proposicao}): {e}")

    time.sleep(DELAY_API) # Respiro para a API

    # 2. Busca Última Tramitação (Comissão e Status)
    try:
        url_tram = f"https://dadosabertos.camara.leg.br/api/v2/proposicoes/{id_proposicao}/tramitacoes"
        res_tram = requests.get(url_tram).json()
        if res_tram.get('dados') and len(res_tram['dados']) > 0:
            ultima_tramitacao = res_tram['dados'][-1] # Pega o último evento
            comissao_atual = ultima_tramitacao.get('siglaOrgao', 'MESA')
            
            # Verifica o histórico para identificar se foi aprovado ou encaminhado ao Ministério
            for t in res_tram['dados']:
                desc_tram = t.get('descricaoTramitacao', '').lower()
                despacho = t.get('despacho', '').lower()
                
                if 'aprovad' in desc_tram or 'encaminhad' in desc_tram or 'aprovad' in despacho or 'encaminhad' in despacho:
                    status_atual = "Aprovado / Encaminhado"
                    # Extrai o mês/ano do evento para o gráfico
                    data_aprov = t.get('dataHora', '').split('T')[0]
                    data_obj = datetime.strptime(data_aprov, '%Y-%m-%d')
                    mes_ano_aprovacao = data_obj.strftime('%Y-%m') # Formato de ordenação
    except Exception as e:
        print(f"Erro ao buscar tramitação Câmara (ID {id_proposicao}): {e}")

    return autor_formatado, comissao_atual, status_atual, mes_ano_aprovacao

def buscar_dados_camara():
    """Busca a lista de RICs da Câmara e enriquece com chamadas secundárias"""
    print("Buscando lista principal da Câmara...")
    url = f"https://dadosabertos.camara.leg.br/api/v2/proposicoes?siglaTipo=RIC&ano={ANO_BUSCA}&itens=100&ordem=DESC&ordenarPor=id"
    response = requests.get(url)
    dados = []
    
    if response.status_code == 200:
        proposicoes = response.json().get('dados', [])
        
        for p in proposicoes:
            ementa = p.get('ementa', '').lower()
            
            # Filtro MPO
            if 'planejamento' in ementa or 'mpo' in ementa or 'orçamento' in ementa:
                id_prop = p.get('id')
            print(f" > Detalhando RIC Câmara ID: {id_prop}...")
            
            # CHAMADAS SECUNDÁRIAS
            time.sleep(DELAY_API)
            autor, comissao, status, mes_ano_aprov = obter_detalhes_camara(id_prop)
            
            # Extrai o mês/ano para a linha do tempo (formato YYYY-MM para ordenação correta)
            data_str = p.get('dataApresentacao', f'{ANO_BUSCA}-01-01T00:00').split('T')[0]
            data_obj = datetime.strptime(data_str, '%Y-%m-%d')
            mes_ano = data_obj.strftime('%Y-%m')
            
            dados.append({
                "data": data_obj.strftime('%d/%m/%Y'),
                "mes_ano": mes_ano,
                "mes_ano_aprovacao": mes_ano_aprov,
                "casa": "Camara",
                "sigla": p.get('siglaTipo'),
                "numero": p.get('numero'),
                "ano": p.get('ano'),
                "autor": autor,
                "comissao": comissao,
                "status": status,
                "ementa": p.get('ementa'),
                "link": f"https://www.camara.leg.br/proposicoesWeb/fichadetramitacao?idProposicao={id_prop}"
            })
    return dados

def buscar_dados_senado():
    """Busca a lista de REQs do Senado e enriquece com a chamada detalhada da Matéria"""
    print("Buscando lista principal do Senado...")
    url = f"https://legis.senado.leg.br/dadosabertos/materia/pesquisa/lista?sigla=REQ&ano={ANO_BUSCA}"
    headers = {"Accept": "application/json"}
    response = requests.get(url, headers=headers)
    dados = []
    
    if response.status_code == 200:
        try:
            materias = response.json().get('PesquisaBasicaMateria', {}).get('Materias', {}).get('Materia', [])
            if isinstance(materias, dict):
                materias = [materias]
                
            for m in materias:
                ementa = m.get('DadosBasicosMateria', {}).get('EmentaMateria', '').lower()
                
                # Filtro MPO
                if 'planejamento' in ementa or 'mpo' in ementa:
                    codigo_materia = m.get('IdentificacaoMateria', {}).get('CodigoMateria')
                    print(f" > Detalhando REQ Senado ID: {codigo_materia}...")
                    
                    # CHAMADA SECUNDÁRIA (Puxa o detalhe completo da matéria)
                time.sleep(DELAY_API)
                url_detalhe = f"https://legis.senado.leg.br/dadosabertos/materia/{codigo_materia}"
                res_detalhe = requests.get(url_detalhe, headers=headers)
                
                autor_formatado = "Senador(a)"
                comissao_atual = "Plenário"
                status_atual = "Em Tramitação"
                mes_ano_aprovacao = None
                
                if res_detalhe.status_code == 200:
                    detalhe_json = res_detalhe.json().get('DetalheMateria', {}).get('Materia', {})
                        
                        # Extrai Autor com Partido
                        autores_list = detalhe_json.get('Autoria', {}).get('Autor', [])
                        if isinstance(autores_list, dict): autores_list = [autores_list]
                        if len(autores_list) > 0:
                            nome_autor = autores_list[0].get('NomeAutor', '')
                            partido = autores_list[0].get('IdentificacaoParlamentar', {}).get('SiglaPartidoParlamentar', '')
                            uf = autores_list[0].get('IdentificacaoParlamentar', {}).get('UfParlamentar', '')
                            autor_formatado = f"Sen. {nome_autor} ({partido}-{uf})" if partido else f"Sen. {nome_autor}"
                            
                        # Extrai Localização/Comissão
                    situacao = detalhe_json.get('SituacaoAtual', {}).get('Autuacoes', {}).get('Autuacao', [])
                    if isinstance(situacao, dict): situacao = [situacao]
                    if len(situacao) > 0:
                        comissao_atual = situacao[0].get('Local', {}).get('SiglaLocal', 'Plenário')
                        desc_situacao = situacao[0].get('Situacao', {}).get('DescricaoSituacao', '').lower()
                        # Tenta prever se foi despachado
                        if 'aprovad' in desc_situacao or 'encaminhad' in desc_situacao or 'remetid' in desc_situacao:
                            status_atual = "Aprovado / Encaminhado"

                data_str = m.get('DadosBasicosMateria', {}).get('DataApresentacao', f'{ANO_BUSCA}-01-01')
                data_obj = datetime.strptime(data_str, '%Y-%m-%d')
                mes_ano = data_obj.strftime('%Y-%m')

                if status_atual == "Aprovado / Encaminhado":
                    mes_ano_aprovacao = mes_ano # fallback para a data de apresentação caso não ache

                dados.append({
                    "data": data_obj.strftime('%d/%m/%Y'),
                    "mes_ano": mes_ano,
                    "mes_ano_aprovacao": mes_ano_aprovacao,
                    "casa": "Senado",
                    "sigla": m.get('IdentificacaoMateria', {}).get('SiglaMateria'),
                    "numero": m.get('IdentificacaoMateria', {}).get('NumeroMateria'),
                    "ano": m.get('IdentificacaoMateria', {}).get('AnoMateria'),
                    "autor": autor_formatado,
                    "comissao": comissao_atual,
                    "status": status_atual,
                    "ementa": m.get('DadosBasicosMateria', {}).get('EmentaMateria', ''),
                    "link": f"https://www25.senado.leg.br/web/atividade/materias/-/materia/{codigo_materia}"
                })
    except Exception as e:
            print(f"Erro ao processar Senado: {e}")
            
    return dados

def gerar_dashboard_data():
    camara = buscar_dados_camara()
    senado = buscar_dados_senado()
    todos_reqs = camara + senado
    
    # Processamento e Agrupamento dos Dados
    for req in todos_reqs:
        req['tema'] = classificar_tema(req['ementa'])

    # Montagem da Linha do Tempo Dupla (Apresentados vs Aprovados)
    meses_unicos = set([r['mes_ano'] for r in todos_reqs])
    for r in todos_reqs:
        if r.get('mes_ano_aprovacao'):
            meses_unicos.add(r['mes_ano_aprovacao'])
            
    # Ordena cronologicamente
    meses_ordenados = sorted(list(meses_unicos))
    
    # Formata labels para o gráfico (Ex: "Jan/26")
    labels_display = []
    for m in meses_ordenados:
        obj = datetime.strptime(m, '%Y-%m')
        labels_display.append(obj.strftime('%b/%y').capitalize())

    apresentados_count = Counter([r['mes_ano'] for r in todos_reqs])
    aprovados_count = Counter([r['mes_ano_aprovacao'] for r in todos_reqs if r.get('mes_ano_aprovacao')])

    timeline_data = {
        "labels": labels_display,
        "apresentados": [apresentados_count.get(m, 0) for m in meses_ordenados],
        "aprovados": [aprovados_count.get(m, 0) for m in meses_ordenados]
    }
    
    total_aprovados = sum(1 for r in todos_reqs if r.get('status') == "Aprovado / Encaminhado")

    autores_count = Counter([r['autor'] for r in todos_reqs])
    temas_count = Counter([r['tema'] for r in todos_reqs])
    comissoes_count = Counter([r['comissao'] for r in todos_reqs])
    
    dados_finais = {
        "ultima_atualizacao": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "resumo": {
            "total_requerimentos": len(todos_reqs),
            "total_autores": len(autores_count),
            "total_aprovados": total_aprovados
        },
        "timeline": timeline_data,
        "autores": dict(autores_count),
        "temas": dict(temas_count),
        "comissoes": dict(comissoes_count),
        "lista_requerimentos": todos_reqs
    }

    # Salva o arquivo JSON
    with open('dados.json', 'w', encoding='utf-8') as f:
        json.dump(dados_finais, f, ensure_ascii=False, indent=4)
    print("=========================================")
    print("Processamento concluído. dados.json gerado!")

if __name__ == "__main__":
    gerar_dashboard_data()
