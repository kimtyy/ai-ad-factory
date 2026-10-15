"""
main.py - AI Short-form Ad Video Factory CLI 파이프라인 메인 진입점
"""
import os
import sys
import json
import argparse
import logging
from dotenv import load_dotenv

# Windows 터미널 한글/유니코드 인코딩 안전화
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

# Core 파이프라인 모듈
from core.analyzer import plan_storyboard
from core.asset_generator import AssetGenerator
from core.compositor import AdCompositor
from core.job import JobContext
from core.render.brand import BrandOverlay, EndCard
from core.planners import BUSINESS_TYPES, COUPANG_DISCLOSURE, plan_commerce_storyboard, plan_owner_storyboard
from core.media import is_video_file
from core.media.normalize import DEFAULT_TRIM_START

# 환경 변수 로드
load_dotenv()

console = Console(legacy_windows=False)

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S"
    )

def print_banner():
    banner_text = """
[bold cyan]====================================================================[/bold cyan]
[bold magenta]   [AI Short-form Ad Video Factory] 자동 광고 영상 파이프라인   [/bold magenta]
[bold cyan]====================================================================[/bold cyan]
[dim]  * Step 1: Gemini 3.6 Flash Vision 레퍼런스 분석 & 콘티 역기획[/dim]
[dim]  * Step 2: 실물 사진·영상 + 로컬 Ken Burns(2D 모션 비디오) + Edge-TTS(음성) 에셋 양산[/dim]
[dim]  * Step 3: MoviePy 기반 오디오 싱크 컷팅 & 9:16 타임라인 조립[/dim]
"""
    console.print(banner_text)

def check_env_keys(mock: bool = False) -> dict:
    """API 키 확인 및 경고 안내 (Google Gemini Key 하나로 전체 구동)"""
    keys = {
        "GEMINI_API_KEY": os.getenv("GEMINI_API_KEY"),
    }
    
    missing = [k for k, v in keys.items() if not v]
    if missing and not mock:
        console.print(Panel(
            f"[bold red]경고: 다음 필수 API Key가 .env에 설정되지 않았습니다:[/bold red]\n"
            f"[yellow]{', '.join(missing)}[/yellow]\n\n"
            f"[dim].env.example 파일을 참고하여 .env 파일에 키를 입력하거나, "
            f"--mock 옵션으로 테스트 모드를 실행하세요.[/dim]",
            title="[red]API Key 누락[/red]",
            border_style="red"
        ))
    return keys

def display_storyboard_table(scenes: list):
    table = Table(title="[Storyboard Plan] 씬별 콘티 기획안 (storyboard.json)", show_lines=True)
    table.add_column("씬", justify="center", style="cyan", no_wrap=True)
    table.add_column("길이", justify="center", style="green", no_wrap=True)
    table.add_column("상단 후킹 바 (Hook Title)", style="bold bright_yellow")
    table.add_column("세이프존 자막 (Caption)", style="bold white")
    table.add_column("나레이션 (Voiceover)", style="white")
    table.add_column("비주얼 연출 (Visual)", style="dim")
    table.add_column("카메라 모션 (Ken Burns)", style="magenta")

    for sc in scenes:
        table.add_row(
            str(sc.get("scene_index") or sc.get("scene_number", "-")),
            f"{sc.get('duration_sec', 2.5)}s",
            sc.get("hook_title", "") or "-",
            sc.get("caption", sc.get("voiceover", "")[:20]),
            sc.get("voiceover", ""),
            sc.get("visual_description", "")[:40] + "...",
            sc.get("video_motion_prompt_en", "")[:30] + "..."
        )
    console.print(table)


