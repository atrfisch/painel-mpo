import requests
import json
import time
from datetime import datetime
from collections import Counter
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Configuração de persistência: Se falhar, tenta novamente até 3 vezes
def get_session():
    session = requests.Session()
    retry = Retry(
        total=3, 
        backoff_factor=1, 
        status_forcelist=[500, 502, 503, 504]
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session

def buscar_dados():
    print("DEBUG: Iniciando busca com retentativas...")
    session = get_session()
    
    # Filtros que você pediu
    SITUACOES_ALVO = [
        "Aguardando Designação de Relator(a)",
        "Aguardando Encaminhamento",
        "Aguardando Envio ao Executivo",
        "Aguardando Resposta"
    ]
    
    url = "https://dadosabertos.camara.leg.br/api/v2/proposicoes?siglaTipo=RIC&ano=2026&itens=100&ordem=DESC"
    
    try:
        # Usando o timeout explícito de 30 segundos
        response = session.get(url, timeout=30)
        response.raise_for_status()
        proposicoes = response.json().get('dados', [])
        print(f"DEBUG: Encontrados {len(proposicoes)} RICs iniciais.")
        
        lista_final = []
        for p in proposicoes:
            # Filtro por Ministério do Planejamento
            ementa = p.get('ementa', '').lower()
            if any(term in ementa for term in ['planejamento', 'mpo', 'orçamento']):
                
                # Busca tramitação detalhada
                id_prop = p.get('id')
                tram_url = f"https://dadosabertos.camara.leg.br/api/v2/proposicoes/{id_prop}/tramitacoes"
                
                # Respiro para a API
                time.sleep(0.5)
                tram_res = session.get(tram_url, timeout=15).json().get('dados', [])
                
                if tram_res:
                    ultima = tram_res[-1]
                    situacao = ultima.get('descricaoSituacao', '')
                    
                    if situacao in SITUACOES_ALVO:
                        # Busca autor
                        aut_url = f"https://dadosabertos.camara.leg.br/api/v2/proposicoes/{id_prop}/autores"
                        autores = session.get(aut_url, timeout=15).json().get('dados', [])
                        autor_nome = autores[0]['nome'] if autores else "Parlamentar"
                        
                        lista_final.append({
                            "data": p['dataApresentacao'][:10],
                            "sigla": p['siglaTipo'],
                            "numero": p['numero'],
                            "ano": p['ano'],
                            "autor": autor_nome,
                            "status": situacao,
                            "ementa": p['ementa'],
                            "tema": "Orçamento e Finanças", # Categoria fixa baseada no seu filtro
                            "link": f"https://www.camara.leg.br/proposicoesWeb/fichadetramitacao?idProposicao={id_prop}",
                            "casa": "Camara"
                        })
        
        # Gera o JSON
        dados_finais = {
            "ultima_atualizacao": datetime.now().strftime("%d/%m/%Y %H:%M"),
            "resumo": {
                "total_requerimentos": len(lista_final),
                "total_autores": len(set(r['autor'] for r in lista_final))
            },
            "timeline": {}, # Simplificado
            "autores": dict(Counter(r['autor'] for r in lista_final).most_common(5)),
            "temas": {"Orçamento e Finanças": len(lista_final)},
            "comissoes": dict(Counter(r.get('comissao', 'Diversas') for r in lista_final)),
            "lista_requerimentos": lista_final
        }
        
        with open('dados.json', 'w', encoding='utf-8') as f:
            json.dump(dados_finais, f, ensure_ascii=False, indent=4)
        print("DEBUG: dados.json gerado com sucesso.")

    except Exception as e:
        print(f"ERRO CRÍTICO: {e}")
        # Não para o script, apenas avisa

if __name__ == "__main__":
    buscar_dados()
