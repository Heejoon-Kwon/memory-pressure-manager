"""Labels shared by the monitoring window without importing optional dependencies."""
REASONS = {
    'unknown_jvm': '미식별 JVM · IDE 오인 종료 방지',
    'insufficient_gain': '32 MiB 미만 · 절약 효과가 작은 후보 제외',
    'protected_descendant': '보호된 앱의 상위 프로세스 · 종료 제외',
    'no_memory': '회수할 메모리 없음',
    'system_process': '시스템·세션 프로세스 · 조회 전용',
    'protected': '보호 중',
    'chrome_tab_mode': 'Chrome 전체 종료 차단 · 탭별 관리',
    'ambiguous_process': '대표 프로세스를 하나로 확인할 수 없음',
    'small_memory': '메모리 사용량이 128 MiB 미만',
    'previous_attempt_failed': '이번 압박 상황에서 대응 실패로 대기 중',
}
