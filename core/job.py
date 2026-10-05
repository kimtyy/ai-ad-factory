"""
core/job.py - 작업(Job) 단위 작업 공간 격리

영상 1편 제작 = Job 1개. 모든 에셋·기획·결과물은 outputs/jobs/{job_id}/ 아래에만 저장되어
동시에 여러 사용자/세션이 렌더링해도 서로의 파일을 덮어쓰지 않습니다.

폴더는 지연 생성(lazy)됩니다: create()는 ID만 발급하고, 처음으로 에셋/업로드/결과/기획을 저장하려고
해당 경로를 요청하는 시점에 디렉터리와 job.json이 만들어집니다 (접속만 한 세션은 디스크에 흔적이 남지 않음).

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
        """작업 ID만 발급 (디스크에는 아직 아무것도 만들지 않음)"""
        return cls(job_id=new_job_id(), root=root, meta={"created_at": time.strftime("%Y-%m-%dT%H:%M:%S"), **meta})

    @classmethod
    def load(cls, job_id: str, root: str = DEFAULT_JOBS_ROOT) -> "JobContext":
        job = cls(job_id=job_id, root=root)
        if not os.path.isdir(job.workdir):
            raise FileNotFoundError(f"작업 공간이 없습니다: {job.workdir}")
        if os.path.exists(job.meta_path):
            with open(job.meta_path, "r", encoding="utf-8") as f:
                job.meta = json.load(f)
        return job

    # ---------------------------------------------------------
    # 경로
    # ---------------------------------------------------------
    @property
    def workdir(self) -> str:
        return os.path.join(self.root, self.job_id)

    @property
    def exists(self) -> bool:
        return os.path.isdir(self.workdir)

    def _subdir(self, name: str) -> str:
        """하위 폴더 경로 (요청 = 곧 저장한다는 뜻이므로 이 시점에 폴더와 job.json을 생성)"""
        path = os.path.join(self.workdir, name)
        if not os.path.isdir(path):
            self._materialize()
            os.makedirs(path, exist_ok=True)
        return path

    def _materialize(self):
        if not os.path.isdir(self.workdir):
            os.makedirs(self.workdir, exist_ok=True)
            logger.info(f"작업 공간 생성 (첫 저장): {self.workdir}")
        if not os.path.exists(self.meta_path):
            self.save_meta()

    @property
    def assets_dir(self) -> str:
        return self._subdir("assets")

    @property
    def uploads_dir(self) -> str:
        return self._subdir("uploads")

    @property
    def final_dir(self) -> str:
        return self._subdir("final")

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
        """모든 하위 폴더를 즉시 생성 (일반적으로는 필요 없음: 경로 요청 시 자동 생성)"""
        for name in ("assets", "uploads", "final"):
            self._subdir(name)

    # ---------------------------------------------------------
    # 기획 / 메타데이터 저장
    # ---------------------------------------------------------
    def save_plan(self, scenes: List[Dict[str, Any]]):
        self._materialize()
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
        os.makedirs(self.workdir, exist_ok=True)
        with open(self.meta_path, "w", encoding="utf-8") as f:
            json.dump({"job_id": self.job_id, **self.meta}, f, ensure_ascii=False, indent=2)


def is_empty_job(path: str) -> bool:
    """job.json 외에 저장된 파일이 하나도 없는 작업 공간인지"""
    for dirpath, _, files in os.walk(path):
        for f in files:
            if not (dirpath == path and f == "job.json"):
                return False
    return True


def _last_modified(path: str) -> float:
    """폴더 안 가장 최근 수정 시각 (하위 파일 포함)"""
    latest = os.path.getmtime(path)
    for dirpath, dirnames, files in os.walk(path):
        for n in dirnames + files:
            try:
                latest = max(latest, os.path.getmtime(os.path.join(dirpath, n)))
            except OSError:
                pass
    return latest


def cleanup_old_jobs(root: str = DEFAULT_JOBS_ROOT, max_age_days: Optional[float] = 7.0, keep: Optional[List[str]] = None,
                     empty_only: bool = False, max_age_hours: Optional[float] = None) -> List[str]:
    """
    오래된 작업 공간 삭제. 삭제한 job_id 목록 반환.
    - empty_only=True: 저장된 파일이 없는 빈 작업 공간만 삭제 (앱 시작 시 자동 정리용, 사용자 결과물은 건드리지 않음)
    - 나이 기준: max_age_hours가 있으면 그것을, 없으면 max_age_days (폴더 안 마지막 수정 시각 기준)
    - keep에 있는 job_id는 항상 보존
    """
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        return []
    keep = set(keep or [])
    age_sec = max_age_hours * 3600 if max_age_hours is not None else (max_age_days or 0) * 86400
    cutoff = time.time() - age_sec
    removed = []
    for name in os.listdir(root):
        path = os.path.join(root, name)
        if name in keep or not os.path.isdir(path) or not _JOB_ID_PATTERN.match(name):
            continue
        if empty_only and not is_empty_job(path):
            continue
        if _last_modified(path) < cutoff:
            shutil.rmtree(path, ignore_errors=True)
            removed.append(name)
    if removed:
        logger.info(f"작업 공간 정리 ({'빈 폴더만' if empty_only else '전체'}): {len(removed)}개 삭제")
    return removed
