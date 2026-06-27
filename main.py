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

# Dicionário de buscas HISTÓRICO E COMPLETO com todas as ramificações de TI (Júnior/Trainee)
SEARCHES = {
    "São Paulo, SP": [
        "Desenvolvedor Júnior",
        "Programador Júnior",
        "Analista de Desenvolvimento Júnior",
        "Junior Web Developer",
        "Desenvolvedor Mobile Júnior",
        "Desenvolvedor iOS Júnior",
        "Desenvolvedor Android Júnior",
        "Junior Frontend Engineer",
        "Junior Backend Engineer",
        "Analista de Dados Júnior",
        "Cientista de Dados Júnior",
        "Engenheiro de Dados Júnior",
        "Analista de BI Júnior",
        "Analista de Analytics Júnior",
        "Analista de QA Júnior",
        "Analista de Testes Júnior",
        "QA Engineer Junior",
        "Test Automation Junior",
        "Analista de Segurança da Informação Júnior",
        "Analista de SOC Júnior",
        "Cyber Security Junior",
        "Analista de Defesa Cibernética Júnior",
        "Analista DevOps Júnior",
        "Analista de Nuvem Júnior",
        "Cloud Engineer Junior",
        "Junior SRE",
        "Analista de Infraestrutura Júnior",
        "Analista de Redes Júnior",
        "Analista de Suporte Júnior",
        "Analista de Suporte L3 Júnior",
        "Analista Salesforce Júnior",
        "Analista SAP Júnior",
        "Technical Customer Success Júnior",
        "Analista de Sistemas Júnior",
        "Analista de Requisitos Júnior",
        "Analista de Negócios Júnior",
        "Product Owner Júnior",
        "Scrum Master Júnior",
        "Analista de Processos TI Júnior",
        "Trainee Tecnologia",
        "Trainee TI",
    ],

    "Curitiba, PR": [
        "Desenvolvedor Júnior",
        "Programador Júnior",
        "Analista de Sistemas Júnior",
        "Analista de Dados Júnior",
        "Analista de QA Júnior",
        "Analista de Segurança Júnior",
        "Analista DevOps Júnior",
        "Analista de Suporte Júnior",
        "Trainee Tecnologia",
        "Desenvolvedor Mobile Júnior",
    ],

    "Remote": [
        "Junior Software Engineer",
        "Junior Software Developer",
        "Junior Backend Developer",
        "Junior Frontend Developer",
        "Junior Full Stack Developer",
        "Junior Mobile Developer",
        "Junior DevOps Engineer",
        "Junior Cloud Engineer",
        "Junior SRE Engineer",
        "Junior Data Analyst",
        "Junior Data Engineer",
        "Junior QA Engineer",
        "Junior QA Automation Engineer",
        "Junior Cyber Security Analyst",
        "Junior Systems Analyst",
        "Junior Product Owner",
        "Junior IT Support Analyst",
        "Technical Support Engineer Junior",
        "Junior Technical Customer Success",
        "Entry Level Software Engineer",
        "Graduate Software Engineer",
        "Trainee Software Engineer",
    ]
}

# Filtros estáticos para economizar processamento e dinheiro (Bloqueando níveis superiores e estágios)
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
    "estagio",
    "estágio",
    "intern",
    "internship",
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
    """
    Valida se a vaga atende aos critérios de localidade (SP, PR ou Remota).
    """
    location = str(vaga.get("location", "")).strip().lower()
    is_remote = vaga.get("is_remote", False)
    if is_remote:
        return True
    if "são paulo" in location or "sp" in location or "curitiba" in location or "pr" in location:
        return True
    return False

