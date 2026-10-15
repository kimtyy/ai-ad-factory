# Railway 배포 가이드 (24시간 상시 가동)

노트북을 꺼도 스마트폰에서 고정 주소(`https://<이름>.up.railway.app`)로 대시보드에 접속하고,
구글 캘린더 자동 제작(10분마다)·떡상 레이더(매일 07:00)가 서버에서 계속 돌아가게 합니다.

## 구조
- **서비스 1개 (컨테이너 1개)**: 대시보드(Streamlit) + 스케줄러(`scripts/railway_scheduler.py`)를 함께 실행
  (Railway 볼륨은 서비스 하나에만 붙어서, 한 서비스에 몰아야 데이터를 같이 씀)
- **볼륨 `/data`**: 작업물·레이더 DB·OAuth 토큰·BGM·글꼴·캘린더 사진 (재배포해도 유지)
- **빌드**: `Dockerfile` (ffmpeg·한글/컬러 이모지 글꼴·서울 시간대), 설정 `railway.json` (헬스체크 `/_stcore/health`)
- **렌더링**: 서버엔 인텔 QSV가 없어 `libx264` (PC보다 느림 - 30~50초 영상 몇 분)
- **외부 접속 잠금**: 모든 접속에 PIN (`DASHBOARD_PIN`)

## 1. GitHub 저장소 (처음 한 번)
1. https://github.com/new → 이름 예: `ai-ad-factory` → **Private** → README 없이 만들기
2. PC에서 (또는 Claude에게 주소를 알려 주면 대신 실행):
   ```
   git remote add origin https://github.com/<계정>/ai-ad-factory.git
   git push -u origin master
   ```
   `.env`·`secrets/`·`outputs/`·BGM은 `.gitignore`로 올라가지 않음

## 2. Railway 프로젝트
1. https://railway.com → **New Project → Deploy from GitHub repo** → 저장소 선택
   (`Dockerfile`·`railway.json`을 자동 인식)
2. 서비스 **Settings → Region**: **Southeast Asia (Singapore)** 권장 (한국에서 반응 빠름)
3. 서비스 **Settings → Resources**: 메모리 2GB 이상 권장 (영상 조립)
4. **볼륨**: 서비스 화면에서 `Ctrl/⌘+K` → *Volume* (또는 오른쪽 클릭 → Attach Volume)
   → Mount path **`/data`** (크기 10GB 정도)

## 3. 환경 변수 (Variables → Raw Editor)
PC의 `.env` 내용을 **그대로 붙여 넣고** 아래만 확인:

| 변수 | 값 |
|---|---|
| `DASHBOARD_PIN` | **필수** (없으면 외부 접속이 전부 막힘) |
| `DASHBOARD_PIN_ALWAYS` | `1` (서버는 모든 접속이 외부) |
| `NGROK_AUTHTOKEN`, `NGROK_DOMAIN` | 넣지 않아도 됨 (Railway가 주소를 줌) |
| `SCHED_CALENDAR` / `SCHED_RADAR` | 기본 켜짐. 끄려면 `0` |
| `RADAR_AT` | 레이더 시각, 기본 `07:00` (서울) |

`PORT`·`DATA_DIR`·`TZ`·`RENDER_ENCODER`는 자동/Dockerfile에서 설정됨

## 4. 고정 주소
서비스 **Settings → Networking → Generate Domain** → `https://<이름>.up.railway.app`
(내 도메인을 쓰려면 같은 곳의 *Custom Domain*에 추가하고 DNS CNAME 연결)

## 5. 첫 배포 확인 + PC 데이터 옮기기
1. **Deployments** 로그에 `You can now view your Streamlit app`과 `스케줄러 시작`이 보이면 성공
2. PC에서 `python scripts/railway_seed.py` → `outputs/railway_seed.zip` (토큰·BGM(m4a)·레이더 DB·대기열)
3. 스마트폰/PC 브라우저로 주소 접속 → PIN → 사이드바 **🛠 서버 데이터 가져오기** → zip 올리고 **서버에 풀기**
4. 같은 패널 아래에 스케줄러 로그가 보이면 캘린더·레이더도 서버에서 도는 중

## 6. PC 쪽 정리 (중복 실행 방지)
서버가 확인되면 PC의 작업 스케줄러를 끄세요 (둘 다 돌면 같은 캘린더 일정을 두 번 만들 수 있음):
```
Disable-ScheduledTask -TaskName AdFactoryCalendar
Disable-ScheduledTask -TaskName AdFactoryRadar
Disable-ScheduledTask -TaskName AdFactoryWatchdog
```

## 알아 둘 것
- **OAuth 토큰**: 유튜브·캘린더 연결(`youtube_auth.py`, `calendar_auth.py`)은 브라우저가 필요해 **PC에서만** 가능.
  새로 연결했으면 `railway_seed.py`로 다시 묶어 올리면 됨. Google Cloud OAuth 동의 화면이 **테스트** 상태면
  토큰이 7일마다 만료될 수 있으니 **프로덕션(게시)** 상태로 바꾸는 걸 권장
- **비용**: Railway는 사용량(CPU·메모리·볼륨) 과금. 렌더가 몰리면 늘어남 → Railway Usage 화면에서 확인
- **저장 공간**: 작업 폴더(`outputs/jobs`)가 계속 쌓이니 가끔 정리
- **업데이트**: 코드를 GitHub에 push하면 Railway가 자동 재배포 (볼륨 데이터는 유지)
