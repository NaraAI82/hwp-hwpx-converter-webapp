# HWP/HWPX URL 공유형 변환 서비스

브라우저 링크만 열면, 설치 없이 `HWP/HWPX -> TXT/MD` 변환 후 ZIP 다운로드가 가능한 Flask 웹앱입니다.

## 실행 방법

### 로컬
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```
접속: `http://localhost:8080`

### Docker
```bash
docker build -t hwp-converter .
docker run -p 8080:8080 -e PORT=8080 hwp-converter
```

## GitHub 업로드 방법
```bash
git init
git add .
git commit -m "Add deployable Flask web converter"
git branch -M main
git remote add origin <YOUR_GITHUB_REPO_URL>
git push -u origin main
```

## Render 배포 방법 (Blueprint)
1. Render 로그인
2. `New +` -> `Blueprint`
3. GitHub 저장소 연결
4. `render.yaml` 자동 인식 후 배포
5. 배포 완료 URL 확인

## URL 공유 방법
- Render가 발급한 주소(예: `https://hwp-hwpx-converter.onrender.com`)를 전달하면 끝입니다.
- 상대방은 회원가입/설치 없이 브라우저에서 바로 업로드-변환-다운로드 가능합니다.

## 보안/처리 정책
- 업로드 파일 영구 저장 안 함
- 처리 후 서버 메모리 객체 즉시 해제
- 응답 ZIP은 일회성 생성
- UTF-8 처리 및 한글 파일명 대응


## HWP 지원 제한
- HWPX: 기본 지원(안정적)
- HWP: 제한 지원
  - 1차: `hwp5txt`(pyhwp 계열 명령) 사용 가능 시 우선 시도
  - 2차: 내장 olefile 파서로 재시도
  - 품질 검증 실패 시 본문 대신 `HWP 지원 제한 안내`만 반환
- Render Docker 기본 이미지에는 `hwp5txt`가 항상 포함되지 않을 수 있습니다.
  이 경우 HWP는 내장 파서 경로만 사용되며, 실패 시 안내문으로 처리됩니다.
