#!/usr/bin/env bash
# install.sh -- 전원만 꽂으면 대시보드가 자동으로 뜨도록 등록한다.
#
#   cd ~/project
#   bash deploy/install.sh            # 대시보드 자동시작만
#   bash deploy/install.sh --hotspot  # + 파이를 Wi-Fi 핫스팟으로 (공유기 없는 곳용)
#
# 되돌리기:
#   sudo systemctl disable --now plant-dashboard
#   sudo nmcli connection delete GML-PLANT   # --hotspot 을 썼을 때만
set -euo pipefail

HOTSPOT=0
[[ "${1:-}" == "--hotspot" ]] && HOTSPOT=1

# 이 스크립트가 있는 곳의 상위 = 프로젝트 루트. 경로를 하드코딩하지 않는다.
PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="${SUDO_USER:-$USER}"

# 가상환경이 있으면 그 파이썬을, 없으면 시스템 파이썬을 쓴다.
if [[ -x "$PROJECT/venv/bin/python3" ]]; then
    PYTHON="$PROJECT/venv/bin/python3"
else
    PYTHON="$(command -v python3)"
    echo "⚠️  venv를 못 찾아 시스템 python3($PYTHON)을 씁니다."
    echo "   가상환경을 쓰려면 먼저: python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
fi

if [[ ! -f "$PROJECT/models/best_model.joblib" ]]; then
    echo "❌ models/best_model.joblib 이 없습니다. 먼저 학습하거나 git pull 하세요."
    exit 1
fi

echo "프로젝트 : $PROJECT"
echo "실행 사용자: $RUN_USER"
echo "파이썬    : $PYTHON"

# I2C 장치를 sudo 없이 읽으려면 i2c 그룹에 있어야 한다.
if getent group i2c >/dev/null && ! id -nG "$RUN_USER" | grep -qw i2c; then
    echo "→ $RUN_USER 를 i2c 그룹에 추가합니다 (재부팅 후 적용)."
    sudo usermod -aG i2c "$RUN_USER"
fi

# 템플릿의 자리표시자를 실제 값으로 바꿔 systemd 유닛을 설치한다.
sed -e "s|__USER__|$RUN_USER|g" \
    -e "s|__PROJECT__|$PROJECT|g" \
    -e "s|__PYTHON__|$PYTHON|g" \
    "$PROJECT/deploy/plant-dashboard.service" \
    | sudo tee /etc/systemd/system/plant-dashboard.service >/dev/null

sudo systemctl daemon-reload
sudo systemctl enable --now plant-dashboard

if [[ $HOTSPOT -eq 1 ]]; then
    echo "→ Wi-Fi 핫스팟(GML-PLANT) 등록"
    sudo nmcli device wifi hotspot ifname wlan0 ssid GML-PLANT password "gml12345" || true
    # 부팅할 때마다 자동으로 올라오게 한다 (hotspot 명령이 만든 연결 이름은 보통 'Hotspot').
    for name in GML-PLANT Hotspot; do
        sudo nmcli connection modify "$name" connection.autoconnect yes 2>/dev/null || true
    done
    echo "   폰을 GML-PLANT 에 연결한 뒤  http://10.42.0.1:5000"
fi

echo
echo "✅ 등록 완료. 이제 전원만 꽂으면 부팅 후 자동으로 대시보드가 뜹니다."
echo "   상태 확인 : systemctl status plant-dashboard"
echo "   로그 보기 : journalctl -u plant-dashboard -f"
echo "   접속 주소 : http://$(hostname -I | awk '{print $1}'):5000"
