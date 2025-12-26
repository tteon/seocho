#!/bin/bash

# 공통 설정: 메인 설정 파일과 Opik 설정 파일을 모두 지정합니다.
COMPOSE_FILES="-f docker-compose.yml -f opik/deployment/docker-compose/docker-compose.yaml"
OPIK_PROFILE="--profile opik"
COMPOSE_CMD="docker compose $COMPOSE_FILES $OPIK_PROFILE"

# 네트워크 확인 및 생성: 외부 네트워크(opik_default)가 없으면 생성합니다.
if ! docker network ls | grep -q "opik_default"; then
  echo "opik_default 네트워크가 없어 새로 생성합니다..."
  docker network create opik_default
fi

case "$1" in
  start)
    echo "서비스를 시작합니다..."
    $COMPOSE_CMD up -d
    ;;

  stop)
    echo "서비스를 중지합니다..."
    $COMPOSE_CMD down
    ;;

  build)
    echo "이미지를 다시 빌드하고 서비스를 시작합니다..."
    $COMPOSE_CMD up -d --build
    ;;

  prune)
    echo "사용하지 않는 컨테이너, 네트워크, 이미지를 정리합니다..."
    docker system prune -f
    ;;

  restart)
    echo "서비스를 재시작합니다..."
    $COMPOSE_CMD down
    $COMPOSE_CMD up -d
    ;;

  logs)
    echo "실시간 로그를 확인합니다 (나가려면 Ctrl+C)..."
    $COMPOSE_CMD logs -f
    ;;

  *)
    echo "사용법: ./manage.sh {start|stop|build|prune|restart|logs}"
    exit 1
    ;;
esac