def passa_filtro_basico(titulo, descricao):
    """
    Elimina vagas óbvias de nível superior, com alta experiência ou de estágio antes de chamar a IA.
    """
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
    """
    Usa o Gemini para ler o escopo e decidir se aceita ADS e nível de entrada (sem experiência).
    Inclui sistema de auto-recuperação inteligente para erros de cota (429).
    """
    prompt = f"""
    Você é um recrutador técnico especialista em tecnologia. Analise a vaga e decida se um estudante ou formado do curso de 'Análise e Desenvolvimento de Sistemas (Tecnólogo de 2 a 3 anos)' que NÃO POSSUI EXPERIÊNCIA PROFISSIONAL PRÉVIA pode se candidatar.

    Regras de Rejeição:
    1. Se exigir EXCLUSIVAMENTE Bacharelado de 4-5 anos sem aceitar equivalentes.
    2. Se exigir tempo mínimo de experiência profissional comprovada (ex: 'mínimo de 1 ano', '2+ anos de experiência').
    3. Se o texto da descrição indicar claramente que a vaga já foi ENCERRADA, finalizada ou que não aceita mais inscrições.

    Regras de Aprovação:
    1. Se for uma vaga de Trainee ou Júnior que aceite Tecnólogo/ADS.
    2. E se deixar explícito que NÃO exige experiência profissional prévia (vagas de nível de entrada).

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

    print("\n🕵️‍♂️ Buscando vagas na internet inteira via Google Jobs, LinkedIn e Indeed...")

    vagas_reais = []

    for local, termos in SEARCHES.items():

        print(f"\n📍 {local}")

        for termo in termos:

            print(f"🔎 {termo}")

            try:
                # Ajuste dinâmico para a busca remota internacional
                is_remote_search = (local == "Remote")
                loc_param = "United States" if is_remote_search else local 

                jobs_df = scrape_jobs(
                    site_name=["google", "linkedin", "indeed"],
                    search_term=termo,
                    location=loc_param,
                    is_remote=is_remote_search,
                    results_wanted=100,  # Traz uma lista gorda do histórico por termo
                    hours_old=4320,      # Abre a varredura para os últimos 6 meses (4320 horas)
                )

                if jobs_df.empty:
                    continue

                vagas_reais.extend(
                    jobs_df.to_dict(orient="records")
                )

            except Exception as e:
                print(f"  ❌ Erro na busca: {e}")

    print(f"\nTotal bruto: {len(vagas_reais)} vagas encontradas.")

    # Remove duplicatas cruzadas de plataformas diferentes
    vagas_unicas = {}
    for vaga in vagas_reais:
        chave = (
            vaga.get("job_url")
            or vaga.get("id")
            or (str(vaga.get("title", "")) + str(vaga.get("company", "")))
        )
        vagas_unicas[chave] = vaga

    vagas_reais = list(vagas_unicas.values())
    print(f"Após remover duplicatas: {len(vagas_reais)} vagas únicas para processar.\n")

    # Loop de filtragem inteligente e acionamento da IA
    for vaga in vagas_reais:
        id_vaga = str(vaga.get("id", "")) or vaga.get("job_url", "")
        titulo = vaga.get("title", "")
        empresa = vaga.get("company", "")
        descricao = vaga.get("description", "")
        localizacao = vaga.get("location", "")
        link = vaga.get("job_url", "")

        print(f"👀 {titulo} ({empresa})")

        # 1. Checa a memória do Supabase
        if vaga_ja_analisada(id_vaga):
            print("   ⏩ Já analisada em execuções anteriores. Pulando...")
            continue

        # 2. Passa pelo filtro geográfico
        if not passa_na_peneira_geografica(vaga):
            print("   🌎 Localização incompatível.")
            salvar_vaga_no_historico(id_vaga, "localizacao")
            continue

        # 3. Passa pelo pré-filtro textual (Filtro Básico)
        if not passa_filtro_basico(titulo, descricao):
            print("   🚫 Eliminada pelo filtro básico de palavras proibidas.")
            salvar_vaga_no_historico(id_vaga, "prefiltro")
            continue

        # 4. Envia o texto limpo para o julgamento final do Gemini
        print("   🤖 Consultando Gemini...")
        if ia_analisa_vaga(titulo, descricao):
            print("   ✅ Aprovada! Enviando para o Telegram e registrando...")
            enviar_alerta_telegram(titulo, empresa, link, localizacao)
            salvar_vaga_no_historico(id_vaga, "aprovada")
        else:
            print("   ❌ Rejeitada pela IA. Registrando decisão no banco...")
            salvar_vaga_no_historico(id_vaga, "rejeitada")

        time.sleep(3) # Pequena pausa de segurança entre vagas
