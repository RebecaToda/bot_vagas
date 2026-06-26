import os
import json
import requests
import time
import re
from datetime import datetime
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel
from jobspy import scrape_jobs
from supabase import create_client, Client

# Carrega chaves locais (.env) ou do GitHub Actions
load_dotenv()

# Inicializa o Gemini
client = genai.Client()

# Inicializa o Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def vaga_ja_analisada(id_vaga):
    """
    Consulta o PostgreSQL no Supabase para ver se o ID da vaga já existe.
    """
    if not supabase:
        return False
    try:
        resposta = supabase.table("historico_vagas").select("id_vaga").eq("id_vaga", id_vaga).execute()
        return len(resposta.data) > 0
    except Exception as e:
        print(f"  ⚠️ Erro ao consultar banco de dados: {e}")
        return False

def salvar_vaga_no_historico(id_vaga, status):
    """
    Salva o ID da vaga e o resultado no banco para nunca mais reanalisar.
    """
    if not supabase:
        return
    try:
        supabase.table("historico_vagas").insert({"id_vaga": id_vaga, "status_analise": status}).execute()
    except Exception as e:
        print(f"  ⚠️ Erro ao salvar no banco de dados: {e}")

def passa_na_peneira_geografica(vaga):
    location = str(vaga.get("location", "")).strip().lower()
    is_remote = vaga.get("is_remote", False)
    if is_remote:
        return True
    if "são paulo" in location or "sp" in location or "curitiba" in location or "pr" in location:
        return True
    return False

class ResultadoVaga(BaseModel):
    aprovada: bool
    motivo: str

def ia_analisa_vaga(titulo, descricao):
    prompt = f"""
    Você é um recrutador técnico especialista em tecnologia. Analise a vaga e decida se um estudante ou formado do curso de 'Análise e Desenvolvimento de Sistemas (Tecnólogo de 2 a 3 anos)' que NÃO POSSUI EXPERIÊNCIA PROFISSIONAL PRÉVIA pode se candidatar.

    Regras de Rejeição:
    1. Se exigir EXCLUSIVAMENTE Bacharelado de 4-5 anos sem aceitar equivalentes.
    2. Se exigir tempo mínimo de experiência profissional comprovada (ex: 'mínimo de 1 ano', '2+ anos de experiência').

    Regras de Aprovação:
    1. Se for uma vaga de Trainee ou Júnior que aceite Tecnólogo/ADS.
    2. E se deixar explícito que NÃO exige experiência profissional prévia.

    Vaga: {titulo}
    Descrição: {descricao}
    """

    while True:
        try:
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=ResultadoVaga,
                    temperature=0.1
                ),
            )
            dados_resposta = json.loads(response.text)
            return dados_resposta.get("aprovada", False)
            
        except Exception as e:
            erro_msg = str(e)
            if "429" in erro_msg or "RESOURCE_EXHAUSTED" in erro_msg:
                segundos_espera = re.search(r"retry in (\d+\.?\d*)s", erro_msg)
                tempo_pausa = int(float(segundos_espera.group(1))) + 2 if segundos_espera else 30
                print(f"  ⚠️ Limite de cota atingido. Aguardando {tempo_pausa} segundos...")
                time.sleep(tempo_pausa)
                continue
            else:
                print(f"  ❌ Erro crítico na chamada da IA: {e}")
                return False

def enviar_alerta_telegram(titulo, empresa, link, local):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    mensagem = (
        f"🚨 *NOVA VAGA COLETADA!* 🚨\n\n"
        f"💼 *Cargo:* {titulo}\n"
        f"🏢 *Empresa:* {empresa}\n"
        f"📍 *Local:* {local}\n"
        f"🔗 *Link:* [Candidatar-se aqui]({link})\n\n"
        f"🤖 _Filtro automático ADS & Júnior/Trainee Recém-formado._"
    )
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": mensagem, "parse_mode": "Markdown", "disable_web_page_preview": True})
    except Exception as e:
        print(f"Erro Telegram: {e}")

if __name__ == "__main__":
    print("\n🕵️‍♂️ Buscando vagas reais em toda a internet através do Google Jobs, LinkedIn e Indeed...")
    
    termos_busca = '"Developer" OR "Desenvolvedor" OR "Estágio TI" OR "Trainee" OR "Analista Júnior"'
    
    try:
        jobs_df = scrape_jobs(
            site_name=["google", "linkedin", "indeed"],
            search_term=termos_busca,
            location="São Paulo, SP",
            results_wanted=30, 
            hours_old=48, 
        )
        
        vagas_reais = jobs_df.to_dict(orient="records")
        print(f"🔍 Encontradas {len(vagas_reais)} vagas potenciais. Iniciando filtragem inteligente...\n")
        
        for vaga in vagas_reais:
            id_vaga = str(vaga.get("id", "")) or vaga.get("job_url", "")
            titulo = vaga.get("title", "Não informado")
            empresa = vaga.get("company", "Não informada")
            link = vaga.get("job_url", "")
            localizacao = vaga.get("location", "Não Especificado")
            descricao = vaga.get("description", "")
            
            print(f"👀 Analisando: {titulo} ({empresa})")
            
            # 🔥 NOVA MEMÓRIA: Se já foi avaliada antes, pula na hora sem gastar nada!
            if vaga_ja_analisada(id_vaga):
                print(f"  ⏩ Vaga já analisada em execuções anteriores. Pulando...")
                continue
            
            if passa_na_peneira_geografica(vaga):
                print(f"  🌍 Passou na localização! Consultando o Gemini...")
                
                if ia_analisa_vaga(titulo, descricao):
                    print(f"  🤖 ✓ APROVADA! Enviando para o Telegram e salvando no banco...")
                    enviar_alerta_telegram(titulo, empresa, link, localizacao)
                    salvar_vaga_no_historico(id_vaga, "aprovada")
                else:
                    print(f"  🤖 ✕ Rejeitada. Salvando decisão no banco...")
                    salvar_vaga_no_historico(id_vaga, "rejeitada")
                    
                time.sleep(3)
            else:
                print(f"  ✕ Descartada no filtro geográfico.")
                
    except Exception as e:
        print(f"❌ Erro ao coletar vagas da internet: {e}")
