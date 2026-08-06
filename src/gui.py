"""
gui.py
======
VNC 원격 데스크톱 환경에서 볼 수 있는 실시간 모니터링 화면을 제공합니다.

- GUI 모드(기본): Matplotlib Figure를 plt.ion()으로 띄워
    1) 최근 5초간 시계열 그래프
    2) 실시간 갱신 스펙트로그램
    3) 현재 상태 텍스트 + 큰 이모티콘(🌱/😵)
  을 하나의 창에 함께 표시합니다. VNC Viewer로 Pi 데스크톱에 접속하면 그대로 보입니다.

- 헤드리스 모드(--no-gui): Rich 라이브러리로 터미널 대시보드를 그립니다.
  (SSH/VNC 터미널 모두에서 동작)

Ctrl+C로 언제든 안전하게 종료됩니다.
"""

import argparse
import os
import sys
import time

import numpy as np

from inference import RealtimeClassifier, SAMPLE_RATE_HZ, WINDOW_SEC

# 상태별 표시 색상. matplotlib은 컬러 이모지를 못 그려 두부글자(□)가 되므로, GUI에서는
# 이모지 대신 이 색상 원으로 상태를 표시한다. (터미널/헤드리스는 이모지가 잘 나오므로 그대로 사용)
STATE_COLOR = {"정상": "#2e7d32", "수분부족": "#1565c0", "자극": "#ef6c00", "스트레스": "#c62828"}


def _has_display():
    """GUI 창을 실제로 띄울 수 있는 디스플레이가 있는지 확인한다.
    X11(DISPLAY) 또는 Wayland(WAYLAND_DISPLAY) 환경변수가 있어야 한다. 둘 다 없으면
    (SSH/헤드리스) matplotlib.use('TkAgg')는 예외를 안 내고 나중에 draw에서야 조용히
    실패하므로, 여기서 미리 판별해 파일 저장 모드로 명확히 폴백한다."""
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def run_gui(model_path, sim_csv, refresh_hz=5.0, sim_state="정상"):
    import matplotlib
    interactive_mode = False
    if _has_display():
        try:
            matplotlib.use("TkAgg")  # VNC 데스크톱에서 창으로 표시
            import matplotlib.pyplot as plt
            plt.ion()
            fig = plt.figure(figsize=(9, 6))
            interactive_mode = True
        except Exception as e:
            print(f"[gui] GUI 백엔드(TkAgg)를 사용할 수 없어 Agg(파일 저장 모드)로 전환합니다: {e}")

    if not interactive_mode:
        # 디스플레이가 없거나 TkAgg 실패 → Agg 비대화형(매 갱신마다 dashboard_last.png 저장)
        print("[gui] 표시할 디스플레이(DISPLAY/WAYLAND_DISPLAY)가 없어 Agg 파일 저장 모드로 실행합니다 "
              "-> dashboard_last.png (헤드리스면 `--no-gui` 텍스트 대시보드도 사용 가능)")
        matplotlib.use("Agg", force=True)
        import matplotlib.pyplot as plt
        plt.ioff()
        fig = plt.figure(figsize=(9, 6))

    # 한글 라벨(상태 텍스트/축 이름)이 깨지지 않도록 설치된 한글 폰트를 자동 지정한다.
    # (라즈베리파이 OS: sudo apt install fonts-noto-cjk 후 rm -rf ~/.cache/matplotlib)
    from font_utils import setup_korean_font
    import matplotlib.patches as mpatches
    if setup_korean_font() is None:
        print("[gui] 한글 폰트를 찾지 못했습니다. 한글이 깨지면 `sudo apt install fonts-noto-cjk` 후 "
              "`rm -rf ~/.cache/matplotlib` 하고 다시 실행하세요.")

    gs = fig.add_gridspec(2, 2)
    ax_ts = fig.add_subplot(gs[0, :])       # 시계열 (상단 전체)
    ax_spec = fig.add_subplot(gs[1, 0])     # 스펙트로그램 (좌하단)
    ax_status = fig.add_subplot(gs[1, 1])   # 상태 텍스트/이모지 (우하단)
    ax_status.axis("off")

    clf = RealtimeClassifier(model_path=model_path, sim_source_csv=sim_csv,
                             predict_hz=refresh_hz, sim_state=sim_state)
    print(f"[gui] 모델 로드: {clf.model_name}, 클래스={clf.classes}")
    print("[gui] Ctrl+C 로 종료할 수 있습니다.")

    last_result = None
    recent_signal = np.zeros(int(5 * clf.sample_rate))  # 최근 5초 표시용

    try:
        while True:
            result = clf.step()
            if result is not None:
                last_result = result
                # 최근 5초 버퍼 갱신: predict 주기(predict_every 샘플)마다 result가 나오므로,
                # 그 사이 새로 들어온 샘플 수만큼만(=predict_every) 필터링 신호의 최신 구간을
                # 밀어넣는다. 필터링 윈도우(2초=window_len) 전체를 매번 밀어넣으면 인접 예측끼리
                # 대부분 겹쳐서 시계열이 중복 표시되므로 새 구간만 반영한다.
                n_new = min(clf.predict_every, len(result["filtered_signal"]), len(recent_signal))
                recent_signal = np.roll(recent_signal, -n_new)
                recent_signal[-n_new:] = result["filtered_signal"][-n_new:]

                ax_ts.cla()
                t_axis = np.linspace(-5, 0, len(recent_signal))
                ax_ts.plot(t_axis, recent_signal, color="tab:green")
                ax_ts.set_title("최근 5초간 전위 신호 (필터링 후)")
                ax_ts.set_xlabel("시간 (s)")
                ax_ts.set_ylabel("전압 (V)")

                ax_spec.cla()
                ax_spec.pcolormesh(result["spec_t"], result["spec_f"], result["spectrogram_db"],
                                    shading="auto", cmap="viridis")
                ax_spec.set_title("실시간 스펙트로그램")
                ax_spec.set_xlabel("시간 (s)")
                ax_spec.set_ylabel("주파수 (Hz)")

                ax_status.cla()
                ax_status.axis("off")
                ax_status.set_xlim(0, 1)
                ax_status.set_ylim(0, 1)
                proba_str = f"{result['proba']*100:.1f}%" if result["proba"] is not None else "N/A"
                color = STATE_COLOR.get(result["state"], "gray")
                # 이모지 대신 상태별 색상 원 + 상태명(원 안) + 확신도(아래)
                ax_status.add_patch(mpatches.Circle((0.5, 0.62), 0.28, color=color, ec="black", lw=2))
                ax_status.text(0.5, 0.62, result["state"], color="white", fontsize=18,
                                fontweight="bold", ha="center", va="center")
                ax_status.text(0.5, 0.12, f"확신도 {proba_str}", fontsize=14, ha="center", va="center")

                fig.tight_layout()
                if interactive_mode:
                    try:
                        fig.canvas.draw()
                        fig.canvas.flush_events()
                        plt.pause(0.001)
                    except Exception:
                        pass
                else:
                    # 디스플레이(GUI 백엔드)가 없는 환경(예: 검증용 컨테이너)에서는
                    # 매 갱신마다 대시보드를 PNG로 저장하여 VNC 화면 갱신을 대체 확인한다.
                    out_path = os.path.join(os.path.dirname(__file__), "..", "dashboard_last.png")
                    fig.savefig(out_path)
                    print(f"상태: {result['emoji']} {result['state']}  (확신도={result['proba']}) -> {out_path}")

            clf.pace()
    except KeyboardInterrupt:
        print("\n[gui] Ctrl+C 감지 - 안전 종료합니다.")
    finally:
        plt.close("all")


