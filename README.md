# memory-pressure-manager

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
| 보호 | Memory Pressure Manager 종료 후보에서 제외. 확인된 전용 사용자 앱 범위에는 systemd-oomd의 `avoid` 적용을 시도 |
| 우선순위 1~999 | 숫자가 작을수록 중요. 메모리가 위험하면 가장 큰 숫자의 앱부터 SIGTERM 요청. 같은 숫자를 여러 앱에 사용 가능 |

일반 앱과 사용자 프로세스의 기본 우선순위는 **500**입니다. IntelliJ IDEA·VS Code·DataGrip·VSCodium은 기본 보호이며 직접 지정한 보호 설정도 유지합니다. 터미널과 파일 탐색기도 종료 후보에 포함됩니다. 루트·다른 사용자·시스템·세션 프로세스는 추적만 하고 자동 종료하지 않습니다. 대표 프로세스가 여러 개면 보류하고, 보호 앱을 자식으로 둔 상위 프로세스는 종료하지 않습니다. SIGTERM은 저장되지 않은 작업을 잃게 할 수 있습니다. 앱별 RSS 합계는 공유 메모리가 중복될 수 있으므로 호스트 전체 사용량에 다시 합산하지 않습니다.

Memory Pressure Manager는 `MemAvailable`, PSI와 스왑 I/O로 압박을 평가합니다. 높은 RAM 표시만으로 종료를 요청하지 않습니다. 기본값에서는 가용 메모리가 8% 이하이거나 가용 메모리가 18% 미만인 상태에서 PSI 압박도 지속되면, 설정상 종료 가능한 앱을 한 번에 하나씩 처리합니다. 재활용된 PID에 신호를 보내지 않도록 Linux pidfd를 사용합니다. 다른 앱을 닫기 전에 가용 메모리가 실제로 늘었는지 확인합니다.

시스템·세션 서비스와 다른 사용자의 프로세스는 Memory Pressure Manager가 종료하지 않습니다. 일반 사용자 프로세스는 실행 파일별로 모아 우선순위를 적용합니다. `systemd-oomd`의 우선순위 반영 여부는 Ubuntu의 cgroup 구성과 권한에 따라 달라집니다. 커널 OOM killer, 시스템 전체 부족, 앱 자체 크래시까지 막을 수는 없습니다. 보호를 적용한 범위 수는 창에서 확인할 수 있습니다.

## OpenAI와 저장된 대응

`config.yml`의 `openai.api_key`와 `openai.monthly_budget_usd`를 채운 뒤 `systemctl --user restart memory-pressure-manager.service`를 실행하세요. 기본 모델 ID는 `gpt-6-luna`이며 필요하면 `openai.model`에서 변경할 수 있습니다. 계정에서 사용 가능하고 가격표에서 확인되는 모델이어야 합니다. 기본 월 예산 0은 호출을 끕니다. RAM, 가용 메모리, 스왑 사용량, PSI, 후보 앱 종류와 예상 RSS만 전달합니다. 모델의 출력은 이미 허용된 최우선 후보 중 앱 하나 또는 보류로 제한합니다. 앱 닫기는 로컬 정책이 검증해 실행합니다. 단가를 확인하지 못하거나 월 예산을 넘을 때는 로컬 기준으로 대응합니다.

문제 유형은 PSI 구간, 최근 스왑 I/O 여부, 같은 우선순위 후보의 조합으로 구분합니다. 해당 유형에 저장된 대응이 있고 여전히 허용된 대상이면 API를 건너뜁니다. 앱 종료와 가용 메모리 개선을 함께 확인한 경우에만 로컬 SQLite에 대응을 저장합니다. 회복하지 못했으면 저장된 대응을 제거하고, 압박이 지속되고 다른 적격 앱이 있을 때 다시 판단합니다. 패널 비용은 이번 달의 로컬 추정치입니다.

우선순위 창 맨 아래의 **AI 제안 · 스킬 · 대응 기록 보기**에서 현재 스킬과 AI 요청·제안, 대응 및 검증 결과를 확인합니다. 요청 내용은 시스템 메모리 지표와 후보 앱 정보이며 API 키는 기록하지 않습니다. 기록은 로컬 SQLite에 최대 2,000건 보관하고 창에는 최신 200건을 표시합니다. 기존 버전에서 발생한 활동 이력은 소급 복원할 수 없지만 기존 스킬은 표시됩니다.

서비스 확인은 `systemctl --user status memory-pressure-manager.service`, 실행 로그는 `journalctl --user -u memory-pressure-manager.service -n 30`, 중지는 `systemctl --user stop memory-pressure-manager.service`입니다. 이후 로그인에서도 시작하지 않으려면 `systemctl --user disable --now memory-pressure-manager.service`를 실행하세요.

