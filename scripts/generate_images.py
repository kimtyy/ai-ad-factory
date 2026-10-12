"""
scripts/generate_images.py - 시골책방 씬별 AI 이미지 생성 (+ 원하면 영상까지)

  python scripts/generate_images.py "하늘과 바람과 별과 시"                 # 책 정보 수집 → 대본 → 씬 1~N-1 AI 이미지
  python scripts/generate_images.py "하늘과 바람과 별과 시" --render        # + 실제 영상 렌더 (마지막 씬은 진짜 표지 카드)
  python scripts/generate_images.py "책" --template fable --quote "책 속 원문"

키: 기존 .env GEMINI_API_KEY 하나 (이미지·대본 공용, 추가 키 없음)
모델: .env IMAGE_MODEL (기본 gemini-3.1-flash-image - 이 키로는 Imagen 3이 404), 해상도 IMAGE_SIZE (기본 2K)
결과: outputs/jobs/<작업>/assets/ai_images/ (ai_scene_NN_*.jpg + ai_scene_prompts.json)
"""
import argparse
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
from dotenv import load_dotenv  # noqa: E402

load_dotenv()


def main(argv=None) -> int:
    import logging
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for n in ("httpx", "google_genai", "ad_factory.guardrail"):
        logging.getLogger(n).setLevel(logging.WARNING)
    from core import pipeline
    from core.book_info import BookInfoError, collect_book
    from core.job import JobContext
    from core.planners.book import REFLECT_MIN_INFO

    ap = argparse.ArgumentParser(description="시골책방 씬별 AI 이미지 생성")
    ap.add_argument("book", help="책 제목 또는 ISBN")
    ap.add_argument("--store", default="시골책방")
    ap.add_argument("--template", choices=["auto", "basic", "fable", "reflect"], default="auto")
    ap.add_argument("--quote", default="", help="책 속 원문 (우화 템플릿)")
    ap.add_argument("--render", action="store_true", help="영상까지 렌더")
    args = ap.parse_args(argv)
    api_key = os.getenv("GEMINI_API_KEY")
    t0 = time.time()
    try:
        info = collect_book(args.book, out_dir=os.path.join(ROOT, "outputs", "books"))
    except BookInfoError as e:
        print(f"[실패] {e}")
        return 1
    print(f"[책] {info.summary()}")
    tpl = args.template if args.template != "auto" else (
        "fable" if args.quote else "reflect" if len(info.description) >= REFLECT_MIN_INFO else "basic")
    job = JobContext.create(app="generate_images")
    os.makedirs(job.uploads_dir, exist_ok=True)
    cover = shutil.copy(info.cover_path, os.path.join(job.uploads_dir, "01_cover.jpg")) if info.cover_path else None
    spec = pipeline.JobSpec(mode="book", name=args.store, book_title=info.title, book_author=info.author,
                            book_publisher=info.publisher, usp=info.description, book_quote=args.quote,
                            book_link=info.purchase_link, book_template=tpl, media_paths=[cover] if cover else [],
                            api_key=api_key)
    scenes = pipeline.plan(spec, job)            # 대본 + 씬 이미지 생성
    print(f"\n[대본·이미지] {time.time() - t0:.1f}초 · 템플릿 {tpl} · 대본 엔진 {scenes[0].get('script_engine')}")
    for v in (scenes[0].get("book") or {}).get("visuals") or []:
        print(f"  {v}")
    for s in scenes:
        print(f"  씬{s['scene_index']} [{s['role']}] {os.path.basename(s.get('source_path') or '')} | {s['voiceover']}")
        if s.get("ai_prompt"):
            print(f"      ↳ {s['ai_prompt'][:160]}…")
    print(f"이미지 폴더: {os.path.join(job.assets_dir, 'ai_images')}")
    if args.render:
        res = pipeline.render(spec, job, scenes)
        from core.render.ffmpeg_assemble import probe
        print(f"\n[렌더] {res.final_path} · {probe(res.final_path)['duration']:.1f}초 · 총 {time.time() - t0:.0f}초")
    return 0


if __name__ == "__main__":
    sys.exit(main())
