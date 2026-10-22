"""
유튜브 토큰 → Streamlit Secrets 한 줄 만들기 (OAuth 동의 화면이 '테스트' 상태라 7일마다 토큰이 만료되므로 매주 쓰는 도우미).

  1) python scripts/youtube_auth.py            # 브라우저에서 채널 선택 로그인 → secrets/token_*.json 갱신
  2) python scripts/make_secret_lines.py       # secrets/_store_secret_line.txt · _travel_secret_line.txt 다시 만듦 (토큰은 화면에 출력하지 않음)
  3) 파일 내용을 Streamlit Cloud → Settings → Secrets 의 같은 이름 줄에 덮어쓰고 Save
  4) 끝나면 두 파일을 삭제 (토큰이 평문으로 들어 있음)
"""
import json
import os
import tomllib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGETS = (("token_store.json", "YOUTUBE_TOKEN_STORE", "_store_secret_line.txt"),
           ("token_travel.json", "YOUTUBE_TOKEN_TRAVEL", "_travel_secret_line.txt"))


def main() -> None:
    for token_file, key, out_name in TARGETS:
        src = os.path.join(ROOT, "secrets", token_file)
        if not os.path.exists(src):
            print(f"건너뜀: {token_file} 없음")
            continue
        data = json.load(open(src, encoding="utf-8"))
        line = f"{key} = " + json.dumps(json.dumps(data, ensure_ascii=False, separators=(",", ":")), ensure_ascii=False) + "\n"
        assert json.loads(tomllib.loads(line)[key]) == data          # TOML로 읽혀서 같은 토큰이 나오는지
        out = os.path.join(ROOT, "secrets", out_name)
        open(out, "w", encoding="utf-8").write(line)
        print(f"만들었어요: secrets/{out_name}  (갱신 토큰 {'있음' if data.get('refresh_token') else '없음!'}, 만료 {data.get('expiry', '?')})")


if __name__ == "__main__":
    main()
