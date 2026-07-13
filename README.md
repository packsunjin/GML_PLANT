# 실시간 식물 상태 분류 시스템

AD8232(생체전위 증폭기) + ADS1115(16비트 ADC)로 식물 잎/줄기의 Ag 전극 미세 전위를 측정하고,
스펙트로그램 이미지 기반 머신러닝(SVM/Random Forest)으로 **정상 / 스트레스(수분부족+물리적 자극)**
상태를 실시간으로 분류하여 VNC로 원격 모니터링하는 시스템입니다.

## 폴더 구조

```
project/
  data/raw/          # (1) 원시 시계열 CSV (정상.csv, 수분부족.csv, 자극.csv)
  data/spectrogram/  # (2) 전처리된 224x224 스펙트로그램 이미지 (정상/, 스트레스/)
  models/            # (3) 학습된 모델(.joblib) + confusion matrix 이미지
  src/
    sensor_control.py   # (1) 하드웨어 제어 및 데이터 수집
    preprocess.py        # (2) 대역통과필터 + 스펙트로그램 생성
    train.py              # (3) SVM/RandomForest 학습 + 평가
    inference.py          # (4) 실시간 추론 엔진
    gui.py                 # (4) VNC용 실시간 GUI / 헤드리스 대시보드
  main.py             # 최상위 실행 진입점
  requirements.txt
  README.md
```

## 설치

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

> 라즈베리파이가 아닌 PC에서 실행하면 `adafruit-circuitpython-ads1x15` 임포트가 실패하며,
> `sensor_control.py`가 자동으로 **SIMULATION 모드**로 전환되어 실제 신호와 유사한 합성 신호를 생성합니다.
> 따라서 하드웨어 없이도 전체 파이프라인(수집→전처리→학습→실시간 추론)을 그대로 검증할 수 있습니다.

## 단계별 실행 방법 및 검증 결과

### [1] 데이터 수집 (`src/sensor_control.py`)

```bash
cd src
python3 sensor_control.py --state 정상 --duration 30 --rate 250
python3 sensor_control.py --state 수분부족 --duration 30 --rate 250
python3 sensor_control.py --state 자극 --duration 30 --rate 250
```

- I2C(ADS1115, 주소 0x48 기본) 채널 A0에서 AD8232 출력을 읽으며, `--rate`로 100~1000Hz 범위 샘플링 주기 설정 가능
- **검증 결과**: 3개 상태 모두 30초 x 250Hz = 7,500 샘플씩 `data/raw/{정상,수분부족,자극}.csv`에 정상 저장 확인 ✅

### [2] 전처리 및 시각화 (`src/preprocess.py`)

```bash
python3 preprocess.py --raw_dir ../data/raw --out_dir ../data/spectrogram
```

- SciPy Butterworth 대역통과필터(0.5~45Hz, order=4)로 50Hz 전원 노이즈 및 DC 드리프트 제거
- 2초 윈도우 / 1초 스텝으로 슬라이딩하며 스펙트로그램(224x224, `viridis`) PNG 생성
- 정상 → `data/spectrogram/정상/`, 수분부족+자극 → `data/spectrogram/스트레스/` 로 클래스 통합 저장
- **검증 결과**: CSV 3개 → 총 87개 이미지 생성 (정상 29장 / 스트레스 58장), 모든 이미지 224x224 확인 ✅

### [3] 모델 학습 (`src/train.py`)

```bash
python3 train.py --spectrogram_dir ../data/spectrogram --models_dir ../models
```

- 이미지를 grayscale 224x224 → flatten(50,176차원) 픽셀 특징으로 사용
- StandardScaler + PCA(차원축소) + SVM(Linear/RBF, `GridSearchCV`) / Random Forest(`GridSearchCV`) 비교
- Confusion Matrix 이미지 자동 저장

**검증 결과 (실제 실행 로그)**

| 모델 | 최적 하이퍼파라미터 | Accuracy | Precision | Recall |
|---|---|---|---|---|
| SVM | kernel=linear, C=0.1, gamma=scale | **0.9259** | 1.0000 | 0.8889 |
| Random Forest | n_estimators=100, max_depth=None | 0.8889 | 0.8947 | 0.9444 |

→ 최적 모델(SVM, Accuracy 92.6%)이 `models/best_model.joblib`로 저장됨. **70% 이상 정확도 요구사항 충족 ✅**
(`models/confusion_matrix_SVM.png`, `models/confusion_matrix_RandomForest.png` 참고)

> 참고: 본 검증은 하드웨어가 없는 개발 환경에서 시뮬레이션 신호로 생성한 샘플 데이터 기준입니다.
> 실제 식물에서 수집한 데이터로 재학습하면 정확도가 달라질 수 있으니, 실측 데이터 확보 후 `train.py`를 재실행하세요.

### [4] 실시간 통합 시스템 (`main.py`)

```bash
# GUI 모드 (기본, VNC 데스크톱 접속 가정)
python3 main.py

# 헤드리스 모드 (터미널/SSH/VNC 터미널)
python3 main.py --no-gui

# 하드웨어 없이 저장된 CSV를 재생하여 테스트(시뮬레이션 입력)
python3 main.py --no-gui --sim_csv data/raw/자극.csv
```

