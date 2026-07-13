# 실시간 식물 상태 분류 시스템

AD8232(생체전위 증폭기) + ADS1115(16비트 ADC)로 식물 잎/줄기의 Ag 전극 미세 전위를 측정하고,
스펙트로그램 이미지 기반 머신러닝(SVM/Random Forest)으로 **정상 / 스트레스(수분부족+물리적 자극)**
상태를 실시간으로 분류하여 VNC로 원격 모니터링하는 시스템입니다.

## 폴더 구조

```
project/
  data/raw/          # (1) 원시 시계열 CSV (정상.csv, 수분부족.csv, 자극.csv 중 수집된 것만)
  data/spectrogram/  # (2) 전처리된 224x224 스펙트로그램 이미지 (정상/, 스트레스/)
  data/features.csv  # (2) 윈도우별 명시적 통계/주파수 특징 14개 (features 모드 학습용)
  models/            # (3) 학습된 모델(.joblib) + confusion matrix 이미지
  src/
    sensor_control.py     # (1) 하드웨어 제어 및 데이터 수집
    preprocess.py          # (2) 대역통과필터 + 스펙트로그램 생성 + 명시적 특징 추출
    feature_extraction.py  # (2) 명시적 통계/주파수 특징 14개 정의 (preprocess/inference 공유)
    spectro_render.py      # (2) 컬러맵 룩업 테이블 기반 스펙트로그램 렌더링 (preprocess/inference 공유)
    train.py                # (3) SVM/RandomForest 학습 + 평가 (pixel/features 두 방식 비교 가능)
    inference.py            # (4) 실시간 추론 엔진
    gui.py                   # (4) VNC용 실시간 GUI / 헤드리스 대시보드
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

> **3가지 상태를 모두 수집하지 못해도 괜찮습니다.** `preprocess.py`/`train.py`는 `data/raw/`에 있는
> 파일만으로 진행하며, 어떤 상태가 빠졌는지 안내 메시지를 출력합니다. 단, `정상.csv`는 반드시 있어야
> 하고(정상 클래스가 통째로 없으면 학습이 불가능하므로 명확한 에러로 즉시 종료됩니다), `수분부족.csv`/
> `자극.csv`는 둘 중 하나만 있어도 `스트레스` 클래스로 정상 학습됩니다.

### [2] 전처리 및 시각화 (`src/preprocess.py`)

```bash
python3 preprocess.py --raw_dir ../data/raw --out_dir ../data/spectrogram
```

- SciPy Butterworth 대역통과필터(0.5~45Hz, order=4)로 50Hz 전원 노이즈 및 DC 드리프트 제거
- 2초 윈도우 / 1초 스텝으로 슬라이딩하며 스펙트로그램(224x224, `viridis`) PNG 생성
  - 동일한 윈도우에서 `feature_extraction.py`의 명시적 통계/주파수 특징 14개도 함께 계산해 `data/features.csv`에 저장
- 정상 → `data/spectrogram/정상/`, 수분부족+자극 → `data/spectrogram/스트레스/` 로 클래스 통합 저장
- **검증 결과**: CSV 3개 → 총 87개 이미지 + `data/features.csv` 87행 생성 (정상 29장 / 스트레스 58장), 모든 이미지 224x224 확인 ✅

### [3] 모델 학습 (`src/train.py`)

두 가지 특징 방식을 지원하며, `--mode`로 선택합니다.

```bash
python3 train.py                     # pixel 모드만 (기존 방식, 기본값)
python3 train.py --mode features     # 명시적 특징(14개)만
python3 train.py --mode both         # 둘 다 학습하고 Accuracy/Precision/Recall 비교 표 출력
```

- **pixel 모드(기본)**: 이미지를 grayscale 224x224 → flatten(50,176차원) 픽셀 특징으로 사용, StandardScaler + PCA(차원축소) 적용
- **features 모드(신규)**: `data/features.csv`의 통계(평균/표준편차/RMS/왜도/첨도/영교차율/피크투피크) + 주파수(대역별 파워/스펙트럴 센트로이드/대역폭/피크 주파수) 특징 14개를 그대로 사용, PCA 없이 StandardScaler만 적용 (이미 저차원이라 추가 축소가 오히려 해석성을 해침)
- 두 모드 모두 SVM(Linear/RBF, `GridSearchCV`) / Random Forest(`GridSearchCV`) 비교, Confusion Matrix 이미지 자동 저장
- 클래스 누락이나 샘플이 극소한 경우(예: 하드웨어로 일부 상태만 수집됨) 알아보기 힘든 sklearn 예외 대신 명확한 한국어 안내와 함께 종료하거나, GridSearchCV 폴드 수를 자동으로 줄여 계속 진행합니다.

**검증 결과 (실제 실행 로그, `--mode both`)**

| 방식 | 모델 | 최적 하이퍼파라미터 | Accuracy | Precision | Recall |
|---|---|---|---|---|---|
| pixel | SVM | kernel=linear, C=0.1, gamma=scale | 0.9259 | 0.9444 | 0.9444 |
| pixel | Random Forest | n_estimators=200, max_depth=None | 0.9259 | 0.9444 | 0.9444 |
| **features** | **SVM** | kernel=linear, C=10, gamma=scale | **1.0000** | 1.0000 | 1.0000 |
| features | Random Forest | n_estimators=100, max_depth=None | 1.0000 | 1.0000 | 1.0000 |

→ 최적 모델은 방식별로 각각 `models/best_model.joblib`(pixel) / `models/best_model_features.joblib`(features)로 저장됨.
**70% 이상 정확도 요구사항 두 방식 모두 충족 ✅** (`models/confusion_matrix_*.png` 참고)

> 참고: features 모드의 100% 정확도는 테스트 샘플이 27개뿐인 작은 데이터셋 기준이라 다소 낙관적일 수
> 있습니다. 그래도 50,176차원 픽셀보다 14개의 해석 가능한 특징만으로 동등하거나 더 나은 성능이 나온다는
> 점은, 이 정도 규모의 데이터셋에서는 명시적 특징 추출이 이미지 픽셀 flatten보다 더 안정적인 접근일 수
> 있음을 시사합니다. 실측 데이터로 재학습 후 더 큰 테스트셋으로 재검증을 권장합니다.
>
> 본 검증은 하드웨어가 없는 개발 환경에서 시뮬레이션 신호로 생성한 샘플 데이터 기준입니다.
> 실제 식물에서 수집한 데이터로 재학습하면 정확도가 달라질 수 있으니, 실측 데이터 확보 후
> `preprocess.py` → `train.py --mode both`를 재실행하세요.

### [4] 실시간 통합 시스템 (`main.py`)

```bash
# GUI 모드 (기본, VNC 데스크톱 접속 가정)
python3 main.py

