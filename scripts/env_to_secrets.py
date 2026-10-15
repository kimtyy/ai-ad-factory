"""
.env → Streamlit Secrets(TOML) 변환 (직접 실행용).

    python scripts/env_to_secrets.py            # 화면에 출력 (복사해서 Streamlit Cloud > Secrets에 붙여넣기)
    python scripts/env_to_secrets.py --file     # .streamlit/secrets.toml 로 저장 (.gitignore 대상, 커밋되지 않음)

모든 값을 큰따옴표로 감싸고 \\ 와 " 는 이스케이프한다. 주석·빈 줄은 건너뛴다.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def convert(env_path: str) -> str:
    out = []
    with open(env_path, encoding="utf-8-sig") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if s.startswith("export "):
                s = s[7:]
            if "=" not in s:
                continue
            key, val = s.split("=", 1)
            key, val = key.strip(), val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                val = val[1:-1]
            elif " #" in val:
                val = val.split(" #", 1)[0].strip()
            val = val.replace("\\", "\\\\").replace('"', '\\"')
            out.append(f'{key} = "{val}"')
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    text = convert(os.path.join(ROOT, ".env"))
    if "--file" in sys.argv:
        dst = os.path.join(ROOT, ".streamlit", "secrets.toml")
        with open(dst, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"저장: {dst} ({len(text.splitlines())}개 항목, git에는 올라가지 않음)")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(text)
