"""選用的 R2 持久化：容器睡眠重啟後，用 R2 復原股票名單與最近一次掃描結果。

未設定 R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY / R2_BUCKET 時，
所有函式都是 no-op，不影響本機執行或沒接 R2 的容器部署。
"""

import os
from pathlib import Path


def _client():
    if not all(os.environ.get(k) for k in
               ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")):
        return None
    import boto3
    return boto3.client(
        "s3",
        endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
    )


def sync_down_all(data_dir: Path):
    """容器啟動時：把 R2 裡保存的檔案同步回本機 data_dir。"""
    client = _client()
    if client is None:
        return
    bucket = os.environ["R2_BUCKET"]
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket):
            for obj in page.get("Contents", []):
                client.download_file(bucket, obj["Key"], str(data_dir / obj["Key"]))
        print(f"R2 還原完成：{data_dir}", flush=True)
    except Exception as exc:
        print(f"R2 還原失敗（略過，視同全新啟動）：{exc}", flush=True)


def sync_up_all(data_dir: Path):
    """名單更新或掃描完成後：把 data_dir 目前的檔案同步上傳到 R2。"""
    client = _client()
    if client is None:
        return
    bucket = os.environ["R2_BUCKET"]
    try:
        for path in data_dir.glob("*"):
            if path.is_file():
                client.upload_file(str(path), bucket, path.name)
        print(f"R2 備份完成：{data_dir}", flush=True)
    except Exception as exc:
        print(f"R2 備份失敗（略過，本機資料不受影響）：{exc}", flush=True)