근거 문서 [Linux 메모리 PSI](https://docs.kernel.org/accounting/psi.html), [Linux cgroup v2](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html), [systemd 자원 제어](https://www.freedesktop.org/software/systemd/man/latest/systemd.resource-control.html), [systemd-oomd](https://www.freedesktop.org/software/systemd/man/latest/systemd-oomd.service.html).

### AI 대응 진단

서비스의 ExecStart에 지정된 폴더로 이동해서 `.venv/bin/python memory_pressure_manager.py --config config.yml --diagnose`를 실행하면 API 키 값 없이 키 입력 여부, 월 예산, 모델 가격 조회 가능 여부, 감지한 앱과 종료 후보, 최근 이력의 종류를 확인할 수 있습니다. 이 명령은 OpenAI API를 호출하거나 앱을 종료하지 않습니다. `priority` 0은 보호, 1~999는 종료 허용입니다. 회수할 메모리가 없거나 대표 프로세스가 하나가 아니면 후보에 포함되지 않습니다. 실제 서비스가 참조하는 경로는 `systemctl --user show memory-pressure-manager.service -p ExecStart`에서 확인하세요.

## 기존 Probe에서 업데이트

프로젝트 이름은 기능을 설명하는 `memory-pressure-manager`입니다. 저장소 주소와 `probe.py`, `probe.service`, `memora.py`, `memora.service`, `probe@local`, Chrome 네이티브 호스트 ID는 기존 설치 연결을 위해 호환됩니다. `./install.sh`는 이전 서비스를 중지하고 `memory-pressure-manager.service`로 이전하며, 기존 서비스 이름을 새 서비스의 별칭으로 연결합니다. 기존 `config.yml`, 우선순위, 비용, 스킬 데이터는 같은 위치를 사용합니다. 두 모니터가 중복 실행되지 않습니다.

관리창은 세로형이며 상단에 RAM·가용 메모리·스왑·API 비용과 대응 상태를 표시합니다. 전체/앱/Chrome 탭 필터와 검색으로 대상을 찾고, 각 항목에서 보호 및 우선순위를 설정합니다. 앱은 설치된 데스크톱 아이콘을 사용하며 일부 앱은 번들 아이콘으로 보완합니다. 탭은 Chrome에 저장된 파비콘을 사용하고, 없으면 브라우저 아이콘으로 표시합니다. 파비콘과 탭 제목은 로컬 표시용이며 OpenAI로 보내지 않습니다.

## Chrome 탭별 메모리 관리

1. `chrome://extensions`에서 **개발자 모드**를 켭니다.
2. **압축해제된 확장 프로그램을 로드합니다**를 눌러 이 저장소의 `chrome-extension` 폴더를 선택합니다.
3. 확장 카드에 표시된 32자리 **ID**를 복사합니다.
4. Memory Pressure Manager 폴더에서 `./install-chrome-host.py 확장ID`를 실행하고, Chrome 확장 아이콘을 눌러 연결 상태를 확인합니다. Memory Pressure Manager 서비스도 실행 중이어야 합니다. 여러 Chrome 프로필에서 쓰려면 각 프로필에 확장을 설치하세요. ID가 서로 다르다면 호스트 등록은 마지막 ID만 허용하므로 동일한 ID로 설치되어야 합니다.
5. Memory Pressure Manager 관리창의 **CHROME 탭** 목록 또는 확장 팝업에서 탭별 보호를 해제하고 1~999를 입력합니다. 관리창 목록은 연결된 프로필마다 갱신됩니다. 숫자가 클수록 먼저 메모리에서 내립니다. 탭 기본 우선순위는 500이며 보호는 직접 설정합니다. 설정은 현재 Chrome 세션과 정확한 URL에 적용됩니다. 확장 재로드·브라우저 재시작·주소 변경 후 기본 500으로 돌아갑니다.

확장은 약 3초마다 로컬 관리창과 상태를 공유합니다. 메모리 압박 시 Memory Pressure Manager 서비스가 탭 회수를 판단하고 지시합니다. 저장된 스킬이 없으면 OpenAI에 익명화한 회수 행동과 메모리 지표로 판단을 요청하고, 확인된 스킬이 있으면 재사용합니다. AI 오류 시 자동 API 재시도 없이 로컬 규칙으로 대응하며 앱 종료와 동일한 월간 예산 가드를 사용합니다. 탭 회수 후 실제 discarded 상태와 최소 32 MiB(또는 전체 메모리의 0.2%) 가용 메모리 증가, PSI 완화를 확인해야 스킬을 저장합니다. 이후 해당 스킬 사용 후 회복이 확인되지 않으면 삭제합니다. 이 검증은 관측된 개선이며 인과관계를 보장하지 않습니다. 활성, 고정, 소리 재생, 시크릿 탭은 제외합니다. 탭은 닫히지 않지만 다시 선택하면 새로 불러오므로 저장하지 않은 입력이나 재생 상태가 사라질 수 있습니다. Memory Pressure Manager의 보호는 Chrome 자체 메모리 절약 기능이나 커널 OOM을 통제하지 않습니다.

탭 주소는 Chrome 세션 저장소에만 두고 네이티브 브리지나 OpenAI로 전송하지 않습니다. 탭 제목은 관리창에 표시하기 위해 현재 사용자 전용 런타임 폴더에 잠시 저장하며 OpenAI에는 전송하지 않습니다. 로컬 이력에는 탭 정리 성공/실패만 남깁니다. 탭 관리 연결을 설치하면 Memory Pressure Manager의 Chrome 프로세스 전체 자동 종료가 차단됩니다. 연결 제거 후 앱 전체 종료를 다시 허용하려면 `~/.local/state/probe/chrome-tab-management` 파일을 삭제하세요(`XDG_STATE_HOME` 사용 시 해당 위치).

OpenAI 실제 연결 테스트는 `.venv/bin/python memory_pressure_manager.py --config config.yml --check-ai`입니다. 로컬 예산 범위에서 소액의 실제 API 요청을 한 번 보내고 사용량을 기록합니다. 가상의 메모리 정보만 사용하고 앱을 종료하지 않습니다. 오류는 종류만 출력합니다.

관리창의 AI 상태는 설정 여부와 자동 대응 요청 상태를 보여 줍니다. AI 연결 테스트의 성공은 인증과 모델 호출이 가능하다는 뜻이며, 실제 대응은 서비스 실행·지속적인 메모리 압박·종료 허용 후보가 있어야 시작됩니다. Chrome 탭 회수도 AI 판단·실행·회복 검증·스킬 저장 경로를 사용합니다. 스킬은 특정 페이지의 내용이 아닌 해당 압박 유형에서 가장 큰 우선순위 숫자의 탭을 하나 회수하는 규칙입니다.

보호를 해제한 앱이라도 현재 조건에서 곧바로 종료 가능한지 별도로 확인합니다. 대표 프로세스가 여러 개이거나 회수할 메모리가 없으면 보류하며, Chrome 탭 관리가 설치된 동안 Chrome 전체 자동 종료는 차단됩니다. 이런 이유는 관리창 앱 이름 아래와 진단 결과 `blocked_reason`에서 확인할 수 있습니다. Chrome 탭은 앱 종료와 별도로 확장이 회수하며 활성·고정·소리 재생 탭 등은 제외됩니다.

## Chrome 앱 창과 실행 버전 확인

Chrome의 설치된 웹 앱(PWA)은 표시 모드가 standalone/minimal-ui/window-controls-overlay인지 확인하여 Chrome 앱으로 구분합니다. 확장 감지용 스크립트는 표시 모드만 읽으며 페이지 본문·입력 내용·쿠키는 읽지 않습니다. 이를 위해 HTTP(S) 페이지 스크립트 권한을 사용합니다. 이전부터 열려 있던 앱이 확장에 보이지 않으면 업데이트 후 그 앱 창을 다시 여세요. Chrome API에서 해당 창을 노출하지 않거나 표시 모드를 확인할 수 없으면 제어를 주장하지 않고 보류합니다.

일반 비활성 탭은 discard, 백그라운드의 보호되지 않은 Chrome 앱은 해당 앱 페이지 종료로 처리합니다. 선택된 탭은 discard가 불가능하므로 앱 창 종료는 별도 행동입니다. 현재 전경 창·고정·소리 재생·보호 항목은 제외합니다. 앱 종료도 AI 판단 → 명령 → 브리지 완료 응답 → 호스트 메모리 개선 확인 → 스킬 저장 경로를 사용합니다. 창을 닫으면 저장하지 않은 내용이 사라질 수 있습니다.

관리창 상단 빌드는 `2026.11.25.3`입니다. `.venv/bin/python memory_pressure_manager.py --config config.yml --diagnose`에서 `installed_build`, `running_instance.build`, `running_instance.source_dir`, `chrome_profiles`를 비교하세요. 키는 출력하지 않습니다. `./install.sh`는 이 설치의 이전 관리창을 닫고, 기존 Chrome 호스트의 허용 확장 ID를 유지하면서 실행 파일 경로만 현재 체크아웃으로 맞춥니다. 확장도 Chrome에서 새로고침해야 합니다.

가용 메모리 3% 이하 또는 가용 8% 미만이면서 PSI full 10 이상인 긴급 상황에서는 연속 샘플과 API를 기다리지 않고 현재 정책으로 로컬 대응합니다. 그 외에는 설정한 압박 확인 조건과 AI·스킬 경로를 사용합니다. 시스템 커널 설정·스왑·전역 캐시는 임의로 바꾸지 않습니다.

### 응답하지 않는 앱 종료

보호되지 않은 일반 사용자 프로세스에 SIGTERM을 보낸 뒤 기본 8초 동안 응답이 없으면, 소유자·PID 시작 시각·실행 파일·보호 설정·보호된 하위 프로세스를 다시 확인하고 SIGKILL로 전환합니다. 긴급 상황의 유예는 2초입니다. `watch.allow_force_kill: false`로 강제 종료 전환을 끌 수 있고, 일반 상황의 유예는 `watch.force_kill_after_seconds`로 바꿉니다. 시스템 프로세스와 보호 대상에는 적용하지 않습니다.

목록에서는 실제 앱 아이콘 또는 사이트 파비콘이 있는 항목을 먼저 표시하고, 기본 아이콘만 있는 항목을 아래에 표시합니다. 앱은 각 그룹 안에서 메모리 사용량 내림차순이며, 탭은 각 그룹 안에서 기존 순서를 유지합니다. 이 화면 정렬은 자동 대응 우선순위를 변경하지 않습니다.
