# HWP/HWPX → TXT/MD 웹 변환기

## 1) 로컬 실행(개발용)
```bash
python3 app.py
```
브라우저에서 `http://localhost:8080` 접속

## 2) 일반 브라우저 링크 공유(실사용)
이 저장소를 Render에 배포하면, 상대방은 **추가 프로그램 없이 URL만 열어서 바로 사용**할 수 있습니다.

### Render 원클릭 배포
1. 이 폴더를 GitHub 저장소로 push
2. Render 대시보드에서 `New +` → `Blueprint` 선택
3. 해당 GitHub 저장소 연결
4. `render.yaml` 감지 후 자동 배포
5. 배포 완료 후 생성된 URL 공유

### 포함된 배포 파일
- `render.yaml`: Render Blueprint 설정
- `Dockerfile`: 서버 실행 이미지
- `.dockerignore`: 불필요 파일 제외

## 주의
- `HWPX`: 비교적 안정적으로 텍스트 추출
- `HWP`: 바이너리 특성상 휴리스틱 추출(품질 저하 가능)
- 영구 저장 없음(요청 처리 후 메모리 상에서 ZIP 생성)