# 헤드리스 모드 (터미널/SSH/VNC 터미널)
python3 main.py --no-gui

# 하드웨어 없이 저장된 CSV를 재생하여 테스트(시뮬레이션 입력)
python3 main.py --no-gui --sim_csv data/raw/자극.csv

# features 모드로 학습한 모델을 사용하고 싶다면 --model로 지정
python3 main.py --no-gui --model models/best_model_features.joblib --sim_csv data/raw/자극.csv
```

- `models/best_model.joblib`(기본, pixel 방식) 로드 → ADS1115(또는 시뮬레이션) 실시간 샘플을 2초 버퍼로 모아 즉시 추론
  - joblib 번들에 담긴 `feature_mode` 값("pixel" 또는 "explicit")을 보고 자동으로 올바른 특징 추출 방식을 선택하므로, `--model`만 바꾸면 pixel/features 어느 모델이든 그대로 동작합니다.
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

- [x] (2) 전처리 스크립트가 CSV → 224x224 스펙트로그램 이미지 생성 확인 (총 87장) + 명시적 특징 CSV 생성 확인
- [x] (3) 학습 스크립트 Accuracy 70% 이상 달성 확인 (pixel SVM 92.6%, features SVM 100%)
- [x] (4) 통합 스크립트가 GUI/헤드리스 모드에서 실시간 그래프·스펙트로그램·상태 표시 갱신 확인
- [x] 하드웨어 미보유 시 `data/raw/`의 샘플 CSV를 시뮬레이션 입력으로 사용하는 모드(`--sim_csv`) 추가 확인
- [x] Ctrl+C 안전 종료 확인
- [x] 정상/수분부족/자극 중 일부 상태만 수집되어도 preprocess.py/train.py가 안내 메시지와 함께 정상 진행(또는 명확한 에러로 종료) 확인
- [x] pixel/features 두 가지 특징 방식 모두 학습 및 실시간 추론(`--model`로 전환) 정상 동작 확인

---

## 데이터/모델 초기화(삭제) 방법

파이프라인 산출물은 `원시 CSV → 스펙트로그램 이미지 + 특징 CSV → 학습된 모델` 순서로 쌓입니다.
다시 수집/재실행하려는 단계에 맞춰 아래 명령으로 지우고 해당 스크립트를 다시 실행하세요.
**`data/raw/`의 원시 CSV는 실측 데이터라면 지우면 복구할 수 없으니 특히 주의하세요.**

**1) 특정 상태만 다시 수집하고 싶을 때** (다른 상태 CSV는 유지)
```bash
rm data/raw/자극.csv
cd src && python3 sensor_control.py --state 자극 --duration 30 --rate 250
```

**2) 원시 CSV는 그대로 두고 전처리 결과만 다시 만들고 싶을 때**
```bash
rm -rf data/spectrogram
rm -f data/features.csv
cd src && python3 preprocess.py --raw_dir ../data/raw --out_dir ../data/spectrogram
```
> `data/spectrogram/`은 `os.makedirs(exist_ok=True)`로 생성되어 기존 파일을 자동으로 지우지
> 않습니다. 원시 CSV 길이가 달라지면(윈도우 개수가 바뀌면) 이전에 생성된 이미지 일부가 지워지지
> 않고 그대로 남을 수 있으므로, 재수집 후에는 폴더를 통째로 지우고 재실행하는 것을 권장합니다.

**3) 전처리 결과는 그대로 두고 모델만 다시 학습하고 싶을 때**
```bash
rm -f models/*.joblib models/confusion_matrix_*.png
cd src && python3 train.py --mode both
```
> `train.py`는 파일명이 겹치면 덮어쓰므로 사실 안 지워도 동작은 하지만, 예전 결과와 섞여
> 헷갈리지 않도록 지우고 새로 학습하는 것을 권장합니다.

**4) 완전 초기화** (한 번에 전부 지우고 처음부터)
```bash
rm -f data/raw/*.csv
rm -rf data/spectrogram
rm -f data/features.csv
rm -rf models
```
이후 `sensor_control.py`(각 상태별) → `preprocess.py` → `train.py --mode both` 순서로 재실행하세요.

---

## 성능 최적화 내역

라즈베리파이 실시간 구동을 고려해 다음과 같은 최적화를 적용했습니다.

- **실시간 추론 주기 제한**: 원래는 버퍼가 찬 이후 매 샘플(최대 250~1000Hz)마다 필터링→스펙트로그램→추론 전체 파이프라인을 다시 실행했습니다. `RealtimeClassifier`가 `predict_hz`(기본 5Hz, GUI/헤드리스의 `refresh_hz`와 연동)로 무거운 연산 빈도를 제한하도록 수정했습니다.
- **스펙트로그램 렌더링 방식 교체**: matplotlib Figure/Axes/Canvas 렌더링 대신 `src/spectro_render.py`의 viridis 컬러맵 룩업 테이블 기반 렌더링을 사용합니다. 스펙트로그램 1장당 렌더링 시간이 약 **19배** 단축되었고(약 14ms → 0.7ms), `preprocess.py`(데이터셋 생성)와 `inference.py`(실시간 추론)가 동일한 함수를 공유해 학습·추론 특징이 항상 일치하도록 했습니다. 이 방식은 기존 matplotlib 렌더링과 픽셀 단위로 완전히 동일하지는 않아(구조적으로는 매우 유사, 상관계수 0.99) 모델을 이 방식으로 재생성한 데이터셋으로 재학습했습니다.
- **시뮬레이션 신호 생성 벡터화**: `sensor_control.py`의 시뮬레이션 모드 데이터 수집을 numpy 벡터 연산으로 일괄 처리해 약 9배 빨라졌습니다.
- **Butterworth 필터 계수 캐싱, 불필요한 PIL 리사이즈 제거** 등 세부 최적화 다수 적용.

재학습 결과 Accuracy는 기존과 동일한 수준(SVM 92.6%, Random Forest 92.6%)을 유지했습니다.
