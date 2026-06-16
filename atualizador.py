import requests, json, time
from datetime import datetime
from collections import Counter

# CONFIGURAÇÕES
ANO_BUSCA = 2026
SITUACOES_ALVO = [
    "Aguardando Designação de Relator(a)",
    "Aguardando Encaminhamento",
    "Aguardando Envio ao Executivo",
    "Aguardando Resposta"
]

def classificar_tema(ementa):
    ementa = ementa.lower()
    if any(x in ementa for x in ['orçamento', 'lrf', 'fiscal', 'meta']): return "Orçamento e Finanças"
    if any(x in ementa for x in ['servidor', 'concurso', 'previdência']): return "Administração Pública"
    if any(x in ementa for x in ['pac', 'obra', 'infraestrutura']): return "Infraestrutura"
    if any(x in ementa for x in ['tributo', 'imposto', 'reforma']): return "Economia e Tributação"
    return "Outros"

def buscar_dados():
    url = f"https://dadosabertos.camara.leg.br/api/v2/proposicoes?siglaTipo=RIC&ano={ANO_BUSCA}&itens=100&ordem=DESC"
    res = requests.get(url).json()
    lista_final = []
    
    for p in res.get('dados', []):
        id_prop = p.get('id')
        ementa = p.get('ementa', '').lower()
        
        if any(keyword in ementa for keyword in ['planejamento', 'orçamento', 'mpo']):
            time.sleep(0.3)
            tram = requests.get(f"https://dadosabertos.camara.leg.br/api/v2/proposicoes/{id_prop}/tramitacoes").json().get('dados', [])
            
            if tram:
                ultima_situacao = tram[-1].get('descricaoSituacao', '')
                
                if ultima_situacao in SITUACOES_ALVO:
                    time.sleep(0.3)
                    autores = requests.get(f"https://dadosabertos.camara.leg.br/api/v2/proposicoes/{id_prop}/autores").json().get('dados', [])
                    autor_nome = autores[0]['nome'] if autores else "Parlamentar"
                    
                    lista_final.append({
                        "data": p['dataApresentacao'][:10],
                        "autor": autor_nome,
                        "tema": classificar_tema(p['ementa']),
                        "status": ultima_situacao,
                        "comissao": tram[-1].get('siglaOrgao', 'Mesa'),
                        "ementa": p['ementa'],
                        "link": f"https://www.camara.leg.br/proposicoesWeb/fichadetramitacao?idProposicao={id_prop}",
                        "sigla": p['siglaTipo'],
                        "numero": p['numero'],
                        "ano": p['ano']
                    })

    final = {
        "ultima_atualizacao": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "resumo": {
            "total": len(lista_final),
            "autores": len(set(r['autor'] for r in lista_final)),
            "temas": len(set(r['tema'] for r in lista_final)),
            "comissoes": len(set(r['comissao'] for r in lista_final))
        },
        "timeline": dict(Counter([r['data'][:7] for r in lista_final])),
        "autores": dict(Counter([r['autor'] for r in lista_final]).most_common(5)),
        "temas": dict(Counter([r['tema'] for r in lista_final])),
        "comissoes": dict(Counter([r['comissao'] for r in lista_final])),
        "lista_requerimentos": lista_final
    }
    
    with open('dados.json', 'w', encoding='utf-8') as f:
        json.dump(final, f, ensure_ascii=False, indent=4)

if __name__ == "__main__": buscar_dados()
