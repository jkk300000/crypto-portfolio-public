# Ubuntu 24.04 LTS x64 설치 및 운영

SuperTrend + Bollinger Bands Gate.io ETH 30m 봇을 우분투에서 설치·실행·서비스로 등록하는 방법입니다.

## 1. 프로젝트 준비

서버에 프로젝트가 있어야 합니다.

- **Git 사용 시**
  ```bash
  git clone <저장소_URL> gateio_supertrend_eth
  cd gateio_supertrend_eth
  ```
- **직접 복사**  
  Windows에서 만든 폴더를 `scp` 등으로 `/home/사용자명/gateio_supertrend_eth` 등 원하는 경로에 복사합니다.

## 2. 설치 (한 번만)

프로젝트 루트에서 실행:

```bash
cd /path/to/gateio_supertrend_eth
chmod +x scripts/*.sh
bash scripts/install_ubuntu.sh
```

- Python3, venv, pip가 없으면 자동 설치를 시도합니다.
- `.venv` 가상환경을 만들고 `requirements.txt` 로 의존성을 설치합니다.
- `.env` 가 없으면 `.env.example` 을 복사합니다.

## 3. 환경 변수 설정

`.env` 파일을 열어 API 키 등을 넣습니다.

```bash
nano .env
```

필수 예시:

```env
GATE_API_KEY=발급받은_키
GATE_API_SECRET=발급받은_시크릿
```

테스트넷 사용 시:

```env
GATE_USE_TESTNET=1
```

**Discord 알림** (진입/청산/반대 신호 시 웹훅 전송):

```env
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/123456/abcdef...
```

- 디스코드 채널 → 연동 → 웹훅 → 새 웹훅 만들기 후 URL 복사하여 위처럼 넣습니다.
- 설정하지 않으면 알림을 보내지 않습니다.

저장 후 종료 (Ctrl+O, Enter, Ctrl+X).

## 4. 실행

### 포그라운드 (터미널에서 직접 실행)

```bash
bash scripts/run.sh
```

또는:

```bash
.venv/bin/python main.py
```

### 백그라운드 (nohup)

```bash
nohup .venv/bin/python main.py >> logs/bot.log 2>&1 &
# 로그 디렉터리 없으면: mkdir -p logs && nohup ...
```

### systemd 서비스 (재부팅 후 자동 시작, 재시작 포함)

```bash
sudo bash scripts/setup_systemd.sh
```

또는 실행할 사용자명을 지정:

```bash
sudo bash scripts/setup_systemd.sh myuser
```

서비스 명령:

```bash
sudo systemctl start gateio-supertrend   # 시작
sudo systemctl stop gateio-supertrend    # 중지
sudo systemctl enable gateio-supertrend  # 부팅 시 자동 시작
sudo systemctl status gateio-supertrend  # 상태 확인
journalctl -u gateio-supertrend -f       # 로그 실시간 보기
```

## 5. 참고

- **시스템 시계**: Gate.io API는 요청 타임스탬프가 서버 시간과 60초 이내여야 합니다. NTP 동기화 권장: `sudo timedatectl set-ntp true`
- **방화벽**: 아웃바운드 HTTPS(443), WSS만 열리면 됩니다. 별도 인바운드 포트는 필요 없습니다.
- **로그**: systemd 사용 시 `journalctl -u gateio-supertrend -f` 로 확인합니다.
