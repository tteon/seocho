import os
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from huggingface_hub import login
from datasets import load_dataset
import opik
from opik import Opik

# 환경 변수 로드 및 SDK 매핑 (대화형 입력 방지)
load_dotenv()
os.environ["OPIK_URL_OVERRIDE"] = os.getenv("OPIK_BASE_URL", "http://localhost:5173/api")
os.environ["OPIK_WORKSPACE"] = os.getenv("OPIK_WORKSPACE", "seocho-kgbuild")
os.environ["OPIK_PROJECT_NAME"] = os.getenv("OPIK_PROJECT_NAME", "kgbuild")

def sanitize_value(v):
    """NumPy 타입을 Python 기본 타입으로 변환하여 JSON 에러 방지"""
    if isinstance(v, np.ndarray):
        return v.tolist()
    if isinstance(v, (np.int64, np.int32, np.float64, np.float32)):
        return v.item()
    if isinstance(v, list):
        return [sanitize_value(i) for i in v]
    if isinstance(v, dict):
        return {k: sanitize_value(val) for k, val in v.items()}
    return v

def main():
    # 1. Hugging Face 데이터 로드
    print(">>> Hugging Face 인증 및 데이터셋 다운로드 시작...")
    login(token=os.getenv("HUGGINGFACE_TOKEN"))
    dataset = load_dataset("Linq-AI-Research/FinDER", split="train")
    df = dataset.to_pandas()
    print(f">>> 로드 완료: 총 {len(df)}개 행")

    # 2. Opik 클라이언트 설정
    client = Opik()
    opik_dataset = client.get_or_create_dataset(name="fibo-evaluation-dataset")

    # 3. 업로드 데이터 구성
    print(">>> 데이터 변환 및 정제 중...")
    dataset_items = []
    for _, row in df.iterrows():
        dataset_items.append({
            "input": {"text": str(row.get("text", ""))},
            "expected_output": str(row.get("answer", "")),
            "metadata": {
                "id": sanitize_value(row.get("_id")),
                "category": sanitize_value(row.get("category")),
                "reasoning": sanitize_value(row.get("reasoning")),
                "type": sanitize_value(row.get("type")),
                "references": sanitize_value(row.get("references"))
            }
        })

    # 4. 배치 업로드 실행 (1000개 단위)
    print(f">>> Opik 데이터셋 업로드 시작...")
    batch_size = 1000
    for i in range(0, len(dataset_items), batch_size):
        batch = dataset_items[i : i + batch_size]
        opik_dataset.insert(items=batch)
        print(f"    진행: {i + len(batch)} / {len(dataset_items)} 완료")

    print(">>> 모든 프로세스가 성공적으로 종료되었습니다.")

if __name__ == "__main__":
    main()