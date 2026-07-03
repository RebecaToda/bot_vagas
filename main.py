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

if not SUPABASE_URL or not SUPABASE_KEY:
    print("⚠️ ALERTA: SUPABASE_URL ou SUPABASE_KEY não foram encontrados nas variáveis de ambiente!")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Dicionário de buscas ULTRA COMPLETO com todas as ramificações de TI (Júnior/Trainee)
SEARCHES = {
    "São Paulo, SP": [
        "Desenvolvedor Júnior", "Programador Júnior", "Analista de Desenvolvimento Júnior",
        "Junior Web Developer", "Desenvolvedor Mobile Júnior", "Desenvolvedor iOS Júnior",
        "Desenvolvedor Android Júnior", "Junior Frontend Engineer", "Junior Backend Engineer",
        "Analista de Dados Júnior", "Cientista de Dados Júnior", "Engenheiro de Dados Júnior",
        "Analista de BI Júnior", "Analista de Analytics Júnior", "Analista de QA Júnior",
        "Analista de Testes Júnior", "QA Engineer Junior", "Test Automation Junior",
        "Analista de Segurança da Informação Júnior", "Analista de SOC Júnior", "Cyber Security Junior",
        "Analista de Defesa Cibernética Júnior", "Analista DevOps Júnior", "Analista de Nuvem Júnior",
        "Cloud Engineer Junior", "Junior SRE", "Analista de Infraestrutura Júnior",
        "Analista de Redes Júnior", "Analista de Suporte Júnior", "Analista de Suporte L3 Júnior",
        "Analista Salesforce Júnior", "Analista SAP Júnior", "Technical Customer Success Júnior",
        "Analista de Sistemas Júnior", "Analista de Requisitos Júnior", "Analista de Negócios Júnior",
        "Product Owner Júnior", "Scrum Master Júnior", "Analista de Processos TI Júnior",
        "Trainee Tecnologia", "Trainee TI"
    ],
    "Curitiba, PR": [
        "Desenvolvedor Júnior", "Programador Júnior", "Analista de Sistemas Júnior",
        "Analista de Dados Júnior", "Analista de QA Júnior", "Analista de Segurança Júnior",
        "Analista DevOps Júnior", "Analista de Suporte Júnior", "Trainee Tecnologia",
        "Desenvolvedor Mobile Júnior"
    ],
    "Remote": [
        "Junior Software Engineer", "Junior Software Developer", "Junior Backend Developer",
        "Junior Frontend Developer", "Junior Full Stack Developer", "Junior Mobile Developer",
        "Junior DevOps Engineer", "Junior Cloud Engineer", "Junior SRE Engineer",
        "Junior Data Analyst", "Junior Data Engineer", "Junior QA Engineer",
        "Junior QA Automation Engineer", "Junior Cyber Security Analyst", "Junior Systems Analyst",
        "Junior Product Owner", "Junior IT Support Analyst", "Technical Support Engineer Junior",
        "Junior Technical Customer Success", "Entry Level Software Engineer",
        "Graduate Software Engineer", "Trainee Software Engineer"
    ]
}

# Filtros estáticos para economizar processamento e dinheiro (Bloqueando níveis superiores e estágios)
PALAVRAS_PROIBIDAS = [
    "senior", "sênior", "pleno", "staff", "lead", "principal", "manager", "coordinator",
    "especialista", "arquiteto", "estagio", "estágio", "intern", "internship", "médio", "medio"
]

# 🔥 EXPANDIDO: Filtros mais rígidos de texto para cortar anos de experiência direto na leitura
EXPERIENCIA_PROIBIDA = [
    "3+ years", "4+ years", "5+ years", "2+ years", "1+ years",
    "3 anos", "4 anos", "5 anos", "2 anos", "1 ano",
    "minimum 3 years", "mínimo de 3 anos", "mínimo de 2 anos", "mínimo de 1 ano",
    "comprovada de 1", "comprovada de 2", "experiência mínima", "experiencia minima"
]

def vaga_ja_analisada(id_vaga):
    if not supabase:
        return False
    try:
        resposta = supabase.table("historico_vagas").select("id_vaga").eq("id_vaga", id_vaga).execute()
        return len(resposta.data) > 0
    except Exception as e:
        print(f"  ❌ ERRO CRÍTICO AO CONSULTAR SUPABASE: {e}")
        return False

def salvar_vaga_no_historico(id_vaga, status):
    if not supabase:
        print("  ⚠️ Gravação abortada: Supabase não inicializado (verifique as chaves).")
        return
    try:
        resposta = supabase.table("historico_vagas").insert({"id_vaga": id_vaga, "status_analise": status}).execute()
        print(f"  💾 Gravado no Supabase com status: {status}")
    except Exception as e:
        # 🔥 Agora o erro vai aparecer escancarado no console do GitHub Actions se falhar
        print(f"  ❌ ERRO CRÍTICO AO SALVAR NO SUPABASE: {e}")

