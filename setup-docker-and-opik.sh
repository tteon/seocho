#!/bin/bash

# 1. 기존 구버전 및 관련 패키지 삭제
echo ">>> Removing old Docker versions..."
sudo apt-get remove -y docker docker-engine docker.io containerd runc docker-compose

# 2. 필수 패키지 설치 (git 포함)
echo ">>> Installing dependencies..."
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg git

# 3. Docker 공식 GPG 키 추가
echo ">>> Setting up Docker GPG key..."
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

# 4. 리포지토리 설정
echo ">>> Setting up Docker repository..."
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# 5. Docker Engine 및 Compose 플러그인 설치
echo ">>> Installing Docker and Compose plugin..."
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# 6. 서비스 시작 및 활성화
sudo systemctl enable docker
sudo systemctl start docker

# 7. 현재 사용자 권한 부여 (Permission Denied 해결)
sudo usermod -aG docker $USER

# --------------------------------------------------
# 8. Opik 플랫폼 설치 및 실행 로직
# --------------------------------------------------
echo ">>> Checking Opik installation..."

if [ -d "opik" ]; then
    echo "Opik directory already exists. Skipping clone..."
    cd opik
else
    echo "Cloning Opik repository..."
    git clone https://github.com/comet-ml/opik.git
    cd opik
fi

# Docker 권한이 아직 현재 세션에 적용되지 않았으므로, 
# 이 스크립트 내에서 즉시 실행을 위해 sudo를 사용하거나 안내 메시지를 출력합니다.
echo ">>> Starting Opik platform..."
# Opik 스크립트 실행 (Docker 권한 문제 방지를 위해 sudo 권한으로 실행 시도)
sudo ./opik.sh

echo "--------------------------------------------------"
echo "모든 프로세스가 완료되었습니다."
echo "주의: 현재 터미널 세션에서는 Docker 권한이 미비할 수 있습니다."
echo "새로운 터미널을 열거나 'exit' 후 재접속하여 작업을 계속하세요."
echo "--------------------------------------------------"