def run_headless(model_path, sim_csv, refresh_hz=2.0, sim_state="정상"):
    """--no-gui: Rich 라이브러리로 터미널(SSH/VNC 터미널) 대시보드 표시"""
    use_rich = False
    if sys.stdout.isatty():
        # Rich의 Live 커서 제어는 실제 터미널(TTY)에서만 의미가 있음.
        # 파일 리다이렉션/파이프(검증 스크립트 등)에서는 일반 print 방식으로 폴백한다.
        try:
            from rich.live import Live
            from rich.table import Table
            use_rich = True
        except ImportError:
            use_rich = False

    clf = RealtimeClassifier(model_path=model_path, sim_source_csv=sim_csv,
                             predict_hz=refresh_hz, sim_state=sim_state)
    print(f"[headless] 모델 로드: {clf.model_name}, 클래스={clf.classes}")
    print("[headless] Ctrl+C 로 종료할 수 있습니다.")

    def render(result):
        if result is None:
            return "대기 중... (버퍼 수집 중)"
        proba_str = f"{result['proba']*100:.1f}%" if result["proba"] is not None else "N/A"
        table = Table(title="🌿 실시간 식물 상태 모니터링")
        table.add_column("항목")
        table.add_column("값")
        table.add_row("상태", f"{result['emoji']}  {result['state']}")
        table.add_row("확신도", proba_str)
        table.add_row("신호 평균(V)", f"{np.mean(result['filtered_signal']):.4f}")
        table.add_row("신호 표준편차(V)", f"{np.std(result['filtered_signal']):.4f}")
        return table

    try:
        if use_rich:
            last_result = None
            with Live(refresh_per_second=refresh_hz) as live:
                while True:
                    result = clf.step()
                    if result is not None:
                        last_result = result
                    live.update(render(last_result))
                    clf.pace()
        else:
            while True:
                result = clf.step()
                if result is not None:
                    proba_str = f"{result['proba']*100:.1f}%" if result["proba"] is not None else "N/A"
                    sys.stdout.write(
                        f"\r상태: {result['emoji']} {result['state']:6s}  확신도={proba_str:>7s}   "
                    )
                    sys.stdout.flush()
                clf.pace()
    except KeyboardInterrupt:
        print("\n[headless] Ctrl+C 감지 - 안전 종료합니다.")


def main():
    parser = argparse.ArgumentParser(description="실시간 식물 상태 모니터링 GUI / 터미널 대시보드")
    parser.add_argument("--model", default="../models/best_model.joblib")
    parser.add_argument("--sim_csv", default=None, help="하드웨어 없을 때 재생할 샘플 CSV 경로")
    parser.add_argument("--sim_state", default="정상", choices=["정상", "수분부족", "자극", "순환"],
                        help="CSV도 하드웨어도 없을 때 라이브로 생성할 상태(순환=8초마다 순환)")
    parser.add_argument("--no-gui", action="store_true", help="헤드리스(터미널) 모드로 실행")
    args = parser.parse_args()

    if args.no_gui:
        run_headless(args.model, args.sim_csv, sim_state=args.sim_state)
    else:
        run_gui(args.model, args.sim_csv, sim_state=args.sim_state)


if __name__ == "__main__":
    main()
