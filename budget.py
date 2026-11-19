"""Local conservative budget reservations, in integer micro-USD."""
import sqlite3
import uuid
from datetime import datetime, timezone

class BudgetExceeded(RuntimeError): pass
class Budget:
    def __init__(self, path, monthly_limit_microusd):
        self.path, self.limit = str(path), int(monthly_limit_microusd)
        if self.limit < 0: raise ValueError('negative limit')
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS usage (id TEXT PRIMARY KEY, month TEXT NOT NULL, reserved INTEGER NOT NULL, actual INTEGER, input_tokens INTEGER, output_tokens INTEGER, status TEXT NOT NULL)')
    def reserve(self, estimate):
        estimate = int(estimate)
        if estimate < 0: raise ValueError('negative estimate')
        month = datetime.now(timezone.utc).strftime('%Y-%m'); request_id = str(uuid.uuid4())
        with sqlite3.connect(self.path, timeout=10, isolation_level=None) as db:
            db.execute('BEGIN IMMEDIATE')
            used = db.execute('SELECT COALESCE(SUM(COALESCE(actual,reserved)),0) FROM usage WHERE month=?', (month,)).fetchone()[0]
            if used + estimate > self.limit:
                db.rollback(); raise BudgetExceeded('로컬 월간 예산 초과: API 호출 차단')
            db.execute('INSERT INTO usage(id,month,reserved,status) VALUES(?,?,?,?)', (request_id, month, estimate, 'pending'))
            db.commit()
        return request_id
    def finalize(self, request_id, actual, input_tokens, output_tokens):
        if min(actual, input_tokens, output_tokens) < 0: raise ValueError('negative usage')
        with sqlite3.connect(self.path) as db:
            cur = db.execute("UPDATE usage SET actual=?,input_tokens=?,output_tokens=?,status='complete' WHERE id=? AND status='pending'", (int(actual), int(input_tokens), int(output_tokens), request_id))
            if cur.rowcount != 1: raise ValueError('unknown or finalized reservation')
    def monthly(self, month=None):
        month = month or datetime.now(timezone.utc).strftime('%Y-%m')
        with sqlite3.connect(self.path) as db:
            total, pending = db.execute("SELECT COALESCE(SUM(COALESCE(actual,reserved)),0), COALESCE(SUM(CASE WHEN status='pending' THEN reserved ELSE 0 END),0) FROM usage WHERE month=?", (month,)).fetchone()
        return {'month_utc': month, 'estimated_microusd': total, 'pending_microusd': pending, 'limit_microusd': self.limit, 'notice': '로컬 추정치는 서버 결제 금액과 다를 수 있습니다.'}
