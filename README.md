# Probe

Ubuntu GNOME에서 메모리 압박을 감시하고, 중요한 앱의 우선순위를 관리합니다. 인터넷으로 노출되는 서버와 대화창 없이 로컬 사용자 서비스와 GNOME 확장으로 실행됩니다.

```sh
git clone https://github.com/Heejoon-Kwon/probe.git
cd probe
./install.sh
```

Python venv, GTK 3용 Python GI, GNOME 사용자 세션이 필요합니다. 설치 스크립트는 설정, 사용자 서비스, 상단 바 확장을 설치합니다. GNOME에서 확장을 찾지 못하면 작업을 저장하고 로그아웃한 뒤 다시 로그인해 `gnome-extensions enable probe@local`을 실행하세요. 기존 RAM Lite와 RAM Guard 표시를 쓰고 있다면 각각 `gnome-extensions disable jarvis-ram-lite@local`과 `gnome-extensions disable ram-guard@local`로 끌 수 있습니다.

서비스가 실행 중인데 상단 바에 표시가 없으면 Ubuntu 화면에서 연 터미널에서 `./panel.sh`를 실행하세요. 이 명령은 Shell의 확장 활성화 상태를 확인하고, 처음 설치한 확장을 현재 세션이 아직 찾지 못하면 다음 로그인에 활성화되도록 등록합니다. GNOME 상단 바와 별개로 `/usr/bin/python3 window.py`로 관리 창을 열 수 있습니다.

상단 바의 RAM·비용을 누르고 **메모리 우선순위 열기**를 선택하세요.

| 앱 설정 | 동작 |
|---|---|
| 보호 | Probe 종료 후보에서 제외. 확인된 전용 사용자 앱 범위에는 systemd-oomd의 `avoid` 적용을 시도 |
| 먼저 닫기 · 다음에 닫기 · 마지막에 닫기 | 심각한 메모리 압박이 지속될 때 목록에서 앞선 순서로 앱 하나에 SIGTERM 요청 |
| 유지 | Probe 종료 후보에서 제외 |

IntelliJ IDEA, VS Code, DataGrip, VSCodium은 기본 **보호**입니다. 다른 앱의 기본값은 **유지**입니다. Chrome 같은 앱의 실행 파일 이름을 입력할 필요 없이 창에서 우선순위를 선택합니다. 자동 대응 후보는 내 계정에서 실행 중이고 이름과 단일 대표 프로세스를 확인할 수 있는 지원 앱으로 제한됩니다. 앱에 대한 SIGTERM은 저장되지 않은 작업을 잃게 할 수 있으므로 자동으로 닫아도 되는 앱에만 닫기 우선순위를 지정하세요. 앱 목록의 메모리는 프로세스 RSS의 합계이고 공유 메모리가 중복될 수 있습니다.

Probe는 `MemAvailable`, PSI와 스왑 I/O로 압박을 평가합니다. 높은 RAM 표시만으로 종료를 요청하지 않습니다. 기본값에서는 가용 메모리가 8% 이하이거나 가용 메모리가 18% 미만인 상태에서 PSI 압박도 지속되면, 설정상 종료 가능한 앱을 한 번에 하나씩 처리합니다. 재활용된 PID에 신호를 보내지 않도록 Linux pidfd를 사용합니다. 다른 앱을 닫기 전에 가용 메모리가 실제로 늘었는지 확인합니다.

시스템·세션 서비스와 미분류 프로세스는 Probe가 종료하지 않습니다. `systemd-oomd`의 우선순위 반영 여부는 Ubuntu의 cgroup 구성과 권한에 따라 달라집니다. 커널 OOM killer, 시스템 전체 부족, 앱 자체 크래시까지 막을 수는 없습니다. 보호를 적용한 범위 수는 창에서 확인할 수 있습니다.

## OpenAI와 저장된 대응

`config.yml`의 `openai.api_key`와 `openai.monthly_budget_usd`를 채운 뒤 `systemctl --user restart probe.service`를 실행하세요. 기본 모델 ID는 `gpt-6-luna`이며 필요하면 `openai.model`에서 변경할 수 있습니다. 계정에서 사용 가능하고 가격표에서 확인되는 모델이어야 합니다. 기본 월 예산 0은 호출을 끕니다. RAM, 가용 메모리, 스왑 사용량, PSI, 후보 앱 종류와 예상 RSS만 전달합니다. 모델의 출력은 이미 허용된 최우선 후보 중 앱 하나 또는 보류로 제한합니다. 앱 닫기는 로컬 정책이 검증해 실행합니다. 단가를 확인하지 못하거나 월 예산을 넘을 때는 로컬 기준으로 대응합니다.

문제 유형은 PSI 구간, 최근 스왑 I/O 여부, 같은 우선순위 후보의 조합으로 구분합니다. 해당 유형에 저장된 대응이 있고 여전히 허용된 대상이면 API를 건너뜁니다. 앱 종료와 가용 메모리 개선을 함께 확인한 경우에만 로컬 SQLite에 대응을 저장합니다. 회복하지 못했으면 저장된 대응을 제거하고, 압박이 지속되고 다른 적격 앱이 있을 때 다시 판단합니다. 패널 비용은 이번 달의 로컬 추정치입니다.

우선순위 창 맨 아래의 **AI 제안 · 스킬 · 대응 기록 보기**에서 현재 스킬과 AI 요청·제안, 대응 및 검증 결과를 확인합니다. 요청 내용은 시스템 메모리 지표와 후보 앱 정보이며 API 키는 기록하지 않습니다. 기록은 로컬 SQLite에 최대 2,000건 보관하고 창에는 최신 200건을 표시합니다. 기존 버전에서 발생한 활동 이력은 소급 복원할 수 없지만 기존 스킬은 표시됩니다.

서비스 확인은 `systemctl --user status probe.service`, 실행 로그는 `journalctl --user -u probe.service -n 30`, 중지는 `systemctl --user stop probe.service`입니다. 이후 로그인에서도 시작하지 않으려면 `systemctl --user disable --now probe.service`를 실행하세요.

근거 문서 [Linux 메모리 PSI](https://docs.kernel.org/accounting/psi.html), [Linux cgroup v2](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html), [systemd 자원 제어](https://www.freedesktop.org/software/systemd/man/latest/systemd.resource-control.html), [systemd-oomd](https://www.freedesktop.org/software/systemd/man/latest/systemd-oomd.service.html).
