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
| 우선순위 1~999 | 숫자가 작을수록 중요. 메모리가 위험하면 가장 큰 숫자의 앱부터 SIGTERM 요청. 같은 숫자를 여러 앱에 사용 가능 |

기본값은 모든 앱을 **보호**합니다. 이전의 보호·유지는 모두 보호로, 기존 먼저·다음·마지막에 닫기는 각각 999·500·1로 유지합니다. 다른 앱을 자동으로 닫아도 된다면 보호를 해제하고 우선순위를 정하세요. 숫자를 입력하면 자동 종료를 허용하는 것입니다. SIGTERM은 저장되지 않은 작업을 잃게 할 수 있습니다. 자동 대응 후보는 내 계정에서 실행 중이고 이름과 단일 대표 프로세스를 확인할 수 있는 지원 앱으로 제한됩니다. 앱 목록의 메모리는 프로세스 RSS 합계이며 공유 메모리가 중복될 수 있습니다.

Probe는 `MemAvailable`, PSI와 스왑 I/O로 압박을 평가합니다. 높은 RAM 표시만으로 종료를 요청하지 않습니다. 기본값에서는 가용 메모리가 8% 이하이거나 가용 메모리가 18% 미만인 상태에서 PSI 압박도 지속되면, 설정상 종료 가능한 앱을 한 번에 하나씩 처리합니다. 재활용된 PID에 신호를 보내지 않도록 Linux pidfd를 사용합니다. 다른 앱을 닫기 전에 가용 메모리가 실제로 늘었는지 확인합니다.

시스템·세션 서비스와 미분류 프로세스는 Probe가 종료하지 않습니다. `systemd-oomd`의 우선순위 반영 여부는 Ubuntu의 cgroup 구성과 권한에 따라 달라집니다. 커널 OOM killer, 시스템 전체 부족, 앱 자체 크래시까지 막을 수는 없습니다. 보호를 적용한 범위 수는 창에서 확인할 수 있습니다.

## OpenAI와 저장된 대응

`config.yml`의 `openai.api_key`와 `openai.monthly_budget_usd`를 채운 뒤 `systemctl --user restart probe.service`를 실행하세요. 기본 모델 ID는 `gpt-6-luna`이며 필요하면 `openai.model`에서 변경할 수 있습니다. 계정에서 사용 가능하고 가격표에서 확인되는 모델이어야 합니다. 기본 월 예산 0은 호출을 끕니다. RAM, 가용 메모리, 스왑 사용량, PSI, 후보 앱 종류와 예상 RSS만 전달합니다. 모델의 출력은 이미 허용된 최우선 후보 중 앱 하나 또는 보류로 제한합니다. 앱 닫기는 로컬 정책이 검증해 실행합니다. 단가를 확인하지 못하거나 월 예산을 넘을 때는 로컬 기준으로 대응합니다.

문제 유형은 PSI 구간, 최근 스왑 I/O 여부, 같은 우선순위 후보의 조합으로 구분합니다. 해당 유형에 저장된 대응이 있고 여전히 허용된 대상이면 API를 건너뜁니다. 앱 종료와 가용 메모리 개선을 함께 확인한 경우에만 로컬 SQLite에 대응을 저장합니다. 회복하지 못했으면 저장된 대응을 제거하고, 압박이 지속되고 다른 적격 앱이 있을 때 다시 판단합니다. 패널 비용은 이번 달의 로컬 추정치입니다.

우선순위 창 맨 아래의 **AI 제안 · 스킬 · 대응 기록 보기**에서 현재 스킬과 AI 요청·제안, 대응 및 검증 결과를 확인합니다. 요청 내용은 시스템 메모리 지표와 후보 앱 정보이며 API 키는 기록하지 않습니다. 기록은 로컬 SQLite에 최대 2,000건 보관하고 창에는 최신 200건을 표시합니다. 기존 버전에서 발생한 활동 이력은 소급 복원할 수 없지만 기존 스킬은 표시됩니다.

서비스 확인은 `systemctl --user status probe.service`, 실행 로그는 `journalctl --user -u probe.service -n 30`, 중지는 `systemctl --user stop probe.service`입니다. 이후 로그인에서도 시작하지 않으려면 `systemctl --user disable --now probe.service`를 실행하세요.

