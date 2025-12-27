# Dockerfile
FROM python:3.11-slim

# 시스템 의존성 설치 (matplotlib 및 분석 도구용)
RUN apt-get update && apt-get install -y \
    curl \
    libpng-dev \
    libfreetype6-dev \
    vim \
    tree \
    && rm -rf /var/lib/apt/lists/*

# 작업 디렉토리를 /workspace로 변경
WORKDIR /workspace

# Requirements 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스 코드 전체 복사 (컨테이너 이미지 빌드용)
COPY . .

# Jupyter Notebook 실행 (작업 디렉토리를 /workspace로 지정)
CMD ["jupyter", "notebook", "--ip=0.0.0.0", "--port=8888", "--no-browser", "--allow-root", "--NotebookApp.token=''"]