def main():
    parser = argparse.ArgumentParser(
        description="AI Short-form Ad Video Factory - 레퍼런스 역기획 및 숏폼 광고 자동 양산 CLI"
    )
    parser.add_argument("--mode", type=str, choices=["owner", "travel", "commerce"], default="travel",
                        help="제작 모드 (owner: 사장님 직영 홍보 / travel: 숙소 제휴 쇼츠 / commerce: 쇼핑 제휴 쇼츠)")
    parser.add_argument("--media", type=str, nargs="*", default=[], help="[owner/commerce] 사진·영상 경로 (순서대로 씬 배정)")
    parser.add_argument("--business-type", type=str, choices=list(BUSINESS_TYPES), default="food", help="[owner] 업종")
    parser.add_argument("--url", type=str, default=None, help="상품/숙소 상세 페이지 URL (입력 시 정보 및 이미지 자동 수집)")
    parser.add_argument("--ref", type=str, default="reference.mp4", help="레퍼런스 영상 경로 (기본값: reference.mp4)")
    parser.add_argument("--product", type=str, default=None, help="홍보 대상 제품/숙소명 (미입력 시 모드별 기본값 자동 적용)")
    parser.add_argument("--usp", type=str, default=None, help="핵심 소구점 (USP)")
    parser.add_argument("--scenes", type=int, default=None, help="목표 씬 개수 (미지정 시 영상 길이에 맞춰 자동 계산)")
    parser.add_argument("--tone", type=str, choices=["aggro", "expert", "review", "speed15"], default="aggro", help="대본 톤앤매너 프리셋")
    parser.add_argument("--duration", type=str, choices=["10s", "15s", "30s", "60s"], default="15s", help="영상 길이 프리셋")
    parser.add_argument("--audio-mode", type=str, choices=["voice_and_sub", "silent"], default="voice_and_sub", help="오디오 모드")
    parser.add_argument("--actor", type=str, choices=["female_20s", "female_30s", "male_20s", "male_30s"], default="female_20s", help="AI 성우 프로필")
    parser.add_argument(
        "--step",
        type=str,
        choices=["all", "analyze", "generate", "compose"],
        default="all",
        help="실행할 파이프라인 단계 (기본값: all)"
    )
    parser.add_argument("--mock", action="store_true", help="실제 API 호출 대신 모의 에셋으로 파이프라인 전체를 빠르게 테스트")
    parser.add_argument("--force", action="store_true", help="기존 캐시 파일 및 기존 기획안을 무시하고 강제로 재생성")
    parser.add_argument("--job-id", type=str, default=None, help="이어서 작업할 job ID (미지정 시 outputs/jobs/ 아래 새 작업 생성, --step generate/compose는 필수)")
    parser.add_argument("--brand-name", type=str, default=None, help="[B2B] 상호명 워터마크 + 검색 유도 엔딩 카드 적용")
    parser.add_argument("--brand-logo", type=str, default=None, help="[B2B] 로고 이미지 경로 (선택)")
    parser.add_argument("--search-keyword", type=str, default=None, help="[B2B] 엔딩 카드 검색어 (미지정 시 상호명)")

    args = parser.parse_args()
    setup_logging()
    print_banner()

    # 상품명·특장점은 지어내지 않음: URL 크롤링 결과나 사용자가 넣은 사실만 사용
    if not args.product and not args.url:
        console.print("[bold red]오류:[/bold red] --product(상품/숙소명) 또는 --url을 지정해 주세요. (기본값을 지어내지 않습니다)")
        sys.exit(1)
    args.usp = args.usp or ""

    # 작업 공간: 새 작업이면 생성, 단계별 이어서 실행이면 기존 job 로드
    if args.job_id:
        job = JobContext.load(args.job_id)
    elif args.step in ("generate", "compose"):
        console.print("[bold red]오류:[/bold red] --step generate/compose는 --job-id로 이어서 작업할 작업을 지정해야 합니다.")
        sys.exit(1)
    else:
        job = JobContext.create(app="cli", mode=args.mode, source_url=args.url)
    console.print(f"[bold green]>> 작업 공간:[/bold green] {job.workdir} (job-id: {job.job_id})")

    image_pool = []
    photo_inventory = None

    # URL 크롤링 옵션이 제공된 경우 자동 추출
    if args.url:
        from core.scraper import scrape_product_info
        console.print(f"[bold cyan]>> 상세 페이지 크롤링 중 (모드={args.mode}):[/bold cyan] {args.url}")
        scraped = scrape_product_info(args.url, output_image_dir=job.assets_dir, mode=args.mode)
        if scraped["success"]:
            args.product = scraped.get("product_name") or args.product
            args.usp = scraped.get("usp") or args.usp
            image_pool = scraped.get("image_pool", [])
            photo_inventory = scraped.get("category_counts")
            if args.mode == "commerce":
                args.media = list(args.media) + scraped.get("product_images", [])
        elif scraped.get("product_name_hint") and not args.product:
            console.print(f"[yellow]{scraped.get('error')}[/yellow] (상품명 후보: {scraped['product_name_hint']})")
            sys.exit(1)
            console.print(f"[bold green]>> 크롤링 성공:[/bold green] 제품/숙소명='{args.product}', USP='{args.usp[:30]}...', 사진 후보={len(image_pool)}장")

    keys = check_env_keys(mock=args.mock)

    console.print(f"[bold green]>> 제작 모드:[/bold green] {'[bold magenta]여행 상품 모드 (누끼 100% 바이패스 & 2D 켄번스 풍경 모션)[/bold magenta]' if args.mode == 'travel' else '[bold cyan]일반 커머스 모드[/bold cyan]'}")
    console.print(f"[bold green]>> 실행 모드:[/bold green] {'[yellow]MOCK (테스트)[/yellow]' if args.mock else '[cyan]PRODUCTION (실제 API)[/cyan]'}")
    console.print(f"[bold green]>> 대상 제품/숙소:[/bold green] {args.product}")
    console.print(f"[bold green]>> 핵심 소구점:[/bold green] {args.usp}")
    console.print(f"[bold green]>> 톤앤매너:[/bold green] {args.tone}")
    console.print(f"[bold green]>> 영상 길이:[/bold green] {args.duration}")
    console.print(f"[bold green]>> 오디오 모드:[/bold green] {args.audio_mode} (성우: {args.actor})")
    console.print(f"[bold green]>> 실행 단계:[/bold green] {args.step.upper()}\n")

    scenes = []

    # =========================================================================
    # Step 1: 콘티 역기획 (Gemini 3.6 Flash Vision)
    # =========================================================================
    if args.step in ["all", "analyze"]:
        console.rule("[bold cyan]Step 1: 콘티 역기획 (Gemini Vision Analyzer)[/bold cyan]")
        try:
            if args.mode == "owner":
                # 트랙 B: 업로드 미디어 기반 콘티 (상호명 보존, 네이버 검색 CTA) + 미디어를 씬 키로 배정
                scenes = plan_owner_storyboard(
                    business_name=args.product,
                    usp=args.usp,
                    business_type=args.business_type,
                    search_keyword=args.search_keyword or args.product,
                    media_paths=args.media,
                    api_key=keys.get("GEMINI_API_KEY"),
                    mock=args.mock,
                    thumb_dir=job.assets_dir,
                )
                for sc, path in zip(scenes, args.media):
                    if is_video_file(path):
                        sc["media"] = {"type": "video", "path": os.path.abspath(path), "trim_start": DEFAULT_TRIM_START, "fit": "auto"}
                    else:
                        sc["image_path"] = os.path.abspath(path)
            elif args.mode == "commerce":
                # 트랙 A 쇼핑 제휴: 실물 상품 사진(판매 페이지/직접 지정) 기반 리뷰 썰, AI 이미지 없음
                if not args.media and not args.mock:
                    raise ValueError("상품 사진이 없습니다. --url(판매 페이지) 또는 --media(사진 경로)를 지정해 주세요.")
                scenes = plan_commerce_storyboard(
                    product_name=args.product, usp=args.usp, media_paths=args.media,
                    api_key=keys.get("GEMINI_API_KEY"), mock=args.mock, thumb_dir=job.assets_dir,
                )
                media_cycle = [args.media[i % len(args.media)] for i in range(len(scenes))] if args.media else []
                from core.render.commerce_layout import assign_shots
                for sc, shot in zip(scenes, assign_shots(media_cycle)):
                    sc["shot"] = shot          # 같은 사진 재사용 시 앵글 변경 (전체 → 중앙 줌인 → 상단·하단 디테일)
                for sc, path in zip(scenes, media_cycle):
                    if is_video_file(path):
                        sc["media"] = {"type": "video", "path": os.path.abspath(path), "trim_start": DEFAULT_TRIM_START, "fit": "auto"}
                    else:
                        sc["image_path"] = os.path.abspath(path)
            elif args.mode == "travel" and image_pool:
                # 트랙 A 숙소 제휴: 사진 풀 비전 분류 → 7씬 비율 슬롯에 사진 배정 → 실제 사진 설명 기반 대본
                from core.planners.travel import plan_travel_storyboard
                scenes, photo_inventory = plan_travel_storyboard(
                    product_name=args.product, usp=args.usp, image_pool=image_pool, assets_dir=job.assets_dir,
                    api_key=keys.get("GEMINI_API_KEY"), mock=args.mock, tone_and_manner=args.tone,
                    duration_preset=args.duration, audio_mode=args.audio_mode, output_plan_path=job.plan_path,
                )
            else:
                # 트랙 A: 제휴(숙소/쇼핑) 대본 선기획
                scenes = plan_storyboard(
                    reference_video_path=args.ref,
                    product_name=args.product,
                    usp=args.usp,
                    num_scenes=args.scenes,
                    tone_and_manner=args.tone,
                    duration_preset=args.duration,
                    audio_mode=args.audio_mode,
                    output_plan_path=job.plan_path,
                    mock=args.mock,
                    force=args.force,
                    api_key=keys.get("GEMINI_API_KEY"),
                    mode=args.mode,
                    photo_inventory=photo_inventory
                )
            # 숙소 제휴 모드는 plan_travel_storyboard에서 사진 배정까지 끝난 상태로 저장
            job.save_plan(scenes)
            display_storyboard_table(scenes)
            console.print(f"[bold green][OK] Step 1 완료![/bold green] 기획안 저장: [underline]{job.plan_path}[/underline]\n")
        except Exception as e:
            console.print(f"[bold red][ERROR] Step 1 실행 중 오류 발생:[/bold red] {e}")
            sys.exit(1)
    else:
        # Step 2나 3만 개별 실행 시 해당 job의 storyboard.json 로드
        scenes = job.load_plan()
        if scenes:
            console.print(f"[dim]'{job.plan_path}'에서 {len(scenes)}개 씬 기획을 로드했습니다.[/dim]\n")
        else:
            console.print(f"[bold red]오류:[/bold red] '{job.plan_path}' 파일이 없습니다. 먼저 Step 1을 실행하세요.")
            sys.exit(1)

    if args.step == "analyze":
        console.print("[green]Step 1(기획 분석)이 완료되었습니다. 플랜을 검토 후 --step generate 또는 all을 실행하세요.[/green]")
        return

    # =========================================================================
    # Step 2: 에셋 생성 (실물 사진·영상 + 로컬 Ken Burns 모션 비디오 + Edge-TTS)
    # =========================================================================
    manifest = []
    if args.step in ["all", "generate"]:
        console.rule("[bold cyan]Step 2: 에셋 배치 생성 (실물 사진·영상, 로컬 Ken Burns 모션, Edge-TTS)[/bold cyan]")
        try:
            generator = AssetGenerator(
                gemini_api_key=keys.get("GEMINI_API_KEY"),
                tts_lang="ko",
                voice_actor=args.actor,
                audio_mode=args.audio_mode,
                cache_dir=job.assets_dir,
                mock=args.mock,
                mode=args.mode
            )
            # 실물 사진 모드(숙소·쇼핑): 각 씬의 image_path(실물 사진)로 1:1 매핑. 사진 없는 씬은 AI 생성 대신 중단
            if args.mode in ("travel", "commerce") and not args.mock:
                missing = [
                    sc.get("scene_index") for sc in scenes
                    if not (sc.get("image_path") and os.path.exists(sc["image_path"]))
                    and not AssetGenerator.scene_video_media(sc)
                ]
                if missing:
                    raise ValueError(f"씬 {missing}에 매칭된 실물 사진이 없습니다. 웹 대시보드에서 교체/업로드해 주세요.")

            manifest = generator.generate_all(
                scenes=scenes,
                force=args.force
            )
            console.print(f"[bold green][OK] Step 2 완료![/bold green] 총 {len(manifest)}개 씬 에셋이 {job.assets_dir} 에 준비되었습니다.\n")
        except Exception as e:
            console.print(f"[bold red][ERROR] Step 2 실행 중 오류 발생:[/bold red] {e}")
            sys.exit(1)
    else:
        # Step 3만 실행하는 경우 해당 job의 assets/ 에서 매니페스트 재구성
        for idx, sc in enumerate(scenes, 1):
            s_num = int(sc.get("scene_index") or sc.get("scene_number", idx))
            manifest.append({
                "scene_number": s_num,
                "scene_index": s_num,
                "image_path": job.scene_asset(s_num, ".png"),
                "audio_path": job.scene_asset(s_num, ".mp3"),
                "video_path": job.scene_asset(s_num, ".mp4"),
                "caption": sc.get("caption") or sc.get("voiceover", ""),
                "hook_title": sc.get("hook_title", "")
            })

    if args.step == "generate":
        console.print("[green]Step 2(에셋 생성)가 완료되었습니다. 생성된 에셋을 확인 후 --step compose 또는 all을 실행하세요.[/green]")
        return

    # =========================================================================
    # Step 3: 타임라인 조립 및 오디오 싱크 렌더링 (MoviePy)
    # =========================================================================
    if args.step in ["all", "compose"]:
        console.rule("[bold cyan]Step 3: 비디오-오디오 싱크 매칭 & 타임라인 조립 (Compositor)[/bold cyan]")
        try:
            brand = None
            brand_name = args.brand_name or (args.product if args.mode == "owner" else None)
            if brand_name:
                brand = BrandOverlay(
                    business_name=brand_name,
                    logo_path=args.brand_logo,
                    end_card=EndCard(search_keyword=args.search_keyword or brand_name),
                )
            compositor = AdCompositor(output_dir=job.final_dir)
            final_video_path = compositor.assemble_ad(
                asset_manifest=manifest,
                output_filename="final.mp4",
                fps=24,
                audio_mode=args.audio_mode,
                mode=args.mode,
                brand=brand,
                disclosure=COUPANG_DISCLOSURE if args.mode == "commerce" else None
            )
            console.print(Panel(
                f"[bold green]최종 광고 영상 제작 완료![/bold green]\n\n"
                f"파일 경로: [bold underline cyan]{final_video_path}[/bold underline cyan]\n"
                f"규격: 9:16 Vertical Video (H.264/AAC, 24fps)\n"
                f"씬 수: 총 {len(manifest)}개 씬 결합",
                title="[green]성공[/green]",
                border_style="bright_green"
            ))

            # 쇼츠 최적화 SEO 메타데이터 자동 생성 및 저장
            try:
                from core.seo_generator import generate_seo_metadata
                seo_meta = generate_seo_metadata(
                    scenes=scenes,
                    mode=args.mode,
                    product_name=args.product,
                    usp=args.usp,
                    search_keyword=args.search_keyword or args.product,
                    api_key=keys.get("GEMINI_API_KEY"),
                    mock=args.mock
                )
                job.save_metadata(seo_meta)
                console.print(f"[dim]쇼츠 SEO 메타데이터 저장 완료: {job.metadata_path}[/dim]")
            except Exception as me:
                logger.warning(f"SEO 메타데이터 저장 실패: {me}")
        except Exception as e:
            console.print(f"[bold red][ERROR] Step 3 실행 중 오류 발생:[/bold red] {e}")
            sys.exit(1)


if __name__ == "__main__":
    main()