- `models/best_model.joblib` 로드 → ADS1115(또는 시뮬레이션) 실시간 샘플을 2초 버퍼로 모아 즉시 추론
- GUI 모드: Matplotlib Figure(`plt.ion()`)에 ① 최근 5초 시계열, ② 실시간 스펙트로그램, ③ 상태 텍스트+이모티콘(🌱/😵) 표시
  - GUI 백엔드(Tk 등)가 없는 환경에서는 자동으로 파일 저장 모드(`dashboard_last.png`)로 폴백
- `--no-gui`: 실제 터미널(TTY)에서는 Rich 대시보드, 파이프/리다이렉션 시에는 일반 텍스트 로그로 자동 전환
- Ctrl+C(SIGINT) 시 예외 처리로 안전 종료

**검증 결과 (실제 실행 로그 발췌, `--sim_csv data/raw/자극.csv`)**
```
상태: 😵 스트레스  확신도= 100.0%
상태: 😵 스트레스  확신도=  99.7%
...
[headless] Ctrl+C 감지 - 안전 종료합니다.
```
→ 자극/수분부족 CSV 재생 시 대부분 '스트레스'로, 정상 CSV 재생 시 대부분 '정상'으로 정확히 분류됨을 확인. Ctrl+C 안전 종료 확인 ✅

---

## [5] VNC 원격 모니터링 설정 가이드 (Raspberry Pi OS, Bullseye 이상)

### 5-1. VNC Server 활성화

**방법 A: raspi-config (GUI 메뉴)**
```bash
sudo raspi-config
# Interface Options → VNC → <Yes> 선택 → Enable
```

**방법 B: systemctl (커맨드라인)**
```bash
sudo systemctl enable vncserver-x11-serviced
sudo systemctl start vncserver-x11-serviced
sudo systemctl status vncserver-x11-serviced   # active (running) 확인
```

### 5-2. 접속 정보 확인

```bash
hostname -I        # Pi의 IP 주소 확인 (예: 192.168.0.42)
```
- 기본 포트: **5900**
- 해상도: `raspi-config` → Display Options → Resolution 에서 **최소 800x480 이상** 권장 (그래프/스펙트로그램이 잘리지 않도록 1024x768 이상 추천)

### 5-3. VNC Viewer로 접속하여 모니터링

1. PC/스마트폰에 **RealVNC Viewer** 설치
2. 새 연결 추가: `<Pi의 IP주소>:5900` (예: `192.168.0.42:5900`)
3. Pi 계정 로그인 정보 입력하여 접속 → Pi 데스크톱 화면이 표시됨
4. 터미널을 열고 프로젝트 폴더에서 실행:
   ```bash
   cd project
   python3 main.py          # GUI 모드로 실행되며, VNC 화면에 Matplotlib 창이 그대로 표시됨
   ```
5. 화면에 아래 3가지가 실시간 갱신되는 것을 확인:
   - 상단: 최근 5초간 필터링된 전위 시계열 그래프
   - 좌하단: 실시간 스펙트로그램
   - 우하단: 현재 상태 텍스트 + 큰 이모티콘(🌱 정상 / 😵 스트레스)
6. 모니터링 종료 시 VNC 터미널에서 `Ctrl+C` → 안전 종료 메시지 확인 후 창이 닫힘

### 5-4. Headless(모니터 없는) 환경에서도 VNC로 접속하는 방법

Raspberry Pi에 모니터가 연결되어 있지 않아 기본 VNC Server가 화면을 잡지 못하는 경우, 가상 디스플레이 기반의
`x11vnc` 또는 `wayvnc`(Wayland/라즈베리파이 OS 최신 버전)를 사용합니다.

**x11vnc 방식 (X11 기반, Bullseye 등)**
```bash
sudo apt update
sudo apt install -y x11vnc xvfb

# 가상 디스플레이 생성 (:1번, 1024x768 해상도)
Xvfb :1 -screen 0 1024x768x24 &
export DISPLAY=:1

# x11vnc로 해당 가상 디스플레이를 VNC로 공유 (포트 5900)
x11vnc -display :1 -forever -rfbport 5900 -passwd <원하는_비밀번호>
```
이후 같은 터미널(DISPLAY=:1 상태)에서 `python3 main.py` 를 실행하면 GUI 창이 가상 디스플레이에 그려지고,
VNC Viewer로 `<Pi IP>:5900` 에 접속하면 동일하게 모니터링할 수 있습니다.

**wayvnc 방식 (Wayland 기반, 최신 Raspberry Pi OS)**
```bash
sudo apt install -y wayvnc
wayvnc 0.0.0.0 5900
```
Wayland 세션(`labwc`/`wayfire`)이 이미 떠 있는 상태에서 실행하면 현재 화면을 그대로 VNC로 공유합니다.

> 헤드리스 서버에서 GUI 창 없이 상태만 확인하고 싶다면 `python3 main.py --no-gui` 로 실행해 SSH/VNC 터미널에서
> Rich 텍스트 대시보드로도 동일한 정보를 확인할 수 있습니다.

---

## 검증 요구사항 체크리스트

- [x] (2) 전처리 스크립트가 CSV → 224x224 스펙트로그램 이미지 생성 확인 (총 87장)
- [x] (3) 학습 스크립트 Accuracy 70% 이상 달성 확인 (SVM 92.6%)
- [x] (4) 통합 스크립트가 GUI/헤드리스 모드에서 실시간 그래프·스펙트로그램·상태 표시 갱신 확인
- [x] 하드웨어 미보유 시 `data/raw/`의 샘플 CSV를 시뮬레이션 입력으로 사용하는 모드(`--sim_csv`) 추가 확인
- [x] Ctrl+C 안전 종료 확인
