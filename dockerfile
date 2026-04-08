# Usa uma imagem oficial do Python, versão enxuta
FROM python:3.12-slim

# Define o diretório de trabalho dentro do container
WORKDIR /sgc

# Instala as dependências do SO necessárias para o WeasyPrint gerar PDFs
# CORREÇÃO: Pacote libgdk-pixbuf-2.0-0 com a nomenclatura atualizada
RUN apt-get update && apt-get install -y \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    shared-mime-info \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copia apenas o arquivo de dependências primeiro (para aproveitar o cache do Docker)
COPY requirements.txt .

# Instala as bibliotecas do Python
RUN pip install --no-cache-dir -r requirements.txt

# Copia o restante do código do projeto para dentro do container
COPY . .

# Expõe a porta que o Flask vai usar
EXPOSE 5000

# Comando para iniciar o sistema
CMD ["python", "app/main.py"]