근거 문서 [Linux 메모리 PSI](https://docs.kernel.org/accounting/psi.html), [Linux cgroup v2](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html), [systemd 자원 제어](https://www.freedesktop.org/software/systemd/man/latest/systemd.resource-control.html), [systemd-oomd](https://www.freedesktop.org/software/systemd/man/latest/systemd-oomd.service.html).

### AI 대응 진단

서비스의 ExecStart에 지정된 폴더로 이동해서 `.venv/bin/python probe.py --config config.yml --diagnose`를 실행하면 API 키 값 없이 키 입력 여부, 월 예산, 모델 가격 조회 가능 여부, 감지한 앱과 종료 후보, 최근 이력의 종류를 확인할 수 있습니다. 이 명령은 OpenAI API를 호출하거나 앱을 종료하지 않습니다. `priority` 0은 보호, 1~999는 종료 허용입니다. 해당 앱이 128 MiB 미만이거나 대표 프로세스가 하나가 아니면 후보에 포함되지 않습니다. 실제 서비스가 참조하는 경로는 `systemctl --user show probe.service -p ExecStart`에서 확인하세요.

## Chrome 탭별 메모리 관리

1. `chrome://extensions`에서 **개발자 모드**를 켭니다.
2. **압축해제된 확장 프로그램을 로드합니다**를 눌러 이 저장소의 `chrome-extension` 폴더를 선택합니다.
3. 확장 카드에 표시된 32자리 **ID**를 복사합니다.
4. Probe 폴더에서 `./install-chrome-host.py 확장ID`를 실행하고, Chrome 확장 아이콘을 눌러 연결 상태를 확인합니다. Probe 서비스도 실행 중이어야 합니다. 여러 Chrome 프로필에서 쓰려면 각 프로필에 확장을 설치하세요. ID가 서로 다르다면 호스트 등록은 마지막 ID만 허용하므로 동일한 ID로 설치되어야 합니다.
5. Probe 관리창의 **CHROME 탭** 목록 또는 확장 팝업에서 탭별 보호를 해제하고 1~999를 입력합니다. 관리창 목록은 연결된 프로필마다 갱신됩니다. 숫자가 클수록 먼저 메모리에서 내립니다. 기본은 보호이며, 설정은 현재 Chrome 세션과 정확한 URL에만 적용됩니다. 브라우저 재시작이나 주소 변경 후 다시 보호됩니다.

확장 프로그램은 30초 간격으로 확인하고, Probe가 최근 심각한 메모리 압박을 감지했을 때에만 사용자가 허용한 HTTP(S) 탭 하나를 메모리에서 내립니다. 프로필 전체에 걸쳐 최소 90초 간격입니다. 활성, 고정, 소리 재생, 시크릿 탭은 제외합니다. 탭은 닫히지 않지만 다시 선택하면 새로 불러오므로 저장하지 않은 입력이나 재생 상태가 사라질 수 있습니다. Probe의 보호는 Chrome 자체 메모리 절약 기능이나 커널 OOM을 통제하지 않습니다.

탭 주소는 Chrome 세션 저장소에만 두고 네이티브 브리지나 OpenAI로 전송하지 않습니다. 탭 제목은 관리창에 표시하기 위해 현재 사용자 전용 런타임 폴더에 잠시 저장하며 OpenAI에는 전송하지 않습니다. 로컬 이력에는 탭 정리 성공/실패만 남깁니다. 탭 관리 연결을 설치하면 Probe의 Chrome 프로세스 전체 자동 종료가 차단됩니다. 연결 제거 후 앱 전체 종료를 다시 허용하려면 `~/.local/state/probe/chrome-tab-management` 파일을 삭제하세요(`XDG_STATE_HOME` 사용 시 해당 위치).

OpenAI 실제 연결 테스트는 `.venv/bin/python probe.py --config config.yml --check-ai`입니다. 로컬 예산 범위에서 소액의 실제 API 요청을 한 번 보내고 사용량을 기록합니다. 가상의 메모리 정보만 사용하고 앱을 종료하지 않습니다. 오류는 종류만 출력합니다.

관리창의 AI 상태는 설정 여부와 자동 대응 요청 상태를 보여 줍니다. AI 연결 테스트의 성공은 인증과 모델 호출이 가능하다는 뜻이며, 실제 대응은 서비스 실행·지속적인 메모리 압박·종료 허용 후보가 있어야 시작됩니다. Chrome 탭 회수는 AI가 아닌 로컬 우선순위 정책으로 처리합니다.

보호를 해제한 앱이라도 현재 조건에서 곧바로 종료 가능한지 별도로 확인합니다. 대표 프로세스가 여러 개이거나 메모리 사용량이 128 MiB 미만이면 보류하며, Chrome 탭 관리가 설치된 동안 Chrome 전체 자동 종료는 차단됩니다. 이런 이유는 관리창 앱 이름 아래와 진단 결과 `blocked_reason`에서 확인할 수 있습니다. Chrome 탭은 앱 종료와 별도로 확장이 회수하며 활성·고정·소리 재생 탭 등은 제외됩니다.
