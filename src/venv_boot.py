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
    except OSError:
        pass                         # 갈아타기 실패하면 그냥 하던 대로 진행
