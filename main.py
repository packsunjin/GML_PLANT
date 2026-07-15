#!/usr/bin/env python3
"""
main.py
=======
실시간 식물 상태 분류 시스템의 최상위 진입점.

기본: GUI 모드 (VNC로 라즈베리파이 데스크톱에 접속했다는 가정 하에 Matplotlib 창 표시)
--no-gui: 헤드리스 모드 (터미널/SSH/VNC 터미널 어디서든 동작하는 Rich 대시보드)

사용 예:
    python main.py                       # GUI 모드, 실제 하드웨어 또는 자동 시뮬레이션
    python main.py --no-gui              # 헤드리스 모드
    python main.py --sim_csv data/raw/정상.csv   # 하드웨어 없을 때 저장된 CSV를 재생하며 테스트
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from gui import run_gui, run_headless  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="실시간 식물 상태 분류 시스템")
    parser.add_argument("--model", default=os.path.join(os.path.dirname(__file__), "models", "best_model.joblib"))
    parser.add_argument("--sim_csv", default=None,
                         help="하드웨어가 없을 때 시뮬레이션 입력으로 사용할 CSV (예: data/raw/정상.csv)")
    parser.add_argument("--sim_state", default="정상", choices=["정상", "수분부족", "자극", "순환"],
                         help="CSV도 하드웨어도 없을 때 라이브로 생성할 상태 (순환=8초마다 정상→수분부족→자극 순환). --sim_csv가 있으면 무시됨")
    parser.add_argument("--no-gui", action="store_true", help="헤드리스 모드로 실행 (기본은 GUI 모드)")
    args = parser.parse_args()

    if not os.path.exists(args.model):
        print(f"[main] 모델 파일을 찾을 수 없습니다: {args.model}")
        print("[main] 먼저 src/train.py 를 실행해 모델을 학습/저장하세요.")
        sys.exit(1)

    if args.no_gui:
        run_headless(args.model, args.sim_csv, sim_state=args.sim_state)
    else:
        run_gui(args.model, args.sim_csv, sim_state=args.sim_state)


if __name__ == "__main__":
    main()
