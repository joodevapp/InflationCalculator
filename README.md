# worth-data

Worth(물가 계산기) 앱이 내려받는 공식 소비자물가지수 데이터 저장소입니다.
GitHub Actions가 매일 한 번 4개 기관에서 전체 기간을 다시 받아 `data/cpi.json`을 만들고,
바뀐 내용이 있을 때만 커밋합니다.

| 국가 | 기관 | 시리즈 |
|---|---|---|
| US | BLS | CPI-U · All items (CUUR0000SA0) |
| GB | ONS | CPI index (D7BT) |
| EU·EEA 30개국 | Eurostat | HICP 총지수 (`prc_hicp_minr`, 최신 기준연도 지수) |
| KR | KOSIS (키가 없으면 OECD 사본) | 소비자물가지수 총지수 |

## 처음 설정

1. GitHub에 **공개(Public)** 저장소를 만듭니다 (예: `worth-data`). 앱이 로그인 없이
   `raw.githubusercontent.com`에서 받아야 하므로 공개여야 합니다.
2. 이 폴더를 올립니다.
   ```bash
   git init -b main
   git add .
   git commit -m "Initial CPI data (v1)"
   git remote add origin https://github.com/joodevapp/InflationCalculator.git
   git push -u origin main
   ```
3. 저장소 **Settings → Secrets and variables → Actions → Secrets**에 추가합니다.
   - `BLS_API_KEY`: https://data.bls.gov/registrationEngine/ 에서 무료 발급
   - `KOSIS_API_KEY`: https://kosis.kr/openapi/ 에서 무료 발급
   키가 없어도 동작합니다. BLS는 느린 무키 API(하루 25회 제한)를, 한국은 OECD 사본을 씁니다.
4. (필요할 때만) **Variables**에 KOSIS 표를 지정합니다. 기본값은
   `KOSIS_TBL_ID=DT_1J22003`, `KOSIS_OBJ_L1=T10`, `KOSIS_ITM_ID=T`입니다.
   KOSIS OpenAPI의 "URL 생성"에서 소비자물가지수(2020=100) 총지수 월별 URL을 만들어
   값이 다르면 그 값으로 넣으세요. 실행 로그에 `KOSIS ...` 경고가 보이면 이 값을 확인하세요.
5. **Actions** 탭 → "Update CPI data" → **Run workflow**로 한 번 실행해 봅니다.
6. 앱의 `custom_code/worth_config.dart`는 이미 이 저장소(`joodevapp/InflationCalculator`)를 가리킵니다.
   저장소 이름을 바꾸면 그 두 주소도 함께 바꾸고 `./fft push` 하세요.

## 동작 규칙

- 매일 06:17 UTC + 수동 실행. 변경이 있으면 `version`을 1 올리고 `generatedAt`과
  `manifest.json`의 sha256을 갱신해 `data: update CPI (vNN)`로 커밋합니다.
- 국가별로 데이터가 예전보다 짧아지거나, 전월 대비 ±10%를 넘는 변화가 새로 생기면
  그 국가는 기존 값을 유지하고 경고를 남깁니다.
- 한 기관이 실패해도 나머지는 진행합니다. 전부 실패하면 실패(종료 코드 1)로 끝납니다.
- `data/*`는 `.gitattributes`로 줄바꿈 변환을 막습니다. 앱이 sha256으로 파일을 검사하기 때문입니다.
- 공개 저장소에서 60일 동안 활동이 없으면 GitHub가 예약 실행을 멈출 수 있습니다.
  매달 새 데이터 커밋이 생기므로 보통은 문제없지만, 멈추면 Actions 탭에서 다시 켜세요.

## 로컬 실행

```bash
pip install -r requirements.txt
python scripts/build_cpi.py          # BLS_API_KEY, KOSIS_API_KEY 환경변수는 선택
```

## 출시 전: 앱 번들 데이터 갱신

```bash
python scripts/export_app_bundle.py <FlutterFlow 작업 폴더>
cd <FlutterFlow 작업 폴더> && ./fft push -m "Update bundled CPI data"
```
`data/cpi.json`을 앱의 `custom_code/cpi_bundle.dart`에 넣습니다. 첫 실행과 오프라인에서 쓰이는 데이터입니다.
