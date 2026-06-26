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

SEARCHES = {
    "São Paulo, SP": [
        "Desenvolvedor Júnior",
        "Programador Júnior",
        "Analista de Sistemas Júnior",
        "Analista de Desenvolvimento Júnior",
        "Estágio Desenvolvimento",
        "Estágio TI",
        "Estágio Tecnologia",
        "Trainee Tecnologia",
        "Trainee TI",
    ],

    "Curitiba, PR": [
        "Desenvolvedor Júnior",
        "Programador Júnior",
        "Analista de Sistemas Júnior",
        "Estágio Desenvolvimento",
        "Estágio TI",
        "Trainee Tecnologia",
    ],

    "Remote": [
        "Junior Software Engineer",
        "Junior Software Developer",
        "Junior Backend Developer",
        "Junior Frontend Developer",
        "Junior Full Stack Developer",
        "Entry Level Software Engineer",
        "Graduate Software Engineer",
        "Trainee Software Engineer",
    ]
}

PALAVRAS_PROIBIDAS = [
    "senior",
    "sênior",
    "pleno",
    "staff",
    "lead",
    "principal",
    "manager",
    "coordinator",
    "especialista",
    "arquiteto",
]

EXPERIENCIA_PROIBIDA = [
    "3+ years",
    "4+ years",
    "5+ years",
    "3 anos",
    "4 anos",
    "5 anos",
    "minimum 3 years",
    "mínimo de 3 anos",
]

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

def passa_filtro_basico(titulo, descricao):
    titulo = titulo.lower()
    descricao = descricao.lower()

    if any(x in titulo for x in PALAVRAS_PROIBIDAS):
        return False

    if any(x.lower() in descricao for x in EXPERIENCIA_PROIBIDA):
        return False

    return True

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

    print("\n🕵️‍♂️ Buscando vagas...")

    vagas_reais = []

    for local, termos in SEARCHES.items():

        print(f"\n📍 {local}")

        for termo in termos:

            print(f"🔎 {termo}")

            try:

                jobs_df = scrape_jobs(
                    site_name=["google", "linkedin", "indeed"],
                    search_term=termo,
                    location=local,
                    results_wanted=30,
                    hours_old=48,
                )

                if jobs_df.empty:
                    continue

                vagas_reais.extend(
                    jobs_df.to_dict(orient="records")
                )

            except Exception as e:
                print(e)

    print(f"\nTotal bruto: {len(vagas_reais)} vagas")

    # remove duplicatas
    vagas_unicas = {}

    for vaga in vagas_reais:

        chave = (
            vaga.get("job_url")
            or vaga.get("id")
            or (
                vaga.get("title", "")
                + vaga.get("company", "")
            )
        )

        vagas_unicas[chave] = vaga

    vagas_reais = list(vagas_unicas.values())

    print(f"Após remover duplicatas: {len(vagas_reais)} vagas\n")

    for vaga in vagas_reais:

        id_vaga = str(vaga.get("id", "")) or vaga.get("job_url", "")

        titulo = vaga.get("title", "")

        empresa = vaga.get("company", "")

        descricao = vaga.get("description", "")

        localizacao = vaga.get("location", "")

        link = vaga.get("job_url", "")

        print(f"👀 {titulo}")

        if vaga_ja_analisada(id_vaga):
            print("   ⏩ Já analisada")
            continue

        if not passa_na_peneira_geografica(vaga):
            print("   🌎 Localização incompatível")
            salvar_vaga_no_historico(id_vaga, "localizacao")
            continue

        if not passa_filtro_basico(titulo, descricao):
            print("   🚫 Eliminada pelo filtro básico")
            salvar_vaga_no_historico(id_vaga, "prefiltro")
            continue

        print("   🤖 Consultando Gemini...")

        if ia_analisa_vaga(titulo, descricao):

            print("   ✅ Aprovada")

            enviar_alerta_telegram(
                titulo,
                empresa,
                link,
                localizacao
            )

            salvar_vaga_no_historico(
                id_vaga,
                "aprovada"
            )

        else:

            print("   ❌ Rejeitada")

            salvar_vaga_no_historico(
                id_vaga,
                "rejeitada"
            )

        time.sleep(3)