def passa_na_peneira_geografica(vaga):
    location = str(vaga.get("location", "")).strip().lower()
    is_remote = vaga.get("is_remote", False)
    if is_remote:
        return True
    if "são paulo" in location or "sp" in location or "curitiba" in location or "pr" in location:
        return True
    return False

def passa_filtro_basico(titulo, descricao):
    titulo = str(titulo or "").lower()
    descricao = str(descricao or "").lower()

    if any(x in titulo for x in PALAVRAS_PROIBIDAS):
        return False

    if any(x.lower() in descricao for x in EXPERIENCIA_PROIBIDA):
        return False

    return True

class ResultadoVaga(BaseModel):
    aprovada: bool
    motivo: str

def ia_analisa_vaga(titulo, descricao):
    # 🔥 PROMPT RECALIBRADO: Muito mais agressivo e tolerância zero com experiência prévia
    prompt = f"""
    Você é um recrutador técnico extremamente rigoroso. Analise a vaga de TI e decida se um profissional RECENTEMENTE FORMADO no curso de 'Análise e Desenvolvimento de Sistemas' que NÃO POSSUI NENHUMA EXPERIÊNCIA PROFISSIONAL ANTERIOR NA ÁREA pode se candidatar e ser contratado.

    REGRAS DE REJEIÇÃO ABSOLUTA (Se violar uma, marque aprovada=false imediatamente):
    1. Se o texto exigir qualquer tempo mínimo de experiência prévia (ex: 'mínimo 6 meses', '1 ano de experiência', 'experiência com desenvolvimento', 'experiência comprovada anterior').
    2. Se a descrição listar requisitos avançados que deixem claro não ser uma vaga para iniciantes do zero.
    3. Se exigir estritamente Bacharelado de 4 a 5 anos (Engenharia/Ciência da Computação) sem abrir margem para tecnólogos (ADS).
    4. Se o texto indicar que o processo foi encerrado.

    REGRAS DE APROVAÇÃO:
    1. A vaga deve ser categoricamente Júnior ou Trainee nível de entrada (Entry Level / Graduate).
    2. O texto deve deixar explícito que aceita pessoas sem experiência prévia, focando apenas em conhecimento teórico, projetos acadêmicos ou portfólio pessoal.

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
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown", "disable_web_page_preview": True})
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
                is_remote_search = (local == "Remote")
                loc_param = "United States" if is_remote_search else local 

                jobs_df = scrape_jobs(
                    site_name=["google", "linkedin", "indeed"],
                    search_term=termo,
                    location=loc_param,
                    is_remote=is_remote_search,
                    results_wanted=100,  
                    hours_old=4320,      
                )

                if jobs_df.empty:
                    continue

                vagas_reais.extend(jobs_df.to_dict(orient="records"))

            except Exception as e:
                print(f"  ❌ Erro na busca: {e}")

    print(f"\nTotal bruto: {len(vagas_reais)} vagas encontradas.")

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

    for vaga in vagas_reais:
        id_vaga = str(vaga.get("id", "")) or vaga.get("job_url", "")
        titulo = vaga.get("title", "")
        empresa = vaga.get("company", "")
        descricao = vaga.get("description", "")
        localizacao = vaga.get("location", "")
        link = vaga.get("job_url", "")

        print(f"👀 {titulo} ({empresa})")

        if vaga_ja_analisada(id_vaga):
            print("   ⏩ Já analisada em execuções anteriores. Pulando...")
            continue

        if not passa_na_peneira_geografica(vaga):
            print("   🌎 Localização incompatível.")
            salvar_vaga_no_historico(id_vaga, "localizacao")
            continue

        if not passa_filtro_basico(titulo, descricao):
            print("   🚫 Eliminada pelo filtro básico de palavras proibidas ou descrição ausente.")
            salvar_vaga_no_historico(id_vaga, "prefiltro")
            continue

        print("   🤖 Consultando Gemini...")
        if ia_analisa_vaga(titulo, descricao):
            print("   ✅ Aprovada! Enviando para o Telegram e registrando...")
            enviar_alerta_telegram(titulo, empresa, link, localizacao)
            salvar_vaga_no_historico(id_vaga, "aprovada")
        else:
            print("   ❌ Rejeitada pela IA. Registrando decisão no banco...")
            salvar_vaga_no_historico(id_vaga, "rejeitada")

        time.sleep(3)
