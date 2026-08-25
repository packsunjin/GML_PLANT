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
    # ⚠️ 여기서 os.path.samefile(py, sys.executable) 로 비교하면 안 된다.
    # samefile 은 심볼릭 링크를 따라가는데, venv 의 python3 는 원본 인터프리터를
    # 가리키는 심볼릭 링크다. 그래서 항상 '같은 파일'로 나와 늘 여기서 되돌아갔고,
    # 결과적으로 한 번도 갈아타지 않았다.
    # '이미 그 venv 안인가'는 sys.prefix 로 판단해야 한다.
    venv_root = os.path.dirname(os.path.dirname(py))
    if os.path.normcase(os.path.abspath(sys.prefix)) == \
       os.path.normcase(os.path.abspath(venv_root)):
        return                   # 이미 그 venv 안이다
    script = os.path.abspath(sys.argv[0])
    if not os.path.isfile(script):
        return                       # -c, -m, 대화형 등은 건드리지 않는다
    os.environ[_GUARD] = "1"
    try:
        os.execv(py, [py, script] + sys.argv[1:])
    except Exception as e:
        # execv 는 성공하면 돌아오지 않는다. 여기까지 왔다는 건 실패했다는 뜻이다.
        # 조용히 넘어가면 곧바로 ModuleNotFoundError 가 뜨는데, 그것만 보고는
        # venv 전환이 실패했다는 걸 알 수 없다.
        print(f"[venv_boot] execv 실패({e}) — 대신 venv 의 라이브러리 경로를 붙입니다.",
              file=sys.stderr)
    # 프로세스를 갈아타지 못했더라도, venv 의 site-packages 를 import 경로에
    # 붙이면 대부분 그대로 돌아간다. execv 가 막힌 환경(권한·컨테이너 등)을 위한 대비다.
    add_venv_site_packages()


def venv_site_packages(root=None):
    """venv 안 site-packages 경로들. 없으면 빈 목록."""
    import glob
    root = root or _repo_root()
    if not root:
        return []
    pats = [os.path.join(root, "venv", "lib", "python*", "site-packages"),
            os.path.join(root, "venv", "Lib", "site-packages")]
    out = []
    for pat in pats:
        out.extend(sorted(p for p in glob.glob(pat) if os.path.isdir(p)))
    return out


def add_venv_site_packages():
    """venv 의 라이브러리를 import 경로에 붙인다(프로세스는 그대로).

    os.execv 로 갈아타는 것이 정석이지만, 그게 막히는 환경이 있다. 그럴 때
    최소한 라이브러리는 찾을 수 있게 한다. 파이썬 버전이 다르면 C 확장이
    안 맞을 수 있어 정석보다 약하지만, 아무것도 안 하는 것보다는 낫다."""
    # 뒤에 붙이면(append) 시스템 쪽 경로가 먼저 잡혀 venv 것이 무시된다.
    # sys.path[0] 은 실행한 스크립트의 폴더라 건드리지 않고, 그 바로 뒤에 넣는다.
    added = []
    for sp in reversed(venv_site_packages()):
        if sp not in sys.path:
            sys.path.insert(1, sp)
            added.append(sp)
    return added


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
