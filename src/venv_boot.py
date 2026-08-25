"""
venv_boot.py
============
`source venv/bin/activate` 를 매번 치지 않아도 되게 한다.

시스템 파이썬으로 실행하면 필요한 라이브러리가 없어서 곧바로 ImportError 가 난다.
그래서 이 모듈은 **다른 import 보다 먼저** 불려서, venv 파이썬이 있으면 그쪽으로
프로세스를 갈아탄다(os.execv). 사용자가 보기에는 그냥 실행된 것처럼 보인다.

    python3 main.py --web          ← venv 안 켜도 이대로 동작

쓰는 법: 스크립트 맨 위, numpy 같은 외부 라이브러리 import 보다 **먼저**

    import venv_boot; venv_boot.ensure()

끄고 싶으면 GML_NO_VENV=1 을 주면 된다(시스템 파이썬을 그대로 쓴다).
"""

import os
import sys

_GUARD = "GML_VENV_REEXEC"          # 무한 재실행 방지


def _repo_root():
    """이 파일 위치에서 위로 올라가며 venv/ 나 requirements.txt 가 있는 곳을 찾는다."""
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(4):
        if os.path.isdir(os.path.join(d, "venv")) or \
           os.path.isfile(os.path.join(d, "requirements.txt")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None


def venv_python(root=None):
    """저장소 안 venv 의 파이썬 실행 파일 경로. 없으면 None."""
    root = root or _repo_root()
    if not root:
        return None
    for rel in (("venv", "bin", "python3"), ("venv", "bin", "python"),
                ("venv", "Scripts", "python.exe")):
        p = os.path.join(root, *rel)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def ensure():
    """venv 파이썬이 따로 있으면 그것으로 갈아타 현재 스크립트를 다시 실행한다."""
    if os.environ.get("GML_NO_VENV"):
        return
    # 이미 어떤 가상환경 안에서 돌고 있으면 그대로 둔다
    if sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        return
    if os.environ.get(_GUARD):
        # 한 번 갈아탔는데도 여기까지 왔다면 더 시도하지 않는다
        return
    py = venv_python()
    if not py:
        return
    try:
        if os.path.samefile(py, sys.executable):
            return               # 이미 그 파이썬으로 돌고 있다
    except OSError:
        pass
    script = os.path.abspath(sys.argv[0])
    if not os.path.isfile(script):
        return                       # -c, -m, 대화형 등은 건드리지 않는다
    os.environ[_GUARD] = "1"
    try:
        os.execv(py, [py, script] + sys.argv[1:])
    except OSError as e:
        # 조용히 넘어가면 곧바로 ModuleNotFoundError 가 뜨는데, 그것만 보고는
        # venv 전환이 실패했다는 걸 알 수 없다. 실패했다는 사실을 남긴다.
        print(f"[venv_boot] ⚠️  venv 파이썬으로 전환하지 못했습니다: {e}", file=sys.stderr)
        print(f"[venv_boot]     {py}", file=sys.stderr)
        print(f"[venv_boot]     시스템 파이썬으로 계속합니다 — 라이브러리가 없으면 "
              f"'source venv/bin/activate' 후 실행하세요.", file=sys.stderr)


def report():
    """왜 전환이 되는지/안 되는지 한눈에 본다.

        python3 src/venv_boot.py
    """
    root = _repo_root()
    py = venv_python(root)
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    print(f"  저장소 위치   : {root}")
    print(f"  venv 파이썬   : {py or '못 찾음'}")
    print(f"  지금 파이썬   : {sys.executable}")
    print(f"  이미 venv 안? : {in_venv}")
    print(f"  GML_NO_VENV   : {os.environ.get('GML_NO_VENV') or '(안 켜짐)'}")
    if in_venv:
        print("  → 이미 가상환경 안이라 아무것도 하지 않습니다.")
    elif not py:
        print("  → venv 를 못 찾아 시스템 파이썬으로 진행합니다.")
    else:
        print("  → 실행하면 이 파이썬으로 갈아탑니다.")
    # 실제로 쓸 파이썬에 필요한 라이브러리가 있는지도 본다
    import subprocess
    target = py if (py and not in_venv) else sys.executable
    need = ["numpy", "scipy", "pandas", "sklearn", "flask", "joblib", "matplotlib"]
    code = "import importlib,sys;print(' '.join(m for m in sys.argv[1:] "\
           "if importlib.util.find_spec(m) is None))"
    try:
        out = subprocess.run([target, "-c", code] + need,
                             capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception as e:
        out = f"확인 실패: {e}"
    print(f"\n  쓸 파이썬     : {target}")
    print(f"  빠진 라이브러리: {out or '없음 (전부 있음)'}")
    if out and "확인 실패" not in out:
        print(f"\n  설치:  {target} -m pip install {out}")


if __name__ == "__main__":
    report()
