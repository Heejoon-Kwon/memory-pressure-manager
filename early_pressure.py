"""Fast local pressure prediction with hysteresis. No network on the response path."""
from collections import deque


class EarlyPressure:
    def __init__(self, config):
        c = config.get('prevention', {})
        self.enabled = c.get('enabled', True)
        self.warning = float(c.get('warning_available_percent', 25))
        self.action = float(c.get('action_available_percent', 20))
        self.release = float(c.get('release_available_percent', 27))
        self.horizon = float(c.get('prediction_seconds', 30))
        if not 8 <= self.action < self.warning <= self.release <= 50 or not 5 <= self.horizon <= 120:
            raise ValueError('prevention 임계값 순서를 확인하세요')
        self.samples = deque(maxlen=15)
        self.active = False
        self.seconds_to_action = None

    def classify(self, sample, level, now):
        if not self.enabled:
            return level
        self.samples.append((now, sample.available))
        self.seconds_to_action = None
        if len(self.samples) >= 3:
            start, available = self.samples[0]
            duration = now-start
            rate = (available-sample.available)/duration if duration > 0 else 0
            if rate > 8*1024**2:
                self.seconds_to_action = max(0,(sample.available-sample.total*self.action/100)/rate)
        falling = (self.seconds_to_action is not None and self.seconds_to_action <= self.horizon
                   and sample.available_percent <= self.warning)
        if sample.available_percent <= self.action or falling or level == 'critical':
            self.active = True
        elif sample.available_percent >= self.release and (sample.full or 0) < 1:
            self.active = False
        if self.active:
            return 'critical'
        return 'warning' if sample.available_percent <= self.warning else level
