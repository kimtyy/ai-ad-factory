"""
core/job.py - 작업(Job) 단위 작업 공간 격리

영상 1편 제작 = Job 1개. 모든 에셋·기획·결과물은 outputs/jobs/{job_id}/ 아래에만 저장되어
동시에 여러 사용자/세션이 렌더링해도 서로의 파일을 덮어쓰지 않습니다.

    outputs/jobs/{job_id}/
      job.json            # 작업 메타데이터 (생성 시각, 모드 등)
      storyboard.json     # 씬 기획 (image_path, media 등 에셋 배정 포함)
      assets/             # 크롤링 사진, 씬별 키프레임/클립/음성 (scene_XX.*)
      uploads/            # 사용자 업로드 원본 (사진, 스마트폰 영상)
      final/              # 최종 렌더링 결과 (final.mp4)
"""
import os
import re
import json
import time
import uuid
import shutil
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ad_factory.job")

DEFAULT_JOBS_ROOT = os.path.join("outputs", "jobs")
_JOB_ID_PATTERN = re.compile(r"^[0-9A-Za-z_-]{1,64}$")


def new_job_id() -> str:
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"


@dataclass
class JobContext:
    job_id: str
    root: str = DEFAULT_JOBS_ROOT
    meta: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        # job_id는 경로에 그대로 쓰이므로 경로 탈출(../ 등)을 막기 위해 형식을 제한
        if not _JOB_ID_PATTERN.match(self.job_id):
            raise ValueError(f"잘못된 job_id 형식: {self.job_id!r}")
        self.root = os.path.abspath(self.root)

    # ---------------------------------------------------------
    # 생성 / 로드
    # ---------------------------------------------------------
    @classmethod
    def create(cls, root: str = DEFAULT_JOBS_ROOT, **meta) -> "JobContext":
        job = cls(job_id=new_job_id(), root=root, meta={"created_at": time.strftime("%Y-%m-%dT%H:%M:%S"), **meta})
        job.ensure_dirs()
        job.save_meta()
        logger.info(f"새 작업 공간 생성: {job.workdir}")
        return job

    @classmethod
    def load(cls, job_id: str, root: str = DEFAULT_JOBS_ROOT) -> "JobContext":
        job = cls(job_id=job_id, root=root)
        if not os.path.isdir(job.workdir):
            raise FileNotFoundError(f"작업 공간이 없습니다: {job.workdir}")
        if os.path.exists(job.meta_path):
            with open(job.meta_path, "r", encoding="utf-8") as f:
                job.meta = json.load(f)
        job.ensure_dirs()
        return job

    # ---------------------------------------------------------
    # 경로
    # ---------------------------------------------------------
    @property
    def workdir(self) -> str:
        return os.path.join(self.root, self.job_id)

    @property
    def assets_dir(self) -> str:
        return os.path.join(self.workdir, "assets")

    @property
    def uploads_dir(self) -> str:
        return os.path.join(self.workdir, "uploads")

    @property
    def final_dir(self) -> str:
        return os.path.join(self.workdir, "final")

    @property
    def plan_path(self) -> str:
        return os.path.join(self.workdir, "storyboard.json")

    @property
    def meta_path(self) -> str:
        return os.path.join(self.workdir, "job.json")

    def asset_path(self, filename: str) -> str:
        return os.path.join(self.assets_dir, filename)

    def scene_asset(self, scene_num: int, ext: str, suffix: str = "") -> str:
        """씬별 에셋 경로 (예: scene_03.mp4, scene_03_thumb.jpg)"""
        return self.asset_path(f"scene_{int(scene_num):02d}{suffix}{ext}")

    def ensure_dirs(self):
        for d in (self.assets_dir, self.uploads_dir, self.final_dir):
            os.makedirs(d, exist_ok=True)

    # ---------------------------------------------------------
    # 기획 / 메타데이터 저장
    # ---------------------------------------------------------
    def save_plan(self, scenes: List[Dict[str, Any]]):
        tmp = self.plan_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(scenes, f, ensure_ascii=False, indent=2, default=str)
        os.replace(tmp, self.plan_path)

    def load_plan(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.plan_path):
            return []
        with open(self.plan_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_meta(self):
        with open(self.meta_path, "w", encoding="utf-8") as f:
            json.dump({"job_id": self.job_id, **self.meta}, f, ensure_ascii=False, indent=2)


def cleanup_old_jobs(root: str = DEFAULT_JOBS_ROOT, max_age_days: float = 7.0, keep: Optional[List[str]] = None) -> List[str]:
    """max_age_days보다 오래된 작업 공간 삭제 (keep에 있는 job_id는 보존). 삭제한 job_id 목록 반환."""
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        return []
    keep = set(keep or [])
    cutoff = time.time() - max_age_days * 86400
    removed = []
    for name in os.listdir(root):
        path = os.path.join(root, name)
        if name in keep or not os.path.isdir(path) or not _JOB_ID_PATTERN.match(name):
            continue
        if os.path.getmtime(path) < cutoff:
            shutil.rmtree(path, ignore_errors=True)
            removed.append(name)
    return removed
